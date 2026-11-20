"""Pure, deterministic 4-step routing engine with step-by-step trace ('Why')."""

from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
import re
import time
import unicodedata
from urllib.parse import urlparse
import zipfile
import zlib

from download_sorter.config import DEFAULT_CATEGORY_MAP, Config, Rule
from download_sorter.dest import apply_rename_template, read_exif_date, resolve_destination_tokens, to_relative_dest

# Anything that is not a letter or digit in any language (underscore counts as a separator).
_NON_WORD_RE = re.compile(r"[\W_]+")
# Scripts written without spaces between words: a keyword can sit inside a longer run.
_UNSPACED_SCRIPT_RANGES: tuple[tuple[int, int], ...] = (
    (0x0E00, 0x0EFF),  # Thai, Lao
    (0x1000, 0x109F),  # Myanmar
    (0x1780, 0x17FF),  # Khmer
    (0x3040, 0x30FF),  # Hiragana, Katakana
    (0x31F0, 0x31FF),  # Katakana extensions
    (0x3400, 0x4DBF),  # Han extension A
    (0x4E00, 0x9FFF),  # Han
    (0xF900, 0xFAFF),  # Han compatibility
    (0x20000, 0x2FA1F),  # Han extensions B and later
)
_COPY_SUFFIX_RE = re.compile(r"(?:\s*-\s*copy)?(?:\s*\(\d{1,3}\))+$|\s*-\s*copy$", re.IGNORECASE)
_GENERIC_SNIFF_EXTS = {"", ".bin", ".tmp", ".dat", ".jfif", ".download"}

try:
    from PIL import ExifTags as _PilExifTags, Image as _PilImage
except ImportError:  # pragma: no cover
    _PilExifTags = None  # type: ignore[assignment]
    _PilImage = None  # type: ignore[assignment]

_MIME_BY_EXT: dict[str, str] = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".webp": "image/webp",
    ".wav": "audio/wav",
    ".avi": "video/x-msvideo",
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".mp3": "audio/mpeg",
    ".flac": "audio/flac",
    ".7z": "application/x-7z-compressed",
    ".rar": "application/vnd.rar",
    ".gz": "application/gzip",
    ".exe": "application/vnd.microsoft.portable-executable",
    ".stl": "model/stl",
    ".3mf": "model/3mf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".zip": "application/zip",
}


def sniff_magic_extension(path: Path | None) -> str:
    """Detect common file extensions from magic bytes and ZIP container structure."""
    if path is None or not path.is_file():
        return ""
    try:
        with path.open("rb") as fh:
            head = fh.read(2048)
    except OSError:
        return ""
    if not head:
        return ""
    if head.startswith(b"%PDF-"):
        return ".pdf"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if head.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if head.startswith(b"BM"):
        return ".bmp"
    if head.startswith(b"RIFF") and len(head) >= 12:
        fourcc = head[8:12]
        if fourcc == b"WEBP":
            return ".webp"
        if fourcc == b"WAVE":
            return ".wav"
        if fourcc == b"AVI ":
            return ".avi"
    if len(head) >= 12 and head[4:8] == b"ftyp":
        if head[8:12] == b"qt  ":
            return ".mov"
        return ".mp4"
    if head.startswith(b"ID3") or (len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0):
        return ".mp3"
    if head.startswith(b"fLaC"):
        return ".flac"
    if head.startswith(b"7z\xbc\xaf\x27\x1c"):
        return ".7z"
    if head.startswith(b"Rar!\x1a\x07"):
        return ".rar"
    if head.startswith(b"\x1f\x8b\x08"):
        return ".gz"
    if head.startswith(b"MZ"):
        return ".exe"
    if head.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")):
        try:
            with zipfile.ZipFile(path, "r") as zf:
                # Case-sensitive part names that real 3MF and Office files always carry.
                # Folder names alone are not enough: an ordinary zip of a "Word" or
                # "PPT" folder is still an archive.
                names = {n.replace("\\", "/").lstrip("/") for n in zf.namelist()}
                if "3D/3dmodel.model" in names:
                    return ".3mf"
                if "word/document.xml" in names:
                    return ".docx"
                if "xl/workbook.xml" in names:
                    return ".xlsx"
                if "ppt/presentation.xml" in names:
                    return ".pptx"
        except (zipfile.BadZipFile, OSError):
            pass
        return ".zip"
    head_lower = head[:512].lstrip().lower()
    if head_lower.startswith(b"solid ") and (b"facet normal" in head.lower() or b"endsolid" in head.lower()):
        return ".stl"
    return ""


def sniff_magic_mime(path: Path | None) -> tuple[str, str]:
    """Return ``(detected_extension, mime_type)`` from magic-byte/container sniffing."""
    ext = sniff_magic_extension(path)
    return ext, _MIME_BY_EXT.get(ext, "")
_PDF_STREAM_RE = re.compile(rb"stream\r?\n(.*?)\r?\nendstream", re.DOTALL)
_PDF_TEXT_LIT_RE = re.compile(rb"\(([^()\\]{2,200})\)")
_PDF_STREAM_MAX_OUT = 65536


def extract_pdf_text_preview(path: Path | None, *, max_bytes: int = 262_144) -> str:
    """Extract a lightweight ASCII/UTF-8 text preview from a PDF without external deps."""
    if path is None or path.suffix.lower() != ".pdf" or not path.is_file():
        return ""
    try:
        with path.open("rb") as fh:
            raw = fh.read(max_bytes)
    except OSError:
        return ""

    chunks: list[str] = []
    # 1. Plain literal strings in uncompressed PDF sections
    for m in _PDF_TEXT_LIT_RE.finditer(raw):
        chunks.append(m.group(1).decode("latin-1", errors="ignore"))

    # 2. FlateDecode compressed streams (common in modern PDFs)
    for m in _PDF_STREAM_RE.finditer(raw):
        stream_bytes = m.group(1)
        try:
            # Only the first 64 KB of each stream is read for text, so never inflate
            # more than that: a small crafted stream can expand to hundreds of MB.
            decompressed = zlib.decompressobj().decompress(stream_bytes, _PDF_STREAM_MAX_OUT)
        except zlib.error:
            continue
        for lit in _PDF_TEXT_LIT_RE.finditer(decompressed):
            chunks.append(lit.group(1).decode("latin-1", errors="ignore"))
        if len(chunks) >= 200:
            break

    return " ".join(chunks)[:16384]


@dataclass(frozen=True)
class FileCandidate:
    """Immutable snapshot of a single top-level file to evaluate."""

    filename: str
    path: Path | None = None
    source_url: str = ""
    tab_url: str = ""
    tab_title: str = ""
    content_preview: str = ""
    size_bytes: int | None = None
    age_days: float | None = None

    @property
    def extension(self) -> str:
        return Path(self.filename).suffix.lower()

    @property
    def stem(self) -> str:
        return Path(self.filename).stem

    @property
    def host(self) -> str:
        return extract_host(self.source_url)

    @property
    def tab_host(self) -> str:
        return extract_host(self.tab_url)


@dataclass(frozen=True)
class RouteStep:
    """One step in the decision trace returned by ``evaluate``."""

    stage: str
    label: str
    matched: bool
    detail: str


@dataclass(frozen=True)
class RouteDecision:
    """Deterministic routing outcome + full human-readable explanation trace."""

    action: str  # "move", "ignore", or "leave"
    relative_dest: Path | None
    reason: str
    matched_rule: str
    match_type: str  # "ignore", "rule", "extension", "fallback"
    trace: list[RouteStep] = field(default_factory=list)


def extract_host(url: str) -> str:
    """Return normalized hostname without leading ``www.``."""
    raw = (url or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = "https://" + raw
    try:
        host = (urlparse(raw).hostname or "").lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def read_zone_url(path: Path) -> str:
    """Read HostUrl/ReferrerUrl from Windows NTFS Zone.Identifier ADS safely."""
    ads = Path(f"{path}:Zone.Identifier")
    try:
        content = ads.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""
    host_url = ""
    referrer_url = ""
    for line in content.splitlines():
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        key = k.strip().lower()
        val = v.strip()
        if key == "hosturl" and val and not host_url:
            host_url = val
        elif key == "referrerurl" and val and not referrer_url:
            referrer_url = val
    return host_url or referrer_url


def candidate_from_path(path: Path) -> FileCandidate:
    """Build a ``FileCandidate`` from a real path on disk, including Zone.Identifier."""
    return FileCandidate(filename=path.name, path=path, source_url=read_zone_url(path))


# Each abbreviation stands for one meaning. Abbreviations that commonly mean
# something else ("rev" as review, "sol" as solar, "calc" as calculator, "mech"
# as a mechanical keyboard) are left out: they filed unrelated files.
_TOKEN_ALIASES: dict[str, tuple[str, ...]] = {
    "hw": ("homework",),
    "hmk": ("homework",),
    "syl": ("syllabus",),
    "syll": ("syllabus",),
    "lec": ("lecture",),
    "soln": ("solution", "solutions"),
    "proj": ("project",),
    "sched": ("schedule",),
    "lab": ("laboratory",),
    "assn": ("assignment",),
    "asgmt": ("assignment",),
    "thermo": ("thermodynamics",),
    "mfg": ("manufacturing",),
    "dwg": ("drawing",),
    "spec": ("specification",),
    "rev": ("revision",),
}

_REVERSE_ALIASES: dict[str, set[str]] = {}
for _abbr, _expansions in _TOKEN_ALIASES.items():
    for _exp in _expansions:
        _REVERSE_ALIASES.setdefault(_exp, set()).add(_abbr)


def _is_latin_letter(ch: str) -> bool:
    code = ord(ch)
    return code < 0x0250 or 0x1E00 <= code <= 0x1EFF


def _fold_latin_accents(text: str) -> str:
    """Drop accents from Latin letters only (``résumé`` -> ``resume``).

    Marks in other scripts are part of the letter (Japanese ``が``, Cyrillic ``й``)
    and are kept.
    """
    out: list[str] = []
    base = ""
    for ch in unicodedata.normalize("NFKD", text):
        if 0x0300 <= ord(ch) <= 0x036F and base and _is_latin_letter(base):
            continue
        if not unicodedata.combining(ch):
            base = ch
        out.append(ch)
    return unicodedata.normalize("NFC", "".join(out))


def _split_word_edges(text: str) -> str:
    """Put a space at CamelCase edges and between letters and digits, in any script."""
    out: list[str] = []
    prev = ""
    for ch in text:
        if prev and (
            ((prev.islower() or prev.isdigit()) and ch.isupper())
            or (prev.isalpha() and ch.isdigit())
            or (prev.isdigit() and ch.isalpha())
        ):
            out.append(" ")
        out.append(ch)
        prev = ch
    return "".join(out)


def _normalize_tokens(text: str) -> str:
    """Split CamelCase, letter/digit runs and separators into lowercase space-separated tokens.

    ``Resume2026`` becomes ``resume 2026`` and ``IME3311`` becomes ``ime 3311``, so a
    keyword still matches a word that has a number stuck to it. Letters from every
    language are kept, and accents on Latin letters are ignored.
    """
    folded = _fold_latin_accents(_split_word_edges(text).casefold())
    return " ".join(_NON_WORD_RE.sub(" ", folded).split())


def _has_unspaced_script(text: str) -> bool:
    for ch in text:
        code = ord(ch)
        if any(lo <= code <= hi for lo, hi in _UNSPACED_SCRIPT_RANGES):
            return True
    return False


def normalize_filename_tokens(filename: str, *, expand_aliases: bool = True) -> list[str]:
    """Return normalized lowercase tokens for a filename, stripping copy suffixes."""
    raw_stem = Path(filename).stem
    cleaned_stem = _COPY_SUFFIX_RE.sub("", raw_stem).strip() or raw_stem
    tokens = _normalize_tokens(cleaned_stem).split()
    if not expand_aliases or not tokens:
        return tokens
    out: list[str] = []
    seen: set[str] = set()
    for tok in tokens:
        if tok not in seen:
            seen.add(tok)
            out.append(tok)
        for alias in _TOKEN_ALIASES.get(tok, ()):
            if alias not in seen:
                seen.add(alias)
                out.append(alias)
        for abbr in _REVERSE_ALIASES.get(tok, set()):
            if abbr not in seen:
                seen.add(abbr)
                out.append(abbr)
    return out


def _joins_consecutive_tokens(compact_kw: str, tokens: list[str]) -> bool:
    """True when ``compact_kw`` is exactly some run of whole tokens written together.

    ``lecturenotes`` matches ``lecture_notes``, but ``bank`` never matches inside
    ``bankruptcy``: a keyword has to line up with word edges on both sides.
    """
    for start in range(len(tokens)):
        joined = ""
        for tok in tokens[start:]:
            joined += tok
            if joined == compact_kw:
                return True
            if len(joined) >= len(compact_kw) or not compact_kw.startswith(joined):
                break
    return False


def _keyword_matches(filename: str, keyword: str) -> bool:
    norm_kw = _normalize_tokens(keyword)
    if not norm_kw:
        return False
    raw_stem = Path(filename).stem
    cleaned_stem = _COPY_SUFFIX_RE.sub("", raw_stem).strip() or raw_stem
    norm_name = _normalize_tokens(cleaned_stem)
    if not norm_name:
        return False
    # Match whole phrase boundaries so e.g. 'cv' does not match inside 'opencv' or 'csv'.
    padded_name = f" {norm_name} "
    padded_kw = f" {norm_kw} "
    if padded_kw in padded_name:
        return True
    kw_tokens = norm_kw.split()
    name_tokens = norm_name.split()
    # Also support compacted token matching ('lecturenotes' vs 'lecture_notes'), but
    # only along word edges: a plain substring check made 'resume' match 'presumed'.
    compact_kw = norm_kw.replace(" ", "")
    if len(compact_kw) >= 4 and _joins_consecutive_tokens(compact_kw, name_tokens):
        return True
    # Chinese, Japanese, Thai and similar languages don't separate words with spaces,
    # so "請求書" has to be found inside "2026年4月請求書".
    if _has_unspaced_script(compact_kw) and compact_kw in norm_name.replace(" ", ""):
        return True
    # Support unordered multi-word token matching (e.g. "tax return" -> "2026_Return_Tax"),
    # including academic/engineering alias expansion (e.g. "hw" <-> "homework").
    # The filename's tokens are widened by one step (abbreviation <-> full word);
    # the keyword is not, so two words that only share an abbreviation never match.
    expanded_name_set = set(normalize_filename_tokens(filename, expand_aliases=True))
    if kw_tokens and all(t in expanded_name_set for t in kw_tokens):
        return True
    # High-threshold fuzzy match for longer single-word keywords (>= 5 chars) to tolerate minor typos.
    # The first letter has to agree: a typo there is rare, and without it 'lecture'
    # matched 'electure' and similar words that merely contain the keyword.
    if len(kw_tokens) == 1 and len(norm_kw) >= 5:
        for tok in name_tokens:
            if (
                len(tok) >= 5
                and tok[0] == norm_kw[0]
                and SequenceMatcher(None, norm_kw, tok).ratio() >= 0.88
            ):
                return True
    return False


def rule_specificity_score(rule: Rule) -> int:
    """Score a rule by how specific its conditions are (higher = narrower/more specific)."""
    score = 0
    if rule.domains:
        score += 40 + len(rule.domains)
    if rule.content_keywords:
        score += 35 + len(rule.content_keywords)
    if rule.regex_pattern:
        score += 30
    if rule.keywords:
        score += 25 + len(rule.keywords)
    if rule.exclude_keywords:
        score += 8 + len(rule.exclude_keywords)
    if rule.min_size_mb > 0 or rule.max_size_mb > 0 or rule.min_age_days > 0:
        score += 12
    if rule.match_mode == "all":
        score += 10
    if rule.extensions:
        score += 10 + len(rule.extensions)
    return score


def sort_rules_by_specificity(rules: list[Rule]) -> list[Rule]:
    """Return rules ordered from most specific to broadest, preserving relative ties."""
    return sorted(rules, key=rule_specificity_score, reverse=True)


def _domain_matches(host: str, domains: list[str]) -> str | None:
    if not host or not domains:
        return None
    for d in domains:
        clean = extract_host(d)
        if clean and (host == clean or host.endswith("." + clean)):
            return clean
    return None


def _candidate_stats(candidate: FileCandidate) -> tuple[int | None, float | None]:
    size = candidate.size_bytes
    age = candidate.age_days
    if (size is None or age is None) and candidate.path is not None and candidate.path.is_file():
        try:
            st = candidate.path.stat()
            if size is None:
                size = st.st_size
            if age is None:
                age = max(0.0, (time.time() - st.st_mtime) / 86400.0)
        except OSError:
            pass
    return size, age


def _evaluate_rule(
    rule: Rule,
    candidate: FileCandidate,
    *,
    effective_ext: str | None = None,
    captured_groups: dict[str, str] | None = None,
) -> tuple[bool, str]:
    if not rule.enabled:
        return False, "Rule is disabled"

    ext = effective_ext if effective_ext is not None else candidate.extension
    if rule.extensions and ext not in rule.extensions:
        return False, f"Extension '{ext or '(none)'}' not in {rule.extensions}"

    # Exclusion guard (Hazel/DropIt negation)
    for ex_kw in rule.exclude_keywords:
        if _keyword_matches(candidate.stem, ex_kw):
            return False, f"Excluded by negative keyword '{ex_kw}'"

    # File size & age bounds
    size_bytes, age_days = _candidate_stats(candidate)
    if rule.min_size_mb > 0:
        if size_bytes is None or size_bytes < rule.min_size_mb * 1024 * 1024:
            return False, f"File smaller than {rule.min_size_mb:g} MB"
    if rule.max_size_mb > 0:
        if size_bytes is None or size_bytes > rule.max_size_mb * 1024 * 1024:
            return False, f"File larger than {rule.max_size_mb:g} MB"
    if rule.min_age_days > 0:
        if age_days is None or age_days < rule.min_age_days:
            return False, f"File newer than {rule.min_age_days} day(s)"

    has_positive = bool(rule.keywords or rule.domains or rule.content_keywords or rule.regex_pattern)
    if not has_positive:
        return True, f"Matched extension/size/age filter ('{ext or 'any'}')"

    reasons: list[str] = []
    misses: list[str] = []

    if rule.domains:
        matched_domain = _domain_matches(candidate.host, rule.domains)
        matched_tab_domain = _domain_matches(candidate.tab_host, rule.domains) if not matched_domain else None
        if matched_domain:
            reasons.append(f"Download domain '{candidate.host}' matched '{matched_domain}'")
        elif matched_tab_domain:
            reasons.append(f"Active tab domain '{candidate.tab_host}' matched '{matched_tab_domain}'")
        else:
            misses.append(f"domain {candidate.host or '(none)'} did not match {rule.domains}")

    if rule.keywords:
        kw_hit = next((kw for kw in rule.keywords if _keyword_matches(candidate.stem, kw)), None)
        if kw_hit:
            reasons.append(f"Filename matched keyword '{kw_hit}'")
        else:
            misses.append("no keyword matched")

    if rule.regex_pattern:
        try:
            rm = re.search(rule.regex_pattern, candidate.stem, flags=re.IGNORECASE)
        except re.error:
            rm = None
        if rm:
            if captured_groups is not None:
                captured_groups.update({k: v for k, v in rm.groupdict().items() if v})
            reasons.append(f"Filename matched regex '{rule.regex_pattern}'")
        else:
            misses.append(f"regex '{rule.regex_pattern}' did not match")

    if rule.content_keywords:
        if ext == ".pdf":
            preview = candidate.content_preview or extract_pdf_text_preview(candidate.path)
            norm_preview = f" {_normalize_tokens(preview)} " if preview else ""
            ckw_hit = next(
                (ckw for ckw in rule.content_keywords if _normalize_tokens(ckw) and f" {_normalize_tokens(ckw)} " in norm_preview),
                None,
            )
            if ckw_hit:
                reasons.append(f"PDF content matched '{ckw_hit}'")
            else:
                misses.append("no PDF content keyword matched")
        else:
            misses.append("PDF content filter requires .pdf")

    if rule.match_mode == "all":
        if misses:
            return False, "; ".join(misses)
        return True, "; ".join(reasons)

    if reasons:
        return True, reasons[0]
    return False, "; ".join(misses)


_ZIP_3D_EXTS = frozenset({".stl", ".3mf", ".obj", ".step", ".stp", ".iges", ".igs", ".scad", ".blend"})
_ZIP_GCODE_EXTS = frozenset({".nc", ".tap", ".ngc", ".gcode", ".bgcode", ".gco"})
_ZIP_DOC_EXTS = frozenset({".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".md"})
_ZIP_CODE_EXTS = frozenset({".py", ".js", ".ts", ".c", ".cpp", ".h", ".hpp", ".rs", ".go", ".java", ".cs"})
_ZIP_MEDIA_EXTS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp4", ".mov", ".mp3", ".wav"})

_SCREENSHOT_RESOLUTIONS: frozenset[tuple[int, int]] = frozenset({
    (1280, 720),
    (1366, 768),
    (1920, 1080),
    (1920, 1200),
    (2560, 1440),
    (2560, 1600),
    (3440, 1440),
    (3840, 2160),
    (1170, 2532),
    (1290, 2796),
})
_CAD_SOFTWARE_HINTS = ("blender", "fusion", "solidworks", "keyshot", "bambu", "prusa", "orca", "cura", "rhino")
_SCREENSHOT_HINTS = ("screenshot", "screen shot", "snip", "greenshot", "sharex", "flameshot", "lightshot")


@dataclass(frozen=True)
class ZipPeekResult:
    """Summary of ZIP archive contents prior to extraction or routing."""

    valid: bool
    file_count: int
    categories: dict[str, int] = field(default_factory=dict)
    dominant_category: str = ""
    suggested_destination: str = ""
    matched_rule: str = ""
    matched_trigger: str = ""
    has_path_traversal: bool = False
    sample_names: tuple[str, ...] = ()


def _is_zip_slip_entry(entry_name: str) -> bool:
    norm = entry_name.replace("\\", "/")
    if norm.startswith("/") or (len(norm) >= 2 and norm[1] == ":"):
        return True
    parts = [p for p in norm.split("/") if p]
    return ".." in parts


def _inner_rule_match(
    rule: Rule,
    inner_name: str,
    outer: FileCandidate | None,
    outer_stats: tuple[int | None, float | None],
) -> tuple[bool, str, str, dict[str, str]]:
    """Check one rule against a file inside an archive.

    Only rules with keywords are considered, and a keyword has to name the inner
    file. The rule's other conditions (file types, exclude words, size and age,
    websites, regex, ALL/ANY) are then checked as usual, with the archive's own
    size, age and download source standing in for the inner file's. Exclude words
    also apply to the archive's own name.
    """
    if not rule.enabled or not rule.keywords:
        return False, "", "", {}
    kw_hit = next((kw for kw in rule.keywords if _keyword_matches(inner_name, kw)), None)
    if kw_hit is None:
        return False, "", "", {}
    if outer is not None and any(_keyword_matches(outer.stem, ex) for ex in rule.exclude_keywords):
        return False, "", "", {}
    size, age = outer_stats
    inner = FileCandidate(
        filename=inner_name,
        source_url=outer.source_url if outer else "",
        tab_url=outer.tab_url if outer else "",
        tab_title=outer.tab_title if outer else "",
        size_bytes=size,
        age_days=age,
    )
    captured: dict[str, str] = {}
    ok, detail = _evaluate_rule(rule, inner, captured_groups=captured)
    return ok, kw_hit, detail, captured


def peek_zip_contents(
    path: Path | None,
    rules: list[Rule] | None = None,
    *,
    candidate: FileCandidate | None = None,
    category_map: dict[str, str] | None = None,
) -> ZipPeekResult:
    """Inspect a ZIP archive's central directory without extracting any files.

    ``suggested_destination`` comes from ``category_map`` (the user's file-type
    folders; the built-in map when omitted), using the most common file type of
    the archive's dominant kind. It is empty when that map has no folder for it.
    """
    if path is None or not path.is_file():
        return ZipPeekResult(valid=False, file_count=0)
    try:
        with zipfile.ZipFile(path, "r") as zf:
            raw_names = zf.namelist()
    except (zipfile.BadZipFile, OSError, ValueError):
        return ZipPeekResult(valid=False, file_count=0)

    has_traversal = any(_is_zip_slip_entry(n) for n in raw_names)
    files: list[str] = []
    for name in raw_names:
        norm = name.replace("\\", "/")
        if norm.endswith("/"):
            continue
        lower = norm.lower()
        if lower.startswith("__macosx/") or lower.endswith("/.ds_store") or lower == ".ds_store":
            continue
        files.append(norm)

    if not files:
        return ZipPeekResult(valid=True, file_count=0, has_path_traversal=has_traversal)

    cats: dict[str, int] = {"3d": 0, "gcode": 0, "docs": 0, "code": 0, "media": 0, "other": 0}
    ext_counts: dict[str, dict[str, int]] = {k: {} for k in cats}
    for f in files:
        f_ext = Path(f).suffix.lower()
        if f_ext in _ZIP_3D_EXTS:
            cat = "3d"
        elif f_ext in _ZIP_GCODE_EXTS:
            cat = "gcode"
        elif f_ext in _ZIP_DOC_EXTS:
            cat = "docs"
        elif f_ext in _ZIP_CODE_EXTS:
            cat = "code"
        elif f_ext in _ZIP_MEDIA_EXTS:
            cat = "media"
        else:
            cat = "other"
        cats[cat] += 1
        ext_counts[cat][f_ext] = ext_counts[cat].get(f_ext, 0) + 1

    if rules and not has_traversal:
        outer_stats = _candidate_stats(candidate) if candidate is not None else (None, None)
        for rule in rules:
            for inner in files:
                inner_name = Path(inner).name
                ok, kw, _detail, captured = _inner_rule_match(rule, inner_name, candidate, outer_stats)
                if not ok:
                    continue
                dest = rule.destination
                for gk, gv in captured.items():
                    dest = dest.replace(f"{{{gk}}}", gv)
                return ZipPeekResult(
                    valid=True,
                    file_count=len(files),
                    categories=cats,
                    dominant_category="keyword_match",
                    suggested_destination=dest,
                    matched_rule=rule.name,
                    matched_trigger=f"{inner_name} ~ {kw}",
                    has_path_traversal=has_traversal,
                    sample_names=tuple(files[:10]),
                )

    total = len(files)
    best_cat, best_count = max(cats.items(), key=lambda kv: kv[1])
    if cats["3d"] > 0 and (cats["3d"] / total >= 0.35 or best_cat == "3d"):
        best_cat = "3d"
    elif cats["gcode"] > 0 and cats["gcode"] >= cats["3d"]:
        best_cat = "gcode"
    elif best_count / total < 0.5 or best_cat == "other":
        best_cat = ""

    suggested = ""
    if best_cat:
        if category_map is None:
            category_map = DEFAULT_CATEGORY_MAP
        by_count = sorted(ext_counts[best_cat].items(), key=lambda kv: (-kv[1], kv[0]))
        suggested = next((category_map[e] for e, _n in by_count if category_map.get(e)), "")

    return ZipPeekResult(
        valid=True,
        file_count=total,
        categories=cats,
        dominant_category=best_cat,
        suggested_destination=suggested,
        has_path_traversal=has_traversal,
        sample_names=tuple(files[:10]),
    )


@dataclass(frozen=True)
class ImageInspection:
    """Result of Pillow-based image EXIF and dimension inspection."""

    available: bool
    category: str  # "camera_photo", "screenshot", "cad_render", "image", "error"
    width: int = 0
    height: int = 0
    image_format: str = ""
    camera_make: str = ""
    camera_model: str = ""
    software: str = ""
    suggested_destination: str = ""


def inspect_image_metadata(path: Path | None) -> ImageInspection:
    """Inspect image EXIF and dimensions using Pillow to classify photos, screenshots, and renders."""
    if _PilImage is None:
        return ImageInspection(available=False, category="error")
    if path is None or not path.is_file():
        return ImageInspection(available=True, category="error")

    try:
        with _PilImage.open(path) as img:
            width, height = img.size
            fmt = (img.format or "").upper()
            exif_dict: dict[str, str] = {}
            try:
                raw_exif = img.getexif()
                if raw_exif and _PilExifTags is not None:
                    for tag_id, val in raw_exif.items():
                        tag_name = _PilExifTags.TAGS.get(tag_id, str(tag_id))
                        exif_dict[str(tag_name)] = str(val).strip()
            except Exception:
                pass
    except Exception:
        return ImageInspection(available=True, category="error")

    make = exif_dict.get("Make", "")
    model = exif_dict.get("Model", "")
    software = exif_dict.get("Software", "")
    sw_lower = software.lower()
    name_lower = path.name.lower()

    if make or model:
        return ImageInspection(
            available=True,
            category="camera_photo",
            width=width,
            height=height,
            image_format=fmt,
            camera_make=make,
            camera_model=model,
            software=software,
            suggested_destination="Images/Photos",
        )

    if any(h in sw_lower or h in name_lower for h in _CAD_SOFTWARE_HINTS):
        return ImageInspection(
            available=True,
            category="cad_render",
            width=width,
            height=height,
            image_format=fmt,
            software=software,
            suggested_destination="Images/Renders",
        )

    if (
        any(h in sw_lower or h in name_lower for h in _SCREENSHOT_HINTS)
        or (fmt in {"PNG", "WEBP", "BMP"} and (width, height) in _SCREENSHOT_RESOLUTIONS)
    ):
        return ImageInspection(
            available=True,
            category="screenshot",
            width=width,
            height=height,
            image_format=fmt,
            software=software,
            suggested_destination="Images/Screenshots",
        )

    return ImageInspection(
        available=True,
        category="image",
        width=width,
        height=height,
        image_format=fmt,
        software=software,
        suggested_destination="Images",
    )


def evaluate(candidate: FileCandidate, config: Config) -> RouteDecision:
    """Evaluate a file candidate strictly top-to-bottom in 4 predictable steps."""
    trace: list[RouteStep] = []
    name_lower = candidate.filename.lower()
    ext = candidate.extension
    if ext in (_GENERIC_SNIFF_EXTS | {".zip"}) and candidate.path is not None:
        sniffed = sniff_magic_extension(candidate.path)
        if sniffed:
            ext = sniffed

    # Step 1: Ignore Guard
    if candidate.filename.startswith("."):
        trace.append(RouteStep("Ignore Guard", "Hidden dotfile", True, f"'{candidate.filename}' starts with '.'"))
        return RouteDecision("ignore", None, "Hidden file ignored", "Ignore Guard", "ignore", trace)
    if name_lower in config.ignore_names:
        trace.append(RouteStep("Ignore Guard", "System file", True, f"'{candidate.filename}' is in ignore_names"))
        return RouteDecision("ignore", None, f"Ignored system file '{candidate.filename}'", "Ignore Guard", "ignore", trace)
    if ext in config.ignore_extensions:
        trace.append(RouteStep("Ignore Guard", "Incomplete download", True, f"Extension '{ext}' is in ignore_extensions"))
        return RouteDecision("ignore", None, f"Ignored partial download '{ext}'", "Ignore Guard", "ignore", trace)
    trace.append(RouteStep("Ignore Guard", "Check active/system files", False, "Passed ignore checks"))

    effective_host = candidate.host or candidate.tab_host
    exif_dt = read_exif_date(candidate.path) if candidate.path is not None else None

    # Step 2: Explicit Ordered User Rules (First Match Wins)
    for idx, rule in enumerate(config.rules, start=1):
        captured: dict[str, str] = {}
        ok, detail = _evaluate_rule(rule, candidate, effective_ext=ext, captured_groups=captured)
        trace.append(RouteStep("User Rules", f"#{idx} {rule.name}", ok, detail))
        if ok:
            raw_dest = rule.destination
            for gk, gv in captured.items():
                raw_dest = raw_dest.replace(f"{{{gk}}}", gv)
            expanded_dest = resolve_destination_tokens(
                raw_dest,
                host=effective_host,
                ext=ext,
                exif_date=exif_dt,
            )
            target_name = apply_rename_template(
                rule.rename_template,
                candidate.filename,
                host=effective_host,
                exif_date=exif_dt,
                regex_groups=captured,
            )
            dest_rel = Path(to_relative_dest(expanded_dest)) / target_name
            return RouteDecision(
                action="move",
                relative_dest=dest_rel,
                reason=f"Rule '{rule.name}': {detail}",
                matched_rule=rule.name,
                match_type="rule",
                trace=trace,
            )

    # Step 2.5: Pre-routing ZIP content peek (when no explicit User Rule matched)
    if ext == ".zip" and candidate.path is not None:
        zip_peek = peek_zip_contents(
            candidate.path,
            rules=config.rules,
            candidate=candidate,
            category_map=config.category_map,
        )
        if zip_peek.has_path_traversal:
            trace.append(RouteStep("Zip Peek", "Security check", True, "Zip Slip path traversal detected; falling back to archive rule"))
        elif zip_peek.valid and zip_peek.suggested_destination:
            expanded_zip_dest = resolve_destination_tokens(
                zip_peek.suggested_destination,
                host=effective_host,
                ext=ext,
                exif_date=exif_dt,
            )
            folder = Path(to_relative_dest(expanded_zip_dest))
            dest_rel = folder / candidate.filename
            detail = (
                f"Archive inner match '{zip_peek.matched_trigger}' -> '{folder}'"
                if zip_peek.matched_rule
                else f"Archive contents ({zip_peek.dominant_category}) -> '{folder}'"
            )
            trace.append(RouteStep("Zip Peek", "Archive inspection", True, detail))
            return RouteDecision(
                action="move",
                relative_dest=dest_rel,
                reason=detail,
                matched_rule=zip_peek.matched_rule or f"Zip Peek ({zip_peek.dominant_category})",
                match_type="rule" if zip_peek.matched_rule else "extension",
                trace=trace,
            )

    # Step 3: Category Extension Map (+ optional Type Subfolder)
    category = config.category_map.get(ext)
    if category:
        sub = config.type_subfolders.get(ext, "") if config.use_type_subfolders else ""
        raw_cat = str(Path(category) / sub) if sub else category
        expanded_cat = resolve_destination_tokens(
            raw_cat,
            host=effective_host,
            ext=ext,
            exif_date=exif_dt,
        )
        folder = Path(to_relative_dest(expanded_cat))
        dest_rel = folder / candidate.filename
        detail = f"Extension '{ext}' -> '{folder}'"
        trace.append(RouteStep("Category Map", "Extension lookup", True, detail))
        return RouteDecision(
            action="move",
            relative_dest=dest_rel,
            reason=f"File type ({ext} -> {folder})",
            matched_rule=str(folder),
            match_type="extension",
            trace=trace,
        )
    trace.append(RouteStep("Category Map", "Extension lookup", False, f"No category mapping for '{ext or '(none)'}'"))

    # Step 4: Fallback
    if config.fallback_mode == "leave":
        trace.append(RouteStep("Fallback", "Unmatched behavior", True, "Configured to leave unmatched files in Downloads"))
        return RouteDecision(
            action="leave",
            relative_dest=None,
            reason="Left in Downloads (no rule or file type matched)",
            matched_rule="Leave in Downloads",
            match_type="fallback",
            trace=trace,
        )

    expanded_default = resolve_destination_tokens(
        config.default_folder,
        host=effective_host,
        ext=ext,
        exif_date=exif_dt,
    )
    dest_rel = Path(to_relative_dest(expanded_default)) / candidate.filename
    trace.append(RouteStep("Fallback", "Default folder", True, f"Routed to '{config.default_folder}'"))
    return RouteDecision(
        action="move",
        relative_dest=dest_rel,
        reason=f"Fallback folder '{config.default_folder}'",
        matched_rule=config.default_folder,
        match_type="fallback",
        trace=trace,
    )
