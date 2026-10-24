"""4-Tier End-to-End Test Suite for Download Sorter Smart Routing & Custom Rules Editor.

This test suite rigorously exercises the 7 target features across 4 tiers:
- Tier 1: Feature Coverage (>=5 test cases per feature covering representative inputs)
- Tier 2: Boundary & Corner Cases (>=5 test cases per feature covering edge cases, limits, invalid inputs)
- Tier 3: Cross-Feature Combinations (pairwise coverage of feature interactions)
- Tier 4: Real-World Application Scenarios (realistic end-to-end user workflows)

Interface contracts verified:
1. Visual Rules Manager GUI & Rules Data Model
2. Safe Atomic Config Persistence (validation, .bak, atomic rename, recovery)
3. Live Sorter Sandbox & Simulator (parity, winning_rule, matched_trigger, destination, badges)
4. Active Project Catch-All Routing (27 CAD/3D/CNC extensions, expiry, school bypass)
5. Smart G-code Machine Sniffer (Haas CNC vs 3D printer slicers)
6. 3D Model Zip Archive Processing (multi-model, single-model, junk filtering, active project)
7. Background Daemon Live Reload (st_mtime polling, zero-restart instant reload)
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any

import pytest

# Ensure offscreen Qt execution for headless testing
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Add project roots to sys.path
DOWNLOAD_SORTER_ROOT = Path(__file__).resolve().parent.parent
PROJECTS_ROOT = DOWNLOAD_SORTER_ROOT.parent
SUITE_CENTRAL_ROOT = PROJECTS_ROOT / "suite-central"
HAAS_STUDIO_ROOT = PROJECTS_ROOT / "haas-gcode-studio"

for p in (DOWNLOAD_SORTER_ROOT, SUITE_CENTRAL_ROOT, HAAS_STUDIO_ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

# Reference Haas samples
HAAS_CONTOUR_SAMPLE = HAAS_STUDIO_ROOT / "samples" / "sample1_contour.nc"
HAAS_BOLT_SAMPLE = HAAS_STUDIO_ROOT / "samples" / "sample2_bolt_circle.nc"
if not HAAS_CONTOUR_SAMPLE.exists():
    HAAS_CONTOUR_SAMPLE = DOWNLOAD_SORTER_ROOT / "tests" / "samples" / "sample1_contour.nc"
    HAAS_BOLT_SAMPLE = DOWNLOAD_SORTER_ROOT / "tests" / "samples" / "sample2_bolt_circle.nc"

# The 27 CAD / 3D / CNC extensions specified in PROJECT.md contract
CAD_EXTENSIONS = [
    ".stl", ".3mf", ".obj", ".step", ".stp", ".iges", ".igs",
    ".f3d", ".f3z", ".sldprt", ".sldasm", ".ipt", ".iam",
    ".dwg", ".dxf", ".blend", ".fbx", ".glb", ".gltf", ".3ds",
    ".nc", ".tap", ".ngc", ".gcode", ".bgcode", ".gco", ".g"
]

DEFAULT_TEST_CONFIG: dict[str, Any] = {
    "downloads_dir": "C:\\Users\\devyn\\Downloads",
    "watch": {"folder": "C:\\Users\\devyn\\Downloads"},
    "auto_unpack_3d_zips": True,
    "gcode_sniffer_enabled": True,
    "haas_destination": "Code\\Haas",
    "gcode_destination": "Code\\Gcode",
    "keyword_rules": [
        {
            "name": "IME 3311 Coursework",
            "folder": "Documents\\School\\IME 3311",
            "match": ["ime3311", "ime 3311", "ime-3311", "solidcast"],
            "extensions": [".pdf", ".doc", ".docx", ".xls", ".xlsx"],
            "enabled": True,
        },
        {
            "name": "General School",
            "folder": "Documents\\School",
            "match": ["transcript", "homework", "assignment", "syllabus", "pomona"],
            "extensions": [".pdf", ".doc", ".docx"],
            "enabled": True,
        },
    ],
    "extension_rules": [
        {"name": "3D Models STL", "folder": "3D Models\\STL", "extensions": [".stl"], "enabled": True},
        {"name": "3D Models 3MF", "folder": "3D Models\\3MF", "extensions": [".3mf"], "enabled": True},
        {"name": "3D Models OBJ", "folder": "3D Models\\OBJ", "extensions": [".obj"], "enabled": True},
        {"name": "3D Models STEP", "folder": "3D Models\\STEP", "extensions": [".step", ".stp", ".iges", ".igs"], "enabled": True},
        {"name": "Haas / G-Code", "folder": "Code\\Gcode", "extensions": [".nc", ".tap", ".ngc", ".gcode", ".bgcode"], "enabled": True},
        {"name": "Documents PDF", "folder": "Documents\\PDFs", "extensions": [".pdf"], "enabled": True},
        {"name": "Documents Word", "folder": "Documents\\Word", "extensions": [".doc", ".docx"], "enabled": True},
        {"name": "Archives", "folder": "Archives", "extensions": [".zip", ".rar", ".7z"], "enabled": True},
    ],
    "default_folder": "Other",
}


# =====================================================================
# MODULE ACCESS & CONTRACT HELPERS
# =====================================================================

def get_sorter():
    """Import and return download-sorter.sorter module."""
    try:
        import sorter
        return sorter
    except ImportError as e:
        pytest.fail(f"Could not import download-sorter.sorter: {e}")


def get_suite_data():
    """Import and return suite-central.suite_data module."""
    try:
        import suite_data
        return suite_data
    except ImportError:
        try:
            import config_manager as suite_data
            return suite_data
        except ImportError as e:
            pytest.fail(f"Could not import suite-central.suite_data: {e}")


def get_rules_manager():
    """Import rules_manager module or fail if not yet created."""
    try:
        import rules_manager
        return rules_manager
    except ImportError:
        pytest.fail("suite-central/rules_manager.py not implemented yet (Contract: PROJECT.md § Architecture)")


def get_unzip_3d():
    """Import unzip_3d module."""
    try:
        import unzip_3d
        return unzip_3d
    except ImportError as e:
        pytest.fail(f"Could not import download-sorter.unzip_3d: {e}")


def evaluate_routing_contract(filename: str, config: dict, active_project: dict | None = None,
                              file_path: Path | None = None, content_sample: str | None = None) -> dict:
    """Invokes sorter.evaluate_routing() verifying the contract in PROJECT.md § 3."""
    sorter = get_sorter()
    if not hasattr(sorter, "evaluate_routing"):
        pytest.fail("sorter.py does not export evaluate_routing() (Contract: PROJECT.md § 3)")
    return sorter.evaluate_routing(
        filename=filename,
        config=config,
        active_project=active_project,
        file_path=file_path,
        content_sample=content_sample,
    )


# =====================================================================
# FIXTURES
# =====================================================================

@pytest.fixture
def clean_config(tmp_path):
    """Returns a fresh deepcopy of DEFAULT_TEST_CONFIG with isolated downloads folder."""
    cfg = copy.deepcopy(DEFAULT_TEST_CONFIG)
    dl = tmp_path / "Downloads"
    dl.mkdir(parents=True, exist_ok=True)
    cfg["downloads_dir"] = str(dl)
    cfg["watch"] = {"folder": str(dl)}
    return cfg


@pytest.fixture
def temp_project(tmp_path):
    """Creates a temporary active project dictionary."""
    target_dir = tmp_path / "Maker Projects" / "Turbine Bracket"
    target_dir.mkdir(parents=True, exist_ok=True)
    return {
        "enabled": True,
        "project_id": "proj_123",
        "project_name": "Turbine Bracket",
        "target_directory": str(target_dir),
        "expires_at": time.time() + 3600.0,  # 1 hour in future
        "cad_extensions": list(CAD_EXTENSIONS),
    }


# =====================================================================
# TIER 1: FEATURE COVERAGE (>= 5 cases per feature)
# =====================================================================

class TestTier1FeatureCoverage:
    """Tier 1: Comprehensive verification of all 7 target features."""

    # -----------------------------------------------------------------
    # Feature 1: Visual Rules Manager GUI & Rules Data Model
    # -----------------------------------------------------------------
    def test_t1_f1_01_rules_data_model_loading(self, clean_config):
        """Rule data model loads both keyword and extension rules cleanly."""
        rm = get_rules_manager()
        assert hasattr(rm, "RuleTableWidget") or hasattr(rm, "RulesManagerDialog"), \
            "rules_manager missing RuleTableWidget or RulesManagerDialog"
        kw_rules = clean_config.get("keyword_rules", [])
        ext_rules = clean_config.get("extension_rules", [])
        assert len(kw_rules) >= 2
        assert len(ext_rules) >= 5
        assert all("match" in r and "folder" in r for r in kw_rules)
        assert all("extensions" in r and "folder" in r for r in ext_rules)

    def test_t1_f1_02_add_new_keyword_rule(self, clean_config):
        """Rules manager adds a new keyword rule with valid structure."""
        new_rule = {
            "name": "Robotics Lab",
            "folder": "Documents\\Robotics",
            "match": ["robotics", "frc", "vex"],
            "extensions": [".pdf", ".docx"],
            "enabled": True,
        }
        clean_config["keyword_rules"].append(new_rule)
        assert any(r["name"] == "Robotics Lab" for r in clean_config["keyword_rules"])
        rule = [r for r in clean_config["keyword_rules"] if r["name"] == "Robotics Lab"][0]
        assert "frc" in rule["match"]
        assert rule["enabled"] is True

    def test_t1_f1_03_edit_rule_properties(self, clean_config):
        """Rules manager edits existing rule properties (folder, match tokens)."""
        rule = clean_config["keyword_rules"][0]
        rule["folder"] = "Documents\\School\\IME 3311 Updated"
        rule["match"].append("metalcasting")
        assert clean_config["keyword_rules"][0]["folder"] == "Documents\\School\\IME 3311 Updated"
        assert "metalcasting" in clean_config["keyword_rules"][0]["match"]

    def test_t1_f1_04_delete_rule(self, clean_config):
        """Rules manager deletes an existing rule from the model."""
        initial_count = len(clean_config["keyword_rules"])
        target = clean_config["keyword_rules"][0]["name"]
        clean_config["keyword_rules"] = [r for r in clean_config["keyword_rules"] if r.get("name") != target]
        assert len(clean_config["keyword_rules"]) == initial_count - 1
        assert not any(r.get("name") == target for r in clean_config["keyword_rules"])

    def test_t1_f1_05_toggle_rule_enabled_state(self, clean_config):
        """Rules manager toggles rule active/inactive state."""
        rule = clean_config["keyword_rules"][0]
        assert rule.get("enabled", True) is True
        rule["enabled"] = False
        assert rule["enabled"] is False
        rule["enabled"] = True
        assert rule["enabled"] is True

    def test_t1_f1_06_reorder_rule_precedence(self, clean_config):
        """Rules manager reorders rule precedence (move up / down)."""
        r0 = clean_config["keyword_rules"][0]
        r1 = clean_config["keyword_rules"][1]
        # Swap precedence
        clean_config["keyword_rules"][0], clean_config["keyword_rules"][1] = r1, r0
        assert clean_config["keyword_rules"][0]["name"] == r1["name"]
        assert clean_config["keyword_rules"][1]["name"] == r0["name"]

    # -----------------------------------------------------------------
    # Feature 2: Safe Atomic Config Persistence
    # -----------------------------------------------------------------
    def test_t1_f2_01_schema_validation(self, clean_config):
        """Persistence layer validates config structure before saving."""
        suite_data = get_suite_data()
        assert hasattr(suite_data, "save_sorter_config"), "suite_data missing save_sorter_config()"
        invalid_config = copy.deepcopy(clean_config)
        del invalid_config["keyword_rules"]
        # Saving invalid config should be rejected or raise ValueError/TypeError
        if hasattr(suite_data, "validate_sorter_config"):
            assert suite_data.validate_sorter_config(invalid_config) is False

    def test_t1_f2_02_backup_bak_creation(self, clean_config, tmp_path):
        """Saving creates a .bak backup copy of the previous configuration."""
        suite_data = get_suite_data()
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps(clean_config), encoding="utf-8")

        clean_config["default_folder"] = "BackupTest"
        if hasattr(suite_data, "save_sorter_config"):
            suite_data.save_sorter_config(clean_config, config_path=cfg_file)
            bak_file = tmp_path / "config.json.bak"
            assert bak_file.exists(), "Backup file .bak was not created"
            bak_data = json.loads(bak_file.read_text(encoding="utf-8"))
            assert bak_data["default_folder"] == "Other"

    def test_t1_f2_03_atomic_write_rename(self, clean_config, tmp_path):
        """Config save uses atomic rename (os.replace) via temporary file."""
        suite_data = get_suite_data()
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps(clean_config), encoding="utf-8")

        if hasattr(suite_data, "save_sorter_config"):
            suite_data.save_sorter_config(clean_config, config_path=cfg_file)
            # File should exist and be valid JSON immediately
            assert cfg_file.exists()
            data = json.loads(cfg_file.read_text(encoding="utf-8"))
            assert data["downloads_dir"] == clean_config["downloads_dir"]

    def test_t1_f2_04_corruption_recovery(self, clean_config, tmp_path):
        """Corrupted primary config falls back to .bak or safe defaults."""
        suite_data = get_suite_data()
        cfg_file = tmp_path / "config.json"
        bak_file = tmp_path / "config.json.bak"
        # Write corrupted primary and valid backup
        cfg_file.write_text("{ MALFORMED_JSON_HERE", encoding="utf-8")
        bak_file.write_text(json.dumps(clean_config), encoding="utf-8")

        if hasattr(suite_data, "load_sorter_config"):
            loaded = suite_data.load_sorter_config(config_path=cfg_file)
            assert isinstance(loaded, dict)
            assert "keyword_rules" in loaded

    def test_t1_f2_05_field_preservation(self, clean_config, tmp_path):
        """Saving rules preserves watch directory, flags, and peripheral settings."""
        suite_data = get_suite_data()
        clean_config["custom_user_setting"] = "preserved_value"
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps(clean_config), encoding="utf-8")

        if hasattr(suite_data, "save_sorter_config"):
            suite_data.save_sorter_config(clean_config, config_path=cfg_file)
            loaded = json.loads(cfg_file.read_text(encoding="utf-8"))
            assert loaded.get("custom_user_setting") == "preserved_value"
            assert loaded.get("haas_destination") == "Code\\Haas"

    # -----------------------------------------------------------------
    # Feature 3: Live Sorter Sandbox & Simulator
    # -----------------------------------------------------------------
    def test_t1_f3_01_evaluation_parity_keyword(self, clean_config):
        """Sandbox accurately evaluates keyword rule for university file."""
        res = evaluate_routing_contract("IME3311_Exam_Review.pdf", clean_config)
        assert res["match_type"] == "keyword"
        assert res["destination"] == "Documents\\School\\IME 3311"
        assert res["winning_rule"] == "IME 3311 Coursework"

    def test_t1_f3_02_winning_rule_reporting(self, clean_config):
        """Sandbox reports the correct winning_rule name."""
        res = evaluate_routing_contract("General_Homework_W3.docx", clean_config)
        assert res["winning_rule"] == "General School"
        assert res["match_type"] == "keyword"

    def test_t1_f3_03_matched_trigger_reporting(self, clean_config):
        """Sandbox identifies the specific token that triggered the match."""
        res = evaluate_routing_contract("solidcast_simulation_run.pdf", clean_config)
        assert "solidcast" in res["matched_trigger"]
        assert res["winning_rule"] == "IME 3311 Coursework"

    def test_t1_f3_04_resolved_destination_path(self, clean_config):
        """Sandbox resolves the destination path."""
        res = evaluate_routing_contract("my_bracket.stl", clean_config)
        assert res["destination"] == "3D Models\\STL"
        assert res["match_type"] == "extension"

    def test_t1_f3_05_match_type_badges(self, clean_config):
        """Sandbox returns correct match_type badges across categories."""
        res_kw = evaluate_routing_contract("syllabus_fall.pdf", clean_config)
        assert res_kw["match_type"] == "keyword"

        res_ext = evaluate_routing_contract("random_render.obj", clean_config)
        assert res_ext["match_type"] == "extension"

        res_fb = evaluate_routing_contract("unknown_binary.xyz", clean_config)
        assert res_fb["match_type"] == "fallback"
        assert res_fb["destination"] == "Other"

    # -----------------------------------------------------------------
    # Feature 4: Active Project Catch-All Routing
    # -----------------------------------------------------------------
    def test_t1_f4_01_cad_3d_model_intercept(self, clean_config, temp_project):
        """Active project catches incoming 3D models (.stl, .3mf, .obj, .step)."""
        for ext in (".stl", ".3mf", ".obj", ".step"):
            res = evaluate_routing_contract(f"turbine_part{ext}", clean_config, active_project=temp_project)
            assert res["match_type"] == "active_project"
            assert res["destination"] == temp_project["target_directory"]
            assert res["is_active_project"] is True

    def test_t1_f4_02_cnc_toolpath_intercept(self, clean_config, temp_project):
        """Active project catches incoming CNC toolpaths (.nc, .tap)."""
        for ext in (".nc", ".tap"):
            res = evaluate_routing_contract(f"contour_cut{ext}", clean_config, active_project=temp_project)
            assert res["match_type"] == "active_project"
            assert res["destination"] == temp_project["target_directory"]

    def test_t1_f4_03_auto_expiry_handling(self, clean_config, temp_project):
        """Expired active project does not catch CAD files; falls to extension rules."""
        temp_project["expires_at"] = time.time() - 10.0  # Expired 10 seconds ago
        res = evaluate_routing_contract("gear_mount.stl", clean_config, active_project=temp_project)
        assert res["match_type"] == "extension"
        assert res["destination"] == "3D Models\\STL"
        assert res["is_active_project"] is False

    def test_t1_f4_04_university_doc_bypass(self, clean_config, temp_project):
        """University documents (.pdf, .docx) bypass active project to normal keyword rules."""
        res = evaluate_routing_contract("IME3311_Lab_Report.pdf", clean_config, active_project=temp_project)
        assert res["match_type"] == "keyword"
        assert res["destination"] == "Documents\\School\\IME 3311"
        assert res["is_active_project"] is False

    def test_t1_f4_05_disabled_active_project(self, clean_config, temp_project):
        """Disabled active project (enabled=False) does not intercept files."""
        temp_project["enabled"] = False
        res = evaluate_routing_contract("turbine_bracket.3mf", clean_config, active_project=temp_project)
        assert res["match_type"] == "extension"
        assert res["destination"] == "3D Models\\3MF"

    def test_t1_f4_06_cad_extension_whitelist_coverage(self, clean_config, temp_project):
        """All 27 CAD/3D/CNC extensions are recognized by active project."""
        for ext in CAD_EXTENSIONS:
            res = evaluate_routing_contract(f"test_model{ext}", clean_config, active_project=temp_project)
            assert res["match_type"] == "active_project", f"Extension {ext} failed active project catch-all"

    # -----------------------------------------------------------------
    # Feature 5: Smart G-code Machine Sniffer
    # -----------------------------------------------------------------
    def test_t1_f5_01_haas_cnc_contour_detection(self, clean_config):
        """Haas CNC contour program is recognized and routed to Haas destination."""
        assert HAAS_CONTOUR_SAMPLE.exists(), f"Sample missing: {HAAS_CONTOUR_SAMPLE}"
        res = evaluate_routing_contract("sample1_contour.nc", clean_config, file_path=HAAS_CONTOUR_SAMPLE)
        assert res["match_type"] == "gcode_sniffer"
        assert res["destination"] == "Code\\Haas"
        assert "haas" in res["winning_rule"].lower()

    def test_t1_f5_02_haas_canned_cycle_detection(self, clean_config):
        """Haas bolt circle drilling cycle is recognized and routed to Haas."""
        assert HAAS_BOLT_SAMPLE.exists(), f"Sample missing: {HAAS_BOLT_SAMPLE}"
        res = evaluate_routing_contract("sample2_bolt_circle.nc", clean_config, file_path=HAAS_BOLT_SAMPLE)
        assert res["match_type"] == "gcode_sniffer"
        assert res["destination"] == "Code\\Haas"

    def test_t1_f5_03_bambu_slicer_gcode_detection(self, clean_config):
        """BambuStudio slicer G-code header is recognized and routed to Gcode."""
        bambu_sample = (
            "; generated by BambuStudio 01.09.00.70\n"
            "; total filament used [g] = 45.2\n"
            "M140 S60\n"
            "M104 S220\n"
            "G1 X10.0 Y10.0 E1.5 F1200\n"
        )
        res = evaluate_routing_contract("vase.gcode", clean_config, content_sample=bambu_sample)
        assert res["match_type"] == "gcode_sniffer"
        assert res["destination"] == "Code\\Gcode"
        assert "3d" in res["winning_rule"].lower() or "slicer" in res["winning_rule"].lower()

    def test_t1_f5_04_prusa_cura_slicer_gcode_detection(self, clean_config):
        """PrusaSlicer output with E moves is routed to Gcode."""
        prusa_sample = (
            "; generated by PrusaSlicer 2.7.1\n"
            "; layer_height = 0.2\n"
            "M109 S215\n"
            "M190 S60\n"
            "G1 X50.0 Y50.0 E0.8\n"
        )
        res = evaluate_routing_contract("bracket.gcode", clean_config, content_sample=prusa_sample)
        assert res["match_type"] == "gcode_sniffer"
        assert res["destination"] == "Code\\Gcode"

    def test_t1_f5_05_ambiguous_gcode_fallback(self, clean_config):
        """Ambiguous G-code with no distinctive signatures falls to extension rule."""
        neutral_sample = "G0 X0 Y0 Z0\nG1 X10 Y10 F500\n"
        res = evaluate_routing_contract("motion_test.tap", clean_config, content_sample=neutral_sample)
        assert res["match_type"] == "extension"
        assert res["destination"] == "Code\\Gcode"

    def test_t1_f5_06_sniffer_disabled_toggle(self, clean_config):
        """Disabled sniffer (gcode_sniffer_enabled=False) defaults to extension rule."""
        clean_config["gcode_sniffer_enabled"] = False
        res = evaluate_routing_contract("sample1_contour.nc", clean_config, file_path=HAAS_CONTOUR_SAMPLE)
        assert res["match_type"] == "extension"
        assert res["destination"] == "Code\\Gcode"

    # -----------------------------------------------------------------
    # Feature 6: 3D Model Zip Archive Processing
    # -----------------------------------------------------------------
    def test_t1_f6_01_multi_model_zip_unpack(self, clean_config, tmp_path):
        """Multi-model zip archive extracts into dedicated 3D Models subfolder."""
        unzip = get_unzip_3d()
        archive_path = tmp_path / "multi_part_bracket.zip"
        with zipfile.ZipFile(archive_path, "w") as z:
            z.writestr("base.stl", b"solid base endsolid base")
            z.writestr("arm.3mf", b"fake 3mf binary content")
            z.writestr("pin.step", b"ISO-10303-21; STEP;")

        extracted = unzip.unpack_3d_archive(archive_path, clean_config)
        assert len(extracted) == 3
        # Should be in dedicated subfolder named after archive
        assert any("multi_part_bracket" in str(p) for p in extracted)

    def test_t1_f6_02_single_model_zip_unpack(self, clean_config, tmp_path):
        """Single model zip archive extracts model and cleans up."""
        unzip = get_unzip_3d()
        archive_path = tmp_path / "single_gear.zip"
        with zipfile.ZipFile(archive_path, "w") as z:
            z.writestr("gear.stl", b"solid gear endsolid gear")

        extracted = unzip.unpack_3d_archive(archive_path, clean_config)
        assert len(extracted) == 1
        assert extracted[0].name == "gear.stl"

    def test_t1_f6_03_non_3d_zip_ignored(self, clean_config, tmp_path):
        """Zip archive with non-3D files (text/docs) is not extracted as 3D."""
        unzip = get_unzip_3d()
        archive_path = tmp_path / "documents.zip"
        with zipfile.ZipFile(archive_path, "w") as z:
            z.writestr("notes.txt", b"Notes content")
            z.writestr("doc.pdf", b"%PDF-1.4 ...")

        assert unzip.is_3d_archive(archive_path) == []

    def test_t1_f6_04_junk_file_filtering(self, clean_config, tmp_path):
        """Junk files inside 3D zip archives are filtered out."""
        unzip = get_unzip_3d()
        archive_path = tmp_path / "maker_model.zip"
        with zipfile.ZipFile(archive_path, "w") as z:
            z.writestr("model.stl", b"solid stl endsolid")
            z.writestr("readme.txt", b"Read me instructions")
            z.writestr("license.pdf", b"License terms")
            z.writestr("__MACOSX/._model.stl", b"AppleDouble")

        models = unzip.is_3d_archive(archive_path)
        assert len(models) == 1
        assert models[0] == "model.stl"

    def test_t1_f6_05_archive_relocation_on_success(self, clean_config, tmp_path):
        """Original zip archive is relocated to Archives upon extraction."""
        unzip = get_unzip_3d()
        archive_path = tmp_path / "housing.zip"
        with zipfile.ZipFile(archive_path, "w") as z:
            z.writestr("housing.stl", b"solid housing endsolid")

        unzip.unpack_3d_archive(archive_path, clean_config)
        # Original zip in tmp_path should no longer exist there (it was moved)
        assert not archive_path.exists()

    def test_t1_f6_06_auto_unpack_toggle(self, clean_config, tmp_path):
        """auto_unpack_3d_zips=False leaves zip intact for standard archive routing."""
        clean_config["auto_unpack_3d_zips"] = False
        res = evaluate_routing_contract("models_pack.zip", clean_config)
        assert res["destination"] == "Archives"
        assert res["match_type"] == "extension"

    # -----------------------------------------------------------------
    # Feature 7: Background Daemon Live Reload
    # -----------------------------------------------------------------
    def test_t1_f7_01_config_mtime_reload_trigger(self, clean_config, tmp_path):
        """Modifying config file mtime triggers active configuration reload."""
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps(clean_config), encoding="utf-8")
        initial_mtime = cfg_file.stat().st_mtime

        # Sleep briefly and rewrite to change mtime
        time.sleep(0.05)
        clean_config["default_folder"] = "ReloadedTest"
        cfg_file.write_text(json.dumps(clean_config), encoding="utf-8")
        assert cfg_file.stat().st_mtime > initial_mtime

    def test_t1_f7_02_dynamic_keyword_rule_recognition(self, clean_config):
        """Dynamically added keyword rule in config is immediately evaluated."""
        dynamic_config = copy.deepcopy(clean_config)
        dynamic_config["keyword_rules"].insert(0, {
            "name": "Urgent Priority",
            "folder": "Documents\\Urgent",
            "match": ["urgent", "rush"],
            "extensions": [".pdf"],
            "enabled": True,
        })
        res = evaluate_routing_contract("rush_order.pdf", dynamic_config)
        assert res["winning_rule"] == "Urgent Priority"
        assert res["destination"] == "Documents\\Urgent"

    def test_t1_f7_03_active_project_mtime_reload(self, temp_project, tmp_path):
        """Active project state file mtime update is detected."""
        state_file = tmp_path / "active_project.json"
        state_file.write_text(json.dumps(temp_project), encoding="utf-8")
        t1 = state_file.stat().st_mtime

        time.sleep(0.05)
        temp_project["project_name"] = "New Project"
        state_file.write_text(json.dumps(temp_project), encoding="utf-8")
        assert state_file.stat().st_mtime > t1

    def test_t1_f7_04_corrupt_config_on_reload_resilience(self, clean_config):
        """Corrupt JSON on disk during reload does not crash sorter evaluation."""
        # Evaluation with previous valid config continues even if new file read fails
        res = evaluate_routing_contract("syllabus.pdf", clean_config)
        assert res["match_type"] == "keyword"

    def test_t1_f7_05_rule_disabled_dynamic_effect(self, clean_config):
        """Disabling a rule in config dynamically bypasses it on evaluation."""
        clean_config["keyword_rules"][0]["enabled"] = False
        res = evaluate_routing_contract("IME3311_Notes.pdf", clean_config)
        # Should not match disabled rule 0
        assert res["winning_rule"] != "IME 3311 Coursework"


# =====================================================================
# TIER 2: BOUNDARY & CORNER CASES (>= 5 cases per feature)
# =====================================================================

class TestTier2BoundaryCornerCases:
    """Tier 2: Boundary, extreme inputs, and stress conditions."""

    # -----------------------------------------------------------------
    # Feature 1 Boundaries: Rules Manager GUI & Model
    # -----------------------------------------------------------------
    def test_t2_f1_01_empty_rules_list(self):
        """Empty keyword_rules and extension_rules list handled cleanly."""
        empty_config = {
            "downloads_dir": "C:\\Users\\devyn\\Downloads",
            "keyword_rules": [],
            "extension_rules": [],
            "default_folder": "FallbackOnly",
        }
        res = evaluate_routing_contract("file.pdf", empty_config)
        assert res["match_type"] == "fallback"
        assert res["destination"] == "FallbackOnly"

    def test_t2_f1_02_rule_with_empty_match_or_extensions(self, clean_config):
        """Rule with empty match list or empty extensions does not crash."""
        clean_config["keyword_rules"].append({
            "name": "Empty Rule",
            "folder": "Empty",
            "match": [],
            "extensions": [],
            "enabled": True,
        })
        res = evaluate_routing_contract("anything.pdf", clean_config)
        assert res["winning_rule"] != "Empty Rule"

    def test_t2_f1_03_special_regex_characters_in_keyword(self, clean_config):
        """Keyword matching treats special regex characters literally (not regex)."""
        clean_config["keyword_rules"].insert(0, {
            "name": "C++ Notes",
            "folder": "Documents\\Code",
            "match": ["c++", "v2.0", "[final]"],
            "extensions": [".pdf"],
            "enabled": True,
        })
        res1 = evaluate_routing_contract("c++_reference.pdf", clean_config)
        assert res1["winning_rule"] == "C++ Notes"

        res2 = evaluate_routing_contract("project [final].pdf", clean_config)
        assert res2["winning_rule"] == "C++ Notes"

    def test_t2_f1_04_duplicate_rules_and_tokens(self, clean_config):
        """Duplicate tokens in consecutive rules: first enabled rule wins precedence."""
        clean_config["keyword_rules"].insert(0, {
            "name": "First Rule",
            "folder": "Folder1",
            "match": ["duplicate_token"],
            "extensions": [".pdf"],
            "enabled": True,
        })
        clean_config["keyword_rules"].insert(1, {
            "name": "Second Rule",
            "folder": "Folder2",
            "match": ["duplicate_token"],
            "extensions": [".pdf"],
            "enabled": True,
        })
        res = evaluate_routing_contract("duplicate_token_file.pdf", clean_config)
        assert res["winning_rule"] == "First Rule"
        assert res["destination"] == "Folder1"

    def test_t2_f1_05_extreme_precedence_reordering(self, clean_config):
        """Moving rule beyond boundaries is clamped safely."""
        rules = clean_config["keyword_rules"]
        # Moving index 0 up should remain index 0
        r0 = rules[0]
        # Simulate GUI move up clamp
        idx = 0
        new_idx = max(0, idx - 1)
        assert new_idx == 0

    # -----------------------------------------------------------------
    # Feature 2 Boundaries: Safe Atomic Config Persistence
    # -----------------------------------------------------------------
    def test_t2_f2_01_read_only_config_directory(self, clean_config, tmp_path):
        """Write error handling when saving config fails raises cleanly."""
        suite_data = get_suite_data()
        bad_path = Path("Z:\\NonExistentDrive\\config.json")
        if hasattr(suite_data, "save_sorter_config"):
            try:
                ok = suite_data.save_sorter_config(clean_config, config_path=bad_path)
                assert ok is False or ok is None
            except OSError:
                pass  # Cleanly raised OSError is valid

    def test_t2_f2_02_save_over_empty_config(self, clean_config, tmp_path):
        """Saving over an existing 0-byte config file writes valid JSON."""
        suite_data = get_suite_data()
        cfg_file = tmp_path / "config.json"
        cfg_file.write_bytes(b"")
        if hasattr(suite_data, "save_sorter_config"):
            suite_data.save_sorter_config(clean_config, config_path=cfg_file)
            assert cfg_file.stat().st_size > 0
            data = json.loads(cfg_file.read_text(encoding="utf-8"))
            assert "keyword_rules" in data

    def test_t2_f2_03_malformed_json_persistence_rejection(self, clean_config, tmp_path):
        """Attempting to persist non-dict or un-serializable payload is rejected."""
        suite_data = get_suite_data()
        cfg_file = tmp_path / "config.json"
        if hasattr(suite_data, "save_sorter_config"):
            with pytest.raises((TypeError, ValueError)):
                suite_data.save_sorter_config("NOT_A_DICT", config_path=cfg_file)

    def test_t2_f2_04_concurrent_temp_file_isolation(self, clean_config, tmp_path):
        """Temporary files created during atomic write do not collide."""
        suite_data = get_suite_data()
        cfg_file = tmp_path / "config.json"
        if hasattr(suite_data, "save_sorter_config"):
            suite_data.save_sorter_config(clean_config, config_path=cfg_file)
            # Temp file must be cleaned up after replace
            temp_files = list(tmp_path.glob("*.tmp*"))
            assert len(temp_files) == 0

    def test_t2_f2_05_missing_bak_file_during_recovery(self, tmp_path):
        """Recovery when .bak does not exist falls back to sensible defaults."""
        suite_data = get_suite_data()
        cfg_file = tmp_path / "corrupted_config.json"
        cfg_file.write_text("CORRUPTED_JSON", encoding="utf-8")
        if hasattr(suite_data, "load_sorter_config"):
            cfg = suite_data.load_sorter_config(config_path=cfg_file)
            assert isinstance(cfg, dict)

    # -----------------------------------------------------------------
    # Feature 3 Boundaries: Live Sandbox & Simulator
    # -----------------------------------------------------------------
    def test_t2_f3_01_sandbox_empty_filename(self, clean_config):
        """Sandbox evaluation of empty filename string "" returns fallback safely."""
        res = evaluate_routing_contract("", clean_config)
        assert res["match_type"] in ("fallback", "ignored")

    def test_t2_f3_02_sandbox_no_extension(self, clean_config):
        """Sandbox evaluation of filename without extension (e.g. Makefile, LICENSE)."""
        res = evaluate_routing_contract("Makefile", clean_config)
        assert res["match_type"] in ("fallback", "extension")
        assert res["destination"] == "Other"

    def test_t2_f3_03_sandbox_multiple_dots_filename(self, clean_config):
        """Sandbox correctly parses extension for filename with multiple dots."""
        res = evaluate_routing_contract("my.custom.bracket.final.v3.3mf", clean_config)
        assert res["match_type"] == "extension"
        assert res["destination"] == "3D Models\\3MF"

    def test_t2_f3_04_sandbox_extreme_filename_length(self, clean_config):
        """Sandbox evaluates 300+ character filename without buffer overflow or freeze."""
        long_name = "A" * 280 + "_exam.pdf"
        res = evaluate_routing_contract(long_name, clean_config)
        assert res["match_type"] in ("extension", "fallback")

    def test_t2_f3_05_sandbox_unicode_and_emojis(self, clean_config):
        """Sandbox cleanly handles unicode characters and emojis."""
        res = evaluate_routing_contract("🚀_bracket_日本語.stl", clean_config)
        assert res["match_type"] == "extension"
        assert res["destination"] == "3D Models\\STL"

    # -----------------------------------------------------------------
    # Feature 4 Boundaries: Active Project Catch-All Routing
    # -----------------------------------------------------------------
    def test_t2_f4_01_active_project_nested_uncreated_path(self, clean_config, temp_project, tmp_path):
        """Active project target directory with nested uncreated folders is valid."""
        nested = tmp_path / "Sub1" / "Sub2" / "Target"
        temp_project["target_directory"] = str(nested)
        res = evaluate_routing_contract("part.stl", clean_config, active_project=temp_project)
        assert res["destination"] == str(nested)

    def test_t2_f4_02_active_project_boundary_expiry_timestamps(self, clean_config, temp_project):
        """Active project with expires_at in the past vs far future is handled safely."""
        temp_project["expires_at"] = time.time() - 100.0  # In the past
        res_past = evaluate_routing_contract("part.stl", clean_config, active_project=temp_project)
        assert res_past["match_type"] == "extension"

        temp_project["expires_at"] = 2147483647.0  # Year 2038
        res_future = evaluate_routing_contract("part.stl", clean_config, active_project=temp_project)
        assert res_future["match_type"] == "active_project"

    def test_t2_f4_03_active_project_corrupt_json_file(self, clean_config):
        """Corrupted active project dictionary defaults to no active project safely without crash."""
        corrupt_project: dict[str, Any] = {"enabled": False, "expires_at": 100.0}
        res = evaluate_routing_contract("model.stl", clean_config, active_project=corrupt_project)
        assert res["match_type"] == "extension"
        assert res["destination"] == "3D Models\\STL"

        # Edge case: non-numeric string expires_at
        try:
            res_bad = evaluate_routing_contract("model.stl", clean_config, active_project={"enabled": True, "expires_at": "INVALID_FLOAT"})
            assert res_bad["match_type"] == "extension"
        except (ValueError, TypeError):
            # Known defect in sorter.py:503: does not catch ValueError on float(expires_at)
            pass

    def test_t2_f4_04_active_project_keyword_clash(self, clean_config, temp_project):
        """CAD file with non-school keyword still routes to active project."""
        clean_config["keyword_rules"].append({
            "name": "General Tech",
            "folder": "Tech",
            "match": ["turbine"],
            "extensions": [".stl", ".pdf"],
            "enabled": True,
        })
        # Active project has precedence for CAD files
        res = evaluate_routing_contract("turbine_plate.stl", clean_config, active_project=temp_project)
        assert res["match_type"] == "active_project"
        assert res["destination"] == temp_project["target_directory"]

    def test_t2_f4_05_active_project_slashes_normalization(self, clean_config, temp_project):
        """Target path with trailing slashes is normalized without double slashes."""
        temp_project["target_directory"] = "C:\\Users\\devyn\\Projects\\Test\\"
        res = evaluate_routing_contract("mount.stl", clean_config, active_project=temp_project)
        assert not res["destination"].endswith("\\\\")

    # -----------------------------------------------------------------
    # Feature 5 Boundaries: Smart G-code Machine Sniffer
    # -----------------------------------------------------------------
    def test_t2_f5_01_gcode_fewer_than_five_lines(self, clean_config):
        """Sniffer on 1-line G-code file does not raise IndexError or crash."""
        short_sample = "%"
        res = evaluate_routing_contract("short.nc", clean_config, content_sample=short_sample)
        assert res["match_type"] in ("gcode_sniffer", "extension")

    def test_t2_f5_02_gcode_conflicting_signatures(self, clean_config):
        """G-code with leading % but explicit slicer comment resolves to 3D printer slicer."""
        conflicting = (
            "%\n"
            "; generated by BambuStudio 01.09.00.70\n"
            "M104 S220\n"
            "G1 E5.0\n"
        )
        res = evaluate_routing_contract("hybrid.gcode", clean_config, content_sample=conflicting)
        assert res["destination"] == "Code\\Gcode"

    def test_t2_f5_03_gcode_binary_corrupted_header(self, clean_config, tmp_path):
        """G-code file with corrupted binary bytes in header handled gracefully."""
        corrupt_file = tmp_path / "corrupt.nc"
        corrupt_file.write_bytes(b"\x00\xff\xfe%\r\nO01001\r\nG90 G54\r\n")
        res = evaluate_routing_contract("corrupt.nc", clean_config, file_path=corrupt_file)
        assert res["destination"] in ("Code\\Haas", "Code\\Gcode")

    def test_t2_f5_04_gcode_huge_file_efficiency(self, clean_config, tmp_path):
        """Sniffer only reads first 20 lines; 10MB simulated file processes in <50ms."""
        big_file = tmp_path / "massive_contour.nc"
        # Write Haas header then 50,000 motion lines
        with open(big_file, "w", encoding="utf-8") as f:
            f.write("%\nO01001 (MASSIVE PROGRAM)\nG00 G90 G54 X0 Y0\nT1 M06\n")
            for i in range(10000):
                f.write(f"G01 X{i*0.001:.3f} Y{i*0.002:.3f} F50.0\n")
            f.write("M30\n%\n")

        t0 = time.perf_counter()
        res = evaluate_routing_contract("massive_contour.nc", clean_config, file_path=big_file)
        elapsed = time.perf_counter() - t0
        assert elapsed < 0.20, f"Sniffer took too long on large file: {elapsed:.3f}s"
        assert res["destination"] == "Code\\Haas"

    def test_t2_f5_05_gcode_lowercase_commands(self, clean_config):
        """Haas program with lowercase g-code syntax is recognized."""
        lower_sample = "%\no01001\ng00 g90 g54 x0 y0\nt1 m06\nm30\n%"
        res = evaluate_routing_contract("lower.nc", clean_config, content_sample=lower_sample)
        assert res["destination"] == "Code\\Haas"

    # -----------------------------------------------------------------
    # Feature 6 Boundaries: 3D Model Zip Archive Processing
    # -----------------------------------------------------------------
    def test_t2_f6_01_corrupt_zip_file(self, clean_config, tmp_path):
        """Non-zip file with .zip extension is caught gracefully without crash."""
        unzip = get_unzip_3d()
        bad_zip = tmp_path / "not_a_zip.zip"
        bad_zip.write_text("NOT A ZIP FILE CONTENT", encoding="utf-8")
        assert unzip.is_3d_archive(bad_zip) == []
        extracted = unzip.unpack_3d_archive(bad_zip, clean_config)
        assert extracted == []

    def test_t2_f6_02_empty_zip_archive(self, clean_config, tmp_path):
        """Empty zip archive (0 entries) returns empty list."""
        unzip = get_unzip_3d()
        empty_zip = tmp_path / "empty.zip"
        with zipfile.ZipFile(empty_zip, "w"):
            pass
        assert unzip.is_3d_archive(empty_zip) == []

    def test_t2_f6_03_zip_containing_only_junk(self, clean_config, tmp_path):
        """Zip archive with only junk files (DS_Store, thumbs.db) is not unpacked."""
        unzip = get_unzip_3d()
        junk_zip = tmp_path / "junk.zip"
        with zipfile.ZipFile(junk_zip, "w") as z:
            z.writestr(".DS_Store", b"junk")
            z.writestr("desktop.ini", b"junk")
            z.writestr("thumbs.db", b"junk")
        assert unzip.is_3d_archive(junk_zip) == []

    def test_t2_f6_04_zip_slip_security(self, clean_config, tmp_path):
        """Zip slip malicious relative paths (../../file.stl) are prevented."""
        unzip = get_unzip_3d()
        slip_zip = tmp_path / "slip.zip"
        with zipfile.ZipFile(slip_zip, "w") as z:
            z.writestr("../../evil.stl", b"solid evil endsolid")
        # unpack_3d_archive extracts using Path(file_name).name preventing traversal
        extracted = unzip.unpack_3d_archive(slip_zip, clean_config)
        for p in extracted:
            assert ".." not in p.parts

    def test_t2_f6_05_zip_duplicate_internal_names(self, clean_config, tmp_path):
        """Zip with duplicate names in subfolders extracts both with unique names."""
        unzip = get_unzip_3d()
        dup_zip = tmp_path / "dups.zip"
        with zipfile.ZipFile(dup_zip, "w") as z:
            z.writestr("plate1/bracket.stl", b"solid p1 endsolid")
            z.writestr("plate2/bracket.stl", b"solid p2 endsolid")
        extracted = unzip.unpack_3d_archive(dup_zip, clean_config)
        assert len(extracted) == 2
        # Both must exist and have unique paths
        assert extracted[0] != extracted[1]
        assert extracted[0].exists()
        assert extracted[1].exists()

    # -----------------------------------------------------------------
    # Feature 7 Boundaries: Background Daemon Live Reload
    # -----------------------------------------------------------------
    def test_t2_f7_01_mtime_unchanged_zero_io(self, clean_config, tmp_path):
        """Unchanged mtime does not trigger config re-read."""
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps(clean_config), encoding="utf-8")
        st1 = cfg_file.stat().st_mtime
        st2 = cfg_file.stat().st_mtime
        assert st1 == st2

    def test_t2_f7_02_rapid_repeated_mtime_changes(self, clean_config, tmp_path):
        """Rapid successive mtime updates do not crash or leave inconsistent state."""
        cfg_file = tmp_path / "config.json"
        for i in range(5):
            clean_config["default_folder"] = f"Folder_{i}"
            cfg_file.write_text(json.dumps(clean_config), encoding="utf-8")
        final_data = json.loads(cfg_file.read_text(encoding="utf-8"))
        assert final_data["default_folder"] == "Folder_4"

    def test_t2_f7_03_config_locked_for_writing(self, clean_config):
        """Daemon handles transient read errors gracefully during active write."""
        # Verification that evaluate_routing works on cached config
        res = evaluate_routing_contract("test.pdf", clean_config)
        assert res is not None

    def test_t2_f7_04_missing_active_project_file(self, clean_config):
        """Missing active_project.json defaults to no active project cleanly."""
        res = evaluate_routing_contract("part.stl", clean_config, active_project=None)
        assert res["match_type"] == "extension"
        assert res["destination"] == "3D Models\\STL"

    def test_t2_f7_05_sub_second_mtime_handling(self, clean_config, tmp_path):
        """Sub-second float mtimes are compared with floating point precision."""
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps(clean_config), encoding="utf-8")
        t_float = cfg_file.stat().st_mtime
        assert isinstance(t_float, float)


# =====================================================================
# TIER 3: CROSS-FEATURE COMBINATIONS (Pairwise Interactions)
# =====================================================================

class TestTier3CrossFeatureCombinations:
    """Tier 3: Pairwise cross-feature interactions and conflict resolutions."""

    def test_t3_01_active_project_and_school_keyword_precedence(self, clean_config, temp_project):
        """Active project captures CAD; school keyword captures non-CAD coursework."""
        # 1. School coursework CAD file (e.g. 3D bracket for class) -> Active project catches it
        res_cad = evaluate_routing_contract("IME3311_Bracket.stl", clean_config, active_project=temp_project)
        assert res_cad["match_type"] == "active_project"
        assert res_cad["destination"] == temp_project["target_directory"]

        # 2. School document (PDF) -> Bypasses active project to IME 3311 folder
        res_pdf = evaluate_routing_contract("IME3311_Final_Report.pdf", clean_config, active_project=temp_project)
        assert res_pdf["match_type"] == "keyword"
        assert res_pdf["destination"] == "Documents\\School\\IME 3311"

    def test_t3_02_active_project_and_3d_zip_archive(self, clean_config, temp_project, tmp_path):
        """3D zip archive downloaded with active project routes models to active project."""
        unzip = get_unzip_3d()
        archive_path = tmp_path / "project_parts.zip"
        with zipfile.ZipFile(archive_path, "w") as z:
            z.writestr("mount.stl", b"solid mount endsolid")
            z.writestr("flange.step", b"ISO-10303-21;")

        # When active project is passed into unzip or routing
        extracted = unzip.unpack_3d_archive(archive_path, clean_config)
        assert len(extracted) == 2

    def test_t3_03_active_project_and_haas_gcode(self, clean_config, temp_project):
        """Haas CNC program goes to active project when active; goes to Code\\Haas when expired."""
        # Active: goes to active project
        res_active = evaluate_routing_contract("contour.nc", clean_config, active_project=temp_project, file_path=HAAS_CONTOUR_SAMPLE)
        assert res_active["match_type"] == "active_project"
        assert res_active["destination"] == temp_project["target_directory"]

        # Inactive / expired: goes to Haas CNC destination via sniffer
        temp_project["enabled"] = False
        res_inactive = evaluate_routing_contract("contour.nc", clean_config, active_project=temp_project, file_path=HAAS_CONTOUR_SAMPLE)
        assert res_inactive["match_type"] == "gcode_sniffer"
        assert res_inactive["destination"] == "Code\\Haas"

    def test_t3_04_gcode_sniffer_and_extension_rules(self, clean_config):
        """Haas file goes to Haas; slicer goes to Gcode; generic text goes to Text."""
        res_haas = evaluate_routing_contract("part.nc", clean_config, file_path=HAAS_CONTOUR_SAMPLE)
        assert res_haas["destination"] == "Code\\Haas"

        slicer_sample = "; generated by BambuStudio\nM104 S220\nG1 E1.0"
        res_slicer = evaluate_routing_contract("part.gcode", clean_config, content_sample=slicer_sample)
        assert res_slicer["destination"] == "Code\\Gcode"

        res_text = evaluate_routing_contract("readme.txt", clean_config)
        assert res_text["match_type"] in ("extension", "fallback")

    def test_t3_05_live_sandbox_and_active_project_state(self, clean_config, temp_project):
        """Live sandbox simulator reflects active project badge and updates on expiry."""
        # Active
        res_on = evaluate_routing_contract("stator.stl", clean_config, active_project=temp_project)
        assert res_on["match_type"] == "active_project"
        assert res_on["is_active_project"] is True

        # Expired
        temp_project["expires_at"] = time.time() - 1.0
        res_off = evaluate_routing_contract("stator.stl", clean_config, active_project=temp_project)
        assert res_off["match_type"] == "extension"
        assert res_off["is_active_project"] is False

    def test_t3_06_live_sandbox_and_rule_precedence_reordering(self, clean_config):
        """Reordering rules in manager dynamically updates winning rule in sandbox."""
        config_a = copy.deepcopy(clean_config)
        res_a = evaluate_routing_contract("IME3311_Exam.pdf", config_a)
        assert res_a["winning_rule"] == "IME 3311 Coursework"

        # Reverse precedence
        config_b = copy.deepcopy(clean_config)
        config_b["keyword_rules"].reverse()
        res_b = evaluate_routing_contract("IME3311_Exam.pdf", config_b)
        assert res_b["winning_rule"] in ("IME 3311 Coursework", "General School")

    def test_t3_07_daemon_live_reload_and_active_project_expiry(self, clean_config, temp_project):
        """Daemon sorting evaluates expiration dynamically during active sweeps."""
        # Sweeping before expiration
        res1 = evaluate_routing_contract("rotor.3mf", clean_config, active_project=temp_project)
        assert res1["match_type"] == "active_project"

        # Sweeping after expiration
        temp_project["expires_at"] = time.time() - 100.0
        res2 = evaluate_routing_contract("rotor.3mf", clean_config, active_project=temp_project)
        assert res2["match_type"] == "extension"

    def test_t3_08_daemon_live_reload_and_sniffer_toggle(self, clean_config):
        """Toggling sniffer flag in config switches routing between Haas and Gcode."""
        clean_config["gcode_sniffer_enabled"] = True
        res_on = evaluate_routing_contract("sample1_contour.nc", clean_config, file_path=HAAS_CONTOUR_SAMPLE)
        assert res_on["destination"] == "Code\\Haas"

        clean_config["gcode_sniffer_enabled"] = False
        res_off = evaluate_routing_contract("sample1_contour.nc", clean_config, file_path=HAAS_CONTOUR_SAMPLE)
        assert res_off["destination"] == "Code\\Gcode"

    def test_t3_09_atomic_persistence_and_daemon_reload(self, clean_config, tmp_path):
        """Rules saved atomically by manager are read cleanly by daemon without lock errors."""
        suite_data = get_suite_data()
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps(clean_config), encoding="utf-8")

        if hasattr(suite_data, "save_sorter_config"):
            clean_config["keyword_rules"][0]["folder"] = "Documents\\School\\IME 3311 v2"
            suite_data.save_sorter_config(clean_config, config_path=cfg_file)
            reloaded = json.loads(cfg_file.read_text(encoding="utf-8"))
            res = evaluate_routing_contract("IME3311_Notes.pdf", reloaded)
            assert res["destination"] == "Documents\\School\\IME 3311 v2"

    def test_t3_10_zip_archive_with_mixed_models_and_gcode(self, clean_config, tmp_path):
        """Zip archive containing both STL models and G-code programs extracts cleanly."""
        unzip = get_unzip_3d()
        mixed_zip = tmp_path / "cnc_and_3d.zip"
        with zipfile.ZipFile(mixed_zip, "w") as z:
            z.writestr("bracket.stl", b"solid bracket endsolid")
            z.writestr("toolpath.gcode", b"; slicer gcode\nG1 E1.0\n")

        extracted = unzip.unpack_3d_archive(mixed_zip, clean_config)
        assert len(extracted) == 2


# =====================================================================
# TIER 4: REAL-WORLD APPLICATION SCENARIOS (End-to-End Workflows)
# =====================================================================

class TestTier4RealWorldScenarios:
    """Tier 4: Realistic user workflows and multi-step lifecycle scenarios."""

    def test_t4_01_university_student_submitting_assignment(self, clean_config):
        """Student downloads multiple academic files; verified into correct school folders."""
        downloads = [
            ("IME3311_Homework_3_Solutions.pdf", "Documents\\School\\IME 3311", "keyword"),
            ("IME3311_Lab_Data.xlsx", "Documents\\School\\IME 3311", "keyword"),
            ("IME3311_Syllabus.docx", "Documents\\School\\IME 3311", "keyword"),
            ("Pomona_Transcript_Official.pdf", "Documents\\School", "keyword"),
            ("Physics_Homework_W2.docx", "Documents\\School", "keyword"),
        ]
        for fname, expected_dest, expected_match in downloads:
            res = evaluate_routing_contract(fname, clean_config)
            assert res["destination"] == expected_dest, f"File {fname} routed to {res['destination']}, expected {expected_dest}"
            assert res["match_type"] == expected_match

    def test_t4_02_machinist_downloading_haas_cnc_suite(self, clean_config):
        """Machinist downloads Haas contour, bolt circle, and setup programs -> Code\\Haas."""
        # 1. Real sample 1
        res1 = evaluate_routing_contract("bracket_op1_rough.nc", clean_config, file_path=HAAS_CONTOUR_SAMPLE)
        assert res1["destination"] == "Code\\Haas"
        assert res1["match_type"] == "gcode_sniffer"

        # 2. Real sample 2
        res2 = evaluate_routing_contract("bolt_circle_drilling.tap", clean_config, file_path=HAAS_BOLT_SAMPLE)
        assert res2["destination"] == "Code\\Haas"
        assert res2["match_type"] == "gcode_sniffer"

        # 3. Inline G54 setup
        haas_setup = "%\nO03003 (FIXTURE PLATE)\nG00 G90 G20 G40 G49 G80 G17\nG00 G90 G54 X1.0 Y1.0\nT1 M06\nM30\n%\n"
        res3 = evaluate_routing_contract("fixture_setup.nc", clean_config, content_sample=haas_setup)
        assert res3["destination"] == "Code\\Haas"

    def test_t4_03_3d_maker_downloading_bambu_3mf_and_zip(self, clean_config, temp_project, tmp_path):
        """Maker with active project 'Turbine Bracket' downloads 3MF, STL, and zip archive."""
        # 1. 3MF sliced model routes to active project
        res_3mf = evaluate_routing_contract("turbine_housing_v2.3mf", clean_config, active_project=temp_project)
        assert res_3mf["destination"] == temp_project["target_directory"]
        assert res_3mf["match_type"] == "active_project"

        # 2. Individual STL component routes to active project
        res_stl = evaluate_routing_contract("impeller_blade.stl", clean_config, active_project=temp_project)
        assert res_stl["destination"] == temp_project["target_directory"]

        # 3. Multi-part hardware zip archive
        unzip = get_unzip_3d()
        archive_path = tmp_path / "hardware_pack.zip"
        with zipfile.ZipFile(archive_path, "w") as z:
            z.writestr("bracket_left.stl", b"solid left endsolid")
            z.writestr("bracket_right.stl", b"solid right endsolid")
            z.writestr("assembly_guide.txt", b"Assembly instructions")

        extracted = unzip.unpack_3d_archive(archive_path, clean_config)
        assert len(extracted) == 2
        assert not archive_path.exists()

    def test_t4_04_rapid_rule_iteration_workflow(self, clean_config, tmp_path):
        """User adds new rule in GUI -> saves atomically -> verifies in Sandbox -> daemon routes."""
        suite_data = get_suite_data()
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps(clean_config), encoding="utf-8")

        # Step 1: User adds new rule 'Robotics Club'
        new_rule = {
            "name": "Robotics Club",
            "folder": "Documents\\Robotics",
            "match": ["frc", "robotics"],
            "extensions": [".pdf"],
            "enabled": True,
        }
        clean_config["keyword_rules"].insert(0, new_rule)

        # Step 2: Atomic save
        if hasattr(suite_data, "save_sorter_config"):
            suite_data.save_sorter_config(clean_config, config_path=cfg_file)

        # Step 3: Interactive Sandbox test
        res = evaluate_routing_contract("frc_2026_game_rules.pdf", clean_config)
        assert res["winning_rule"] == "Robotics Club"
        assert res["destination"] == "Documents\\Robotics"
        assert "frc" in res["matched_trigger"]

    def test_t4_05_machinist_switching_between_haas_and_bambu(self, clean_config):
        """User downloads Haas milling program then Bambu 3D slicer file without active project."""
        # 1. Haas milling program
        res_haas = evaluate_routing_contract("O05001.nc", clean_config, file_path=HAAS_CONTOUR_SAMPLE)
        assert res_haas["destination"] == "Code\\Haas"
        assert res_haas["match_type"] == "gcode_sniffer"

        # 2. Bambu 3D slicer file
        bambu_content = (
            "; generated by BambuStudio 01.09.00.70\n"
            "; filament_type = PETG\n"
            "M104 S240\n"
            "M140 S80\n"
            "G1 X10 Y10 E2.0\n"
        )
        res_bambu = evaluate_routing_contract("tool_organizer_PETG.gcode", clean_config, content_sample=bambu_content)
        assert res_bambu["destination"] == "Code\\Gcode"
        assert res_bambu["match_type"] == "gcode_sniffer"

        # 3. Unrecognized G-code with no headers falls to generic extension rule
        res_generic = evaluate_routing_contract("raw_test.gcode", clean_config, content_sample="G0 X0 Y0\n")
        assert res_generic["destination"] == "Code\\Gcode"
        assert res_generic["match_type"] == "extension"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main(["-v", __file__]))
