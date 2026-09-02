"""Watch Downloads (top level) and Windows Documents (recursive)."""

from __future__ import annotations

import logging
import sys
import threading
import time
import uuid
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from sorter import (
    document_roots,
    is_paused,
    is_quarantined,
    is_under,
    keep_in_documents,
    load_config,
    move_file,
    relocate_from_documents,
    setup_logging,
    should_relocate_from_documents,
    sort_existing,
    sort_existing_documents,
    watch_root,
)

PENDING_DOWNLOADS: set[Path] = set()
PENDING_DOCUMENTS: set[Path] = set()


def acquire_single_instance_lock(name: str = "DownloadSorter_SingleInstance") -> object:
    if sys.platform == "win32":
        import ctypes
        mutex = ctypes.windll.kernel32.CreateMutexW(None, False, f"Local\\{name}")
        last_error = ctypes.windll.kernel32.GetLastError()
        if last_error == 183:  # ERROR_ALREADY_EXISTS
            return None
        return mutex
    return True


class TopLevelHandler(FileSystemEventHandler):
    def __init__(self, root: Path) -> None:
        self.root = root

    def _queue(self, src: str) -> None:
        path = Path(src)
        if path.parent.resolve() != self.root:
            return
        if path.exists() and path.is_dir():
            return
        PENDING_DOWNLOADS.add(path)

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
    def __init__(self, sources: list[Path], downloads: Path, config: dict) -> None:
        self.sources = sources
        self.downloads = downloads
        self.config = config

    def _queue(self, src: str) -> None:
        path = Path(src)
        if path.exists() and path.is_dir():
            return
        resolved = path.resolve() if path.exists() else path
        if is_under(resolved, self.downloads):
            return
        if not any(is_under(resolved, source) for source in self.sources):
            return
        if not should_relocate_from_documents(path, self.config):
            return
        PENDING_DOCUMENTS.add(path)

    def on_created(self, event) -> None:  # type: ignore[no-untyped-def]
        if not event.is_directory:
            self._queue(event.src_path)

    def on_moved(self, event) -> None:  # type: ignore[no-untyped-def]
        if not event.is_directory:
            self._queue(event.dest_path)

    def on_modified(self, event) -> None:  # type: ignore[no-untyped-def]
        if not event.is_directory:
            self._queue(event.src_path)


def drain(config: dict) -> int:
    moved_count = 0
    if is_paused():
        return moved_count
    if PENDING_DOWNLOADS:
        batch = list(PENDING_DOWNLOADS)
        PENDING_DOWNLOADS.clear()
        batch_id = uuid.uuid4().hex[:8]
        for path in batch:
            try:
                dest = move_file(path, config, batch_id=batch_id)
                if dest is not None:
                    moved_count += 1
                elif path.exists() and path.is_file() and not is_quarantined(path):
                    PENDING_DOWNLOADS.add(path)
            except Exception:
                logging.exception("Failed sorting %s", path)
    if PENDING_DOCUMENTS:
        batch = list(PENDING_DOCUMENTS)
        PENDING_DOCUMENTS.clear()
        batch_id = uuid.uuid4().hex[:8]
        for path in batch:
            try:
                if relocate_from_documents(path, config, batch_id=batch_id) is not None:
                    moved_count += 1
            except Exception:
                logging.exception("Failed relocating %s", path)
    return moved_count


def run(stop: "threading.Event | None" = None, on_sorted=None) -> int:
    """Watch the folders until stop is set. Safe to call from a background thread.

    on_sorted, if given, is called with the number of files moved each time a
    batch is drained, so a host like the tray app can report activity.
    """
    config = load_config()
    root = watch_root(config)
    batch_id = uuid.uuid4().hex[:8]
    moved = sort_existing(config, batch_id=batch_id)
    logging.info("Startup sort moved %s file(s) in %s", moved, root)
    docs_moved = sort_existing_documents(config, batch_id=batch_id)
    logging.info("Startup Documents sweep moved %s file(s)", docs_moved)

    observer = Observer()
    observer.schedule(TopLevelHandler(root), str(root), recursive=False)
    logging.info("Watching %s (top level only)", root)

    sources = document_roots(config)
    if sources:
        observer.schedule(
            DocumentsHandler(sources, root, config),
            str(sources[0]),
            recursive=True,
        )
        for extra in sources[1:]:
            observer.schedule(
                DocumentsHandler(sources, root, config),
                str(extra),
                recursive=True,
            )
        for source in sources:
            logging.info("Watching %s (recursive)", source)
    else:
        logging.warning("No Documents sources found to watch")

    observer.start()
    try:
        while not (stop is not None and stop.is_set()):
            time.sleep(0.5)
            moved = drain(config)
            if moved and on_sorted is not None:
                try:
                    on_sorted(moved)
                except Exception:
                    logging.exception("Activity callback failed")
    except KeyboardInterrupt:
        pass
    finally:
        observer.stop()
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
