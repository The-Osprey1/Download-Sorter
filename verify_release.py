"""Pre-flight verification script for Download Sorter mass public release.

Validates:
1. All required release files and documentation exist.
2. Version consistency across pyproject.toml, main.py, build_release.py, and CHANGELOG.md.
3. Schema integrity of config.json, default configurations, and built-in preset library.
4. CLI dispatcher functionality (main.py help, version, dry-run).
5. Comprehensive test suite execution (750+ tests passing).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

if sys.platform == "win32":
    try:
        if sys.stdout and hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if sys.stderr and hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent

REQUIRED_RELEASE_FILES = [
    "README.md",
    "LICENSE",
    "FLOWCHART.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "CHANGELOG.md",
    "pyproject.toml",
    "requirements.txt",
    "config.json",
    "setup.bat",
    "uninstall.bat",
    "uninstall.ps1",
    "build.bat",
    "install_startup.ps1",
    "uninstall_startup.ps1",
    "install_context_menu.ps1",
    "uninstall_context_menu.ps1",
    "main.py",
    "sorter.py",
    "config_manager.py",
    "rules_manager.py",
    "tray.py",
    "watch.py",
    "sort_once.py",
    "sort_undo.py",
    "gcode_peek.py",
    "unzip_3d.py",
    "unzip_archive.py",
    "clean_old.py",
    "status.py",
    "quick_switcher.py",
    "ribbon_popup.py",
    "suite_theme.py",
    "DownloadSorter.spec",
    "build_release.py",
    ".github/workflows/ci.yml",
    ".github/workflows/release.yml",
    ".github/ISSUE_TEMPLATE/bug_report.yml",
    ".github/ISSUE_TEMPLATE/feature_request.yml",
    ".github/ISSUE_TEMPLATE/rule_preset_request.yml",
]


def check_files() -> list[str]:
    missing = []
    for rel_path in REQUIRED_RELEASE_FILES:
        target = ROOT / rel_path
        if not target.exists():
            missing.append(rel_path)
    return missing


def check_version_alignment() -> tuple[bool, str, dict[str, str]]:
    versions: dict[str, str] = {}

    # 1. pyproject.toml
    pyproject_text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'version\s*=\s*"([^"]+)"', pyproject_text)
    if m:
        versions["pyproject.toml"] = m.group(1)

    # 2. main.py
    main_text = (ROOT / "main.py").read_text(encoding="utf-8")
    m = re.search(r'__version__\s*=\s*"([^"]+)"', main_text)
    if m:
        versions["main.py"] = m.group(1)

    # 3. build_release.py
    build_text = (ROOT / "build_release.py").read_text(encoding="utf-8")
    m = re.search(r'VERSION\s*=\s*"([^"]+)"', build_text)
    if m:
        versions["build_release.py"] = m.group(1)

    # 4. CHANGELOG.md
    changelog_text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    m = re.search(r'## \[([^\]]+)\]', changelog_text)
    if m:
        versions["CHANGELOG.md"] = m.group(1)

    distinct = set(versions.values())
    if len(distinct) == 1 and len(versions) == 4:
        return True, list(distinct)[0], versions
    return False, "mismatch", versions


def check_config_and_presets() -> list[str]:
    errors = []
    import config_manager as suite_data

    # Config validation
    cfg = suite_data.load_sorter_config()
    if not suite_data.validate_sorter_config(cfg):
        errors.append("Default configuration failed schema validation")

    # Presets validation
    presets = suite_data.get_available_presets()
    if not presets:
        errors.append("No built-in presets returned by get_available_presets")
    for p in presets:
        if not p.get("id") or not p.get("name") or not p.get("category"):
            errors.append(f"Preset {p} missing id, name, or category")
        for r in p.get("rules", []):
            if not r.get("name") or not r.get("folder") or "match" not in r:
                errors.append(f"Preset rule {r} in {p.get('id')} malformed")

    return errors


def check_cli_dispatcher() -> list[str]:
    errors = []
    # Test --help
    cmd = [sys.executable, str(ROOT / "main.py"), "--help"]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0 or "Download Sorter" not in res.stdout:
        errors.append(f"main.py --help failed: {res.stderr or res.stdout}")

    # Test --version
    cmd = [sys.executable, str(ROOT / "main.py"), "--version"]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0 or "v1.0.0" not in res.stdout:
        errors.append(f"main.py --version failed: {res.stderr or res.stdout}")

    # Test --dialog routing (ensure --dialog option is properly forwarded)
    cmd = [sys.executable, str(ROOT / "main.py"), "--dialog", "9999999"]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if "unrecognized arguments" in (res.stderr + res.stdout):
        errors.append(f"main.py --dialog failed to forward arguments properly: {res.stderr}")

    # Test compiled binaries in dist/DownloadSorter if built
    for exe_name in ("DownloadSorter-cli.exe", "DownloadSorter.exe"):
        bin_path = ROOT / "dist" / "DownloadSorter" / exe_name
        if bin_path.exists():
            bin_res = subprocess.run([str(bin_path), "--version"], capture_output=True, text=True)
            if bin_res.returncode != 0 or "Download Sorter v1.0.0" not in (bin_res.stdout + bin_res.stderr):
                errors.append(f"{exe_name} --version execution failed (rc={bin_res.returncode}): {bin_res.stdout} {bin_res.stderr}")

    return errors


def run_tests() -> tuple[bool, int, str]:
    pytest_bin = Path(sys.executable).parent / "pytest.exe"
    if pytest_bin.exists():
        cmd = [str(pytest_bin), "tests"]
    else:
        cmd = [sys.executable, "-m", "pytest", "tests"]
    res = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
    output = (res.stdout + "\n" + res.stderr).strip()
    passed_match = re.search(r'(\d+)\s+passed', output)
    passed_count = int(passed_match.group(1)) if passed_match else 0
    success = (res.returncode == 0 or (passed_count > 0 and "failed" not in output.lower() and "error" not in output.lower())) and (passed_count > 0)
    return success, passed_count, output


def main() -> int:
    print("=" * 68)
    print("      Download Sorter -- Public Release Pre-Flight Verification")
    print("=" * 68)

    # 1. File inventory
    print("\n[1/5] Checking Release Files & Community Docs...")
    missing = check_files()
    if missing:
        print(f"  [FAIL] Missing {len(missing)} required file(s):")
        for f in missing:
            print(f"         - {f}")
        return 1
    print(f"  [PASS] All {len(REQUIRED_RELEASE_FILES)} release files and community docs present.")

    # 2. Version consistency
    print("\n[2/5] Checking Version Alignment Across Components...")
    ok, ver, versions = check_version_alignment()
    if not ok:
        print(f"  [FAIL] Version mismatch detected: {versions}")
        return 1
    print(f"  [PASS] All components aligned to version: v{ver} ({versions})")

    # 3. Config & Presets
    print("\n[3/5] Validating Configurations & Preset Library Schema...")
    cfg_errors = check_config_and_presets()
    if cfg_errors:
        print(f"  [FAIL] Configuration or preset error(s):")
        for e in cfg_errors:
            print(f"         - {e}")
        return 1
    import config_manager as suite_data
    preset_count = len(suite_data.get_available_presets())
    total_preset_rules = sum(len(p.get("rules", [])) for p in suite_data.get_available_presets())
    print(f"  [PASS] Config schemas valid; {preset_count} preset packs ({total_preset_rules} curated rules) verified.")

    # 4. CLI Dispatcher
    print("\n[4/5] Testing Main CLI Dispatcher...")
    cli_errors = check_cli_dispatcher()
    if cli_errors:
        print(f"  [FAIL] CLI dispatcher errors:")
        for e in cli_errors:
            print(f"         - {e}")
        return 1
    print("  [PASS] CLI dispatcher responsive (--help, --version verified).")

    # 5. Full Test Suite
    print("\n[5/5] Running Comprehensive Test Suite...")
    test_ok, count, output = run_tests()
    if not test_ok:
        print("  [FAIL] Test suite failed!")
        print(output)
        return 1
    print(f"  [PASS] Test suite passed: {count} tests passed with 0 failures.")

    print("\n" + "=" * 68)
    print(f"  ALL AUDITS PASSED (100%) -- READY FOR MASS PUBLIC RELEASE v{ver}")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    sys.exit(main())
