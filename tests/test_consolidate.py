"""Unit tests for consolidate.py: flattening nested subfolders and legacy categories."""

from __future__ import annotations

from pathlib import Path
import pytest

import consolidate
import sorter
import sort_undo


@pytest.fixture
def mock_downloads(tmp_path: Path) -> Path:
    """Create a mock Downloads directory with simulated old nested folder hierarchy."""
    downloads = tmp_path / "Downloads"
    downloads.mkdir()

    # Documents subfolders
    (downloads / "Documents" / "PDFs").mkdir(parents=True)
    (downloads / "Documents" / "PDFs" / "annual_report.pdf").write_text("PDF_CONTENT", encoding="utf-8")
    (downloads / "Documents" / "Word").mkdir(parents=True)
    (downloads / "Documents" / "Word" / "essay.docx").write_text("DOCX_CONTENT", encoding="utf-8")

    # Spreadsheets (legacy top-level)
    (downloads / "Spreadsheets").mkdir(parents=True)
    (downloads / "Spreadsheets" / "budget.xlsx").write_text("XLSX_CONTENT", encoding="utf-8")

    # 3D Models subfolders
    (downloads / "3D Models" / "STL").mkdir(parents=True)
    (downloads / "3D Models" / "STL" / "benchy.stl").write_text("STL_CONTENT", encoding="utf-8")

    # Code/Gcode (legacy subfolder)
    (downloads / "Code" / "Gcode").mkdir(parents=True)
    (downloads / "Code" / "Gcode" / "part.gcode").write_text("GCODE_CONTENT", encoding="utf-8")

    # Protected repo inside Code
    repo_dir = downloads / "Code" / "MyRepo"
    repo_dir.mkdir(parents=True)
    (repo_dir / ".git").mkdir()
    (repo_dir / "main.py").write_text("print('hello')", encoding="utf-8")

    # Protected Games folder
    game_dir = downloads / "Games" / "MyGame"
    game_dir.mkdir(parents=True)
    (game_dir / "game.exe").write_text("EXE", encoding="utf-8")
    (game_dir / "assets.dat").write_text("DAT", encoding="utf-8")

    # Loose file at root
    (downloads / "loose_doc.pdf").write_text("LOOSE_PDF", encoding="utf-8")

    return downloads


def test_consolidate_dry_run_leaves_disk_untouched(mock_downloads: Path):
    config = sorter.DEFAULT_CONFIG.copy()
    config["watch"] = {"folder": str(mock_downloads)}

    res = consolidate.consolidate_downloads(
        target_root=mock_downloads,
        config=config,
        dry_run=True,
    )

    assert res["moved"] > 0
    # Ensure source files still exist at their original locations
    assert (mock_downloads / "Documents" / "PDFs" / "annual_report.pdf").exists()
    assert (mock_downloads / "Spreadsheets" / "budget.xlsx").exists()
    assert not (mock_downloads / "Documents" / "annual_report.pdf").exists()


def test_consolidate_flattens_nested_and_legacy_folders(mock_downloads: Path):
    config = sorter.DEFAULT_CONFIG.copy()
    config["watch"] = {"folder": str(mock_downloads)}

    res = consolidate.consolidate_downloads(
        target_root=mock_downloads,
        config=config,
        dry_run=False,
    )

    assert res["moved"] >= 5
    # Documents flattened
    assert (mock_downloads / "Documents" / "annual_report.pdf").exists()
    assert (mock_downloads / "Documents" / "essay.docx").exists()
    assert (mock_downloads / "Documents" / "budget.xlsx").exists()
    assert (mock_downloads / "Documents" / "loose_doc.pdf").exists()

    # 3D Models flattened
    assert (mock_downloads / "3D Models" / "benchy.stl").exists()
    assert (mock_downloads / "3D Models" / "part.gcode").exists()

    # Emptied subfolders removed
    assert not (mock_downloads / "Documents" / "PDFs").exists()
    assert not (mock_downloads / "Documents" / "Word").exists()
    assert not (mock_downloads / "Spreadsheets").exists()
    assert not (mock_downloads / "3D Models" / "STL").exists()
    assert not (mock_downloads / "Code" / "Gcode").exists()

    # Protected folders preserved
    assert (mock_downloads / "Code" / "MyRepo" / "main.py").exists()
    assert (mock_downloads / "Games" / "MyGame" / "game.exe").exists()


def test_consolidate_collision_resolution(tmp_path: Path):
    downloads = tmp_path / "Downloads"
    downloads.mkdir()

    # Create two files with the same name but different content in subfolders
    (downloads / "Documents" / "FolderA").mkdir(parents=True)
    (downloads / "Documents" / "FolderB").mkdir(parents=True)
    (downloads / "Documents" / "FolderA" / "report.pdf").write_text("CONTENT_A", encoding="utf-8")
    (downloads / "Documents" / "FolderB" / "report.pdf").write_text("CONTENT_B", encoding="utf-8")

    config = sorter.DEFAULT_CONFIG.copy()
    config["watch"] = {"folder": str(downloads)}

    res = consolidate.consolidate_downloads(target_root=downloads, config=config, dry_run=False)
    assert res["moved"] == 2

    # One is report.pdf, the other is report (2).pdf
    assert (downloads / "Documents" / "report.pdf").exists()
    alt_file = downloads / "Documents" / "report (2).pdf"
    assert alt_file.exists()

    contents = {
        (downloads / "Documents" / "report.pdf").read_text(encoding="utf-8"),
        alt_file.read_text(encoding="utf-8"),
    }
    assert contents == {"CONTENT_A", "CONTENT_B"}


def test_consolidate_exact_duplicate_quarantined(tmp_path: Path):
    downloads = tmp_path / "Downloads"
    downloads.mkdir()

    (downloads / "Documents" / "FolderA").mkdir(parents=True)
    (downloads / "Documents" / "FolderB").mkdir(parents=True)
    # Identical content
    (downloads / "Documents" / "FolderA" / "dup.pdf").write_text("IDENTICAL", encoding="utf-8")
    (downloads / "Documents" / "FolderB" / "dup.pdf").write_text("IDENTICAL", encoding="utf-8")

    config = sorter.DEFAULT_CONFIG.copy()
    config["watch"] = {"folder": str(downloads)}

    res = consolidate.consolidate_downloads(target_root=downloads, config=config, dry_run=False)
    assert res["moved"] == 1
    assert res["duplicates"] == 1

    assert (downloads / "Documents" / "dup.pdf").exists()
    # Duplicate moved to _Duplicates (under YYYY-MM)
    dup_files = list((downloads / "_Duplicates").rglob("dup*.pdf"))
    assert len(dup_files) == 1


def test_consolidate_undo_restores_original_locations(mock_downloads: Path, monkeypatch):
    config = sorter.DEFAULT_CONFIG.copy()
    config["watch"] = {"folder": str(mock_downloads)}

    # Point sorter history to tmp_path
    history_file = mock_downloads.parent / "history.jsonl"
    monkeypatch.setattr(sorter, "HISTORY_FILE", history_file)
    monkeypatch.setattr(sort_undo, "HISTORY_FILE", history_file)

    res = consolidate.consolidate_downloads(
        target_root=mock_downloads,
        config=config,
        dry_run=False,
    )

    batch_id = res["batch_id"]
    assert (mock_downloads / "Documents" / "annual_report.pdf").exists()

    # Now execute undo
    active = sort_undo.get_active_moves(sort_undo.load_history())
    targets = [rec for rec in reversed(active) if rec.get("batch_id") == batch_id]
    undone = sort_undo.undo_moves(targets)
    assert undone >= 5

    # Original files should be back in their original subfolders!
    assert (mock_downloads / "Documents" / "PDFs" / "annual_report.pdf").exists()
    assert (mock_downloads / "Spreadsheets" / "budget.xlsx").exists()
    assert (mock_downloads / "3D Models" / "STL" / "benchy.stl").exists()


def test_main_cli_dispatch_consolidate(mock_downloads: Path, monkeypatch):
    import main
    config = sorter.DEFAULT_CONFIG.copy()
    config["watch"] = {"folder": str(mock_downloads)}
    monkeypatch.setattr(sorter, "load_config", lambda: config)

    ret = main.main(["--consolidate", "--dry-run", "-t", str(mock_downloads)])
    assert ret == 0


def test_consolidate_reconciles_interrupted_planned_moves(tmp_path: Path, monkeypatch):
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    history_file = tmp_path / "history.jsonl"
    monkeypatch.setattr(sorter, "HISTORY_FILE", history_file)
    monkeypatch.setattr(sort_undo, "HISTORY_FILE", history_file)

    # Simulate an interrupted move where destination was written and matches source
    sub_dir = downloads / "Documents" / "Nested"
    sub_dir.mkdir(parents=True)
    src_file = sub_dir / "doc.pdf"
    src_file.write_text("IDENTICAL", encoding="utf-8")
    dest_file = downloads / "Documents" / "doc.pdf"
    dest_file.write_text("IDENTICAL", encoding="utf-8")

    import safe_ops
    safe_ops._plan("move", src_file, dest_file, batch_id="consolidate_prior123")

    config = sorter.DEFAULT_CONFIG.copy()
    config["watch"] = {"folder": str(downloads)}

    res = consolidate.consolidate_downloads(target_root=downloads, config=config, dry_run=False)

    # Source should be unlinked, destination preserved, and empty dir removed
    assert not src_file.exists()
    assert dest_file.exists()
    assert not sub_dir.exists()

    # The move should now be in history as completed and undoable
    active = sort_undo.get_active_moves(sort_undo.load_history())
    assert any(rec.get("dest") == str(dest_file) for rec in active)
