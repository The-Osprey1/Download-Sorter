"""Watch Downloads and Documents folders and sort incoming files continuously."""

from __future__ import annotations

import json
import logging
from pathlib import Path
import sys
import threading
import time
import uuid

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from sorter import (
    ACTIVE_PROJECT_FILE,
    CONFIG_PATH,
    PAUSE_FILE,
    RoutingContext,
    compile_routing,
    get_default_config,
    document_roots,
    is_incomplete,
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


def _settle_seconds(config: dict) -> float:
    try:
        return max(0.0, float(config.get("watch", {}).get("settle_seconds", 1.5)))
    except (TypeError, ValueError):
        return 1.5


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


class DownloadsHandler(FileSystemEventHandler):
    """Monitors Downloads recursively for top-level downloads and misplaced category files."""
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

        # Case 1: File is sitting directly at the top level of Downloads
        if len(rel.parts) == 1:
            _enqueue("download", PENDING_DOWNLOADS, path, cfg)
            return

        # Case 2: File is sitting directly inside one of our managed category folders
        parent_rel = str(path.parent.resolve().relative_to(self.root)).replace("/", "\\")
        if parent_rel.lower() in categories:
            # Classification is deliberately deferred until the file is stable;
            # modified events from large G-code files can otherwise reread it
            # many times while the producing application is still writing.
            _enqueue("misplaced", PENDING_MISPLACED, path, cfg)
            return

        # Case 3: File landed directly inside an unmanaged subfolder (e.g. browser remembered an old folder)
        if len(rel.parts) == 2:
            _enqueue("misplaced", PENDING_MISPLACED, path, cfg)

    def on_created(self, event) -> None:  # type: ignore[no-untyped-def]
        if not event.is_directory:
            self._queue(event.src_path)

    def on_moved(self, event) -> None:  # type: ignore[no-untyped-def]
        if not event.is_directory:
            self._queue(event.dest_path)

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


def drain(
    config: dict,
    root: Path | None = None,
    routing_context: RoutingContext | None = None,
) -> int:
    moved_count = 0
    if is_paused():
        return moved_count
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
                    try:
                        from ribbon_popup import notify_file_moved
                        notify_file_moved(dest, target_root)
                    except Exception as err:
                        logging.debug("Ribbon notification skipped: %s", err)
                elif (
                    path.exists()
                    and path.is_file()
                    and not is_quarantined(path)
                    and not is_cloud_placeholder(path)
                    and not is_incomplete(path, config, routing_context)
                ):
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
                    try:
                        from ribbon_popup import notify_file_moved
                        notify_file_moved(dest, target_root)
                    except Exception as err:
                        logging.debug("Ribbon notification skipped: %s", err)
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
                    try:
                        from ribbon_popup import notify_file_moved
                        notify_file_moved(dest, target_root)
                    except Exception as err:
                        logging.debug("Ribbon notification skipped: %s", err)
            except Exception:
                logging.exception("Failed auto-correcting misplaced file for %s", path)
    return moved_count


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
        if not already_signaled:
            WAKE_EVENT.wait(timeout)
        self.wake_count += 1

        now = time.monotonic()
        fallback_due = now >= self._next_fallback
        if fallback_due:
            self._next_fallback = now + self.fallback_seconds
        return fallback_due


def run(stop: "threading.Event | None" = None, on_sorted=None) -> int:
    """Watch the folders until stop is set. Safe to call from a background thread.

    on_sorted, if given, is called with the number of files moved each time a
    batch is drained, so a host like the tray app can report activity.
    """
    reloader = ConfigReloader(CONFIG_PATH)
    config = reloader.config
    routing_context = reloader.routing_context
    root = watch_root(config)

    observer = Observer()
    coordinator = WatchCoordinator()
    downloads_handler = DownloadsHandler(root, config, routing_context)
    observer.schedule(downloads_handler, str(root), recursive=True)
    logging.info("Watching %s (recursive, with category auto-correction)", root)

    doc_handlers: list[DocumentsHandler] = []
    sources = document_roots(config)
    if sources:
        dh0 = DocumentsHandler(sources, root, config, routing_context)
        doc_handlers.append(dh0)
        observer.schedule(dh0, str(sources[0]), recursive=True)
        for extra in sources[1:]:
            dhe = DocumentsHandler(sources, root, config, routing_context)
            doc_handlers.append(dhe)
            observer.schedule(dhe, str(extra), recursive=True)
        for source in sources:
            logging.info("Watching %s (recursive)", source)
    else:
        logging.warning("No Documents sources found to watch")

    control_handler = ControlFileHandler(reloader)
    control_dirs = {CONFIG_PATH.resolve().parent, ACTIVE_PROJECT_FILE.resolve().parent}
    for control_dir in control_dirs:
        control_dir.mkdir(parents=True, exist_ok=True)
        observer.schedule(control_handler, str(control_dir), recursive=False)

    observer.start()
    write_runtime_state("running", "filesystem observers active")
    last_heartbeat = time.monotonic()
    try:
        # Observe first, reconcile second: a download arriving during the scan is
        # queued and cannot fall into the startup race window.
        batch_id = uuid.uuid4().hex[:8]
        moved = sort_existing(
            config,
            batch_id=batch_id,
            routing_context=routing_context,
        )
        logging.info("Startup sort moved %s file(s) in %s", moved, root)
        docs_moved = sort_existing_documents(
            config,
            batch_id=batch_id,
            routing_context=routing_context,
        )
        logging.info("Startup Documents sweep moved %s file(s)", docs_moved)
        misplaced_moved = sort_existing_misplaced(
            config,
            batch_id=batch_id,
            routing_context=routing_context,
        )
        if misplaced_moved:
            logging.info("Startup category sweep corrected %s misplaced file(s)", misplaced_moved)

        while not (stop is not None and stop.is_set()):
            fallback_due = coordinator.wait(ignore_pending=is_paused())
            if stop is not None and stop.is_set():
                break

            changed = False
            new_config = config
            if reloader.dirty or fallback_due:
                changed, new_config = reloader.check_for_updates()
            if time.monotonic() - last_heartbeat >= 300.0:
                write_runtime_state("running", "heartbeat")
                last_heartbeat = time.monotonic()
            if changed:
                config = new_config
                routing_context = reloader.routing_context
                downloads_handler.update_config(config, routing_context)
                for dh in doc_handlers:
                    dh.update_config(config, routing_context)

            moved = drain(config, root=root, routing_context=routing_context)
            if moved and on_sorted is not None:
                try:
                    on_sorted(moved)
                except Exception:
                    logging.exception("Activity callback failed")
    except KeyboardInterrupt:
        pass
    finally:
        observer.stop()
        write_runtime_state("stopped", "watch loop exited")
    observer.join()
    return 0


def main() -> int:
    setup_logging()
    lock = acquire_single_instance_lock()
    if not lock:
        logging.warning("Another instance of DownloadSorter is already running. Exiting.")
        return 0
    return run()


if __name__ == "__main__":
    sys.exit(main())
