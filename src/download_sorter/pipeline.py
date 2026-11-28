"""Unified Sorting Flow trace & Teach-by-Example inference engine.

Wraps :func:`download_sorter.router.evaluate`, :class:`download_sorter.service.SorterService`
hold rules, and :mod:`download_sorter.learning` AI hints so the Sorting Flow UI, CLI
``--explain``, Detox batch cleanup, and live watcher always share one source of truth.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import TYPE_CHECKING, Any

from download_sorter.config import Config, Rule
from download_sorter.router import (
    FileCandidate,
    RouteDecision,
    _evaluate_rule,
    candidate_from_path,
    evaluate,
    extract_host,
)

if TYPE_CHECKING:
    from download_sorter.service import SorterService


@dataclass(frozen=True)
class TypeBucket:
    """Visual file-type bucket shown in Step 3 of Sorting Flow."""

    id: str
    name: str
    default_folder: str
    extensions: tuple[str, ...]
    unzippable: bool = False


TYPE_BUCKETS: tuple[TypeBucket, ...] = (
    TypeBucket(
        id="t-docs",
        name="Documents",
        default_folder="Documents",
        extensions=(".pdf", ".doc", ".docx", ".xls", ".xlsx", ".csv", ".ppt", ".pptx", ".txt", ".md", ".rtf", ".epub"),
    ),
    TypeBucket(
        id="t-imgs",
        name="Images",
        default_folder="Images",
        extensions=(".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".heic", ".avif", ".ico"),
    ),
    TypeBucket(
        id="t-3d",
        name="3D & CAD",
        default_folder="3D Models",
        extensions=(".3mf", ".stl", ".obj", ".gcode", ".bgcode", ".step", ".stp", ".iges", ".igs", ".f3d", ".sldprt", ".dxf"),
    ),
    TypeBucket(
        id="t-media",
        name="Video & Audio",
        default_folder="Videos",
        extensions=(".mp4", ".mkv", ".mov", ".avi", ".webm", ".mp3", ".wav", ".flac", ".m4a", ".ogg"),
    ),
    TypeBucket(
        id="t-arc",
        name="Archives",
        default_folder="Archives",
        extensions=(".zip", ".7z", ".rar", ".tar", ".gz", ".xz"),
        unzippable=True,
    ),
    TypeBucket(
        id="t-app",
        name="Installers",
        default_folder="Installers",
        extensions=(".exe", ".msi", ".msix", ".appx", ".dmg", ".pkg"),
    ),
    TypeBucket(
        id="t-code",
        name="Code",
        default_folder="Code",
        extensions=(".py", ".js", ".ts", ".html", ".css", ".json", ".yaml", ".yml", ".cpp", ".c", ".rs", ".go"),
    ),
)

_EXT_TO_BUCKET_ID: dict[str, str] = {
    ext: bucket.id for bucket in TYPE_BUCKETS for ext in bucket.extensions
}


@dataclass(frozen=True)
class PipelineTrace:
    """Complete, UI-ready explanation of how a file moves through Steps 1 -> 2 -> 3."""

    filename: str
    source_url: str
    winning_step: int  # 0 = idle/empty, 1 = Hold/Ignore, 2 = Priority Rules, 3 = File Types / Fallback
    outcome: str  # "idle" | "ignore" | "hold" | "move" | "ai" | "leave"
    decision: RouteDecision
    hold_id: str = ""
    matched_rule_id: str = ""
    shadowed_rule_ids: tuple[str, ...] = ()
    blocked_rules: dict[str, str] = field(default_factory=dict)
    matched_bucket_id: str = ""
    ai_matched: bool = False
    ai_confidence: float = 0.0
    dest_folder: str = ""
    final_filename: str = ""
    human_reason: str = ""


def bucket_for_extension(ext: str) -> str:
    """Return the Step 3 bucket id for ``ext``, or empty string if unmapped."""
    return _EXT_TO_BUCKET_ID.get(ext.strip().lower(), "")


def bucket_target_folders(bucket: TypeBucket, config: Config) -> set[str]:
    """Return every destination folder a Step 3 bucket can route into under ``config``."""
    targets: set[str] = set()
    for ext in bucket.extensions:
        base = str(config.category_map.get(ext, "") or "").replace("/", "\\").strip("\\")
        if not base:
            continue
        targets.add(base)
        if config.use_type_subfolders:
            sub = str(config.type_subfolders.get(ext, "") or "").strip("\\")
            if sub:
                targets.add(f"{base}\\{sub}")
    if not targets:
        targets.add(bucket.default_folder)
    return targets


def _inspect_all_rules(
    candidate: FileCandidate,
    config: Config,
    *,
    effective_ext: str,
) -> tuple[list[str], dict[str, str]]:
    """Evaluate every enabled rule so the UI can flag shadowed matches and exclude blocks."""
    matched_ids: list[str] = []
    blocked: dict[str, str] = {}
    name_lower = candidate.filename.lower()
    for rule in config.rules:
        if not rule.enabled:
            continue
        ok, detail = _evaluate_rule(rule, candidate, effective_ext=effective_ext)
        if ok:
            matched_ids.append(rule.id)
        elif "excluded" in detail.lower() or "exclude" in detail.lower():
            for ex in rule.exclude_keywords:
                ex_l = ex.strip().lower()
                if ex_l and ex_l in name_lower:
                    blocked[rule.id] = ex_l
                    break
            if rule.id not in blocked:
                blocked[rule.id] = "exclude"
    return matched_ids, blocked


def trace_file(
    filename: str,
    config: Config,
    *,
    source_url: str = "",
    path: Path | None = None,
    service: SorterService | None = None,
    simulate_held_3mf: bool = False,
) -> PipelineTrace:
    """Evaluate ``filename`` (or ``path``) through Steps 1 -> 2 -> 3 with full trace metadata."""
    clean_name = (filename or "").strip()
    if not clean_name and path is None:
        empty_dec = RouteDecision("leave", None, "Enter or drop a file to trace.", "", "fallback", [])
        return PipelineTrace(
            filename="",
            source_url=source_url,
            winning_step=0,
            outcome="idle",
            decision=empty_dec,
            human_reason="Drop or type any file name above to trace its path.",
        )

    if path is not None and path.is_file():
        cand = candidate_from_path(path, lazy_zone=False)
        if source_url and not cand.source_url:
            cand = FileCandidate(
                filename=cand.filename,
                path=cand.path,
                source_url=source_url,
                content_preview=cand.content_preview,
                size_bytes=cand.size_bytes,
                age_days=cand.age_days,
            )
    else:
        cand = FileCandidate(filename=clean_name, path=path, source_url=source_url)

    ext = cand.extension
    all_matched_ids, blocked = _inspect_all_rules(cand, config, effective_ext=ext)

    # Step 1: Ignore Guard or Active .3MF Hold
    decision = evaluate(cand, config, include_trace=True)
    if service is not None:
        decision = service._append_ml_hint(cand, decision, cfg=config)

    if decision.action == "ignore":
        return PipelineTrace(
            filename=cand.filename,
            source_url=cand.source_url,
            winning_step=1,
            outcome="ignore",
            decision=decision,
            hold_id="h-ignore",
            shadowed_rule_ids=tuple(all_matched_ids),
            blocked_rules=blocked,
            final_filename=cand.filename,
            human_reason=f"Waits quietly: {decision.reason}",
        )

    is_held_3mf = False
    if ext == ".3mf" and config.hold_3mf_enabled and config.hold_3mf_days > 0:
        if path is not None and service is not None:
            is_held_3mf = service._is_3mf_held(path, config)
        elif simulate_held_3mf or path is None:
            is_held_3mf = True

    if is_held_3mf:
        return PipelineTrace(
            filename=cand.filename,
            source_url=cand.source_url,
            winning_step=1,
            outcome="hold",
            decision=decision,
            hold_id="h-3mf",
            shadowed_rule_ids=tuple(all_matched_ids),
            blocked_rules=blocked,
            dest_folder="__ROOT_HOLD__",
            final_filename=cand.filename,
            human_reason=(
                f"Held at the top of Downloads for {config.hold_3mf_days} day(s) "
                "so your 3D slicer can open it."
            ),
        )

    dest_folder = ""
    final_name = cand.filename
    if decision.relative_dest is not None:
        dest_folder = str(decision.relative_dest.parent).replace("/", "\\")
        if dest_folder == ".":
            dest_folder = ""
        final_name = decision.relative_dest.name

    dest_pretty = (dest_folder or "Downloads").replace("\\", " › ")

    # Step 2: Explicit User Rules
    if decision.match_type == "rule":
        winning_rule_id = ""
        for r in config.rules:
            if r.enabled and r.name == decision.matched_rule:
                winning_rule_id = r.id
                break
        if not winning_rule_id and all_matched_ids:
            winning_rule_id = all_matched_ids[0]
        shadowed = tuple(rid for rid in all_matched_ids if rid != winning_rule_id)
        return PipelineTrace(
            filename=cand.filename,
            source_url=cand.source_url,
            winning_step=2,
            outcome="move",
            decision=decision,
            matched_rule_id=winning_rule_id,
            shadowed_rule_ids=shadowed,
            blocked_rules=blocked,
            dest_folder=dest_folder,
            final_filename=final_name,
            human_reason=f"Moves into {dest_pretty} — {decision.reason}",
        )

    # Step 3: Offline AI Smart Route
    if decision.match_type == "ai_smart_route":
        return PipelineTrace(
            filename=cand.filename,
            source_url=cand.source_url,
            winning_step=3,
            outcome="ai",
            decision=decision,
            blocked_rules=blocked,
            ai_matched=True,
            dest_folder=dest_folder,
            final_filename=final_name,
            human_reason=f"Classified offline into {dest_pretty} — {decision.reason}",
        )

    # Step 3: Built-in File Type Routing or Fallback
    bucket_id = bucket_for_extension(ext)
    if decision.action == "move" and decision.match_type == "extension":
        return PipelineTrace(
            filename=cand.filename,
            source_url=cand.source_url,
            winning_step=3,
            outcome="move",
            decision=decision,
            blocked_rules=blocked,
            matched_bucket_id=bucket_id,
            dest_folder=dest_folder,
            final_filename=final_name,
            human_reason=f"Moves into {dest_pretty} by file type ({ext or 'detected'}).",
        )

    if decision.action == "move":
        return PipelineTrace(
            filename=cand.filename,
            source_url=cand.source_url,
            winning_step=3,
            outcome="move",
            decision=decision,
            blocked_rules=blocked,
            dest_folder=dest_folder,
            final_filename=final_name,
            human_reason=f"Unrecognized extension — moves into {dest_pretty or 'Other'}.",
        )

    return PipelineTrace(
        filename=cand.filename,
        source_url=cand.source_url,
        winning_step=3,
        outcome="leave",
        decision=decision,
        blocked_rules=blocked,
        final_filename=cand.filename,
        human_reason="Unrecognized extension — left safely in Downloads.",
    )


def infer_teach_cues(filename: str, source_url: str = "") -> dict[str, Any]:
    """Infer toggleable Teach-by-Example rule cues from a dropped file and its source URL."""
    p = Path(filename)
    ext = p.suffix.lower()
    raw_tokens = [tok for tok in re.split(r"[^a-z0-9]+", p.stem.lower()) if len(tok) >= 3]
    # Prefer the first non-numeric word as the default selected keyword
    first_word_idx = next((i for i, tok in enumerate(raw_tokens) if not tok.isdigit()), -1)

    word_cues: list[dict[str, Any]] = []
    seen: set[str] = set()
    for idx, tok in enumerate(raw_tokens):
        if tok in seen:
            continue
        seen.add(tok)
        word_cues.append(
            {
                "kind": "word",
                "value": tok,
                "label": f'"{tok}"',
                "checked": idx == first_word_idx,
            }
        )
        if len(word_cues) >= 3:
            break

    host = extract_host(source_url)
    best_word = raw_tokens[first_word_idx] if first_word_idx >= 0 else (raw_tokens[0] if raw_tokens else "")
    return {
        "filename": p.name,
        "ext": ext,
        "host": host,
        "best_word": best_word,
        "word_cues": word_cues,
    }


def build_rule_from_cues(
    *,
    title: str,
    destination: str,
    words: list[str],
    extensions: list[str],
    domains: list[str],
) -> Rule:
    """Construct a validated :class:`Rule` from Teach-by-Example cues."""
    return Rule(
        id=f"teach_{abs(hash((title, destination, tuple(words), tuple(domains)))) % 10_000_000}",
        name=title.strip() or "New Rule",
        destination=destination.replace("/", "\\").strip("\\") or "Other",
        keywords=[w.strip() for w in words if w.strip()],
        domains=[d.strip() for d in domains if d.strip()],
        extensions=[e.strip().lower() for e in extensions if e.strip()],
        enabled=True,
    )
