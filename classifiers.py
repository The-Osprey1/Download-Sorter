"""Classification helpers for Download Sorter (Milestone 3).

Provides content-aware, Windows-aware, and fuzzy/normalized classification
signals without mutating sorter.py or requiring heavy external runtimes:
1. Windows Zone.Identifier ADS inspection (with source_url override for sandbox parity)
2. Magic-byte / MIME sniffing + ZIP container disambiguation (.3mf, .docx, .xlsx, .pptx, .zip)
3. Pre-routing zipfile.namelist() peek for archive content classification & Zip Slip check
4. Fuzzy & normalized filename token matching (delimiters, CamelCase, copy suffixes, aliases)
5. Pillow EXIF/dimension image inspection + optional page-1 PDF text/title extraction
"""

from __future__ import annotations

import configparser
import difflib
import os
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

try:
    from PIL import ExifTags, Image

    _HAS_PIL = True
except ImportError:  # pragma: no cover - Pillow is in requirements.txt
    Image = None  # type: ignore[assignment]
    ExifTags = None  # type: ignore[assignment]
    _HAS_PIL = False

try:
    from pypdf import PdfReader

    _HAS_PYPDF = True
except ImportError:  # pragma: no cover - graceful degradation
    PdfReader = None  # type: ignore[assignment]
    _HAS_PYPDF = False


# ---------------------------------------------------------------------------
# 1. Windows Zone.Identifier ADS & Referrer/Host URL Inspection
# ---------------------------------------------------------------------------

DEFAULT_DOMAIN_DESTINATIONS: dict[str, str] = {
    "makerworld.com": "3D Models",
    "printables.com": "3D Models",
    "thingiverse.com": "3D Models",
    "thangs.com": "3D Models",
    "myminifactory.com": "3D Models",
    "cults3d.com": "3D Models",
    "grabcad.com": "3D Models\\CAD",
    "mcmaster.com": "3D Models\\CAD",
    "gradescope.com": "Documents\\School",
    "blackboard.com": "Documents\\School",
    "instructure.com": "Documents\\School",
    "canvas.edu": "Documents\\School",
    "github.com": "Code",
    "gitlab.com": "Code",
}


@dataclass(frozen=True)
class ZoneInfo:
    """Parsed Windows Zone.Identifier ADS or synthetic source_url metadata."""

    zone_id: int | None = None
    host_url: str = ""
    referrer_url: str = ""
    domain: str = ""
    source: str = "none"  # "ads", "override", or "none"


@dataclass(frozen=True)
class DomainMatch:
    """Result of matching a ZoneInfo domain against known/custom domain rules."""

    domain: str
    matched_rule: str
    destination: str
    url: str


def _extract_domain(url: str) -> str:
    if not url:
        return ""
    raw = url.strip()
    if "://" not in raw:
        raw = "https://" + raw
    try:
        netloc = urlparse(raw).netloc.lower()
    except ValueError:
        return ""
    if "@" in netloc:
        netloc = netloc.split("@", 1)[-1]
    if ":" in netloc:
        netloc = netloc.split(":", 1)[0]
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc


def parse_zone_identifier_text(text: str) -> ZoneInfo:
    """Parse raw Zone.Identifier INI content into a ZoneInfo."""
    if not text or not text.strip():
        return ZoneInfo()

    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read_string(text)
    except configparser.Error:
        # Fallback line-by-line parse for malformed ADS
        zone_id: int | None = None
        host_url = ""
        referrer_url = ""
        for line in text.splitlines():
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            kl = k.strip().lower()
            val = v.strip()
            if kl == "zoneid" and val.isdigit():
                zone_id = int(val)
            elif kl == "hosturl":
                host_url = val
            elif kl == "referrerurl":
                referrer_url = val
        domain = _extract_domain(host_url) or _extract_domain(referrer_url)
        return ZoneInfo(
            zone_id=zone_id,
            host_url=host_url,
            referrer_url=referrer_url,
            domain=domain,
            source="ads" if (host_url or referrer_url or zone_id is not None) else "none",
        )

    section = "ZoneTransfer" if parser.has_section("ZoneTransfer") else (parser.sections()[0] if parser.sections() else "")
    if not section:
        return ZoneInfo()

    raw_zone = parser.get(section, "ZoneId", fallback="").strip()
    zone_id = int(raw_zone) if raw_zone.isdigit() else None
    host_url = parser.get(section, "HostUrl", fallback="").strip()
    referrer_url = parser.get(section, "ReferrerUrl", fallback="").strip()
    domain = _extract_domain(host_url) or _extract_domain(referrer_url)
    return ZoneInfo(
        zone_id=zone_id,
        host_url=host_url,
        referrer_url=referrer_url,
        domain=domain,
        source="ads" if (host_url or referrer_url or zone_id is not None) else "none",
    )


def read_zone_identifier(
    file_path: Path | str | None = None,
    source_url: str | None = None,
) -> ZoneInfo:
    """Read Windows NTFS Zone.Identifier ADS or use explicit source_url override.

    Accepting `source_url` allows the Suite Central Live Sandbox to evaluate
    hypothetical downloads with the exact same logic as the background daemon.
    """
    if source_url and source_url.strip():
        clean_url = source_url.strip()
        return ZoneInfo(
            zone_id=3,
            host_url=clean_url,
            referrer_url="",
            domain=_extract_domain(clean_url),
            source="override",
        )

    if file_path is None:
        return ZoneInfo()

    ads_path = f"{os.fspath(file_path)}:Zone.Identifier"
    try:
        with open(ads_path, "r", encoding="utf-8", errors="replace") as fh:
            return parse_zone_identifier_text(fh.read())
    except OSError:
        return ZoneInfo()


def classify_source_domain(
    zone_info: ZoneInfo,
    domain_rules: dict[str, str] | None = None,
) -> DomainMatch | None:
    """Match a ZoneInfo domain against custom or default domain destination rules."""
    domain = zone_info.domain.lower().strip()
    if not domain:
        return None

    merged: dict[str, str] = dict(DEFAULT_DOMAIN_DESTINATIONS)
    if domain_rules:
        for k, v in domain_rules.items():
            if k and v:
                merged[k.strip().lower()] = v.strip()

    # Exact or subdomain match (e.g. "canvas.wmich.edu" matches "canvas." or ".edu")
    if domain in merged:
        return DomainMatch(
            domain=domain,
            matched_rule=domain,
            destination=merged[domain],
            url=zone_info.host_url or zone_info.referrer_url,
        )

    for pattern, dest in merged.items():
        if domain.endswith("." + pattern):
            return DomainMatch(
                domain=domain,
                matched_rule=pattern,
                destination=dest,
                url=zone_info.host_url or zone_info.referrer_url,
            )

    # Academic .edu or canvas.* heuristic
    if domain.endswith(".edu") or domain.startswith("canvas.") or ".instructure." in domain:
        return DomainMatch(
            domain=domain,
            matched_rule="*.edu/canvas",
            destination=merged.get("instructure.com", "Documents\\School"),
            url=zone_info.host_url or zone_info.referrer_url,
        )

    return None


# ---------------------------------------------------------------------------
# 2. Magic-Byte / MIME Sniffing & ZIP Container Disambiguation
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SniffResult:
    """Result of magic-byte and container sniffing."""

    detected_ext: str
    mime_type: str
    container_kind: str
    confidence: float
    matched_by: str


_SIMPLE_MAGIC: tuple[tuple[bytes, int, str, str, str], ...] = (
    (b"%PDF-", 0, ".pdf", "application/pdf", "pdf"),
    (b"\x89PNG\r\n\x1a\n", 0, ".png", "image/png", "image"),
    (b"\xff\xd8\xff", 0, ".jpg", "image/jpeg", "image"),
    (b"GIF87a", 0, ".gif", "image/gif", "image"),
    (b"GIF89a", 0, ".gif", "image/gif", "image"),
    (b"BM", 0, ".bmp", "image/bmp", "image"),
    (b"7z\xbc\xaf\x27\x1c", 0, ".7z", "application/x-7z-compressed", "archive"),
    (b"Rar!\x1a\x07\x01\x00", 0, ".rar", "application/vnd.rar", "archive"),
    (b"Rar!\x1a\x07\x00", 0, ".rar", "application/vnd.rar", "archive"),
    (b"\x1f\x8b\x08", 0, ".gz", "application/gzip", "archive"),
    (b"MZ", 0, ".exe", "application/vnd.microsoft.portable-executable", "executable"),
    (b"ID3", 0, ".mp3", "audio/mpeg", "audio"),
    (b"\xff\xfb", 0, ".mp3", "audio/mpeg", "audio"),
    (b"\xff\xf3", 0, ".mp3", "audio/mpeg", "audio"),
)

_ZIP_MAGICS = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")


def _disambiguate_zip_container(file_path: Path | str) -> SniffResult:
    """Inspect ZIP central directory to distinguish .3mf, .docx, .xlsx, .pptx, and .zip."""
    try:
        with zipfile.ZipFile(file_path, "r") as zf:
            names = {n.replace("\\", "/").lstrip("/").lower() for n in zf.namelist()}
    except (zipfile.BadZipFile, OSError, ValueError):
        return SniffResult(".zip", "application/zip", "zip", 0.7, "magic_zip_unreadable")

    if "3d/3dmodel.model" in names or any(n.startswith("3d/") and n.endswith(".model") for n in names):
        return SniffResult(".3mf", "model/3mf", "3mf", 0.99, "zip_container")
    if "word/document.xml" in names:
        return SniffResult(
            ".docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "ooxml_word",
            0.99,
            "zip_container",
        )
    if "xl/workbook.xml" in names:
        return SniffResult(
            ".xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "ooxml_excel",
            0.99,
            "zip_container",
        )
    if "ppt/presentation.xml" in names:
        return SniffResult(
            ".pptx",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "ooxml_ppt",
            0.99,
            "zip_container",
        )
    return SniffResult(".zip", "application/zip", "zip", 0.95, "zip_container")


def sniff_magic(
    file_path: Path | str | None = None,
    header_bytes: bytes | None = None,
) -> SniffResult | None:
    """Identify file format from magic bytes and (for ZIPs) container structure."""
    buf = header_bytes
    if buf is None:
        if file_path is None:
            return None
        try:
            with open(file_path, "rb") as fh:
                buf = fh.read(4096)
        except OSError:
            return None

    if not buf:
        return None

    for sig, offset, ext, mime, kind in _SIMPLE_MAGIC:
        if len(buf) >= offset + len(sig) and buf[offset : offset + len(sig)] == sig:
            return SniffResult(ext, mime, kind, 0.98, "magic_bytes")

    # RIFF containers (WEBP, WAV, AVI)
    if len(buf) >= 12 and buf[:4] == b"RIFF":
        sub = buf[8:12]
        if sub == b"WEBP":
            return SniffResult(".webp", "image/webp", "image", 0.98, "magic_bytes")
        if sub == b"WAVE":
            return SniffResult(".wav", "audio/wav", "audio", 0.98, "magic_bytes")
        if sub == b"AVI ":
            return SniffResult(".avi", "video/x-msvideo", "video", 0.98, "magic_bytes")

    # ISO Base Media (MP4 / MOV)
    if len(buf) >= 12 and buf[4:8] == b"ftyp":
        brand = buf[8:12]
        if brand == b"qt  ":
            return SniffResult(".mov", "video/quicktime", "video", 0.95, "magic_bytes")
        return SniffResult(".mp4", "video/mp4", "video", 0.95, "magic_bytes")

    # ZIP container family
    if any(buf.startswith(m) for m in _ZIP_MAGICS):
        if file_path is not None:
            return _disambiguate_zip_container(file_path)
        return SniffResult(".zip", "application/zip", "zip", 0.85, "magic_bytes")

    # ASCII STL heuristic
    head_lower = buf[:512].lstrip().lower()
    if head_lower.startswith(b"solid ") and (b"facet normal" in buf[:2048].lower() or b"endsolid" in buf[:2048].lower()):
        return SniffResult(".stl", "model/stl", "stl_ascii", 0.92, "ascii_header")

    return None


# ---------------------------------------------------------------------------
# 3. Pre-Routing ZIP Archive Content Peek
# ---------------------------------------------------------------------------

_3D_EXTS = frozenset({".stl", ".3mf", ".obj", ".step", ".stp", ".iges", ".igs", ".scad", ".blend"})
_GCODE_EXTS = frozenset({".nc", ".tap", ".ngc", ".gcode", ".bgcode", ".gco"})
_DOC_EXTS = frozenset({".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".md"})
_CODE_EXTS = frozenset({".py", ".js", ".ts", ".c", ".cpp", ".h", ".hpp", ".rs", ".go", ".java", ".cs"})
_MEDIA_EXTS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp4", ".mov", ".mp3", ".wav"})


@dataclass(frozen=True)
class ZipPeekResult:
    """Summary of archive contents prior to extraction or routing."""

    valid: bool
    file_count: int
    categories: dict[str, int] = field(default_factory=dict)
    dominant_category: str = ""
    suggested_destination: str = ""
    matched_keyword_rule: str = ""
    matched_trigger: str = ""
    has_path_traversal: bool = False
    sample_names: tuple[str, ...] = ()


def _is_zip_slip_entry(entry_name: str) -> bool:
    norm = entry_name.replace("\\", "/")
    if norm.startswith("/") or (len(norm) >= 2 and norm[1] == ":"):
        return True
    parts = [p for p in norm.split("/") if p]
    return ".." in parts


def peek_zip_contents(
    file_path: Path | str,
    keyword_rules: list[dict[str, Any]] | None = None,
) -> ZipPeekResult:
    """Inspect a ZIP archive's central directory without extracting any files."""
    try:
        with zipfile.ZipFile(file_path, "r") as zf:
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
        return ZipPeekResult(
            valid=True,
            file_count=0,
            has_path_traversal=has_traversal,
        )

    cats: dict[str, int] = {"3d": 0, "gcode": 0, "docs": 0, "code": 0, "media": 0, "other": 0}
    for f in files:
        ext = Path(f).suffix.lower()
        if ext in _3D_EXTS:
            cats["3d"] += 1
        elif ext in _GCODE_EXTS:
            cats["gcode"] += 1
        elif ext in _DOC_EXTS:
            cats["docs"] += 1
        elif ext in _CODE_EXTS:
            cats["code"] += 1
        elif ext in _MEDIA_EXTS:
            cats["media"] += 1
        else:
            cats["other"] += 1

    # Check inner filenames against keyword rules first (if provided)
    if keyword_rules:
        for rule in keyword_rules:
            if not rule.get("enabled", True):
                continue
            folder = str(rule.get("folder", "")).strip()
            if not folder:
                continue
            tokens = rule.get("keywords") or rule.get("tokens") or []
            for inner in files:
                inner_name = Path(inner).name
                for tok in tokens:
                    matched, _, _ = fuzzy_matches_keyword(inner_name, str(tok))
                    if matched:
                        return ZipPeekResult(
                            valid=True,
                            file_count=len(files),
                            categories=cats,
                            dominant_category="keyword_match",
                            suggested_destination=folder,
                            matched_keyword_rule=str(rule.get("name", folder)),
                            matched_trigger=f"{inner_name} ~ {tok}",
                            has_path_traversal=has_traversal,
                            sample_names=tuple(files[:10]),
                        )

    # Determine dominant category
    total = len(files)
    best_cat, best_count = max(cats.items(), key=lambda kv: kv[1])
    suggested = ""
    if cats["3d"] > 0 and (cats["3d"] / total >= 0.35 or best_cat == "3d"):
        best_cat = "3d"
        suggested = "3D Models"
    elif cats["gcode"] > 0 and cats["gcode"] >= cats["3d"]:
        best_cat = "gcode"
        suggested = "Code\\Gcode"
    elif best_count / total >= 0.5:
        suggested = {
            "docs": "Documents",
            "code": "Code",
            "media": "Images",
        }.get(best_cat, "")

    return ZipPeekResult(
        valid=True,
        file_count=total,
        categories=cats,
        dominant_category=best_cat,
        suggested_destination=suggested,
        has_path_traversal=has_traversal,
        sample_names=tuple(files[:10]),
    )


# ---------------------------------------------------------------------------
# 4. Fuzzy & Normalized Filename Token Matching
# ---------------------------------------------------------------------------

_COPY_SUFFIX_RE = re.compile(r"(?:\s*-\s*copy)?(?:\s*\(\d{1,3}\))+$|\s*-\s*copy$", re.IGNORECASE)
_CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])|(?<=[a-zA-Z])(?=[0-9])|(?<=[0-9])(?=[a-zA-Z])")

TOKEN_ALIASES: dict[str, tuple[str, ...]] = {
    "hw": ("homework",),
    "hmk": ("homework",),
    "syl": ("syllabus",),
    "syll": ("syllabus",),
    "lec": ("lecture",),
    "sol": ("solution", "solutions"),
    "soln": ("solution", "solutions"),
    "proj": ("project",),
    "sched": ("schedule",),
    "lab": ("laboratory",),
    "assn": ("assignment",),
    "asgmt": ("assignment",),
    "calc": ("calculus",),
    "thermo": ("thermodynamics",),
    "mech": ("mechanics", "mechanical"),
    "mfg": ("manufacturing",),
    "dwg": ("drawing",),
    "spec": ("specification",),
    "rev": ("review", "revision"),
}

_REVERSE_ALIASES: dict[str, set[str]] = {}
for _abbr, _expansions in TOKEN_ALIASES.items():
    for _exp in _expansions:
        _REVERSE_ALIASES.setdefault(_exp, set()).add(_abbr)


def normalize_filename_tokens(name: str, expand_aliases: bool = True) -> list[str]:
    """Normalize a filename into lowercase semantic tokens.

    Handles copy suffixes `(1)`, `- Copy`, delimiters (`_`, `-`, `.`),
    CamelCase boundaries (`BankStatement` -> `bank`, `statement`),
    alphanumeric compounds (`ME-3210` -> `me`, `3210`, `me3210`), and
    common academic/engineering abbreviations (`hw` -> `homework`).
    """
    p = Path(name)
    stem = _COPY_SUFFIX_RE.sub("", p.stem).strip()
    if not stem:
        stem = p.stem

    raw_chunks = [c for c in re.split(r"[\s_\-.,;:/\\()\[\]{}]+", stem) if c]
    tokens: list[str] = []
    seen: set[str] = set()

    def _add(tok: str) -> None:
        tl = tok.lower()
        if tl and tl not in seen:
            seen.add(tl)
            tokens.append(tl)

    for chunk in raw_chunks:
        _add(chunk)
        split_camel = _CAMEL_BOUNDARY_RE.split(chunk)
        if len(split_camel) > 1:
            for part in split_camel:
                _add(part)

    # Also emit adjacent alpha+digit joined tokens (e.g. "ME" + "3210" -> "me3210")
    for idx in range(len(raw_chunks) - 1):
        a, b = raw_chunks[idx], raw_chunks[idx + 1]
        if a.isalpha() and b.isdigit():
            _add(a + b)

    if expand_aliases:
        base_snapshot = list(tokens)
        for tok in base_snapshot:
            for exp in TOKEN_ALIASES.get(tok, ()):
                _add(exp)
            for abbr in _REVERSE_ALIASES.get(tok, ()):
                _add(abbr)

    return tokens


def fuzzy_matches_keyword(
    name: str,
    token: str,
    threshold: float = 0.84,
) -> tuple[bool, float, str]:
    """Match a filename against a keyword using normalization, aliases, and fuzzy ratio.

    Returns `(matched, score, reason)`.
    """
    clean_tok = token.strip().lower()
    if not clean_tok:
        return (False, 0.0, "")

    name_tokens = normalize_filename_tokens(name, expand_aliases=True)
    if not name_tokens:
        return (False, 0.0, "")

    kw_parts = [p for p in re.split(r"[\s_\-.]+", clean_tok) if p]
    if not kw_parts:
        return (False, 0.0, "")

    joined_kw = "".join(kw_parts)
    name_token_set = set(name_tokens)

    # 1. Exact single-token or delimiter-collapsed match (e.g. "me3210" matches "ME-3210")
    if joined_kw in name_token_set:
        return (True, 1.0, "normalized_exact")

    # 2. Multi-word keyword: all parts must appear (with alias expansion)
    if len(kw_parts) > 1:
        all_found = True
        for part in kw_parts:
            candidates = {part} | set(TOKEN_ALIASES.get(part, ())) | _REVERSE_ALIASES.get(part, set())
            if not (candidates & name_token_set):
                all_found = False
                break
        if all_found:
            return (True, 0.98, "multi_token_normalized")

    # 3. Fuzzy match (guarded to tokens >= 4 chars to prevent short-token false positives)
    if len(joined_kw) >= 4:
        best_ratio = 0.0
        for nt in name_tokens:
            if len(nt) < 4:
                continue
            ratio = difflib.SequenceMatcher(None, joined_kw, nt).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
        if best_ratio >= threshold:
            return (True, round(best_ratio, 3), "fuzzy_token")

    return (False, 0.0, "")


# ---------------------------------------------------------------------------
# 5. Pillow EXIF/Dimension Image Inspection & Optional PDF Page-1 Extraction
# ---------------------------------------------------------------------------

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
class ImageInspection:
    """Result of Pillow-based image metadata and dimension inspection."""

    available: bool
    category: str  # "camera_photo", "screenshot", "cad_render", "image", "error"
    width: int = 0
    height: int = 0
    image_format: str = ""
    camera_make: str = ""
    camera_model: str = ""
    software: str = ""
    suggested_destination: str = ""


def inspect_image_metadata(file_path: Path | str) -> ImageInspection:
    """Inspect image EXIF and dimensions using Pillow to distinguish photos, screenshots, and renders."""
    if not _HAS_PIL or Image is None:
        return ImageInspection(available=False, category="error")

    p = Path(file_path)
    try:
        with Image.open(p) as img:
            width, height = img.size
            fmt = (img.format or "").upper()
            exif_dict: dict[str, str] = {}
            try:
                raw_exif = img.getexif()
                if raw_exif and ExifTags is not None:
                    for tag_id, val in raw_exif.items():
                        tag_name = ExifTags.TAGS.get(tag_id, str(tag_id))
                        exif_dict[str(tag_name)] = str(val).strip()
            except Exception:
                pass
    except Exception:
        return ImageInspection(available=True, category="error")

    make = exif_dict.get("Make", "")
    model = exif_dict.get("Model", "")
    software = exif_dict.get("Software", "")
    sw_lower = software.lower()
    name_lower = p.name.lower()

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
            suggested_destination="Images\\Photos",
        )

    if any(h in sw_lower or h in name_lower for h in _CAD_SOFTWARE_HINTS):
        return ImageInspection(
            available=True,
            category="cad_render",
            width=width,
            height=height,
            image_format=fmt,
            software=software,
            suggested_destination="Images\\Renders",
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
            suggested_destination="Images\\Screenshots",
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


@dataclass(frozen=True)
class PdfSnippet:
    """Extracted PDF title and page-1 text snippet."""

    available: bool
    title: str = ""
    text: str = ""
    error: str = ""


def extract_pdf_page1_text(file_path: Path | str, max_chars: int = 2000) -> PdfSnippet:
    """Extract PDF metadata title and page-1 text, degrading gracefully if pypdf is missing."""
    if not _HAS_PYPDF or PdfReader is None:
        return PdfSnippet(available=False, error="pypdf_not_installed")

    try:
        reader = PdfReader(os.fspath(file_path))
        if getattr(reader, "is_encrypted", False):
            return PdfSnippet(available=True, error="encrypted_pdf")
        title = ""
        if reader.metadata and getattr(reader.metadata, "title", None):
            title = str(reader.metadata.title).strip()
        page1 = ""
        if len(reader.pages) > 0:
            page1 = (reader.pages[0].extract_text() or "").strip()
        combined = page1[:max_chars] if max_chars > 0 else page1
        return PdfSnippet(available=True, title=title, text=combined)
    except Exception as exc:
        return PdfSnippet(available=True, error=str(exc))


def match_pdf_keywords(
    file_path: Path | str,
    keyword_rules: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Run keyword rules against a PDF's title and page-1 text (for filename keyword misses)."""
    if not keyword_rules:
        return None
    snippet = extract_pdf_page1_text(file_path)
    haystack = f"{snippet.title} {snippet.text}".strip()
    if not haystack:
        return None

    for rule in keyword_rules:
        if not rule.get("enabled", True):
            continue
        folder = str(rule.get("folder", "")).strip()
        if not folder:
            continue
        tokens = rule.get("keywords") or rule.get("tokens") or []
        for tok in tokens:
            matched, score, reason = fuzzy_matches_keyword(haystack, str(tok))
            if matched:
                return {
                    "destination": folder,
                    "rule": str(rule.get("name", folder)),
                    "matched_trigger": str(tok),
                    "score": score,
                    "reason": f"pdf_page1_{reason}",
                }
    return None
