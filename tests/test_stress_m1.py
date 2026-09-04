"""Adversarial stress tests for 3D Archive Unpacker and Daemon Config Reloading."""

from __future__ import annotations

import io
import json
import os
import random
import shutil
import tempfile
import threading
import time
import zipfile
from pathlib import Path

import pytest

from sorter import (
    CAD_EXTENSIONS,
    evaluate_routing,
    load_active_project,
    watch_root,
)
from unzip_3d import JUNK_NAMES, TARGET_3D_EXTENSIONS, is_3d_archive, unpack_3d_archive
from watch import ConfigReloader, acquire_single_instance_lock


def create_zip(path: Path, entries: dict[str, bytes | str]) -> Path:
    """Helper to write custom zip files with exact entry names and bytes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as z:
        for name, content in entries.items():
            if isinstance(content, str):
                z.writestr(name, content.encode("utf-8"))
            else:
                z.writestr(name, content)
    return path


@pytest.fixture
def stress_config(tmp_path: Path) -> dict:
    downloads = tmp_path / "Downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    return {
        "watch": {"folder": str(downloads), "settle_seconds": 0.1},
        "auto_unpack_3d_zips": True,
        "keyword_rules": [
            {
                "name": "IME Coursework",
                "folder": "Documents\\School",
                "match": ["ime"],
                "extensions": [".pdf", ".docx"],
                "enabled": True,
            }
        ],
        "extension_rules": [
            {"name": "3D Models STL", "folder": "3D Models\\STL", "extensions": [".stl"], "enabled": True},
            {"name": "3D Models 3MF", "folder": "3D Models\\3MF", "extensions": [".3mf"], "enabled": True},
            {"name": "Archives", "folder": "Archives", "extensions": [".zip"], "enabled": True},
        ],
        "default_folder": "Other",
    }


# =============================================================================
# 1. CORRUPTED ZIP FILES & UNUSUAL ARCHIVE CORRUPTION
# =============================================================================

def test_corrupted_zero_byte_zip(tmp_path: Path, stress_config: dict) -> None:
    """Empty 0-byte file with .zip extension."""
    downloads = watch_root(stress_config)
    z = downloads / "zero_bytes.zip"
    z.touch()

    assert is_3d_archive(z) == []
    extracted = unpack_3d_archive(z, stress_config)
    assert extracted == []
    # File must not be deleted or corrupted
    assert z.exists()


def test_corrupted_truncated_header_zip(tmp_path: Path, stress_config: dict) -> None:
    """Truncated zip file with partial local header signature."""
    downloads = watch_root(stress_config)
    z = downloads / "truncated_header.zip"
    z.write_bytes(b"PK\x03\x04\x14\x00\x00\x00\x08\x00\x12\x34")

    assert is_3d_archive(z) == []
    extracted = unpack_3d_archive(z, stress_config)
    assert extracted == []
    assert z.exists()


def test_corrupted_random_binary_garbage(tmp_path: Path, stress_config: dict) -> None:
    """Completely random binary garbage simulating corrupted download."""
    downloads = watch_root(stress_config)
    z = downloads / "garbage.zip"
    random_bytes = bytes(random.getrandbits(8) for _ in range(2048))
    z.write_bytes(random_bytes)

    assert is_3d_archive(z) == []
    extracted = unpack_3d_archive(z, stress_config)
    assert extracted == []
    assert z.exists()


def test_nonexistent_zip_path(tmp_path: Path, stress_config: dict) -> None:
    """Path that does not exist on disk."""
    z = tmp_path / "Downloads" / "nonexistent.zip"
    assert is_3d_archive(z) == []
    extracted = unpack_3d_archive(z, stress_config)
    assert extracted == []


# =============================================================================
# 2. EMPTY ZIP ARCHIVES & JUNK-ONLY ARCHIVES
# =============================================================================

def test_valid_empty_zip(tmp_path: Path, stress_config: dict) -> None:
    """Valid zip archive containing zero entries."""
    downloads = watch_root(stress_config)
    z = downloads / "valid_empty.zip"
    with zipfile.ZipFile(z, "w") as empty_z:
        pass

    assert is_3d_archive(z) == []
    extracted = unpack_3d_archive(z, stress_config)
    assert extracted == []
    assert z.exists()


def test_directories_only_zip(tmp_path: Path, stress_config: dict) -> None:
    """Zip archive containing only folder directory entries and no files."""
    downloads = watch_root(stress_config)
    z = downloads / "dirs_only.zip"
    create_zip(z, {
        "folder1/": b"",
        "folder1/subfolder/": b"",
        "empty_dir/": b"",
    })

    assert is_3d_archive(z) == []
    extracted = unpack_3d_archive(z, stress_config)
    assert extracted == []
    assert z.exists()


def test_junk_names_only_zip(tmp_path: Path, stress_config: dict) -> None:
    """Zip containing only OS junk files (.DS_Store, desktop.ini, readme, license)."""
    downloads = watch_root(stress_config)
    z = downloads / "junk_only.zip"
    create_zip(z, {
        "desktop.ini": "[.ShellClassInfo]",
        "thumbs.db": b"\xd0\xcf\x11\xe0",
        ".DS_Store": b"\x00\x00\x00\x01Bud1",
        "readme.txt": "Project documentation",
        "README.MD": "# Documentation",
        "license.txt": "MIT License",
        "license.pdf": "%PDF-1.4 ...",
    })

    assert is_3d_archive(z) == []
    extracted = unpack_3d_archive(z, stress_config)
    assert extracted == []
    assert z.exists()


# =============================================================================
# 3. PATH TRAVERSAL ATTEMPTS (../../, root slashes, Windows backslashes)
# =============================================================================

def test_path_traversal_relative_parent(tmp_path: Path, stress_config: dict) -> None:
    """Zip containing ../../ path traversal attempts."""
    downloads = watch_root(stress_config)
    z = downloads / "traversal_parent.zip"
    create_zip(z, {
        "../../escape1.stl": "solid escape1",
        "subdir/../../../escape2.3mf": "3mf escape2",
    })

    model_files = is_3d_archive(z)
    assert len(model_files) == 2

    extracted = unpack_3d_archive(z, stress_config)
    assert len(extracted) == 2

    # Verify every extracted file is strictly within the Downloads hierarchy
    for file_path in extracted:
        resolved = file_path.resolve()
        assert resolved.is_relative_to(downloads), f"Path traversal leaked outside downloads: {resolved}"
        assert file_path.exists()
        # Escaped filenames are flattened to their base name
        assert file_path.name in ("escape1.stl", "escape2.3mf")

    # Verify no file escaped outside downloads
    escaped1 = tmp_path / "escape1.stl"
    escaped2 = tmp_path / "escape2.3mf"
    assert not escaped1.exists()
    assert not escaped2.exists()


def test_path_traversal_windows_backslashes(tmp_path: Path, stress_config: dict) -> None:
    """Zip containing Windows-style backslash traversal ..\\..\\."""
    downloads = watch_root(stress_config)
    z = downloads / "traversal_win.zip"
    create_zip(z, {
        "..\\..\\win_escape.stl": "solid win_escape",
        "folder\\..\\..\\nested_escape.step": "step nested_escape",
    })

    extracted = unpack_3d_archive(z, stress_config)
    for file_path in extracted:
        resolved = file_path.resolve()
        assert resolved.is_relative_to(downloads), f"Backslash traversal leaked: {resolved}"
        assert file_path.exists()
        assert file_path.name in ("win_escape.stl", "nested_escape.step")


def test_path_traversal_absolute_paths(tmp_path: Path, stress_config: dict) -> None:
    """Zip containing root-prefixed or Windows drive absolute paths."""
    downloads = watch_root(stress_config)
    z = downloads / "traversal_abs.zip"
    create_zip(z, {
        "/root_abs.stl": "solid root",
        "\\win_root.3mf": "3mf win root",
        "C:/abs_drive.stl": "solid c drive",
    })

    extracted = unpack_3d_archive(z, stress_config)
    for file_path in extracted:
        resolved = file_path.resolve()
        assert resolved.is_relative_to(downloads), f"Absolute path leaked: {resolved}"
        assert file_path.exists()
        assert file_path.name in ("root_abs.stl", "win_root.3mf", "abs_drive.stl")


# =============================================================================
# 4. MACOS ZIP ARTIFACTS (__MACOSX, ._ files)
# =============================================================================

def test_macos_artifacts_only_ignored(tmp_path: Path, stress_config: dict) -> None:
    """Zip created by macOS Finder with __MACOSX directory and ._ resource forks only."""
    downloads = watch_root(stress_config)
    z = downloads / "macos_only.zip"
    create_zip(z, {
        "__MACOSX/": b"",
        "__MACOSX/._bracket.stl": b"\x00\x05\x16\x07AppleDouble",
        "sub/__MACOSX/._subpart.3mf": b"\x00\x05\x16\x07AppleDouble",
        "._root_hidden.stl": b"\x00\x05\x16\x07AppleDouble",
        "__MACOSX/license.txt": "license text",
    })

    assert is_3d_archive(z) == []
    extracted = unpack_3d_archive(z, stress_config)
    assert extracted == []
    assert z.exists()


def test_macos_mixed_with_legitimate_models(tmp_path: Path, stress_config: dict) -> None:
    """Zip containing valid 3D models alongside macOS metadata files."""
    downloads = watch_root(stress_config)
    z = downloads / "macos_legit.zip"
    create_zip(z, {
        "real_model.stl": "solid real",
        "__MACOSX/._real_model.stl": b"\x00\x05\x16\x07AppleDouble",
        "parts/bearing.3mf": "3mf bearing content",
        "parts/__MACOSX/._bearing.3mf": b"\x00\x05\x16\x07AppleDouble",
        "._loose_fork.step": b"metadata",
    })

    detected = is_3d_archive(z)
    assert set(detected) == {"real_model.stl", "parts/bearing.3mf"}

    extracted = unpack_3d_archive(z, stress_config)
    extracted_names = [p.name for p in extracted]
    assert "real_model.stl" in extracted_names
    assert "bearing.3mf" in extracted_names
    assert not any(name.startswith("._") for name in extracted_names)
    assert not any("__MACOSX" in str(p) for p in extracted)


# =============================================================================
# 5. MIXED ZIP FILES (3D Models + Documents + Executables)
# =============================================================================

def test_mixed_zip_3d_docs_executables(tmp_path: Path, stress_config: dict) -> None:
    """Zip containing 3D models, office documents, and dangerous executables."""
    downloads = watch_root(stress_config)
    z = downloads / "cad_and_software.zip"
    create_zip(z, {
        "enclosure.stl": "solid enclosure",
        "spec_sheet.pdf": "%PDF-1.4 spec",
        "bill_of_materials.xlsx": "excel binary",
        "installer.exe": "MZ binary PE executable",
        "flash_firmware.bat": "@echo off\r\necho Flashing...",
        "powershell_script.ps1": "Write-Host 'test'",
    })

    detected = is_3d_archive(z)
    # Only 3D model detected
    assert detected == ["enclosure.stl"]

    extracted = unpack_3d_archive(z, stress_config)
    assert len(extracted) == 1
    assert extracted[0].name == "enclosure.stl"
    assert extracted[0].exists()

    # Destination directory must contain ONLY enclosure.stl, NOT executables or docs
    parent_dir = extracted[0].parent
    extracted_siblings = [p.name for p in parent_dir.iterdir() if p.is_file()]
    assert "installer.exe" not in extracted_siblings
    assert "flash_firmware.bat" not in extracted_siblings
    assert "spec_sheet.pdf" not in extracted_siblings

    # Original zip preserved in Archives with all files intact
    archived_zip = downloads / "Archives" / "cad_and_software.zip"
    assert archived_zip.exists()
    with zipfile.ZipFile(archived_zip, "r") as check_z:
        names = check_z.namelist()
        assert "installer.exe" in names
        assert "spec_sheet.pdf" in names
        assert "flash_firmware.bat" in names
        assert "enclosure.stl" in names


def test_mixed_zip_without_3d_models(tmp_path: Path, stress_config: dict) -> None:
    """Zip containing only documents, archives, and executables (no 3D models)."""
    downloads = watch_root(stress_config)
    z = downloads / "office_bundle.zip"
    create_zip(z, {
        "quarterly_report.docx": "word doc",
        "budget.xlsx": "excel sheet",
        "setup.exe": "MZ binary",
    })

    assert is_3d_archive(z) == []
    extracted = unpack_3d_archive(z, stress_config)
    assert extracted == []
    # Untouched in downloads
    assert z.exists()


# =============================================================================
# 6. RAPID MTIME CHANGES (Simulating Rapid UI Saves)
# =============================================================================

def test_rapid_mtime_changes_config(tmp_path: Path) -> None:
    """Rapid successive updates to config.json with monotonic mtime advance."""
    cfg_file = tmp_path / "config.json"
    ap_file = tmp_path / "active_project.json"

    initial_cfg = {"default_folder": "Other", "version": 0}
    cfg_file.write_text(json.dumps(initial_cfg), encoding="utf-8")
    ap_file.write_text(json.dumps({"enabled": False}), encoding="utf-8")

    reloader = ConfigReloader(config_path=cfg_file, active_project_path=ap_file)
    assert reloader.config["version"] == 0

    base_time = time.time()
    for v in range(1, 51):
        data = {"default_folder": "Other", "version": v, "rule_count": v * 2}
        cfg_file.write_text(json.dumps(data), encoding="utf-8")
        advance = base_time + (v * 0.1)
        os.utime(cfg_file, (advance, advance))

        changed, current = reloader.check_for_updates()
        assert changed is True, f"Failed to detect change at iteration {v}"
        assert current["version"] == v
        assert current["rule_count"] == v * 2

    # Final check with no file change returns False
    changed, final_cfg = reloader.check_for_updates()
    assert changed is False
    assert final_cfg["version"] == 50


def test_rapid_mtime_changes_active_project(tmp_path: Path) -> None:
    """Rapid successive updates to active_project.json."""
    cfg_file = tmp_path / "config.json"
    ap_file = tmp_path / "active_project.json"

    cfg_file.write_text(json.dumps({"default_folder": "Other"}), encoding="utf-8")
    ap_file.write_text(json.dumps({"enabled": False, "version": 0}), encoding="utf-8")

    reloader = ConfigReloader(config_path=cfg_file, active_project_path=ap_file)

    base_time = time.time()
    for v in range(1, 31):
        ap_data = {"enabled": True, "project_name": f"Project_{v}", "version": v}
        ap_file.write_text(json.dumps(ap_data), encoding="utf-8")
        advance = base_time + (v * 0.1)
        os.utime(ap_file, (advance, advance))

        changed, cfg = reloader.check_for_updates()
        assert changed is True, f"Failed to detect active_project change at iteration {v}"

    changed, _ = reloader.check_for_updates()
    assert changed is False


# =============================================================================
# 7. TEMPORARY & PARTIAL FILE WRITES DURING RELOAD (Crash Safety)
# =============================================================================

def test_partial_json_write_preserves_stable_config(tmp_path: Path) -> None:
    """Truncated JSON write (e.g. power loss or mid-write read) does not crash and preserves config."""
    cfg_file = tmp_path / "config.json"
    ap_file = tmp_path / "active_project.json"

    good_config = {"default_folder": "Other", "stable": True, "version": 42}
    cfg_file.write_text(json.dumps(good_config), encoding="utf-8")
    ap_file.write_text(json.dumps({"enabled": False}), encoding="utf-8")

    reloader = ConfigReloader(config_path=cfg_file, active_project_path=ap_file)
    assert reloader.config["stable"] is True

    # 1. Truncated mid-write
    cfg_file.write_text('{"default_folder": "Other", "stable": ', encoding="utf-8")
    future = time.time() + 10.0
    os.utime(cfg_file, (future, future))

    changed, cfg = reloader.check_for_updates()
    # Must NOT crash, must retain good_config
    assert cfg["stable"] is True
    assert cfg["version"] == 42
    assert reloader.config["stable"] is True

    # 2. 0-byte file write
    cfg_file.write_text("", encoding="utf-8")
    future += 1.0
    os.utime(cfg_file, (future, future))

    changed, cfg = reloader.check_for_updates()
    assert cfg["stable"] is True
    assert cfg["version"] == 42

    # 3. Valid write recovers immediately
    recovered_config = {"default_folder": "Other", "stable": True, "version": 43, "recovered": True}
    cfg_file.write_text(json.dumps(recovered_config), encoding="utf-8")
    future += 1.0
    os.utime(cfg_file, (future, future))

    changed, cfg = reloader.check_for_updates()
    assert changed is True
    assert cfg["recovered"] is True
    assert cfg["version"] == 43


def test_non_dict_json_resilience(tmp_path: Path) -> None:
    """JSON that parses to non-dict (e.g. list or string) is safely rejected."""
    cfg_file = tmp_path / "config.json"
    ap_file = tmp_path / "active_project.json"

    good_config = {"default_folder": "Other", "version": 1}
    cfg_file.write_text(json.dumps(good_config), encoding="utf-8")
    ap_file.write_text(json.dumps({"enabled": False}), encoding="utf-8")

    reloader = ConfigReloader(config_path=cfg_file, active_project_path=ap_file)

    # Write a JSON array [1, 2, 3]
    cfg_file.write_text("[1, 2, 3]", encoding="utf-8")
    future = time.time() + 5.0
    os.utime(cfg_file, (future, future))

    changed, cfg = reloader.check_for_updates()
    # Should not crash, and should not overwrite reloader.config with list
    assert isinstance(cfg, dict)
    assert cfg["version"] == 1


def test_concurrent_writer_reader_stress(tmp_path: Path) -> None:
    """Multi-threaded stress: writer randomly writes valid, truncated, and atomic configs
    while background reader calls check_for_updates() continuously.
    """
    cfg_file = tmp_path / "config.json"
    ap_file = tmp_path / "active_project.json"

    initial_cfg = {"default_folder": "Other", "counter": 0}
    cfg_file.write_text(json.dumps(initial_cfg), encoding="utf-8")
    ap_file.write_text(json.dumps({"enabled": False}), encoding="utf-8")

    reloader = ConfigReloader(config_path=cfg_file, active_project_path=ap_file)

    stop_event = threading.Event()
    reader_exceptions: list[Exception] = []
    read_count = 0

    def reader_loop() -> None:
        nonlocal read_count
        while not stop_event.is_set():
            try:
                changed, cfg = reloader.check_for_updates()
                assert isinstance(cfg, dict)
                assert "default_folder" in cfg
                read_count += 1
            except Exception as e:
                reader_exceptions.append(e)
            time.sleep(0.002)

    reader_thread = threading.Thread(target=reader_loop, daemon=True)
    reader_thread.start()

    try:
        # Writer performs 40 rapid operations with various patterns
        base = time.time()
        for i in range(1, 41):
            advance = base + i * 0.05
            op = i % 4
            if op == 0:
                # Valid standard write
                data = {"default_folder": "Other", "counter": i}
                cfg_file.write_text(json.dumps(data), encoding="utf-8")
                os.utime(cfg_file, (advance, advance))
            elif op == 1:
                # Truncated partial write
                cfg_file.write_text('{"default_folder": ', encoding="utf-8")
                os.utime(cfg_file, (advance, advance))
            elif op == 2:
                # Atomic temp file + replace with retry for Windows transient file locks
                tmp_target = tmp_path / f"cfg_{i}.tmp"
                tmp_target.write_text(json.dumps({"default_folder": "Other", "counter": i, "atomic": True}), encoding="utf-8")
                for _ in range(10):
                    try:
                        os.replace(tmp_target, cfg_file)
                        os.utime(cfg_file, (advance, advance))
                        break
                    except PermissionError:
                        time.sleep(0.005)
            elif op == 3:
                # Active project update
                ap_file.write_text(json.dumps({"enabled": True, "project_name": f"P{i}"}), encoding="utf-8")
                os.utime(ap_file, (advance, advance))

            time.sleep(0.005)
    finally:
        stop_event.set()
        reader_thread.join(timeout=3.0)

    assert not reader_exceptions, f"Reader thread caught exceptions: {reader_exceptions}"
    assert read_count > 20, f"Expected >20 reads, got {read_count}"
    assert isinstance(reloader.config, dict)
    assert reloader.config["default_folder"] == "Other"


# =============================================================================
# 8. ACTIVE PROJECT DYNAMIC DISK LOAD & MALFORMED EXPIRY
# =============================================================================

def test_load_active_project_malformed_disk_states(tmp_path: Path) -> None:
    """Stress test load_active_project against malformed json, non-numeric expiry, missing fields."""
    ap_file = tmp_path / "active_project.json"

    # 1. Non-existent file
    assert load_active_project(path=tmp_path / "none.json") is None

    # 2. Corrupted JSON syntax
    ap_file.write_text("{malformed: [", encoding="utf-8")
    assert load_active_project(path=ap_file) is None

    # 3. JSON array instead of dict
    ap_file.write_text("[1, 2, 3]", encoding="utf-8")
    assert load_active_project(path=ap_file) is None

    # 4. Disabled active project
    ap_file.write_text(json.dumps({"enabled": False, "project_name": "Off"}), encoding="utf-8")
    assert load_active_project(path=ap_file) is None

    # 5. Non-numeric expires_at (treated as invalid/expired, returns None)
    ap_file.write_text(json.dumps({
        "enabled": True,
        "project_name": "ValidName",
        "target_directory": str(tmp_path),
        "expires_at": "not-a-number",
    }), encoding="utf-8")
    loaded = load_active_project(path=ap_file)
    assert loaded is None


# =============================================================================
# 9. SINGLE INSTANCE MUTEX STRESS
# =============================================================================

def test_acquire_single_instance_lock() -> None:
    """Verify single instance lock behaves correctly on this OS."""
    unique_lock_name = f"TestLock_{time.time()}_{random.randint(1, 10000)}"
    lock1 = acquire_single_instance_lock(unique_lock_name)
    assert lock1 is not None

    # Attempting to acquire again with same name must return None
    lock2 = acquire_single_instance_lock(unique_lock_name)
    assert lock2 is None
