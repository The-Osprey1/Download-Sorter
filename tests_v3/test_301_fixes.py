"""3.0.1 fixes: destinations stay inside Downloads, the pill hotkey setting works,
the Settings folder reads as a real path, and the window fits 900x560."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from download_sorter.config import (
    Config,
    Rule,
    import_v2_config,
    load_config,
    portable_path,
    readable_path,
    save_config,
)
from download_sorter.db import StateDB
from download_sorter.dest import to_relative_dest
from download_sorter.fs_ops import execute_move
from download_sorter.paths import AppPaths
from download_sorter.quick_switcher import normalize_hotkey, parse_hotkey
from download_sorter.router import FileCandidate, RouteDecision, evaluate
from download_sorter.service import SorterService

HOME = Path.home()


# ---------------------------------------------------------------------------
# Destinations
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Documents/School/", r"Documents\School"),
        (r"${USERPROFILE}\Documents\Banking", r"Documents\Banking"),
        (r"%USERPROFILE%\Documents\Banking", r"Documents\Banking"),
        (r"%userprofile%\Documents\Banking", r"Documents\Banking"),
        (str(HOME / "Downloads" / "Banking"), "Banking"),
        (str(HOME / "Documents" / "Taxes"), r"Documents\Taxes"),
        (r"Z:\Archive\Taxes", "Taxes"),
        (r"\\server\share\Scans", "Scans"),
        (r"..\..\Desktop", "Other"),
        (r"Documents\..\..\Windows", "Other"),
        (r"Docs\bad:name", "Other"),
        (r"${NO_SUCH_VARIABLE_HERE}\Work", "Work"),
        ("School.", "School"),
        ("", "Other"),
    ],
)
def test_destinations_become_folders_inside_the_watched_folder(raw: str, expected: str) -> None:
    assert to_relative_dest(raw) == expected


def test_a_full_path_inside_a_custom_watch_folder_keeps_its_subfolders() -> None:
    assert to_relative_dest(r"Z:\Sorted\Bank\2026", watch_root=Path(r"Z:\Sorted")) == r"Bank\2026"


def test_rules_and_type_folders_are_cleaned_when_settings_load(app_paths: AppPaths) -> None:
    raw = Config().to_dict()
    raw["rules"] = [
        {"id": "a", "name": "Bank", "destination": r"%USERPROFILE%\Documents\Banking", "keywords": ["bank"]},
        {"id": "b", "name": "Escape", "destination": r"..\..\Desktop", "keywords": ["escape"]},
    ]
    raw["category_map"] = {".pdf": str(HOME / "Documents")}
    raw["default_folder"] = r"C:\Windows\Temp\Junk"
    app_paths.config_file.write_text(json.dumps(raw), encoding="utf-8")

    cfg = load_config(app_paths)
    assert [r.destination for r in cfg.rules] == [r"Documents\Banking", "Other"]
    assert cfg.category_map[".pdf"] == "Documents"
    assert cfg.default_folder == "Junk"


def test_a_2x_rule_naming_the_profile_folder_no_longer_makes_a_dollar_folder(tmp_path: Path) -> None:
    v2_file = tmp_path / "v2.json"
    v2_file.write_text(
        json.dumps(
            {
                "watch": {"folder": r"%USERPROFILE%\Downloads"},
                "keyword_rules": [
                    {"name": "Banking", "folder": r"%USERPROFILE%\Documents\Banking", "match": ["bank"]},
                ],
            }
        ),
        encoding="utf-8",
    )
    cfg = import_v2_config(v2_file)
    assert cfg.rules[0].destination == r"Documents\Banking"
    decision = evaluate(FileCandidate("bank_statement.pdf"), cfg)
    assert decision.relative_dest == Path("Documents") / "Banking" / "bank_statement.pdf"
    assert "$" not in str(decision.relative_dest) and "%" not in str(decision.relative_dest)


def test_the_router_cleans_a_destination_changed_after_loading() -> None:
    cfg = Config(rules=[Rule(id="a", name="x", destination="Docs", keywords=["bank"])])
    cfg.rules[0].destination = r"C:\Outside\Bank"  # set directly, as code (not settings) could
    decision = evaluate(FileCandidate("bank.pdf"), cfg)
    assert decision.relative_dest == Path("Bank") / "bank.pdf"


def test_a_move_outside_the_watched_folder_is_refused(app_paths: AppPaths, tmp_path: Path) -> None:
    root = app_paths.downloads_dir
    assert root is not None
    src = root / "report.pdf"
    src.write_bytes(b"data")
    db = StateDB(app_paths.db_file)
    try:
        decision = RouteDecision(
            action="move",
            relative_dest=Path("..") / "Elsewhere" / "report.pdf",
            reason="hand-made",
            matched_rule="test",
            match_type="rule",
        )
        assert execute_move(src, decision, batch_id="b1", db=db, watch_root=root) is None
        assert src.exists()
        assert not (tmp_path / "Elsewhere").exists()
        assert db.list_active_moves() == []
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Settings: watched folder
# ---------------------------------------------------------------------------
def test_the_watched_folder_reads_as_a_real_path_and_is_stored_portably() -> None:
    assert readable_path(r"${USERPROFILE}\Downloads") == str(HOME / "Downloads")
    assert portable_path(str(HOME / "Downloads")) == r"${USERPROFILE}\Downloads"
    assert portable_path((HOME / "Downloads").as_posix()) == r"${USERPROFILE}\Downloads"
    assert portable_path(r"Z:\Sorted") == r"Z:\Sorted"


# ---------------------------------------------------------------------------
# Quick Switcher hotkeys
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Ctrl+G", "Ctrl+G"),
        ("ctrl + shift + g", "Ctrl+Shift+G"),
        ("Alt+F2", "Alt+F2"),
        ("Shift+Ctrl+7", "Ctrl+Shift+7"),
        ("G", None),  # would fire while typing a file name
        ("Shift+G", None),
        ("Ctrl+Ctrl+G", None),
        ("Ctrl+Enter", None),
        ("Win+G", None),
        ("", None),
    ],
)
def test_hotkeys_are_parsed_or_refused(text: str, expected: str | None) -> None:
    parsed = parse_hotkey(text)
    assert (parsed.text if parsed else None) == expected
    assert normalize_hotkey(text, "Ctrl+G") == (expected or "Ctrl+G")


def test_a_bad_hotkey_in_settings_falls_back_to_the_default() -> None:
    assert Config(quick_switcher_hotkey="alt+7").quick_switcher_hotkey == "Alt+7"
    assert Config(quick_switcher_hotkey="Q").quick_switcher_hotkey == "Ctrl+G"


def test_the_pill_listens_for_the_hotkey_from_settings(v3_qapp) -> None:  # noqa: ANN001
    from download_sorter.ui.quick_switcher_pill import QuickSwitcherPill

    recent = {"path": str(HOME / "x.pdf"), "name": "x.pdf", "category": "documents"}
    pill = QuickSwitcherPill(0, recent, hotkey_select="Alt+F2")
    try:
        assert pill._shortcut_select.key().toString() == "Alt+F2"
        assert pill.action_btn is not None and "Alt+F2" in pill.action_btn.text()
    finally:
        pill.close()

    default_pill = QuickSwitcherPill(0, recent, hotkey_select="nonsense")
    try:
        assert default_pill._shortcut_select.key().toString() == "Ctrl+G"
    finally:
        default_pill.close()


# ---------------------------------------------------------------------------
# Window
# ---------------------------------------------------------------------------
@pytest.fixture
def window(v3_qapp, app_paths: AppPaths):  # noqa: ANN001, ANN201
    from PySide6.QtGui import QFont, QFontDatabase

    from download_sorter.ui.main_window import MainWindow

    # Offscreen Qt has no fonts and draws text about twice as wide; measure with
    # the font the app really uses on Windows.
    fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    if not (fonts / "segoeui.ttf").exists():
        pytest.skip("Segoe UI is needed to measure the layout")
    for name in ("segoeui.ttf", "segoeuib.ttf", "seguisb.ttf"):
        if (fonts / name).exists():
            QFontDatabase.addApplicationFont(str(fonts / name))
    old_font = v3_qapp.font()
    v3_qapp.setFont(QFont("Segoe UI", 10))

    cfg = Config.with_starter_defaults()
    save_config(cfg, app_paths)  # tutorial not finished: the Home intro card is on
    for name in ("Bank_Statement_Oct_2026.pdf", "benchy_plate1.3mf", "setup-7zip-x64.exe"):
        (app_paths.downloads_dir / name).write_bytes(b"x" * 100)
    svc = SorterService(app_paths)
    win = MainWindow(svc)
    win.show()
    yield win
    win.close()
    svc.stop(timeout=1.0)
    v3_qapp.setFont(old_font)


def _resize(win, width: int, height: int) -> None:  # noqa: ANN001
    from PySide6.QtWidgets import QApplication

    for _ in range(3):  # folding a card lowers the minimum; a user's drag gets there too
        win.resize(width, height)
        QApplication.processEvents()


def _clipped_buttons(win) -> list[str]:  # noqa: ANN001
    from PySide6.QtWidgets import QPushButton

    clipped = []
    for btn in win.findChildren(QPushButton):
        if not btn.isVisible() or not btn.text():
            continue
        if btn.width() + 1 < btn.sizeHint().width():
            clipped.append(f"{btn.text()!r} {btn.width()} < {btn.sizeHint().width()}")
    return clipped


@pytest.mark.parametrize(("width", "height"), [(900, 560), (1040, 640), (1100, 760), (1400, 900)])
def test_the_window_fits_and_no_button_is_cut_off(window, width: int, height: int) -> None:  # noqa: ANN001
    _resize(window, width, height)
    assert (window.width(), window.height()) == (width, height)
    for index in range(window.tabs.count()):
        window.tabs.setCurrentIndex(index)
        _resize(window, width, height)
        assert (window.width(), window.height()) == (width, height), window.tabs.tabText(index)
        assert _clipped_buttons(window) == [], window.tabs.tabText(index)


def test_a_narrow_window_shows_header_icons_and_keeps_the_words_in_tooltips(window) -> None:  # noqa: ANN001
    _resize(window, 1400, 900)
    assert window.btn_undo_last.text() == "Undo"
    assert window.lbl_status_badge.isVisible()
    _resize(window, 900, 560)
    for btn in (window.btn_undo_last, window.btn_open_downloads, window.btn_walkthrough, window.btn_pause):
        assert btn.is_collapsed() and btn.text() == ""
        assert btn.toolTip()
    assert not window.lbl_status_badge.isVisible()
    assert window.instructions_widget.isHidden()  # Home's intro folds in a short window
    _resize(window, 1400, 900)
    assert window.btn_undo_last.text() == "Undo"
    assert not window.btn_pause.is_collapsed()
    assert not window.instructions_widget.isHidden()


def test_the_header_path_is_shortened_not_widened(window) -> None:  # noqa: ANN001
    _resize(window, 900, 560)
    label = window.lbl_watch_path
    full = label.full_text()
    assert full == str(window.service.watch_root)
    shown = super(type(label), label).text()  # what is drawn
    assert len(shown) < len(full) and "…" in shown
    assert label.toolTip() == full


def test_settings_shows_the_folder_as_a_path_and_saves_it_portably(window) -> None:  # noqa: ANN001
    window.service.config.watch_folder = r"${USERPROFILE}\Downloads"
    window._refresh_settings_fields()
    assert window.edit_watch_folder.text() == str(HOME / "Downloads")
    window._save_settings_form()
    assert window.service.config.watch_folder == r"${USERPROFILE}\Downloads"


def test_settings_refuses_an_unusable_hotkey_and_says_so(window) -> None:  # noqa: ANN001
    window.edit_qs_hotkey.setText("Q")
    window._save_settings_form()
    assert window.service.config.quick_switcher_hotkey == "Ctrl+G"
    assert window.edit_qs_hotkey.text() == "Ctrl+G"
    assert "can't be used" in window.banner._text.text()

    window.edit_qs_hotkey.setText("alt + f2")
    window._save_settings_form()
    assert window.service.config.quick_switcher_hotkey == "Alt+F2"
