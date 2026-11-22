"""Recent File Insert keeps its pill above the Open/Upload window when that window is
dragged to another monitor.

Windows reports the window's position in physical pixels. Qt numbers each monitor from
its own starting point and scales only the distance from it, so dividing the whole
position by one scale factor (the old code) put the pill on the wrong monitor, or in
the gap between monitors, as soon as scaling wasn't 100%."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRect

from download_sorter.ui.quick_switcher_pill import ScreenInfo, native_to_logical, pill_position, screen_for_native_point

PILL_W, PILL_H = 360, 44

# Two 4K monitors at 150%, side by side: physical 3840 wide each.
LEFT_150 = ScreenInfo(geometry=QRect(0, 0, 2560, 1440), available=QRect(0, 0, 2560, 1392), dpr=1.5)
RIGHT_150 = ScreenInfo(geometry=QRect(3840, 0, 2560, 1440), available=QRect(3840, 0, 2560, 1392), dpr=1.5)

# A 1440p laptop at 100% with a 4K monitor at 150% on its right.
LAPTOP_100 = ScreenInfo(geometry=QRect(0, 0, 2560, 1440), available=QRect(0, 0, 2560, 1400), dpr=1.0)
MONITOR_150 = ScreenInfo(geometry=QRect(2560, 0, 2560, 1440), available=QRect(2560, 0, 2560, 1392), dpr=1.5)


def _inside(screen: ScreenInfo, x: int, y: int) -> bool:
    return screen.geometry.contains(x, y) and screen.geometry.contains(x + PILL_W - 1, y + PILL_H - 1)


def test_the_monitor_is_found_from_physical_pixels() -> None:
    screens = [LEFT_150, RIGHT_150]
    assert screen_for_native_point(100, 100, screens) is LEFT_150
    assert screen_for_native_point(3839, 100, screens) is LEFT_150
    assert screen_for_native_point(3840, 100, screens) is RIGHT_150
    assert screen_for_native_point(7000, 100, screens) is RIGHT_150


def test_positions_are_scaled_from_each_monitors_own_start() -> None:
    assert native_to_logical(3840 + 1500, 300, RIGHT_150) == (3840 + 1000, 200)
    assert native_to_logical(1500, 300, LEFT_150) == (1000, 200)


def test_pill_follows_a_window_dragged_to_the_second_150_percent_monitor() -> None:
    dialog = (3840 + 300, 450, 3840 + 1800, 1500)  # physical left, top, right, bottom
    x, y = pill_position(dialog, PILL_W, PILL_H, [LEFT_150, RIGHT_150])
    assert _inside(RIGHT_150, x, y)
    # Docked just above the window's top-right corner.
    assert (x, y) == (3840 + 1200 - PILL_W - 16, 300 - PILL_H - 6)


def test_pill_follows_a_window_onto_a_monitor_with_different_scaling() -> None:
    screens = [LAPTOP_100, MONITOR_150]
    on_laptop = pill_position((300, 400, 1300, 1100), PILL_W, PILL_H, screens)
    assert _inside(LAPTOP_100, *on_laptop)
    assert on_laptop == (1300 - PILL_W - 16, 400 - PILL_H - 6)

    on_monitor = pill_position((2560 + 600, 600, 2560 + 2100, 1800), PILL_W, PILL_H, screens)
    assert _inside(MONITOR_150, *on_monitor)
    assert on_monitor == (2560 + 1400 - PILL_W - 16, 400 - PILL_H - 6)


def test_window_at_the_top_edge_gets_the_pill_below_its_title_bar() -> None:
    x, y = pill_position((3840 + 300, 0, 3840 + 1800, 900), PILL_W, PILL_H, [LEFT_150, RIGHT_150])
    assert y == 0 + 36
    assert _inside(RIGHT_150, x, y)


def test_pill_stays_on_screen_when_the_window_hangs_off_the_right_edge() -> None:
    x, _y = pill_position((3840 + 3500, 450, 3840 + 4800, 900), PILL_W, PILL_H, [LEFT_150, RIGHT_150])
    assert x + PILL_W <= RIGHT_150.available.right() + 1 - 8


def test_checklist_moves_with_the_pill(v3_qapp, tmp_path: Path) -> None:
    from download_sorter.ui.quick_switcher_pill import QuickSwitcherPill

    files = []
    for name in ("a.pdf", "b.pdf"):
        f = tmp_path / name
        f.write_bytes(b"x")
        files.append({"path": str(f), "name": name})
    pill = QuickSwitcherPill(0, {"path": files[0]["path"], "name": "a.pdf", "items": files, "batch_count": 2})
    pill._expand_checklist()
    assert pill.checklist_win is not None
    pill._place(500, 300)
    assert (pill.root.x(), pill.root.y()) == (500, 300)
    assert (pill.checklist_win.x(), pill.checklist_win.y()) == (500, 300 + pill.root.height() + 4)
    pill.close()


def test_monitor_from_windows_becomes_qt_units() -> None:
    from download_sorter.ui.quick_switcher_pill import screen_from_monitor

    # A 4K monitor at 150% to the right of a 4K one: physical (3840, 0)-(7680, 2160).
    screen = screen_from_monitor((3840, 0, 7680, 2160), (3840, 0, 7680, 2088), 1.5)
    assert screen.geometry == QRect(3840, 0, 2560, 1440)
    assert screen.available == QRect(3840, 0, 2560, 1392)
    # A 100% monitor to the left of the main one, at negative coordinates.
    left = screen_from_monitor((-1920, 0, 0, 1080), (-1920, 0, 0, 1040), 1.0)
    assert left.geometry == QRect(-1920, 0, 1920, 1080)
    x, _y = pill_position((-1800, 200, -1000, 700), PILL_W, PILL_H, [left])
    assert x == -1000 - PILL_W - 16
