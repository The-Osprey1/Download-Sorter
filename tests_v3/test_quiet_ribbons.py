"""Sorted-file ribbons stay away while a full-screen app, a presentation or the lock
screen is up. Files are still sorted and listed on Home; only the pop-up is skipped."""

from __future__ import annotations

import sys

import pytest

from download_sorter import quiet_time
from download_sorter.config import Config


class _FakeToasts:
    def __init__(self) -> None:
        self.shown: list[tuple[str, list]] = []

    def show_moves(self, batch_id, moves, *, duration_ms=None):  # noqa: ANN001
        self.shown.append((batch_id, moves))


class _FakeService:
    def __init__(self, cfg: Config) -> None:
        self.config = cfg


MOVES = [{"file_name": "a.pdf", "dest_path": "Documents/a.pdf"}]


def _handler(cfg: Config, quiet: bool):
    from download_sorter.ui.tray_app import make_moves_ribbon_handler

    toasts = _FakeToasts()
    handler = make_moves_ribbon_handler(_FakeService(cfg), toasts, is_quiet_time=lambda: quiet)
    return handler, toasts


def test_ribbon_shows_when_windows_accepts_notifications() -> None:
    handler, toasts = _handler(Config(), quiet=False)
    handler("b-1", MOVES)
    assert toasts.shown == [("b-1", MOVES)]


def test_ribbon_is_skipped_during_full_screen_or_presentation() -> None:
    handler, toasts = _handler(Config(), quiet=True)
    handler("b-1", MOVES)
    assert toasts.shown == []


def test_ribbon_is_skipped_when_turned_off_in_settings() -> None:
    handler, toasts = _handler(Config(notifications_enabled=False), quiet=False)
    handler("b-1", MOVES)
    assert toasts.shown == []


@pytest.mark.parametrize(
    ("state", "quiet"),
    [
        (1, True),   # screen locked or screen saver
        (2, True),   # a full-screen app
        (3, True),   # a full-screen game or video
        (4, True),   # presentation mode
        (5, False),  # accepts notifications
        (6, True),   # quiet hours after a new Windows setup
        (7, True),   # a full-screen Store app
    ],
)
def test_windows_states_that_hold_ribbons(state: int, quiet: bool) -> None:
    assert quiet_time.is_quiet_state(state) is quiet


def test_unknown_state_does_not_hold_ribbons() -> None:
    assert quiet_time.is_quiet_state(None) is False
    assert quiet_time.is_quiet_state(99) is False


def test_outside_windows_ribbons_are_never_held() -> None:
    if sys.platform == "win32":
        pytest.skip("asks the real desktop on Windows")
    assert quiet_time.notification_state() is None
    assert quiet_time.is_quiet_time() is False


@pytest.mark.skipif(sys.platform != "win32", reason="asks Windows")
def test_windows_reports_a_known_state() -> None:
    assert quiet_time.notification_state() in (1, 2, 3, 4, 5, 6, 7)
