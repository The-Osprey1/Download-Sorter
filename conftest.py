"""Keep the complete test suite isolated from the user's live sorter state."""

from __future__ import annotations

import os
from pathlib import Path
import sys

if sys.platform == "win32":
    base = sys.base_prefix
    tcl_cand = os.path.join(base, "tcl", "tcl8.6").replace("\\", "/")
    tk_cand = os.path.join(base, "tcl", "tk8.6").replace("\\", "/")
    if os.path.exists(tcl_cand):
        os.environ["TCL_LIBRARY"] = tcl_cand
    if os.path.exists(tk_cand):
        os.environ["TK_LIBRARY"] = tk_cand

import pytest

# Animations finish instantly in tests, so labels show their final values at
# once. tests/test_ui_motion.py turns them back on where it checks them.
os.environ.setdefault("DOWNLOAD_SORTER_REDUCED_MOTION", "1")
# Never ask GitHub for the latest version during tests (tests/test_update_check.py
# turns it back on with a fake network).
os.environ.setdefault("DOWNLOAD_SORTER_NO_UPDATE_CHECK", "1")
# The welcome steps ask before the first big sort; tests answer "Sort them".
os.environ.setdefault("DOWNLOAD_SORTER_NO_DIALOGS", "1")


# pystray's Windows backend registers a window class named after id(icon) when
# an Icon is created, and only unregisters it when the icon has run.  The tests
# build many tray icons without running them, so a later icon that Python puts
# at a reused address fails with "WinError 1410: Class already exists".  Keeping
# every test-created icon alive means no address (and so no class name) is reused.
# The app itself creates one icon per process, so this only affects tests.
_TEST_TRAY_ICONS: list = []

if sys.platform == "win32":
    try:
        import pystray._win32 as _pystray_win32
    except Exception:  # pystray missing or not importable: nothing to protect
        _pystray_win32 = None
    if _pystray_win32 is not None:
        _original_icon_init = _pystray_win32.Icon.__init__

        def _icon_init_kept_alive(self, *args, **kwargs):
            _TEST_TRAY_ICONS.append(self)
            _original_icon_init(self, *args, **kwargs)

        _pystray_win32.Icon.__init__ = _icon_init_kept_alive


@pytest.fixture(autouse=True)
def restore_error_hooks():
    """tray.main() installs Download Sorter's crash handler; don't let it outlive the test."""
    import threading
    saved = (sys.excepthook, threading.excepthook)
    yield
    sys.excepthook, threading.excepthook = saved


@pytest.fixture(autouse=True)
def isolate_sorter_state(tmp_path, monkeypatch):
    import sorter
    import watch

    state_dir = tmp_path / "sorter-state"
    paths = {
        "LOG_DIR": state_dir,
        "LOG_FILE": state_dir / "sorter.log",
        "HISTORY_FILE": state_dir / "history.jsonl",
        "PAUSE_FILE": state_dir / "paused",
        "FAILURE_FILE": state_dir / "failures.json",
        "ACTIVE_PROJECT_FILE": state_dir / "active_project.json",
        "RUNTIME_STATE_FILE": state_dir / "runtime_state.json",
        "SERVICE_DISABLED_FILE": state_dir / "service_disabled",
        "USER_CONFIG_PATH": state_dir / "config.json",
        "CONFIG_PATH": state_dir / "config.json",
        "SORTER_CONFIG_FILE": state_dir / "config.json",
    }
    for name, path in paths.items():
        if hasattr(sorter, name):
            monkeypatch.setattr(sorter, name, path)

    sorter._failure_cache = None
    with watch.QUEUE_LOCK:
        watch.PENDING_DOWNLOADS.clear()
        watch.PENDING_DOCUMENTS.clear()
        watch.PENDING_MISPLACED.clear()
        watch.PENDING_DEADLINES.clear()
    watch.WAKE_EVENT.clear()
    for module_name in ("watch", "tray", "status", "sort_undo", "config_manager"):
        module = sys.modules.get(module_name)
        if module is None:
            continue
        for name, path in paths.items():
            if hasattr(module, name):
                monkeypatch.setattr(module, name, path)
    yield
    sorter._failure_cache = None
    with watch.QUEUE_LOCK:
        watch.PENDING_DOWNLOADS.clear()
        watch.PENDING_DOCUMENTS.clear()
        watch.PENDING_MISPLACED.clear()
        watch.PENDING_DEADLINES.clear()
    watch.WAKE_EVENT.clear()


@pytest.fixture(autouse=True)
def only_test_folders_collect_files(monkeypatch):
    """Keep the folders in the real Downloads of whoever runs the tests out of them.

    A folder the user made collects files named after it (sorter.my_folders). A
    test that sorts with the default settings would otherwise look at the real
    Downloads folder of this PC, and a folder there called "Benchy" would change
    where "benchy.stl" goes. Only folders inside the tests' temporary area count.
    """
    import tempfile

    import sorter

    real = sorter.my_folders
    temp_root = Path(tempfile.gettempdir()).resolve()

    def my_folders(config, root=None):
        try:
            target = Path(root) if root is not None else sorter.watch_root(config)
        except Exception:
            return []
        if not sorter.is_under(Path(target), temp_root):
            return []
        return real(config, root)

    monkeypatch.setattr(sorter, "my_folders", my_folders)


@pytest.fixture
def finished_downloads(monkeypatch):
    """Treat every existing file as finished downloading.

    For tests about where a file goes, not about waiting for a download to
    finish. On Windows a file written a moment ago can briefly look unfinished:
    antivirus may still have it open, which fails the exclusive-open check in
    sorter.is_file_locked (used by the readiness check and again by safe_ops
    just before moving). The real watcher simply tries again a moment later,
    but a test that moves the file at once would fail now and then.
    """
    import sorter

    monkeypatch.setattr(sorter, "is_file_ready_to_move", lambda path, settle: Path(path).is_file())
    monkeypatch.setattr(sorter, "is_file_locked", lambda path: False)


@pytest.fixture(scope="session")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is None:
            app = QApplication([])
        yield app
    except ImportError:
        yield None
