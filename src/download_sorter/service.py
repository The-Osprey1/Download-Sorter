"""Single-process top-level Downloads watcher, dry-run simulator, and orchestrator."""

from __future__ import annotations

from collections.abc import Callable
import copy
import dataclasses
import logging
import os
from pathlib import Path
import threading
import time
import uuid
from typing import Any

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from download_sorter.architecture import (
    PREMADE_FOLDER_CATALOG,
    TIER2_SUGGESTIONS_BY_PARENT,
    architecture_from_config,
)
from download_sorter.config import (
    DEFAULT_CATEGORY_MAP,
    DEFAULT_TYPE_SUBFOLDERS,
    Config,
    load_config,
    save_config,
)
from download_sorter.db import StateDB
from download_sorter.dest import is_under_watch_root, to_relative_dest
from download_sorter.fs_ops import (
    ProgressCallback,
    cleanup_orphaned_staging,
    delete_files_safely,
    execute_move,
    extract_zip_safely,
    is_slicer_running,
    prune_empty_folders,
    resolve_duplicate_delete_new_locate_old,
    resolve_duplicate_keep_both,
    resolve_duplicate_replace_old,
    scan_detox_candidates,
    scan_duplicate_files,
    undo_batch,
    undo_move,
)
from download_sorter.learning import TokenRouteLearner
from download_sorter.paths import AppPaths
from download_sorter.router import FileCandidate, RouteDecision, RouteStep, candidate_from_path, evaluate


class _TopLevelEventHandler(FileSystemEventHandler):
    """Queue only immediate child file events inside ``watch_root``."""

    def __init__(self, watch_root: Path, on_candidate: Callable[[Path], None]) -> None:
        self.watch_root = watch_root.resolve()
        self.on_candidate = on_candidate

    def _enqueue_if_top_level(self, raw_path: str | bytes) -> None:
        if not raw_path:
            return
        text = raw_path.decode("utf-8", errors="ignore") if isinstance(raw_path, bytes) else raw_path
        path = Path(text)
        try:
            parent = path.resolve().parent
        except OSError:
            parent = path.parent
        if parent == self.watch_root:
            self.on_candidate(path)

    def on_created(self, event) -> None:  # type: ignore[no-untyped-def]
        if not event.is_directory:
            self._enqueue_if_top_level(event.src_path)

    def on_modified(self, event) -> None:  # type: ignore[no-untyped-def]
        if not event.is_directory:
            self._enqueue_if_top_level(event.src_path)

    def on_moved(self, event) -> None:  # type: ignore[no-untyped-def]
        if not event.is_directory:
            self._enqueue_if_top_level(event.dest_path)


def _overlaps(a: Path, b: Path) -> bool:
    """True when one folder is the other or sits inside it."""
    return a == b or a in b.parents or b in a.parents


# A file that is open in another program is retried with a growing pause instead of
# every couple of seconds: base * 2^attempt, never longer than this.
BUSY_RETRY_MAX_SECONDS = 60.0
_BUSY_RETRY_MIN_BASE_SECONDS = 0.5
_BUSY_RETRY_MAX_BASE_SECONDS = 5.0


# A .3mf whose hold has run out stays put while a slicer is open, but never longer
# than this, so a slicer left running for weeks cannot pin files in Downloads forever.
SLICER_HOLD_MAX_DAYS = 14


class SorterService:
    """Single owner of Config, StateDB, non-recursive watcher, and sorting worker."""

    def __init__(
        self,
        paths: AppPaths | None = None,
        on_moves_completed: Callable[[str, list[dict[str, Any]]], None] | None = None,
    ) -> None:
        self.paths = paths or AppPaths.default()
        self.paths.ensure_dirs()
        self._lock = threading.RLock()
        self.config: Config = load_config(self.paths)
        self.db = StateDB(self.paths.db_file)
        self.on_moves_completed = on_moves_completed
        self.on_dialog_detected: Callable[[int, dict[str, Any]], None] | None = None
        self.quick_switcher_monitor: Any | None = None

        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._pending: set[Path] = set()
        # path -> (failed attempts, earliest next retry on self._clock, last error text)
        self._busy_backoff: dict[Path, tuple[int, float, str]] = {}
        self._clock: Callable[[], float] = time.monotonic
        self._observer: Observer | None = None
        self._worker_thread: threading.Thread | None = None
        self._config_undo_stack: list[tuple[str, Config]] = []
        self._cached_learner: TokenRouteLearner | None = None
        self._cached_learner_key: tuple[tuple[int, int], tuple[int, int], tuple[int, int]] | None = None

    def get_route_learner(self) -> TokenRouteLearner:
        """Return a cached ``TokenRouteLearner`` invalidated when learned_routes or state.db changes."""
        def _sig(p: Path) -> tuple[int, int]:
            try:
                st = p.stat()
                return (int(st.st_mtime_ns), int(st.st_size))
            except OSError:
                return (0, 0)

        key = (
            _sig(self.paths.data_dir / "learned_routes.json"),
            _sig(self.paths.db_file),
            _sig(Path(f"{self.paths.db_file}-wal")),
        )
        with self._lock:
            if self._cached_learner is not None and self._cached_learner_key == key:
                return self._cached_learner
        learner = TokenRouteLearner()
        learner.train_from_db_and_store(self.db, self.paths.data_dir)
        with self._lock:
            self._cached_learner = learner
            self._cached_learner_key = key
        return learner

    @property
    def watch_root(self) -> Path:
        with self._lock:
            return self.config.resolved_watch_folder(self.paths)

    def watch_roots(self) -> list[Path]:
        """Downloads first, then each extra folder that exists and doesn't overlap.

        A folder inside another watched folder (or containing one) is left out: files
        sorted into it could be picked up and sorted again.
        """
        with self._lock:
            primary = self.config.resolved_watch_folder(self.paths)
            extras = self.config.resolved_extra_folders()
        roots = [primary]
        for folder in extras:
            if not folder.is_dir():
                logging.info("Extra folder to tidy is not there, skipped: %s", folder)
                continue
            if any(_overlaps(folder, root) for root in roots):
                logging.warning("Extra folder overlaps another watched folder, skipped: %s", folder)
                continue
            roots.append(folder)
        return roots

    @property
    def paused(self) -> bool:
        with self._lock:
            return self.config.paused

    def set_paused(self, paused: bool) -> Config:
        with self._lock:
            self.config.paused = bool(paused)
            save_config(self.config, self.paths)
            self._wake_event.set()
            return self.config

    def update_config(self, new_config: Config) -> None:
        old_observer: Observer | None = None
        with self._lock:
            old_roots = self.watch_roots()
            save_config(new_config, self.paths)
            self.config = new_config
            new_roots = self.watch_roots()
            if self._observer is not None and new_roots != old_roots:
                # Detach only. The watcher thread takes this same lock in _enqueue(),
                # so joining it while the lock is held would stall both threads.
                old_observer = self._observer
                self._observer = None
        if old_observer is not None:
            self._stop_observer(old_observer)
            self._start_observer()
        self._wake_event.set()

    def reload_config(self) -> Config:
        # Not under self._lock: update_config() must be free to join the watcher.
        loaded = load_config(self.paths)
        self.update_config(loaded)
        return loaded

    def _append_ml_hint(
        self,
        cand: FileCandidate,
        decision: RouteDecision,
        *,
        learner: TokenRouteLearner | None = None,
        cfg: Config | None = None,
    ) -> RouteDecision:
        if decision.match_type != "fallback":
            return decision
        active_cfg = cfg or self.config
        if not active_cfg.ai_smart_hints_enabled and not active_cfg.ai_auto_route_fallback:
            return decision
        try:
            model = learner or self.get_route_learner()
            min_conf = max(0.50, min(0.95, float(active_cfg.ai_min_confidence) / 100.0))
            pred = model.predict_folder(
                cand.filename,
                source_url=cand.source_url,
                ext=cand.extension,
                min_confidence=min_conf,
            )
        except Exception:
            logging.debug("Smart hint evaluation skipped", exc_info=True)
            return decision
        if pred is None:
            return decision
        pct = int(round(pred.confidence * 100))
        dest_display = pred.destination.replace(chr(92), "/")
        reasons = ", ".join(pred.top_reasons)
        if active_cfg.ai_auto_route_fallback:
            detail = f"Auto-routing to '{dest_display}' ({pct}% match · {reasons})"
            return RouteDecision(
                action="move",
                relative_dest=Path(pred.destination) / cand.filename,
                reason=f"AI Smart Route ({pct}% match · {reasons})",
                matched_rule="AI Smart Route",
                match_type="ai_smart_route",
                trace=list(decision.trace)
                + [
                    RouteStep(
                        stage="Experimental AI",
                        label="Auto-Route",
                        matched=True,
                        detail=detail,
                    )
                ],
            )
        detail = f"Likely '{dest_display}' ({pct}% match · {reasons})"
        new_trace = list(decision.trace) + [
            RouteStep(
                stage="Smart Hint",
                label="Learned pattern",
                matched=False,
                detail=detail,
            )
        ]
        return dataclasses.replace(decision, trace=new_trace)

    def explain(self, filename: str, source_url: str = "") -> RouteDecision:
        cand = FileCandidate(filename=filename, source_url=source_url)
        with self._lock:
            cfg = self.config
            decision = evaluate(cand, cfg)
        return self._append_ml_hint(cand, decision, cfg=cfg)

    @staticmethod
    def _is_3mf_held(path: Path, cfg: Config, now: float | None = None) -> bool:
        if not cfg.hold_3mf_enabled or cfg.hold_3mf_days <= 0:
            return False
        if path.suffix.lower() != ".3mf":
            return False
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return False
        ref_now = time.time() if now is None else now
        age_seconds = max(0.0, ref_now - mtime)
        if age_seconds < (cfg.hold_3mf_days * 86400.0):
            return True
        # Hold has run out. A slicer keeps the model in memory without holding the
        # file open, so while one is running the file stays where the slicer expects
        # it, up to the safety cap. "Move Held .3MF" still files it on request.
        if age_seconds < (SLICER_HOLD_MAX_DAYS * 86400.0):
            return bool(is_slicer_running())
        return False

    def list_held_3mf(self, root: Path | None = None) -> list[Path]:
        """Return top-level ``.3mf`` files currently inside the grace-period hold."""
        with self._lock:
            cfg = self.config
            target = (root or cfg.resolved_watch_folder(self.paths)).resolve()
        if not cfg.hold_3mf_enabled or cfg.hold_3mf_days <= 0 or not target.is_dir():
            return []
        held_files: list[Path] = []
        for entry in sorted(target.iterdir(), key=lambda p: p.name.lower()):
            if not entry.is_file() or entry.suffix.lower() != ".3mf":
                continue
            if self._is_3mf_held(entry, cfg):
                decision = evaluate(candidate_from_path(entry), cfg)
                if decision.action == "move":
                    held_files.append(entry)
        return held_files

    def simulate_top_level(self, root: Path | None = None) -> list[tuple[Path, RouteDecision]]:
        """Read-only preview of what ``v3.0`` would do to every top-level file in ``root``."""
        with self._lock:
            cfg = self.config
            target = (root or cfg.resolved_watch_folder(self.paths)).resolve()
        if not target.is_dir():
            return []
        results: list[tuple[Path, RouteDecision]] = []
        learner: TokenRouteLearner | None = None
        for entry in sorted(target.iterdir(), key=lambda p: p.name.lower()):
            if entry.is_file():
                cand = candidate_from_path(entry)
                decision = evaluate(cand, cfg)
                if decision.action == "move" and self._is_3mf_held(entry, cfg):
                    planned = str(decision.relative_dest.parent) if decision.relative_dest else ""
                    decision = RouteDecision(
                        action="leave",
                        relative_dest=decision.relative_dest,
                        reason=f"3MF grace hold ({cfg.hold_3mf_days}d window; planned: {planned})",
                        matched_rule="3MF Hold",
                        match_type="hold",
                        trace=decision.trace,
                    )
                elif decision.match_type == "fallback" and (cfg.ai_smart_hints_enabled or cfg.ai_auto_route_fallback):
                    if learner is None:
                        learner = self.get_route_learner()
                    decision = self._append_ml_hint(cand, decision, learner=learner, cfg=cfg)
                results.append((entry, decision))
        return results

    def scan_top_level_now(
        self,
        settle_seconds: float | None = None,
        *,
        bypass_3mf_hold: bool = False,
        on_progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        """Sort eligible top-level files in ``watch_root`` cleanly in one batch."""
        if on_progress is not None:
            on_progress(0, 0, "Scanning Downloads...")
        with self._lock:
            cfg = self.config
        roots = [r for r in self.watch_roots() if r.is_dir()]
        if cfg.paused or not roots:
            return {
                "batch_id": "",
                "moved": [],
                "ignored": 0,
                "left": 0,
                "held": 0,
                "skipped_busy": 0,
            }

        wait = cfg.settle_seconds if settle_seconds is None else float(settle_seconds)
        candidates = [
            entry
            for root in roots
            for entry in sorted((e for e in root.iterdir() if e.is_file()), key=lambda p: p.name.lower())
        ]
        # An explicit sort is a fresh start: forget what the background loop backed off.
        with self._lock:
            self._busy_backoff.clear()
        return self._process_paths(
            candidates,
            settle_seconds=wait,
            bypass_3mf_hold=bypass_3mf_hold,
            on_progress=on_progress,
        )

    def file_held_3mf_now(self, *, on_progress: ProgressCallback | None = None) -> dict[str, Any]:
        """Immediately sort all top-level ``.3mf`` files currently inside the grace hold."""
        if on_progress is not None:
            on_progress(0, 0, "Checking held .3MF files...")
        held_files = self.list_held_3mf()
        if not held_files:
            return {
                "batch_id": "",
                "moved": [],
                "ignored": 0,
                "left": 0,
                "held": 0,
                "skipped_busy": 0,
            }
        return self._process_paths(
            held_files,
            settle_seconds=0.0,
            bypass_3mf_hold=True,
            on_progress=on_progress,
        )

    def sort_managed_tiers_now(
        self,
        *,
        settle_seconds: float = 0.0,
        on_progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        """Re-sort top-level and managed Tier 1/Tier 2+ folders into their target subfolders."""
        if on_progress is not None:
            on_progress(0, 0, "Scanning managed folders...")
        with self._lock:
            cfg = self.config
            watch_dir = cfg.resolved_watch_folder(self.paths)
        if not watch_dir.is_dir():
            return {"batch_id": "", "moved": []}

        plan = self.preview_architecture_shift(cfg, root=watch_dir)
        planned = plan["moves_planned"]
        total = len(planned)
        batch_id = f"tiers-{uuid.uuid4().hex[:8]}"
        moved: list[dict[str, Any]] = []
        for idx, item in enumerate(planned, start=1):
            if on_progress is not None:
                on_progress(idx - 1, total, Path(item["src"]).name)
            try:
                rec = execute_move(
                    item["src"],
                    item["decision"],
                    batch_id=batch_id,
                    db=self.db,
                    watch_root=watch_dir,
                    settle_seconds=settle_seconds,
                    source_url=str(item.get("source_url") or ""),
                )
            except OSError as exc:
                logging.warning("Tier sort skipped %s: %s", item["src"], exc)
                continue
            if rec is not None:
                moved.append(rec)

        if on_progress is not None and total > 0:
            on_progress(total, total, "Complete")

        if moved:
            self.db.checkpoint()
            if self.on_moves_completed is not None:
                try:
                    self.on_moves_completed(batch_id, moved)
                except Exception:
                    logging.exception("Error in on_moves_completed callback")

        return {"batch_id": batch_id if moved else "", "moved": moved}

    # ------------------------------------------------------------------
    # Native Duplicate & Watch-Folder Deletion Helpers
    # ------------------------------------------------------------------
    def list_duplicate_files(self, root: Path | None = None) -> list[dict[str, Any]]:
        """Return quarantined duplicate files inside ``watch_root / '_Duplicates'``."""
        watch_dir = (root or self.watch_root).resolve()
        return scan_duplicate_files(watch_dir, db=self.db)

    def replace_duplicate_with_new(
        self,
        dup_path: Path | str,
        existing_path: Path | str,
        *,
        root: Path | None = None,
        use_recycle_bin: bool = True,
    ) -> dict[str, Any]:
        """Recycle the older existing file and replace it with the newly downloaded duplicate."""
        watch_dir = (root or self.watch_root).resolve()
        with self._lock:
            result = resolve_duplicate_replace_old(
                Path(dup_path),
                Path(existing_path),
                watch_dir,
                db=self.db,
                use_recycle_bin=use_recycle_bin,
            )
            if result.get("ok"):
                self.db.checkpoint()
                logging.info(
                    "Replaced older copy %s with newer duplicate %s",
                    result.get("existing_path"),
                    Path(dup_path).name,
                )
            return result

    def delete_duplicate_and_locate_old(
        self,
        dup_path: Path | str,
        existing_path: Path | str,
        *,
        root: Path | None = None,
        use_recycle_bin: bool = True,
    ) -> dict[str, Any]:
        """Delete the newly downloaded duplicate and return the verified path to the old copy."""
        watch_dir = (root or self.watch_root).resolve()
        with self._lock:
            result = resolve_duplicate_delete_new_locate_old(
                Path(dup_path),
                Path(existing_path),
                watch_dir,
                db=self.db,
                use_recycle_bin=use_recycle_bin,
            )
            if result.get("ok"):
                self.db.checkpoint()
                logging.info(
                    "Deleted duplicate %s; locating older copy at %s",
                    Path(dup_path).name,
                    result.get("existing_path"),
                )
            return result

    def keep_both_duplicate(
        self,
        dup_path: Path | str,
        existing_path: Path | str,
        *,
        root: Path | None = None,
    ) -> dict[str, Any]:
        """Keep both files by moving the quarantined duplicate next to the older copy."""
        watch_dir = (root or self.watch_root).resolve()
        with self._lock:
            result = resolve_duplicate_keep_both(
                Path(dup_path),
                Path(existing_path),
                watch_dir,
                db=self.db,
            )
            if result.get("ok"):
                self.db.checkpoint()
                logging.info(
                    "Kept both files: %s alongside %s",
                    result.get("final_path"),
                    result.get("existing_path"),
                )
            return result

    def scan_detox_now(self, old_days: int = 30, root: Path | None = None) -> dict[str, Any]:
        """Scan ``watch_root`` for old installers and empty directories."""
        watch_dir = (root or self.watch_root).resolve()
        return scan_detox_candidates(watch_dir, old_installer_days=old_days)

    def delete_detox_installers(
        self,
        paths: list[Path | str],
        *,
        use_recycle_bin: bool = True,
    ) -> dict[str, Any]:
        """Move selected old installers to Recycle Bin (strictly inside ``watch_root``)."""
        watch_dir = self.watch_root.resolve()
        res = delete_files_safely(
            paths,
            watch_root=watch_dir,
            confine_dir=watch_dir,
            use_recycle_bin=use_recycle_bin,
        )
        if res["deleted"]:
            self.db.mark_orphaned_by_dest(res["deleted"])
            self.db.checkpoint()
        return res

    def prune_detox_folders(self, folder_paths: list[str]) -> dict[str, Any]:
        """Remove empty directories inside ``watch_root``."""
        return prune_empty_folders(folder_paths, self.watch_root.resolve())

    def delete_duplicate_files(
        self,
        paths: list[Path | str],
        *,
        use_recycle_bin: bool = True,
        on_progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        """Safely remove selected duplicate files confined strictly inside ``_Duplicates``."""
        watch_dir = self.watch_root.resolve()
        dup_root = watch_dir / "_Duplicates"
        res = delete_files_safely(
            paths,
            watch_root=watch_dir,
            confine_dir=dup_root,
            use_recycle_bin=use_recycle_bin,
            on_progress=on_progress,
        )
        if res["deleted"]:
            self.db.mark_orphaned_by_dest(res["deleted"])
            self.db.checkpoint()
            # Clean up any empty monthly subfolders inside _Duplicates
            if dup_root.is_dir():
                for subdir in sorted(
                    [p for p in dup_root.rglob("*") if p.is_dir()],
                    key=lambda p: len(p.parts),
                    reverse=True,
                ):
                    try:
                        if not any(subdir.iterdir()):
                            subdir.rmdir()
                    except OSError:
                        pass
        return res

    def delete_watch_files(
        self,
        paths: list[Path | str],
        *,
        use_recycle_bin: bool = True,
        on_progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        """Safely delete selected files inside ``watch_root`` (via Recycle Bin on Windows)."""
        watch_dir = self.watch_root.resolve()
        res = delete_files_safely(
            paths,
            watch_root=watch_dir,
            confine_dir=watch_dir,
            use_recycle_bin=use_recycle_bin,
            on_progress=on_progress,
        )
        if res["deleted"]:
            for deleted_str in res["deleted"]:
                self._clear_busy(Path(deleted_str))
            self.db.mark_orphaned_by_dest(res["deleted"])
            self.db.checkpoint()
        return res

    _PROJECT_MARKER_NAMES: frozenset[str] = frozenset(
        {".git", ".gitignore", ".gitattributes", "pyproject.toml", "package.json", "cargo.toml", "cmakelists.txt"}
    )
    _HIST_MANAGED_SUBFOLDERS: frozenset[str] = frozenset(
        {
            r"audio\wav",
            r"audio\mp3",
            r"audio\m4a",
            r"videos\mp4",
            r"videos\mov",
            r"images\vector",
            r"images\camera",
            r"images\photos",
            r"images\renders",
            r"code\web",
            r"code\c and c++",
            r"code\data",
            r"code\logs",
            r"code\python",
            r"code\scripts & firmware",
            r"3d models\fusion 360",
            r"3d models\step",
            r"3d models\g-code",
            r"cad\cam & cnc toolpaths",
            r"documents\school",
            r"documents\career",
            r"documents\receipts & invoices",
        }
    )

    @classmethod
    def _is_project_or_bundle_dir(cls, dir_path: Path) -> bool:
        try:
            with os.scandir(dir_path) as it:
                for entry in it:
                    if entry.name.lower() in cls._PROJECT_MARKER_NAMES:
                        return True
        except OSError:
            return False
        return False

    @classmethod
    def _managed_relative_folders(cls, *configs: Config) -> set[str]:
        """Return all lowercase ``\\\\``-normalized managed folder paths and parent prefixes."""
        rels: set[str] = set()

        def _add_path(raw: str) -> None:
            norm = str(raw or "").replace("/", "\\").strip("\\ ").lower()
            if not norm or norm == "_duplicates":
                return
            parts = [p.strip() for p in norm.split("\\") if p.strip()]
            for i in range(1, len(parts) + 1):
                rels.add("\\".join(parts[:i]))

        for ext, cat in DEFAULT_CATEGORY_MAP.items():
            _add_path(cat)
            sub = DEFAULT_TYPE_SUBFOLDERS.get(ext, "")
            if cat and sub:
                _add_path(f"{cat}\\{sub}")
        for tpl in PREMADE_FOLDER_CATALOG:
            tname = str(tpl.get("name", "")).strip()
            pname = str(tpl.get("parent_name", "")).strip()
            _add_path(f"{pname}\\{tname}" if pname else tname)
        for pkey, groups in TIER2_SUGGESTIONS_BY_PARENT.items():
            _add_path(pkey)
            for items in groups.values():
                for sug in items:
                    sname = str(sug.get("name", "")).strip()
                    if sname:
                        _add_path(f"{pkey}\\{sname}")
        for hist in cls._HIST_MANAGED_SUBFOLDERS:
            _add_path(hist)

        for cfg in configs:
            if cfg.default_folder:
                _add_path(cfg.default_folder)
            for ext, cat in cfg.category_map.items():
                _add_path(cat)
                sub = cfg.type_subfolders.get(ext, "")
                if cat and sub:
                    _add_path(f"{cat}\\{sub}")
            for rule in cfg.rules:
                _add_path(rule.destination)
            for folder in architecture_from_config(cfg).all_target_folders():
                _add_path(folder)

        rels.discard("")
        rels.discard("_duplicates")
        return rels

    @staticmethod
    def _managed_root_names(*configs: Config) -> set[str]:
        roots: set[str] = set()
        for cat in DEFAULT_CATEGORY_MAP.values():
            roots.add(cat.split("\\")[0].strip().lower())
        for cfg in configs:
            if cfg.default_folder:
                roots.add(cfg.default_folder.split("\\")[0].strip().lower())
            for val in cfg.category_map.values():
                top = val.split("\\")[0].strip().lower()
                if top:
                    roots.add(top)
            for rule in cfg.rules:
                top = rule.destination.split("\\")[0].strip().lower()
                if top:
                    roots.add(top)
            if isinstance(cfg.architecture, dict):
                for item in cfg.architecture.get("nodes", []):
                    if isinstance(item, dict) and int(item.get("tier", 1)) == 1:
                        nm = str(item.get("name", "")).strip().lower()
                        if nm:
                            roots.add(nm)
        roots.discard("")
        roots.discard("_duplicates")
        return roots

    def preview_architecture_shift(
        self,
        target_config: Config,
        root: Path | None = None,
        *,
        on_progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        """Compute files to shift (top-level + managed folders) and folders created for ``target_config``."""
        with self._lock:
            cur_cfg = self.config
            watch_dir = (root or target_config.resolved_watch_folder(self.paths)).resolve()

        arch = architecture_from_config(target_config)
        all_folders = arch.all_target_folders()
        planned: list[dict[str, Any]] = []
        folders_needed: list[str] = []

        if not watch_dir.is_dir():
            return {"moves_planned": [], "folders_to_create": [], "architecture_folders": all_folders}

        if on_progress is not None:
            on_progress(0, 0, f"Listing files in {watch_dir.name}...")

        candidates: list[tuple[Path, bool]] = []
        with os.scandir(watch_dir) as it:
            top_entries = sorted(list(it), key=lambda e: e.name.lower())

        # 1. Top-level loose files in Downloads
        for entry in top_entries:
            if entry.is_file():
                candidates.append((Path(entry.path), True))

        # 2. Files currently inside known managed category/rule folders
        managed = self._managed_root_names(cur_cfg, target_config)
        managed_rels = self._managed_relative_folders(cur_cfg, target_config)
        for entry in top_entries:
            if not entry.is_dir() or entry.name.startswith((".", "_")):
                continue
            if entry.name.lower() not in managed:
                continue
            for dirroot, dirnames, filenames in os.walk(entry.path):
                cur_rel = str(Path(dirroot).relative_to(watch_dir)).replace("/", "\\").lower()
                dirnames[:] = sorted(
                    [
                        d
                        for d in dirnames
                        if not d.startswith((".", "_"))
                        and f"{cur_rel}\\{d.lower()}" in managed_rels
                        and not self._is_project_or_bundle_dir(Path(dirroot) / d)
                    ],
                    key=str.lower,
                )
                filenames.sort(key=str.lower)
                root_p = Path(dirroot)
                for fname in filenames:
                    candidates.append((root_p / fname, False))

        total_candidates = len(candidates)
        seen_paths: set[str] = set()
        for idx, (fpath, is_top_level) in enumerate(candidates, start=1):
            if on_progress is not None and (idx == 1 or idx % 500 == 0 or idx == total_candidates):
                on_progress(idx, max(1, total_candidates), fpath.name)

            key_path = os.path.normcase(os.path.abspath(fpath))
            if key_path in seen_paths:
                continue
            seen_paths.add(key_path)

            try:
                cand = candidate_from_path(fpath, lazy_zone=True)
                dec = evaluate(cand, target_config, include_trace=False)
                if dec.action != "move" or dec.relative_dest is None:
                    continue
                if is_top_level and self._is_3mf_held(fpath, target_config):
                    continue

                dest_candidate = watch_dir / dec.relative_dest
                if os.path.normcase(str(dest_candidate)) == key_path:
                    continue

                resolved = fpath.resolve()
                dest_full = dest_candidate.resolve()
                if dest_full == resolved:
                    continue

                src_rel = str(resolved.relative_to(watch_dir))
                dest_rel = str(dec.relative_dest)
            except (OSError, ValueError) as exc:
                logging.warning("Architecture preview skipped %s: %s", fpath, exc)
                continue
            rel_parent = str(dec.relative_dest.parent)
            if rel_parent and rel_parent != "." and rel_parent not in folders_needed:
                folders_needed.append(rel_parent)
            planned.append(
                {
                    "src": resolved,
                    "dest": dest_full,
                    "src_rel": src_rel,
                    "dest_rel": dest_rel,
                    "decision": dec,
                    "source_url": cand.ensure_source_url(),
                }
            )

        if on_progress is not None and total_candidates > 0:
            on_progress(total_candidates, total_candidates, "Scan complete")

        return {
            "moves_planned": planned,
            "folders_to_create": folders_needed,
            "architecture_folders": all_folders,
        }

    def apply_architecture_and_shift(
        self,
        target_config: Config,
        *,
        shift_files: bool = True,
        create_empty_folders: bool = False,
        batch_prefix: str = "arch",
        precomputed_plan: dict[str, Any] | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        """Persist ``target_config`` and shift existing managed/top-level files into their expected folders."""
        if on_progress is not None:
            on_progress(0, 0, "Planning folder shift...")
        with self._lock:
            prev_cfg = copy.deepcopy(self.config)
            watch_dir = target_config.resolved_watch_folder(self.paths)
            managed_before = self._managed_root_names(prev_cfg, target_config)

        plan = (
            precomputed_plan
            if precomputed_plan is not None
            else self.preview_architecture_shift(target_config, root=watch_dir, on_progress=on_progress)
        )
        batch_id = f"{batch_prefix}-{uuid.uuid4().hex[:8]}"
        self._config_undo_stack.append((batch_id, prev_cfg))

        self.update_config(target_config)
        watch_dir.mkdir(parents=True, exist_ok=True)

        folders_created = 0
        if create_empty_folders:
            for rel_folder in plan["architecture_folders"]:
                target_sub = (watch_dir / rel_folder).resolve()
                if not target_sub.exists():
                    target_sub.mkdir(parents=True, exist_ok=True)
                    folders_created += 1

        moved: list[dict[str, Any]] = []
        if shift_files:
            planned = plan["moves_planned"]
            total = len(planned)
            for idx, item in enumerate(planned, start=1):
                if on_progress is not None:
                    on_progress(idx - 1, total, Path(item["src"]).name)
                try:
                    rec = execute_move(
                        item["src"],
                        item["decision"],
                        batch_id=batch_id,
                        db=self.db,
                        watch_root=watch_dir,
                        settle_seconds=0.0,
                        source_url=str(item.get("source_url") or ""),
                    )
                except OSError as exc:
                    logging.warning("Architecture shift skipped %s: %s", item["src"], exc)
                    continue
                if rec is not None:
                    moved.append(rec)
            if on_progress is not None and total > 0:
                on_progress(total, total, "Complete")

            # Remove now-empty old managed directories cleanly (never touch _Duplicates or unmanaged dirs)
            if watch_dir.is_dir():
                for sub_root in list(watch_dir.iterdir()):
                    if not sub_root.is_dir() or sub_root.name.lower() not in managed_before:
                        continue
                    try:
                        subdirs = [p for p in sub_root.rglob("*") if p.is_dir()] + [sub_root]
                    except OSError:
                        continue
                    for dirpath in sorted(subdirs, key=lambda p: len(p.parts), reverse=True):
                        try:
                            if not any(dirpath.iterdir()):
                                dirpath.rmdir()
                        except OSError:
                            pass

        if moved:
            self.db.checkpoint()
        if moved and self.on_moves_completed is not None:
            try:
                self.on_moves_completed(batch_id, moved)
            except Exception:
                logging.exception("Error in on_moves_completed callback during architecture shift")

        return {
            "batch_id": batch_id if moved else "",
            "config_undo_id": batch_id,
            "moved": moved,
            "shifted_count": len(moved),
            "folders_created": folders_created,
        }

    def reset_to_defaults_and_shift(
        self,
        *,
        shift_files: bool = True,
        create_empty_folders: bool = False,
    ) -> dict[str, Any]:
        """Reset architecture & rules to what a new install starts with, and shift files back if requested."""
        with self._lock:
            cur = self.config
            default_cfg = Config.new_install()
            default_cfg.watch_folder = cur.watch_folder
            default_cfg.tutorial_completed = cur.tutorial_completed
            default_cfg.notifications_enabled = cur.notifications_enabled
            default_cfg.check_for_updates = cur.check_for_updates
            default_cfg.show_advanced_tools = cur.show_advanced_tools
            default_cfg.ribbon_timeout_seconds = cur.ribbon_timeout_seconds
            default_cfg.paused = cur.paused
            default_cfg.architecture = architecture_from_config(default_cfg).to_dict()
        return self.apply_architecture_and_shift(
            default_cfg,
            shift_files=shift_files,
            create_empty_folders=create_empty_folders,
            batch_prefix="arch",
        )

    def merge_folders_on_disk(
        self,
        source_rel: str,
        target_rel: str,
        *,
        root: Path | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        """Move all files from ``source_rel`` into ``target_rel`` inside Downloads with Undo support."""
        watch_dir = (root or self.watch_root).resolve()
        src_clean = to_relative_dest(source_rel, fallback="")
        dst_clean = to_relative_dest(target_rel, fallback="")
        if not src_clean or not dst_clean:
            return {"ok": False, "error": "Invalid source or target folder", "moved": [], "moved_count": 0}
        src_dir = (watch_dir / src_clean).resolve()
        dst_dir = (watch_dir / dst_clean).resolve()
        if (
            src_dir == watch_dir
            or not is_under_watch_root(src_dir, watch_dir)
            or not is_under_watch_root(dst_dir, watch_dir)
            or src_dir.as_posix().lower() == dst_dir.as_posix().lower()
        ):
            return {"ok": False, "error": "Invalid folder path inside watch folder", "moved": [], "moved_count": 0}

        if not src_dir.is_dir():
            return {"ok": True, "batch_id": "", "moved": [], "moved_count": 0}

        files = [p for p in sorted(src_dir.rglob("*"), key=lambda x: x.as_posix().lower()) if p.is_file()]
        total = len(files)
        batch_id = f"merge-{uuid.uuid4().hex[:8]}"
        moved: list[dict[str, Any]] = []
        for idx, fp in enumerate(files, start=1):
            if on_progress is not None:
                on_progress(idx - 1, total, fp.name)
            sub_rel = fp.relative_to(src_dir)
            decision = RouteDecision(
                action="move",
                relative_dest=Path(dst_clean) / sub_rel,
                reason=f"Merged folder '{src_clean}' into '{dst_clean}'",
                matched_rule="Folder Merge",
                match_type="merge",
            )
            try:
                rec = execute_move(
                    fp,
                    decision,
                    batch_id=batch_id,
                    db=self.db,
                    watch_root=watch_dir,
                    settle_seconds=0.0,
                )
            except OSError as exc:
                logging.warning("Folder merge skipped %s: %s", fp, exc)
                continue
            if rec is not None:
                moved.append(rec)

        if on_progress is not None and total > 0:
            on_progress(total, total, "Complete")

        for dirpath in sorted(
            [p for p in src_dir.rglob("*") if p.is_dir()] + [src_dir],
            key=lambda p: len(p.parts),
            reverse=True,
        ):
            try:
                if dirpath.is_dir() and not any(dirpath.iterdir()):
                    dirpath.rmdir()
            except OSError:
                pass

        if moved:
            self.db.checkpoint()
            if self.on_moves_completed is not None:
                try:
                    self.on_moves_completed(batch_id, moved)
                except Exception:
                    logging.exception("Error in on_moves_completed callback during folder merge")

        return {
            "ok": True,
            "batch_id": batch_id if moved else "",
            "moved": moved,
            "moved_count": len(moved),
        }

    def inspect_folder_on_disk(
        self,
        rel_path: str,
        *,
        root: Path | None = None,
    ) -> dict[str, Any]:
        """Inspect whether a subfolder exists inside ``watch_root`` and count its files."""
        watch_dir = (root or self.watch_root).resolve()
        clean_rel = to_relative_dest(rel_path, watch_root=watch_dir, fallback="")
        if not clean_rel:
            return {"exists": False, "file_count": 0, "sample_files": [], "rel_path": ""}
        folder_dir = (watch_dir / clean_rel).resolve()
        if (
            folder_dir == watch_dir
            or not is_under_watch_root(folder_dir, watch_dir)
            or not folder_dir.is_dir()
        ):
            return {"exists": False, "file_count": 0, "sample_files": [], "rel_path": clean_rel}
        file_count = 0
        sample_files: list[str] = []
        for _dirpath, dirnames, filenames in os.walk(folder_dir):
            dirnames.sort(key=str.lower)
            file_count += len(filenames)
            if len(sample_files) < 5 and filenames:
                for fn in sorted(filenames, key=str.lower):
                    sample_files.append(fn)
                    if len(sample_files) >= 5:
                        break
        return {
            "exists": True,
            "file_count": file_count,
            "sample_files": sample_files,
            "rel_path": clean_rel,
        }

    def evacuate_or_remove_folder_on_disk(
        self,
        source_rel: str,
        target_rel: str | None = None,
        *,
        remove_empty_dir: bool = True,
        root: Path | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        """Safely move files out of a removed subfolder (never deleting user files) and tidy empty dirs."""
        watch_dir = (root or self.watch_root).resolve()
        src_clean = to_relative_dest(source_rel, watch_root=watch_dir, fallback="")
        if not src_clean:
            return {"ok": False, "error": "Invalid source folder", "moved": [], "moved_count": 0, "removed_dir": False}
        src_dir = (watch_dir / src_clean).resolve()
        if src_dir == watch_dir or not is_under_watch_root(src_dir, watch_dir):
            return {"ok": False, "error": "Source folder must stay inside Downloads", "moved": [], "moved_count": 0, "removed_dir": False}
        if not src_dir.is_dir():
            return {"ok": True, "batch_id": "", "moved": [], "moved_count": 0, "removed_dir": True}

        moved: list[dict[str, Any]] = []
        batch_id = f"evacuate-{uuid.uuid4().hex[:8]}"
        if target_rel is not None:
            raw_target = str(target_rel).strip()
            if raw_target in ("", ".", "\\", "/"):
                dest_prefix = Path(".")
                dest_label = "Downloads"
            else:
                dst_clean = to_relative_dest(raw_target, watch_root=watch_dir, fallback="")
                if not dst_clean:
                    return {"ok": False, "error": "Invalid destination folder", "moved": [], "moved_count": 0, "removed_dir": False}
                dst_dir = (watch_dir / dst_clean).resolve()
                if not is_under_watch_root(dst_dir, watch_dir) or dst_dir.as_posix().lower() == src_dir.as_posix().lower():
                    return {"ok": False, "error": "Invalid destination folder", "moved": [], "moved_count": 0, "removed_dir": False}
                dest_prefix = Path(dst_clean)
                dest_label = f"Downloads\\{dst_clean}"

            files = [p for p in sorted(src_dir.rglob("*"), key=lambda x: x.as_posix().lower()) if p.is_file()]
            total = len(files)
            for idx, fp in enumerate(files, start=1):
                if on_progress is not None:
                    on_progress(idx - 1, total, fp.name)
                sub_rel = fp.relative_to(src_dir)
                rel_dest = sub_rel if str(dest_prefix) == "." else (dest_prefix / sub_rel)
                decision = RouteDecision(
                    action="move",
                    relative_dest=rel_dest,
                    reason=f"Moved from removed folder '{src_clean}' to '{dest_label}'",
                    matched_rule="Folder Removal",
                    match_type="evacuate",
                )
                try:
                    rec = execute_move(
                        fp,
                        decision,
                        batch_id=batch_id,
                        db=self.db,
                        watch_root=watch_dir,
                        settle_seconds=0.0,
                    )
                except OSError as exc:
                    logging.warning("Folder evacuation skipped %s: %s", fp, exc)
                    continue
                if rec is not None:
                    moved.append(rec)
            if on_progress is not None and total > 0:
                on_progress(total, total, "Complete")

        if remove_empty_dir:
            for dirpath in sorted(
                [p for p in src_dir.rglob("*") if p.is_dir()] + [src_dir],
                key=lambda p: len(p.parts),
                reverse=True,
            ):
                try:
                    if dirpath.is_dir() and not any(dirpath.iterdir()):
                        dirpath.rmdir()
                except OSError:
                    pass

        if moved:
            self.db.checkpoint()
            if self.on_moves_completed is not None:
                try:
                    self.on_moves_completed(batch_id, moved)
                except Exception:
                    logging.exception("Error in on_moves_completed callback during folder removal")

        return {
            "ok": True,
            "batch_id": batch_id if moved else "",
            "moved": moved,
            "moved_count": len(moved),
            "removed_dir": not src_dir.exists(),
        }

    def undo_architecture_or_last_batch(
        self,
        *,
        on_progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        """Undo last file batch and also restore previous Folder Architecture config if applicable."""
        if on_progress is not None:
            on_progress(0, 0, "Preparing undo...")
        res = undo_batch(self.db, on_progress=on_progress)
        config_restored = False
        if self._config_undo_stack:
            top_id, prev_cfg = self._config_undo_stack[-1]
            if res.get("batch_id") == top_id or res.get("restored", 0) == 0:
                self._config_undo_stack.pop()
                self.update_config(prev_cfg)
                config_restored = True
        res["config_restored"] = config_restored
        return res

    def undo_last_batch(self, *, on_progress: ProgressCallback | None = None) -> dict[str, Any]:
        res = undo_batch(self.db, on_progress=on_progress)
        config_restored = False
        if self._config_undo_stack and res.get("batch_id") == self._config_undo_stack[-1][0]:
            _, prev_cfg = self._config_undo_stack.pop()
            self.update_config(prev_cfg)
            config_restored = True
        res["config_restored"] = config_restored
        return res

    def undo_single_move(
        self,
        move_id: int,
        *,
        on_progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        if on_progress is not None:
            on_progress(0, 1, "Restoring file...")
        return undo_move(self.db, move_id, on_progress=on_progress)

    def start(self) -> None:
        if self._worker_thread is not None and self._worker_thread.is_alive():
            return
        self._stop_event.clear()
        # Settle moves a crash or kill interrupted, before anything new is sorted.
        try:
            self.db.reconcile_incomplete_moves()
        except Exception:
            logging.exception("Could not reconcile interrupted moves")
        for root in self.watch_roots():
            try:
                cleanup_orphaned_staging(root)
            except Exception:
                logging.exception("Could not clean up orphaned staging directories in %s", root)
        try:
            self.db.prune_missing_hashes()
        except Exception:
            logging.debug("Could not prune missing file hashes", exc_info=True)
        try:
            from download_sorter.chrome_bridge import chrome_manifest_path, install_chrome_native_host

            if chrome_manifest_path(self.paths.data_dir).is_file():
                install_chrome_native_host(self.paths.data_dir)
        except Exception:
            logging.debug("Chrome host auto-heal skipped", exc_info=True)
        self._start_observer()
        if self.config.quick_switcher_enabled:
            self._start_quick_switcher()
        self._worker_thread = threading.Thread(target=self._run_loop, name="sorter-v3-worker", daemon=True)
        self._worker_thread.start()
        logging.info(
            "Download Sorter v3 service started (watching top-level %s)",
            ", ".join(str(r) for r in self.watch_roots()),
        )

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_event.set()
        self._wake_event.set()
        self._stop_quick_switcher()
        with self._lock:
            old_observer = self._observer
            self._observer = None
        if old_observer is not None:
            self._stop_observer(old_observer, timeout=timeout)
        if self._worker_thread is not None:
            self._worker_thread.join(timeout=timeout)
            self._worker_thread = None
        # Empty the write-ahead log so state.db is self-contained for backups/updates.
        self.db.close()
        logging.info("Download Sorter v3 service stopped")

    def _start_quick_switcher(self) -> None:
        if self.quick_switcher_monitor is not None and self.quick_switcher_monitor.is_alive():
            return
        try:
            from download_sorter.quick_switcher import QuickSwitcherMonitor

            def _on_detected(hwnd: int, data: dict[str, Any]) -> None:
                if self.on_dialog_detected is not None:
                    try:
                        self.on_dialog_detected(hwnd, data)
                    except Exception as exc:
                        logging.debug("Error in on_dialog_detected: %s", exc)

            self.quick_switcher_monitor = QuickSwitcherMonitor(
                on_dialog_detected=_on_detected,
                data_dir=self.paths.data_dir,
            )
            self.quick_switcher_monitor.start()
        except Exception as exc:
            logging.debug("Could not start QuickSwitcherMonitor: %s", exc)

    def _stop_quick_switcher(self) -> None:
        if self.quick_switcher_monitor is not None:
            try:
                self.quick_switcher_monitor.stop()
            except Exception:
                pass
            self.quick_switcher_monitor = None

    def _enqueue(self, path: Path) -> None:
        with self._lock:
            self._pending.add(path)
            entry = self._busy_backoff.get(path)
            if entry is not None:
                # The file just changed (a download still being written, or the
                # program that had it open saved and closed it): look again one
                # settle period from now instead of waiting out the long pause.
                attempts, next_retry, message = entry
                soon = self._clock() + self._busy_base_seconds(self.config.settle_seconds)
                if soon < next_retry:
                    self._busy_backoff[path] = (attempts, soon, message)
        self._wake_event.set()

    # ------------------------------------------------------------------
    # Busy-file backoff (background loop only)
    # ------------------------------------------------------------------
    @staticmethod
    def _busy_base_seconds(settle_seconds: float) -> float:
        return max(_BUSY_RETRY_MIN_BASE_SECONDS, min(float(settle_seconds), _BUSY_RETRY_MAX_BASE_SECONDS))

    def _note_busy(self, path: Path, settle_seconds: float, message: str = "") -> bool:
        """Record one more failed attempt for ``path`` and schedule its next retry.

        Returns True when this failure is news (first one, or a different error),
        which is the only time it is worth a WARNING in the log.
        """
        with self._lock:
            attempts, _next, last_message = self._busy_backoff.get(path, (0, 0.0, ""))
            attempts += 1
            delay = min(
                self._busy_base_seconds(settle_seconds) * (2.0 ** min(attempts, 16)),
                BUSY_RETRY_MAX_SECONDS,
            )
            self._busy_backoff[path] = (attempts, self._clock() + delay, message)
            self._pending.add(path)
            return attempts == 1 or message != last_message

    def _clear_busy(self, path: Path) -> None:
        with self._lock:
            self._busy_backoff.pop(path, None)

    def _take_due_paths(self) -> list[Path]:
        """Pop the queued paths whose retry time has come; backed-off ones stay queued."""
        with self._lock:
            now = self._clock()
            due = [p for p in self._pending if self._busy_backoff.get(p, (0, 0.0, ""))[1] <= now]
            self._pending.difference_update(due)
            return due

    def _next_wait_seconds(self) -> float:
        """How long the background loop may sleep before something could be ready."""
        with self._lock:
            if not self._pending:
                return 30.0
            settle = max(0.5, min(self.config.settle_seconds, 5.0))
            now = self._clock()
            waits = [
                max(0.05, self._busy_backoff[p][1] - now) if p in self._busy_backoff else settle
                for p in self._pending
            ]
            return min(30.0, min(waits))

    def _start_observer(self) -> None:
        roots = []
        for root in self.watch_roots():
            if root.is_dir():
                roots.append(root)
            else:
                logging.warning("Watched folder does not exist yet: %s", root)
        if not roots:
            return
        observer = Observer()
        for root in roots:
            observer.schedule(_TopLevelEventHandler(root, self._enqueue), str(root), recursive=False)
        observer.start()
        with self._lock:
            displaced = self._observer
            self._observer = observer
        if displaced is not None:
            # Two restarts raced; keep the newest watcher and retire the other.
            self._stop_observer(displaced)

    @staticmethod
    def _stop_observer(observer: Observer, timeout: float = 3.0) -> None:
        """Stop and join a detached observer. Never call this while holding ``_lock``."""
        try:
            observer.stop()
            observer.join(timeout=timeout)
        except Exception as exc:
            logging.debug("Error stopping watchdog observer: %s", exc)

    def _restart_observer(self) -> None:
        with self._lock:
            old_observer = self._observer
            self._observer = None
        if old_observer is not None:
            self._stop_observer(old_observer)
        self._start_observer()

    def _first_run_pending(self) -> bool:
        """True until the welcome steps have been finished (or skipped) once.

        Nothing is sorted automatically before that: a new install must not empty
        the Downloads folder the moment it starts. "Clean Up Now" and the command
        line still work, because those are things the user asked for.
        """
        with self._lock:
            return not self.config.tutorial_completed

    def _run_loop(self) -> None:
        swept_existing = False
        while not self._stop_event.is_set():
            if not swept_existing and not self._first_run_pending():
                # Process existing top-level files once: at startup, or as soon as
                # the welcome steps are done on a first run.
                swept_existing = True
                self.scan_top_level_now()
            self._wake_event.wait(timeout=self._next_wait_seconds())
            self._wake_event.clear()
            if self._stop_event.is_set():
                break
            if self.paused or self._first_run_pending():
                continue
            queued = self._take_due_paths()
            with self._lock:
                wait = self.config.settle_seconds
            if queued:
                self._process_paths(queued, settle_seconds=wait, requeue_unsettled=True)

    def _process_paths(
        self,
        paths_list: list[Path],
        *,
        settle_seconds: float,
        requeue_unsettled: bool = False,
        bypass_3mf_hold: bool = False,
        on_progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            cfg = self.config
        roots = self.watch_roots()
        primary = roots[0]
        # In an extra folder (a Desktop, say) only files a rule or file type matches move:
        # shortcuts and other unknown files stay instead of going to "Other".
        extra_cfg = dataclasses.replace(cfg, fallback_mode="leave") if len(roots) > 1 else cfg
        batch_id = f"b-{uuid.uuid4().hex[:8]}"
        moved: list[dict[str, Any]] = []
        ignored = 0
        left = 0
        held = 0
        skipped_busy = 0
        ai_learner: TokenRouteLearner | None = None

        total = len(paths_list)
        for index, path in enumerate(paths_list, start=1):
            if on_progress is not None:
                on_progress(index - 1, total, path.name)
            if not path.exists() or not path.is_file():
                self._clear_busy(path)  # deleted or moved by the user: nothing to retry
                continue
            try:
                root = path.resolve().parent
            except OSError:
                self._clear_busy(path)
                continue
            if root not in roots:
                self._clear_busy(path)
                continue

            cand = candidate_from_path(path)
            decision = evaluate(cand, cfg if root == primary else extra_cfg, include_trace=False)
            if decision.match_type == "fallback" and root == primary and cfg.ai_auto_route_fallback:
                if ai_learner is None:
                    ai_learner = self.get_route_learner()
                decision = self._append_ml_hint(cand, decision, learner=ai_learner, cfg=cfg)
            if decision.action == "ignore":
                ignored += 1
                continue
            if decision.action == "leave":
                left += 1
                continue
            if not bypass_3mf_hold and self._is_3mf_held(path, cfg):
                held += 1
                continue

            try:
                rec = execute_move(
                    path,
                    decision,
                    batch_id=batch_id,
                    db=self.db,
                    watch_root=root,
                    settle_seconds=settle_seconds,
                    source_url=cand.source_url,
                )
            except OSError as exc:
                skipped_busy += 1
                if requeue_unsettled:
                    # Retried with a growing pause; only the first failure (or a new
                    # kind of failure) is worth a line in the log.
                    if self._note_busy(path, settle_seconds, str(exc)):
                        logging.warning(
                            "Could not move %s: %s (will keep retrying, less often)", path.name, exc
                        )
                    else:
                        logging.debug("Still could not move %s: %s", path.name, exc)
                else:
                    logging.warning("Could not move %s: %s", path.name, exc)
                continue

            if rec is None:
                skipped_busy += 1
                if requeue_unsettled:
                    self._note_busy(path, settle_seconds)
            else:
                self._clear_busy(path)
                moved.append(rec)
                dest_file = Path(rec["dest_path"])
                if dest_file.suffix.lower() == ".zip":
                    matched_rule = (
                        next(
                            (r for r in cfg.rules if r.enabled and r.name == decision.matched_rule and r.auto_unzip),
                            None,
                        )
                        if decision.match_type == "rule"
                        else None
                    )
                    if cfg.auto_unzip_all or matched_rule is not None:
                        extract_zip_safely(
                            dest_file,
                            dest_file.parent,
                            root,
                            db=self.db,
                            batch_id=batch_id,
                            keep_original=bool(cfg.zip_keep_original),
                        )
                if self.config.quick_switcher_enabled:
                    try:
                        from download_sorter.quick_switcher import record_recent_file

                        record_recent_file(
                            rec["dest_path"],
                            downloads_root=root,
                            batch_id=batch_id,
                            settings={
                                "timeout_seconds": self.config.quick_switcher_timeout_seconds,
                                "3d_timeout_hours": self.config.quick_switcher_3d_timeout_hours,
                            },
                            data_dir=self.paths.data_dir,
                        )
                    except Exception as exc:
                        logging.debug("Error recording recent file in service: %s", exc)

        if on_progress is not None and total > 0:
            on_progress(total, total, "Complete")

        if moved:
            self.db.checkpoint()
        if moved and self.on_moves_completed is not None:
            try:
                self.on_moves_completed(batch_id, moved)
            except Exception:
                logging.exception("Error in on_moves_completed callback")

        return {
            "batch_id": batch_id if moved else "",
            "moved": moved,
            "ignored": ignored,
            "left": left,
            "held": held,
            "skipped_busy": skipped_busy,
        }
