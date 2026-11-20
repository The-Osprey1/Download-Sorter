"""The log file and what happens to unexpected errors.

The log is capped (a few MB in all) instead of growing for as long as the app is
installed, and an error nobody caught is written to it with its traceback, so a
bug report can say what went wrong.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys
import threading

LOG_MAX_BYTES = 2_000_000
LOG_BACKUPS = 2
LOG_FORMAT = "%(asctime)s %(levelname)s %(message)s"

# Marks the handler this module added, so setting up again replaces it.
HANDLER_TAG = "_download_sorter_log"
_CRASH_HOOK_TAG = "_download_sorter_crash_hook"


def remove_file_logging() -> None:
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, HANDLER_TAG, False):
            root.removeHandler(handler)
            handler.close()


def configure_file_logging(
    log_file: Path,
    *,
    level: int = logging.INFO,
    max_bytes: int = LOG_MAX_BYTES,
    backups: int = LOG_BACKUPS,
) -> logging.Handler:
    """Log to ``log_file``, starting a new file once it reaches ``max_bytes``.

    ``backups`` older files are kept (``.1``, ``.2``); anything older is dropped.
    """
    remove_file_logging()
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        str(log_file), maxBytes=max_bytes, backupCount=backups, encoding="utf-8", delay=True
    )
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    setattr(handler, HANDLER_TAG, True)
    root = logging.getLogger()
    root.addHandler(handler)
    if root.level == logging.NOTSET or root.level > level:
        root.setLevel(level)
    return handler


def install_crash_logging() -> None:
    """Write errors nobody caught (main thread, Qt slots and background threads) to the log.

    The previous handlers still run afterwards, so nothing else changes.
    """
    if not getattr(sys.excepthook, _CRASH_HOOK_TAG, False):
        previous = sys.excepthook

        def _log_uncaught(exc_type, exc, tb) -> None:  # noqa: ANN001
            if not issubclass(exc_type, KeyboardInterrupt):
                logging.critical("Unexpected error", exc_info=(exc_type, exc, tb))
            previous(exc_type, exc, tb)

        setattr(_log_uncaught, _CRASH_HOOK_TAG, True)
        sys.excepthook = _log_uncaught

    if not getattr(threading.excepthook, _CRASH_HOOK_TAG, False):
        previous_thread_hook = threading.excepthook

        def _log_uncaught_in_thread(args: threading.ExceptHookArgs) -> None:
            if args.exc_type is not SystemExit:
                name = args.thread.name if args.thread is not None else "unknown"
                logging.critical(
                    "Unexpected error in background thread %s",
                    name,
                    exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
                )
            previous_thread_hook(args)

        setattr(_log_uncaught_in_thread, _CRASH_HOOK_TAG, True)
        threading.excepthook = _log_uncaught_in_thread
