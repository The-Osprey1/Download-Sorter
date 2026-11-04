"""Single-source attribution: Made by Devyn Fidel (2026) everywhere."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

import about

ROOT = Path(__file__).resolve().parent.parent

CLI_SCRIPTS = [
    "sort_once", "sort_undo", "status", "clean_old", "gcode_peek", "unzip_3d",
    "unzip_archive", "quick_switcher", "ribbon_popup", "toggle_pause", "watch",
    "tray", "rules_manager", "remodel",
]

HELPER_SCRIPTS = [
    "hold_manager", "batch_handoff", "clean_test_history", "restore_from_log",
]


def test_credit_constants():
    assert about.AUTHOR == "Devyn Fidel"
    assert about.YEAR == 2026
    assert about.CREDIT == "Made by Devyn Fidel (2026)"
    assert about.__version__.startswith("2.")
    assert about.CREDIT in about.version_string()
    assert about.CREDIT in about.about_text()
    assert about.REPO_URL in about.about_text()


def test_version_banner_is_ascii_safe_for_any_console():
    about.version_string().encode("ascii")


@pytest.mark.parametrize("script", CLI_SCRIPTS)
def test_every_cli_prints_credit_on_version(script):
    result = subprocess.run(
        [sys.executable, str(ROOT / f"{script}.py"), "--version"],
        capture_output=True, text=True, timeout=60, cwd=str(ROOT),
    )
    assert result.returncode == 0, result.stderr
    assert about.CREDIT in result.stdout
    assert about.__version__ in result.stdout


@pytest.mark.parametrize("script", HELPER_SCRIPTS)
@pytest.mark.parametrize("flag", ["--version", "-V"])
def test_helper_scripts_print_credit_on_version_and_v(script, flag):
    result = subprocess.run(
        [sys.executable, str(ROOT / f"{script}.py"), flag],
        capture_output=True, text=True, timeout=60, cwd=str(ROOT),
    )
    assert result.returncode == 0, result.stderr
    assert about.CREDIT in result.stdout
    assert about.__version__ in result.stdout


@pytest.mark.parametrize("script", ["setup.bat", "update.bat", "install_context_menu.ps1", "install_startup.ps1"])
def test_install_scripts_print_credit(script):
    assert about.CREDIT in (ROOT / script).read_text(encoding="utf-8", errors="ignore")


def test_docs_and_license_credit_the_author():
    assert "Devyn Fidel" in (ROOT / "LICENSE").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert about.CREDIT in readme
    assert about.CREDIT in (ROOT / "FLOWCHART.md").read_text(encoding="utf-8")
    assert about.REPO_URL in readme


def test_packaging_metadata_credits_author_and_reads_version_from_about():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'name = "Devyn Fidel"' in text
    assert 'attr = "about.__version__"' in text
    assert '"batch_handoff"' in text
    assert "version =" not in text.split("[project]")[1].split("[")[0].replace('version = {attr', "")


@pytest.mark.parametrize("module", ["tray.py", "status.py", "rules_manager.py"])
def test_ui_surfaces_source_credit_from_about(module):
    source = (ROOT / module).read_text(encoding="utf-8")
    assert "about.CREDIT" in source
    assert "Devyn Fidel" not in source  # never hard-coded; always imported
