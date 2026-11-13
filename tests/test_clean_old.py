"""Unit and regression tests for clean_old.py: daily cleanup safety for personal and category folders."""

from __future__ import annotations

import os
from pathlib import Path
import sys
import time
import pytest

import clean_old
import sorter
import space_report
import status


def _create_file_with_age(path: Path, content: str, age_days: float) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    mtime = time.time() - (age_days * 86400)
    os.utime(path, (mtime, mtime))
    return path


def _mixed_programs_config(downloads: Path) -> dict:
    cfg = sorter.get_default_config()
    cfg["downloads_dir"] = str(downloads)
    cfg["watch"] = {"folder": str(downloads)}
    cfg["extension_rules"] = [
        {
            "name": "Installers",
            "folder": "Programs",
            "extensions": [".exe", ".msi", ".msix"],
            "enabled": True,
        },
        {
            "name": "Archives",
            "folder": "Archives",
            "extensions": [".zip", ".tar", ".gz"],
            "enabled": True,
        },
    ]
    return cfg


def test_cleanup_personal_folder_with_mixed_file_types(tmp_path: Path):
    """If setup points Installers at a personal folder like Programs,

    daily cleanup must only consider installer files (.exe, .msi, etc.) by age,
    both at top level and inside two-level subfolders. Unrelated files like
    README.txt or user_guide.pdf must NEVER be recycled.
    """
    downloads = tmp_path / "Downloads"
    programs = downloads / "Programs"

    old_installer = _create_file_with_age(programs / "old_tool_setup.exe", "BIN", 40.0)
    nested_msi = _create_file_with_age(programs / "Windows" / "package.msi", "MSI", 35.0)
    old_readme = _create_file_with_age(programs / "README.txt", "Text documentation", 45.0)
    nested_pdf = _create_file_with_age(programs / "Windows" / "user_guide.pdf", "PDF manual", 50.0)
    old_doc = _create_file_with_age(programs / "notes.docx", "Word notes", 32.0)
    recent_installer = _create_file_with_age(programs / "new_app.exe", "BIN", 5.0)

    config = _mixed_programs_config(downloads)
    allowed_exts = clean_old.get_folder_cleanup_extensions("Programs", config)
    assert allowed_exts == {".exe", ".msi", ".msix"}

    candidates = clean_old.find_old_files(programs, min_age_days=30.0, allowed_extensions=allowed_exts)
    candidate_paths = {p for p, _, _ in candidates}

    assert candidate_paths == {old_installer, nested_msi}
    assert old_readme not in candidate_paths
    assert nested_pdf not in candidate_paths
    assert old_doc not in candidate_paths
    assert recent_installer not in candidate_paths


def test_resolve_cleanup_folders_detects_custom_installer_folder():
    config = {
        "extension_rules": [
            {"name": "Installers", "folder": "CustomPrograms", "extensions": [".exe"]},
            {"name": "Archives", "folder": "CustomZips", "extensions": [".zip"]},
        ]
    }
    folders = clean_old.resolve_cleanup_folders(config)
    assert folders == ["CustomPrograms", "CustomZips"]


def test_cleanup_execution_preserves_personal_mixed_files(tmp_path: Path, monkeypatch):
    """When clean_old runs on a personal folder, non-installer files are preserved."""
    downloads = tmp_path / "Downloads"
    programs = downloads / "Programs"

    old_exe = _create_file_with_age(programs / "setup.exe", "EXE", 40.0)
    old_txt = _create_file_with_age(programs / "license.txt", "TXT", 40.0)

    config = _mixed_programs_config(downloads)
    monkeypatch.setattr(clean_old, "load_config", lambda: config)
    monkeypatch.setattr(clean_old, "watch_root", lambda cfg: downloads)

    recycled = []
    monkeypatch.setattr(clean_old, "send_to_recycle_bin", lambda p: (recycled.append(p), True)[1])
    monkeypatch.setattr("sys.argv", ["clean_old.py", "--days", "30"])

    rc = clean_old.main()
    assert rc == 0
    assert recycled == [old_exe]
    assert old_txt not in recycled


def test_status_and_space_report_respect_extension_filter_on_renamed_folders(tmp_path: Path, monkeypatch):
    """Reclaimable space and leftovers cleanup must match auto_cleanup's extension filter."""
    downloads = tmp_path / "Downloads"
    programs = downloads / "Programs"

    old_exe = _create_file_with_age(programs / "setup.exe", "EXE123", 45.0)
    old_readme = _create_file_with_age(programs / "README.txt", "keep me forever", 45.0)

    config = _mixed_programs_config(downloads)
    count, total_bytes = status.get_reclaimable_space(downloads, days=30.0, config=config)
    assert count == 1
    assert total_bytes == len(b"EXE123")

    rep = space_report.report(config)
    assert rep["old_downloads"] == {"files": 1, "bytes": len(b"EXE123")}

    binned: list[Path] = []
    monkeypatch.setattr(clean_old, "send_to_recycle_bin", lambda p: (binned.append(p), True)[1])
    sent = space_report.send_category_to_recycle_bin(config, "old_downloads")
    assert sent == 1
    assert binned == [old_exe]
    assert old_readme not in binned
