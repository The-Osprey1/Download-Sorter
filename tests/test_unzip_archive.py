import zipfile
from pathlib import Path
import pytest

from sorter import move_file, watch_root
from unzip_archive import (
    get_strip_prefix,
    is_valid_zip,
    unpack_archive,
)


def create_zip(path: Path, files: dict[str, bytes | str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as z:
        for fname, content in files.items():
            if isinstance(content, str):
                z.writestr(fname, content.encode("utf-8"))
            else:
                z.writestr(fname, content)
    return path


@pytest.fixture
def mock_config(tmp_path):
    downloads = tmp_path / "Downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    return {
        "watch": {
            "folder": str(downloads),
            "settle_seconds": 0,
            "auto_unpack_3d_zips": True,
            "auto_unzip": True,
            "extracted_destination": "Extracted",
        },
        "auto_unpack_3d_zips": True,
        "auto_unzip": True,
        "extracted_destination": "Extracted",
        "keyword_rules": [],
        "extension_rules": [
            {"folder": "3D Models\\STL", "extensions": [".stl"], "enabled": True},
            {"folder": "Archives", "extensions": [".zip"], "enabled": True},
        ],
        "default_folder": "Other",
    }


def test_is_valid_zip(tmp_path):
    # Valid zip
    good_zip = tmp_path / "good.zip"
    create_zip(good_zip, {"hello.txt": "world"})
    assert is_valid_zip(good_zip) is True

    # Missing file
    missing = tmp_path / "missing.zip"
    assert is_valid_zip(missing) is False

    # 0-byte file
    empty = tmp_path / "empty.zip"
    empty.touch()
    assert is_valid_zip(empty) is False

    # Corrupt zip
    corrupt = tmp_path / "corrupt.zip"
    corrupt.write_bytes(b"PK\x03\x04random_garbage_corrupt_data")
    assert is_valid_zip(corrupt) is False


def test_get_strip_prefix():
    # Single root folder
    namelist = ["my_folder/file1.txt", "my_folder/sub/file2.txt", "my_folder/"]
    assert get_strip_prefix(namelist) == "my_folder/"

    # Loose files at root
    namelist = ["file1.txt", "file2.txt", "sub/file3.txt"]
    assert get_strip_prefix(namelist) == ""

    # Multiple root folders
    namelist = ["dir1/file1.txt", "dir2/file2.txt"]
    assert get_strip_prefix(namelist) == ""

    # Single root with macOS metadata
    namelist = ["__MACOSX/._my_folder", "my_folder/file.txt", "my_folder/.DS_Store"]
    assert get_strip_prefix(namelist) == "my_folder/"


def test_unpack_archive_flat(mock_config):
    downloads = Path(mock_config["watch"]["folder"])
    zip_path = downloads / "documents.zip"
    create_zip(zip_path, {
        "report.pdf": "PDF content",
        "notes.txt": "Notes content",
    })

    landing = unpack_archive(zip_path, mock_config)
    assert landing is not None
    assert landing.exists()
    assert landing == downloads / "Extracted" / "documents"

    # Files should exist inside landing folder
    assert (landing / "report.pdf").exists()
    assert (landing / "notes.txt").exists()
    assert (landing / "report.pdf").read_text() == "PDF content"

    # Original zip should be moved to Archives
    assert not zip_path.exists()
    assert (downloads / "Archives" / "documents.zip").exists()


def test_unpack_archive_with_redundant_root(mock_config):
    downloads = Path(mock_config["watch"]["folder"])
    zip_path = downloads / "code_bundle.zip"
    create_zip(zip_path, {
        "code_bundle/src/app.py": "print('hello')",
        "code_bundle/README.md": "# Docs",
    })

    landing = unpack_archive(zip_path, mock_config)
    assert landing is not None

    # Should NOT be Extracted/code_bundle/code_bundle/src/app.py
    assert (landing / "src" / "app.py").exists()
    assert (landing / "README.md").exists()
    assert not (landing / "code_bundle").exists()


def test_zip_slip_prevention(mock_config):
    downloads = Path(mock_config["watch"]["folder"])
    zip_path = downloads / "malicious.zip"

    # Craft zip containing path traversal entry
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w") as z:
        z.writestr("../../escaped.txt", "should not escape")
        z.writestr("safe.txt", "safe content")

    landing = unpack_archive(zip_path, mock_config)
    assert landing is not None

    assert (landing / "safe.txt").exists()
    # Ensure escaped file was not written outside
    assert not (downloads.parent / "escaped.txt").exists()


def test_unpack_archive_dry_run(mock_config):
    downloads = Path(mock_config["watch"]["folder"])
    zip_path = downloads / "preview.zip"
    create_zip(zip_path, {"sample.txt": "hello world"})

    landing = unpack_archive(zip_path, mock_config, dry_run=True)
    assert landing is not None

    # In dry-run, landing directory should NOT be created on disk
    assert not landing.exists()
    # Original zip should NOT be moved
    assert zip_path.exists()
    assert not (downloads / "Archives" / "preview.zip").exists()


def test_sorter_integration_general_zip(mock_config):
    downloads = Path(mock_config["watch"]["folder"])
    zip_path = downloads / "project_assets.zip"
    create_zip(zip_path, {
        "data.csv": "col1,col2\n1,2",
        "chart.png": "image bytes",
    })

    res = move_file(zip_path, mock_config, check_age=False)
    assert res is not None

    # Should land in Extracted/project_assets
    assert res == downloads / "Extracted" / "project_assets"
    assert (res / "data.csv").exists()
    assert (res / "chart.png").exists()

    # Original zip moved to Archives
    assert (downloads / "Archives" / "project_assets.zip").exists()


def test_sorter_integration_3d_precedence(mock_config):
    downloads = Path(mock_config["watch"]["folder"])
    zip_path = downloads / "benchy.zip"
    create_zip(zip_path, {
        "benchy.stl": "solid benchy ...",
        "readme.txt": "print settings",
    })

    res = move_file(zip_path, mock_config, check_age=False)
    assert res is not None

    # Because benchy contains .stl and auto_unpack_3d_zips is True,
    # it routes to 3D Models (not Extracted)
    assert "3D Models" in str(res)
    assert not (downloads / "Extracted" / "benchy").exists()


def test_sorter_integration_auto_unzip_disabled(mock_config):
    downloads = Path(mock_config["watch"]["folder"])
    mock_config["watch"]["auto_unzip"] = False
    mock_config["auto_unzip"] = False

    zip_path = downloads / "manual_only.zip"
    create_zip(zip_path, {"file.txt": "data"})

    res = move_file(zip_path, mock_config, check_age=False)
    assert res is not None

    # Should be moved directly to Archives as a raw zip
    assert res == downloads / "Archives" / "manual_only.zip"
    assert not (downloads / "Extracted" / "manual_only").exists()


def test_corrupt_zip_fallback(mock_config):
    downloads = Path(mock_config["watch"]["folder"])
    zip_path = downloads / "broken.zip"
    zip_path.write_bytes(b"bad zip content")

    res = move_file(zip_path, mock_config, check_age=False)
    assert res is not None

    # Corrupt zip cannot be unzipped, so it safely routes directly to Archives
    assert res == downloads / "Archives" / "broken.zip"
    assert (downloads / "Archives" / "broken.zip").exists()
