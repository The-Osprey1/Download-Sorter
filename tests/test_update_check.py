"""The "newer version is available" notice: once a day, silent on failure, optional."""

from __future__ import annotations

import io
import json

import pytest

import about
import update_check


@pytest.fixture
def checks_on(monkeypatch):
    monkeypatch.delenv(update_check.DISABLE_ENV, raising=False)


def _fake_fetch(version: str | None, calls: list):
    def fetch():
        calls.append(1)
        if version is None:
            return None
        return {"version": version, "url": "https://github.com/The-Osprey1/Download-Sorter/releases/tag/" + version}
    return fetch


@pytest.mark.parametrize("latest, current, newer", [
    ("v2.1.0", "2.0.2", True),
    ("v2.0.10", "2.0.9", True),
    ("2.0.2", "2.0.2", False),
    ("v2.0.1", "2.0.2", False),
    ("v3", "2.9.9", True),
    ("v2.1.0-beta", "2.1.0", False),
    ("nightly", "2.0.2", False),
    (None, "2.0.2", False),
])
def test_version_comparison(latest, current, newer):
    assert update_check.is_newer(latest, current) is newer


def test_finds_a_newer_release(checks_on):
    calls: list = []
    release = update_check.check({}, now=1_000_000, fetch=_fake_fetch("v99.0.0", calls))
    assert release["version"] == "v99.0.0"
    assert update_check.display_version(release) == "99.0.0"
    assert calls == [1]


def test_says_nothing_when_up_to_date(checks_on):
    calls: list = []
    assert update_check.check({}, now=1_000_000, fetch=_fake_fetch("v" + about.__version__, calls)) is None


def test_asks_github_at_most_once_a_day(checks_on):
    calls: list = []
    fetch = _fake_fetch("v99.0.0", calls)
    update_check.check({}, now=1_000_000, fetch=fetch)
    again = update_check.check({}, now=1_000_000 + 3600, fetch=fetch)
    assert calls == [1], "the second check within a day must not ask GitHub"
    assert again["version"] == "v99.0.0", "it still answers from the last check"
    update_check.check({}, now=1_000_000 + update_check.CHECK_EVERY_SECONDS + 1, fetch=fetch)
    assert calls == [1, 1]


def test_failure_is_silent_and_waits_a_day(checks_on):
    calls: list = []
    assert update_check.check({}, now=1_000_000, fetch=_fake_fetch(None, calls)) is None
    assert update_check.check({}, now=1_000_000 + 60, fetch=_fake_fetch(None, calls)) is None
    assert calls == [1]


def test_switched_off_means_no_request(checks_on):
    calls: list = []
    assert update_check.check({"update_check": {"enabled": False}}, now=1, fetch=_fake_fetch("v99.0.0", calls)) is None
    assert calls == []


def test_tests_never_reach_the_network_by_default():
    # conftest.py sets DOWNLOAD_SORTER_NO_UPDATE_CHECK=1 for the whole suite.
    assert update_check.is_enabled({}) is False


def test_tray_mentions_each_version_once(checks_on):
    release = {"version": "v99.0.0", "url": "https://github.com/x"}
    assert update_check.should_notify(release) is True
    assert update_check.should_notify(release) is False
    assert update_check.should_notify({"version": "v99.1.0"}) is True


def test_cached_update_reads_the_last_check_without_the_network(checks_on):
    assert update_check.cached_update() is None
    update_check.check({}, now=1_000_000, fetch=_fake_fetch("v99.0.0", []))
    assert update_check.cached_update()["version"] == "v99.0.0"


def test_fetch_reads_githubs_answer(monkeypatch):
    import urllib.request

    seen = {}

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(request, timeout):
        seen["url"] = request.full_url
        seen["headers"] = dict(request.header_items())
        seen["timeout"] = timeout
        body = {"tag_name": "v2.5.0", "html_url": "https://github.com/The-Osprey1/Download-Sorter/releases/tag/v2.5.0",
                "draft": False, "prerelease": False}
        return Response(json.dumps(body).encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    release = update_check.fetch_latest()

    assert release == {"version": "v2.5.0",
                       "url": "https://github.com/The-Osprey1/Download-Sorter/releases/tag/v2.5.0"}
    assert seen["url"] == update_check.RELEASES_API
    assert seen["timeout"] <= 10
    user_agent = seen["headers"].get("User-agent", "")
    assert user_agent.startswith("Download-Sorter/"), "only the app name and version are sent"


def test_fetch_ignores_odd_answers(monkeypatch):
    import urllib.request

    def failing(request, timeout):
        raise OSError("offline")

    monkeypatch.setattr(urllib.request, "urlopen", failing)
    assert update_check.fetch_latest() is None


def test_fetch_never_links_outside_github(monkeypatch):
    import urllib.request

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(urllib.request, "urlopen", lambda r, timeout: Response(
        json.dumps({"tag_name": "v9.0.0", "html_url": "https://example.com/evil"}).encode()))
    assert update_check.fetch_latest()["url"] == update_check.RELEASES_PAGE


# ---------------------------------------------------------------------------
# Where the user sees it
# ---------------------------------------------------------------------------

def test_home_shows_the_notice(qapp, monkeypatch, checks_on, tmp_path):
    pytest.importorskip("PySide6")
    import config_manager
    import onboarding
    import sorter

    cfg = sorter.get_default_config()
    cfg["downloads_dir"] = str(tmp_path)
    cfg["watch"]["folder"] = str(tmp_path)
    monkeypatch.setattr(config_manager, "load_sorter_config", lambda *a, **k: cfg)
    monkeypatch.setattr(update_check, "check", lambda config: {"version": "v99.0.0", "url": "https://github.com/x"})

    home = onboarding.HomeDashboardWidget(config=cfg)
    try:
        home.show()
        qapp.processEvents()
        home._on_update_ready(update_check.check(cfg))
        assert "Download Sorter 99.0.0 is available" in home._update_banner.text()
        assert home._update_banner.action_text() == "Download"
    finally:
        home.close()


def test_settings_switch_saves_the_choice(qapp, monkeypatch):
    pytest.importorskip("PySide6")
    import onboarding

    saved: list[dict] = []
    monkeypatch.setattr(onboarding.suite_data, "save_sorter_config", lambda cfg, *a, **k: saved.append(json.loads(json.dumps(cfg))))
    widget = onboarding.SettingsWidget(config={})
    try:
        widget._chk_updates.setChecked(False)
        assert saved[-1]["update_check"]["enabled"] is False
    finally:
        widget.close()


def test_tray_tooltip_stays_within_the_windows_limit(monkeypatch, checks_on):
    import tray

    monkeypatch.setattr(update_check, "cached_update", lambda: {"version": "v12.34.56"})
    monkeypatch.setattr(tray, "moves_today", lambda: 123456)

    class FakeTray:
        _held_count = lambda self: 999
    text = tray.SorterTray.tooltip(FakeTray())
    assert len(text) <= tray.TOOLTIP_MAX
    assert "Version 12.34.56 is available" in text
