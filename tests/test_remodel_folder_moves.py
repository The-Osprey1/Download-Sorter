"""Regression tests: "Organize" must move every file in a folder, not just one.

Since 2.0.1 the per-file move in RemodelEngine.apply sat outside the file loop,
so only the last file of each folder was moved, and when that last file was
skipped (an Office lock file, a partial download) the leftover variables could
move the skipped file under another file's name. The older tests used one file
per folder, so they did not notice.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import remodel
import sorter


@pytest.fixture
def root(tmp_path) -> Path:
    target = tmp_path / "Old PC"
    target.mkdir()
    return target


@pytest.fixture
def engine(root) -> remodel.RemodelEngine:
    config = sorter.get_default_config()
    config["downloads_dir"] = str(root)
    config["watch"] = {"folder": str(root), "settle_seconds": 0, "min_age_minutes": 0}
    config["hold"]["enabled"] = False
    return remodel.RemodelEngine(config)


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _plan(engine: remodel.RemodelEngine, root: Path) -> remodel.RemodelPlan:
    # active_folder_days=0: the test files are brand new, which would otherwise
    # mark every folder "active" and keep it intact.
    return engine.scan(root, active_folder_days=0)


def _organize_every_folder(plan: remodel.RemodelPlan) -> dict[str, str]:
    """What a user does by ticking "Organize by file type" on every card."""
    return {str(card.folder_path): "organize" for card in plan.cards}


def _folder_moves(result: dict) -> list[dict]:
    return [m for m in result["executed_moves"] if m["action"] == "organize_folder_item"]


def test_dry_run_lists_every_file_in_the_folder(engine, root):
    files = {
        "Mixed Stuff/report.pdf": "Documents/report.pdf",
        "Mixed Stuff/photo.jpg": "Images/photo.jpg",
        "Mixed Stuff/clip.mp4": "Videos/clip.mp4",
        "Mixed Stuff/Sub/budget.xlsx": "Documents/budget.xlsx",
    }
    for rel in files:
        _write(root / rel, rel)

    result = engine.apply(_plan(engine, root), dry_run=True)

    planned = {
        Path(m["src"]).relative_to(root).as_posix(): Path(m["dest"]).relative_to(root).as_posix()
        for m in _folder_moves(result)
    }
    assert planned == files
    for rel in files:
        assert (root / rel).exists(), "a dry run must not move anything"


def test_apply_moves_every_file_and_undo_puts_them_all_back(engine, root):
    files = {
        "Mixed Stuff/report.pdf": "Documents/report.pdf",
        "Mixed Stuff/photo.jpg": "Images/photo.jpg",
        "Mixed Stuff/clip.mp4": "Videos/clip.mp4",
        "Mixed Stuff/Sub/budget.xlsx": "Documents/budget.xlsx",
    }
    for rel in files:
        _write(root / rel, rel)

    result = engine.apply(_plan(engine, root))

    assert len(_folder_moves(result)) == len(files)
    for src_rel, dest_rel in files.items():
        assert not (root / src_rel).exists(), f"{src_rel} was left behind"
        assert (root / dest_rel).read_text(encoding="utf-8") == src_rel

    undone = engine.undo(result["remodel_id"])
    assert undone["ok"] is True, undone
    for src_rel in files:
        assert (root / src_rel).read_text(encoding="utf-8") == src_rel


def test_skipped_files_stay_put_and_never_take_another_files_name(engine, root):
    report = _write(root / "Thesis/report.pdf", "the real report")
    lock = _write(root / "Thesis/~$report.docx", "office lock file")
    partial = _write(root / "Thesis/movie.mp4.crdownload", "half a download")

    plan = _plan(engine, root)
    engine.apply(plan, card_overrides=_organize_every_folder(plan))

    assert not report.exists()
    assert (root / "Documents/report.pdf").read_text(encoding="utf-8") == "the real report"
    assert lock.read_text(encoding="utf-8") == "office lock file"
    assert partial.read_text(encoding="utf-8") == "half a download"
    pdfs = sorted(p.name for p in (root / "Documents").glob("report*.pdf"))
    assert pdfs == ["report.pdf"], "nothing else may be moved in under the report's name"


def test_same_name_in_two_folders_keeps_both(engine, root):
    _write(root / "Trip 2019/notes.txt", "trip notes")
    _write(root / "Recipes/notes.txt", "recipe notes")

    plan = _plan(engine, root)
    engine.apply(plan, card_overrides=_organize_every_folder(plan))

    texts = sorted(p.read_text(encoding="utf-8") for p in (root / "Documents").glob("notes*.txt"))
    assert texts == ["recipe notes", "trip notes"]


def test_folder_whose_files_are_all_in_subfolders(engine, root):
    """The top folder has no files of its own; before the fix that aborted it."""
    _write(root / "Dump/2018/a.pdf", "a")
    _write(root / "Dump/2019/b.pdf", "b")

    result = engine.apply(_plan(engine, root))

    assert len(_folder_moves(result)) == 2
    assert sorted(p.read_text(encoding="utf-8") for p in (root / "Documents").glob("*.pdf")) == ["a", "b"]


@pytest.mark.parametrize("name", ["New folder", "New folder (2)", "Misc", "Old Downloads",
                                  "Mixed Stuff", "Messy_Folder", "untitled folder", "to sort"])
def test_dumping_ground_names_look_unsorted(name):
    assert remodel.looks_unsorted(name)


@pytest.mark.parametrize("name", ["Wedding 2019", "Taxes 2024", "Senior Design Project",
                                  "Band Album", "Steam", "Old Photos", "2019", ""])
def test_named_folders_do_not_look_unsorted(name):
    assert not remodel.looks_unsorted(name)


def test_named_folder_defaults_to_keep_intact(engine, root):
    _write(root / "Wedding 2019/first_dance.mp4", "video")
    _write(root / "Wedding 2019/guests.xlsx", "list")
    _write(root / "New folder/report.pdf", "pdf")

    plan = _plan(engine, root)
    cards = {c.folder_path.name: c for c in plan.cards}

    assert cards["Wedding 2019"].action == "keep_intact"
    assert not cards["Wedding 2019"].protection.is_protected, "the user can still choose Organize"
    assert cards["New folder"].action == "organize"

    result = engine.apply(plan, dry_run=True)
    moved_from = {Path(m["src"]).parent.name for m in _folder_moves(result)}
    assert moved_from == {"New folder"}


def test_named_folder_can_still_be_organized_on_request(engine, root):
    _write(root / "Wedding 2019/first_dance.mp4", "video")
    _write(root / "Wedding 2019/guests.xlsx", "list")

    plan = _plan(engine, root)
    folder = str(next(c.folder_path for c in plan.cards if c.folder_path.name == "Wedding 2019"))
    result = engine.apply(plan, card_overrides={folder: "organize"}, dry_run=True)

    assert len(_folder_moves(result)) == 2
