"""Watch Downloads and Documents folders and sort incoming files continuously."""

from __future__ import annotations

import logging
import os
from pathlib import Path
import sys
import threading
import time
import uuid

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from sorter import (
    classify,
    collect_category_folders,
    document_roots,
    is_incomplete,
    is_paused,
    is_quarantined,
    is_under,
    keep_in_documents,
    load_config,
    move_file,
    reclassify_misplaced_file,
    relocate_from_documents,
    setup_logging,
    should_relocate_from_documents,
    sort_existing,
    sort_existing_documents,
    sort_existing_misplaced,
    watch_root,
)

PENDING_DOWNLOADS: set[Path] = set()
PENDING_DOCUMENTS: set[Path] = set()
PENDING_MISPLACED: set[Path] = set()


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
    def __init__(self, root: Path, config: dict) -> None:
        self.root = root.resolve()
        self.config = config
        self.category_folders = {f.replace("/", "\\").lower() for f in collect_category_folders(config)}

    def _queue(self, src: str) -> None:
        path = Path(src)
        if path.exists() and path.is_dir():
            return
        if is_incomplete(path, self.config):
            return

        try:
            rel = path.resolve().relative_to(self.root)
        except (ValueError, OSError):
            return

        # Case 1: File is sitting directly at the top level of Downloads
        if len(rel.parts) == 1:
            PENDING_DOWNLOADS.add(path)
            return

        # Case 2: File is sitting directly inside one of our managed category folders
        parent_rel = str(path.parent.resolve().relative_to(self.root)).replace("/", "\\")
        if parent_rel.lower() in self.category_folders:
            target_folder = classify(path, self.config).replace("/", "\\")
            # Only queue if the file is in the WRONG category folder
            if parent_rel.lower() != target_folder.lower():
                PENDING_MISPLACED.add(path)
            return

        # Case 3: File landed directly inside an unmanaged subfolder (e.g. browser remembered an old folder)
        if len(rel.parts) == 2:
            target_folder = classify(path, self.config).replace("/", "\\")
            default_folder = self.config.get("default_folder", "Other").lower()
            if target_folder.lower() != default_folder:
                PENDING_MISPLACED.add(path)

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


def drain(config: dict, root: Path | None = None) -> int:
    moved_count = 0
    if is_paused():
        return moved_count
    target_root = root or watch_root(config)
    if PENDING_DOWNLOADS:
        batch = list(PENDING_DOWNLOADS)
        PENDING_DOWNLOADS.clear()
        batch_id = uuid.uuid4().hex[:8]
        for path in batch:
            try:
                dest = move_file(path, config, batch_id=batch_id)
                if dest is not None:
                    moved_count += 1
                    try:
                        from ribbon_popup import notify_file_moved
                        notify_file_moved(dest, target_root)
                    except Exception as err:
                        logging.debug("Ribbon notification skipped: %s", err)
                elif path.exists() and path.is_file() and not is_quarantined(path) and not is_incomplete(path, config):
                    PENDING_DOWNLOADS.add(path)
            except Exception:
                logging.exception("Failed sorting %s", path)
    if PENDING_DOCUMENTS:
        batch = list(PENDING_DOCUMENTS)
        PENDING_DOCUMENTS.clear()
        batch_id = uuid.uuid4().hex[:8]
        for path in batch:
            try:
                dest = relocate_from_documents(path, config, batch_id=batch_id)
                if dest is not None:
                    moved_count += 1
                    try:
                        from ribbon_popup import notify_file_moved
                        notify_file_moved(dest, target_root)
                    except Exception as err:
                        logging.debug("Ribbon notification skipped: %s", err)
            except Exception:
                logging.exception("Failed relocating %s", path)
    if PENDING_MISPLACED:
        batch = list(PENDING_MISPLACED)
        PENDING_MISPLACED.clear()
        batch_id = uuid.uuid4().hex[:8]
        for path in batch:
            try:
                dest = reclassify_misplaced_file(path, config, batch_id=batch_id)
                if dest is not None:
                    moved_count += 1
                    try:
                        from ribbon_popup import notify_file_moved
                        notify_file_moved(dest, target_root)
                    except Exception as err:
                        logging.debug("Ribbon notification skipped: %s", err)
                elif path.exists() and path.is_file() and not is_quarantined(path) and not is_incomplete(path, config):
                    PENDING_MISPLACED.add(path)
            except Exception:
                logging.exception("Failed auto-correcting misplaced file for %s", path)
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
    misplaced_moved = sort_existing_misplaced(config, batch_id=batch_id)
    if misplaced_moved:
        logging.info("Startup category sweep corrected %s misplaced file(s)", misplaced_moved)

    observer = Observer()
    observer.schedule(DownloadsHandler(root, config), str(root), recursive=True)
    logging.info("Watching %s (recursive, with category auto-correction)", root)

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
            moved = drain(config, root=root)
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
