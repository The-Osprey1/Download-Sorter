"""Watch Downloads and Documents folders and sort incoming files continuously."""

from __future__ import annotations

from collections.abc import Callable
import json
import logging
from pathlib import Path
import sys
import threading
import time
import uuid

import sorter

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from sorter import (
    ACTIVE_PROJECT_FILE,
    CONFIG_PATH,
    DEFAULT_SETTLE_SECONDS,
    DEFAULT_CONFIG_PATH,
    PAUSE_FILE,
    RoutingContext,
    compile_routing,
    get_default_config,
    document_roots,
    existing_files_cutoff,
    is_incomplete,
    is_internal_path,
    is_keep_protected,
    is_restored_by_undo,
    is_file_ready_to_move,
    is_cloud_placeholder,
    is_ignored_directory,
    is_paused,
    is_quarantined,
    is_under,
    load_config,
    load_active_project,
    move_file,
    reclassify_misplaced_file,
    relocate_from_documents,
    setup_logging,
    sort_existing,
    sort_existing_documents,
    sort_existing_misplaced,
    watch_root,
    write_runtime_state,
)

PENDING_DOWNLOADS: set[Path] = set()
PENDING_DOCUMENTS: set[Path] = set()
PENDING_MISPLACED: set[Path] = set()
QUEUE_LOCK = threading.Lock()
PENDING_DEADLINES: dict[tuple[str, Path], float] = {}
WAKE_EVENT = threading.Event()
# Set while run(wait_for_setup=True) is holding off until the welcome wizard is done.
SETUP_PENDING = threading.Event()
# Set while the folder to watch is not there (an unplugged drive, a deleted or misspelled folder).
WATCH_FOLDER_MISSING = threading.Event()


def _settle_seconds(config: dict) -> float:
    try:
        return max(0.0, float(config.get("watch", {}).get("settle_seconds", DEFAULT_SETTLE_SECONDS)))
    except (TypeError, ValueError):
        return DEFAULT_SETTLE_SECONDS


def _enqueue(kind: str, bucket: set[Path], path: Path, config: dict) -> None:
    """Debounce repeated filesystem events and wake the coordinator."""
    with QUEUE_LOCK:
        bucket.add(path)
        PENDING_DEADLINES[(kind, path)] = time.monotonic() + _settle_seconds(config)
    WAKE_EVENT.set()


def _take_due(kind: str, bucket: set[Path]) -> list[Path]:
    now = time.monotonic()
    with QUEUE_LOCK:
        due = [path for path in bucket if PENDING_DEADLINES.get((kind, path), 0.0) <= now]
        for path in due:
            bucket.discard(path)
            PENDING_DEADLINES.pop((kind, path), None)
    return due


def _retry(kind: str, bucket: set[Path], path: Path, delay: float = 0.5) -> None:
    with QUEUE_LOCK:
        bucket.add(path)
        PENDING_DEADLINES[(kind, path)] = time.monotonic() + delay
    WAKE_EVENT.set()


def _next_pending_delay() -> float | None:
    with QUEUE_LOCK:
        if not PENDING_DOWNLOADS and not PENDING_DOCUMENTS and not PENDING_MISPLACED:
            return None
        deadlines = [
            deadline
            for (kind, path), deadline in PENDING_DEADLINES.items()
            if (kind == "download" and path in PENDING_DOWNLOADS)
            or (kind == "document" and path in PENDING_DOCUMENTS)
            or (kind == "misplaced" and path in PENDING_MISPLACED)
        ]
    if not deadlines:
        return 0.0
    return max(0.0, min(deadlines) - time.monotonic())


def acquire_single_instance_lock(name: str = "DownloadSorter_SingleInstance") -> object:
    if sys.platform == "win32":
        import ctypes
        mutex = ctypes.windll.kernel32.CreateMutexW(None, False, f"Local\\{name}")
        last_error = ctypes.windll.kernel32.GetLastError()
        if last_error == 183:  # ERROR_ALREADY_EXISTS
            return None
        return mutex
    return True


def is_instance_running(name: str = "DownloadSorter_SingleInstance") -> bool:
    """Check if another instance of Download Sorter is currently holding the single-instance mutex."""
    if sys.platform == "win32":
        import ctypes
        # SYNCHRONIZE = 0x00100000
        h = ctypes.windll.kernel32.OpenMutexW(0x00100000, False, f"Local\\{name}")
        if h:
            ctypes.windll.kernel32.CloseHandle(h)
            return True
        return False
    return False


class DownloadsHandler(FileSystemEventHandler):
    """Monitors Downloads recursively for top-level downloads and files misplaced in
    the sorter's own folders. Folders the user made are never sorted."""
    def __init__(self, root: Path, config: dict, routing_context: RoutingContext | None = None) -> None:
        self._lock = threading.Lock()
        self.root = root.resolve()
        self.config = config
        self.routing_context = routing_context or compile_routing(config)
        self.category_folders = set(self.routing_context.category_folders)

    def update_config(self, config: dict, routing_context: RoutingContext | None = None) -> None:
        with self._lock:
            self.config = config
            self.routing_context = routing_context or compile_routing(config)
            self.category_folders = set(self.routing_context.category_folders)

    def _queue(self, src: str) -> None:
        path = Path(src)
        if path.exists() and path.is_dir():
            return

        with self._lock:
            cfg = self.config
            categories = self.category_folders
            routing_context = self.routing_context

        if is_incomplete(path, cfg, routing_context):
            return

        try:
            rel = path.resolve().relative_to(self.root)
        except (ValueError, OSError):
            return

        # Download Sorter's own working folders (_Duplicates, remodel quarantine, batch staging)
        if is_internal_path(path, self.root):
            return

        # Case 1: File is sitting directly at the top level of Downloads
        if len(rel.parts) == 1:
            _enqueue("download", PENDING_DOWNLOADS, path, cfg)
            return

        # Case 2: File is sitting directly inside one of the sorter's own folders:
        # a main folder (Documents), a type folder (Documents\PDF) or a rule's folder.
        parent_rel = str(path.parent.resolve().relative_to(self.root)).replace("/", "\\")
        if parent_rel.lower() in categories:
            # Classification is deliberately deferred until the file is stable;
            # modified events from large G-code files can otherwise reread it
            # many times while the producing application is still writing.
            _enqueue("misplaced", PENDING_MISPLACED, path, cfg)
            return

        # Anything else is in a folder the user made. Files there are theirs:
        # nothing is taken out of a folder you made.

    def on_created(self, event) -> None:  # type: ignore[no-untyped-def]
        if not event.is_directory:
            self._queue(event.src_path)

    def on_moved(self, event) -> None:  # type: ignore[no-untyped-def]
        if event.is_directory:
            return
        if self._handle_user_move(event.src_path, event.dest_path):
            return
        self._queue(event.dest_path)

    def _handle_user_move(self, src: str, dest: str) -> bool:
        """A file the user moved between folders stays where they put it.

        Without this, a file dragged into a folder like Downloads\\Banking was
        treated as a misplaced download and moved straight back out. When learning
        is switched on, the move is also an example of where such files belong.
        """
        with self._lock:
            cfg = self.config
        try:
            import learning
            if not learning.is_user_move(src, dest, self.root, cfg):
                return False
            sorter.remember_user_placed(Path(dest))
            taken_back = None
            try:
                # Dragged out of the folder it was filed into: a "no" for a learned placement.
                import sort_undo
                filed = sort_undo.find_active_move(src)
                if filed and filed.get("why") in ("learned", "keyword"):
                    taken_back = learning.observe_correction(filed, cfg, self.root)
            except Exception as err:
                logging.debug("Could not note a correction: %s", err)
            record = learning.observe_user_move(Path(dest), self.root, cfg)
            suggestion = (record or {}).get("suggestion") or (taken_back or {}).get("suggestion")
            if suggestion and cfg.get("ribbon_enabled", True) and cfg.get("notifications", True):
                # A ribbon that can't show must never undo the user's move.
                try:
                    from ribbon_popup import notify_suggestion
                    notify_suggestion(suggestion["id"])
                except Exception as err:
                    logging.debug("Rule suggestion ribbon skipped: %s", err)
        except Exception:
            logging.exception("Could not handle the move of %s", dest)
            return False
        return True

    def on_modified(self, event) -> None:  # type: ignore[no-untyped-def]
        if not event.is_directory:
            self._queue(event.src_path)


class DocumentsHandler(FileSystemEventHandler):
    def __init__(
        self,
        sources: list[Path],
        downloads: Path,
        config: dict,
        routing_context: RoutingContext | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self.sources = sources
        self.downloads = downloads
        self.config = config
        self.routing_context = routing_context or compile_routing(config)

    def update_config(self, config: dict, routing_context: RoutingContext | None = None) -> None:
        with self._lock:
            self.config = config
            self.routing_context = routing_context or compile_routing(config)

    def _queue(self, src: str) -> None:
        path = Path(src)
        if path.exists() and path.is_dir():
            return
        with self._lock:
            cfg = self.config
            routing_context = self.routing_context
        resolved = path.resolve() if path.exists() else path
        if is_under(resolved, self.downloads):
            return
        if not any(is_under(resolved, source) for source in self.sources):
            return
        if is_incomplete(path, cfg, routing_context):
            return
        if is_ignored_directory(path.parent, cfg, routing_context):
            return
        relocate_exts = routing_context.document_relocate_extensions
        if relocate_exts and path.suffix.lower() not in relocate_exts:
            return
        _enqueue("document", PENDING_DOCUMENTS, path, cfg)

    def on_created(self, event) -> None:  # type: ignore[no-untyped-def]
        if not event.is_directory:
            self._queue(event.src_path)

    def on_moved(self, event) -> None:  # type: ignore[no-untyped-def]
        if not event.is_directory:
            self._queue(event.dest_path)

    def on_modified(self, event) -> None:  # type: ignore[no-untyped-def]
        if not event.is_directory:
            self._queue(event.src_path)


_BAMBU_PROMPTED_SESSION = False


def _handle_post_move(
    dest: Path,
    target_root: Path,
    config: dict,
    batch_id: str | None = None,
    source: Path | None = None,
) -> None:
    """Notify user via ribbon popup and record recent file for quick switcher, gated by config."""
    global _BAMBU_PROMPTED_SESSION
    if dest.suffix.lower() in (".3mf", ".gcode", ".bgcode"):
        try:
            import prints
            from gcode_peek import parse_3mf, parse_gcode
            meta = parse_3mf(dest) if dest.suffix.lower() == ".3mf" else parse_gcode(dest)
            prints.record_print(dest, meta)
        except Exception as err:
            logging.debug("Print details note skipped: %s", err)
    if source is not None:
        if dest.suffix.lower() == ".3mf":
            try:
                import bambu_recent
                if bambu_recent.is_enabled(config):
                    bambu_recent.record_move(source, dest, config=config)
                elif (
                    not _BAMBU_PROMPTED_SESSION
                    and bambu_recent.should_ask(config)
                    and config.get("ribbon_enabled", True)
                    and config.get("notifications", True)
                ):
                    _BAMBU_PROMPTED_SESSION = True
                    from ribbon_popup import notify_bambu_prompt
                    notify_bambu_prompt(source, dest)
            except Exception as err:
                logging.debug("Bambu recent-file note skipped: %s", err)
        if dest.suffix.lower() == ".3mf":
            try:
                import kits
                from gcode_peek import parse_3mf
                meta = parse_3mf(dest)
                if meta.get("is_makerworld"):
                    kits.remember_model(meta.get("makerworld_meta", {}).get("model_id"), dest.parent.name)
            except Exception as err:
                logging.debug("Kit index note skipped: %s", err)
    if config.get("ribbon_enabled", True) and config.get("notifications", True):
        try:
            from ribbon_popup import notify_file_moved
            notify_file_moved(dest, target_root, batch_id=batch_id)
        except Exception as err:
            logging.debug("Ribbon notification skipped: %s", err)
    try:
        import learning
        learning.observe_sorted(dest, target_root, config)
    except Exception as err:
        logging.debug("Learning log skipped: %s", err)
    try:
        import learning
        suggestion = learning.fallback_suggestion(dest, target_root, config)
        if suggestion and config.get("ribbon_enabled", True) and config.get("notifications", True):
            from ribbon_popup import notify_suggestion
            notify_suggestion(suggestion["id"])
    except Exception as err:
        logging.debug("Fallback suggestion skipped: %s", err)
    if config.get("quick_switcher_enabled", True):
        try:
            from quick_switcher import record_recent_file
            if batch_id is not None:
                record_recent_file(dest, target_root, batch_id=batch_id)
            else:
                record_recent_file(dest, target_root)
        except Exception as err:
            logging.debug("Quick switcher record skipped: %s", err)


def drain(
    config: dict,
    root: Path | None = None,
    routing_context: RoutingContext | None = None,
    on_activity: Callable[[int], None] | None = None,
) -> int:
    if is_paused():
        return 0
    # The user's own folders are read once for the whole batch.
    with sorter.reuse_my_folders():
        return _drain(config, root, routing_context, on_activity)


def _drain(
    config: dict,
    root: Path | None,
    routing_context: RoutingContext | None,
    on_activity: Callable[[int], None] | None,
) -> int:
    moved_count = 0
    target_root = root or watch_root(config)

    routing_context = routing_context or compile_routing(config, load_active_project())
    batch_downloads = _take_due("download", PENDING_DOWNLOADS)

    if batch_downloads:
        batch_id = uuid.uuid4().hex[:8]
        for path in batch_downloads:
            try:
                dest = move_file(
                    path,
                    config,
                    batch_id=batch_id,
                    routing_context=routing_context,
                )
                if dest is not None:
                    moved_count += 1
                    _handle_post_move(dest, target_root, config, batch_id=batch_id, source=path)
                elif (
                    path.exists()
                    and path.is_file()
                    and not is_quarantined(path)
                    and not is_cloud_placeholder(path)
                    and not is_incomplete(path, config, routing_context)
                    and not is_keep_protected(path)
                    and not is_restored_by_undo(path)
                ):
                    import hold_manager
                    if hold_manager.is_held(path):
                        if on_activity is not None:
                            try:
                                on_activity(0)
                            except Exception:
                                logging.debug("Activity callback for held file failed")
                    else:
                        _retry("download", PENDING_DOWNLOADS, path)
            except Exception:
                logging.exception("Failed sorting %s", path)

    batch_documents = _take_due("document", PENDING_DOCUMENTS)

    if batch_documents:
        batch_id = uuid.uuid4().hex[:8]
        for path in batch_documents:
            try:
                settle = _settle_seconds(config)
                if not is_file_ready_to_move(path, settle):
                    if path.exists() and not is_quarantined(path) and not is_cloud_placeholder(path):
                        _retry("document", PENDING_DOCUMENTS, path)
                    continue
                dest = relocate_from_documents(
                    path,
                    config,
                    batch_id=batch_id,
                    routing_context=routing_context,
                )
                if dest is not None:
                    moved_count += 1
                    _handle_post_move(dest, target_root, config, batch_id=batch_id, source=path)
            except Exception:
                logging.exception("Failed relocating %s", path)

    batch_misplaced = _take_due("misplaced", PENDING_MISPLACED)

    if batch_misplaced:
        batch_id = uuid.uuid4().hex[:8]
        for path in batch_misplaced:
            try:
                settle = _settle_seconds(config)
                if not is_file_ready_to_move(path, settle):
                    if path.exists() and not is_quarantined(path) and not is_cloud_placeholder(path):
                        _retry("misplaced", PENDING_MISPLACED, path)
                    continue
                dest = reclassify_misplaced_file(
                    path,
                    config,
                    batch_id=batch_id,
                    routing_context=routing_context,
                )
                if dest is not None:
                    moved_count += 1
                    _handle_post_move(dest, target_root, config, batch_id=batch_id, source=path)
            except Exception:
                logging.exception("Failed auto-correcting misplaced file for %s", path)

    moved_count += process_hold_releases(config, target_root)

    return moved_count


def process_hold_releases(config: dict, target_root: Path) -> int:
    """File every held .3mf whose grace period is over.

    A hold is only dropped after its file has actually been moved.  If the move is
    refused or fails, the hold stays and is retried later (see
    ``hold_manager.release_failed``), so a file is never left unsorted with no
    record of why.
    """
    moved = 0
    try:
        import hold_manager
        import safe_ops
        releases = hold_manager.check_releases(config)
    except Exception as exc:
        logging.debug("Hold manager check releases skipped: %s", exc)
        return 0
    for src, dest in releases:
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            final = safe_ops.move(src, dest, batch_id="hold_release")
        except Exception as exc:
            hold_manager.release_failed(src, exc)
            logging.warning("Could not file held %s yet (%s); will retry", src.name, exc)
            continue
        hold_manager.remove_held(src)
        moved += 1
        try:
            _handle_post_move(final, target_root, config, batch_id="hold_release", source=src)
        except Exception:
            logging.exception("Post-move handling failed for %s", final)
    return moved


class ConfigReloader:
    """Monitors config.json and active_project.json for filesystem modification (st_mtime).
    Enables zero-restart live reload in the background daemon loop with atomic resilience.
    """
    def __init__(
        self,
        config_path: Path | None = None,
        active_project_path: Path | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self.config_path = (config_path or CONFIG_PATH).resolve()
        self.active_project_path = (active_project_path or ACTIVE_PROJECT_FILE).resolve()
        self.config_mtime = self._get_mtime(self.config_path)
        self.active_project_mtime = self._get_mtime(self.active_project_path)
        self._config_dirty = False
        self._active_project_dirty = False
        if self.config_path.exists():
            try:
                data = json.loads(self.config_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    self._config = data
                else:
                    logging.warning("Config at %s is not a dictionary; using default", self.config_path)
                    self._config = get_default_config()
            except Exception as exc:
                logging.warning("Failed parsing config at %s (%s); using default", self.config_path, exc)
                self._config = get_default_config()
        else:
            self._config = load_config()
        self._active_project = load_active_project(self.active_project_path)
        self._routing_context = compile_routing(self._config, self._active_project)

    @property
    def config(self) -> dict:
        with self._lock:
            return self._config

    @config.setter
    def config(self, val: dict) -> None:
        with self._lock:
            self._config = val
            self._routing_context = compile_routing(val, self._active_project)

    @property
    def routing_context(self) -> RoutingContext:
        with self._lock:
            return self._routing_context

    @property
    def dirty(self) -> bool:
        with self._lock:
            return self._config_dirty or self._active_project_dirty

    def mark_dirty(self, path: Path) -> bool:
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        with self._lock:
            if resolved == self.config_path:
                self._config_dirty = True
                return True
            if resolved == self.active_project_path:
                self._active_project_dirty = True
                return True
            return resolved == PAUSE_FILE.resolve()

    @staticmethod
    def _get_mtime(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    def check_for_updates(self) -> tuple[bool, dict]:
        """Returns (has_changed, current_config).
        If config.json changed, safely parses and validates JSON before swapping.
        If active_project.json changed, flags update as well.
        """
        with self._lock:
            changed = False
            curr_cfg_mtime = self._get_mtime(self.config_path)
            if self._config_dirty or curr_cfg_mtime != self.config_mtime:
                try:
                    if not self.config_path.exists():
                        self.config_mtime = 0.0
                        self._config_dirty = False
                        logging.warning("Live reload: config.json is missing; keeping previous config")
                        return False, self._config
                    text = self.config_path.read_text(encoding="utf-8")
                    new_data = json.loads(text)
                    if isinstance(new_data, dict):
                        self._config = new_data
                        self.config_mtime = curr_cfg_mtime
                        changed = True
                        logging.info("Live reload: reloaded config.json in-place (mtime: %s)", curr_cfg_mtime)
                except Exception as exc:
                    self.config_mtime = curr_cfg_mtime
                    logging.warning("Live reload: failed reading config.json (keeping previous config): %s", exc)
                finally:
                    self._config_dirty = False

            curr_ap_mtime = self._get_mtime(self.active_project_path)
            active_expired = False
            if self._active_project:
                try:
                    expiry = float(self._active_project.get("expires_at", 0))
                    active_expired = expiry > 0 and time.time() >= expiry
                except (TypeError, ValueError):
                    active_expired = True
            if self._active_project_dirty or curr_ap_mtime != self.active_project_mtime or active_expired:
                self.active_project_mtime = curr_ap_mtime
                self._active_project = load_active_project(self.active_project_path)
                self._active_project_dirty = False
                changed = True
                logging.info("Live reload: active project state modified (mtime: %s)", curr_ap_mtime)

            if changed:
                self._routing_context = compile_routing(self._config, self._active_project)
            return changed, self._config


class ControlFileHandler(FileSystemEventHandler):
    """Wake the daemon only when config, project, or pause state changes."""

    def __init__(self, reloader: ConfigReloader) -> None:
        self.reloader = reloader

    def _changed(self, *paths: str) -> None:
        relevant = False
        for value in paths:
            if value:
                relevant = self.reloader.mark_dirty(Path(value)) or relevant
        if relevant:
            WAKE_EVENT.set()

    def on_created(self, event) -> None:  # type: ignore[no-untyped-def]
        if not event.is_directory:
            self._changed(event.src_path)

    def on_modified(self, event) -> None:  # type: ignore[no-untyped-def]
        if not event.is_directory:
            self._changed(event.src_path)

    def on_deleted(self, event) -> None:  # type: ignore[no-untyped-def]
        if not event.is_directory:
            self._changed(event.src_path)

    def on_moved(self, event) -> None:  # type: ignore[no-untyped-def]
        if not event.is_directory:
            self._changed(event.src_path, event.dest_path)


class WatchCoordinator:
    """Event-driven wait policy with a periodic missed-event safety check."""

    def __init__(self, fallback_seconds: float = 30.0) -> None:
        self.fallback_seconds = fallback_seconds
        self._next_fallback = time.monotonic() + fallback_seconds
        self.wake_count = 0

    def wake(self) -> None:
        WAKE_EVENT.set()

    def wait(self, ignore_pending: bool = False) -> bool:
        """Wait for work; return True when the fallback deadline elapsed."""
        now = time.monotonic()
        pending_delay = None if ignore_pending else _next_pending_delay()
        timeout = max(0.0, self._next_fallback - now)
        if pending_delay is not None:
            timeout = min(timeout, pending_delay)

        already_signaled = WAKE_EVENT.is_set()
        WAKE_EVENT.clear()
        signaled = False
        if not already_signaled:
            signaled = WAKE_EVENT.wait(timeout)
        self.wake_count += 1

        now = time.monotonic()
        fallback_due = (
            now >= (self._next_fallback - 0.005)
            if (not already_signaled and not signaled and pending_delay is None)
            else (now >= self._next_fallback)
        )
        if fallback_due:
            self._next_fallback = now + self.fallback_seconds
        return fallback_due


def _is_folder(path: Path) -> bool:
    """``Path.is_dir`` that says no, instead of raising, for a folder that cannot be looked at.

    A network share that drops, or a drive that is half there, can fail with an error that
    ``is_dir`` passes on. The watcher asks while a drive may be coming and going, and must not
    stop because of that.
    """
    try:
        return path.is_dir()
    except OSError:
        return False


class FolderWatches:
    """The Downloads and Documents subscriptions of one observer.

    Live reload used to update the handlers' settings but keep the subscriptions
    made at startup, so changing the watched folders needed a restart.
    ``retarget`` compares the folders a new config asks for with the ones being
    watched and re-subscribes when they differ.
    """

    def __init__(self, observer) -> None:
        self.observer = observer
        self.root: Path | None = None
        self.sources: tuple[Path, ...] = ()
        self.downloads_handler: DownloadsHandler | None = None
        self.doc_handlers: list[DocumentsHandler] = []
        self._watches: list = []
        # True while the folder to watch is not there. Nothing is watched then (not even the
        # Documents sources, which are filed into that folder); ``retry`` starts the watches
        # once the folder is back.
        self.root_missing = False

    @staticmethod
    def targets(config: dict) -> tuple[Path, tuple[Path, ...]]:
        root = watch_root(config).resolve()
        sources = tuple(Path(s).resolve() for s in document_roots(config))
        return root, sources

    def build(self, config: dict, routing_context: RoutingContext | None = None) -> None:
        root, sources = self.targets(config)
        self.root, self.sources = root, sources
        self.downloads_handler = DownloadsHandler(root, config, routing_context)
        self.doc_handlers = []
        self._watches = []
        self.root_missing = not _is_folder(root)
        reason = "was not found"
        if not self.root_missing:
            try:
                self._watches = [self.observer.schedule(self.downloads_handler, str(root), recursive=True)]
                logging.info("Watching %s (recursive, with category auto-correction)", root)
                for source in sources:
                    handler = DocumentsHandler(list(sources), root, config, routing_context)
                    self.doc_handlers.append(handler)
                    self._watches.append(self.observer.schedule(handler, str(source), recursive=True))
                    logging.info("Watching %s (recursive)", source)
                if not sources:
                    logging.warning("No Documents sources found to watch")
            except OSError as exc:
                # The folder went away between the check and the watch (a drive that comes and
                # goes), or it cannot be opened. Whatever was watched so far is let go.
                reason = f"could not be opened ({exc})"
                self._drop_watches()
                self.root_missing = True
        if self.root_missing:
            # Watching a folder that is not there fails, and sorting some other folder in its
            # place would be worse. The folder is not created either: it may be a drive that is
            # not plugged in, and a different drive may have its letter now.
            logging.warning(
                "The folder to watch, %s, %s. Nothing is sorted until it is there: "
                "plug the drive back in, or choose another folder in Settings.",
                root, reason,
            )

    def _drop_watches(self) -> None:
        """Unschedule every watch. One whose folder is gone has stopped by itself and may refuse to be removed."""
        for watch in self._watches:
            try:
                self.observer.unschedule(watch)
            except Exception as exc:  # the observer already dropped it
                logging.debug("Unschedule failed: %s", exc)
        self._watches = []
        self.doc_handlers = []

    def _dead_watches(self) -> list:
        """The watches whose watcher thread has stopped.

        watchdog stops the thread of a watch by itself when its folder is deleted or its drive goes,
        and keeps the watch on its books: nothing is watched any more, and nothing says so.
        """
        mine = set(self._watches)
        emitters = getattr(self.observer, "emitters", None) or ()
        return [emitter.watch for emitter in list(emitters) if emitter.watch in mine and not emitter.is_alive()]

    def watch_lost(self) -> bool:
        """Notice that the folder being watched is gone, or that a watch has died (a drive unplugged while the app runs).

        Without this the app kept saying it was running, and never watched the folder again when it
        came back. The watches are let go and ``root_missing`` is set; ``retry`` starts them again
        once the folder is there, in the same round when it was only away for a moment.
        Returns True when it did.
        """
        if self.root_missing or not self._watches:
            return False
        gone = self.root is not None and not _is_folder(self.root)
        if not gone and not self._dead_watches():
            return False
        self._drop_watches()
        self.root_missing = True
        if gone:
            logging.warning(
                "The folder being watched, %s, is gone (a drive that was unplugged?). "
                "Nothing is sorted until it is back.",
                self.root,
            )
        else:
            logging.warning("The watch on %s stopped (a drive that was unplugged for a moment?); watching it again", self.root)
        return True

    def update_config(self, config: dict, routing_context: RoutingContext | None = None) -> None:
        if self.downloads_handler is not None:
            self.downloads_handler.update_config(config, routing_context)
        for handler in self.doc_handlers:
            handler.update_config(config, routing_context)

    def retarget(self, config: dict, routing_context: RoutingContext | None = None) -> bool:
        """Re-subscribe if the watched folders changed.  Returns True when they did."""
        root, sources = self.targets(config)
        if root == self.root and sources == self.sources:
            return False
        logging.info(
            "Watched folders changed (%s, %d Documents source(s) -> %s, %d); re-subscribing",
            self.root, len(self.sources), root, len(sources),
        )
        self._drop_watches()
        # Pending events belong to the old folders; the sweep after a retarget
        # picks up anything that is in the new ones.
        with QUEUE_LOCK:
            PENDING_DOWNLOADS.clear()
            PENDING_DOCUMENTS.clear()
            PENDING_MISPLACED.clear()
            PENDING_DEADLINES.clear()
        self.build(config, routing_context)
        return True

    def retry(self, config: dict, routing_context: RoutingContext | None = None) -> bool:
        """Start the watches if the folder to watch is back (a drive plugged in again).

        Returns True when it is, and the watches are running. Cheap enough to call on every
        wake-up while ``root_missing`` is set: it does nothing until the folder exists.
        """
        if not self.root_missing:
            return False
        try:
            root, _sources = self.targets(config)
            if not _is_folder(root):
                return False
            self.build(config, routing_context)
        except OSError as exc:  # a drive that is only half there: it is tried again next time
            logging.debug("The folder to watch is not ready yet: %s", exc)
            return False
        return not self.root_missing


def _publish_folder_state(folders: FolderWatches, on_sorted: Callable[[int], None] | None = None) -> None:
    """Say, in the runtime state and for the tray's tooltip, whether the watch folder is there."""
    was_missing = WATCH_FOLDER_MISSING.is_set()
    # Waiting for the welcome wizard is the state that matters more: the wizard looks for it.
    if folders.root_missing:
        WATCH_FOLDER_MISSING.set()
        if not SETUP_PENDING.is_set():
            write_runtime_state("waiting_for_folder", f"waiting for {folders.root}")
    else:
        WATCH_FOLDER_MISSING.clear()
        if was_missing and not SETUP_PENDING.is_set():
            write_runtime_state("running", "the watch folder is back")
    if on_sorted is not None and was_missing != folders.root_missing:
        try:
            on_sorted(0)  # refreshes the tray
        except Exception:
            logging.exception("Activity callback failed")


def _run_daily_cleanup(config: dict) -> None:
    """Old installers and zips to the Recycle Bin, if turned on (clean_old.auto_cleanup),
    and quarantine batches past their window (clean_old.expire_quarantine)."""
    try:
        import clean_old
        result = clean_old.auto_cleanup(config)
    except Exception:
        logging.exception("Daily clean-up failed")
        return
    try:
        clean_old.expire_quarantine(config)
    except Exception:
        logging.exception("Quarantine expiry failed")
    try:
        import desktop_sweep
        desktop_sweep.sweep(config)
    except Exception:
        logging.exception("Desktop sweep failed")
    if result and result["count"] and config.get("notifications", True):
        try:
            import ribbon_popup
            noun = "file" if result["count"] == 1 else "files"
            ribbon_popup.notify_summary(
                "Cleaned up old downloads",
                f"Moved {result['count']} old {noun} ({clean_old.format_size(result['bytes'])}) to the Recycle Bin.",
            )
        except Exception as err:
            logging.debug("Clean-up ribbon skipped: %s", err)


def _startup_sweep(config: dict, routing_context: RoutingContext | None, root: Path) -> None:
    """Sort what is already in the watched folders, as a restart would.

    Files that were already there when the user finished the welcome wizard and
    chose not to organize existing files are left alone (``skip_files_before``).
    """
    batch_id = uuid.uuid4().hex[:8]
    cutoff = existing_files_cutoff(config)
    moved = sort_existing(
        config,
        batch_id=batch_id,
        routing_context=routing_context,
        skip_before=cutoff,
    )
    logging.info("Startup sort moved %s file(s) in %s", moved, root)
    docs_moved = sort_existing_documents(
        config,
        batch_id=batch_id,
        routing_context=routing_context,
        skip_before=cutoff,
    )
    logging.info("Startup Documents sweep moved %s file(s)", docs_moved)
    # Its own batch: after an update that changes the folder layout, this is what
    # moves files into their type folders (Documents\PDF), and "Undo last sort"
    # then undoes exactly that.
    tidy_id = uuid.uuid4().hex[:8]
    misplaced_moved = sort_existing_misplaced(
        config,
        batch_id=tidy_id,
        routing_context=routing_context,
        skip_before=cutoff,
    )
    if misplaced_moved:
        logging.info("Startup category sweep corrected %s misplaced file(s)", misplaced_moved)
        _notify_tidied(config, misplaced_moved)


def _notify_tidied(config: dict, count: int) -> None:
    """Say that files inside the sorted folders were moved to where they belong now."""
    if not (config.get("ribbon_enabled", True) and config.get("notifications", True)):
        return
    try:
        import ribbon_popup
        noun = "file" if count == 1 else "files"
        ribbon_popup.notify_summary(
            "Folders tidied",
            f"Moved {count} {noun} into the folder for their kind, like Documents\\PDF. "
            "“Undo last sort” puts them back.",
        )
    except Exception as err:
        logging.debug("Tidy ribbon skipped: %s", err)


def run(
    stop: "threading.Event | None" = None,
    on_sorted=None,
    wait_for_setup: bool = False,
) -> int:
    """Watch the folders until stop is set. Safe to call from a background thread.

    on_sorted, if given, is called with the number of files moved each time a
    batch is drained, so a host like the tray app can report activity.

    wait_for_setup: when True and the welcome wizard has not been completed yet,
    nothing is sorted until the saved settings say it has.  New downloads that
    arrive in the meantime stay queued and are sorted with the chosen profile.
    """
    reloader = ConfigReloader(CONFIG_PATH)
    config = reloader.config
    routing_context = reloader.routing_context
    root = watch_root(config)

    observer = Observer()
    coordinator = WatchCoordinator()
    folders = FolderWatches(observer)
    folders.build(config, routing_context)

    control_handler = ControlFileHandler(reloader)
    control_dirs = {CONFIG_PATH.resolve().parent, ACTIVE_PROJECT_FILE.resolve().parent, DEFAULT_CONFIG_PATH.resolve().parent}
    for control_dir in control_dirs:
        control_dir.mkdir(parents=True, exist_ok=True)
        observer.schedule(control_handler, str(control_dir), recursive=False)

    observer.start()
    write_runtime_state("running", "filesystem observers active")
    last_heartbeat = time.monotonic()

    switcher_monitor = None
    if config.get("quick_switcher_enabled", True) and sys.platform == "win32":
        try:
            from quick_switcher import QuickSwitcherMonitor
            switcher_monitor = QuickSwitcherMonitor(stop_event=stop)
            switcher_monitor.start()
        except Exception as err:
            logging.debug("Quick switcher monitor start skipped: %s", err)
    setup_pending = wait_for_setup and not config.get("onboarding_completed", False)
    # A sweep that was due while sorting was paused, or while the folder to watch was missing; it
    # runs when sorting is resumed, or when the folder is back.
    sweep_pending = False
    try:
        # Observe first, reconcile second: a download arriving during the scan is
        # queued and cannot fall into the startup race window.
        _publish_folder_state(folders, on_sorted)
        if setup_pending:
            SETUP_PENDING.set()
            write_runtime_state("waiting_for_setup", "waiting for the welcome wizard")
            logging.info("Waiting for the welcome wizard to finish before sorting anything")
        elif is_paused():
            # Paused means nothing moves, including what is already in the folders.
            sweep_pending = True
            logging.info("Sorting is paused; the startup sort waits until it is resumed")
        elif folders.root_missing:
            sweep_pending = True
            logging.info("The startup sort waits until the folder to watch is there")
        else:
            _startup_sweep(config, routing_context, root)

        while not (stop is not None and stop.is_set()):
            fallback_due = coordinator.wait(ignore_pending=is_paused() or setup_pending)
            if stop is not None and stop.is_set():
                break

            # Before anything else touches the folder: a drive unplugged while the app runs.
            lost = folders.watch_lost()
            if lost:
                sweep_pending = True  # what arrives while nothing is watched is not seen

            changed = False
            new_config = config
            if reloader.dirty or fallback_due:
                changed, new_config = reloader.check_for_updates()
            if time.monotonic() - last_heartbeat >= 300.0:
                if setup_pending:
                    heartbeat_state = "waiting_for_setup"
                elif folders.root_missing:
                    heartbeat_state = "waiting_for_folder"
                else:
                    heartbeat_state = "running"
                write_runtime_state(heartbeat_state, "heartbeat")
                last_heartbeat = time.monotonic()
                if not setup_pending and not is_paused() and not folders.root_missing:
                    _run_daily_cleanup(config)
                try:
                    import bambu_recent
                    bambu_recent.apply_pending(config=config)
                except Exception:
                    logging.exception("Bambu recent-file update failed")
            if changed:
                config = new_config
                routing_context = reloader.routing_context
                retargeted = folders.retarget(config, routing_context)
                if not retargeted:
                    folders.update_config(config, routing_context)
                else:
                    _publish_folder_state(folders, on_sorted)
                root = watch_root(config)
                if setup_pending and config.get("onboarding_completed", False):
                    # The wizard just saved the user's profile: start sorting with it.
                    setup_pending = False
                    SETUP_PENDING.clear()
                    write_runtime_state("running", "welcome wizard finished")
                    logging.info("Welcome wizard finished; sorting starts now")
                    _publish_folder_state(folders)  # the folder may still be missing
                    if is_paused() or folders.root_missing:
                        sweep_pending = True
                    else:
                        _startup_sweep(config, routing_context, root)
                    if on_sorted is not None:
                        try:
                            on_sorted(0)
                        except Exception:
                            logging.exception("Activity callback failed")
                elif retargeted and not setup_pending:
                    # Watched folders changed: behave as a restart would for the new
                    # folders (sweep what is already there).
                    if is_paused() or folders.root_missing:
                        sweep_pending = True
                    else:
                        cutoff = existing_files_cutoff(config)
                        sweep_id = uuid.uuid4().hex[:8]
                        sort_existing(config, batch_id=sweep_id, routing_context=routing_context, skip_before=cutoff)
                        sort_existing_documents(config, batch_id=sweep_id, routing_context=routing_context, skip_before=cutoff)

            came_back = folders.root_missing and folders.retry(config, routing_context)
            if came_back:
                # The sweep that was waiting for the folder (sweep_pending) runs below.
                logging.info("The folder to watch is back: %s", folders.root)
            if lost or came_back:
                _publish_folder_state(folders, on_sorted)

            if setup_pending:
                continue

            if folders.root_missing:
                continue  # nothing is sorted, released from a hold or cleaned up until the folder is there

            if sweep_pending and not is_paused():
                sweep_pending = False
                _startup_sweep(config, routing_context, root)
                if on_sorted is not None:
                    try:
                        on_sorted(0)
                    except Exception:
                        logging.exception("Activity callback failed")

            moved = drain(config, root=root, routing_context=routing_context, on_activity=on_sorted)
            if moved and on_sorted is not None:
                try:
                    on_sorted(moved)
                except Exception:
                    logging.exception("Activity callback failed")
    except KeyboardInterrupt:
        pass
    finally:
        SETUP_PENDING.clear()
        WATCH_FOLDER_MISSING.clear()
        observer.stop()
        write_runtime_state("stopped", "watch loop exited")
    observer.join()
    return 0


def main() -> int:
    import about
    if about.handle_version_flag():
        return 0
    setup_logging()
    lock = acquire_single_instance_lock()
    if not lock:
        logging.warning("Another instance of DownloadSorter is already running. Exiting.")
        return 0
    try:
        import safe_ops
        safe_ops.recover_pending_journal(min_age_seconds=10.0)
    except Exception:
        logging.exception("Failed to run pending journal recovery")
    return run()


if __name__ == "__main__":
    sys.exit(main())
