"""Animations run when motion is allowed and are skipped when it isn't."""

from __future__ import annotations

import time

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QLabel, QTableWidget, QTableWidgetItem, QWidget

import ui_motion


def _run_events(qapp, ms: int) -> None:
    deadline = time.monotonic() + ms / 1000
    while time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)


@pytest.fixture
def motion_on(monkeypatch):
    monkeypatch.setenv(ui_motion.REDUCED_MOTION_ENV, "0")
    monkeypatch.setattr(ui_motion.sys, "platform", "linux")


@pytest.fixture
def motion_off(monkeypatch):
    monkeypatch.setenv(ui_motion.REDUCED_MOTION_ENV, "1")


def test_reduced_motion_setting_is_respected(motion_off):
    assert ui_motion.animations_enabled() is False


def test_motion_is_on_by_default(motion_on):
    assert ui_motion.animations_enabled() is True


def test_count_up_ends_on_the_new_number(qapp, motion_on):
    label = QLabel("0 files")
    ui_motion.count_up(label, 0, 12, lambda n: f"{n} files", duration=120)
    _run_events(qapp, 300)
    assert label.text() == "12 files"


def test_count_up_is_instant_without_motion(qapp, motion_off):
    label = QLabel("0 files")
    ui_motion.count_up(label, 0, 12, lambda n: f"{n} files")
    assert label.text() == "12 files"


def test_breathe_starts_and_stops(qapp, motion_on):
    dot = QWidget()
    ui_motion.breathe(dot, True)
    assert ui_motion.is_breathing(dot)
    ui_motion.breathe(dot, False)
    assert not ui_motion.is_breathing(dot)
    assert dot.graphicsEffect() is None or dot.graphicsEffect().opacity() == 1.0


def test_breathe_does_nothing_without_motion(qapp, motion_off):
    dot = QWidget()
    ui_motion.breathe(dot, True)
    assert not ui_motion.is_breathing(dot)


def test_flash_rows_highlights_then_clears(qapp, motion_on):
    table = QTableWidget(2, 1)
    table.setItem(0, 0, QTableWidgetItem("new"))
    table.setItem(1, 0, QTableWidgetItem("old"))
    table.show()
    qapp.processEvents()
    ui_motion.flash_rows(table, [0], duration=120)
    assert ui_motion.flashing_rows(table) == 1
    _run_events(qapp, 300)
    assert ui_motion.flashing_rows(table) == 0
    table.close()


def test_flash_rows_does_nothing_without_motion(qapp, motion_off):
    table = QTableWidget(1, 1)
    table.setItem(0, 0, QTableWidgetItem("new"))
    ui_motion.flash_rows(table, [0])
    assert ui_motion.flashing_rows(table) == 0


def test_fade_in_removes_its_effect_when_done(qapp, motion_on):
    page = QWidget()
    page.show()
    ui_motion.fade_in(page, duration=60)
    assert page.graphicsEffect() is not None
    _run_events(qapp, 250)
    assert page.graphicsEffect() is None
    page.close()


def test_banner_shows_action_and_hides_itself(qapp, motion_on):
    host = QWidget()
    banner = ui_motion.Banner(host)
    host.show()
    clicked = []
    banner.show_message("Sorted 3 files.", "success", "Undo", lambda: clicked.append(1), timeout_ms=100)
    assert banner.isVisible()
    assert banner.text() == "Sorted 3 files."
    assert banner.action_text() == "Undo"

    _run_events(qapp, 600)
    assert not banner.isVisible(), "the message should go away on its own"

    banner.show_message("Sorted 3 files.", "success", "Undo", lambda: clicked.append(1), timeout_ms=0)
    banner._action_btn.click()
    assert clicked == [1]
    host.close()


def test_banner_without_motion_hides_immediately(qapp, motion_off):
    host = QWidget()
    banner = ui_motion.Banner(host)
    host.show()
    banner.show_message("Paused.", "warning", timeout_ms=0)
    assert banner.isVisible()
    banner.dismiss()
    assert not banner.isVisible()
    host.close()
