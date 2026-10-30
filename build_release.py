"""Automated standalone release packaging script for Download Sorter.

Builds a self-contained Windows x64 distribution using PyInstaller, packages
all documentation, license, and installer scripts, creates a zip archive, and
generates SHA-256 verification hashes for public release.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST_DIR = ROOT / "dist"
BUILD_DIR = ROOT / "build"
RELEASE_DIR = DIST_DIR / "DownloadSorter"
VERSION = "1.0.0"
ZIP_NAME = f"DownloadSorter-v{VERSION}-windows-x64.zip"


def calculate_sha256(file_path: Path) -> str:
    """Compute SHA-256 checksum of a file."""
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def build_standalone() -> int:
    print("=" * 60)
    print(f"  Building Download Sorter v{VERSION} Standalone Release")
    print("=" * 60)

    # 1. Clean previous build artifacts
    if RELEASE_DIR.exists():
        print(f"[*] Removing previous dist directory: {RELEASE_DIR}")
        shutil.rmtree(RELEASE_DIR, ignore_errors=True)

    spec_file = ROOT / "DownloadSorter.spec"
    if not spec_file.exists():
        print(f"[ERROR] Spec file not found at {spec_file}")
        return 1

    # 2. Invoke PyInstaller
    print("[*] Running PyInstaller...")
    cmd = [sys.executable, "-m", "PyInstaller", str(spec_file), "--noconfirm"]
    res = subprocess.run(cmd, cwd=str(ROOT))
    if res.returncode != 0:
        print(f"[ERROR] PyInstaller failed with exit code {res.returncode}")
        return res.returncode

    exe_path = RELEASE_DIR / "DownloadSorter.exe"
    cli_exe_path = RELEASE_DIR / "DownloadSorter-cli.exe"
    if not exe_path.exists():
        print(f"[ERROR] Built windowed executable not found at {exe_path}")
        return 1
    if not cli_exe_path.exists():
        print(f"[ERROR] Built CLI executable not found at {cli_exe_path}")
        return 1

    print(f"[OK] Standalone executables compiled:\n     - {exe_path.name}\n     - {cli_exe_path.name}")

    # 3. Create CMD launcher wrapper for easy command line access
    cmd_launcher = RELEASE_DIR / "DownloadSorter.cmd"
    cmd_launcher.write_text('@echo off\n"%~dp0DownloadSorter-cli.exe" %*\n', encoding="utf-8")
    print(f"[*] Generated CLI launcher wrapper: {cmd_launcher.name}")

    # 4. Copy scripts, configs, and documentation into release directory
    extra_files = [
        "setup.bat",
        "uninstall.bat",
        "uninstall.ps1",
        "install_context_menu.ps1",
        "uninstall_context_menu.ps1",
        "install_startup.ps1",
        "uninstall_startup.ps1",
        "config.json",
        "README.md",
        "LICENSE",
        "FLOWCHART.md",
        "CHANGELOG.md",
        "CONTRIBUTING.md",
        "SECURITY.md",
    ]
    for filename in extra_files:
        src = ROOT / filename
        if src.exists():
            shutil.copy2(src, RELEASE_DIR / filename)
            print(f"[*] Bundled: {filename}")
        else:
            print(f"[WARNING] Optional release file not found to bundle: {filename}")

    # 4. Package into ZIP archive
    zip_path = DIST_DIR / ZIP_NAME
    if zip_path.exists():
        zip_path.unlink()

    print(f"[*] Creating ZIP distribution archive: {zip_path.name}...")
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for file in RELEASE_DIR.rglob("*"):
            if file.is_file():
                arcname = file.relative_to(DIST_DIR)
                zf.write(file, arcname)

    zip_size_mb = round(zip_path.stat().st_size / (1024 * 1024), 2)
    print(f"[OK] ZIP archive created ({zip_size_mb} MB)")

    # 5. Compute SHA-256 Checksums
    checksum = calculate_sha256(zip_path)
    checksum_file = DIST_DIR / "checksums.txt"
    with open(checksum_file, "w", encoding="utf-8") as f:
        f.write(f"{checksum}  {zip_path.name}\n")

    print("=" * 60)
    print("  Build Succeeded!")
    print(f"  Distribution: {zip_path}")
    print(f"  SHA-256:      {checksum}")
    print(f"  Checksum Log: {checksum_file}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(build_standalone())
