"""Pure, deterministic 4-step routing engine with step-by-step trace ('Why')."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from urllib.parse import urlparse
import zlib

from download_sorter.config import Config, Rule
from download_sorter.dest import resolve_destination_tokens, to_relative_dest

_NON_WORD_RE = re.compile(r"[^a-z0-9]+")
_PDF_STREAM_RE = re.compile(rb"stream\r?\n(.*?)\r?\nendstream", re.DOTALL)
_PDF_TEXT_LIT_RE = re.compile(rb"\(([^()\\]{2,200})\)")


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
            decompressed = zlib.decompress(stream_bytes)
        except zlib.error:
            continue
        for lit in _PDF_TEXT_LIT_RE.finditer(decompressed[:65536]):
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
    """Normalize separators (underscore, hyphen, punctuation) into single lowercase spaces."""
    return " ".join(_NON_WORD_RE.sub(" ", text.lower()).split())


def _keyword_matches(filename: str, keyword: str) -> bool:
    norm_kw = _normalize_tokens(keyword)
    if not norm_kw:
        return False
    norm_name = _normalize_tokens(Path(filename).stem)
    # Match whole phrase boundaries so e.g. 'cv' does not match inside 'opencv' or 'csv'.
    padded_name = f" {norm_name} "
    padded_kw = f" {norm_kw} "
    if padded_kw in padded_name:
        return True
    # Also support compacted token matching (e.g. 'ime3311' vs 'ime 3311').
    if len(norm_kw) >= 4 and norm_kw.replace(" ", "") in norm_name.replace(" ", ""):
        return True
    return False


def _domain_matches(host: str, domains: list[str]) -> str | None:
    if not host or not domains:
        return None
    for d in domains:
        clean = extract_host(d)
        if clean and (host == clean or host.endswith("." + clean)):
            return clean
    return None


def _evaluate_rule(rule: Rule, candidate: FileCandidate) -> tuple[bool, str]:
    if not rule.enabled:
        return False, "Rule is disabled"

    ext = candidate.extension
    if rule.extensions and ext not in rule.extensions:
        return False, f"Extension '{ext or '(none)'}' not in {rule.extensions}"

    if not rule.keywords and not rule.domains and not rule.content_keywords:
        return True, f"Extension '{ext}' matched rule with no keyword/domain filter"

    matched_domain = _domain_matches(candidate.host, rule.domains)
    if matched_domain:
        return True, f"Download domain '{candidate.host}' matched '{matched_domain}'"

    matched_tab_domain = _domain_matches(candidate.tab_host, rule.domains)
    if matched_tab_domain:
        return True, f"Active tab domain '{candidate.tab_host}' matched '{matched_tab_domain}'"

    for kw in rule.keywords:
        if _keyword_matches(candidate.stem, kw):
            return True, f"Filename matched keyword '{kw}'"

    if rule.content_keywords and ext == ".pdf":
        preview = candidate.content_preview or extract_pdf_text_preview(candidate.path)
        norm_preview = f" {_normalize_tokens(preview)} " if preview else ""
        for ckw in rule.content_keywords:
            norm_ckw = _normalize_tokens(ckw)
            if norm_ckw and f" {norm_ckw} " in norm_preview:
                return True, f"PDF content matched '{ckw}'"

    parts: list[str] = []
    if rule.domains:
        parts.append(f"domain {candidate.host or '(none)'} did not match {rule.domains}")
    if rule.keywords:
        parts.append("no keyword matched")
    if rule.content_keywords:
        parts.append("no PDF content keyword matched")
    return False, "; ".join(parts)


def evaluate(candidate: FileCandidate, config: Config) -> RouteDecision:
    """Evaluate a file candidate strictly top-to-bottom in 4 predictable steps."""
    trace: list[RouteStep] = []
    name_lower = candidate.filename.lower()
    ext = candidate.extension

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

    # Step 2: Explicit Ordered User Rules (First Match Wins)
    for idx, rule in enumerate(config.rules, start=1):
        ok, detail = _evaluate_rule(rule, candidate)
        trace.append(RouteStep("User Rules", f"#{idx} {rule.name}", ok, detail))
        if ok:
            expanded_dest = resolve_destination_tokens(rule.destination, host=effective_host, ext=ext)
            dest_rel = Path(to_relative_dest(expanded_dest)) / candidate.filename
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
        expanded_cat = resolve_destination_tokens(raw_cat, host=effective_host, ext=ext)
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

    expanded_default = resolve_destination_tokens(config.default_folder, host=effective_host, ext=ext)
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
