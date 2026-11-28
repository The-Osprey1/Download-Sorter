"""The window has three tabs, and "Tidy Files Inside Folders" is always in Settings.

The old "Show Advanced Tools" switch only hid that one button, so 4.0.0-beta.3 removed it.
The saved setting is still read and written so older setups load unchanged.
"""

from __future__ import annotations

from download_sorter.config import Config


def test_the_old_setting_still_loads_and_saves() -> None:
    assert Config.from_dict({}).show_advanced_tools is True
    cfg = Config(show_advanced_tools=False)
    assert Config.from_dict(cfg.to_dict()).show_advanced_tools is False


def test_tidy_inside_folders_is_always_shown(service_instance, v3_qapp) -> None:
    from download_sorter.ui.main_window import MainWindow

    cfg = service_instance.config
    cfg.show_advanced_tools = False
    service_instance.update_config(cfg)
    win = MainWindow(service_instance)
    assert win.tabs.count() == 3
    assert win.btn_sort_tiers.isHidden() is False
    assert not hasattr(win, "chk_show_advanced")
    win.close()


def test_zip_content_sorting_has_a_switch(service_instance, v3_qapp) -> None:
    from download_sorter.ui.main_window import MainWindow

    win = MainWindow(service_instance)
    assert win.chk_zip_peek.isChecked() is False
    win.chk_zip_peek.setChecked(True)
    win._save_settings_form()
    assert service_instance.config.zip_peek_enabled is True
    win.close()


def test_return_to_defaults_keeps_the_choice(service_instance) -> None:
    cfg = service_instance.config
    cfg.show_advanced_tools = True
    service_instance.update_config(cfg)
    service_instance.reset_to_defaults_and_shift(shift_files=False)
    assert service_instance.config.show_advanced_tools is True
