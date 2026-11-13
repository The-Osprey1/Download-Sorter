"""A list of every file under the sorted folder, to search by more than its name.

One small SQLite file in the app's data folder holds a row per file: where it is, when it
was filed and why, the website it came from, and for print files the printer, filament and
model. `search("chase october")` or `search("pla from:makerworld")` answers from it in a few
milliseconds, which is what the recent-files shelf and Home's filter box ask.

Rules it keeps:
- Local only. Nothing is read from inside a file here, and nothing leaves the PC.
- It is a copy of what is on disk, never the truth: a result whose file is gone is dropped
  when it is seen, and `rebuild()` walks the folder again (at start-up and once a day).
- A project folder (a Git repository) is one entry, and its files are not listed. Folders
  such as node_modules and .venv are skipped.
- The watcher and the windows may write at the same time; SQLite's write-ahead log and a
  short wait make that safe. A failed write is only logged: the next walk repairs it.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import sys
import time
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import sorter

DB_NAME = "file_index.db"
SCHEMA_VERSION = 1
MAX_FILES = 200_000          # a walk stops here; a Downloads folder this big is not one we can list
BUSY_TIMEOUT_MS = 2000
RECENCY_WEIGHT = 2.0         # how much "filed lately" counts against "matches better"
THREE_D_SUFFIXES = (".3mf", ".stl", ".step", ".stp", ".f3d", ".f3z", ".obj")
# Folders never walked into. A folder holding one of the first group is a project: one entry.
PROJECT_MARKERS = (".git", ".svn", ".hg")
SKIP_DIR_NAMES = frozenset({
    ".git", ".svn", ".hg", ".venv", "venv", "node_modules", ".cargo", ".rustup",
    ".obsidian", "__pycache__", ".idea", ".vscode",
})
_FILTER = re.compile(r"(?:^|\s)(in|from|type):(\"[^\"]*\"|\S+)", re.IGNORECASE)
_SPLIT = re.compile(r"[^0-9a-zA-ZÀ-￿]+")
_CAMEL = re.compile(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Za-z])(?=\d)|(?<=\d)(?=[A-Za-z])")


def is_enabled(config: dict | None) -> bool:
    block = (config or {}).get("search")
    return not isinstance(block, dict) or bool(block.get("enabled", True))


def db_file() -> Path:
    return Path(sorter.LOG_DIR) / DB_NAME


def path_key(path: Path | str) -> str:
    """One key per place on disk: Windows ignores case and accepts both slashes."""
    return os.path.normcase(os.path.normpath(str(path)))


# ---------------------------------------------------------------------------
# the database
# ---------------------------------------------------------------------------

_HAS_FTS: bool | None = None

_TABLE = """
CREATE TABLE IF NOT EXISTS files (
    key      TEXT PRIMARY KEY,
    path     TEXT NOT NULL,
    name     TEXT NOT NULL,
    folder   TEXT NOT NULL DEFAULT '',
    ext      TEXT NOT NULL DEFAULT '',
    size     INTEGER NOT NULL DEFAULT 0,
    mtime    REAL NOT NULL DEFAULT 0,
    filed_at REAL,
    site     TEXT,
    why      TEXT,
    rule     TEXT,
    extra    TEXT,
    is_dir   INTEGER NOT NULL DEFAULT 0,
    words    TEXT NOT NULL DEFAULT '',
    seen     INTEGER NOT NULL DEFAULT 0
)
"""
_FTS = [
    "CREATE VIRTUAL TABLE IF NOT EXISTS files_fts USING fts5("
    "words, content='files', content_rowid='rowid', tokenize='unicode61 remove_diacritics 2', prefix='1 2 3')",
    "CREATE TRIGGER IF NOT EXISTS files_ai AFTER INSERT ON files BEGIN "
    "INSERT INTO files_fts(rowid, words) VALUES (new.rowid, new.words); END",
    "CREATE TRIGGER IF NOT EXISTS files_ad AFTER DELETE ON files BEGIN "
    "INSERT INTO files_fts(files_fts, rowid, words) VALUES ('delete', old.rowid, old.words); END",
    "CREATE TRIGGER IF NOT EXISTS files_au AFTER UPDATE ON files BEGIN "
    "INSERT INTO files_fts(files_fts, rowid, words) VALUES ('delete', old.rowid, old.words); "
    "INSERT INTO files_fts(rowid, words) VALUES (new.rowid, new.words); END",
]


def fts_available(conn: sqlite3.Connection) -> bool:
    """Whether this Python's SQLite can do word search (FTS5). Without it, search still
    works by plain text matching, only slower and without ranking."""
    global _HAS_FTS
    if _HAS_FTS is None:
        try:
            conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS temp.fts_probe USING fts5(x)")
            conn.execute("DROP TABLE IF EXISTS temp.fts_probe")
            _HAS_FTS = True
        except sqlite3.Error:
            _HAS_FTS = False
    return _HAS_FTS


def connect(target: Path | str | None = None) -> sqlite3.Connection:
    """Open the index (making it if needed). Close it when done: `with closing(connect()) as c`."""
    target = Path(target) if target else db_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target), timeout=BUSY_TIMEOUT_MS / 1000)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version != SCHEMA_VERSION:
        # It is only a copy of the disk: a different layout is thrown away and rebuilt.
        with conn:
            for name in ("files_ai", "files_ad", "files_au"):
                conn.execute(f"DROP TRIGGER IF EXISTS {name}")
            conn.execute("DROP TABLE IF EXISTS files_fts")
            conn.execute("DROP TABLE IF EXISTS files")
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    with conn:
        conn.execute(_TABLE)
        conn.execute("CREATE INDEX IF NOT EXISTS files_when ON files (COALESCE(filed_at, mtime))")
        if fts_available(conn):
            for statement in _FTS:
                conn.execute(statement)
    return conn


# ---------------------------------------------------------------------------
# words
# ---------------------------------------------------------------------------

def _singular(word: str) -> str:
    try:
        import learning
        return learning._singular(word)
    except Exception:
        return word


def split_words(text: str) -> list[str]:
    """"Bank_StatementsOct2026.pdf" -> bank, statementsoct2026, statements, statement, oct, 2026, pdf."""
    out: list[str] = []
    for chunk in _SPLIT.split(str(text)):
        # The whole run first ("x1c", "oct2026"), then its parts ("oct", "2026").
        for part in dict.fromkeys([chunk, *_CAMEL.split(chunk)]):
            low = part.lower()
            if not low:
                continue
            out.append(low)
            single = _singular(low)
            if single != low:
                out.append(single)
    return out


def search_text(name: str, folder: str = "", site: str | None = None, extra: str | None = None) -> str:
    """Everything a file can be found by, as one line of words."""
    words = split_words(name) + split_words(folder) + split_words(site or "") + split_words(extra or "")
    if site:
        words.append(site.lower())  # "yourbank.com" as typed, too
    return " ".join(dict.fromkeys(words))


# ---------------------------------------------------------------------------
# writing
# ---------------------------------------------------------------------------

_UPSERT = """
INSERT INTO files (key, path, name, folder, ext, size, mtime, filed_at, site, why, rule, extra, is_dir, words, seen)
VALUES (:key, :path, :name, :folder, :ext, :size, :mtime, :filed_at, :site, :why, :rule, :extra, :is_dir, :words, :seen)
ON CONFLICT(key) DO UPDATE SET
    path = excluded.path, name = excluded.name, folder = excluded.folder, ext = excluded.ext,
    size = excluded.size, mtime = excluded.mtime, is_dir = excluded.is_dir, seen = excluded.seen,
    filed_at = COALESCE(excluded.filed_at, files.filed_at),
    site = COALESCE(excluded.site, files.site),
    why = COALESCE(excluded.why, files.why),
    rule = COALESCE(excluded.rule, files.rule),
    extra = COALESCE(excluded.extra, files.extra),
    words = excluded.words
"""


def _folder_label(path: Path, root: Path | None) -> str:
    if root is not None:
        try:
            rel = path.parent.relative_to(root)
            return "\\".join(rel.parts)
        except ValueError:
            pass
    return str(path.parent)


def _row(path: Path, root: Path | None, stat: os.stat_result | None, seen: int, is_dir: bool = False,
         known: dict[str, Any] | None = None) -> dict[str, Any]:
    known = known or {}
    folder = _folder_label(path, root)
    site, extra = known.get("site") or None, known.get("extra") or None
    return {
        "key": path_key(path),
        "path": str(path),
        "name": path.name,
        "folder": folder,
        "ext": "" if is_dir else path.suffix.lower(),
        "size": 0 if is_dir or stat is None else int(stat.st_size),
        "mtime": float(stat.st_mtime) if stat is not None else 0.0,
        "filed_at": known.get("filed_at"),
        "site": site,
        "why": known.get("why") or None,
        "rule": known.get("rule") or None,
        "extra": extra,
        "is_dir": 1 if is_dir else 0,
        "words": search_text(path.name, folder, site, extra),
        "seen": seen,
    }


def _timestamp(value: Any) -> float | None:
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except (TypeError, ValueError):
        return None


def _known_from_record(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "filed_at": _timestamp(record.get("timestamp")),
        "site": record.get("site"),
        "why": record.get("why"),
        "rule": record.get("rule"),
    }


def _print_details() -> dict[str, str]:
    """{path key: "X1C PLA Benchy by Someone"} for the print files that were filed."""
    out: dict[str, str] = {}
    try:
        import prints
        for row in prints.rows():
            path = row.get("path")
            if not path:
                continue
            parts = [row.get(k) for k in ("title", "designer", "printer", "filament", "filament_color", "slicer")]
            out[path_key(path)] = " ".join(str(p) for p in parts if p)
    except Exception:
        logging.debug("Print details could not be read for the file list", exc_info=True)
    return out


def _history_notes() -> dict[str, dict[str, Any]]:
    """{path key of where a file was filed: when, why, by which rule, from which website}."""
    notes: dict[str, dict[str, Any]] = {}
    try:
        import safe_ops
        for record in safe_ops.read_journal():
            if record.get("action") in sorter.NON_MOVE_ACTIONS or not record.get("dest"):
                continue
            notes[path_key(record["dest"])] = _known_from_record(record)  # the latest move wins
    except Exception:
        logging.debug("The history could not be read for the file list", exc_info=True)
    return notes


def _walk(root: Path, limit: int = MAX_FILES) -> Iterable[tuple[Path, os.stat_result | None, bool]]:
    """Every file under ``root`` as (path, stat, is a project folder). Never follows links."""
    internal = {name.lower() for name in sorter.INTERNAL_DIR_NAMES}
    stack = [root]
    count = 0
    while stack:
        folder = stack.pop()
        try:
            entries = list(os.scandir(folder))
        except OSError:
            continue
        for entry in entries:
            if count >= limit:
                logging.info("The file list stopped at %s files in %s", limit, root)
                return
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    low = entry.name.lower()
                    if low in SKIP_DIR_NAMES or (folder == root and low in internal):
                        continue
                    here = Path(entry.path)
                    if any((here / marker).exists() for marker in PROJECT_MARKERS):
                        count += 1
                        yield here, entry.stat(follow_symlinks=False), True
                        continue
                    stack.append(here)
                elif entry.is_file(follow_symlinks=False):
                    count += 1
                    yield Path(entry.path), entry.stat(follow_symlinks=False), False
            except OSError:
                continue


def rebuild(root: Path | str, target: Path | str | None = None) -> dict[str, int]:
    """Walk ``root`` and make the list match it: new files added, changed ones updated, and
    rows for files that are gone removed. What is known about a file (when it was filed, the
    website, print details) is kept or filled in from the history. Returns the counts."""
    root = Path(root).resolve()
    started = time.perf_counter()
    notes = _history_notes()
    prints_by_key = _print_details()
    seen = time.time_ns()  # marks the rows this walk met; the rest under root are gone
    listed = 0
    with closing(connect(target)) as conn:
        with conn:
            batch: list[dict[str, Any]] = []
            for path, stat, is_project in _walk(root):
                key = path_key(path)
                known = dict(notes.get(key) or {})
                if key in prints_by_key:
                    known["extra"] = prints_by_key[key]
                if is_project:
                    known["extra"] = "project"
                batch.append(_row(path, root, stat, seen, is_dir=is_project, known=known))
                if len(batch) >= 2000:
                    conn.executemany(_UPSERT, batch)
                    listed += len(batch)
                    batch = []
            if batch:
                conn.executemany(_UPSERT, batch)
                listed += len(batch)
            prefix = path_key(root) + os.sep
            removed = conn.execute(
                "DELETE FROM files WHERE seen != ? AND substr(key, 1, ?) = ?", (seen, len(prefix), prefix)
            ).rowcount
    took = time.perf_counter() - started
    logging.info("File list: %s files in %s (%s removed), %.1fs", listed, root, removed, took)
    return {"files": listed, "removed": int(removed or 0), "ms": int(took * 1000)}


def recent_record(dest: Path | str, max_bytes: int = 256 * 1024) -> dict[str, Any] | None:
    """The newest history record that put a file at ``dest``, looking only at the end of the
    history (the move that was just made is there), so it stays quick however long the history is."""
    history = Path(sorter.HISTORY_FILE)
    wanted = path_key(dest)
    try:
        size = history.stat().st_size
        with history.open("rb") as handle:
            if size > max_bytes:
                handle.seek(size - max_bytes)
                handle.readline()
            chunk = handle.read().decode("utf-8", errors="ignore")
    except OSError:
        return None
    for line in reversed(chunk.splitlines()):
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if (isinstance(record, dict) and record.get("dest") and path_key(record["dest"]) == wanted
                and record.get("action") not in sorter.NON_MOVE_ACTIONS):
            return record
    return None


def note_file(path: Path | str, root: Path | str | None = None, known: dict[str, Any] | None = None,
              target: Path | str | None = None) -> bool:
    """Add or refresh one file. ``known`` may carry filed_at, site, why, rule, extra."""
    path = Path(path)
    try:
        stat = path.stat()
        if not path.is_file():
            return False
        with closing(connect(target)) as conn, conn:
            conn.execute(_UPSERT, _row(path, Path(root).resolve() if root else None, stat, time.time_ns(),
                                       known=known))
        return True
    except (OSError, sqlite3.Error) as exc:
        logging.debug("Could not add %s to the file list: %s", path, exc)
        return False


def note_filed(dest: Path | str, root: Path | str | None = None, source: Path | str | None = None,
               target: Path | str | None = None) -> bool:
    """A file was just filed at ``dest`` (from ``source``): list it with what the history says."""
    known = _known_from_record(recent_record(dest) or {})
    if known.get("filed_at") is None:
        known["filed_at"] = time.time()
    try:
        import prints
        row = prints.latest_for(Path(dest).name)
        if row and path_key(row.get("path", "")) == path_key(dest):
            parts = [row.get(k) for k in ("title", "designer", "printer", "filament", "filament_color", "slicer")]
            known["extra"] = " ".join(str(p) for p in parts if p) or None
    except Exception:
        pass
    if source is not None:
        forget(source, target=target)
    return note_file(dest, root, known, target)


def note_move(src: Path | str, dest: Path | str, root: Path | str | None = None,
              target: Path | str | None = None) -> bool:
    """A file was moved by hand: keep what was known about it, at its new place."""
    known: dict[str, Any] = {}
    try:
        with closing(connect(target)) as conn:
            old = conn.execute("SELECT filed_at, site, why, rule, extra FROM files WHERE key = ?",
                               (path_key(src),)).fetchone()
        if old:
            known = dict(old)
    except sqlite3.Error:
        pass
    forget(src, target=target)
    return note_file(dest, root, known, target)


def forget(path: Path | str, target: Path | str | None = None) -> None:
    """A file is gone (deleted, or moved out of the sorted folder)."""
    try:
        with closing(connect(target)) as conn, conn:
            conn.execute("DELETE FROM files WHERE key = ?", (path_key(path),))
    except sqlite3.Error as exc:
        logging.debug("Could not remove %s from the file list: %s", path, exc)


def count(target: Path | str | None = None) -> int:
    try:
        with closing(connect(target)) as conn:
            return int(conn.execute("SELECT COUNT(*) FROM files").fetchone()[0])
    except sqlite3.Error:
        return 0


# ---------------------------------------------------------------------------
# searching
# ---------------------------------------------------------------------------

def parse_query(text: str) -> tuple[list[str], dict[str, str]]:
    """"pla from:makerworld type:3mf" -> (["pla"], {"from": "makerworld", "type": ".3mf"})."""
    filters: dict[str, str] = {}

    def take(match: re.Match) -> str:
        value = match.group(2).strip('"').strip().lower()
        if value:
            name = match.group(1).lower()
            filters[name] = ("." + value.lstrip(".")) if name == "type" else value
        return " "

    rest = _FILTER.sub(take, str(text))
    words = [w for w in (part.lower() for part in _SPLIT.split(rest)) if w]
    return words, filters


def _like(value: str) -> str:
    return "%" + value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def search(text: str = "", limit: int = 30, three_d_first: bool = False,
           target: Path | str | None = None, now: float | None = None) -> list[dict[str, Any]]:
    """Files matching every word of ``text``, best first.

    Words match from their start ("stat" finds "statement") and ignore plurals. `in:banking`
    keeps one folder, `from:yourbank` one website, `type:pdf` one kind of file. With no words
    the newest files come first. Files that are no longer on disk are left out (and removed
    from the list as they are met).
    """
    words, filters = parse_query(text)
    where: list[str] = []
    args: list[Any] = []
    if "in" in filters:
        where.append("LOWER(f.folder) LIKE ? ESCAPE '\\'")
        args.append(_like(filters["in"]))
    if "from" in filters:
        where.append("LOWER(COALESCE(f.site, '')) LIKE ? ESCAPE '\\'")
        args.append(_like(filters["from"]))
    if "type" in filters:
        where.append("f.ext = ?")
        args.append(filters["type"])

    now = time.time() if now is None else now
    fetch = max(limit * 4, 60)
    try:
        with closing(connect(target)) as conn:
            if words and fts_available(conn):
                terms = []
                for word in words:
                    forms = dict.fromkeys((word, _singular(word)))
                    terms.append("(" + " OR ".join(f'"{form}"*' for form in forms) + ")")
                sql = ("SELECT f.*, bm25(files_fts) AS rank FROM files_fts JOIN files f ON f.rowid = files_fts.rowid "
                       "WHERE files_fts MATCH ?" + "".join(f" AND {w}" for w in where) +
                       " ORDER BY rank LIMIT ?")
                rows = conn.execute(sql, [" AND ".join(terms), *args, fetch]).fetchall()
            else:
                for word in words:
                    where.append("(' ' || f.words) LIKE ? ESCAPE '\\'")
                    args.append("% " + _like(_singular(word))[1:])
                sql = ("SELECT f.*, 0.0 AS rank FROM files f" +
                       (" WHERE " + " AND ".join(where) if where else "") +
                       " ORDER BY COALESCE(f.filed_at, f.mtime) DESC LIMIT ?")
                rows = conn.execute(sql, [*args, fetch]).fetchall()
    except sqlite3.Error as exc:
        logging.debug("File search failed: %s", exc)
        return []

    scored: list[tuple[float, dict[str, Any]]] = []
    for row in rows:
        item = dict(row)
        when = item.get("filed_at") or item.get("mtime") or 0.0
        age_days = max(now - float(when), 0.0) / 86400
        # bm25 is smaller for a better match; newer files get a head start that fades over months.
        score = float(item.pop("rank") or 0.0) - RECENCY_WEIGHT / (1.0 + age_days / 30.0)
        item["when"] = float(when)
        scored.append((score, item))
    if words:
        scored.sort(key=lambda pair: pair[0])
    if three_d_first:
        scored.sort(key=lambda pair: 0 if pair[1]["ext"] in THREE_D_SUFFIXES else 1)

    found: list[dict[str, Any]] = []
    gone: list[str] = []
    for _score, item in scored:
        if not os.path.exists(item["path"]):
            gone.append(item["key"])
            continue
        item.pop("words", None)
        item.pop("seen", None)
        found.append(item)
        if len(found) >= limit:
            break
    if gone:
        try:
            with closing(connect(target)) as conn, conn:
                conn.executemany("DELETE FROM files WHERE key = ?", [(key,) for key in gone])
        except sqlite3.Error:
            pass
    return found


# ---------------------------------------------------------------------------
# command line: --find "words"
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="--find", description="Find a file in your sorted folder.")
    parser.add_argument("words", nargs="*", help='What to look for, e.g. statement from:yourbank type:pdf')
    parser.add_argument("--rebuild", action="store_true", help="Walk the folder again first.")
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args(argv)
    config = sorter.load_config()
    root = sorter.watch_root(config)
    if args.rebuild or not count():
        result = rebuild(root)
        print(f"Listed {result['files']} files in {root}.")
    for item in search(" ".join(args.words), limit=args.limit):
        print(item["path"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
