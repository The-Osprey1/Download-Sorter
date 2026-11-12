"""The first welcome step asks what you mostly download.

Everyday files: the 3D Models and CAD folders start unticked and the Projects page is hidden.
3D printing and CAD: those folders are ticked, the maker tools and the Projects page show.
Software projects: the Projects page shows, without the 3D folders.
Installs from before the choice existed keep the Projects page.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import config_manager
import sorter

onboarding = pytest.importorskip("onboarding")

MAKER = ("3D Models & Print Files", "CAD & Engineering")


@pytest.fixture
def downloads(tmp_path) -> Path:
    root = tmp_path / "Downloads"
    root.mkdir()
    return root.resolve()


def _config(downloads: Path) -> dict:
    cfg = sorter.get_default_config()
    cfg["watch"] = {"folder": str(downloads)}
    return cfg


@pytest.fixture
def wizard(qapp, downloads):
    dlg = onboarding.WelcomeWizardDialog(config=_config(downloads))
    yield dlg
    dlg.close()
    dlg.deleteLater()


def _enabled(config: dict, name: str) -> bool:
    return next(bool(r.get("enabled", True)) for r in config["extension_rules"] if r["name"] == name)


def test_everyday_is_the_choice_for_an_ordinary_downloads_folder(wizard):
    assert wizard.chosen_use() == "everyday"
    assert [wizard._category_checks[name].isChecked() for name in MAKER] == [False, False]
    assert wizard._category_checks["Documents"].isChecked()
    assert wizard._category_checks["Images"].isChecked()


def test_everyday_setup_hides_the_maker_tools_and_the_projects_page(wizard):
    chosen = wizard._chosen_config()

    assert chosen["use_profile"] == "everyday"
    assert chosen["show_maker_features"] is False
    assert chosen["show_projects_page"] is False
    assert not _enabled(chosen, MAKER[0]) and not _enabled(chosen, MAKER[1])
    assert _enabled(chosen, "Documents")


def test_picking_3d_printing_ticks_its_folders_and_shows_its_tools(wizard):
    wizard._use_buttons["maker"].setChecked(True)

    assert [wizard._category_checks[name].isChecked() for name in MAKER] == [True, True]
    chosen = wizard._chosen_config()
    assert chosen["use_profile"] == "maker"
    assert chosen["show_maker_features"] is True and chosen["show_projects_page"] is True
    assert _enabled(chosen, MAKER[0]) and _enabled(chosen, MAKER[1])


def test_picking_software_projects_shows_the_projects_page_without_3d_folders(wizard):
    wizard._use_buttons["developer"].setChecked(True)

    chosen = wizard._chosen_config()
    assert chosen["use_profile"] == "developer"
    assert chosen["show_projects_page"] is True and chosen["show_maker_features"] is False
    assert not _enabled(chosen, MAKER[0])
    assert _enabled(chosen, "Code & Data")


def test_going_back_to_everyday_unticks_the_3d_folders_again(wizard):
    wizard._use_buttons["maker"].setChecked(True)
    wizard._use_buttons["everyday"].setChecked(True)

    assert [wizard._category_checks[name].isChecked() for name in MAKER] == [False, False]


def test_a_3d_folder_ticked_by_hand_is_kept_for_an_everyday_setup(wizard):
    wizard._category_checks[MAKER[0]].setChecked(True)

    chosen = wizard._chosen_config()
    assert _enabled(chosen, MAKER[0]) and not _enabled(chosen, MAKER[1])
    assert chosen["use_profile"] == "everyday"


def test_3d_files_already_in_downloads_start_on_3d_printing(qapp, downloads):
    (downloads / "My prints").mkdir()
    for name in ("bracket.stl", "case.3mf", "mount.step"):
        (downloads / "My prints" / name).write_bytes(b"x")
    dlg = onboarding.WelcomeWizardDialog(config=_config(downloads))
    try:
        assert dlg._scan.get("maker_files", 0) >= 3
        assert dlg.chosen_use() == "maker"
        assert all(dlg._category_checks[name].isChecked() for name in MAKER)
    finally:
        dlg.close()


def test_running_setup_again_starts_on_the_saved_choice(qapp, downloads):
    cfg = _config(downloads)
    cfg["use_profile"] = "developer"
    dlg = onboarding.WelcomeWizardDialog(config=cfg)
    try:
        assert dlg.chosen_use() == "developer"
    finally:
        dlg.close()


def test_one_choice_is_always_picked(wizard):
    assert sum(btn.isChecked() for btn in wizard._use_buttons.values()) == 1
    assert [use for use, _text, _hint in onboarding.USE_PROFILES] == list(wizard._use_buttons)


# ---- the Projects page ----

def test_installs_from_before_the_choice_keep_the_projects_page():
    assert onboarding.projects_page_shown({}) is True
    assert onboarding.projects_page_shown({"show_projects_page": False}) is False


def test_settings_can_show_the_projects_page_again(qapp, downloads):
    cfg = onboarding.apply_use_profile(_config(downloads), "everyday")
    assert config_manager.save_sorter_config(cfg)
    page = onboarding.SettingsWidget(config=cfg)
    heard: list[bool] = []
    page.projects_page_toggled.connect(heard.append)
    try:
        assert page._chk_projects_page.isChecked() is False

        page._chk_projects_page.setChecked(True)

        assert config_manager.load_sorter_config()["show_projects_page"] is True
        assert heard == [True]
    finally:
        page.close()


def test_the_window_hides_the_projects_page_for_an_everyday_setup(qapp, downloads):
    rules_manager = pytest.importorskip("rules_manager")
    cfg = onboarding.apply_use_profile(_config(downloads), "everyday")
    cfg["onboarding_completed"] = True
    assert config_manager.save_sorter_config(cfg)
    window = rules_manager.RulesManagerDialog(initial_view="home")
    try:
        assert window._nav_buttons["projects"].isHidden()
        assert not window._nav_buttons["settings"].isHidden()

        window._settings_widget._chk_projects_page.setChecked(True)
        assert not window._nav_buttons["projects"].isHidden()

        window.switch_view("projects")
        window._settings_widget._chk_projects_page.setChecked(False)
        assert window._nav_buttons["projects"].isHidden()
        assert window._nav_buttons["home"].isChecked(), "hiding the page you are on goes to Home"
    finally:
        window._accepting_close = True
        window.close()
