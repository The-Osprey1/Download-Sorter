"""New installs start from file types only; the welcome lets people pick rule packs
that fit what they download, and the Rules page offers every pack."""

from __future__ import annotations

from pathlib import Path

import pytest

from download_sorter import config as config_module
from download_sorter.config import STARTER_PACK_LABELS, STARTER_TEMPLATES, Config, load_config
from download_sorter.paths import AppPaths
from download_sorter.router import FileCandidate, evaluate

pytestmark = pytest.mark.shipped_defaults


def test_a_new_install_has_no_pack_rules(app_paths: AppPaths) -> None:
    cfg = load_config(app_paths)
    assert cfg.rules == []


def test_a_new_install_still_sorts_by_file_type(app_paths: AppPaths) -> None:
    cfg = load_config(app_paths)
    decision = evaluate(FileCandidate("lecture_notes_week3.pdf"), cfg)
    assert decision.match_type == "extension"
    assert decision.relative_dest is not None
    assert decision.relative_dest.name == "lecture_notes_week3.pdf"


def test_every_pack_has_a_label_and_rules() -> None:
    assert set(STARTER_PACK_LABELS) == set(STARTER_TEMPLATES)
    for key, (label, description) in STARTER_PACK_LABELS.items():
        assert label and description, key
        assert STARTER_TEMPLATES[key], key


@pytest.mark.parametrize(
    ("pack", "filename", "url"),
    [
        ("finance", "Invoice_2026-04.pdf", ""),
        ("finance", "Rechnung_April.pdf", ""),
        ("travel", "Boarding Pass - LHR.pdf", ""),
        ("travel", "itinerary_rome.pdf", ""),
        ("contracts", "Lease_Agreement_signed.pdf", ""),
    ],
)
def test_everyday_packs_file_typical_downloads(pack: str, filename: str, url: str) -> None:
    cfg = Config()
    assert cfg.apply_template(pack) > 0
    decision = evaluate(FileCandidate(filename, source_url=url), cfg)
    assert decision.match_type == "rule", (pack, filename, decision.reason)


def test_everyday_packs_leave_unrelated_files_alone() -> None:
    cfg = Config()
    for pack in ("finance", "travel", "contracts"):
        cfg.apply_template(pack)
    for name in ("Bill_Nye_episode.mp4", "holiday_photo.jpg", "formula_sheet.pdf"):
        assert evaluate(FileCandidate(name), cfg).match_type != "rule", name


def test_return_to_defaults_uses_new_install_defaults(service_instance) -> None:
    cfg = service_instance.config
    cfg.apply_template("school")
    service_instance.update_config(cfg)
    service_instance.reset_to_defaults_and_shift(shift_files=False)
    assert service_instance.config.rules == []


def test_welcome_offers_every_pack_and_reports_the_ticked_ones(app_paths: AppPaths, v3_qapp) -> None:
    from download_sorter.ui.main_window import WalkthroughDialog

    dlg = WalkthroughDialog(Path(app_paths.downloads_dir), 7)
    assert set(dlg.pack_boxes) == set(STARTER_PACK_LABELS)
    assert dlg.selected_packs() == []
    dlg.pack_boxes["finance"].setChecked(True)
    dlg.pack_boxes["travel"].setChecked(True)
    assert dlg.selected_packs() == ["finance", "travel"]
    dlg.close()


def test_welcome_second_step_is_about_packs_not_3d_printing(app_paths: AppPaths, v3_qapp) -> None:
    from download_sorter.ui.main_window import WalkthroughDialog

    dlg = WalkthroughDialog(Path(app_paths.downloads_dir), 7)
    dlg._on_next_clicked()
    assert ".3mf" not in dlg.lbl_banner_title.text().lower()
    assert "slicer" not in dlg.lbl_banner_title.text().lower()
    assert dlg.packs_panel.isVisibleTo(dlg)
    dlg.close()


def test_finishing_the_welcome_adds_the_chosen_packs(
    service_instance, v3_qapp, monkeypatch: pytest.MonkeyPatch
) -> None:
    from download_sorter.ui import main_window as mw

    def fake_exec(dialog) -> int:
        dialog.pack_boxes["travel"].setChecked(True)
        return 1

    monkeypatch.setattr(mw.WalkthroughDialog, "exec", fake_exec)
    win = mw.MainWindow(service_instance)
    win.open_walkthrough()
    names = [r.name for r in service_instance.config.rules]
    assert names == [r.name for r in STARTER_TEMPLATES["travel"]]
    assert service_instance.config.tutorial_completed is True
    win.close()


def test_rules_page_menu_lists_every_pack(service_instance, v3_qapp) -> None:
    from download_sorter.ui.main_window import MainWindow

    win = MainWindow(service_instance)
    actions = {a.data(): a for a in win.menu_rule_packs.actions()}
    assert set(actions) == set(STARTER_PACK_LABELS)
    actions["finance"].trigger()
    assert [r.name for r in service_instance.config.rules] == [r.name for r in STARTER_TEMPLATES["finance"]]
    win.close()


def test_old_defaults_constant_is_not_used_for_new_installs() -> None:
    assert config_module.NEW_INSTALL_TEMPLATES == ()
