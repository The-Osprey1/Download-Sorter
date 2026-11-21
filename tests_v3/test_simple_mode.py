"""A new install shows a simpler window: Home, Custom Rules and Settings. Folder
Architecture and "Sort Lower Tiers" come back with one switch in Settings, and
setups from before this change keep everything they had."""

from __future__ import annotations

import pytest

from download_sorter.config import Config, load_config
from download_sorter.paths import AppPaths


def _arch_index(win) -> int:  # noqa: ANN001
    return win.tabs.indexOf(win.arch_tab)


@pytest.mark.shipped_defaults
def test_a_new_install_hides_the_advanced_tools(app_paths: AppPaths) -> None:
    assert load_config(app_paths).show_advanced_tools is False


def test_existing_setups_keep_the_advanced_tools() -> None:
    # A config.json saved before this setting existed has no such key.
    assert Config.from_dict({}).show_advanced_tools is True
    assert Config().show_advanced_tools is True


def test_the_choice_is_saved() -> None:
    cfg = Config(show_advanced_tools=False)
    assert Config.from_dict(cfg.to_dict()).show_advanced_tools is False


def test_simple_window_hides_folder_architecture_and_sort_lower_tiers(service_instance, v3_qapp) -> None:
    from download_sorter.ui.main_window import MainWindow

    cfg = service_instance.config
    cfg.show_advanced_tools = False
    service_instance.update_config(cfg)
    win = MainWindow(service_instance)
    assert win.tabs.isTabVisible(_arch_index(win)) is False
    assert win.btn_sort_tiers.isHidden() is True
    assert win.chk_show_advanced.isChecked() is False
    win.close()


def test_turning_the_switch_on_shows_them(service_instance, v3_qapp) -> None:
    from download_sorter.ui.main_window import MainWindow

    cfg = service_instance.config
    cfg.show_advanced_tools = False
    service_instance.update_config(cfg)
    win = MainWindow(service_instance)
    win.chk_show_advanced.setChecked(True)
    win._save_settings_form()
    assert service_instance.config.show_advanced_tools is True
    assert win.tabs.isTabVisible(_arch_index(win)) is True
    assert win.btn_sort_tiers.isHidden() is False
    win.close()


def test_full_window_is_unchanged_for_existing_setups(service_instance, v3_qapp) -> None:
    from download_sorter.ui.main_window import MainWindow

    win = MainWindow(service_instance)
    assert win.tabs.isTabVisible(_arch_index(win)) is True
    assert win.btn_sort_tiers.isHidden() is False
    win.close()


def test_return_to_defaults_keeps_the_choice(service_instance) -> None:
    cfg = service_instance.config
    cfg.show_advanced_tools = True
    service_instance.update_config(cfg)
    service_instance.reset_to_defaults_and_shift(shift_files=False)
    # Return to Defaults is on the Folder Architecture page; it must not hide that page.
    assert service_instance.config.show_advanced_tools is True
