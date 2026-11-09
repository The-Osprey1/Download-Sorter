"""Help people report problems: crash reports and pre-filled bug reports.

The installed app has no console window, so an unexpected error used to
vanish without a trace: the app simply closed. This module makes sure every
unexpected error is:

* written to ``sorter.log``;
* saved as a small text file in ``%LOCALAPPDATA%\\download-sorter\\crash-reports``
  (the newest 20 are kept), with the app version, the Windows version and the
  error's details;
* shown once, in the installed app's windows and tray, with a button that opens
  a bug report on GitHub with those details already filled in.

Nothing is ever sent automatically. Opening the bug report only opens a page in
the browser; the person reads it and decides whether to post it.

``system_details()`` and ``issue_url()`` are also used by **Report a problem**
in the tray menu and in Settings.
"""

from __future__ import annotations

import logging
import os
import platform
import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

import about

CRASH_DIR_NAME = "crash-reports"
KEEP_CRASH_REPORTS = 20
# Browsers and GitHub cope with long addresses, but keep well under the ~8000
# characters some proxies allow. The traceback is shortened to fit.
MAX_URL_LENGTH = 7000
BUG_TEMPLATE = "bug_report.yml"
# Set by the tests, so a message box never waits for a click.
NO_DIALOGS_ENV = "DOWNLOAD_SORTER_NO_DIALOGS"

_handler_lock = threading.Lock()
_dialog_shown = False
_installed_for: str | None = None


# ---------------------------------------------------------------------------
# Details about this copy of Download Sorter (never about files)
# ---------------------------------------------------------------------------

def data_dir() -> Path:
    """Where Download Sorter keeps its settings, history and logs."""
    try:
        import sorter
        return Path(sorter.LOG_DIR)
    except Exception:
        base = os.environ.get("LOCALAPPDATA") or str(Path(__file__).resolve().parent)
        return Path(base) / "download-sorter"


def crash_dir() -> Path:
    return data_dir() / CRASH_DIR_NAME


def install_kind() -> str:
    if getattr(sys, "frozen", False):
        return "Installed app (installer or portable zip)"
    return "From source (Python)"


def windows_version() -> str:
    if sys.platform == "win32":
        try:
            release, version, _csd, _ptype = platform.win32_ver()
            # Windows 11 still reports release "10"; build 22000 and later is 11.
            build = int(version.split(".")[-1]) if version else 0
            if release == "10" and build >= 22000:
                release = "11"
            return f"Windows {release} (build {version})" if version else f"Windows {release}"
        except Exception:
            pass
    return platform.platform()


def system_details() -> str:
    """A few lines that help with a bug report. No file names, no folder names."""
    lines = [
        f"Download Sorter: {about.__version__}",
        f"Runs as: {install_kind()}",
        f"System: {windows_version()}",
        f"Python: {platform.python_version()}",
    ]
    try:
        import sorter
        lines.append(f"Sorting: {'paused' if sorter.is_paused() else 'on'}")
    except Exception:
        pass
    return "\n".join(lines)


def issue_url(title: str = "", description: str = "", logs: str = "") -> str:
    """A GitHub "new bug report" address with the version and system filled in.

    GitHub fills in an issue form's fields from query parameters that match
    the field ids in ``.github/ISSUE_TEMPLATE/bug_report.yml``.
    """
    base = f"{about.REPO_URL}/issues/new"

    def build(log_text: str) -> str:
        params = {"template": BUG_TEMPLATE, "version": about.__version__, "system": system_details()}
        if title:
            params["title"] = title
        if description:
            params["description"] = description
        if log_text:
            params["logs"] = log_text
        return f"{base}?{urlencode(params)}"

    url = build(logs)
    if len(url) > MAX_URL_LENGTH and logs:
        # Keep the end of the traceback: that's where the error is.
        tail = logs
        while tail and len(url) > MAX_URL_LENGTH:
            tail = tail[max(1, len(tail) // 4):]
            url = build("...\n" + tail)
    return url


def open_issue(title: str = "", description: str = "", logs: str = "") -> bool:
    """Open a pre-filled bug report in the browser. False if no browser opened."""
    import webbrowser
    try:
        return bool(webbrowser.open(issue_url(title, description, logs)))
    except Exception:
        logging.exception("Could not open the bug report page")
        return False


# ---------------------------------------------------------------------------
# Crash reports
# ---------------------------------------------------------------------------

def format_error(exc_type, exc, tb) -> str:
    return "".join(traceback.format_exception(exc_type, exc, tb)).rstrip()


def write_crash_report(exc_type, exc, tb, where: str = "") -> Path | None:
    """Save the error to crash-reports\\crash-<time>.txt. Returns the file, or None."""
    folder = crash_dir()
    try:
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        path = folder / f"crash-{stamp}.txt"
        text = (
            f"Download Sorter crash report, {datetime.now().isoformat(timespec='seconds')}\n"
            f"Where: {where or 'unknown'}\n"
            f"{system_details()}\n\n"
            f"{format_error(exc_type, exc, tb)}\n"
        )
        path.write_text(text, encoding="utf-8")
        _prune_old_reports(folder)
        return path
    except Exception:
        logging.exception("Could not save a crash report")
        return None


def _prune_old_reports(folder: Path) -> None:
    try:
        reports = sorted(folder.glob("crash-*.txt"))
        for old in reports[:-KEEP_CRASH_REPORTS]:
            old.unlink(missing_ok=True)
    except OSError:
        pass


def recent_crash_reports() -> list[Path]:
    """Newest first."""
    try:
        return sorted(crash_dir().glob("crash-*.txt"), reverse=True)
    except OSError:
        return []


def _short_summary(exc_type, exc) -> str:
    name = getattr(exc_type, "__name__", "Error")
    message = str(exc).strip().splitlines()[0] if str(exc).strip() else ""
    return f"{name}: {message}"[:120] if message else name


def _show_crash_dialog(report: Path | None, exc_type, exc, tb, where: str) -> None:
    """Tell the person once, and offer to open a pre-filled bug report.

    Uses the plain Windows message box, which works in every part of the app
    (tray, windows and ribbons) without needing Qt to still be healthy.
    """
    global _dialog_shown
    if _dialog_shown or sys.platform != "win32" or os.environ.get(NO_DIALOGS_ENV):
        return
    _dialog_shown = True
    saved = f"A report was saved in:\n{report}\n\n" if report else ""
    message = (
        "Download Sorter ran into an unexpected problem.\n\n"
        f"{_short_summary(exc_type, exc)}\n\n"
        f"{saved}"
        "Would you like to report it on GitHub? Your browser opens a bug report "
        "with the details filled in. Nothing is sent until you post it, and you "
        "can remove anything you don't want to share, such as file names."
    )
    try:
        import ctypes
        MB_YESNO, MB_ICONWARNING, MB_SETFOREGROUND, IDYES = 0x4, 0x30, 0x10000, 6
        answer = ctypes.windll.user32.MessageBoxW(
            None, message, f"{about.APP_NAME} - something went wrong",
            MB_YESNO | MB_ICONWARNING | MB_SETFOREGROUND,
        )
        if answer == IDYES:
            open_issue(
                title=f"Crash: {_short_summary(exc_type, exc)}",
                description=f"Download Sorter showed an error ({where or 'unknown part'}).\n\n"
                            "What I was doing just before:\n",
                logs=format_error(exc_type, exc, tb),
            )
    except Exception:
        logging.exception("Could not show the crash message")


def handle_exception(exc_type, exc, tb, where: str = "", show_dialog: bool = False) -> Path | None:
    """Log and save an unexpected error, and tell the person if asked to."""
    if exc_type is not None and issubclass(exc_type, (KeyboardInterrupt, SystemExit)):
        return None
    with _handler_lock:
        try:
            logging.critical("Unexpected error in %s", where or "Download Sorter",
                             exc_info=(exc_type, exc, tb))
        except Exception:
            pass
        report = write_crash_report(exc_type, exc, tb, where)
        if show_dialog:
            _show_crash_dialog(report, exc_type, exc, tb, where)
        return report


def install_crash_handler(where: str, show_dialog: bool | None = None) -> None:
    """Catch errors nothing else caught, in the main thread and in other threads.

    ``show_dialog`` defaults to True only in the installed app, where there is
    no console to show the error in. Calling it again just updates ``where``.
    """
    global _installed_for
    if show_dialog is None:
        show_dialog = bool(getattr(sys, "frozen", False))
    _installed_for = where
    previous_hook = sys.excepthook

    def excepthook(exc_type, exc, tb):
        handle_exception(exc_type, exc, tb, where=_installed_for or where, show_dialog=show_dialog)
        # Still print it when there is a console (running from source, tests).
        if previous_hook not in (None, excepthook) and not getattr(previous_hook, "_download_sorter", False):
            try:
                previous_hook(exc_type, exc, tb)
            except Exception:
                pass

    def thread_hook(args):
        if args.exc_type is SystemExit:
            return
        thread_name = getattr(args.thread, "name", "a background task")
        # A failed background task doesn't close the app, so it is saved and
        # logged without interrupting the person.
        handle_exception(args.exc_type, args.exc_value, args.exc_traceback,
                         where=f"{_installed_for or where} ({thread_name})", show_dialog=False)

    excepthook._download_sorter = True  # type: ignore[attr-defined]
    if getattr(sys.excepthook, "_download_sorter", False):
        return  # already installed; ``where`` was updated above
    sys.excepthook = excepthook
    threading.excepthook = thread_hook
