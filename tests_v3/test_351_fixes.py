"""Regression tests for the 3.5.1 fixes to the 3.5.0 classifiers and staging folders."""

from __future__ import annotations

import os
from pathlib import Path
import zipfile

import pytest

from download_sorter import fs_ops
from download_sorter.config import Config, Rule
from download_sorter.db import STAGING_DIR_NAME, StateDB
from download_sorter.paths import AppPaths
from download_sorter.router import FileCandidate, _keyword_matches, evaluate, sniff_magic_extension


def _zip(path: Path, members: dict[str, bytes | str]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return path


def _dest(decision) -> str:
    return str(decision.relative_dest).replace("\\", "/")


# ---------------------------------------------------------------------------
# Keyword shortcuts (hw <-> homework) must not file unrelated files
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("filename", "keyword"),
    [
        ("PCB_rev3.zip", "review"),
        ("Annual review.pdf", "rev"),
        ("sol_system_map.png", "solution"),
        ("calc_mortgage.xlsx", "calculus"),
        ("fluid_mechanical_pump.pdf", "fluid mechanics"),
        ("mech_keyboard_manual.pdf", "mechanics"),
        ("hmk_notes.pdf", "hw"),  # two abbreviations of one word do not match each other
    ],
)
def test_keyword_shortcuts_do_not_match_unrelated_words(filename: str, keyword: str) -> None:
    assert _keyword_matches(filename, keyword) is False


@pytest.mark.parametrize(
    ("filename", "keyword"),
    [
        ("ME3210_hw3.pdf", "homework"),
        ("Homework_4.pdf", "hw"),
        ("Fall_Syl_2026.pdf", "syllabus"),
        ("bracket_rev3.pdf", "revision"),
        ("lab_report_2.pdf", "laboratory"),
        ("laboratory_safety.pdf", "lab"),
        ("hw_4_soln.pdf", "homework solution"),
    ],
)
def test_keyword_shortcuts_still_match_their_meaning(filename: str, keyword: str) -> None:
    assert _keyword_matches(filename, keyword) is True


# ---------------------------------------------------------------------------
# An ordinary zip of a "Word" or "PPT" folder is an archive, not an Office file
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "members",
    [
        {"PPT/Lecture1.pptx": "x", "PPT/Lecture2.pptx": "x"},
        {"Word/essay.docx": "x", "Excel/budget.xlsx": "x"},
        {"word/notes.txt": "x"},
        {"xl/sheet.csv": "x"},
        {"3D/bracket.model": "x"},
    ],
)
def test_zipped_folders_are_not_mistaken_for_office_or_3mf_files(app_paths: AppPaths, members) -> None:
    z = _zip(app_paths.downloads_dir / "backup.zip", members)
    assert sniff_magic_extension(z) == ".zip"


# ---------------------------------------------------------------------------
# Zip contents are routed with the user's own file-type folders
# ---------------------------------------------------------------------------
def test_zip_contents_use_the_users_type_folders(app_paths: AppPaths) -> None:
    cfg = Config()
    cfg.category_map[".pdf"] = "Papers"
    cfg.category_map[".stl"] = "Printing"

    papers = _zip(app_paths.downloads_dir / "papers.zip", {"a.pdf": "x", "b.pdf": "x"})
    assert _dest(evaluate(FileCandidate(filename="papers.zip", path=papers), cfg)) == "Papers/papers.zip"

    parts = _zip(app_paths.downloads_dir / "parts.zip", {"base.stl": "x", "lid.stl": "x", "readme.txt": "x"})
    assert _dest(evaluate(FileCandidate(filename="parts.zip", path=parts), cfg)) == "Printing/parts.zip"


def test_zip_contents_without_a_users_folder_stay_with_archives(app_paths: AppPaths) -> None:
    cfg = Config()
    for ext in (".jpg", ".png"):
        cfg.category_map.pop(ext, None)
    photos = _zip(app_paths.downloads_dir / "photos.zip", {"a.jpg": "x", "b.png": "x"})
    decision = evaluate(FileCandidate(filename="photos.zip", path=photos), cfg)
    assert _dest(decision) == f"{cfg.category_map['.zip']}/photos.zip"

    mixed = _zip(app_paths.downloads_dir / "mixed.zip", {"a.bin": "x", "b.dat": "x", "c.pdf": "x"})
    decision = evaluate(FileCandidate(filename="mixed.zip", path=mixed), cfg)
    assert _dest(decision) == f"{cfg.category_map['.zip']}/mixed.zip"


# ---------------------------------------------------------------------------
# A keyword match inside a zip must pass the rule's other conditions too
# ---------------------------------------------------------------------------
def _resume_rule(**overrides) -> Rule:
    fields = {"id": "cv", "name": "Career", "destination": "Career", "keywords": ["resume"]}
    fields.update(overrides)
    return Rule(**fields)


def test_inner_keyword_match_routes_the_zip(app_paths: AppPaths) -> None:
    cfg = Config(rules=[_resume_rule(extensions=[".pdf"])])
    z = _zip(app_paths.downloads_dir / "applications.zip", {"docs/resume.pdf": "x"})
    decision = evaluate(FileCandidate(filename="applications.zip", path=z), cfg)
    assert decision.matched_rule == "Career"
    assert _dest(decision) == "Career/applications.zip"


@pytest.mark.parametrize(
    ("zip_name", "rule_overrides"),
    [
        ("applications.zip", {"extensions": [".docx"]}),  # inner file is the wrong type
        ("template_pack.zip", {"exclude_keywords": ["template"]}),  # zip's own name is excluded
        ("applications.zip", {"exclude_keywords": ["draft"], "keywords": ["resume"]}),  # inner name excluded
        ("applications.zip", {"domains": ["linkedin.com"], "match_mode": "all"}),  # website required
        ("applications.zip", {"min_size_mb": 50.0}),  # zip far smaller than the limit
        ("applications.zip", {"enabled": False}),
    ],
)
def test_inner_keyword_match_respects_the_rules_other_conditions(
    app_paths: AppPaths, zip_name: str, rule_overrides: dict
) -> None:
    cfg = Config(rules=[_resume_rule(**rule_overrides)])
    z = _zip(app_paths.downloads_dir / zip_name, {"resume_draft.pdf": "x"})
    decision = evaluate(FileCandidate(filename=zip_name, path=z), cfg)
    assert decision.matched_rule != "Career"
    assert not _dest(decision).startswith("Career/")


# ---------------------------------------------------------------------------
# Staging folders are cleaned up and never take a user's folder with them
# ---------------------------------------------------------------------------
def _no_staging_anywhere(root: Path) -> bool:
    return not any(p.name == STAGING_DIR_NAME for p in root.rglob("*"))


def test_extraction_leaves_no_staging_folder(app_paths: AppPaths) -> None:
    root = app_paths.downloads_dir
    target = root / "Archives"
    target.mkdir()
    good = _zip(target / "pack.zip", {"a/one.txt": "1", "two.txt": "2"})
    result = fs_ops.extract_zip_safely(good, target, root, keep_original=True)
    assert result["ok"] is True
    assert (target / "pack" / "a" / "one.txt").is_file()
    assert _no_staging_anywhere(root)

    bad = _zip(target / "evil.zip", {"../escape.txt": "x"})
    result = fs_ops.extract_zip_safely(bad, target, root, keep_original=True)
    assert result["ok"] is False
    assert not (target / "evil").exists()
    assert _no_staging_anywhere(root)


def test_failed_extraction_never_deletes_a_folder_it_did_not_create(
    app_paths: AppPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = app_paths.downloads_dir
    target = root / "Archives"
    target.mkdir()
    z = _zip(target / "pack.zip", {"one.txt": "1"})
    real_replace = os.replace

    def replace_after_user_made_the_folder(src, dst):
        if Path(src).parent.name == STAGING_DIR_NAME:
            # The user creates a folder with the same name just before the rename.
            Path(dst).mkdir()
            (Path(dst) / "mine.txt").write_text("keep me")
            raise OSError("destination exists")
        return real_replace(src, dst)

    monkeypatch.setattr(fs_ops.os, "replace", replace_after_user_made_the_folder)
    result = fs_ops.extract_zip_safely(z, target, root, keep_original=True)
    assert result["ok"] is False
    assert (target / "pack" / "mine.txt").read_text() == "keep me"
    assert _no_staging_anywhere(root)


def test_startup_cleanup_removes_an_empty_staging_folder(app_paths: AppPaths) -> None:
    root = app_paths.downloads_dir
    old = root / STAGING_DIR_NAME / "batch-old"
    old.mkdir(parents=True)
    (old / "partial.bin").write_bytes(b"x")
    past = os.stat(old).st_mtime - 3600
    os.utime(old, (past, past))
    fs_ops.cleanup_orphaned_staging(root)
    assert not (root / STAGING_DIR_NAME).exists()


def test_recovering_an_interrupted_move_removes_its_staged_copy(app_paths: AppPaths, tmp_path: Path) -> None:
    db = StateDB(app_paths.db_file)
    src = app_paths.downloads_dir / "video.mp4"
    src.write_bytes(b"original")
    dest_dir = tmp_path / "OtherDrive" / "Videos"
    dest_dir.mkdir(parents=True)
    (dest_dir / "keep.mp4").write_bytes(b"user file")
    staged = dest_dir / STAGING_DIR_NAME / "xvol-1234abcd" / "video.mp4"
    staged.parent.mkdir(parents=True)
    staged.write_bytes(b"orig")  # partial copy left by a crash

    db.record_move_intent(
        batch_id="b1",
        filename="video.mp4",
        src_path=src,
        dest_path=dest_dir / "video.mp4",
        reason="test",
        matched_rule="Videos",
        match_type="extension",
    )
    summary = db.reconcile_incomplete_moves()

    assert summary["partials_removed"] == 1
    assert not (dest_dir / STAGING_DIR_NAME).exists()
    assert src.read_bytes() == b"original"
    assert (dest_dir / "keep.mp4").read_bytes() == b"user file"
