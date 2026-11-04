"""Adversarial stress-test harness for Milestone 1: Attribution & Packaging Completeness.

Empirically challenges:
1. CLI --version flag across all scripts.
2. CLI -V flag across all scripts (including remodel.py and legacy scripts).
3. Exact attribution banner strings and ASCII compatibility.
4. Mixed argument combinations (prefixed, suffixed, unrecognized arguments).
5. Empty argument execution.
6. Piped output integrity.
7. Packaging metadata in pyproject.toml.
8. PowerShell and batch installer attribution headers.
"""

from __future__ import annotations

import io
import subprocess
import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import about

ALL_SCRIPTS = [
    "remodel.py",
    "hold_manager.py",
    "batch_handoff.py",
    "clean_test_history.py",
    "restore_from_log.py",
    "sort_once.py",
    "sort_undo.py",
    "status.py",
    "clean_old.py",
    "gcode_peek.py",
    "unzip_3d.py",
    "unzip_archive.py",
    "quick_switcher.py",
    "ribbon_popup.py",
    "toggle_pause.py",
    "watch.py",
    "tray.py",
    "rules_manager.py",
]

M1_OWNED_SCRIPTS = [
    "remodel.py",
    "hold_manager.py",
    "batch_handoff.py",
    "clean_test_history.py",
    "restore_from_log.py",
]


# =============================================================================
# 1. EXACT ATTRIBUTION STRINGS & PACKAGING
# =============================================================================

def test_oracle_attribution_constants():
    assert about.CREDIT == "Made by Devyn Fidel (2026)"
    assert about.version_string() == f"Download Sorter {about.__version__} - Made by Devyn Fidel (2026)"
    assert "Made by Devyn Fidel (2026)" in about.about_text()


def test_oracle_packaging_metadata():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'name = "Devyn Fidel"' in pyproject
    assert 'attr = "about.__version__"' in pyproject
    assert '"batch_handoff"' in pyproject


def test_oracle_shell_scripts_attribution():
    for script in ["setup.bat", "update.bat", "install_context_menu.ps1", "install_startup.ps1"]:
        content = (ROOT / script).read_text(encoding="utf-8", errors="ignore")
        assert "Made by Devyn Fidel (2026)" in content, f"Attribution missing in {script}"


# =============================================================================
# 2. ALL SCRIPTS UNDER --version
# =============================================================================

@pytest.mark.parametrize("script_name", ALL_SCRIPTS)
def test_all_scripts_version_flag_exits_zero(script_name):
    script_path = ROOT / script_name
    assert script_path.exists(), f"Script {script_name} does not exist"
    res = subprocess.run(
        [sys.executable, str(script_path), "--version"],
        capture_output=True, text=True, timeout=15, cwd=str(ROOT),
    )
    assert res.returncode == 0, f"{script_name} --version failed with code {res.returncode}: {res.stderr}"
    assert res.stdout.strip() == about.version_string()
    assert "Made by Devyn Fidel (2026)" in res.stdout


# =============================================================================
# 3. M1 SCRIPTS UNDER -V (CHALLENGE / STRESS POINT)
# =============================================================================

@pytest.mark.parametrize("script_name", ["hold_manager.py", "batch_handoff.py", "clean_test_history.py", "restore_from_log.py"])
def test_helper_scripts_v_flag_exits_zero(script_name):
    script_path = ROOT / script_name
    res = subprocess.run(
        [sys.executable, str(script_path), "-V"],
        capture_output=True, text=True, timeout=15, cwd=str(ROOT),
    )
    assert res.returncode == 0, f"{script_name} -V failed: {res.stderr}"
    assert res.stdout.strip() == about.version_string()


def test_remodel_v_flag_adversarial_vulnerability():
    """Documents the failure of remodel.py under -V flag.
    
    remodel.py configures parser.add_argument('--version') without '-V',
    causing argparse to reject -V with exit code 2.
    """
    script_path = ROOT / "remodel.py"
    res = subprocess.run(
        [sys.executable, str(script_path), "-V"],
        capture_output=True, text=True, timeout=15, cwd=str(ROOT),
    )
    # remodel.py now correctly supports -V
    assert res.returncode == 0
    assert about.version_string() in res.stdout


# =============================================================================
# 4. MIXED ARGUMENTS & EDGE CASES
# =============================================================================

@pytest.mark.parametrize("script_name", M1_OWNED_SCRIPTS)
@pytest.mark.parametrize("args", [
    ["--version", "--unknown-flag"],
    ["--unknown-flag", "--version"],
    ["", "--version"],
])
def test_version_precedence_with_mixed_args(script_name, args):
    script_path = ROOT / script_name
    res = subprocess.run(
        [sys.executable, str(script_path)] + args,
        capture_output=True, text=True, timeout=15, cwd=str(ROOT),
    )
    assert res.returncode == 0, f"{script_name} {args} failed: {res.stderr}"
    assert res.stdout.strip() == about.version_string()


# =============================================================================
# 5. PIPED STDOUT VERIFICATION
# =============================================================================

@pytest.mark.parametrize("script_name", M1_OWNED_SCRIPTS)
def test_piped_stdout_integrity(script_name):
    script_path = ROOT / script_name
    p1 = subprocess.Popen(
        [sys.executable, str(script_path), "--version"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    out, err = p1.communicate(timeout=10)
    assert p1.returncode == 0
    assert out.decode("utf-8").strip() == about.version_string()
