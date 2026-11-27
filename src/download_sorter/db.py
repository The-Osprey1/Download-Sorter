"""Single WAL-mode SQLite store for move history, batches, and undo state."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import logging
import os
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any

_SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS moves (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id TEXT NOT NULL,
    filename TEXT NOT NULL,
    src_path TEXT NOT NULL,
    dest_path TEXT NOT NULL,
    reason TEXT NOT NULL,
    matched_rule TEXT NOT NULL,
    match_type TEXT NOT NULL,
    source_url TEXT NOT NULL DEFAULT '',
    size_bytes INTEGER NOT NULL DEFAULT 0,
    is_duplicate INTEGER NOT NULL DEFAULT 0,
    moved_at REAL NOT NULL,
    undone_at REAL,
    status TEXT NOT NULL DEFAULT 'COMPLETED'
);

CREATE INDEX IF NOT EXISTS idx_moves_batch ON moves(batch_id);
CREATE INDEX IF NOT EXISTS idx_moves_active ON moves(undone_at, moved_at DESC);

CREATE TABLE IF NOT EXISTS file_hashes (
    path TEXT PRIMARY KEY,
    size_bytes INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    sparse_hash TEXT NOT NULL,
    full_hash TEXT,
    updated_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_file_hashes_size_sparse ON file_hashes(size_bytes, sparse_hash);
"""

# Hidden folder that holds partial copies while a cross-volume move or a ZIP
# extraction is in progress (``<folder>\\.staging\\<id>\\``).
STAGING_DIR_NAME = ".staging"


def _remove_staged_copies(dest: Path) -> int:
    """Delete partial copies of ``dest`` left in ``dest.parent/.staging`` by a crash.

    Everything under ``.staging`` is written by Download Sorter itself, never by
    the user, and the move it belonged to never finished.
    """
    staging_root = dest.parent / STAGING_DIR_NAME
    if not staging_root.is_dir():
        return 0
    removed = 0
    try:
        batch_dirs = [d for d in staging_root.iterdir() if d.is_dir()]
    except OSError:
        return 0
    for batch_dir in batch_dirs:
        staged = batch_dir / dest.name
        try:
            if staged.is_file():
                staged.unlink()
                removed += 1
            if not any(batch_dir.iterdir()):
                batch_dir.rmdir()
        except OSError as exc:
            logging.warning("Could not remove staged copy %s: %s", staged, exc)
    try:
        if not any(staging_root.iterdir()):
            staging_root.rmdir()
    except OSError:
        pass
    return removed


# Move lifecycle. A row is written as PENDING before the file is touched and
# becomes COMPLETED once the file is at its destination, so a crash between the
# two never leaves a moved file without history. ROLLED_BACK rows are moves that
# never happened; ORPHANED rows are moves whose sorted file was later deleted.
STATUS_PENDING = "PENDING"
STATUS_COMPLETED = "COMPLETED"
STATUS_ROLLED_BACK = "ROLLED_BACK"
STATUS_ORPHANED = "ORPHANED"

_PREFIX_CHUNK = 1024 * 1024


def _is_leftover_copy(src: Path, dest: Path) -> bool:
    """True when ``dest`` is a partial or complete copy of ``src`` (safe to remove).

    An interrupted cross-volume move leaves the first N bytes of the source at the
    destination. Anything else found there is a different file and is left alone.
    """
    try:
        src_size = src.stat().st_size
        dest_size = dest.stat().st_size
        if dest_size > src_size:
            return False
        remaining = dest_size
        with open(src, "rb") as fs, open(dest, "rb") as fd:
            while remaining > 0:
                want = min(_PREFIX_CHUNK, remaining)
                a = fs.read(want)
                b = fd.read(want)
                if a != b or not a:
                    return False
                remaining -= len(a)
        return True
    except OSError:
        return False


class StateDB:
    """Thread-safe SQLite repository for all Download Sorter state."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=5.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        # synchronous resets to FULL on every new connection, so it has to be set
        # here and not only when the schema is created. Re-asserting WAL is a no-op.
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        return conn

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        if self._conn is None:
            self._conn = self._connect()
        yield self._conn

    def _init_schema(self) -> None:
        with self._lock, self._connection() as conn:
            conn.executescript(_SCHEMA_SQL)
            columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(moves)")}
            if "status" not in columns:
                # A state.db from before two-phase journaling: every row is a finished move.
                conn.execute("ALTER TABLE moves ADD COLUMN status TEXT NOT NULL DEFAULT 'COMPLETED'")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_moves_status ON moves(status)")
            conn.commit()

    def record_move(
        self,
        *,
        batch_id: str,
        filename: str,
        src_path: str | Path,
        dest_path: str | Path,
        reason: str,
        matched_rule: str,
        match_type: str,
        source_url: str = "",
        size_bytes: int = 0,
        is_duplicate: bool = False,
        moved_at: float | None = None,
        status: str = STATUS_COMPLETED,
    ) -> int:
        ts = time.time() if moved_at is None else float(moved_at)
        with self._lock, self._connection() as conn:
            cur = conn.execute(
                """
                INSERT INTO moves (
                    batch_id, filename, src_path, dest_path,
                    reason, matched_rule, match_type, source_url,
                    size_bytes, is_duplicate, moved_at, undone_at, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
                """,
                (
                    batch_id,
                    filename,
                    str(src_path),
                    str(dest_path),
                    reason,
                    matched_rule,
                    match_type,
                    source_url,
                    int(size_bytes),
                    1 if is_duplicate else 0,
                    ts,
                    str(status),
                ),
            )
            conn.commit()
            assert cur.lastrowid is not None
            return int(cur.lastrowid)

    # ------------------------------------------------------------------
    # Two-phase move journal
    # ------------------------------------------------------------------
    def record_move_intent(
        self,
        *,
        batch_id: str,
        filename: str,
        src_path: str | Path,
        dest_path: str | Path,
        reason: str,
        matched_rule: str,
        match_type: str,
        source_url: str = "",
        size_bytes: int = 0,
        is_duplicate: bool = False,
        moved_at: float | None = None,
    ) -> int:
        """Phase 1: journal a move as PENDING before the file is touched."""
        return self.record_move(
            batch_id=batch_id,
            filename=filename,
            src_path=src_path,
            dest_path=dest_path,
            reason=reason,
            matched_rule=matched_rule,
            match_type=match_type,
            source_url=source_url,
            size_bytes=size_bytes,
            is_duplicate=is_duplicate,
            moved_at=moved_at,
            status=STATUS_PENDING,
        )

    def complete_move_intent(self, move_id: int, final_dest: str | Path) -> None:
        """Phase 2: the file is at ``final_dest``; the move is now part of history."""
        with self._lock, self._connection() as conn:
            conn.execute(
                "UPDATE moves SET dest_path = ?, status = ? WHERE id = ?",
                (str(final_dest), STATUS_COMPLETED, int(move_id)),
            )
            conn.commit()

    def rollback_move_intent(self, move_id: int) -> None:
        """The move did not happen; keep the row for diagnosis but out of history."""
        with self._lock, self._connection() as conn:
            conn.execute(
                "UPDATE moves SET status = ? WHERE id = ? AND status = ?",
                (STATUS_ROLLED_BACK, int(move_id), STATUS_PENDING),
            )
            conn.commit()

    def list_pending_moves(self) -> list[dict[str, Any]]:
        with self._lock, self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM moves WHERE status = ? ORDER BY id", (STATUS_PENDING,)
            ).fetchall()
            return [dict(r) for r in rows]

    def reconcile_incomplete_moves(self) -> dict[str, int]:
        """Settle PENDING rows left by a crash, by looking at what is on disk.

        - source gone, destination present: the move finished -> COMPLETED
        - source present, destination absent: it never started -> ROLLED_BACK
        - both present: an interrupted copy; the leftover is removed, but only when
          it really is a copy of the source -> ROLLED_BACK
        - neither present: nothing left to restore -> ROLLED_BACK
        """
        summary = {"completed": 0, "rolled_back": 0, "partials_removed": 0}
        for rec in self.list_pending_moves():
            move_id = int(rec["id"])
            src = Path(str(rec["src_path"]))
            dest = Path(str(rec["dest_path"]))
            summary["partials_removed"] += _remove_staged_copies(dest)
            src_exists = os.path.lexists(src)
            dest_exists = os.path.lexists(dest)
            if dest_exists and not src_exists:
                self.complete_move_intent(move_id, dest)
                summary["completed"] += 1
                continue
            if src_exists and dest_exists:
                if _is_leftover_copy(src, dest):
                    try:
                        dest.unlink()
                        summary["partials_removed"] += 1
                    except OSError as exc:
                        logging.warning("Could not remove partial copy %s: %s", dest, exc)
                else:
                    logging.warning(
                        "Interrupted move of %s: %s holds a different file, left untouched", src, dest
                    )
            self.rollback_move_intent(move_id)
            summary["rolled_back"] += 1
        if summary["completed"] or summary["rolled_back"]:
            logging.info("Reconciled interrupted moves: %s", summary)
        return summary

    # ------------------------------------------------------------------
    # WAL hygiene
    # ------------------------------------------------------------------
    def checkpoint(self, truncate: bool = False) -> None:
        """Fold the write-ahead log into state.db (PASSIVE never blocks writers)."""
        mode = "TRUNCATE" if truncate else "PASSIVE"
        try:
            with self._lock, self._connection() as conn:
                conn.execute(f"PRAGMA wal_checkpoint({mode});").fetchall()
        except sqlite3.Error as exc:
            logging.debug("WAL checkpoint (%s) skipped: %s", mode, exc)

    def close(self) -> None:
        """Shutdown hygiene: empty the WAL file, refresh planner stats, and release connection.

        The persistent handle is reopened lazily if the store is queried again afterwards.
        """
        with self._lock:
            conn = self._conn
            try:
                if conn is None and self.db_path.exists():
                    conn = self._connect()
                if conn is not None:
                    conn.execute("PRAGMA wal_checkpoint(TRUNCATE);").fetchall()
                    conn.execute("PRAGMA optimize;").fetchall()
            except sqlite3.Error as exc:
                logging.debug("StateDB close hygiene skipped: %s", exc)
            finally:
                if conn is not None:
                    try:
                        conn.close()
                    except sqlite3.Error:
                        pass
                self._conn = None

    # ------------------------------------------------------------------
    # History queries (only COMPLETED moves are history)
    # ------------------------------------------------------------------
    def list_active_moves(self, limit: int = 200) -> list[dict[str, Any]]:
        with self._lock, self._connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM moves
                WHERE undone_at IS NULL AND status = 'COMPLETED'
                ORDER BY moved_at DESC, id DESC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
            return [dict(r) for r in rows]

    def list_batches(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock, self._connection() as conn:
            rows = conn.execute(
                """
                SELECT
                    batch_id,
                    COUNT(*) AS count,
                    MIN(moved_at) AS started_at,
                    MAX(moved_at) AS finished_at,
                    SUM(CASE WHEN undone_at IS NULL THEN 1 ELSE 0 END) AS active_count
                FROM moves
                WHERE status IN ('COMPLETED', 'ORPHANED')
                GROUP BY batch_id
                ORDER BY MAX(moved_at) DESC, MAX(id) DESC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_batch_moves(self, batch_id: str | None = None) -> list[dict[str, Any]]:
        with self._lock, self._connection() as conn:
            target_batch = batch_id
            if not target_batch:
                latest = conn.execute(
                    """
                    SELECT batch_id FROM moves
                    WHERE undone_at IS NULL AND status = 'COMPLETED'
                    ORDER BY moved_at DESC, id DESC
                    LIMIT 1
                    """
                ).fetchone()
                if not latest:
                    return []
                target_batch = str(latest["batch_id"])
            rows = conn.execute(
                """
                SELECT * FROM moves
                WHERE batch_id = ? AND undone_at IS NULL AND status = 'COMPLETED'
                ORDER BY id DESC
                """,
                (target_batch,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_move(self, move_id: int) -> dict[str, Any] | None:
        with self._lock, self._connection() as conn:
            row = conn.execute("SELECT * FROM moves WHERE id = ?", (int(move_id),)).fetchone()
            return dict(row) if row else None

    def rewrite_dest_prefix(self, old_prefix: str, new_prefix: str) -> int:
        """Point history rows whose destination is under ``old_prefix`` at ``new_prefix``."""
        old_prefix = str(old_prefix).rstrip("\\/")
        new_prefix = str(new_prefix).rstrip("\\/")
        changed = 0
        with self._lock, self._connection() as conn:
            rows = conn.execute("SELECT id, dest_path FROM moves").fetchall()
            for row in rows:
                dest = str(row["dest_path"])
                if dest == old_prefix or dest.startswith((old_prefix + "\\", old_prefix + "/")):
                    conn.execute(
                        "UPDATE moves SET dest_path = ? WHERE id = ?",
                        (new_prefix + dest[len(old_prefix) :], int(row["id"])),
                    )
                    changed += 1
            conn.commit()
        return changed

    def mark_undone(self, move_ids: list[int]) -> None:
        if not move_ids:
            return
        now = time.time()
        placeholders = ",".join("?" for _ in move_ids)
        with self._lock, self._connection() as conn:
            conn.execute(
                f"UPDATE moves SET undone_at = ? WHERE id IN ({placeholders})",
                [now, *[int(i) for i in move_ids]],
            )
            conn.commit()

    def mark_orphaned(self, move_ids: list[int]) -> None:
        """The sorted file no longer exists: retire the move so undo stops offering it."""
        if not move_ids:
            return
        now = time.time()
        placeholders = ",".join("?" for _ in move_ids)
        with self._lock, self._connection() as conn:
            conn.execute(
                f"UPDATE moves SET undone_at = ?, status = ? WHERE id IN ({placeholders})",
                [now, STATUS_ORPHANED, *[int(i) for i in move_ids]],
            )
            conn.commit()

    def mark_orphaned_by_dest(self, dest_paths: list[str]) -> int:
        """Retire active COMPLETED move rows matching deleted ``dest_path`` entries."""
        cleaned = [str(p) for p in dest_paths if p]
        if not cleaned:
            return 0
        now = time.time()
        placeholders = ",".join("?" for _ in cleaned)
        with self._lock, self._connection() as conn:
            cur = conn.execute(
                f"""
                UPDATE moves
                SET undone_at = ?, status = ?
                WHERE undone_at IS NULL AND status = 'COMPLETED' AND dest_path IN ({placeholders})
                """,
                [now, STATUS_ORPHANED, *cleaned],
            )
            conn.commit()
            return int(cur.rowcount or 0)

    def stats(self) -> dict[str, Any]:
        with self._lock, self._connection() as conn:
            row = conn.execute(
                """
                SELECT
                    COUNT(*) AS total_moves,
                    SUM(CASE WHEN undone_at IS NULL THEN 1 ELSE 0 END) AS active_moves,
                    SUM(CASE WHEN undone_at IS NOT NULL THEN 1 ELSE 0 END) AS undone_moves
                FROM moves
                WHERE status IN ('COMPLETED', 'ORPHANED')
                """
            ).fetchone()
            last = conn.execute(
                """
                SELECT batch_id, moved_at FROM moves
                WHERE undone_at IS NULL AND status = 'COMPLETED'
                ORDER BY moved_at DESC, id DESC
                LIMIT 1
                """
            ).fetchone()
            return {
                "total_moves": int(row["total_moves"] or 0),
                "active_moves": int(row["active_moves"] or 0),
                "undone_moves": int(row["undone_moves"] or 0),
                "last_batch_id": str(last["batch_id"]) if last else "",
                "last_moved_at": float(last["moved_at"]) if last else None,
            }

    # ------------------------------------------------------------------
    # Sparse-hash duplicate index (shares state.db, no second database)
    # ------------------------------------------------------------------
    def record_file_hash(
        self,
        path: str | Path,
        *,
        size_bytes: int,
        mtime_ns: int,
        sparse_hash: str,
        full_hash: str | None = None,
    ) -> None:
        norm = str(Path(path).resolve())
        now = time.time()
        with self._lock, self._connection() as conn:
            conn.execute(
                """
                INSERT INTO file_hashes (path, size_bytes, mtime_ns, sparse_hash, full_hash, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                    size_bytes = excluded.size_bytes,
                    mtime_ns = excluded.mtime_ns,
                    sparse_hash = excluded.sparse_hash,
                    full_hash = COALESCE(excluded.full_hash, file_hashes.full_hash),
                    updated_at = excluded.updated_at
                """,
                (norm, int(size_bytes), int(mtime_ns), str(sparse_hash), full_hash, now),
            )
            conn.commit()

    def remove_file_hash(self, path: str | Path) -> None:
        norm = str(Path(path).resolve())
        with self._lock, self._connection() as conn:
            conn.execute("DELETE FROM file_hashes WHERE path = ?", (norm,))
            conn.commit()

    def find_hash_candidates(
        self,
        size_bytes: int,
        *,
        exclude_path: str | Path | None = None,
    ) -> list[dict[str, Any]]:
        excl = str(Path(exclude_path).resolve()) if exclude_path is not None else ""
        with self._lock, self._connection() as conn:
            rows = conn.execute(
                """
                SELECT path, size_bytes, mtime_ns, sparse_hash, full_hash, updated_at
                FROM file_hashes
                WHERE size_bytes = ? AND path != ?
                """,
                (int(size_bytes), excl),
            ).fetchall()
            return [dict(r) for r in rows]

    def prune_missing_hashes(self) -> int:
        with self._lock, self._connection() as conn:
            rows = conn.execute("SELECT path FROM file_hashes").fetchall()
            missing = [str(r["path"]) for r in rows if not Path(str(r["path"])).exists()]
            if missing:
                conn.executemany("DELETE FROM file_hashes WHERE path = ?", [(p,) for p in missing])
                conn.commit()
            return len(missing)
