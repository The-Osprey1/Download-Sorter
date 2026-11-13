"""Learning from how the user organizes files.

When the user moves a sorted file into another folder inside Downloads, that move
is an example of where files like it belong. This module turns such moves into
counts of simple features (words in the file name, the file type, and the website
the file was downloaded from) and keeps a practice log of what it *would* have
guessed, so its reliability can be judged ("right 8 of 10 times").

It never moves a file by itself. After the user has moved 3 files that share a
word or a website into the same folder, it can *suggest* a rule ("Put files like
this in Banking from now on?"); only a Yes adds a rule, which then shows on the
Rules page like any other. The folder scan at setup (folder_scan.py) also feeds
it the files already in the user's folders, so it knows them from day one.

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

# Suggest a rule once the user has moved this many files that share a word or a
# website into one folder, and that word or website points to that folder at
# least this often (so "scan" or "copy", found everywhere, never becomes a rule).
SUGGEST_AFTER = 3
SUGGEST_SHARE = 0.8
PENDING_HOURS = 24        # don't ask about the same thing again for a day
MAX_PENDING = 20
# Taking back this many files a learned rule filed is enough to ask whether to switch it off.
MISS_LIMIT = 2
MAX_CORRECTED = 300       # ids of the moves already counted as taken back

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


def suggestions_enabled(config: dict | None) -> bool:
    block = (config or {}).get("learning")
    return is_enabled(config) and isinstance(block, dict) and bool(block.get("suggest", True))


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
    if len(word) > 4 and word.endswith(("xes", "ches", "shes", "sses", "zes")):
        return word[:-2]  # taxes -> tax, matches -> match, classes -> class
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
        # Everything else kept alongside: the user's own moves per folder,
        # suggestions waiting for an answer, declined ones, seeded file names.
        self.extra: dict[str, Any] = {k: v for k, v in data.items() if k not in ("version", "folders")}

    def to_dict(self) -> dict:
        return {"version": 1, "folders": self.folders, **self.extra}

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

    def unlearn(self, feats: Iterable[str], folder_label: str) -> None:
        """Take one example back: the user said a file like this does not belong there."""
        entry = self.folders.get(folder_key(folder_label))
        if not entry:
            return
        entry["examples"] = max(int(entry.get("examples", 0)) - 1, 0)
        counts = entry.setdefault("features", {})
        for feat in set(feats):
            left = int(counts.get(feat, 0)) - 1
            if left > 0:
                counts[feat] = left
            else:
                counts.pop(feat, None)

    def misses(self, key: str) -> int:
        return int((self.extra.get("misses") or {}).get(key, 0))

    def add_miss(self, key: str) -> int:
        misses = self.extra.setdefault("misses", {})
        misses[key] = int(misses.get(key, 0)) + 1
        return misses[key]

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


_MODEL_CACHE: tuple[tuple, "Model"] | None = None


def _cached_model() -> "Model":
    """The learned model, re-read only when learning.json changes on disk.

    For read-only use (predicting what it would suggest): one sorted file must not
    re-parse the whole model. Callers that change the model use load_model().
    """
    global _MODEL_CACHE
    target = _learning_file()
    try:
        st = target.stat()
    except OSError:
        return load_model()
    signature = (str(target), st.st_mtime_ns, st.st_size)
    if _MODEL_CACHE is not None and _MODEL_CACHE[0] == signature:
        return _MODEL_CACHE[1]
    model = load_model()
    _MODEL_CACHE = (signature, model)
    return model


def save_model(model: Model) -> None:
    try:
        _write_json_atomic(_learning_file(), model.to_dict())
    except OSError as exc:
        logging.debug("Could not save learning data: %s", exc)


_LOG_COUNT: tuple[str, int] | None = None  # (log path, lines as this process last counted them)


def _append_log(record: dict) -> None:
    global _LOG_COUNT
    target = _log_file()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")
        # Count lines in memory: the file is only read when it really has to be trimmed,
        # not on every sorted file.
        if _LOG_COUNT is None or _LOG_COUNT[0] != str(target):
            _LOG_COUNT = (str(target), _count_lines(target))
        else:
            _LOG_COUNT = (str(target), _LOG_COUNT[1] + 1)
        if _LOG_COUNT[1] > MAX_LOG_LINES:
            _LOG_COUNT = (str(target), _trim_log(target))
    except OSError as exc:
        logging.debug("Could not write learning log: %s", exc)


def _count_lines(target: Path) -> int:
    try:
        with target.open("r", encoding="utf-8", errors="ignore") as handle:
            return sum(1 for _ in handle)
    except OSError:
        return 0


def _trim_log(target: Path) -> int:
    """Keep the newest KEEP_LOG_LINES lines. Returns how many lines the log now has."""
    try:
        lines = target.read_text(encoding="utf-8").splitlines()
        if len(lines) <= MAX_LOG_LINES:
            return len(lines)
        kept_lines = lines[-KEEP_LOG_LINES:]
        temp = target.with_name(f"{target.name}.{uuid.uuid4().hex}.tmp")
        temp.write_text("\n".join(kept_lines) + "\n", encoding="utf-8")
        os.replace(temp, target)
        return len(kept_lines)
    except OSError:
        return _count_lines(target)


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
    """Learn from a move the user made. Logs the guess it would have made first.

    The returned record carries a "suggestion" when this move makes a rule worth
    offering (see SUGGEST_AFTER); the caller shows it and the user decides.
    """
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
        _count_move(model, feats, label)
        if suggestions_enabled(config):
            suggestion = _suggestion_for(model, feats, label, Path(dest), config)
            if suggestion:
                record["suggestion"] = suggestion
        save_model(model)
        _append_log({k: v for k, v in record.items() if k != "suggestion"})
    return record


# ---------------------------------------------------------------------------
# seeding from the folder scan, and suggesting rules
# ---------------------------------------------------------------------------

def _name_features(file_name: str) -> list[str]:
    feats = [f"w:{w}" for w in name_words(file_name)]
    suffix = Path(file_name).suffix.lower()
    if suffix:
        feats.append(f"x:{suffix}")
    return feats


def seed_examples(examples: Iterable[tuple[str, str]], max_remembered: int = 5000) -> int:
    """Learn (file name, folder) pairs found by the setup scan. Each file is only counted once."""
    with _LOCK:
        model = load_model()
        seeded = list(model.extra.get("seeded") or [])
        known = set(seeded)
        added = 0
        for name, folder in examples:
            key = f"{folder_key(folder)}/{str(name).lower()}"
            if key in known:
                continue
            feats = _name_features(str(name))
            if not feats:
                continue
            model.learn(feats, folder)
            known.add(key)
            seeded.append(key)
            added += 1
        model.extra["seeded"] = seeded[-max_remembered:]
        if added:
            save_model(model)
    return added


def _count_move(model: Model, feats: Iterable[str], label: str) -> None:
    """Count the user's own moves separately: only those can lead to a suggestion."""
    moves = model.extra.setdefault("moves", {})
    entry = moves.setdefault(folder_key(label), {"label": label, "count": 0, "features": {}})
    entry["label"] = label
    entry["count"] = int(entry.get("count", 0)) + 1
    counts = entry.setdefault("features", {})
    for feat in set(feats):
        counts[feat] = int(counts.get(feat, 0)) + 1


def _asked_recently(model: Model, key: str) -> bool:
    now = datetime.now(timezone.utc)
    for item in (model.extra.get("pending") or {}).values():
        if key in item.get("keys", []):
            try:
                asked = datetime.fromisoformat(str(item.get("time")))
            except ValueError:
                return True
            if (now - asked).total_seconds() < PENDING_HOURS * 3600:
                return True
    return False


def _already_routed_there(dest: Path, label: str, config: dict) -> bool:
    try:
        routing = sorter.evaluate_routing(dest.name, config, file_path=dest)
    except Exception:
        return False
    return folder_key(str(routing.get("destination") or "")) == folder_key(label)


def _suggestion_for(model: Model, feats: list[str], label: str, dest: Path, config: dict) -> dict | None:
    key = folder_key(label)
    moved = (model.extra.get("moves") or {}).get(key) or {}
    if int(moved.get("count", 0)) < SUGGEST_AFTER:
        return None
    declined = set(model.extra.get("declined") or [])
    here = (model.folders.get(key) or {}).get("features", {})
    best: dict[str, tuple[int, str]] = {}
    for feat in dict.fromkeys(feats):
        kind = feat[:2]
        if kind not in ("w:", "s:"):
            continue
        n = int((moved.get("features") or {}).get(feat, 0))
        if n < SUGGEST_AFTER or f"{feat}|{key}" in declined or _asked_recently(model, f"{feat}|{key}"):
            continue
        total = sum(int(f.get("features", {}).get(feat, 0)) for f in model.folders.values())
        if not total or int(here.get(feat, 0)) / total < SUGGEST_SHARE:
            continue
        if kind not in best or n > best[kind][0]:
            best[kind] = (n, feat[2:])
    if not best:
        return None
    if _already_routed_there(dest, label, config):
        return None  # a rule already sends files like this there

    word = best["w:"][1] if "w:" in best else None
    site = best["s:"][1] if "s:" in best else None
    extension = None
    suffix = dest.suffix.lower()
    if word and not site and suffix and int((moved.get("features") or {}).get(f"x:{suffix}", 0)) >= SUGGEST_AFTER:
        extension = suffix  # e.g. only PDFs, as every file moved there was one
    keys = ([f"w:{word}|{key}"] if word else []) + ([f"s:{site}|{key}"] if site else [])
    suggestion = {
        "id": uuid.uuid4().hex[:10],
        "time": _now(),
        "folder": label,
        "word": word,
        "site": site,
        "extension": extension,
        "example": dest.name,
        "count": max(n for n, _ in best.values()),
        "keys": keys,
    }
    pending = model.extra.setdefault("pending", {})
    pending[suggestion["id"]] = suggestion
    for old in sorted(pending, key=lambda i: str(pending[i].get("time", "")))[:-MAX_PENDING]:
        pending.pop(old, None)
    return suggestion


# ---------------------------------------------------------------------------
# corrections: the user took back a file that was filed for them
# ---------------------------------------------------------------------------

def miss_key(feat: str, folder_label: str) -> str:
    return f"{feat}|{folder_key(folder_label)}"


def rule_miss_key(rule_name: str) -> str:
    return f"rule:{rule_name}"


def blocked_features(model: Model, feats: Iterable[str], folder_label: str) -> list[str]:
    """The words and websites of ``feats`` the user has already said "not there" to for this folder."""
    return [f for f in dict.fromkeys(feats)
            if f[:2] in ("w:", "s:") and model.misses(miss_key(f, folder_label))]


def _learned_rule(config: dict, name: str) -> dict | None:
    for rule in config.get("keyword_rules", []) or []:
        if isinstance(rule, dict) and rule.get("name") == name and rule.get("origin") == "learned":
            return rule
    return None


def observe_correction(record: dict, config: dict | None = None, root: Path | str | None = None) -> dict | None:
    """The user put back, or moved elsewhere, a file the program had filed.

    ``record`` is that move's history record. Only two kinds of move teach anything here:
    one the learner made on its own guess (``why == "learned"``), and one a learned rule
    made. A file filed by a rule the user wrote, or by its file type, says nothing about
    the learner. Returns what was noted, with a "suggestion" when a learned rule has now
    been wrong often enough to ask whether to switch it off; None when nothing was noted.
    """
    if config is None:
        try:
            import config_manager
            config = config_manager.load_sorter_config()
        except Exception:
            config = {}
    if not is_enabled(config):
        return None
    why = str(record.get("why") or "")
    rule_name = str(record.get("rule") or "")
    dest = Path(str(record.get("dest") or ""))
    record_id = str(record.get("id") or "")
    if why not in ("learned", "keyword") or not dest.name:
        return None
    if why == "keyword" and not _learned_rule(config, rule_name):
        return None
    try:
        base = Path(root) if root is not None else sorter.watch_root(config)
    except Exception:
        return None
    label = _folder_label(dest.parent, base)
    if not label:
        return None

    with _LOCK:
        model = load_model()
        corrected = list(model.extra.get("corrected") or [])
        if record_id and record_id in corrected:
            return None  # this move was already counted
        if record_id:
            corrected.append(record_id)
            model.extra["corrected"] = corrected[-MAX_CORRECTED:]
        note: dict[str, Any] = {"time": _now(), "kind": "taken_back", "file": dest.name,
                                "was_in": label, "why": why}
        if why == "learned":
            feats = _name_features(dest.name)
            if record.get("site"):
                feats.append(f"s:{record['site']}")
            model.unlearn(feats, label)
            for feat in feats:
                if feat[:2] in ("w:", "s:"):
                    model.add_miss(miss_key(feat, label))
        else:
            note["rule"] = rule_name
            key = rule_miss_key(rule_name)
            count = model.add_miss(key)
            declined = set(model.extra.get("declined") or [])
            if (count >= MISS_LIMIT and suggestions_enabled(config) and key not in declined
                    and not _asked_recently(model, key)):
                suggestion = {
                    "id": uuid.uuid4().hex[:10],
                    "time": _now(),
                    "kind": "disable_rule",
                    "rule": rule_name,
                    "folder": label,
                    "example": dest.name,
                    "count": count,
                    "keys": [key],
                }
                model.extra.setdefault("pending", {})[suggestion["id"]] = suggestion
                note["suggestion"] = suggestion
        save_model(model)
        _append_log({k: v for k, v in note.items() if k != "suggestion"})
    return note


def describe_suggestion(suggestion: dict) -> tuple[str, str]:
    """("Put files like this in Banking?", "“statement” files from yourbank.com").

    Short on purpose: a ribbon line holds about 48 characters.
    """
    if suggestion.get("kind") == "disable_rule":
        return (f"Stop filing into {suggestion.get('folder')}?",
                f"You took back {suggestion.get('count')} files this learned rule filed")
    title = f"Put files like this in {suggestion.get('folder')}?"
    word, site, ext = suggestion.get("word"), suggestion.get("site"), suggestion.get("extension")
    if word and site:
        message = f"“{word}” files from {site}"
    elif site:
        message = f"Files downloaded from {site}"
    else:
        message = f"Files with “{word}” in the name"
        if ext:
            message = f"{ext.lstrip('.').upper()} files with “{word}” in the name"
    return title, message


def pending_suggestion(suggestion_id: str) -> dict | None:
    with _LOCK:
        return dict((load_model().extra.get("pending") or {}).get(suggestion_id) or {}) or None


def suggestion_rule(suggestion: dict) -> dict[str, Any]:
    """The keyword rule a suggestion adds."""
    rule: dict[str, Any] = {
        "name": f"{suggestion['folder']} (learned)",
        "folder": suggestion["folder"],
        "match": [suggestion["word"]] if suggestion.get("word") else [],
        "extensions": [suggestion["extension"]] if suggestion.get("extension") else [],
        "enabled": True,
        "origin": "learned",
    }
    if suggestion.get("site"):
        rule["sites"] = [suggestion["site"]]
    return rule


def accept_suggestion(suggestion_id: str, config_path: Path | str | None = None) -> dict | None:
    """Yes: add the suggested rule (first in the list, so it wins) and save the settings."""
    import config_manager

    with _LOCK:
        model = load_model()
        suggestion = (model.extra.get("pending") or {}).pop(suggestion_id, None)
        if not suggestion:
            return None
        save_model(model)
    if suggestion.get("kind") == "disable_rule":
        return _switch_rule_off(suggestion, config_path)
    rule = suggestion_rule(suggestion)
    config = config_manager.load_sorter_config(config_path)
    rules = list(config.get("keyword_rules", []))
    for existing in rules:
        same_target = (existing.get("origin") == "learned" and existing.get("folder") == rule["folder"]
                       and existing.get("sites", []) == rule.get("sites", [])
                       and existing.get("extensions", []) == rule["extensions"]
                       and bool(existing.get("match")) == bool(rule["match"]))
        if same_target:
            existing["match"] = list(dict.fromkeys(list(existing.get("match", [])) + rule["match"]))
            existing["enabled"] = True
            rule = existing
            break
    else:
        rules.insert(0, rule)
    config["keyword_rules"] = rules
    config_manager.save_sorter_config(config, config_path)
    _append_log({"time": _now(), "kind": "rule_added", "folder": rule["folder"],
                 "match": rule.get("match", []), "sites": rule.get("sites", [])})
    return rule


def _switch_rule_off(suggestion: dict, config_path: Path | str | None = None) -> dict | None:
    """Yes to "Stop filing into Banking?": the learned rule stays on the Rules page, switched off."""
    import config_manager

    config = config_manager.load_sorter_config(config_path)
    rule = _learned_rule(config, str(suggestion.get("rule") or ""))
    if rule is None:
        return None
    rule["enabled"] = False
    config_manager.save_sorter_config(config, config_path)
    _append_log({"time": _now(), "kind": "rule_switched_off", "folder": rule.get("folder"),
                 "rule": rule.get("name")})
    return {**rule, "switched_off": True}


def decline_suggestion(suggestion_id: str) -> bool:
    """No: never suggest this word or website for this folder again."""
    with _LOCK:
        model = load_model()
        suggestion = (model.extra.get("pending") or {}).pop(suggestion_id, None)
        if not suggestion:
            return False
        declined = list(model.extra.get("declined") or [])
        declined.extend(k for k in suggestion.get("keys", []) if k not in declined)
        model.extra["declined"] = declined
        save_model(model)
        _append_log({"time": _now(), "kind": "rule_declined", "folder": suggestion.get("folder"),
                     "keys": suggestion.get("keys", [])})
    return True


def observe_sorted(dest: Path, root: Path, config: dict) -> dict | None:
    """Note what it would have suggested for a file the rules just sorted (no learning)."""
    if not is_enabled(config):
        return None
    with _LOCK:
        model = _cached_model()
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
        "rules_added": sum(1 for r in log if r.get("kind") == "rule_added"),
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


def fallback_suggestion(dest: Path, root: Path, config: dict) -> dict | None:
    """A file the rules could not place (it landed in the default folder): ask whether
    a folder the learner recognises should have taken it. Returns the suggestion to show,
    or None. Reuses the same rules as the move-based suggestions, so the same
    "Yes / No" answers and the same declining apply."""
    if not suggestions_enabled(config):
        return None
    fallback = folder_key(str(config.get("default_folder") or "Other"))
    try:
        parent_rel = Path(dest).parent.relative_to(Path(root))
    except ValueError:
        return None
    if folder_key(str(parent_rel)) != fallback:
        return None
    with _LOCK:
        model = load_model()
        if not model.folders:
            return None
        feats = features(Path(dest))
        for guess in model.predict(feats, top=3):
            label = str(guess.get("folder") or "")
            if not label or folder_key(label) == fallback:
                continue
            suggestion = _suggestion_for(model, feats, label, Path(dest), config)
            if suggestion:
                save_model(model)
                return suggestion
    return None
