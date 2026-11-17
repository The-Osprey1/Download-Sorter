"""3.4.1: rules survive Apply & Shift, keywords match whole words, no blank icons,
learned rule suggestions are shown, and PDF text reading has a memory cap."""

from __future__ import annotations

from pathlib import Path
import re
import tracemalloc
import zlib

import pytest

from download_sorter.architecture import architecture_from_config, apply_architecture_to_config
from download_sorter.config import Config, Rule, save_config
from download_sorter.paths import AppPaths
from download_sorter.router import FileCandidate, _keyword_matches, evaluate, extract_pdf_text_preview

SRC = Path(__file__).resolve().parents[1] / "src" / "download_sorter"


def _power_rule() -> Rule:
    return Rule(
        id="mine",
        name="My Banking",
        destination=r"Finance\Bank",
        keywords=["chase"],
        exclude_keywords=["draft"],
        regex_pattern=r"(?P<acct>\d{4})",
        rename_template="{acct}-{stem}",
        match_mode="all",
        min_size_mb=0.5,
        content_keywords=["statement"],
        auto_unzip=True,
    )


# ---------------------------------------------------------------------------
# Apply & Shift keeps Custom Rules
# ---------------------------------------------------------------------------
def test_apply_architecture_keeps_every_user_rule_unchanged() -> None:
    cfg = Config.with_starter_defaults()
    cfg.rules.append(_power_rule())
    before = [r.to_dict() for r in cfg.rules]

    out = apply_architecture_to_config(architecture_from_config(cfg), cfg)

    user_after = [r.to_dict() for r in out.rules if not r.id.startswith("arch-")]
    assert user_after == before


def test_apply_architecture_twice_does_not_pile_up_rules() -> None:
    cfg = Config.with_starter_defaults()
    cfg.rules.append(_power_rule())
    once = apply_architecture_to_config(architecture_from_config(cfg), cfg)
    twice = apply_architecture_to_config(architecture_from_config(once), once)
    assert [r.to_dict() for r in twice.rules] == [r.to_dict() for r in once.rules]


def test_words_added_on_a_folder_card_become_a_rule_for_just_those_words() -> None:
    cfg = Config.with_starter_defaults()
    arch = architecture_from_config(cfg)
    career = arch.ensure_path_chain(r"Documents\Career")
    assert career is not None
    career.keywords = [*career.keywords, "reference_letter"]

    out = apply_architecture_to_config(arch, cfg)

    extra = [r for r in out.rules if r.id.startswith("arch-") and r.destination.lower() == r"documents\career"]
    assert len(extra) == 1
    assert extra[0].keywords == ["reference_letter"]
    assert evaluate(FileCandidate("Prof_reference_letter.pdf"), out).relative_dest == Path(
        r"Documents\Career\Prof_reference_letter.pdf"
    )


def test_an_empty_folder_card_never_catches_every_file() -> None:
    cfg = Config.with_starter_defaults()
    arch = architecture_from_config(cfg)
    arch.ensure_path_chain(r"Documents\School\Empty Folder")

    out = apply_architecture_to_config(arch, cfg)

    assert not any("Empty Folder" in r.destination for r in out.rules)
    decision = evaluate(FileCandidate("holiday_photo.jpg"), out)
    assert "Empty Folder" not in str(decision.relative_dest)


# ---------------------------------------------------------------------------
# Keywords match whole words
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("keyword", "stem"),
    [
        ("bank", "Bankruptcy_notice"),
        ("note", "Keynote_slides"),
        ("game", "endgame_tablebase"),
        ("class", "classified_ad"),
        ("lecture", "electure"),
        ("resume", "presumed_dead"),
        ("school", "preschool_menu"),
        ("cv", "opencv_build"),
    ],
)
def test_a_keyword_does_not_match_inside_another_word(keyword: str, stem: str) -> None:
    assert not _keyword_matches(stem, keyword)


@pytest.mark.parametrize(
    ("keyword", "stem"),
    [
        ("resume", "Resume2026"),
        ("ime3311", "IME 3311 hw"),
        ("ime 3311", "IME3311_hw"),
        ("lecturenotes", "lecture_notes_wk3"),
        ("tax return", "2026_ReturnTaxFinal"),
        ("syllabus", "CS101_Sylabus_Fall"),
        ("cv", "My_CV"),
        ("invoice", "Invoice-0042"),
        ("bank statement", "BankStatement_Oct"),
        ("cs101", "CS101_Syllabus"),
    ],
)
def test_keywords_still_match_real_variants(keyword: str, stem: str) -> None:
    assert _keyword_matches(stem, keyword)


# ---------------------------------------------------------------------------
# No blank icon buttons
# ---------------------------------------------------------------------------
def _icon_names_used_in_source() -> set[str]:
    names: set[str] = set()
    for py in SRC.rglob("*.py"):
        names.update(re.findall(r'make_vector_icon\("([a-z_]+)"', py.read_text(encoding="utf-8")))
    return names


def test_every_icon_the_app_asks_for_is_drawn(v3_qapp) -> None:  # noqa: ANN001
    from download_sorter.ui.motion import make_vector_icon

    used = _icon_names_used_in_source()
    assert {"search", "trash"} <= used
    blank = []
    for name in sorted(used):
        img = make_vector_icon(name, "#FFFFFF", 16).pixmap(32, 32).toImage()
        if not any(img.pixelColor(x, y).alpha() for x in range(img.width()) for y in range(img.height())):
            blank.append(name)
    assert blank == []


# ---------------------------------------------------------------------------
# Learned rule suggestions are shown
# ---------------------------------------------------------------------------
def test_rules_tab_offers_a_learned_rule_and_can_add_or_dismiss_it(
    v3_qapp, app_paths: AppPaths, monkeypatch  # noqa: ANN001
) -> None:
    from PySide6.QtWidgets import QDialog

    from download_sorter.learning import record_manual_route
    from download_sorter.service import SorterService
    from download_sorter.ui import main_window as mw

    monkeypatch.setattr(mw, "is_startup_enabled", lambda: False)
    monkeypatch.setattr(mw, "is_context_menu_enabled", lambda: False)
    cfg = Config.with_starter_defaults()
    cfg.tutorial_completed = True
    save_config(cfg, app_paths)
    for _ in range(2):
        record_manual_route(destination=r"Finance\Bank", data_dir=app_paths.data_dir, host="chase.com")
        record_manual_route(destination=r"Music\Stems", data_dir=app_paths.data_dir, ext=".flac")

    win = mw.MainWindow(SorterService(app_paths))
    win._refresh_rules()
    assert not win.frm_rule_suggestion.isHidden()
    assert "chase.com" in win.lbl_rule_suggestion.text() or ".flac" in win.lbl_rule_suggestion.text()

    first = dict(win._current_suggestion or {})
    monkeypatch.setattr(mw.RuleEditorDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    n_before = len(win.service.config.rules)
    win._accept_rule_suggestion()
    rules = win.service.config.rules
    assert len(rules) == n_before + 1
    added = rules[-1]  # learned rules go last, behind the user's own
    assert added.destination == first["destination"]

    # The accepted one is covered now; the other one is offered, and Not Now hides it.
    assert win._current_suggestion is not None
    assert win._current_suggestion["key"] != first["key"]
    win._dismiss_rule_suggestion()
    assert win._current_suggestion is None
    assert win.frm_rule_suggestion.isHidden()


def test_a_suggestion_folds_into_a_button_so_the_window_still_fits_900x560(
    v3_qapp, app_paths: AppPaths, monkeypatch  # noqa: ANN001
) -> None:
    from download_sorter.learning import record_manual_route
    from download_sorter.service import SorterService
    from download_sorter.ui import main_window as mw

    monkeypatch.setattr(mw, "is_startup_enabled", lambda: False)
    monkeypatch.setattr(mw, "is_context_menu_enabled", lambda: False)
    cfg = Config.with_starter_defaults()
    cfg.tutorial_completed = True
    save_config(cfg, app_paths)
    for _ in range(3):
        record_manual_route(destination=r"Finance\Bank", data_dir=app_paths.data_dir, host="chase.com")

    win = mw.MainWindow(SorterService(app_paths))
    win.show()
    for _ in range(3):
        win.resize(900, 560)
        v3_qapp.processEvents()

    assert win.height() <= 560
    assert win.frm_rule_suggestion.isHidden()
    assert not win.btn_rule_suggestion_compact.isHidden()
    assert "chase.com" in win.btn_rule_suggestion_compact.toolTip()

    win.resize(1100, 760)
    v3_qapp.processEvents()
    assert not win.frm_rule_suggestion.isHidden()
    assert win.btn_rule_suggestion_compact.isHidden()


# ---------------------------------------------------------------------------
# PDF text reading has a memory cap
# ---------------------------------------------------------------------------
def test_pdf_text_preview_does_not_inflate_a_huge_stream(tmp_path: Path) -> None:
    bomb = zlib.compress(b"(Statement) Tj " + b"\0" * (200 * 1024 * 1024), 9)
    assert len(bomb) < 262_144
    pdf = tmp_path / "bomb.pdf"
    pdf.write_bytes(b"%PDF-1.4\n1 0 obj\n<< /Filter /FlateDecode >>\nstream\n" + bomb + b"\nendstream\nendobj\n")

    tracemalloc.start()
    try:
        text = extract_pdf_text_preview(pdf)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert "Statement" in text
    assert peak < 16 * 1024 * 1024
