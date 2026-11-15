"""The in-app update check and installer download (no real network anywhere)."""

from __future__ import annotations

import hashlib
import io
from pathlib import Path
import sys
import urllib.error

import pytest

from download_sorter import update_check, updater
from download_sorter.about import __version__
from download_sorter.config import Config, save_config
from download_sorter.paths import AppPaths
from download_sorter.service import SorterService

PREFIX = update_check.DOWNLOAD_PREFIX
SETUP = "DownloadSorter-Setup-v9.1.0-windows-x64.exe"
PAYLOAD = b"MZ fake installer bytes " * 4096


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_a, **_k):  # noqa: ANN002, ANN003
        raise AssertionError("a test tried to reach the network")

    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    monkeypatch.delenv(update_check.DISABLE_ENV, raising=False)
    monkeypatch.setattr(updater, "_sleep", lambda _s: None)


def _api_release(tag: str = "v9.1.0", **extra) -> dict:  # noqa: ANN003
    setup = update_check.installer_name(tag)
    return {
        "tag_name": tag,
        "html_url": f"https://github.com/{update_check.REPO}/releases/tag/{tag}",
        "draft": False,
        "prerelease": False,
        "assets": [
            {"name": setup, "browser_download_url": f"{PREFIX}{tag}/{setup}", "size": len(PAYLOAD)},
            {"name": "checksums.txt", "browser_download_url": f"{PREFIX}{tag}/checksums.txt", "size": 200},
            {"name": "DownloadSorter-v9.1.0-windows-x64.zip", "browser_download_url": f"{PREFIX}{tag}/x.zip"},
        ],
        **extra,
    }


# ---------------------------------------------------------------------------
# Versions and release parsing
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("latest", "current", "expected"),
    [
        ("v3.0.1", "3.0.0", True),
        ("3.1", "3.0.9", True),
        ("v4.0.0", "3.9.9", True),
        ("v3.0.0", "3.0.0", False),
        ("v2.8.0", "3.0.0", False),  # the 2.x release must never look like an update to v3
        ("v3.0.1-beta", "3.0.0", True),
        ("nonsense", "3.0.0", False),
        (None, "3.0.0", False),
    ],
)
def test_is_newer(latest: str | None, current: str, expected: bool) -> None:
    assert update_check.is_newer(latest, current) is expected


def test_running_version_is_the_default_comparison() -> None:
    assert update_check.is_newer(__version__) is False
    assert update_check.is_newer("v999.0.0") is True
    assert update_check.user_agent() == f"Download-Sorter/{__version__}"


def test_release_from_api_keeps_only_this_repos_installer_and_checksums() -> None:
    release = update_check.release_from_api(_api_release())
    assert release == {
        "version": "v9.1.0",
        "url": f"https://github.com/{update_check.REPO}/releases/tag/v9.1.0",
        "installer_url": f"{PREFIX}v9.1.0/{SETUP}",
        "installer_name": SETUP,
        "size": len(PAYLOAD),
        "checksums_url": f"{PREFIX}v9.1.0/checksums.txt",
    }
    assert update_check.display_version(release) == "9.1.0"


@pytest.mark.parametrize("flag", ["draft", "prerelease"])
def test_drafts_and_prereleases_are_never_offered(flag: str) -> None:
    assert update_check.release_from_api(_api_release(**{flag: True})) is None


def test_assets_hosted_elsewhere_are_ignored() -> None:
    data = _api_release()
    data["assets"][0]["browser_download_url"] = f"https://evil.example/{SETUP}"
    release = update_check.release_from_api(data)
    assert release is not None and "installer_url" not in release  # notice only, no self-install
    assert updater.has_installer(release) is False

    data = _api_release()
    data["assets"] = [a for a in data["assets"] if a["name"] != "checksums.txt"]
    assert updater.has_installer(update_check.release_from_api(data)) is False

    data = _api_release(html_url="https://evil.example/phish")
    assert update_check.release_from_api(data)["url"] == update_check.RELEASES_PAGE
    assert update_check.release_from_api({"tag_name": "latest"}) is None
    assert update_check.release_from_api(["not", "a", "dict"]) is None


# ---------------------------------------------------------------------------
# check(): throttling, opt-out, cache
# ---------------------------------------------------------------------------
def test_check_asks_at_most_once_a_day_and_answers_from_cache(tmp_path: Path) -> None:
    calls: list[int] = []
    newer = update_check.release_from_api(_api_release())

    def fetch() -> dict:
        calls.append(1)
        return dict(newer)

    day = update_check.CHECK_EVERY_SECONDS
    start = 1.7e9  # a real-looking clock: a first run always asks
    assert update_check.check(tmp_path, now=start, fetch=fetch) == newer
    assert update_check.check(tmp_path, now=start + day - 1, fetch=fetch) == newer
    assert len(calls) == 1, "second check inside 24 h must not ask GitHub again"
    assert update_check.cached_update(tmp_path) == newer

    assert update_check.check(tmp_path, now=start + day, fetch=fetch) == newer
    assert len(calls) == 2
    assert update_check.check(tmp_path, now=start + day + 5, fetch=fetch, force=True) == newer
    assert len(calls) == 3
    # A clock that jumped backwards must not silence the check forever.
    update_check.check(tmp_path, now=10.0, fetch=fetch)
    assert len(calls) == 4


def test_check_returns_nothing_when_up_to_date_or_offline(tmp_path: Path) -> None:
    same = {"version": f"v{__version__}", "url": update_check.RELEASES_PAGE}
    assert update_check.check(tmp_path, now=1.0e6, fetch=lambda: same) is None
    assert update_check.cached_update(tmp_path) is None

    offline_dir = tmp_path / "offline"
    assert update_check.check(offline_dir, now=1.0e6, fetch=lambda: None) is None
    assert update_check.load_state(offline_dir)["checked_at"] == 1.0e6  # try again tomorrow, not every minute


def test_check_respects_the_setting_and_the_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def must_not_fetch() -> dict:
        raise AssertionError("update check ran although it is switched off")

    assert update_check.check(tmp_path, enabled=False, fetch=must_not_fetch) is None
    monkeypatch.setenv(update_check.DISABLE_ENV, "1")
    assert update_check.is_enabled(True) is False
    assert update_check.check(tmp_path, enabled=True, fetch=must_not_fetch) is None
    # "Check now" (force) still works when the daily check is off.
    newer = update_check.release_from_api(_api_release())
    assert update_check.check(tmp_path, enabled=False, fetch=lambda: newer, force=True) == newer


def test_corrupt_state_file_is_ignored(tmp_path: Path) -> None:
    update_check.state_path(tmp_path).write_text("{not json", encoding="utf-8")
    assert update_check.load_state(tmp_path) == {}
    newer = update_check.release_from_api(_api_release())
    assert update_check.check(tmp_path, now=5.0e6, fetch=lambda: newer) == newer


def test_fetch_latest_failure_is_silent(monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.request

    def offline(*_a, **_k):  # noqa: ANN002, ANN003
        raise urllib.error.URLError("no network")

    monkeypatch.setattr(urllib.request, "urlopen", offline)
    assert update_check.fetch_latest() is None


# ---------------------------------------------------------------------------
# download(): verification before anything is run
# ---------------------------------------------------------------------------
class _Response(io.BytesIO):
    def __init__(self, body: bytes) -> None:
        super().__init__(body)
        self.headers = {"Content-Length": str(len(body))}

    def __enter__(self):  # noqa: ANN204
        return self

    def __exit__(self, *_exc) -> None:  # noqa: ANN002
        self.close()


def _serve(monkeypatch: pytest.MonkeyPatch, installer: bytes, checksums: str) -> list[str]:
    opened: list[str] = []

    def fake_open(url: str) -> _Response:
        if not url.startswith(PREFIX):
            raise updater.UpdateError("The update isn't hosted on Download Sorter's release page.")
        opened.append(url)
        return _Response(checksums.encode() if url.endswith("checksums.txt") else installer)

    monkeypatch.setattr(updater, "_open", fake_open)
    return opened


def _release() -> dict:
    release = update_check.release_from_api(_api_release())
    assert release is not None
    return release


def _checksums(body: bytes, name: str = SETUP) -> str:
    return f"{hashlib.sha256(body).hexdigest()}  {name}\n{'0' * 64}  other-file.zip\n"


def test_download_verifies_and_keeps_the_installer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    opened = _serve(monkeypatch, PAYLOAD, _checksums(PAYLOAD))
    progress: list[tuple[int, int]] = []
    path = updater.download(_release(), tmp_path, progress=lambda d, t: progress.append((d, t)))

    assert path == tmp_path / "updates" / SETUP and path.read_bytes() == PAYLOAD
    assert progress[0] == (0, len(PAYLOAD)) and progress[-1] == (len(PAYLOAD), len(PAYLOAD))
    assert not (tmp_path / "updates" / (SETUP + ".part")).exists()

    # Asked again: the checked file is reused, only checksums.txt is fetched.
    opened.clear()
    assert updater.download(_release(), tmp_path) == path
    assert [u.rsplit("/", 1)[-1] for u in opened] == ["checksums.txt"]


def test_download_that_does_not_match_the_checksum_is_deleted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tampered = b"X" + PAYLOAD[1:]  # same size, different bytes
    _serve(monkeypatch, tampered, _checksums(PAYLOAD))
    with pytest.raises(updater.UpdateError, match="didn't match"):
        updater.download(_release(), tmp_path)
    assert list((tmp_path / "updates").iterdir()) == [], "an unverified installer must not be left on disk"


def test_download_refuses_incomplete_missing_checksum_and_bad_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _serve(monkeypatch, PAYLOAD[:-100], _checksums(PAYLOAD[:-100]))
    with pytest.raises(updater.UpdateError, match="incomplete"):
        updater.download(_release(), tmp_path)

    _serve(monkeypatch, PAYLOAD, f"{'a' * 64}  some-other-file.exe\n")
    with pytest.raises(updater.UpdateError, match="doesn't list a checksum"):
        updater.download(_release(), tmp_path)

    for bad in ("..\\evil.exe", "sub/evil.exe", "setup.bat"):
        with pytest.raises(updater.UpdateError, match="name looks wrong"):
            updater.download({**_release(), "installer_name": bad}, tmp_path)
    with pytest.raises(updater.UpdateError, match="larger than expected"):
        updater.download({**_release(), "size": updater.MAX_INSTALLER_BYTES + 1}, tmp_path)
    with pytest.raises(updater.UpdateError, match="no installer"):
        updater.download({"version": "v9.1.0"}, tmp_path)
    assert not list((tmp_path / "updates").glob("*"))


def test_download_only_from_this_repos_release_files(tmp_path: Path) -> None:
    with pytest.raises(updater.UpdateError, match="isn't hosted"):
        updater._open("https://evil.example/DownloadSorter-Setup.exe")
    release = {**_release(), "checksums_url": "https://evil.example/checksums.txt"}
    with pytest.raises(updater.UpdateError, match="isn't hosted"):
        updater.download(release, tmp_path)


def test_download_retries_temporary_failures_and_can_be_cancelled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempts: list[str] = []
    good = _checksums(PAYLOAD)

    def flaky(url: str) -> _Response:
        attempts.append(url)
        if len(attempts) <= 2:
            raise urllib.error.HTTPError(url, 504, "Gateway Timeout", {}, None)  # type: ignore[arg-type]
        return _Response(good.encode() if url.endswith("checksums.txt") else PAYLOAD)

    monkeypatch.setattr(updater, "_open", flaky)
    assert updater.download(_release(), tmp_path).read_bytes() == PAYLOAD
    assert len(attempts) == 4  # checksums: 2 failures + 1 success, then the installer

    def gone(url: str) -> _Response:
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)  # type: ignore[arg-type]

    monkeypatch.setattr(updater, "_open", gone)
    with pytest.raises(updater.UpdateError, match="error 404"):
        updater.download(_release(), tmp_path / "other")

    _serve(monkeypatch, PAYLOAD, good)
    with pytest.raises(updater.Cancelled):
        updater.download(_release(), tmp_path / "cancelled", cancelled=lambda: True)
    assert not list((tmp_path / "cancelled" / "updates").glob("*"))


def test_clean_old_downloads_keeps_only_newer_installers(tmp_path: Path) -> None:
    folder = updater.updates_dir(tmp_path)
    folder.mkdir()
    keep = folder / "DownloadSorter-Setup-v999.0.0-windows-x64.exe"
    for item in (keep, folder / f"DownloadSorter-Setup-v{__version__}-windows-x64.exe",
                 folder / "DownloadSorter-Setup-v2.8.0-windows-x64.exe", folder / "half.exe.part", folder / "junk.txt"):
        item.write_bytes(b"x")
    updater.clean_old_downloads(tmp_path)
    assert [p.name for p in folder.iterdir()] == [keep.name]
    updater.clean_old_downloads(tmp_path / "does-not-exist")  # no folder: nothing to do


# ---------------------------------------------------------------------------
# Handing over to the installer
# ---------------------------------------------------------------------------
def test_installer_is_started_silently_in_update_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    installer = tmp_path / SETUP
    installer.write_bytes(PAYLOAD)
    target = tmp_path / "Programs" / "DownloadSorter"
    assert updater.installer_command(installer, target) == [str(installer), "/S", f"/D={target}", "/UPDATE"]

    started: list[tuple[list[str], dict]] = []
    monkeypatch.setattr(updater.subprocess, "Popen", lambda cmd, **kw: started.append((cmd, kw)))
    updater.start_install(installer, target)
    assert started[0][0] == [str(installer), "/S", f"/D={target}", "/UPDATE"]
    if sys.platform == "win32":
        assert started[0][1]["creationflags"] & 0x00000008, "must outlive the app it is about to replace"

    with pytest.raises(updater.UpdateError, match="missing"):
        updater.start_install(tmp_path / "nope.exe", target)
    # Running from source: never self-update.
    assert updater.install_dir() is None and updater.can_self_update() is False
    with pytest.raises(updater.UpdateError, match="Only the installed app"):
        updater.start_install(installer)
    assert len(started) == 1


# ---------------------------------------------------------------------------
# Setting and window
# ---------------------------------------------------------------------------
def test_check_for_updates_setting_round_trips_and_defaults_on() -> None:
    assert Config().check_for_updates is True
    assert Config.from_dict({}).check_for_updates is True  # configs written before the setting existed
    cfg = Config.from_dict({"check_for_updates": False})
    assert cfg.check_for_updates is False and cfg.to_dict()["check_for_updates"] is False


@pytest.fixture
def window(v3_qapp, app_paths: AppPaths):  # noqa: ANN001, ANN201
    from download_sorter.ui.main_window import MainWindow

    cfg = Config.with_starter_defaults()
    cfg.tutorial_completed = True
    save_config(cfg, app_paths)
    svc = SorterService(app_paths)
    win = MainWindow(svc)
    yield win
    win.close()
    svc.stop(timeout=1.0)


def test_window_never_checks_on_its_own_inside_tests(window, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(update_check, "check", lambda *a, **k: pytest.fail("checked during a test"))
    window.check_for_updates()
    assert window._update_release is None


def test_window_offers_download_page_when_it_cannot_self_update(window, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    newer = _release()
    monkeypatch.setattr(update_check, "fetch_latest", lambda *a, **k: dict(newer))
    opened: list[str] = []
    from download_sorter.ui import main_window as mw

    monkeypatch.setattr(mw.QDesktopServices, "openUrl", lambda url: opened.append(url.toString()))

    window.check_for_updates(force=True)  # not frozen -> cannot self-update
    assert window._update_release == newer
    assert "Download Sorter 9.1.0 is available" in window.banner._text.text()
    assert window.banner._action_btn.text() == "Open download page"
    window.banner._action()
    assert opened == [newer["url"]]


def test_window_downloads_verifies_and_starts_the_installer(
    window, app_paths: AppPaths, monkeypatch: pytest.MonkeyPatch  # noqa: ANN001
) -> None:
    newer = _release()
    monkeypatch.setattr(update_check, "fetch_latest", lambda *a, **k: dict(newer))
    monkeypatch.setattr(updater, "can_self_update", lambda: True)
    started: list[Path] = []
    monkeypatch.setattr(updater, "start_install", lambda path, folder=None: started.append(Path(path)))

    window.check_for_updates(force=True)
    assert window.banner._action_btn.text() == "Download and install"

    # 1st try: the file is tampered with -> nothing is started, the user can retry.
    _serve(monkeypatch, b"X" + PAYLOAD[1:], _checksums(PAYLOAD))
    window.banner._action()
    assert started == [] and window._update_downloading is False
    assert "didn't match" in window.banner._text.text()
    assert window.banner._action_btn.text() == "Try again"

    # 2nd try: a good download is verified and handed to the installer.
    _serve(monkeypatch, PAYLOAD, _checksums(PAYLOAD))
    window.banner._action()
    assert started == [app_paths.data_dir / "updates" / SETUP]
    assert "Installing Download Sorter 9.1.0" in window.banner._text.text()
    window._start_update_download()  # a second click while installing does nothing
    assert len(started) == 1


def test_window_setting_switches_the_daily_check_off(window, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    assert window.chk_check_updates.isChecked() is True
    window.chk_check_updates.setChecked(False)
    window._save_settings_form()
    assert window.service.config.check_for_updates is False

    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)  # behave like the real app
    monkeypatch.setattr(update_check, "check", lambda *a, **k: pytest.fail("checked although switched off"))
    window.check_for_updates()


def test_window_manual_check_for_updates_button_reports_up_to_date(
    window, monkeypatch: pytest.MonkeyPatch  # noqa: ANN001
) -> None:
    assert hasattr(window, "btn_check_updates")
    monkeypatch.setattr(
        update_check,
        "fetch_latest",
        lambda *a, **k: {
            "version": "v3.0.0",
            "url": "https://github.com/The-Osprey1/Download-Sorter/releases/tag/v3.0.0",
        },
    )
    window.btn_check_updates.click()
    assert f"You're on the latest version (v{update_check.__version__})." in window.banner._text.text()
    assert window.btn_check_updates.isEnabled() is True
