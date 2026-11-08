"""Learning from how the user organizes files (stage 1: watch only).

When the user moves a sorted file into another folder inside Downloads, that move
is an example of where files like it belong. This module turns such moves into
counts of simple features (words in the file name, the file type, and the website
the file was downloaded from) and keeps a practice log of what it *would* have
suggested. It never moves a file and never shows a suggestion; it only learns and
writes the log, so its reliability can be judged before anything uses it.

Design notes (ideas from other projects, our own code):
- Learn only from deliberate actions (the user's own moves), never from the
  sorter's own moves, so it can't learn its own mistakes.
- Plain word counting with smoothing (as in SwiftFile and POPFile-style email
  filers), not a neural network: tiny, fast, explainable, works on any PC.
- Normalize words (lowercase, split joined words, singular form, drop numbers and
  filler words) so "Invoices_2026" and "invoice-march" share a signal.
- Only folders the user has actually used are ever predicted.
- Everything stays in the user's AppData folder. Only a website's name is kept
  (for example "yourbank.com"), never a full address.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
import logging
import math
import os
from pathlib import Path
import re
import sys
import threading
from typing import Any, Iterable
from urllib.parse import urlsplit
import uuid

import sorter

LEARNING_FILE_NAME = "learning.json"
LOG_FILE_NAME = "learning_log.jsonl"
MAX_LOG_LINES = 2000
KEEP_LOG_LINES = 1500
TOP_N = 3

# Words that say nothing about where a file belongs.
FILLER_WORDS = frozenset({
    "a", "an", "and", "the", "of", "for", "to", "in", "on", "at", "by", "with", "from",
    "copy", "final", "new", "old", "file", "files", "download", "downloads", "document",
    "doc", "img", "image", "scan", "untitled", "version", "ver", "rev", "draft", "edit",
    "jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
    "january", "february", "march", "april", "june", "july", "august", "september",
    "october", "november", "december",
})

_LOCK = threading.RLock()


# ---------------------------------------------------------------------------
# settings and storage
# ---------------------------------------------------------------------------

def is_enabled(config: dict | None) -> bool:
    block = (config or {}).get("learning")
    return isinstance(block, dict) and bool(block.get("enabled", False))


def _learning_file() -> Path:
    return Path(sorter.LOG_DIR) / LEARNING_FILE_NAME


def _log_file() -> Path:
    return Path(sorter.LOG_DIR) / LOG_FILE_NAME


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json_atomic(target: Path, data: Any) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f"{target.name}.{uuid.uuid4().hex}.tmp")
    temp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(temp, target)


# ---------------------------------------------------------------------------
# features
# ---------------------------------------------------------------------------

def _singular(word: str) -> str:
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def name_words(file_name: str) -> list[str]:
    """Meaningful words in a file name, normalized: "Bank_StatementsOct2026.pdf" -> ["bank", "statement"]."""
    stem = Path(file_name).stem if "." in file_name else file_name
    stem = re.sub(r"([a-z])([A-Z])", r"\1 \2", stem)          # GoogleChrome -> Google Chrome
    stem = re.sub(r"([A-Za-z])(\d)|(\d)([A-Za-z])", r"\1\3 \2\4", stem)  # Oct2026 -> Oct 2026
    words: list[str] = []
    seen: set[str] = set()
    for raw in re.split(r"[^A-Za-z0-9]+", stem.lower()):
        if len(raw) < 2 or raw.isdigit() or raw in FILLER_WORDS:
            continue
        word = _singular(raw)
        if word in FILLER_WORDS or word in seen:
            continue
        seen.add(word)
        words.append(word)
    return words


def parse_zone_identifier(text: str) -> str | None:
    """The website name from a Windows "downloaded from" note, or None.

    Browsers write ReferrerUrl (the page the download started on) and HostUrl (the
    file's own address, often a download server). The referring site says more
    about what the file is, so it wins when present.
    """
    values: dict[str, str] = {}
    for line in (text or "").splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            values[key.strip().lower()] = value.strip()
    for key in ("referrerurl", "hosturl"):
        host = _site_name(values.get(key, ""))
        if host:
            return host
    return None


def _site_name(url: str) -> str | None:
    if not url or "://" not in url:
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme not in ("http", "https"):
        return None
    host = (parts.hostname or "").lower().strip(".")
    if not host or "." not in host:
        return None
    if host.startswith("www."):
        host = host[4:]
    return host


def _read_zone_identifier(path: Path) -> str:
    """The raw "downloaded from" note Windows keeps beside a downloaded file ('' if none)."""
    if sys.platform != "win32":
        return ""
    try:
        with open(f"{path}:Zone.Identifier", encoding="utf-8", errors="ignore") as handle:
            return handle.read(4096)
    except OSError:
        return ""


def source_site(path: Path) -> str | None:
    """The website a file was downloaded from (site name only), if Windows recorded it."""
    try:
        return parse_zone_identifier(_read_zone_identifier(Path(path)))
    except Exception:  # never let a quirky file break sorting
        return None


def features(path: Path) -> list[str]:
    path = Path(path)
    feats = [f"w:{w}" for w in name_words(path.name)]
    if path.suffix:
        feats.append(f"x:{path.suffix.lower()}")
    site = source_site(path)
    if site:
        feats.append(f"s:{site}")
    return feats


# ---------------------------------------------------------------------------
# the learner
# ---------------------------------------------------------------------------

def folder_key(label: str) -> str:
    """One key per folder however it's written: "Banking", "banking/", "Banking\\" -> "banking"."""
    return label.replace("/", "\\").strip("\\").lower()


class Model:
    """Counts of features per folder, with a smoothed word-count (naive Bayes) guess."""

    def __init__(self, data: dict | None = None) -> None:
        data = data if isinstance(data, dict) else {}
        folders = data.get("folders")
        self.folders: dict[str, dict] = folders if isinstance(folders, dict) else {}

    def to_dict(self) -> dict:
        return {"version": 1, "folders": self.folders}

    @property
    def example_count(self) -> int:
        return sum(int(f.get("examples", 0)) for f in self.folders.values())

    def learn(self, feats: Iterable[str], folder_label: str) -> None:
        key = folder_key(folder_label)
        entry = self.folders.setdefault(key, {"label": folder_label, "examples": 0, "features": {}})
        entry["label"] = folder_label
        entry["examples"] = int(entry.get("examples", 0)) + 1
        counts = entry.setdefault("features", {})
        for feat in set(feats):
            counts[feat] = int(counts.get(feat, 0)) + 1

    def predict(self, feats: Iterable[str], top: int = TOP_N) -> list[dict]:
        """Best folders for these features, most likely first (empty until it has examples)."""
        feats = list(dict.fromkeys(feats))
        if not self.folders or not feats:
            return []
        total = self.example_count or 1
        scored: list[tuple[float, str, dict, int]] = []
        for key, entry in self.folders.items():
            examples = max(int(entry.get("examples", 0)), 0)
            if examples == 0:
                continue
            counts = entry.get("features", {})
            # log P(folder) + sum log P(feature | folder), add-one smoothing on presence
            score = math.log(examples / total)
            shared = 0
            for feat in feats:
                seen = int(counts.get(feat, 0))
                if seen:
                    shared += 1
                score += math.log((seen + 1) / (examples + 2))
            scored.append((score, key, entry, shared))
        if not scored:
            return []
        best = max(s for s, *_ in scored)
        weights = [(math.exp(s - best), key, entry, shared) for s, key, entry, shared in scored]
        norm = sum(w for w, *_ in weights) or 1.0
        weights.sort(key=lambda item: item[0], reverse=True)
        return [
            {
                "folder": entry.get("label", key),
                "confidence": round(w / norm, 3),
                "examples": int(entry.get("examples", 0)),
                "shared_features": shared,
            }
            for w, key, entry, shared in weights[:top]
        ]


def load_model() -> Model:
    try:
        data = json.loads(_learning_file().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    return Model(data)


def save_model(model: Model) -> None:
    try:
        _write_json_atomic(_learning_file(), model.to_dict())
    except OSError as exc:
        logging.debug("Could not save learning data: %s", exc)


def _append_log(record: dict) -> None:
    target = _log_file()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")
        _trim_log(target)
    except OSError as exc:
        logging.debug("Could not write learning log: %s", exc)


def _trim_log(target: Path) -> None:
    try:
        lines = target.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    if len(lines) <= MAX_LOG_LINES:
        return
    kept = "\n".join(lines[-KEEP_LOG_LINES:]) + "\n"
    temp = target.with_name(f"{target.name}.{uuid.uuid4().hex}.tmp")
    temp.write_text(kept, encoding="utf-8")
    os.replace(temp, target)


def read_log() -> list[dict]:
    records: list[dict] = []
    try:
        for line in _log_file().read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict):
                records.append(rec)
    except OSError:
        pass
    return records


# ---------------------------------------------------------------------------
# telling the user's moves from the program's
# ---------------------------------------------------------------------------

def _norm(path: Path | str) -> str:
    try:
        resolved = Path(path).resolve()
    except OSError:
        resolved = Path(path)
    return os.path.normcase(os.path.normpath(str(resolved)))


def _recent_program_moves(max_bytes: int = 512 * 1024) -> set[tuple[str, str]]:
    """(src, dest) pairs the program recorded recently.

    Every program move (sorting, Undo, clean-up, hold release) writes a "planned"
    entry to the history file before the file moves, and a "done" entry after, so
    a move the watcher sees that isn't listed here was made by someone else.
    """
    history = Path(sorter.HISTORY_FILE)
    pairs: set[tuple[str, str]] = set()
    try:
        size = history.stat().st_size
        with history.open("rb") as handle:
            if size > max_bytes:
                handle.seek(size - max_bytes)
                handle.readline()  # skip a partial line
            chunk = handle.read().decode("utf-8", errors="ignore")
    except OSError:
        return pairs
    for line in chunk.splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict) and rec.get("src") and rec.get("dest"):
            pairs.add((_norm(rec["src"]), _norm(rec["dest"])))
    return pairs


def is_user_move(src: Path | str, dest: Path | str, root: Path | str, config: dict | None = None) -> bool:
    """True when a file moved between two folders inside ``root`` and the program didn't do it."""
    src, dest, root = Path(src), Path(dest), Path(root)
    try:
        if not dest.is_file():
            return False
        if not (sorter.is_under(dest, root.resolve()) and sorter.is_under(src.parent, root.resolve())):
            return False
    except OSError:
        return False
    if _norm(src.parent) == _norm(dest.parent):
        return False  # a rename in place, e.g. a browser finishing "file.pdf.crdownload"
    if sorter.is_internal_path(dest, root) or sorter.is_internal_path(src, root):
        return False
    if config is not None and sorter.is_incomplete(dest, config):
        return False
    return (_norm(src), _norm(dest)) not in _recent_program_moves()


# ---------------------------------------------------------------------------
# what the watcher calls
# ---------------------------------------------------------------------------

def _folder_label(folder: Path, root: Path) -> str | None:
    try:
        rel = Path(folder).resolve().relative_to(Path(root).resolve())
    except (ValueError, OSError):
        return None
    if not rel.parts:
        return None  # the top of Downloads itself isn't a destination
    return "\\".join(rel.parts)


def observe_user_move(dest: Path, root: Path, config: dict) -> dict | None:
    """Learn from a move the user made. Logs the guess it would have made first."""
    if not is_enabled(config):
        return None
    label = _folder_label(Path(dest).parent, root)
    if not label:
        return None
    feats = features(Path(dest))
    with _LOCK:
        model = load_model()
        guesses = model.predict(feats)
        had_knowledge = bool(model.folders)
        names = [folder_key(g["folder"]) for g in guesses]
        record = {
            "time": _now(),
            "kind": "your_move",
            "file": Path(dest).name,
            "moved_to": label,
            "guesses": guesses,
            "counted": had_knowledge,
            "first_guess_right": bool(names) and names[0] == folder_key(label),
            "in_top_three": folder_key(label) in names,
        }
        model.learn(feats, label)
        save_model(model)
        _append_log(record)
    return record


def observe_sorted(dest: Path, root: Path, config: dict) -> dict | None:
    """Note what it would have suggested for a file the rules just sorted (no learning)."""
    if not is_enabled(config):
        return None
    with _LOCK:
        model = load_model()
        if not model.folders:
            return None
        guesses = model.predict(features(Path(dest)))
        record = {
            "time": _now(),
            "kind": "sorted",
            "file": Path(dest).name,
            "rules_put_it_in": _folder_label(Path(dest).parent, root) or "",
            "would_suggest": guesses,
        }
        _append_log(record)
    return record


def report() -> dict:
    """Numbers for "See what it has learned"."""
    model = load_model()
    log = read_log()
    judged = [r for r in log if r.get("kind") == "your_move" and r.get("counted")]
    first = sum(1 for r in judged if r.get("first_guess_right"))
    top3 = sum(1 for r in judged if r.get("in_top_three"))
    folders = sorted(
        ({"folder": f.get("label", k), "examples": int(f.get("examples", 0))} for k, f in model.folders.items()),
        key=lambda item: item["examples"],
        reverse=True,
    )
    sites: Counter = Counter()
    for entry in model.folders.values():
        for feat, count in entry.get("features", {}).items():
            if feat.startswith("s:"):
                sites[feat[2:]] += int(count)
    return {
        "your_moves": sum(1 for r in log if r.get("kind") == "your_move"),
        "judged": len(judged),
        "first_guess_right": first,
        "in_top_three": top3,
        "first_guess_rate": round(first / len(judged), 3) if judged else None,
        "top_three_rate": round(top3 / len(judged), 3) if judged else None,
        "folders": folders,
        "sites": [site for site, _ in sites.most_common(10)],
        "recent": log[-10:],
    }


def forget() -> None:
    """Delete everything it has learned and its practice log."""
    with _LOCK:
        for target in (_learning_file(), _log_file()):
            try:
                target.unlink(missing_ok=True)
            except OSError as exc:
                logging.debug("Could not delete %s: %s", target, exc)
