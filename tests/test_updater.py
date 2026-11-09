"""In-app updates: find the installer, download it, check it, hand over."""

from __future__ import annotations

import hashlib
import io
import sys
from pathlib import Path

import pytest

import update_check
import updater

PREFIX = update_check.DOWNLOAD_PREFIX + "v9.0.0/"
NAME = "DownloadSorter-Setup-v9.0.0-windows-x64.exe"
PAYLOAD = b"MZ" + b"installer bytes " * 50_000  # about 800 KB


def _release(payload: bytes = PAYLOAD, size: int | None = None) -> dict:
    return {
        "version": "v9.0.0",
        "url": "https://github.com/The-Osprey1/Download-Sorter/releases/tag/v9.0.0",
        "installer_url": PREFIX + NAME,
        "installer_name": NAME,
        "size": len(payload) if size is None else size,
        "checksums_url": PREFIX + "checksums.txt",
    }


class _Response(io.BytesIO):
    def __init__(self, data: bytes):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def fake_github(monkeypatch):
    """Serve an installer and its checksums.txt; returns a dict to tweak them."""
    served = {"installer": PAYLOAD, "checksum": hashlib.sha256(PAYLOAD).hexdigest(), "opened": []}

    def fake_open(url):
        served["opened"].append(url)
        if url.endswith("checksums.txt"):
            text = f"{served['checksum']}  {NAME}\nabc  DownloadSorter-v9.0.0-windows-x64.zip\n"
            return _Response(text.encode())
        return _Response(served["installer"])

    monkeypatch.setattr(updater, "_open", fake_open)
    return served


# ---------------------------------------------------------------------------
# Picking the files from GitHub's answer
# ---------------------------------------------------------------------------

def _asset(name, url=None, size=1234):
    return {"name": name, "browser_download_url": url or PREFIX + name, "size": size}


def test_finds_the_installer_and_checksums():
    found = update_check._installer_assets(
        [_asset("DownloadSorter-v9.0.0-windows-x64.zip"), _asset(NAME, size=66_000_000), _asset("checksums.txt")],
        "v9.0.0",
    )
    assert found == {"installer_url": PREFIX + NAME, "installer_name": NAME, "size": 66_000_000,
                     "checksums_url": PREFIX + "checksums.txt"}


@pytest.mark.parametrize("assets", [
    [_asset(NAME)],                                                       # no checksums.txt
    [_asset("checksums.txt")],                                            # no installer
    [_asset("DownloadSorter-Setup-v8.0.0-windows-x64.exe"), _asset("checksums.txt")],  # other version
    [_asset(NAME, url="https://example.com/" + NAME), _asset("checksums.txt")],        # other site
    [_asset(NAME, url="https://github.com/someone-else/fork/releases/download/v9.0.0/" + NAME),
     _asset("checksums.txt")],                                            # other repository
    "not a list",
])
def test_ignores_anything_unexpected(assets):
    assert update_check._installer_assets(assets, "v9.0.0") == {}


# ---------------------------------------------------------------------------
# Downloading and checking
# ---------------------------------------------------------------------------

def test_download_checks_and_keeps_the_installer(fake_github):
    seen = []
    path = updater.download(_release(), progress=lambda done, total: seen.append((done, total)))
    assert path.name == NAME
    assert path.read_bytes() == PAYLOAD
    assert not path.with_name(NAME + ".part").exists()
    assert seen[0] == (0, len(PAYLOAD)) and seen[-1] == (len(PAYLOAD), len(PAYLOAD))
    assert fake_github["opened"][0].endswith("checksums.txt"), "the checksum is fetched first"


def test_a_file_that_does_not_match_is_deleted(fake_github):
    fake_github["installer"] = PAYLOAD[:-1] + b"X"
    with pytest.raises(updater.UpdateError, match="didn't match"):
        updater.download(_release(size=len(PAYLOAD)))
    assert list(updater.updates_dir().iterdir()) == []


def test_an_incomplete_download_is_deleted(fake_github):
    fake_github["installer"] = PAYLOAD[: len(PAYLOAD) // 2]
    with pytest.raises(updater.UpdateError, match="incomplete"):
        updater.download(_release(size=len(PAYLOAD)))
    assert list(updater.updates_dir().iterdir()) == []


def test_missing_checksum_line_stops_the_download(fake_github):
    fake_github["checksum"] = "not-a-hash"
    with pytest.raises(updater.UpdateError, match="checksum"):
        updater.download(_release())
    assert not (updater.updates_dir() / NAME).exists()


def test_cancel_removes_the_partial_file(fake_github):
    with pytest.raises(updater.Cancelled):
        updater.download(_release(), cancelled=lambda: True)
    assert list(updater.updates_dir().iterdir()) == []


def test_network_error_becomes_a_plain_message(monkeypatch):
    def broken(url):
        raise OSError("connection reset")
    monkeypatch.setattr(updater, "_open", broken)
    with pytest.raises(updater.UpdateError, match="Couldn't download"):
        updater.download(_release())


def test_refuses_links_outside_the_release_page():
    with pytest.raises(updater.UpdateError):
        updater._open("https://example.com/" + NAME)


@pytest.mark.parametrize("name", ["..\\evil.exe", "../evil.exe", "notes.txt"])
def test_refuses_odd_file_names(fake_github, name):
    release = _release()
    release["installer_name"] = name
    with pytest.raises(updater.UpdateError):
        updater.download(release)


def test_checksum_lookup():
    text = f"{'a' * 64}  {NAME}\n{'b' * 64} *other.zip\n"
    assert updater.expected_sha256(text, NAME) == "a" * 64
    assert updater.expected_sha256(text, "other.zip") == "b" * 64
    assert updater.expected_sha256(text, "missing.exe") is None


# ---------------------------------------------------------------------------
# Handing over
# ---------------------------------------------------------------------------

def test_only_the_installed_app_can_update_itself(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert updater.install_dir() is None
    assert updater.can_self_update() is False


def test_start_install_passes_folder_and_update_flag(monkeypatch, tmp_path):
    installer = tmp_path / NAME
    installer.write_bytes(b"MZ")
    started = {}

    def fake_popen(cmd, **kwargs):
        started["cmd"] = cmd
        started["kwargs"] = kwargs

    monkeypatch.setattr(updater.subprocess, "Popen", fake_popen)
    updater.start_install(installer, tmp_path / "Programs" / "DownloadSorter")

    assert started["cmd"] == [str(installer), "/S", f"/D={tmp_path / 'Programs' / 'DownloadSorter'}", "/UPDATE"]


def test_start_install_needs_the_file(tmp_path):
    with pytest.raises(updater.UpdateError):
        updater.start_install(tmp_path / "missing.exe", tmp_path)


def test_old_downloads_are_cleaned_up():
    folder = updater.updates_dir()
    folder.mkdir(parents=True)
    old = folder / "DownloadSorter-Setup-v0.1.0-windows-x64.exe"
    newer = folder / "DownloadSorter-Setup-v99.0.0-windows-x64.exe"
    partial = folder / "DownloadSorter-Setup-v99.0.0-windows-x64.exe.part"
    for f in (old, newer, partial):
        f.write_bytes(b"x")

    updater.clean_old_downloads()

    assert not old.exists() and not partial.exists()
    assert newer.exists(), "an installer for a newer version is kept for Restart and install"


# ---------------------------------------------------------------------------
# Asking a running copy to quit
# ---------------------------------------------------------------------------

def test_quit_signal_does_nothing_off_windows(monkeypatch):
    import quit_signal

    monkeypatch.setattr(quit_signal.sys, "platform", "linux")
    assert quit_signal.request_quit() is False
    listener = quit_signal.QuitListener()
    assert listener.requested() is False
    assert listener.watch(lambda: None, __import__("threading").Event()) is None


@pytest.mark.skipif(sys.platform != "win32", reason="named events are a Windows feature")
def test_quit_signal_reaches_a_listener():
    import threading

    import quit_signal

    listener = quit_signal.QuitListener(reset=True)
    try:
        fired = threading.Event()
        listener.watch(fired.set, threading.Event())
        assert quit_signal.request_quit() is True
        assert fired.wait(5)
    finally:
        listener.close()


def test_main_quit_command(monkeypatch):
    import main
    import quit_signal

    calls = []
    monkeypatch.setattr(quit_signal, "request_quit", lambda: calls.append(1) or True)
    assert main.main(["--quit"]) == 0
    assert calls == [1]


def test_tray_closes_on_request_without_turning_sorting_off():
    import tray

    class FakeIcon:
        stopped = False

        def stop(self):
            self.stopped = True

    class FakeTray:
        stop_event = __import__("threading").Event()
        icon = FakeIcon()

    fake = FakeTray()
    tray.SorterTray.on_quit_requested(fake)
    assert fake.stop_event.is_set() and fake.icon.stopped
    assert not tray.SERVICE_DISABLED_FILE.exists(), "an update must not leave sorting switched off"


# ---------------------------------------------------------------------------
# GitHub's download server sometimes answers 504 or drops the connection
# ---------------------------------------------------------------------------

def _http_error(code: int):
    import urllib.error

    return urllib.error.HTTPError(PREFIX + NAME, code, "error", {}, None)


@pytest.fixture
def no_waiting(monkeypatch):
    waits = []
    monkeypatch.setattr(updater, "_sleep", waits.append)
    return waits


def test_a_504_is_tried_again(monkeypatch, fake_github, no_waiting):
    answer = updater._open
    failures = [_http_error(504)]

    def flaky(url):
        if not url.endswith("checksums.txt") and failures:
            raise failures.pop()
        return answer(url)

    monkeypatch.setattr(updater, "_open", flaky)
    path = updater.download(_release())
    assert path.read_bytes() == PAYLOAD
    assert no_waiting == [updater.RETRY_WAITS[0]]


def test_a_connection_dropped_mid_download_starts_again(monkeypatch, fake_github, no_waiting):
    answer = updater._open
    calls = []

    class Dropping(io.BytesIO):
        headers = {"Content-Length": str(len(PAYLOAD))}

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self, size=-1):
            data = super().read(size)
            if self.tell() > len(PAYLOAD) // 2:
                raise ConnectionResetError("connection reset")
            return data

    def flaky(url):
        if not url.endswith("checksums.txt"):
            calls.append(url)
            if len(calls) == 1:
                return Dropping(PAYLOAD)
        return answer(url)

    monkeypatch.setattr(updater, "_open", flaky)
    path = updater.download(_release())
    assert path.read_bytes() == PAYLOAD
    assert len(calls) == 2


def test_gives_up_after_three_tries_and_says_why(monkeypatch, no_waiting):
    tries = []

    def always_504(url):
        tries.append(url)
        raise _http_error(504)

    monkeypatch.setattr(updater, "_open", always_504)
    with pytest.raises(updater.UpdateError, match="error 504"):
        updater.download(_release())
    assert len(tries) == 3
    assert list(updater.updates_dir().iterdir()) == []


def test_a_missing_file_is_not_tried_again(monkeypatch, no_waiting):
    tries = []

    def not_found(url):
        tries.append(url)
        raise _http_error(404)

    monkeypatch.setattr(updater, "_open", not_found)
    with pytest.raises(updater.UpdateError, match="error 404"):
        updater.download(_release())
    assert len(tries) == 1 and no_waiting == []


def test_an_installer_already_downloaded_and_checked_is_reused(fake_github):
    updater.download(_release())
    fake_github["opened"].clear()

    seen = []
    path = updater.download(_release(), progress=lambda done, total: seen.append((done, total)))

    assert path.read_bytes() == PAYLOAD
    assert fake_github["opened"] == [PREFIX + "checksums.txt"], "only the checksum is fetched again"
    assert seen == [(len(PAYLOAD), len(PAYLOAD))]


def test_an_old_file_that_does_not_match_is_downloaded_again(fake_github):
    updater.updates_dir().mkdir(parents=True, exist_ok=True)
    (updater.updates_dir() / NAME).write_bytes(b"something else")

    path = updater.download(_release())

    assert path.read_bytes() == PAYLOAD
