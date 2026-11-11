"""The windows must fit the sizes they promise.

The main window says it can be 900x640 and the welcome wizard 720x640. When a
page asks for more room than that (a longer list, one more row), Qt quietly
makes the window bigger, and on a small or highly scaled screen the buttons end
up below the edge. These checks measure with a real font (Qt's offscreen
platform has none, which would draw every letter as a wide box) and fail when a
page outgrows its window.
"""

from __future__ import annotations

import json
import os
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


@pytest.fixture(scope="module")
def real_font(qapp):
    """Load Segoe UI so text is measured the way it will be drawn."""
    from PySide6.QtGui import QFontDatabase

    fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    files = [fonts / name for name in ("segoeui.ttf", "segoeuib.ttf", "seguisb.ttf")]
    if not all(path.exists() for path in files):
        pytest.skip("Segoe UI isn't installed, so text can't be measured")
    ids = [QFontDatabase.addApplicationFont(str(path)) for path in files]
    yield
    for font_id in ids:
        QFontDatabase.removeApplicationFont(font_id)


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
