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
    assert not (mock_downloads / "Documents" / "PDF" / "annual_report.pdf").exists()


def test_consolidate_files_old_folders_into_the_two_tier_layout(mock_downloads: Path):
    config = sorter.DEFAULT_CONFIG.copy()
    config["watch"] = {"folder": str(mock_downloads)}

    res = consolidate.consolidate_downloads(
        target_root=mock_downloads,
        config=config,
        dry_run=False,
    )

    # essay.docx and benchy.stl already sit in the folder for their kind
    assert res["moved"] == 4
    # Each kind of document in its own folder
    assert (mock_downloads / "Documents" / "PDF" / "annual_report.pdf").exists()
    assert (mock_downloads / "Documents" / "Word" / "essay.docx").exists()
    assert (mock_downloads / "Documents" / "Spreadsheets" / "budget.xlsx").exists()
    assert (mock_downloads / "Documents" / "PDF" / "loose_doc.pdf").exists()

    # 3D files in the folder for their kind
    assert (mock_downloads / "3D Models" / "STL" / "benchy.stl").exists()
    assert (mock_downloads / "3D Models" / "G-code" / "part.gcode").exists()

    # Emptied old folders removed
    assert not (mock_downloads / "Documents" / "PDFs").exists()
    assert not (mock_downloads / "Spreadsheets").exists()
    assert not (mock_downloads / "Code" / "Gcode").exists()

    # Protected folders preserved
    assert (mock_downloads / "Code" / "MyRepo" / "main.py").exists()
    assert (mock_downloads / "Games" / "MyGame" / "game.exe").exists()


def test_consolidate_collision_resolution(tmp_path: Path):
    downloads = tmp_path / "Downloads"
    downloads.mkdir()

    # Create two files with the same name but different content in legacy category subfolders
    (downloads / "Documents" / "PDFs").mkdir(parents=True)
    (downloads / "Documents" / "Word").mkdir(parents=True)
    (downloads / "Documents" / "PDFs" / "report.pdf").write_text("CONTENT_A", encoding="utf-8")
    (downloads / "Documents" / "Word" / "report.pdf").write_text("CONTENT_B", encoding="utf-8")

    config = sorter.DEFAULT_CONFIG.copy()
    config["watch"] = {"folder": str(downloads)}

    res = consolidate.consolidate_downloads(target_root=downloads, config=config, dry_run=False)
    assert res["moved"] == 2

    # One is report.pdf, the other is report (2).pdf
    assert (downloads / "Documents" / "PDF" / "report.pdf").exists()
    alt_file = downloads / "Documents" / "PDF" / "report (2).pdf"
    assert alt_file.exists()

    contents = {
        (downloads / "Documents" / "PDF" / "report.pdf").read_text(encoding="utf-8"),
        alt_file.read_text(encoding="utf-8"),
    }
    assert contents == {"CONTENT_A", "CONTENT_B"}


def test_consolidate_exact_duplicate_quarantined(tmp_path: Path):
    downloads = tmp_path / "Downloads"
    downloads.mkdir()

    (downloads / "Documents" / "PDFs").mkdir(parents=True)
    (downloads / "Documents" / "Word").mkdir(parents=True)
    # Identical content
    (downloads / "Documents" / "PDFs" / "dup.pdf").write_text("IDENTICAL", encoding="utf-8")
    (downloads / "Documents" / "Word" / "dup.pdf").write_text("IDENTICAL", encoding="utf-8")

    config = sorter.DEFAULT_CONFIG.copy()
    config["watch"] = {"folder": str(downloads)}

    res = consolidate.consolidate_downloads(target_root=downloads, config=config, dry_run=False)
    assert res["moved"] == 1
    assert res["duplicates"] == 1

    assert (downloads / "Documents" / "PDF" / "dup.pdf").exists()
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
    assert (mock_downloads / "Documents" / "PDF" / "annual_report.pdf").exists()

    # Now execute undo
    active = sort_undo.get_active_moves(sort_undo.load_history())
    targets = [rec for rec in reversed(active) if rec.get("batch_id") == batch_id]
    undone = sort_undo.undo_moves(targets)
    assert undone == 4

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
    sub_dir = downloads / "Documents" / "PDFs"
    sub_dir.mkdir(parents=True)
    src_file = sub_dir / "doc.pdf"
    src_file.write_text("IDENTICAL", encoding="utf-8")
    dest_file = downloads / "Documents" / "PDF" / "doc.pdf"
    dest_file.parent.mkdir(parents=True, exist_ok=True)
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


def test_consolidate_leaves_your_own_top_level_folders_alone(mock_downloads: Path):
    """Only folders older versions of Download Sorter made are emptied."""
    for name in ("Finance", "XYZFiles", "DownloadSorter-v2.1.0-artifacts"):
        (mock_downloads / name).mkdir()
        (mock_downloads / name / "keep.pdf").write_text(name, encoding="utf-8")
    config = sorter.DEFAULT_CONFIG.copy()
    config["watch"] = {"folder": str(mock_downloads)}

    consolidate.consolidate_downloads(target_root=mock_downloads, config=config)

    for name in ("Finance", "XYZFiles", "DownloadSorter-v2.1.0-artifacts"):
        assert (mock_downloads / name / "keep.pdf").exists()


def test_legacy_lists_only_name_folders_the_app_made():
    """Personal folder names must not ship in the public app."""
    assert consolidate.LEGACY_FOLDERS_TO_CONSOLIDATE == {"spreadsheets"}
    assert consolidate.LEGACY_SUBFOLDERS_TO_CONSOLIDATE == [("code", "gcode"), ("code", "haas")]


def test_old_folders_are_found_whatever_their_capitals(tmp_path: Path):
    downloads = tmp_path / "Downloads"
    (downloads / "SPREADSHEETS").mkdir(parents=True)
    (downloads / "SPREADSHEETS" / "budget.xlsx").write_text("XLSX", encoding="utf-8")
    (downloads / "code" / "HAAS").mkdir(parents=True)
    (downloads / "code" / "HAAS" / "O1234.nc").write_text("%\nO1234\nG0 X0\nM30\n%", encoding="utf-8")
    config = sorter.DEFAULT_CONFIG.copy()
    config["watch"] = {"folder": str(downloads)}

    consolidate.consolidate_downloads(target_root=downloads, config=config)

    assert (downloads / "Documents" / "Spreadsheets" / "budget.xlsx").exists()
    assert not (downloads / "SPREADSHEETS").exists()
    assert not (downloads / "code" / "HAAS").exists()


def test_consolidate_leaves_personal_subfolders_inside_categories_alone(mock_downloads: Path, tmp_path: Path):
    """Personal subfolders inside categories (e.g. Documents\\Tax Records) must never be flattened or removed."""
    personal_doc_dir = mock_downloads / "Documents" / "Tax Records"
    personal_doc_dir.mkdir(parents=True)
    personal_doc = personal_doc_dir / "taxes_2025.pdf"
    personal_doc.write_text("CONFIDENTIAL", encoding="utf-8")

    empty_personal_dir = mock_downloads / "Documents" / "Empty Personal"
    empty_personal_dir.mkdir(parents=True)

    nested_type_dir = mock_downloads / "Documents" / "PDF" / "Old Taxes"
    nested_type_dir.mkdir(parents=True)
    nested_type_doc = nested_type_dir / "w2.pdf"
    nested_type_doc.write_text("W2", encoding="utf-8")

    personal_model_dir = mock_downloads / "3D Models" / "Flanged Mace"
    personal_model_dir.mkdir(parents=True)
    personal_model = personal_model_dir / "bracket.step"
    personal_model.write_text("STEP", encoding="utf-8")

    maker_dir = mock_downloads / "3D Models" / "MakerWorld" / "Some Designer" / "Cable Clip"
    maker_dir.mkdir(parents=True)
    maker_stl = maker_dir / "clip.stl"
    maker_stl.write_text("STL", encoding="utf-8")

    config = sorter.DEFAULT_CONFIG.copy()
    config["watch"] = {"folder": str(mock_downloads)}

    consolidate.consolidate_downloads(target_root=mock_downloads, config=config, dry_run=False)

    assert personal_doc.exists()
    assert personal_doc_dir.exists()
    assert empty_personal_dir.exists()
    assert nested_type_doc.exists()
    assert nested_type_dir.exists()
    assert not (mock_downloads / "Documents" / "PDF" / "taxes_2025.pdf").exists()

    assert personal_model.exists()
    assert personal_model_dir.exists()
    assert maker_stl.exists()
    assert not (mock_downloads / "CAD" / "STEP" / "bracket.step").exists()

    # A watch root placed inside a parent folder named "Documents" must still consolidate legacy subfolders inside itself
    nested_root = tmp_path / "Documents" / "TestDownloads"
    legacy_pdf = nested_root / "Documents" / "PDFs" / "nested_root_doc.pdf"
    legacy_pdf.parent.mkdir(parents=True)
    legacy_pdf.write_text("LEGACY", encoding="utf-8")
    nested_cfg = sorter.DEFAULT_CONFIG.copy()
    nested_cfg["watch"] = {"folder": str(nested_root)}
    consolidate.consolidate_downloads(target_root=nested_root, config=nested_cfg, dry_run=False)
    assert (nested_root / "Documents" / "PDF" / "nested_root_doc.pdf").exists()
    assert not legacy_pdf.exists()


def test_consolidate_dry_run_never_creates_destination_directories(tmp_path: Path):
    """A dry run must never create destination directories on disk."""
    downloads = tmp_path / "Downloads"
    downloads.mkdir()

    # Create a legacy subfolder with an installer that routes to Installers
    (downloads / "Documents" / "PDFs").mkdir(parents=True)
    (downloads / "Documents" / "PDFs" / "tool.exe").write_text("BIN", encoding="utf-8")

    config = sorter.DEFAULT_CONFIG.copy()
    config["watch"] = {"folder": str(downloads)}

    installers_dir = downloads / "Installers"
    assert not installers_dir.exists()

    res = consolidate.consolidate_downloads(target_root=downloads, config=config, dry_run=True)

    assert res["moved"] > 0
    assert not installers_dir.exists()
