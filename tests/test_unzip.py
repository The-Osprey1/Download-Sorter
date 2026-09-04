import io
import zipfile
from pathlib import Path
import pytest
from sorter import watch_root
from unzip_3d import is_3d_archive, unpack_3d_archive, TARGET_3D_EXTENSIONS, JUNK_NAMES


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
        "watch": {"folder": str(downloads)},
        "auto_unpack_3d_zips": True,
        "keyword_rules": [],
        "extension_rules": [
            {"folder": "3D Models\\STL", "extensions": [".stl"], "enabled": True},
            {"folder": "3D Models\\STEP", "extensions": [".step"], "enabled": True},
            {"folder": "3D Models\\3MF", "extensions": [".3mf"], "enabled": True},
            {"folder": "Archives", "extensions": [".zip"], "enabled": True},
        ],
        "default_folder": "Other",
    }


def test_is_3d_archive_positive(tmp_path):
    zip_path = tmp_path / "test_model.zip"
    create_zip(zip_path, {
        "model.stl": "solid model ...",
        "nested/part.step": "ISO-10303-21 ...",
        "readme.txt": "instructions",
        "license.pdf": "license terms",
        ".DS_Store": "junk",
        "nested/__MACOSX/._part.step": "mac os metadata",
    })

    found = is_3d_archive(zip_path)
    assert "model.stl" in found
    assert "nested/part.step" in found
    assert "readme.txt" not in found
    assert "license.pdf" not in found
    assert ".DS_Store" not in found
    assert len(found) == 2


def test_is_3d_archive_negative(tmp_path):
    zip_path = tmp_path / "documents.zip"
    create_zip(zip_path, {
        "report.docx": "word doc",
        "sheet.xlsx": "excel sheet",
        "readme.txt": "notes",
    })

    found = is_3d_archive(zip_path)
    assert found == []


def test_is_3d_archive_corrupt(tmp_path):
    bad_zip = tmp_path / "bad.zip"
    bad_zip.write_bytes(b"not a valid zip file content")
    assert is_3d_archive(bad_zip) == []


def test_unpack_single_model(tmp_path, mock_config):
    downloads = watch_root(mock_config)
    zip_path = downloads / "bracket.zip"
    create_zip(zip_path, {
        "bracket.stl": "stl binary data",
        "readme.txt": "junk",
    })

    extracted = unpack_3d_archive(zip_path, mock_config)
    assert len(extracted) == 1
    target_file = extracted[0]

    # Single model should be in 3D Models\STL
    assert target_file.name == "bracket.stl"
    assert target_file.exists()
    assert "3D Models" in str(target_file)
    assert "STL" in str(target_file)

    # Original zip moved to Archives
    assert not zip_path.exists()
    assert (downloads / "Archives" / "bracket.zip").exists()


def test_unpack_multi_model(tmp_path, mock_config):
    downloads = watch_root(mock_config)
    zip_path = downloads / "robot_arm.zip"
    create_zip(zip_path, {
        "base.stl": "base",
        "joint.3mf": "joint",
        "readme.txt": "read me",
    })

    extracted = unpack_3d_archive(zip_path, mock_config)
    assert len(extracted) == 2

    # Multi-model should create a folder named after the archive stem: 3D Models\robot_arm
    dest_dir = downloads / "3D Models" / "robot_arm"
    assert (dest_dir / "base.stl").exists()
    assert (dest_dir / "joint.3mf").exists()

    # Original zip moved to Archives
    assert not zip_path.exists()
    assert (downloads / "Archives" / "robot_arm.zip").exists()


def test_unpack_with_active_project_single(tmp_path, mock_config):
    downloads = watch_root(mock_config)
    project_dir = tmp_path / "CAD_Projects" / "ProjectX"
    project_dir.mkdir(parents=True, exist_ok=True)

    zip_path = downloads / "gear.zip"
    create_zip(zip_path, {"gear.step": "step data"})

    active_proj = {
        "name": "ProjectX",
        "target_directory": str(project_dir),
        "enabled": True,
    }

    extracted = unpack_3d_archive(zip_path, mock_config, active_project=active_proj)
    assert len(extracted) == 1
    assert extracted[0] == project_dir / "gear.step"
    assert (project_dir / "gear.step").exists()

    # Original zip moved to project_dir / Archives
    assert not zip_path.exists()
    assert (project_dir / "Archives" / "gear.zip").exists()


def test_unpack_with_active_project_multi(tmp_path, mock_config):
    downloads = watch_root(mock_config)
    project_dir = tmp_path / "CAD_Projects" / "ProjectY"
    project_dir.mkdir(parents=True, exist_ok=True)

    zip_path = downloads / "chassis_parts.zip"
    create_zip(zip_path, {
        "plate.stl": "plate",
        "standoff.step": "standoff",
    })

    active_proj = {
        "name": "ProjectY",
        "target_directory": str(project_dir),
        "enabled": True,
    }

    extracted = unpack_3d_archive(zip_path, mock_config, active_project=active_proj)
    assert len(extracted) == 2
    # Subfolder under project_dir with stem name
    assert (project_dir / "chassis_parts" / "plate.stl").exists()
    assert (project_dir / "chassis_parts" / "standoff.step").exists()


def test_unpack_collision_handling(tmp_path, mock_config):
    downloads = watch_root(mock_config)
    stl_dir = downloads / "3D Models" / "STL"
    stl_dir.mkdir(parents=True, exist_ok=True)
    existing_file = stl_dir / "mount.stl"
    existing_file.write_text("existing content", encoding="utf-8")

    zip_path = downloads / "mount.zip"
    create_zip(zip_path, {"mount.stl": "new content"})

    extracted = unpack_3d_archive(zip_path, mock_config)
    assert len(extracted) == 1
    assert extracted[0].name == "mount (2).stl"
    assert extracted[0].exists()
    assert existing_file.read_text(encoding="utf-8") == "existing content"


def test_unpack_dry_run(tmp_path, mock_config):
    downloads = watch_root(mock_config)
    zip_path = downloads / "test_dry.zip"
    create_zip(zip_path, {"dry.stl": "dry data"})

    extracted = unpack_3d_archive(zip_path, mock_config, dry_run=True)
    assert len(extracted) == 1
    # Files should NOT exist on disk
    assert not extracted[0].exists()
    # Original zip should still exist
    assert zip_path.exists()
