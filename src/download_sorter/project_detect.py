"""Automatic project cluster detection for a folder inside the watch directory.

Scans a folder (and its file-type subfolders, while skipping already-mapped
classification/project subfolders) and discovers cohesive multi-file projects
from filename anchors, structured codes, cross-extension cohesion, and
temporal bursts. Each detected project produces high-precision keywords
verified against ``download_sorter.router._keyword_matches`` so adding the
project to ``FolderArchitecture`` routes both existing and future downloads.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass
import math
import os
from pathlib import Path
import re
from typing import Any

from download_sorter.architecture import _SCAN_SKIP_DIRS
from download_sorter.router import _keyword_matches

ProgressCallback = Callable[[int, int, str], None]

_COPY_SUFFIX_RE = re.compile(r"(?:\s*-\s*copy)?(?:\s*\(\d{1,3}\))+$|\s*-\s*copy$", re.IGNORECASE)
_VERSION_RE = re.compile(
    r"\b(?:v|ver|version|rev|revision|iter|iteration|edit|plate|part|pt|step|iss|draft|try)"
    r"\s*[_\-.]?\s*\d+(?:\.\d+)*\b",
    re.IGNORECASE,
)
_DATE_RE = re.compile(r"\b(?:\d{4}[-_]\d{1,2}[-_]\d{1,2}|\d{1,2}[-_]\d{1,2}[-_]\d{2,4}(?=[A-Za-z]|\b)|\d{8})\b")
_DIM_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:mm|cm|in|oz|lbs|dpi|deg|c|a|khz|mhz|gb|mb|px|x\d+(?:mm|px)?)\b",
    re.IGNORECASE,
)
_COURSE_CODE_RE = re.compile(r"\b([a-z]{2,4})[\s_-]?(\d{3,4}[a-z]?)\b", re.IGNORECASE)
_HYPHEN_CODE_RE = re.compile(r"\b(\d{2}-[a-z0-9&]*[a-z][a-z0-9&]{0,5})\b", re.IGNORECASE)

# Prefixes that look like <letters><digits> but are NOT course or project codes.
_NON_COURSE_PREFIXES: frozenset[str] = frozenset(
    {
        "v", "ver", "rev", "img", "dsc", "pxl", "win", "utf", "iso", "mp", "vid",
        "ch", "fig", "sec", "tab", "box", "bit", "pin", "led", "usb", "cad", "cam",
        "cnc", "pcb", "esp", "stm", "mcu", "arm", "ram", "rom", "ssd", "hdd", "gpu",
        "cpu", "psu", "fan", "pla", "tpu", "pet", "abs", "ams", "stl", "obj", "svg",
        "png", "jpg", "pdf", "doc", "xls", "ppt", "zip", "rar", "exe", "msi", "dmg",
        "pkg", "bak", "tmp", "log", "out", "bin", "dat", "hex", "elf", "lib", "dll",
        "sys", "cfg", "ini", "xml", "yml", "sql", "csv", "txt", "md", "py", "js",
        "ts", "cpp", "hpp", "ino", "part", "body", "step", "item", "page", "file",
        "line", "copy", "edit", "shot", "clip", "snip", "test", "demo", "temp",
        "a1", "p1", "x1", "h2", "mk", "no", "id", "pr", "v2", "v3",
    }
)

# Noise tokens stripped before building project anchors.
_STRIP_NOISE_WORDS: frozenset[str] = frozenset(
    {
        "the", "and", "for", "with", "from", "into", "of", "in", "on", "to", "by", "at",
        "new", "old", "copy", "final", "draft", "updated", "good", "bad", "full",
        "file", "files", "model", "models", "part", "parts", "body", "untitled",
        "document", "documents", "image", "images", "img", "photo", "photos",
        "video", "videos", "screenshot", "screenshots", "snip", "project", "projects",
        "design", "export", "download", "downloads", "test", "sample", "example",
        "default", "top", "bottom", "left", "right", "front", "back", "side", "base",
        "lid", "cover", "plate", "plates", "pla", "petg", "tpu", "abs", "ams", "bambu", "prusa", "orca",
        "gcode", "stl", "3mf", "step", "stp", "f3d", "f3z", "fusion", "solidworks",
        "pdf", "docx", "xlsx", "pptx", "png", "jpg", "jpeg", "webp", "svg",
        "a1mini", "h2d", "x1c", "p1s", "mini", "all", "set", "one", "two", "three",
        "single", "multi", "color", "colored", "print", "printed", "printing", "ready",
        "version", "rev", "edition", "generated", "gemini", "chatgpt", "batch",
        "landscape", "portrait", "class", "struct", "hw", "hmk", "hmwk", "homework",
        "assignment", "lab", "report", "slides", "presentation", "template",
        "solution", "solutions", "fall", "spring", "summer", "winter", "notes",
        "profile", "profiles", "normal", "small", "large", "big", "long", "longer",
        "short", "better", "extra", "super", "pro", "plus", "air", "standard",
        "official", "community", "yourlastname", "shared", "archive", "backup",
    }
)

# Generic nouns that are fine INSIDE a 2-word bigram ("Watch Case", "Bottle Opener")
# but should NEVER stand alone as a 1-word project name.
_GENERIC_UNIGRAM_BLOCKLIST: frozenset[str] = frozenset(
    {
        "plate", "plates", "holder", "holders", "case", "cases", "stand", "stands",
        "mount", "mounts", "bracket", "brackets", "adapter", "adapters", "pattern",
        "patterns", "sheet", "sheets", "engineering", "release", "releases", "keyword",
        "keywords", "config", "configs", "driver", "drivers", "audio", "logo", "logos",
        "icon", "icons", "resume", "resumes", "cover", "covers", "clip", "clips",
        "box", "boxes", "tray", "trays", "handle", "handles", "head", "heads",
        "arm", "arms", "wall", "walls", "ring", "rings", "pin", "pins", "gear", "gears",
        "shaft", "shafts", "cap", "caps", "tool", "tools", "kit", "kits", "pack",
        "packs", "bundle", "bundles", "guide", "guides", "manual", "manuals", "form",
        "forms", "list", "lists", "data", "info", "summary", "overview", "update",
        "updates", "status", "plan", "plans", "check", "score", "grade", "course",
        "week", "chapter", "module", "unit", "exam", "quiz", "midterm", "paper",
        "essay", "letter", "statement", "invoice", "receipt", "contract", "agreement",
        "output", "input", "common", "control", "frame", "latch", "clamp", "hinge",
        "gating", "gate", "runner", "split", "piece", "pieces", "render", "renders",
        "solidcast", "mesh", "meshes", "custom", "universal", "magnetic", "simple",
        "quick", "basic", "advanced", "complete", "original", "revised", "cleaned",
        "bluetooth", "volume", "button", "buttons", "switch", "cable", "wire", "power",
        "workshop", "workshops", "source", "sources", "study", "studies", "inner", "outer",
        "vector", "vectors", "manufacturing", "steel", "metal", "separation", "primary",
        "secondary", "general", "special", "rear", "upper", "lower", "middle", "center",
        "single", "double", "triple", "multi", "blank", "empty", "clean", "cleaning",
    }
)


@dataclass(frozen=True)
class DetectedProject:
    """One cohesive multi-file project cluster discovered in a folder."""

    name: str
    keywords: tuple[str, ...]
    extensions: tuple[str, ...]
    file_count: int
    distinct_count: int
    confidence: int  # 0..100
    anchor_kind: str  # "code", "bigram", "unigram"
    sample_files: tuple[str, ...]
    matched_paths: tuple[Path, ...]

    @property
    def confidence_label(self) -> str:
        if self.confidence >= 75:
            return "High"
        if self.confidence >= 50:
            return "Medium"
        return "Low"


def _split_word_edges(text: str) -> str:
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    text = re.sub(r"([A-Za-z])(\d)", r"\1 \2", text)
    text = re.sub(r"(\d)([A-Za-z])", r"\1 \2", text)
    return text


def _extract_stem_features(stem: str) -> tuple[str, list[str], list[str], dict[str, str]]:
    """Return ``(base_key, structured_codes, cleaned_tokens, original_casing_map)``."""
    base = _COPY_SUFFIX_RE.sub("", stem).strip() or stem
    # Strip dates first so date fragments like '12-14-24' are never mistaken for project codes
    no_dates = _DATE_RE.sub(" ", base)
    codes: list[str] = []
    casing: dict[str, str] = {}

    for m in _HYPHEN_CODE_RE.finditer(no_dates):
        raw_code = m.group(1)
        lo = raw_code.lower()
        codes.append(lo)
        casing[lo] = raw_code.upper()

    for m in _COURSE_CODE_RE.finditer(no_dates):
        dept, num = m.group(1), m.group(2)
        dept_lo = dept.lower()
        num_lo = num.lower()
        if dept_lo in _NON_COURSE_PREFIXES or num_lo.startswith("000"):
            continue
        # Require 3-4 digits (typical course/project codes: IME 4030, CHE 2301, CIS 2027)
        digits_only = re.sub(r"[^0-9]", "", num_lo)
        if len(digits_only) < 3:
            continue
        code_key = f"{dept_lo} {num_lo}"
        if code_key not in codes:
            codes.append(code_key)
            casing[code_key] = f"{dept.upper()} {num.upper()}"

    # Preserve compound CamelCase tokens (e.g. "DriftPad", "CyberBrick") for nice display naming
    for raw_word in re.findall(r"[A-Za-z][A-Za-z0-9]{2,}", base):
        if any(c.isupper() for c in raw_word[1:]) and any(c.islower() for c in raw_word):
            casing[raw_word.lower()] = raw_word

    # Replace underscores with spaces before regex stripping so '_plate_1' / '_v2' are caught cleanly
    cleaned = no_dates.replace("_", " ")
    cleaned = _DIM_RE.sub(" ", cleaned)
    cleaned = _VERSION_RE.sub(" ", cleaned)
    spaced = _split_word_edges(cleaned)
    raw_words = re.findall(r"[A-Za-z]{2,}", spaced)

    tokens: list[str] = []
    for w in raw_words:
        lo = w.lower()
        if lo in _STRIP_NOISE_WORDS or len(lo) < 3:
            continue
        tokens.append(lo)
        if lo not in casing:
            casing[lo] = w if (w.isupper() and len(w) <= 5) else w.capitalize()

    return base.lower(), codes, tokens, casing


def _compact_key(phrase: str) -> str:
    """Collapse spaces and trailing plural 's' for variant merging ('drift pad' == 'driftpad')."""
    comp = phrase.replace(" ", "").lower()
    if len(comp) >= 5 and comp.endswith("s") and not comp.endswith("ss"):
        comp = comp[:-1]
    return comp


def _nice_project_name(
    anchor_kind: str,
    anchor_label: str,
    casing_votes: Counter[str],
    all_casing: dict[str, str],
) -> str:
    if anchor_kind == "code":
        return all_casing.get(anchor_label, anchor_label.upper())
    # If the original files predominantly wrote it as a CamelCase compound (e.g., "DriftPad"), use that
    comp = anchor_label.replace(" ", "")
    if comp in all_casing and any(c.isupper() for c in all_casing[comp][1:]):
        return all_casing[comp]
    if casing_votes:
        top_form = casing_votes.most_common(1)[0][0]
        if top_form:
            return top_form
    parts = [all_casing.get(w, w.capitalize()) for w in anchor_label.split()]
    return " ".join(parts)


def _select_verified_keywords(
    primary_anchor: str,
    anchor_kind: str,
    member_indices: set[int],
    records: list[dict[str, Any]],
) -> tuple[str, ...]:
    """Build clean, high-precision keywords verified against ``router._keyword_matches``."""
    all_count = len(records)
    if all_count == 0 or not member_indices:
        return (primary_anchor,)

    candidates: list[str] = [primary_anchor]
    # Also consider compact form for multi-word anchors (e.g., "drift pad" -> "driftpad")
    if " " in primary_anchor:
        compact = primary_anchor.replace(" ", "")
        if len(compact) >= 5:
            candidates.append(compact)
        # Check if a single rare token from the anchor covers absorbed files cleanly
        for tok in primary_anchor.split():
            if len(tok) >= 5 and tok not in _GENERIC_UNIGRAM_BLOCKLIST:
                candidates.append(tok)
                if tok.endswith("s") and len(tok) >= 6:
                    candidates.append(tok[:-1])

    chosen: list[str] = []
    covered: set[int] = set()

    for idx_cand, kw in enumerate(candidates):
        in_hits = {i for i in member_indices if _keyword_matches(records[i]["stem"], kw)}
        if not in_hits:
            continue
        out_hits = sum(
            1 for i in range(all_count) if i not in member_indices and _keyword_matches(records[i]["stem"], kw)
        )
        precision = len(in_hits) / max(1, len(in_hits) + out_hits)
        if idx_cand == 0:
            # Always keep primary anchor if it hits anything in the cluster
            chosen.append(kw)
            covered |= in_hits
        else:
            # Secondary keywords must add newly covered cluster members AND stay high-precision (>= 80%)
            new_hits = in_hits - covered
            if new_hits and precision >= 0.80:
                chosen.append(kw)
                covered |= in_hits

    return tuple(chosen) if chosen else (primary_anchor,)


def detect_projects_in_folder(
    folder_path: Path,
    *,
    min_files: int = 3,
    exclude_subdirs: set[str] | None = None,
    max_results: int = 20,
    max_files: int = 4000,
    on_progress: ProgressCallback | None = None,
) -> list[DetectedProject]:
    """Discover cohesive multi-file projects inside ``folder_path``.

    Args:
        folder_path: Target directory on disk (e.g. ``Downloads/CAD``).
        min_files: Minimum files required for a single-type project cluster (default 3).
            Cross-extension clusters (e.g. ``.f3d`` + ``.step``) with structured codes or
            2-word anchors may still qualify at ``max(2, min_files - 1)``.
        exclude_subdirs: Direct child folder names (lowercased) to skip because they are
            already dedicated classification/project folders (e.g. ``{"logos & icons", "screenshots"}``).
        max_results: Maximum number of project proposals to return, ranked by confidence.
        max_files: Safety cap on scanned files for responsiveness.
        on_progress: Optional ``(done, total, label)`` progress callback.
    """
    root = folder_path.resolve()
    if not root.is_dir():
        return []

    min_n = max(2, int(min_files))
    skip_direct = {s.strip().lower() for s in (exclude_subdirs or set()) if s.strip()}

    if on_progress is not None:
        on_progress(0, 0, f"Scanning files in {root.name}...")

    file_paths: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        cur = Path(dirpath)
        depth = len(cur.relative_to(root).parts)
        dirnames[:] = sorted(
            [
                d
                for d in dirnames
                if not d.startswith(".")
                and d.lower() not in _SCAN_SKIP_DIRS
                and not (depth == 0 and d.lower() in skip_direct)
            ],
            key=str.lower,
        )
        for fname in sorted(filenames, key=str.lower):
            if fname.startswith("."):
                continue
            file_paths.append(cur / fname)
            if len(file_paths) >= max_files:
                break
        if len(file_paths) >= max_files:
            break

    if len(file_paths) < 2:
        return []

    records: list[dict[str, Any]] = []
    tok_df: Counter[str] = Counter()
    all_casing: dict[str, str] = {}

    total_files = len(file_paths)
    for idx, fp in enumerate(file_paths, start=1):
        if on_progress is not None and (idx == 1 or idx % 200 == 0 or idx == total_files):
            on_progress(idx, total_files, f"Analyzing {fp.name}")
        base_key, codes, toks, casing = _extract_stem_features(fp.stem)
        all_casing.update(casing)
        bigrams = [f"{toks[i]} {toks[i + 1]}" for i in range(len(toks) - 1)]
        try:
            mtime = fp.stat().st_mtime
        except OSError:
            mtime = 0.0
        records.append(
            {
                "path": fp,
                "name": fp.name,
                "stem": fp.stem,
                "ext": fp.suffix.lower(),
                "base_key": base_key,
                "codes": codes,
                "toks": toks,
                "bigrams": bigrams,
                "mtime": mtime,
            }
        )
        for t in set(toks):
            tok_df[t] += 1

    n_docs = len(records)
    # Suppress folder-wide boilerplate tokens (e.g. the user's own name if in >35% of files)
    dominant_tokens = {t for t, df in tok_df.items() if df >= max(8, int(n_docs * 0.35))}

    cand_groups: dict[tuple[str, str], set[int]] = defaultdict(set)
    for idx, r in enumerate(records):
        for c in r["codes"]:
            cand_groups[("code", c)].add(idx)
        for bg in set(r["bigrams"]):
            w1, w2 = bg.split(" ", 1)
            if w1 in dominant_tokens and w2 in dominant_tokens:
                continue
            cand_groups[("bigram", bg)].add(idx)
        for t in set(r["toks"]):
            if (
                len(t) >= 4
                and t not in _GENERIC_UNIGRAM_BLOCKLIST
                and t not in dominant_tokens
                and tok_df[t] <= max(5, int(n_docs * 0.28))
            ):
                cand_groups[("unigram", t)].add(idx)

    # Merge spacing/plural variants ('drift pad' + 'driftpad')
    by_compact: dict[str, list[tuple[tuple[str, str], set[int]]]] = defaultdict(list)
    for key, idxs in cand_groups.items():
        by_compact[_compact_key(key[1])].append((key, idxs))

    ranked_cands: list[tuple[float, str, str, set[int]]] = []
    for _comp, entries in by_compact.items():
        merged_idxs: set[int] = set()
        for _, idxs in entries:
            merged_idxs |= idxs
        entries.sort(
            key=lambda e: (
                {"code": 0, "bigram": 1, "unigram": 2}[e[0][0]],
                -len(e[1]),
                -len(e[0][1]),
            )
        )
        best_kind, best_label = entries[0][0]
        exts = {records[i]["ext"] for i in merged_idxs if records[i]["ext"]}
        distinct_bases = len({records[i]["base_key"] for i in merged_idxs})
        effective_min = max(2, min_n - 1) if (len(exts) >= 2 and best_kind in ("code", "bigram")) else min_n
        if len(merged_idxs) < effective_min or distinct_bases < 2:
            continue

        idf = math.log((n_docs + 1) / (len(merged_idxs) + 1)) + 1.0
        kind_weight = {"code": 2.3, "bigram": 1.85, "unigram": 1.0}[best_kind]
        ext_bonus = 1.0 + 0.35 * max(0, len(exts) - 1)
        # Temporal burst cohesion: bonus when >= 50% of files were modified within a 14-day window
        mtimes = sorted(records[i]["mtime"] for i in merged_idxs if records[i]["mtime"] > 0)
        burst_bonus = 1.0
        if len(mtimes) >= 2:
            span_days = (mtimes[-1] - mtimes[0]) / 86400.0
            if span_days <= 14.0:
                burst_bonus = 1.2
            elif span_days <= 45.0:
                burst_bonus = 1.1

        score = distinct_bases * idf * kind_weight * ext_bonus * burst_bonus
        ranked_cands.append((score, best_kind, best_label, merged_idxs))

    ranked_cands.sort(key=lambda item: item[0], reverse=True)

    claimed: set[int] = set()
    claimed_distinctive_words: dict[str, int] = {}
    selected: list[dict[str, Any]] = []

    for score, kind, label, idxs in ranked_cands:
        # If this is a single-word unigram whose word already anchors a stronger accepted bigram
        # (e.g. 'mace' after 'flanged mace', or 'horsemans' after 'horsemans axe'), absorb its
        # remaining files into the parent project instead of spawning a redundant duplicate project!
        if kind == "unigram":
            comp_uni = _compact_key(label)
            parent_pos = claimed_distinctive_words.get(comp_uni)
            if parent_pos is not None:
                extra = idxs - claimed
                if extra:
                    selected[parent_pos]["idxs"] |= extra
                    claimed |= extra
                continue

        rem = idxs - claimed
        exts = {records[i]["ext"] for i in rem if records[i]["ext"]}
        distinct_bases = len({records[i]["base_key"] for i in rem})
        effective_min = max(2, min_n - 1) if (len(exts) >= 2 and kind in ("code", "bigram")) else min_n
        if len(rem) < effective_min or distinct_bases < 2:
            continue

        pos = len(selected)
        claimed |= rem
        selected.append(
            {
                "score": score,
                "kind": kind,
                "label": label,
                "idxs": set(rem),
            }
        )
        # Register rare non-generic words from bigram projects for sub-cluster absorption
        if kind == "bigram":
            for tok in label.split():
                if len(tok) >= 4 and tok not in _GENERIC_UNIGRAM_BLOCKLIST and tok not in dominant_tokens:
                    ctok = _compact_key(tok)
                    if ctok not in claimed_distinctive_words:
                        claimed_distinctive_words[ctok] = pos

    if not selected:
        return []

    # Second pass: absorb any still-unclaimed files that contain a rare distinctive token from
    # an accepted bigram project (e.g., "Horseman Head.step" into "Horsemans Axe").
    for i, rec in enumerate(records):
        if i in claimed:
            continue
        matched_projects = {
            claimed_distinctive_words[_compact_key(t)]
            for t in rec["toks"]
            if _compact_key(t) in claimed_distinctive_words
        }
        if len(matched_projects) == 1:
            target_pos = next(iter(matched_projects))
            selected[target_pos]["idxs"].add(i)
            claimed.add(i)

    # Convert to DetectedProject objects with calibrated 0..100 confidence and verified keywords
    results: list[DetectedProject] = []
    for item in selected:
        idxs = item["idxs"]
        kind = item["kind"]
        label = item["label"]
        exts = tuple(sorted({records[i]["ext"] for i in idxs if records[i]["ext"]}))
        distinct_bases = len({records[i]["base_key"] for i in idxs})
        file_count = len(idxs)

        # Calibrated confidence score (45..99)
        conf = 50
        if kind == "code":
            conf += 22
        elif kind == "bigram":
            conf += 16
        else:
            conf += 6
        conf += min(18, distinct_bases * 2)
        if len(exts) >= 2:
            conf += min(12, (len(exts) - 1) * 7)
        mtimes = sorted(records[i]["mtime"] for i in idxs if records[i]["mtime"] > 0)
        if len(mtimes) >= 2 and (mtimes[-1] - mtimes[0]) <= 14.0 * 86400.0:
            conf += 6
        confidence = max(40, min(99, conf))

        verified_kws = _select_verified_keywords(label, kind, idxs, records)
        # Re-check actual matched paths using the verified keywords so preview == shift behavior
        ordered_idxs = sorted(idxs, key=lambda idx: records[idx]["name"].lower())
        casing_votes: Counter[str] = Counter()
        if " " not in label and label in all_casing:
            casing_votes[all_casing[label]] += 1
        proj_name = _nice_project_name(kind, label, casing_votes, all_casing)

        results.append(
            DetectedProject(
                name=proj_name,
                keywords=verified_kws,
                extensions=exts,
                file_count=file_count,
                distinct_count=distinct_bases,
                confidence=confidence,
                anchor_kind=kind,
                sample_files=tuple(records[i]["name"] for i in ordered_idxs[:8]),
                matched_paths=tuple(records[i]["path"] for i in ordered_idxs),
            )
        )

    results.sort(key=lambda p: (-p.confidence, -p.file_count, p.name.lower()))
    return results[: max(1, int(max_results))]
