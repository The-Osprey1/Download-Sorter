"""Plain-text helpers for rules and folder cues: token cleanup and plain-English summaries.

Kept free of Qt so the Folders screen, the rule editor and tests can share them.
"""

from __future__ import annotations

import re

from download_sorter.router import extract_host

_MULTI_PART_TLDS: frozenset[str] = frozenset(
    {
        "co.uk",
        "org.uk",
        "ac.uk",
        "gov.uk",
        "com.au",
        "edu.au",
        "org.au",
        "net.au",
        "co.jp",
        "ne.jp",
        "ac.jp",
        "co.nz",
        "com.br",
        "co.in",
        "co.za",
        "com.mx",
        "com.sg",
        "com.hk",
        "com.tw",
        "co.kr",
    }
)


# Multi-product root domains where the service subdomain itself is meaningful.
_KEEP_SUBDOMAIN_ROOTS: frozenset[str] = frozenset(
    {
        "google.com",
        "live.com",
        "githubusercontent.com",
    }
)


def registrable_domain(raw: str) -> str:
    """Normalize a full URL or domain (e.g. ``https://dashboard.stripe.com/pay`` -> ``stripe.com``)."""
    text = str(raw or "").strip().strip("'\"")
    if not text:
        return ""
    host = extract_host(text)
    if not host or "." not in host:
        return ""
    parts = [p for p in host.split(".") if p]
    if len(parts) < 2 or any(not re.match(r"^[a-z0-9-]+$", p) for p in parts):
        return ""
    if len(parts) == 2:
        return host
    tail2 = ".".join(parts[-2:])
    if tail2 in _MULTI_PART_TLDS:
        return ".".join(parts[-3:]) if len(parts) >= 3 else host
    if tail2 in _KEEP_SUBDOMAIN_ROOTS:
        return ".".join(parts[-3:])
    return tail2


def registrable_domain_tokens(raw_or_list: str | list[str] | tuple[str, ...]) -> list[str]:
    """Parse and normalize comma/space-separated URLs or domain tokens."""
    if isinstance(raw_or_list, str):
        parts = re.split(r"[,;\s]+", raw_or_list)
    else:
        parts = []
        for item in raw_or_list:
            parts.extend(re.split(r"[,;\s]+", str(item or "")))
    out: list[str] = []
    for part in parts:
        norm = registrable_domain(part)
        if norm and norm not in out:
            out.append(norm)
    return out


NEW_FOLDER_SENTINEL = "__NEW_FOLDER__"

QUICK_FILE_TYPE_CHIPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (".pdf", (".pdf",)),
    (".docx", (".docx",)),
    (".xlsx/.csv", (".xlsx", ".csv")),
    (".png/.jpg", (".png", ".jpg")),
    (".zip", (".zip",)),
    (".3mf/.stl", (".3mf", ".stl")),
    (".exe", (".exe",)),
)
_CHIP_EXT_SET: frozenset[str] = frozenset(
    ext for _lbl, exts in QUICK_FILE_TYPE_CHIPS for ext in exts
)


def normalize_ext_tokens(raw: str | list[str] | tuple[str, ...]) -> list[str]:
    """Normalize file extension tokens (e.g. ``'pdf, *.DOCX, .xlsx/.csv'`` -> ``['.pdf', '.docx', '.xlsx', '.csv']``)."""
    pieces: list[str] = []
    if isinstance(raw, str):
        pieces = [p for p in re.split(r"[,;\s/]+", raw) if p]
    else:
        for item in raw:
            pieces.extend(p for p in re.split(r"[,;\s/]+", str(item or "")) if p)
    out: list[str] = []
    for tok in pieces:
        cleaned = tok.strip().lstrip("*").strip().lower()
        if not cleaned:
            continue
        if not cleaned.startswith("."):
            cleaned = "." + cleaned
        if len(cleaned) < 2 or not re.match(r"^\.[a-z0-9_+-]+$", cleaned):
            continue
        if cleaned not in out:
            out.append(cleaned)
    return out


def normalize_domain_tokens(raw: str | list[str] | tuple[str, ...]) -> list[str]:
    """Normalize website URLs or domains via ``extract_host`` (``'https://stripe.com/pay'`` -> ``'stripe.com'``)."""
    pieces: list[str] = []
    if isinstance(raw, str):
        pieces = [p for p in re.split(r"[,;\s]+", raw) if p]
    else:
        for item in raw:
            pieces.extend(p for p in re.split(r"[,;\s]+", str(item or "")) if p)
    out: list[str] = []
    for tok in pieces:
        cleaned = tok.strip().strip("\"'")
        if not cleaned:
            continue
        host = extract_host(cleaned)
        if not host:
            continue
        if host not in out:
            out.append(host)
    return out


def normalize_word_tokens(raw: str | list[str] | tuple[str, ...]) -> list[str]:
    """Normalize comma-separated filename keyword tokens cleanly."""
    pieces: list[str] = []
    if isinstance(raw, str):
        pieces = raw.split(",")
    else:
        pieces = [str(item or "") for item in raw]
    out: list[str] = []
    for tok in pieces:
        cleaned = tok.strip().strip("\"'")
        if cleaned and cleaned not in out:
            out.append(cleaned)
    return out


def rule_has_any_cue(
    *,
    extensions: list[str] | tuple[str, ...] = (),
    keywords: list[str] | tuple[str, ...] = (),
    domains: list[str] | tuple[str, ...] = (),
    content_keywords: list[str] | tuple[str, ...] = (),
    regex_pattern: str = "",
    min_size_mb: float = 0.0,
    max_size_mb: float = 0.0,
    min_age_days: int = 0,
) -> bool:
    """Return True when at least one positive file-matching cue is configured."""
    return bool(
        extensions
        or keywords
        or domains
        or content_keywords
        or (regex_pattern or "").strip()
        or min_size_mb > 0
        or max_size_mb > 0
        or min_age_days > 0
    )


def suggest_rule_name(
    extensions: list[str],
    keywords: list[str],
    domains: list[str],
    destination: str,
) -> str:
    """Generate a concise human-friendly rule name when the user leaves the title blank."""
    leaf = destination.replace("/", "\\").strip("\\").split("\\")[-1] or "Sorted"
    if domains and keywords:
        return f"{domains[0]} ({keywords[0]})"
    if domains:
        return f"From {domains[0]}"
    if keywords:
        return f"{leaf} ({keywords[0]})"
    if extensions:
        ext_label = ", ".join(extensions[:2])
        return f"{ext_label} → {leaf}"
    return f"{leaf} Rule"


def _join_or(items: list[str]) -> str:
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} or {items[1]}"
    return f"{', '.join(items[:-1])}, or {items[-1]}"


def describe_rule_plain_english(
    *,
    extensions: list[str] | tuple[str, ...] = (),
    keywords: list[str] | tuple[str, ...] = (),
    domains: list[str] | tuple[str, ...] = (),
    destination: str = "Documents",
    match_mode: str = "any",
    exclude_keywords: list[str] | tuple[str, ...] = (),
    content_keywords: list[str] | tuple[str, ...] = (),
    regex_pattern: str = "",
    min_size_mb: float = 0.0,
    max_size_mb: float = 0.0,
    min_age_days: int = 0,
) -> str:
    """Build a live plain-English summary sentence (or completeness prompt when empty)."""
    exts = list(extensions)
    words = list(keywords)
    doms = list(domains)
    ckws = list(content_keywords)
    excl = list(exclude_keywords)
    rx = (regex_pattern or "").strip()

    if not rule_has_any_cue(
        extensions=exts,
        keywords=words,
        domains=doms,
        content_keywords=ckws,
        regex_pattern=rx,
        min_size_mb=min_size_mb,
        max_size_mb=max_size_mb,
        min_age_days=min_age_days,
    ):
        return "Add at least one file type, filename word, or website above"

    if exts:
        shown_exts = exts[:3]
        ext_part = _join_or(shown_exts)
        if len(exts) > 3:
            ext_part = f"{', '.join(shown_exts)} (+{len(exts) - 3})"
        subject = f"{ext_part} files"
    else:
        subject = "files"

    clauses: list[str] = []
    if words:
        quoted = [f"'{w}'" for w in words[:3]]
        w_str = _join_or(quoted)
        if len(words) > 3:
            w_str += f" (+{len(words) - 3})"
        clauses.append(f"containing {w_str}")
    if doms:
        d_str = _join_or(doms[:2])
        if len(doms) > 2:
            d_str += f" (+{len(doms) - 2})"
        clauses.append(f"from {d_str}")
    if ckws:
        clauses.append(f"with PDF text '{ckws[0]}'")
    if rx:
        clauses.append(f"matching /{rx}/")

    joiner = " and " if match_mode == "all" else " or "
    cond_str = f" {joiner.join(clauses)}" if clauses else ""

    bounds: list[str] = []
    if min_size_mb > 0:
        bounds.append(f"≥{min_size_mb:g} MB")
    if max_size_mb > 0:
        bounds.append(f"≤{max_size_mb:g} MB")
    if min_age_days > 0:
        bounds.append(f"≥{min_age_days}d old")
    bound_str = f" ({', '.join(bounds)})" if bounds else ""

    excl_str = ""
    if excl:
        excl_quoted = ", ".join(f"'{x}'" for x in excl[:2])
        excl_str = f" (except {excl_quoted})"

    dest_crumb = destination.replace("/", "\\").strip("\\").replace("\\", " › ") or "Downloads"
    return f"Moves {subject}{cond_str}{bound_str}{excl_str} → {dest_crumb}"


