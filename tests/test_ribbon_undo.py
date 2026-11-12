"""A ribbon's Undo button puts back just that file, and the ribbon's buttons always fit.

* ``sort_undo --file PATH`` (``undo_file``) restores the one filed file at PATH and
  leaves every other filed file, in the same batch or not, where it is.
* A single-file ribbon has an **Undo** pill next to **Jump to File**. A held 3MF
  ribbon has **File now** and **Keep here** as plain text, with no emoji and no Undo
  (that file has not been filed yet).
* "Saved to <folder>" is measured with the font that draws it and shortened in the
  middle, so the pills are fully visible at every display scale.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

ribbon_popup = pytest.importorskip("ribbon_popup")

import main
import safe_ops
import sort_undo


# ---------------------------------------------------------------------------
# sort_undo --file / undo_file
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def keep_undo_side_effects_off_real_files(monkeypatch):
    """Undo tells other processes about a restore through files in the real temp folder."""
    monkeypatch.setattr(sort_undo, "dismiss_active_ribbons", None)
    monkeypatch.setattr(sort_undo, "clear_recent_file", None)
    monkeypatch.setattr(sort_undo, "get_recent_file", None)


@pytest.fixture
def toasts(monkeypatch):
    """The short ribbons a command without a window would show: (title, message)."""
    shown: list[tuple[str, str]] = []
    monkeypatch.setattr(
        ribbon_popup, "notify_summary",
        lambda title, message, accent="", timeout_ms=0: shown.append((title, message)),
    )
    return shown


def _file(path: Path, text: str = "data") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    old = time.time() - 30  # long enough ago that the safety layer treats it as finished
    os.utime(path, (old, old))
    return path


def _filed(tmp_path: Path, name: str, folder: str = "Documents", batch: str = "b1", text: str = "data"):
    """Put a file in Downloads and file it the way the sorter does. Returns (original, filed)."""
    original = _file(tmp_path / "Downloads" / name, text)
    filed = safe_ops.move(original, tmp_path / "Downloads" / folder / name, batch_id=batch)
    return original, filed


def _active_dests() -> list[str]:
    return [rec["dest"] for rec in sort_undo.get_active_moves(sort_undo.load_history())]


def test_undo_file_puts_back_only_that_file(tmp_path):
    original_a, filed_a = _filed(tmp_path, "a.pdf", batch="b1")
    original_b, filed_b = _filed(tmp_path, "b.pdf", batch="b1")  # same batch

    assert sort_undo.undo_file(filed_a) == 1

    assert original_a.read_text() == "data" and not filed_a.exists()
    assert filed_b.exists() and not original_b.exists(), "the other file of the batch is not touched"
    assert _active_dests() == [str(filed_b.resolve())]


def test_undo_file_twice_restores_nothing_the_second_time(tmp_path):
    original, filed = _filed(tmp_path, "a.pdf")

    assert sort_undo.undo_file(filed) == 1
    assert sort_undo.undo_file(filed) == 0
    assert original.exists()


def test_undo_file_for_a_file_that_was_never_filed_does_nothing(tmp_path):
    _filed(tmp_path, "a.pdf")
    stranger = _file(tmp_path / "Downloads" / "Documents" / "mine.pdf")

    assert sort_undo.undo_file(stranger) == 0
    assert stranger.exists()
    assert len(_active_dests()) == 1


def test_undo_file_reads_the_path_however_it_is_written(tmp_path):
    original, filed = _filed(tmp_path, "a.pdf")
    spelled = str(filed).replace("\\", "/")
    if sys.platform == "win32":
        spelled = spelled.upper()  # Windows paths do not care about case

    assert sort_undo.find_active_move(spelled) is not None
    assert sort_undo.undo_file(spelled) == 1
    assert original.exists()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows paths ignore case")
def test_a_path_is_matched_without_regard_to_case_even_when_the_file_is_gone(tmp_path):
    # For a file that exists, Windows reports its true spelling; once it is gone the
    # spelling has to be compared as written.
    _original, filed = _filed(tmp_path, "Mixed Case.pdf")
    filed.unlink()

    assert sort_undo.find_active_move(str(filed).upper()) is not None
    assert sort_undo.find_active_move(str(filed).lower().replace("\\", "/")) is not None


def test_the_newest_move_wins_when_a_name_was_filed_twice(tmp_path):
    original, filed = _filed(tmp_path, "x.pdf", batch="b1", text="one")
    filed.unlink()  # gone by hand: the first record is still active, its file is not
    second_original, second = _filed(tmp_path, "x.pdf", batch="b2", text="two")
    assert second == filed

    record = sort_undo.find_active_move(filed)
    assert record is not None and record["batch_id"] == "b2"

    assert sort_undo.undo_file(filed) == 1
    assert second_original.read_text() == "two"


def test_undo_file_takes_a_dry_run(tmp_path):
    original, filed = _filed(tmp_path, "a.pdf")

    assert sort_undo.undo_file(filed, dry_run=True) == 1

    assert filed.exists() and not original.exists()
    assert len(_active_dests()) == 1


def test_undo_file_can_reuse_the_records_already_loaded(tmp_path, monkeypatch):
    _original, filed = _filed(tmp_path, "a.pdf")
    active = sort_undo.get_active_moves(sort_undo.load_history())
    monkeypatch.setattr(sort_undo, "load_history", lambda: pytest.fail("history was read again"))

    assert sort_undo.undo_file(filed, active=active) == 1


# ---- the command line ------------------------------------------------------

def test_file_option_restores_one_file_and_says_so(tmp_path, capsys, toasts):
    original_a, filed_a = _filed(tmp_path, "a.pdf")
    _original_b, filed_b = _filed(tmp_path, "b.pdf")

    assert sort_undo.main(["--file", str(filed_a)]) == 0

    assert "Restored 1 file(s)." in capsys.readouterr().out
    assert original_a.exists() and filed_b.exists()
    assert toasts == [], "no ribbon unless --notify or --quiet-toast is given"


def test_file_option_shows_a_ribbon_with_quiet_toast(tmp_path, toasts):
    _original, filed = _filed(tmp_path, "invoice.pdf")

    assert sort_undo.main(["-f", str(filed), "--quiet-toast"]) == 0

    assert toasts == [("Put back", "invoice.pdf is back in Downloads.")]


def test_file_option_wins_over_all(tmp_path):
    original_a, filed_a = _filed(tmp_path, "a.pdf")
    _original_b, filed_b = _filed(tmp_path, "b.pdf", batch="b2")

    assert sort_undo.main(["--all", "--file", str(filed_a)]) == 0

    assert original_a.exists() and filed_b.exists()


def test_file_option_for_an_unknown_file_says_so(tmp_path, capsys, toasts):
    _filed(tmp_path, "a.pdf")
    stranger = _file(tmp_path / "Downloads" / "stranger.pdf")

    assert sort_undo.main(["--file", str(stranger), "--quiet-toast"]) == 0

    out = capsys.readouterr().out
    assert "No active move found for file" in out and "stranger.pdf" in out
    assert stranger.exists()
    assert [title for title, _message in toasts] == ["Nothing to put back"]


def test_file_option_with_no_history_at_all(tmp_path, capsys, toasts):
    stranger = _file(tmp_path / "Downloads" / "stranger.pdf")

    assert sort_undo.main(["--file", str(stranger), "--notify"]) == 0

    assert "No active move found for file" in capsys.readouterr().out
    assert [title for title, _message in toasts] == ["Nothing to put back"], "not the batch undo's title"


def test_file_option_when_the_file_is_gone(tmp_path, capsys, toasts):
    _original, filed = _filed(tmp_path, "a.pdf")
    filed.unlink()

    assert sort_undo.main(["--file", str(filed), "--quiet-toast"]) == 0

    assert "Restored 0 file(s)." in capsys.readouterr().out
    assert toasts == [("Could not put it back", "a.pdf is no longer where it was filed.")]


def test_file_option_when_the_file_is_in_use_the_ribbon_says_so(tmp_path, monkeypatch, toasts):
    original, filed = _filed(tmp_path, "a.pdf")

    def busy(entry, **kwargs):
        raise safe_ops.Refused("locked", Path(entry["dest"]))

    monkeypatch.setattr(sort_undo.safe_ops, "restore", busy)

    assert sort_undo.main(["--file", str(filed), "--quiet-toast"]) == 0

    assert filed.exists() and not original.exists()
    assert toasts == [("Could not put it back", "It may be open in another program.")]


def test_a_path_the_console_cannot_show_does_not_stop_the_ribbon_message(tmp_path, monkeypatch, toasts):
    import io

    console = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")
    monkeypatch.setattr(sys, "stdout", console)

    assert sort_undo.main(["--file", str(tmp_path / "日本語" / "請求書.pdf"), "--quiet-toast"]) == 0

    console.flush()
    assert b"No active move found" in console.buffer.getvalue()
    assert [title for title, _message in toasts] == ["Nothing to put back"], "the ribbon is the real answer"


def test_finding_a_move_does_not_ask_the_disk_about_every_record(monkeypatch):
    # Resolving a path touches the disk and the history can hold thousands of moves, so
    # only records whose file name matches are looked at that closely.
    records = [
        {"id": str(n), "src": f"C:/D/file{n}.pdf", "dest": f"C:/D/Documents/PDF/file{n}.pdf"}
        for n in range(500)
    ]
    wanted = records[0]["dest"]  # the oldest: the whole history is searched
    calls: list[str] = []
    real = sort_undo._path_key
    monkeypatch.setattr(sort_undo, "_path_key", lambda path: calls.append(str(path)) or real(path))

    assert sort_undo.find_active_move(wanted, records) is records[0]
    assert len(calls) <= 3, f"{len(calls)} paths resolved"


def test_file_option_dry_run_moves_nothing_and_shows_no_ribbon(tmp_path, capsys, toasts):
    original, filed = _filed(tmp_path, "a.pdf")

    assert sort_undo.main(["--file", str(filed), "--dry-run", "--quiet-toast"]) == 0

    assert "Would restore 1 file(s)." in capsys.readouterr().out
    assert filed.exists() and not original.exists()
    assert toasts == []


def test_list_still_only_lists_when_a_file_is_given(tmp_path, capsys):
    original, filed = _filed(tmp_path, "a.pdf")

    assert sort_undo.main(["--list", "--file", str(filed)]) == 0

    assert "Recent Active Moves" in capsys.readouterr().out
    assert filed.exists() and not original.exists()


def test_the_other_options_work_as_before(tmp_path, capsys):
    original_a, _filed_a = _filed(tmp_path, "a.pdf", batch="b1")
    original_b, _filed_b = _filed(tmp_path, "b.pdf", batch="b2")

    assert sort_undo.main([]) == 0  # no option: the most recent batch

    out = capsys.readouterr().out
    assert "Selecting most recent batch 'b2' (1 file(s))." in out and "Restored 1 file(s)." in out
    assert original_b.exists() and not original_a.exists()


def test_main_hands_file_and_quiet_toast_to_undo(monkeypatch):
    seen: list = []
    monkeypatch.setattr(sort_undo, "main", lambda argv=None: seen.append(argv) or 0)

    assert main.main(["--undo", "--file", "C:/Downloads/Documents/a.pdf", "--quiet-toast"]) == 0

    assert seen == [["--file", "C:/Downloads/Documents/a.pdf", "--quiet-toast"]]


def test_the_command_a_ribbon_starts_in_the_installed_app_and_from_source(monkeypatch):
    path = r"C:\Users\me\Downloads\Documents\a b.pdf"

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\Apps\DownloadSorter.exe")
    assert ribbon_popup._app_command("--undo", "--file", path, "--quiet-toast") == [
        r"C:\Apps\DownloadSorter.exe", "--undo", "--file", path, "--quiet-toast",
    ]

    monkeypatch.setattr(sys, "frozen", False, raising=False)
    command = ribbon_popup._app_command("--undo", "--file", path, "--quiet-toast")
    assert command[1:] == [str(ribbon_popup.ROOT / "main.py"), "--undo", "--file", path, "--quiet-toast"]


# ---------------------------------------------------------------------------
# Text that has to fit (no Tk: every letter is 6 px wide here)
# ---------------------------------------------------------------------------

def six_px(text: str) -> int:
    return 6 * len(text)


@pytest.mark.parametrize("text", [
    "short", "Documents\\Invoices & Receipts", "x" * 80, "...\\" + "Quarterly Reports " * 6,
])
def test_ellipsize_middle_never_exceeds_the_room(text):
    for room in range(0, 6 * len(text) + 12):
        result = ribbon_popup.ellipsize_middle(text, room, six_px)
        assert six_px(result) <= room or result == ribbon_popup.ELLIPSIS, (room, result)
        if six_px(text) <= room:
            assert result == text, "text that fits is not touched"


def test_ellipsize_middle_keeps_both_ends():
    result = ribbon_popup.ellipsize_middle("Quarterly Report Final.pdf", 120, six_px)

    assert result.startswith("Quarte") and result.endswith("nal.pdf") and "…" in result
    assert six_px(result) <= 120


def test_ellipsize_middle_keeps_the_fixed_start():
    result = ribbon_popup.ellipsize_middle("...\\Invoices & Receipts & Statements", 100, six_px, keep_head=4)

    assert result.startswith("...\\") and result.count("…") == 1 and six_px(result) <= 100


@pytest.mark.parametrize("folder, expected", [
    ("Documents\\Invoices & Receipts", [
        "Saved to Documents\\Invoices & Receipts",
        "Saved to ...\\Invoices & Receipts",
        "...\\Invoices & Receipts",
    ]),
    ("Downloads", ["Saved to Downloads", "Downloads"]),
    ("3D Models/MakerWorld/Benchy", [
        "Saved to 3D Models\\MakerWorld\\Benchy",
        "Saved to ...\\MakerWorld\\Benchy",
        "Saved to ...\\Benchy",
        "...\\Benchy",
    ]),
    ("", ["Saved to"]),
])
def test_ways_to_say_where_a_file_went(folder, expected):
    assert ribbon_popup.folder_text_candidates(folder) == expected


@pytest.mark.parametrize("room, expected", [
    (228, "Saved to Documents\\Invoices & Receipts"),
    (200, "Saved to ...\\Invoices & Receipts"),      # leading folders collapse first
    (150, "...\\Invoices & Receipts"),               # then "Saved to" goes, the folder stays whole
])
def test_the_folder_text_gives_up_the_least_useful_part_first(room, expected):
    assert ribbon_popup.fit_folder_text("Documents\\Invoices & Receipts", room, six_px) == expected


def test_a_folder_name_too_long_for_the_room_is_cut_in_the_middle_after_its_marker():
    result = ribbon_popup.fit_folder_text("Documents\\Invoices & Receipts & Statements 2026", 110, six_px)

    assert result.startswith("...\\") and "…" in result and result.endswith("2026")
    assert six_px(result) <= 110


def test_the_held_ribbon_text_gets_shorter_before_it_is_cut():
    fit = ribbon_popup.fit_text
    assert fit(ribbon_popup.HELD_TEXTS, 220, six_px) == "Holding in Downloads (Bambu Studio)"
    assert fit(ribbon_popup.HELD_TEXTS, 125, six_px) == "Holding in Downloads"
    assert fit(ribbon_popup.HELD_TEXTS, 105, six_px) == "Held in Downloads"
    assert six_px(fit(ribbon_popup.HELD_TEXTS, 90, six_px)) <= 90


# ---------------------------------------------------------------------------
# The real ribbon in a real Tk window, at each display scale
# ---------------------------------------------------------------------------

DISPLAY_SCALES = [96, 120, 144, 168, 192]  # 100%, 125%, 150%, 175%, 200%


@pytest.fixture
def make_ribbon(tmp_path, monkeypatch, qapp, real_font):
    """Build a RibbonPopup at a given DPI. Its side effects stay out of the real temp files."""
    from PySide6.QtCore import QEvent

    monkeypatch.setattr(ribbon_popup, "SLOTS_FILE", tmp_path / "slots.json")
    monkeypatch.setattr(ribbon_popup, "SLOTS_LOCK", tmp_path / "slots.lock")
    monkeypatch.setattr(ribbon_popup, "RIBBON_DISMISS_FILE", tmp_path / "dismiss.json")
    monkeypatch.setattr(ribbon_popup, "add_to_recent_docs", lambda path: None)
    quick_switcher = pytest.importorskip("quick_switcher")
    monkeypatch.setattr(quick_switcher, "record_recent_file", lambda *args, **kwargs: None)

    built: list = []

    def build(dest: Path, dpi: int = 96, held: bool = False):
        monkeypatch.setattr(ribbon_popup, "_get_display_dpi", lambda _w=None: float(dpi))
        popup = ribbon_popup.RibbonPopup(
            dest, tmp_path / "Downloads", enable_animation=False,
            is_held=held, planned_dest=dest if held else None,
        )
        popup.root.show()
        qapp.processEvents()
        built.append(popup)
        return popup

    yield build
    for popup in built:
        try:
            popup.root.close()
            popup.root.deleteLater()
            qapp.processEvents()
            qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
        except Exception:
            pass
        ribbon_popup.release_slot(popup.slot, popup.pid)


def _widgets(widget):
    from PySide6.QtWidgets import QWidget

    yield widget
    for child in widget.findChildren(QWidget):
        yield child


def test_a_single_file_ribbon_has_undo_next_to_jump_to_file(tmp_path, make_ribbon):
    popup = make_ribbon(tmp_path / "Downloads" / "Documents" / "PDF" / "a.pdf")

    assert popup.undo_btn.text() == "Undo"
    assert popup.jump_btn.text() == "Jump to File ›"
    undo_right = popup.undo_btn.x() + popup.undo_btn.width()
    assert undo_right <= popup.jump_btn.x(), "Undo is left of Jump to File, not on top of it"
    assert popup.jump_btn.x() - undo_right < popup.jump_btn.width() / 2, "side by side"
    assert popup.undo_btn.y() == popup.jump_btn.y()
    assert not hasattr(popup, "file_now_btn") and not hasattr(popup, "keep_here_btn")


def test_a_held_ribbon_has_plain_text_buttons_and_no_undo(tmp_path, make_ribbon):
    popup = make_ribbon(tmp_path / "Downloads" / "Model.3mf", held=True)

    assert popup.is_held
    assert not hasattr(popup, "undo_btn"), "a held file has not been filed, so there is nothing to undo"
    assert popup.file_now_btn.text() == "File now"
    assert popup.keep_here_btn.text() == "Keep here"
    assert popup.file_now_btn.property("fg_color") == "#0B0D12", "dark text on the mint button, for contrast"
    assert popup.keep_here_btn.property("bg_color") == "#171B24"


@pytest.mark.parametrize("held", [False, True])
def test_no_ribbon_text_is_an_emoji(tmp_path, make_ribbon, held):
    popup = make_ribbon(tmp_path / "Downloads" / "Documents" / "PDF" / "a.3mf", held=held)

    for widget in _widgets(popup.root):
        if not hasattr(widget, "text"):
            continue
        text = str(widget.text())
        assert not any(ord(char) >= 0x1F000 for char in text), f"emoji in {text!r}"
    if held:
        assert popup.file_now_btn.text().isascii() and popup.keep_here_btn.text().isascii()


def test_clicking_undo_starts_the_undo_command_and_closes_the_ribbon(tmp_path, make_ribbon, monkeypatch, qapp):
    spawned: list[list[str]] = []
    monkeypatch.setattr(ribbon_popup, "_spawn", spawned.append)
    opened: list = []
    monkeypatch.setattr(ribbon_popup.subprocess, "Popen", lambda *a, **k: opened.append(a))
    dest = tmp_path / "Downloads" / "Documents" / "PDF" / "a b, c.pdf"
    popup = make_ribbon(dest)

    popup.undo_btn.click()
    qapp.processEvents()

    assert spawned == [ribbon_popup._app_command("--undo", "--file", str(dest.resolve()), "--quiet-toast")]
    assert popup.is_closing
    assert opened == [], "Undo does not also open Explorer, as clicking the rest of the ribbon does"


def test_a_second_click_while_the_ribbon_slides_out_does_not_undo_twice(tmp_path, make_ribbon, monkeypatch):
    spawned: list[list[str]] = []
    monkeypatch.setattr(ribbon_popup, "_spawn", spawned.append)
    popup = make_ribbon(tmp_path / "Downloads" / "Documents" / "PDF" / "a.pdf")

    popup._on_undo()
    popup._on_undo()  # the ribbon takes a moment to leave, and the pill is still under the pointer

    assert len(spawned) == 1


def test_a_ribbon_undo_puts_the_file_back_end_to_end(tmp_path, make_ribbon, monkeypatch, toasts, qapp):
    original, filed = _filed(tmp_path, "Lab Report.docx", folder="Documents\\Word")
    other_original, other_filed = _filed(tmp_path, "notes.txt", folder="Documents")
    spawned: list[list[str]] = []
    monkeypatch.setattr(ribbon_popup, "_spawn", spawned.append)
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    popup = make_ribbon(filed)

    popup.undo_btn.click()
    qapp.processEvents()

    # What the new process would run: <python> main.py --undo --file <path> --quiet-toast
    assert len(spawned) == 1 and spawned[0][2] == "--undo"
    assert main.main(spawned[0][2:]) == 0

    assert original.read_text() == "data" and not filed.exists()
    assert other_filed.exists() and not other_original.exists(), "only the ribbon's file is put back"
    assert toasts == [("Put back", "Lab Report.docx is back in Downloads.")]


FOLDERS = [
    "Documents/PDF",
    "Documents/Invoices & Receipts",
    "3D Models/MakerWorld",
    "CAD/Fusion 360",
    "Documents/Invoices & Receipts & Statements 2026/Very Long Quarterly Reports",
    "Dokumente/ÅÄÖ 日本語のフォルダ名",
]


@pytest.mark.parametrize("dpi", DISPLAY_SCALES)
@pytest.mark.parametrize("folder", FOLDERS)
def test_the_bottom_row_fits_at_every_display_scale(tmp_path, make_ribbon, folder, dpi):
    popup = make_ribbon(tmp_path / "Downloads" / folder / "a.pdf", dpi=dpi)

    row = popup.bottom_row
    assert row.sizeHint().width() <= row.width(), "the bottom row asks for more than it gets"
    for pill in (popup.jump_btn, popup.undo_btn):
        assert pill.width() >= pill.sizeHint().width(), f"{pill.text()!r} is cut off"
    label = popup.action_lbl
    assert label.width() >= label.sizeHint().width(), f"{label.text()!r} is cut off"
    assert label.x() + label.width() <= popup.undo_btn.x(), "text runs into Undo"
    # ... and all of it is inside the ribbon itself
    ribbon_right = popup.root.mapToGlobal(popup.root.rect().topRight()).x() + 1
    jump_right = popup.jump_btn.mapToGlobal(popup.jump_btn.rect().topRight()).x() + 1
    assert jump_right <= ribbon_right


def test_the_ribbon_is_360_px_wide_at_100_percent(tmp_path, make_ribbon):
    popup = make_ribbon(tmp_path / "Downloads" / "Documents" / "PDF" / "a.pdf", dpi=96)

    assert popup.root.width() == ribbon_popup.BASE_WIDTH == 360


@pytest.mark.parametrize("dpi", DISPLAY_SCALES)
def test_the_held_ribbon_buttons_are_fully_visible_at_every_display_scale(tmp_path, make_ribbon, dpi):
    # "Keep here" used to be squeezed to a 1 px sliver at 100% by the long "Holding in
    # Downloads (Bambu Studio)" text that was laid out before it.
    popup = make_ribbon(tmp_path / "Downloads" / "Model.3mf", dpi=dpi, held=True)

    for pill in (popup.file_now_btn, popup.keep_here_btn):
        assert pill.width() >= pill.sizeHint().width(), f"{pill.text()!r} is cut off"
    label = popup.action_lbl
    assert label.width() >= label.sizeHint().width()
    assert label.x() + label.width() <= popup.keep_here_btn.x()
    assert label.text().startswith("Hold") or label.text().startswith("Held")


def test_short_folders_are_written_out_in_full(tmp_path, make_ribbon):
    popup = make_ribbon(tmp_path / "Downloads" / "a.pdf")

    assert popup.action_lbl.text() == "Saved to Downloads"
