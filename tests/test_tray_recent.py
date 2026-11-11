"""The tray's "Recently sorted" menu, and a main menu that groups what you do most.

* The last 5 files filed are listed, newest first, each opening File Explorer with
  that file selected.
* Sort now, Undo and Pause sit together right under "Open Download Sorter".
* Every item that was in the menu before is still there and does what it did.
"""

from __future__ import annotations

import json
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import about
import config_manager
import sorter
import tray

SEPARATOR = tray.pystray.Menu.SEPARATOR


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _append_history(*records: dict) -> None:
    sorter.HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with sorter.HISTORY_FILE.open("a", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")


def _move_record(dest: Path, batch: str = "b1") -> dict:
    return {
        "id": uuid.uuid4().hex,
        "batch_id": batch,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "src": str(dest.parents[1] / dest.name),
        "dest": str(dest),
        "planned_id": uuid.uuid4().hex,
    }


def _filed(tmp_path: Path, name: str, folder: str = "PDF", exists: bool = True) -> dict:
    """File a file (on disk when ``exists``) and write its history record. Returns the record."""
    dest = tmp_path / "Downloads" / "Documents" / folder / name
    if exists:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("x", encoding="utf-8")
    record = _move_record(dest)
    _append_history(record)
    return record


def _menu():
    return tray.SorterTray().build_menu()


def _recent_item(menu):
    return next(item for item in menu.items if item is not SEPARATOR and str(item.text) == "Recently sorted")


def _texts(menu) -> list[str]:
    return [item.text for item in menu]


@pytest.fixture
def explorer(monkeypatch):
    """Stand-in for starting File Explorer; ``explorer.call_args_list`` shows what would start."""
    popen = MagicMock()
    monkeypatch.setattr(tray.subprocess, "Popen", popen)
    return popen


# ---------------------------------------------------------------------------
# the submenu
# ---------------------------------------------------------------------------

def test_recently_sorted_lists_the_last_five_files_newest_first(tmp_path):
    for number in range(1, 8):
        _filed(tmp_path, f"file{number}.pdf")

    submenu = _recent_item(_menu()).submenu

    assert _texts(submenu) == [f"file{n}.pdf  ·  PDF" for n in (7, 6, 5, 4, 3)]
    assert all(item.enabled for item in submenu)


def test_an_empty_history_says_so_in_a_greyed_out_line(tmp_path):
    item = _recent_item(_menu())

    assert item.visible, "the submenu stays in the menu when there is nothing to list"
    (only,) = list(item.submenu)
    assert only.text == "Nothing sorted yet" and only.enabled is False


def test_files_put_back_are_not_listed(tmp_path):
    _filed(tmp_path, "kept.pdf")
    undone = _filed(tmp_path, "undone.pdf")
    _append_history({"id": uuid.uuid4().hex, "action": "undo", "ref_id": undone["id"]})

    assert _texts(_recent_item(_menu()).submenu) == ["kept.pdf  ·  PDF"]


def test_bookkeeping_records_are_not_listed(tmp_path):
    record = _filed(tmp_path, "real.pdf")
    _append_history({"id": uuid.uuid4().hex, "action": "planned", "src": "a", "dest": str(tmp_path / "x.pdf")})
    _append_history({"id": uuid.uuid4().hex, "action": "aborted", "planned_id": record["planned_id"]})

    assert _texts(_recent_item(_menu()).submenu) == ["real.pdf  ·  PDF"]


def test_a_file_that_is_gone_stays_listed_but_greyed_out(tmp_path):
    _filed(tmp_path, "here.pdf")
    _filed(tmp_path, "gone.pdf", exists=False)

    gone, here = list(_recent_item(_menu()).submenu)

    assert gone.text.startswith("gone.pdf") and gone.enabled is False
    assert here.text.startswith("here.pdf") and here.enabled is True


def test_ampersands_are_doubled_so_windows_does_not_read_them_as_shortcuts(tmp_path):
    _filed(tmp_path, "Terms & Conditions.pdf", folder="Invoices & Receipts")

    assert _texts(_recent_item(_menu()).submenu) == ["Terms && Conditions.pdf  ·  Invoices && Receipts"]


def test_long_names_are_cut_in_the_middle_and_keep_their_file_type(tmp_path):
    _filed(tmp_path, "A" * 60 + ".pdf")

    (label,) = _texts(_recent_item(_menu()).submenu)

    assert label == "A" * 25 + "…" + "A" * 8 + ".pdf" + "  ·  PDF"
    assert len(label.split("  ·  ")[0]) == tray.RECENT_NAME_MAX


def test_the_submenu_follows_the_history_without_rebuilding_the_menu(tmp_path):
    item = _recent_item(_menu())
    assert _texts(item.submenu) == ["Nothing sorted yet"]

    _filed(tmp_path, "first.pdf")
    assert _texts(item.submenu) == ["first.pdf  ·  PDF"]

    _filed(tmp_path, "second.pdf")
    assert _texts(item.submenu) == ["second.pdf  ·  PDF", "first.pdf  ·  PDF"]


def test_the_history_is_read_once_per_change_not_once_per_question(tmp_path, monkeypatch):
    # pystray asks for a menu's items several times while it builds the menu (is it
    # visible? has it a submenu? what are the entries?), and reading the history means
    # reading the whole file.
    _filed(tmp_path, "a.pdf")
    reads: list = []
    real = config_manager.get_recent_activity

    def counting(*args, **kwargs):
        reads.append((args, kwargs))
        return real(*args, **kwargs)

    monkeypatch.setattr(config_manager, "get_recent_activity", counting)
    menu = _menu()
    item = _recent_item(menu)

    list(menu)                       # pystray walks the visible items ...
    assert item.submenu and item.visible
    list(item.submenu)               # ... then builds the submenu
    list(item.submenu.items)
    str(menu)
    assert reads == [((), {"limit": tray.RECENT_LIMIT})]

    _filed(tmp_path, "b.pdf")
    list(item.submenu)
    list(item.submenu)
    assert len(reads) == 2, "a new record means one new read"


def test_a_file_deleted_since_is_greyed_out_even_when_the_history_has_not_changed(tmp_path):
    record = _filed(tmp_path, "soon-gone.pdf")
    item = _recent_item(_menu())
    assert [entry.enabled for entry in item.submenu] == [True]

    Path(record["dest"]).unlink()

    assert [entry.enabled for entry in item.submenu] == [False]


def test_a_history_that_cannot_be_read_gives_the_empty_line(tmp_path, monkeypatch):
    _filed(tmp_path, "a.pdf")

    def broken(*args, **kwargs):
        raise OSError("disk trouble")

    monkeypatch.setattr(config_manager, "get_recent_activity", broken)

    assert _texts(_recent_item(_menu()).submenu) == ["Nothing sorted yet"]


# ---------------------------------------------------------------------------
# clicking a file
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", [
    "plain.pdf", "with space.pdf", "comma, inside.pdf", "Terms & Conditions.pdf",
])
def test_clicking_a_file_shows_it_selected_in_explorer(tmp_path, explorer, name):
    record = _filed(tmp_path, name)

    (entry,) = list(_recent_item(_menu()).submenu)
    entry(None)

    # The path is its own argument: joined to "/select," Explorer ignores it when it has a space.
    explorer.assert_called_once_with(["explorer.exe", "/select,", record["dest"]])


def test_clicking_a_file_that_is_gone_opens_the_folder_it_was_in(tmp_path, explorer):
    record = _filed(tmp_path, "present.pdf")
    Path(record["dest"]).unlink()

    (entry,) = list(_recent_item(_menu()).submenu)
    entry(None)  # greyed out in the menu, but a menu built earlier can still be open

    explorer.assert_called_once_with(["explorer.exe", str(Path(record["dest"]).parent)])


def test_nothing_opens_when_the_folder_is_gone_too(tmp_path, explorer):
    tray.reveal_in_explorer(tmp_path / "no" / "such" / "file.pdf")

    explorer.assert_not_called()


def test_explorer_not_starting_is_not_an_error(tmp_path, monkeypatch):
    present = tmp_path / "a.pdf"
    present.write_text("x")

    def refuse(*args, **kwargs):
        raise OSError("no explorer")

    monkeypatch.setattr(tray.subprocess, "Popen", refuse)

    tray.reveal_in_explorer(present)  # must not raise


# ---------------------------------------------------------------------------
# the main menu
# ---------------------------------------------------------------------------

def test_sort_undo_and_pause_sit_together_right_under_open(tmp_path):
    items = list(_menu().items)
    layout = ["---" if item is SEPARATOR else str(item.text) for item in items]

    assert layout[:7] == [
        "Open Download Sorter", "Finish setup...", "---",
        "Sort Downloads now", "Undo last sort", "Pause sorting", "---",
    ]
    assert layout[7] == "Recently sorted"


def test_open_download_sorter_is_still_the_default_first_item(tmp_path):
    first = list(_menu().items)[0]

    assert first.text == "Open Download Sorter" and first.default is True


def test_the_filed_today_counts_follow_recently_sorted(tmp_path):
    layout = ["---" if item is SEPARATOR else str(item.text) for item in _menu().items]
    start = layout.index("Recently sorted")

    assert layout[start + 1].endswith("filed today")
    assert layout[start + 2].endswith("held in Downloads")


HANDLERS = {
    "Open Download Sorter": "on_open_home",
    "Finish setup...": "on_finish_setup",
    "Sort Downloads now": "on_sort_now",
    "Undo last sort": "on_undo",
    "Pause sorting": "on_toggle_pause",
    "Rules...": "on_open_rules_manager",
    "Clean up a folder...": "on_remodel",
    "Open Downloads": "on_open_downloads",
    "Open log folder": "on_open_logs",
    "Report a problem...": "on_report_problem",
    f"About {about.APP_NAME}": "on_about",
    "Quit": "on_quit",
}


@pytest.mark.parametrize(("text", "handler"), list(HANDLERS.items()))
def test_every_item_the_menu_had_before_still_does_what_it_did(monkeypatch, text, handler):
    st = tray.SorterTray()
    replacement = MagicMock()
    monkeypatch.setattr(st, handler, replacement)

    item = next(i for i in st.build_menu().items if i is not SEPARATOR and str(i.text) == text)
    item(None)

    replacement.assert_called_once()


def test_the_credit_line_is_still_greyed_out(tmp_path):
    credit = next(i for i in _menu().items if i is not SEPARATOR and str(i.text) == about.CREDIT)

    assert credit.enabled is False
