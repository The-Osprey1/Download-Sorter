"""Download Sorter follows Windows' "Text size" setting (Settings > Accessibility >
Text size). Every font size in the app's styles was fixed in pixels, so the setting
had no effect."""

from __future__ import annotations

import sys

import pytest
from PySide6.QtWidgets import QLabel

from download_sorter.ui import text_scale


@pytest.fixture(autouse=True)
def _restore_text_scaling():
    yield
    text_scale.uninstall_text_scaling()


def test_styles_are_unchanged_at_normal_text_size() -> None:
    css = "QLabel { font-size: 12.5px; color: #FFF; } QPushButton { font-size: 11px; }"
    assert text_scale.scale_font_sizes(css, 1.0) == css


def test_font_sizes_grow_with_the_text_size_setting() -> None:
    css = "QLabel { font-size: 12px; } QPushButton { font-size:11.5px; padding: 4px 8px; }"
    scaled = text_scale.scale_font_sizes(css, 1.5)
    assert "font-size: 18px" in scaled
    assert "font-size: 17px" in scaled
    assert "padding: 4px 8px" in scaled  # only font sizes change


def test_sizes_in_points_are_left_alone() -> None:
    assert text_scale.scale_font_sizes("font-size: 10pt;", 1.5) == "font-size: 10pt;"


@pytest.mark.parametrize(
    ("stored", "factor"),
    [(None, 1.0), (100, 1.0), (125, 1.25), (150, 1.5), (225, text_scale.MAX_TEXT_SCALE), (90, 1.0), ("x", 1.0)],
)
def test_windows_setting_becomes_a_scale_factor(stored, factor: float) -> None:
    assert text_scale.read_windows_text_scale(lambda: stored) == pytest.approx(factor)


def test_outside_windows_text_is_normal_size() -> None:
    if sys.platform == "win32":
        pytest.skip("reads the real setting on Windows")
    assert text_scale.read_windows_text_scale() == 1.0


@pytest.mark.skipif(sys.platform != "win32", reason="reads the Windows setting")
def test_windows_setting_can_be_read() -> None:
    assert 1.0 <= text_scale.read_windows_text_scale() <= text_scale.MAX_TEXT_SCALE


def test_every_style_set_after_install_is_scaled(v3_qapp) -> None:
    text_scale.install_text_scaling(1.25)
    label = QLabel("x")
    label.setStyleSheet("font-size: 12px;")
    assert label.styleSheet() == "font-size: 15px;"
    assert text_scale.current_text_scale() == 1.25


def test_installing_twice_does_not_scale_twice(v3_qapp) -> None:
    text_scale.install_text_scaling(1.5)
    text_scale.install_text_scaling(1.5)
    label = QLabel("x")
    label.setStyleSheet("font-size: 10px;")
    assert label.styleSheet() == "font-size: 15px;"


def test_uninstall_restores_normal_styles(v3_qapp) -> None:
    text_scale.install_text_scaling(1.5)
    text_scale.uninstall_text_scaling()
    label = QLabel("x")
    label.setStyleSheet("font-size: 10px;")
    assert label.styleSheet() == "font-size: 10px;"
    assert text_scale.current_text_scale() == 1.0


def test_scaled_px_rounds_to_whole_pixels() -> None:
    assert text_scale.scaled_px(108, 1.25) == 135
    assert text_scale.scaled_px(108, 1.0) == 108


def test_header_buttons_fold_to_icons_sooner_with_bigger_text(service_instance, v3_qapp) -> None:
    from download_sorter.ui.main_window import MainWindow

    win = MainWindow(service_instance)
    win.resize(1100, 760)
    win._fit_to_window()
    assert win.btn_pause.is_collapsed() is False
    win.close()

    text_scale.install_text_scaling(1.5)
    big = MainWindow(service_instance)
    big.resize(1100, 760)
    big._fit_to_window()
    # With 1.5x text the full labels no longer fit at this width, so they fold to icons.
    assert big.btn_pause.is_collapsed() is True
    big.close()


def test_folder_template_filter_pills_are_scaled_once(v3_qapp) -> None:
    from download_sorter.ui.architecture_canvas import TemplateShelfWidget

    text_scale.install_text_scaling(1.5)
    shelf = TemplateShelfWidget()
    pill = shelf._filter_btns["all"]
    # The pill's 11.5px at 150% is 17px; reading the style back and setting it
    # again scaled it a second time, to 26px.
    assert "font-size: 17px" in pill.styleSheet()
    assert "font-size: 26px" not in pill.styleSheet()
    shelf.close()


def test_table_columns_and_rows_grow_with_the_text(service_instance, v3_qapp) -> None:
    from download_sorter.ui.main_window import MainWindow

    text_scale.install_text_scaling(1.5)
    win = MainWindow(service_instance)
    assert win.tbl_sim.columnWidth(1) == 222  # 148 at 100%
    assert win.tbl_rules.columnWidth(1) == 270  # 180 at 100%
    assert win.tbl_sim.verticalHeader().defaultSectionSize() == 60  # 40 at 100%
    win.close()
