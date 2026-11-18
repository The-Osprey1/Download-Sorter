"""Size-gated 3-point sparse hashing and SQLite duplicate index for Download Sorter."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import struct
import threading
import time
from dataclasses import dataclass
from pathlib import Path

SPARSE_CHUNK_SIZE = 64 * 1024  # 64 KB


def default_index_path() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    return base / "download-sorter" / "dedup_index.db"


def compute_sparse_hash(path: Path | str, chunk_size: int = SPARSE_CHUNK_SIZE) -> str:
    """Compute a 3-point sparse SHA-256 fingerprint (head/mid/tail 64 KB + size)."""
    target = Path(path)
    size = target.stat().st_size
    h = hashlib.sha256()
    h.update(struct.pack("<Q", size))
    with target.open("rb") as f:
        if size <= 3 * chunk_size:
            while True:
                buf = f.read(chunk_size)
                if not buf:
                    break
                h.update(buf)
        else:
            # Head
            h.update(f.read(chunk_size))
            # Mid
            mid_offset = (size - chunk_size) // 2
            f.seek(mid_offset)
            h.update(f.read(chunk_size))
            # Tail
            f.seek(size - chunk_size)
            h.update(f.read(chunk_size))
    return h.hexdigest()


def compute_full_sha256(path: Path | str, chunk_size: int = 1024 * 1024) -> str:
    """Compute full streaming SHA-256 digest of a file."""
    target = Path(path)
    h = hashlib.sha256()
    with target.open("rb") as f:
        while True:
            buf = f.read(chunk_size)
            if not buf:
                break
            h.update(buf)
    return h.hexdigest()


@dataclass(frozen=True)
class FileEntry:
    path: str
    size: int
    mtime_ns: int
    sparse_hash: str
    full_hash: str | None
    updated_at: float


class DedupIndex:
    """Thread-safe SQLite index for fast size-gated duplicate detection across renamed files."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        self.db_path = Path(db_path) if db_path is not None else default_index_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS file_hashes (
                    path TEXT PRIMARY KEY,
                    size INTEGER NOT NULL,
                    mtime_ns INTEGER NOT NULL,
                    sparse_hash TEXT NOT NULL,
                    full_hash TEXT,
                    updated_at REAL NOT NULL
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_file_hashes_size_sparse "
                "ON file_hashes(size, sparse_hash)"
            )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> DedupIndex:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    @staticmethod
    def _norm(path: Path | str) -> str:
        return str(Path(path).resolve())

    def record_file(
        self,
        path: Path | str,
        *,
        sparse_hash: str | None = None,
        full_hash: str | None = None,
    ) -> FileEntry | None:
        """Index ``path`` with its size, mtime_ns, sparse_hash, and optional full_hash."""
        target = Path(path)
        if not target.exists() or not target.is_file():
            return None
        st = target.stat()
        norm = self._norm(target)
        s_hash = sparse_hash or compute_sparse_hash(target)
        now = time.time()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO file_hashes (path, size, mtime_ns, sparse_hash, full_hash, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                    size = excluded.size,
                    mtime_ns = excluded.mtime_ns,
                    sparse_hash = excluded.sparse_hash,
                    full_hash = COALESCE(excluded.full_hash, file_hashes.full_hash),
                    updated_at = excluded.updated_at
                """,
                (norm, st.st_size, st.st_mtime_ns, s_hash, full_hash, now),
            )
            self._conn.commit()
        return FileEntry(
            path=norm,
            size=st.st_size,
            mtime_ns=st.st_mtime_ns,
            sparse_hash=s_hash,
            full_hash=full_hash,
            updated_at=now,
        )

    def remove_file(self, path: Path | str) -> None:
        norm = self._norm(path)
        with self._lock:
            self._conn.execute("DELETE FROM file_hashes WHERE path = ?", (norm,))
            self._conn.commit()

    def get_entry(self, path: Path | str) -> FileEntry | None:
        norm = self._norm(path)
        with self._lock:
            row = self._conn.execute(
                "SELECT path, size, mtime_ns, sparse_hash, full_hash, updated_at FROM file_hashes WHERE path = ?",
                (norm,),
            ).fetchone()
        if row is None:
            return None
        return FileEntry(
            path=row["path"],
            size=row["size"],
            mtime_ns=row["mtime_ns"],
            sparse_hash=row["sparse_hash"],
            full_hash=row["full_hash"],
            updated_at=row["updated_at"],
        )

    def find_duplicate(self, candidate: Path | str, *, record_if_unique: bool = False) -> Path | None:
        """Return an existing duplicate path for ``candidate`` using size -> sparse -> full SHA-256."""
        cand = Path(candidate)
        if not cand.exists() or not cand.is_file():
            return None
        cand_norm = self._norm(cand)
        cand_st = cand.stat()

        with self._lock:
            size_rows = self._conn.execute(
                "SELECT path, size, mtime_ns, sparse_hash, full_hash FROM file_hashes "
                "WHERE size = ? AND path != ?",
                (cand_st.st_size, cand_norm),
            ).fetchall()

        if not size_rows:
            if record_if_unique:
                self.record_file(cand)
            return None

        cand_sparse = compute_sparse_hash(cand)
        sparse_matches = [r for r in size_rows if r["sparse_hash"] == cand_sparse]
        if not sparse_matches:
            if record_if_unique:
                self.record_file(cand, sparse_hash=cand_sparse)
            return None

        cand_full: str | None = None
        for row in sparse_matches:
            other = Path(row["path"])
            if not other.exists() or not other.is_file():
                self.remove_file(other)
                continue
            try:
                other_st = other.stat()
            except OSError:
                continue
            if other_st.st_size != row["size"]:
                self.remove_file(other)
                continue

            if cand_full is None:
                cand_full = compute_full_sha256(cand)

            other_full = row["full_hash"]
            if not other_full or other_st.st_mtime_ns != row["mtime_ns"]:
                other_full = compute_full_sha256(other)
                self.record_file(other, sparse_hash=row["sparse_hash"], full_hash=other_full)

            if cand_full == other_full:
                return other

        if record_if_unique:
            self.record_file(cand, sparse_hash=cand_sparse, full_hash=cand_full)
        return None

    def prune_missing(self) -> int:
        """Remove indexed paths that no longer exist on disk. Returns count removed."""
        with self._lock:
            rows = self._conn.execute("SELECT path FROM file_hashes").fetchall()
            missing = [r["path"] for r in rows if not Path(r["path"]).exists()]
            if missing:
                self._conn.executemany(
                    "DELETE FROM file_hashes WHERE path = ?",
                    [(p,) for p in missing],
                )
                self._conn.commit()
            return len(missing)
