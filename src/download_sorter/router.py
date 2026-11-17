"""Pure, deterministic 4-step routing engine with step-by-step trace ('Why')."""

from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
import re
import time
from urllib.parse import urlparse
import zipfile
import zlib

from download_sorter.config import Config, Rule
from download_sorter.dest import apply_rename_template, read_exif_date, resolve_destination_tokens, to_relative_dest

_NON_WORD_RE = re.compile(r"[^a-z0-9]+")
_CAMEL_SPLIT_RE = re.compile(r"([a-z0-9])([A-Z])")
_LETTER_DIGIT_RE = re.compile(r"(?<=[a-z])(?=[0-9])|(?<=[0-9])(?=[a-z])")
_GENERIC_SNIFF_EXTS = {"", ".bin", ".tmp", ".dat", ".jfif", ".download"}


def sniff_magic_extension(path: Path | None) -> str:
    """Detect common file extensions from magic bytes when a filename lacks a real extension."""
    if path is None or not path.is_file():
        return ""
    try:
        with path.open("rb") as fh:
            head = fh.read(512)
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
    if head.startswith(b"RIFF") and len(head) >= 12:
        fourcc = head[8:12]
        if fourcc == b"WEBP":
            return ".webp"
        if fourcc == b"WAVE":
            return ".wav"
        if fourcc == b"AVI ":
            return ".avi"
    if len(head) >= 12 and head[4:8] == b"ftyp":
        return ".mp4"
    if head.startswith(b"ID3") or (len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0):
        return ".mp3"
    if head.startswith(b"fLaC"):
        return ".flac"
    if head.startswith(b"7z\xbc\xaf\x27\x1c"):
        return ".7z"
    if head.startswith(b"Rar!\x1a\x07"):
        return ".rar"
    if head.startswith(b"MZ"):
        return ".exe"
    if head.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(path, "r") as zf:
                names = set(zf.namelist()[:64])
                if any(n.lower().endswith(".model") and "3d/" in n.lower() for n in names):
                    return ".3mf"
                if any(n.startswith("word/") for n in names):
                    return ".docx"
                if any(n.startswith("xl/") for n in names):
                    return ".xlsx"
                if any(n.startswith("ppt/") for n in names):
                    return ".pptx"
        except (zipfile.BadZipFile, OSError):
            pass
        return ".zip"
    return ""
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


def _normalize_tokens(text: str) -> str:
    """Split CamelCase, letter/digit runs and separators into lowercase space-separated tokens.

    ``Resume2026`` becomes ``resume 2026`` and ``IME3311`` becomes ``ime 3311``, so a
    keyword still matches a word that has a number stuck to it.
    """
    camel_split = _CAMEL_SPLIT_RE.sub(r"\1 \2", text)
    split = _LETTER_DIGIT_RE.sub(" ", camel_split.lower())
    return " ".join(_NON_WORD_RE.sub(" ", split).split())


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
    norm_name = _normalize_tokens(Path(filename).stem)
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
    # Support unordered multi-word token matching (e.g. "tax return" -> "2026_Return_Tax").
    name_set = set(name_tokens)
    if len(kw_tokens) > 1 and all(t in name_set for t in kw_tokens):
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


def evaluate(candidate: FileCandidate, config: Config) -> RouteDecision:
    """Evaluate a file candidate strictly top-to-bottom in 4 predictable steps."""
    trace: list[RouteStep] = []
    name_lower = candidate.filename.lower()
    ext = candidate.extension
    if ext in _GENERIC_SNIFF_EXTS and candidate.path is not None:
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
