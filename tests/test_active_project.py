import json
import time
from pathlib import Path
import sys
import pytest

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from sorter import CAD_EXTENSIONS, evaluate_routing, load_active_project, load_config

ALL_27_EXTENSIONS = [
    ".stl", ".3mf", ".obj", ".step", ".stp", ".iges", ".igs",
    ".f3d", ".f3z", ".sldprt", ".sldasm", ".ipt", ".iam",
    ".dwg", ".dxf", ".blend", ".fbx", ".glb", ".gltf", ".3ds",
    ".nc", ".tap", ".ngc", ".gcode", ".bgcode", ".gco", ".g",
]

@pytest.fixture
def config():
    return load_config()

@pytest.fixture
def active_project_dict(tmp_path):
    target_dir = tmp_path / "Maker Projects" / "Turbine Bracket"
    target_dir.mkdir(parents=True, exist_ok=True)
    return {
        "enabled": True,
        "project_id": "proj_123",
        "project_name": "Turbine Bracket",
        "target_directory": str(target_dir),
        "expires_at": time.time() + 7200.0,
        "cad_extensions": ALL_27_EXTENSIONS,
    }

def test_all_27_extensions_constant():
    assert len(CAD_EXTENSIONS) == 27
    for ext in ALL_27_EXTENSIONS:
        assert ext in CAD_EXTENSIONS

@pytest.mark.parametrize("ext", ALL_27_EXTENSIONS)
def test_active_project_routes_cad_extensions(config, active_project_dict, ext):
    filename = f"part_model{ext}"
    res = evaluate_routing(filename, config, active_project=active_project_dict)
    assert res["is_active_project"] is True
    assert res["match_type"] == "active_project"
    assert res["destination"] == active_project_dict["target_directory"]
    assert "Turbine Bracket" in res["winning_rule"]

def test_non_cad_university_documents_bypass_active_project(config, active_project_dict):
    # IME 3311 course document
    res1 = evaluate_routing("IME3311_Exam_Review.pdf", config, active_project=active_project_dict)
    assert res1["is_active_project"] is False
    assert res1["match_type"] == "keyword"
    assert res1["destination"] == "Documents\\School\\IME 3311"

    # General school homework
    res2 = evaluate_routing("homework_assignment_week4.docx", config, active_project=active_project_dict)
    assert res2["is_active_project"] is False
    assert res2["match_type"] == "keyword"
    assert res2["destination"] == "Documents\\School"

    # Spreadsheets
    res3 = evaluate_routing("budget_calculations.xlsx", config, active_project=active_project_dict)
    assert res3["is_active_project"] is False
    assert res3["match_type"] == "extension"
    assert res3["destination"] == "Spreadsheets"

    # Generic PDF without keywords
    res4 = evaluate_routing("unrelated_paper.pdf", config, active_project=active_project_dict)
    assert res4["is_active_project"] is False
    assert res4["match_type"] == "extension"
    assert res4["destination"] == "Documents\\PDFs"

def test_active_project_auto_expiry(config, active_project_dict):
    # Expired timestamp (1 hour ago)
    active_project_dict["expires_at"] = time.time() - 3600.0
    res = evaluate_routing("gear.stl", config, active_project=active_project_dict)
    assert res["is_active_project"] is False
    assert res["match_type"] == "extension"
    assert res["destination"] == "3D Models\\STL"

def test_active_project_corrupted_expiry(config, active_project_dict):
    # Corrupted non-numeric expiry must be treated as invalid/expired
    active_project_dict["expires_at"] = "invalid_not_a_number"
    res = evaluate_routing("gear.stl", config, active_project=active_project_dict)
    assert res["is_active_project"] is False
    assert res["match_type"] == "extension"
    assert res["destination"] == "3D Models\\STL"

def test_active_project_manual_disabled(config, active_project_dict):
    active_project_dict["enabled"] = False
    res = evaluate_routing("nozzle.3mf", config, active_project=active_project_dict)
    assert res["is_active_project"] is False
    assert res["match_type"] == "extension"
    assert res["destination"] == "3D Models\\3MF"

def test_load_active_project_from_disk(tmp_path):
    state_file = tmp_path / "active_project.json"

    # Missing file
    assert load_active_project(state_file) is None

    # Valid active file
    state_data = {
        "enabled": True,
        "project_name": "Test Mount",
        "target_directory": str(tmp_path),
        "expires_at": time.time() + 3600.0,
    }
    state_file.write_text(json.dumps(state_data), encoding="utf-8")
    loaded = load_active_project(state_file)
    assert loaded is not None
    assert loaded["project_name"] == "Test Mount"

    # Expired file on disk
    state_data["expires_at"] = time.time() - 60.0
    state_file.write_text(json.dumps(state_data), encoding="utf-8")
    expired = load_active_project(state_file)
    assert expired is None

    # Check file was updated with enabled: False
    saved = json.loads(state_file.read_text(encoding="utf-8"))
    assert saved["enabled"] is False
    assert saved["active"] is False

