"""The searchable file list: built from the sorted folder, kept current by the watcher,
and searched by name, folder, website and print details."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from watchdog.events import FileDeletedEvent, FileMovedEvent

import file_index
import prints
import safe_ops
import sorter
import watch


@pytest.fixture(autouse=True)
def _samples_are_finished_downloading(finished_downloads):
    """These tests are about finding files, not about waiting for downloads to finish."""


@pytest.fixture
def downloads(tmp_path) -> Path:
    root = tmp_path / "Downloads"
    root.mkdir()
    return root.resolve()


@pytest.fixture
def config(downloads) -> dict:
    cfg = sorter.get_default_config()
    cfg["downloads_dir"] = str(downloads)
    cfg["watch"] = {"folder": str(downloads), "settle_seconds": 0, "min_age_minutes": 0,
                    "auto_unzip": False, "auto_unpack_3d_zips": False}
    cfg["notifications"] = False
    cfg["ribbon_enabled"] = False
    cfg["quick_switcher_enabled"] = False
    cfg["hold"]["enabled"] = False
    return cfg


def _file(path: Path, text: str = "data", age_days: float = 0.0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    past = time.time() - 120 - age_days * 86400
    os.utime(path, (past, past))
    return path


def _names(results: list[dict]) -> list[str]:
    return [r["name"] for r in results]


@pytest.fixture
def tree(downloads) -> Path:
    _file(downloads / "Banking" / "Chase_Statements_Oct2026.pdf")
    _file(downloads / "Banking" / "chase statement september.pdf", age_days=40)
    _file(downloads / "Documents" / "PDF" / "Personal Statement.pdf")
    _file(downloads / "3D Models" / "STL" / "bracket_v2.stl")
    _file(downloads / "Images" / "holiday.jpg")
    _file(downloads / "loose notes.txt")
    file_index.rebuild(downloads)
    return downloads


# ---------------------------------------------------------------------------
# building
# ---------------------------------------------------------------------------

def test_rebuild_lists_every_file_with_its_folder(tree):
    assert file_index.count() == 6
    (hit,) = file_index.search("holiday")
    assert hit["folder"] == "Images" and hit["ext"] == ".jpg" and hit["path"].endswith("holiday.jpg")
    (top,) = file_index.search("loose")
    assert top["folder"] == ""          # directly in the sorted folder


def test_a_project_is_one_entry_and_dev_folders_are_skipped(downloads):
    _file(downloads / "my-tool" / ".git" / "config")
    _file(downloads / "my-tool" / "src" / "main.py")
    _file(downloads / "site" / "node_modules" / "left-pad" / "index.js")
    _file(downloads / "site" / "index.html")
    _file(downloads / "_Duplicates" / "copy.pdf")
    _file(downloads / ".batch_stage" / "staged.stl")

    file_index.rebuild(downloads)

    assert sorted(_names(file_index.search(""))) == ["index.html", "my-tool"]
    (project,) = file_index.search("tool")
    assert project["is_dir"] == 1
    assert file_index.search("project in:downloads") == [] and _names(file_index.search("project")) == ["my-tool"]


def test_rebuild_drops_what_is_gone_and_keeps_what_it_knew(tree):
    file_index.note_file(tree / "Images" / "holiday.jpg", tree, {"site": "photos.example.com"})
    (tree / "loose notes.txt").unlink()
    _file(tree / "Images" / "new one.jpg")

    result = file_index.rebuild(tree)

    assert result["files"] == 6 and result["removed"] == 1
    assert file_index.search("loose") == []
    assert _names(file_index.search("from:photos.example")) == ["holiday.jpg"]   # the walk didn't forget it


def test_rebuild_fills_in_what_the_history_and_print_log_know(downloads, config, monkeypatch):
    monkeypatch.setattr("learning._read_zone_identifier",
                        lambda path: "[ZoneTransfer]\nZoneId=3\nReferrerUrl=https://www.yourbank.com/x\n")
    filed = sorter.move_file(_file(downloads / "october.pdf"), config)
    plate = _file(downloads / "3D Models" / "3MF" / "plate_1.3mf")
    prints.record_print(plate, {"printer": "X1C", "filament_type": "PLA",
                                "makerworld_meta": {"title": "Cable Clip", "designer": "Ada"}})

    file_index.rebuild(downloads)

    (pdf,) = file_index.search("october")
    assert (pdf["site"], pdf["why"], pdf["rule"]) == ("yourbank.com", "extension", "Documents")
    assert pdf["filed_at"] and pdf["path"] == str(filed)
    assert _names(file_index.search("from:yourbank")) == ["october.pdf"]
    for words in ("pla", "x1c", "cable clip", "ada"):
        assert _names(file_index.search(words)) == ["plate_1.3mf"], words


# ---------------------------------------------------------------------------
# searching
# ---------------------------------------------------------------------------

def test_every_word_must_match_from_its_start_and_plurals_dont_matter(tree):
    assert set(_names(file_index.search("statement"))) == {
        "Chase_Statements_Oct2026.pdf", "chase statement september.pdf", "Personal Statement.pdf"}
    assert set(_names(file_index.search("statements chase"))) == {
        "Chase_Statements_Oct2026.pdf", "chase statement september.pdf"}
    assert _names(file_index.search("stat pers")) == ["Personal Statement.pdf"]
    assert _names(file_index.search("oct")) == ["Chase_Statements_Oct2026.pdf"]   # split out of "Oct2026"
    assert file_index.search("statement holiday") == []


def test_the_folder_name_finds_files_too(tree):
    assert set(_names(file_index.search("banking"))) == {
        "Chase_Statements_Oct2026.pdf", "chase statement september.pdf"}


def test_filters(tree):
    assert _names(file_index.search("statement in:documents")) == ["Personal Statement.pdf"]
    assert _names(file_index.search("type:stl")) == ["bracket_v2.stl"]
    assert _names(file_index.search("type:.STL")) == ["bracket_v2.stl"]
    assert file_index.search("statement type:jpg") == []
    assert file_index.parse_query('chase in:"my bank" type:pdf') == (["chase"], {"in": "my bank", "type": ".pdf"})


def test_newer_files_come_first_when_they_match_as_well(tree):
    assert _names(file_index.search("chase statement")) == [
        "Chase_Statements_Oct2026.pdf", "chase statement september.pdf"]


def test_no_words_lists_the_newest_files(tree):
    _file(tree / "Images" / "just now.jpg")
    os.utime(tree / "Images" / "just now.jpg", None)
    file_index.rebuild(tree)
    assert file_index.search("", limit=1)[0]["name"] == "just now.jpg"


def test_3d_files_can_lead(tree):
    assert file_index.search("", three_d_first=True)[0]["name"] == "bracket_v2.stl"


def test_a_file_that_is_gone_is_left_out_and_forgotten(tree):
    (tree / "Images" / "holiday.jpg").unlink()
    assert file_index.search("holiday") == []
    assert file_index.count() == 5


def test_odd_input_never_breaks_the_search(tree):
    for text in ('"', "a\"b", "(", "*", "AND", "NEAR(", "in:", "100%", "type:", "  "):
        assert isinstance(file_index.search(text), list), text


def test_search_works_without_fts(tree, tmp_path, monkeypatch):
    monkeypatch.setattr(file_index, "_HAS_FTS", False)
    plain = tmp_path / "plain.db"
    file_index.rebuild(tree, target=plain)
    assert set(_names(file_index.search("statements chase", target=plain))) == {
        "Chase_Statements_Oct2026.pdf", "chase statement september.pdf"}
    assert _names(file_index.search("stat pers", target=plain)) == ["Personal Statement.pdf"]
    assert file_index.search("atement", target=plain) == []       # from the start of a word only
    assert _names(file_index.search("type:stl", target=plain)) == ["bracket_v2.stl"]


# ---------------------------------------------------------------------------
# kept current by the watcher
# ---------------------------------------------------------------------------

def test_a_filed_file_is_listed_with_why(downloads, config):
    source = _file(downloads / "Quarterly Report.pdf")
    dest = sorter.move_file(source, config)
    watch._handle_post_move(dest, downloads, config, source=source)

    (hit,) = file_index.search("quarterly")
    assert hit["path"] == str(dest) and hit["why"] == "extension" and hit["filed_at"]
    assert hit["folder"] == "Documents\\PDF"


def test_a_move_by_hand_keeps_what_was_known(downloads, config):
    source = _file(downloads / "Quarterly Report.pdf")
    dest = sorter.move_file(source, config)
    watch._handle_post_move(dest, downloads, config, source=source)

    moved = downloads / "Work" / dest.name
    moved.parent.mkdir()
    os.replace(dest, moved)
    watch.DownloadsHandler(downloads, config).on_moved(FileMovedEvent(str(dest), str(moved)))

    (hit,) = file_index.search("quarterly")
    assert hit["folder"] == "Work" and hit["why"] == "extension"
    assert _names(file_index.search("work")) == ["Quarterly Report.pdf"]


def test_a_deleted_file_leaves_the_list(tree, config):
    gone = tree / "Images" / "holiday.jpg"
    gone.unlink()
    watch.DownloadsHandler(tree, config).on_deleted(FileDeletedEvent(str(gone)))
    assert file_index.count() == 5


def test_switched_off_nothing_is_listed(downloads, config):
    config["search"] = {"enabled": False}
    source = _file(downloads / "Quarterly Report.pdf")
    dest = sorter.move_file(source, config)
    watch._handle_post_move(dest, downloads, config, source=source)
    assert watch.refresh_file_list(config, force=True) is None
    assert not file_index.db_file().exists()


def test_the_watcher_refreshes_in_the_background_once_a_day(tree, config, monkeypatch):
    monkeypatch.setattr(watch, "_FILE_LIST_REFRESHED", 0.0)
    _file(tree / "Images" / "late.jpg")
    thread = watch.refresh_file_list(config)
    assert thread is not None
    thread.join(10)
    assert _names(file_index.search("late")) == ["late.jpg"]
    assert watch.refresh_file_list(config) is None            # not again today
    assert watch.refresh_file_list(config, force=True) is not None


def test_recent_record_reads_only_the_end_of_the_history(downloads):
    src = _file(downloads / "a.pdf")
    dest = safe_ops.move(src, downloads / "Docs" / "a.pdf", meta={"why": "keyword", "rule": "Docs"})
    assert file_index.recent_record(dest)["rule"] == "Docs"
    assert file_index.recent_record(downloads / "Docs" / "other.pdf") is None


def test_find_command_prints_paths(tree, config, monkeypatch, capsys):
    monkeypatch.setattr(sorter, "load_config", lambda: config)
    assert file_index.main(["holiday"]) == 0
    assert capsys.readouterr().out.strip().endswith("holiday.jpg")


def test_it_is_quick_on_a_big_folder(downloads):
    for folder in range(40):
        target = downloads / f"Folder {folder}"
        target.mkdir()
        for n in range(250):
            (target / f"report {folder} item {n}.pdf").write_bytes(b"")
    result = file_index.rebuild(downloads)
    assert result["files"] == 10_000 and result["ms"] < 10_000
    started = time.perf_counter()
    assert len(file_index.search("report 17 item 3")) > 0
    assert time.perf_counter() - started < 0.25
