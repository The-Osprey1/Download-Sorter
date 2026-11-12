"""The windows must fit the sizes they promise.

The main window says it can be 900x640 and the welcome wizard 720x640. When a
page asks for more room than that (a longer list, one more row), Qt quietly
makes the window bigger, and on a small or highly scaled screen the buttons end
up below the edge. These checks measure with a real font (Qt's offscreen
platform has none, which would draw every letter as a wide box) and fail when a
page outgrows its window. (The ``real_font`` fixture is in conftest.py.)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

import config_manager
import onboarding
import sorter

# A 1366x768 screen at 100% still shows the whole main window.
MAIN_LIMIT = (1000, 640)
WIZARD_LIMIT = (720, 640)
SIDEBAR_WIDTH = 216


def _config(downloads: Path) -> dict:
    cfg = sorter.get_default_config()
    cfg["downloads_dir"] = str(downloads)
    cfg["watch"] = {"folder": str(downloads)}
    cfg["onboarding_completed"] = True
    return cfg


@pytest.fixture
def window(qapp, real_font, tmp_path, monkeypatch):
    import rules_manager

    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps(_config(downloads)), encoding="utf-8")
    monkeypatch.setattr(config_manager, "SORTER_CONFIG_FILE", cfg_file)
    monkeypatch.setattr(config_manager, "ACTIVE_PROJECT_FILE", tmp_path / "active_project.json")
    monkeypatch.setattr(config_manager, "is_sorter_running", lambda: True)
    dlg = rules_manager.RulesManagerDialog(initial_view="home")
    dlg.show()
    qapp.processEvents()
    yield dlg
    dlg._set_dirty(False)
    dlg.close()
    dlg.deleteLater()
    qapp.processEvents()


def test_every_main_window_page_fits_the_promised_size(window):
    problems = []
    for index, page in enumerate(window.PAGES):
        hint = window._view_stack.widget(index).minimumSizeHint()
        if hint.height() > MAIN_LIMIT[1] or hint.width() > MAIN_LIMIT[0] - SIDEBAR_WIDTH:
            problems.append(f"{page} needs {hint.width()}x{hint.height()}")
    assert not problems, "pages outgrew the window: " + "; ".join(problems)

    whole = window.minimumSizeHint()
    assert whole.height() <= MAIN_LIMIT[1], f"the window needs {whole.height()} px of height"
    assert whole.width() <= MAIN_LIMIT[0], f"the window needs {whole.width()} px of width"


@pytest.mark.parametrize("own_folders", [True, False], ids=["with-folders", "no-folders"])
def test_welcome_wizard_fits_the_promised_size(qapp, real_font, tmp_path, own_folders):
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    if own_folders:
        for folder, names in {"Invoices": ["invoice_march.pdf"], "IME 4030": ["lecture 3.pdf"],
                              "Music": ["a.mp3"], "Banking": ["statement_may.pdf"]}.items():
            (downloads / folder).mkdir()
            for name in names:
                (downloads / folder / name).write_text("x", encoding="utf-8")
    wizard = onboarding.WelcomeWizardDialog(config=_config(downloads))
    try:
        wizard.show()
        qapp.processEvents()
        hint = wizard.minimumSizeHint()
        assert hint.height() <= WIZARD_LIMIT[1], f"the wizard needs {hint.height()} px of height"
        assert hint.width() <= WIZARD_LIMIT[0], f"the wizard needs {hint.width()} px of width"
    finally:
        wizard.close()
        wizard.deleteLater()
        qapp.processEvents()


def test_clean_up_after_a_scan_of_many_long_folders_still_fits_the_promised_size(window, qapp, tmp_path):
    import remodel

    long_name = "A folder with a very long name that goes on and on "
    cards = [
        remodel.FolderCard(tmp_path / f"{long_name * 3}{index}", "keep_intact", 5, 1.2,
                           remodel.ProtectionCheck(True, "Python virtual environment detected " * 3), {})
        for index in range(12)
    ]
    plan = remodel.RemodelPlan(
        remodel_id="fit", target_root=tmp_path, created_at="2026-01-01T00:00:00Z", cards=cards,
        loose_files_to_organize=[], duplicates_to_quarantine=[], protected_items=[],
        total_files_scanned=60, reclaimable_duplicate_bytes=0,
    )
    window.switch_view("cleanup")
    window._remodel_widget._on_scan_finished(plan)
    qapp.processEvents()

    page = window._view_stack.widget(window.PAGES.index("cleanup"))
    hint = page.minimumSizeHint()
    assert hint.height() <= MAIN_LIMIT[1], f"the Clean up page needs {hint.height()} px of height after a scan"
    assert hint.width() <= MAIN_LIMIT[0] - SIDEBAR_WIDTH, f"the Clean up page needs {hint.width()} px of width"
    assert window.minimumSizeHint().height() <= MAIN_LIMIT[1]


def test_rules_page_with_many_long_rules_still_fits_the_promised_size(window, qapp):
    rules = [
        {"name": f"A rule with a rather long name number {index}", "folder": "Documents\\" + "Deeply\\Nested\\" * 3,
         "match": ["alpha", "beta", "gamma", "delta"] * 3, "extensions": [".pdf", ".docx", ".xlsx", ".pptx"],
         "enabled": True, "origin": "learned" if index % 2 else "scan"}
        for index in range(30)
    ]
    window._keyword_table.load_rules(rules)
    window.switch_view("rules")
    window.resize(window.minimumWidth(), window.height())  # as narrow as the window can be
    qapp.processEvents()

    page = window._view_stack.widget(window.PAGES.index("rules"))
    hint = page.minimumSizeHint()
    assert hint.height() <= MAIN_LIMIT[1], f"the Rules page needs {hint.height()} px of height"
    assert hint.width() <= MAIN_LIMIT[0] - SIDEBAR_WIDTH, f"the Rules page needs {hint.width()} px of width"
    table = window._keyword_table._table
    assert table.columnWidth(3) >= 80, "the words column keeps room to read, even in the narrowest window"
    assert not table.horizontalScrollBar().isVisible(), "the table fits its page without a sideways scroll bar"
