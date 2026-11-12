"""Keep the test run away from the Download Sorter the person is actually using.

The code under test closes running copies of the app, writes the "start with Windows" value and
the right-click menu entries, registers an installer, and creates the folders of the rules it is
given. All of that is shared with a real install: one named Windows event that every running copy
listens on, per-user registry keys, and the real Downloads folder. Without the fixtures below, a
full test run quits the tray that is open, replaces its startup and right-click entries with ones
that point at this checkout, overwrites its "Installed apps" entry (so Uninstall in Windows
Settings stops working), and leaves empty folders such as XYZFiles and Finance\\Taxes in Downloads.

The fixtures apply to every test on their own. tests/test_machine_isolation.py checks them.
"""

from __future__ import annotations

import os
import sys
import uuid

import pytest

import quit_signal

REAL_QUIT_EVENT = quit_signal.EVENT_NAME

# Where the registry writes of a test go instead (under HKEY_CURRENT_USER, deleted after the test).
SCRATCH_KEY = r"Software\Pytest\DownloadSorter"

# The per-user registry paths the app or its installer writes, and the tests' own scratch keys.
# "User Shell Folders" is only read, but it is where sorter.watch_root finds the real Downloads
# folder when the one in the settings does not exist (yet), so a test must not see it either.
# Everything else is read from the real registry as usual.
_REDIRECTED = (
    r"software\microsoft\windows\currentversion\run",
    r"software\microsoft\windows\currentversion\uninstall",
    r"software\microsoft\windows\currentversion\explorer\user shell folders",
    r"software\classes",
    r"software\downloadsorter",
)


@pytest.fixture(autouse=True)
def own_quit_event(monkeypatch):
    """Give each test a quit event of its own, so it can never close a copy that is really running.

    ``--quit`` (what the installer does before it replaces the app) sets one named event that every
    running copy waits on. Yields the real event's name, to compare against.
    """
    monkeypatch.setattr(
        quit_signal, "EVENT_NAME", f"{REAL_QUIT_EVENT}_pytest_{os.getpid()}_{uuid.uuid4().hex[:8]}"
    )
    yield REAL_QUIT_EVENT


@pytest.fixture(autouse=True)
def own_home_folder(monkeypatch, tmp_path_factory):
    """Give each test a home folder of its own, with an empty Downloads and Documents in it.

    Settings that name no Downloads folder fall back to ``${USERPROFILE}\\Downloads``. A test that
    built its own settings and ran the welcome wizard therefore created the wizard's rule folders
    (an empty XYZFiles and Finance\\Taxes, on every run) in the Downloads of whoever ran the tests.
    Yields the home folder.
    """
    home = tmp_path_factory.mktemp("home")
    for name in ("Downloads", "Documents"):
        (home / name).mkdir()
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    yield home


class RegistryScratch:
    """Where this test's registry writes went, and a way to look at the real keys."""

    def __init__(self, winreg_module, originals, root: str) -> None:
        self.root = root
        self._winreg = winreg_module
        self._originals = originals

    def _values(self, path: str):
        winreg = self._winreg
        try:
            with self._originals["OpenKey"](winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_READ) as key:
                values, index = {}, 0
                while True:
                    try:
                        name, value, _kind = self._originals["EnumValue"](key, index)
                    except OSError:
                        return values
                    values[name] = value
                    index += 1
        except OSError:
            return None

    def real(self, path: str):
        """Values (name -> value) of the key as it is on this PC, or None when it does not exist."""
        return self._values(path)

    def scratch(self, path: str):
        """Values of the key the code under test wrote to in this test's scratch area, or None."""
        return self._values(rf"{self.root}\{path}")


def _delete_key_tree(winreg, originals, path: str) -> None:
    """Delete a key and everything under it with the real functions (not the redirecting ones)."""
    try:
        with originals["OpenKey"](winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_READ) as key:
            children, index = [], 0
            while True:
                try:
                    children.append(originals["EnumKey"](key, index))
                    index += 1
                except OSError:
                    break
    except OSError:
        return
    for child in children:
        _delete_key_tree(winreg, originals, rf"{path}\{child}")
    try:
        originals["DeleteKey"](winreg.HKEY_CURRENT_USER, path)
    except OSError:
        pass


@pytest.fixture(autouse=True)
def registry_scratch(monkeypatch):
    """Send the registry writes of a test to a scratch area instead of the real keys.

    The startup value, the right-click entries, the "Installed apps" entry and the tests' own
    ``Software\\DownloadSorter...`` keys are opened under a per-test key that is deleted afterwards.
    Code that runs in another process (the setup program) cannot be redirected, so a test that
    starts it has to pass ``/NO-REGISTRY``. Yields a RegistryScratch (None off Windows).
    """
    if sys.platform != "win32":
        yield None
        return
    import winreg

    originals = {
        name: getattr(winreg, name)
        for name in ("OpenKey", "OpenKeyEx", "CreateKey", "CreateKeyEx", "DeleteKey", "DeleteKeyEx",
                     "EnumKey", "EnumValue")
        if hasattr(winreg, name)
    }
    root = rf"{SCRATCH_KEY}\{os.getpid()}_{uuid.uuid4().hex[:8]}"

    def scratch_path(sub_key):
        if isinstance(sub_key, str) and sub_key.lower().lstrip("\\").startswith(_REDIRECTED):
            return rf"{root}\{sub_key.lstrip(chr(92))}"
        return sub_key

    def redirecting(function):
        def call(key, sub_key, *args, **kwargs):
            if isinstance(key, int) and key == winreg.HKEY_CURRENT_USER:  # a handle from an earlier open is left alone
                sub_key = scratch_path(sub_key)
            return function(key, sub_key, *args, **kwargs)
        return call

    for name in ("OpenKey", "OpenKeyEx", "CreateKey", "CreateKeyEx", "DeleteKey", "DeleteKeyEx"):
        if name in originals:
            monkeypatch.setattr(winreg, name, redirecting(originals[name]))

    yield RegistryScratch(winreg, originals, root)

    _delete_key_tree(winreg, originals, root)
    try:  # the shared parent goes too, unless another test run is using it right now
        originals["DeleteKey"](winreg.HKEY_CURRENT_USER, SCRATCH_KEY)
        originals["DeleteKey"](winreg.HKEY_CURRENT_USER, SCRATCH_KEY.rsplit("\\", 1)[0])
    except OSError:
        pass
