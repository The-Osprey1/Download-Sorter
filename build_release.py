"""Automated standalone release packaging script for Download Sorter.

Builds a self-contained Windows x64 distribution using PyInstaller, packages
all documentation, license, and installer scripts, creates a zip archive, and
generates SHA-256 verification hashes for public release.
"""

from __future__ import annotations

from collections.abc import Callable
import ctypes
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
import about
import installer_builder

VERSION = about.__version__
ZIP_NAME = f"DownloadSorter-v{VERSION}-windows-x64.zip"
SETUP_NAME = f"DownloadSorter-Setup-v{VERSION}-windows-x64.exe"
VCPP_RUNTIME_PREFIXES = ("vcruntime140", "msvcp140", "concrt140", "vccorlib140")


def calculate_sha256(file_path: Path) -> str:
    """Compute SHA-256 checksum of a file."""
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def _verify_app_executables() -> int:
    exe_path = RELEASE_DIR / "DownloadSorter.exe"
    cli_exe_path = RELEASE_DIR / "DownloadSorter-cli.exe"
    if not exe_path.exists():
        print(f"[ERROR] Built windowed executable not found at {exe_path}")
        return 1
    if not cli_exe_path.exists():
        print(f"[ERROR] Built CLI executable not found at {cli_exe_path}")
        return 1
    return 0


def get_pe_file_version(path: Path) -> tuple[int, int, int, int] | None:
    """Read the 4-part numeric FileVersion from a Windows PE binary's version resource."""
    if sys.platform != "win32":
        return None
    from ctypes import wintypes

    class _VSFixedFileInfo(ctypes.Structure):
        _fields_ = [
            ("dwSignature", wintypes.DWORD),
            ("dwStrucVersion", wintypes.DWORD),
            ("dwFileVersionMS", wintypes.DWORD),
            ("dwFileVersionLS", wintypes.DWORD),
            ("dwProductVersionMS", wintypes.DWORD),
            ("dwProductVersionLS", wintypes.DWORD),
            ("dwFileFlagsMask", wintypes.DWORD),
            ("dwFileFlags", wintypes.DWORD),
            ("dwFileOS", wintypes.DWORD),
            ("dwFileType", wintypes.DWORD),
            ("dwFileSubtype", wintypes.DWORD),
            ("dwFileDateMS", wintypes.DWORD),
            ("dwFileDateLS", wintypes.DWORD),
        ]

    try:
        version_dll = ctypes.windll.version
        size = version_dll.GetFileVersionInfoSizeW(str(path), None)
        if not size:
            return None
        buf = ctypes.create_string_buffer(size)
        if not version_dll.GetFileVersionInfoW(str(path), 0, size, buf):
            return None
        ptr = ctypes.c_void_p()
        length = wintypes.UINT()
        if not version_dll.VerQueryValueW(buf, "\\", ctypes.byref(ptr), ctypes.byref(length)):
            return None
        if not ptr.value:
            return None
        info = _VSFixedFileInfo.from_address(ptr.value)
        return (
            (info.dwFileVersionMS >> 16) & 0xFFFF,
            info.dwFileVersionMS & 0xFFFF,
            (info.dwFileVersionLS >> 16) & 0xFFFF,
            info.dwFileVersionLS & 0xFFFF,
        )
    except OSError:
        return None


def _format_version_tuple(ver: tuple[int, int, int, int] | None) -> str:
    return ".".join(str(part) for part in ver) if ver is not None else "unknown"


def find_vswhere(path_override: Path | None = None) -> Path | None:
    """Locate ``vswhere.exe`` on Windows."""
    if path_override is not None:
        return path_override if path_override.is_file() else None
    on_path = shutil.which("vswhere") or shutil.which("vswhere.exe")
    if on_path:
        return Path(on_path)
    pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    standard = Path(pf86) / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
    return standard if standard.is_file() else None


def _msvc_version_key(version_dir: Path) -> tuple[int, ...]:
    parts: list[int] = []
    for token in version_dir.name.split("."):
        if token.isdigit():
            parts.append(int(token))
    return tuple(parts)


def find_vcpp_redist_dirs(
    *,
    vswhere_path: Path | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> list[Path]:
    """Use ``vswhere`` to find ``VC\\Redist\\MSVC\\<version>\\x64\\Microsoft.VC143.CRT`` folders."""
    exe = find_vswhere(vswhere_path)
    if exe is None:
        return []
    run_cmd = runner or subprocess.run
    try:
        res = run_cmd(
            [str(exe), "-products", "*", "-property", "installationPath"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if res.returncode != 0:
        return []

    crt_dirs: list[tuple[tuple[int, ...], Path]] = []
    for raw_line in (res.stdout or "").splitlines():
        install_root = raw_line.strip()
        if not install_root:
            continue
        msvc_root = Path(install_root) / "VC" / "Redist" / "MSVC"
        if not msvc_root.is_dir():
            continue
        for version_dir in msvc_root.iterdir():
            if not version_dir.is_dir():
                continue
            crt = version_dir / "x64" / "Microsoft.VC143.CRT"
            if crt.is_dir():
                crt_dirs.append((_msvc_version_key(version_dir), crt))

    crt_dirs.sort(key=lambda item: item[0], reverse=True)
    return [crt for _, crt in crt_dirs]


def _is_vcpp_runtime_dll(path: Path, redist_names: set[str]) -> bool:
    low = path.name.lower()
    return low.endswith(".dll") and (low in redist_names or low.startswith(VCPP_RUNTIME_PREFIXES))


def replace_vcpp_runtime_dlls(
    app_dir: Path,
    *,
    redist_dirs: list[Path] | None = None,
    vswhere_path: Path | None = None,
    sig_runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    version_getter: Callable[[Path], tuple[int, int, int, int] | None] | None = None,
    vswhere_runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> list[str]:
    """Replace bundled VC++ runtime DLLs lacking embedded signatures with Microsoft's signed redist copies.

    Never fails the build when Visual Studio / redistributables are missing or older;
    leaves files untouched and logs a warning instead.
    """
    if sys.platform != "win32" and redist_dirs is None and sig_runner is None:
        return []

    import pe_signing

    resolved_redist = (
        redist_dirs
        if redist_dirs is not None
        else find_vcpp_redist_dirs(vswhere_path=vswhere_path, runner=vswhere_runner)
    )
    redist_index: dict[str, list[Path]] = {}
    for crt_dir in resolved_redist:
        if not crt_dir.is_dir():
            continue
        for item in sorted(crt_dir.iterdir(), key=lambda p: p.name.lower()):
            if item.is_file() and item.suffix.lower() == ".dll":
                redist_index.setdefault(item.name.lower(), []).append(item)

    redist_names = set(redist_index)
    target_dlls = [
        p
        for p in sorted(app_dir.rglob("*.dll"), key=lambda p: p.relative_to(app_dir).as_posix().lower())
        if _is_vcpp_runtime_dll(p, redist_names)
    ]
    if not target_dlls:
        return []

    try:
        bundled_sigs = pe_signing.inspect_pe_signatures(app_dir, paths=target_dlls, runner=sig_runner)
    except Exception as exc:
        print(f"[WARNING] Could not inspect bundled VC++ runtime DLL signatures: {exc}")
        return []

    needing_replacement = [info for info in bundled_sigs if not info.is_valid]
    if not needing_replacement:
        return []

    if not redist_index:
        names = ", ".join(info.rel_path for info in needing_replacement)
        print(
            "[WARNING] No Visual Studio Microsoft.VC143.CRT redistributable folder found via vswhere; "
            f"leaving bundled VC++ runtime DLL(s) unchanged: {names}"
        )
        return []

    # Check embedded Authenticode signatures on all candidate redistributable DLLs in one batch.
    candidate_paths: list[Path] = []
    seen_candidates: set[Path] = set()
    for info in needing_replacement:
        for cand in redist_index.get(info.path.name.lower(), ()):
            if cand not in seen_candidates:
                seen_candidates.add(cand)
                candidate_paths.append(cand)

    valid_candidates: set[Path] = set()
    if candidate_paths:
        try:
            cand_sigs = pe_signing.inspect_pe_signatures(app_dir, paths=candidate_paths, runner=sig_runner)
            valid_candidates = {sig.path for sig in cand_sigs if sig.is_valid}
        except Exception as exc:
            print(f"[WARNING] Could not inspect VC++ redistributable signatures: {exc}")
            return []

    get_ver = version_getter or get_pe_file_version
    replaced: list[str] = []
    for info in needing_replacement:
        bundled_ver = get_ver(info.path)
        chosen: tuple[Path, tuple[int, int, int, int]] | None = None
        for cand in redist_index.get(info.path.name.lower(), ()):
            if cand not in valid_candidates:
                continue
            cand_ver = get_ver(cand)
            if bundled_ver is None or cand_ver is None:
                continue
            if cand_ver >= bundled_ver:
                chosen = (cand, cand_ver)
                break

        if chosen is None:
            print(
                f"[WARNING] No suitable embedded-signed VC++ redistributable copy (>= {_format_version_tuple(bundled_ver)}) "
                f"found for {info.rel_path}; leaving bundled copy unchanged."
            )
            continue

        cand_path, cand_ver = chosen
        shutil.copy2(cand_path, info.path)
        replaced.append(info.rel_path)
        print(
            f"[*] Replaced {info.rel_path} ({_format_version_tuple(bundled_ver)}) "
            f"with embedded-signed {cand_path} ({_format_version_tuple(cand_ver)})"
        )
    return replaced


def build_app_stage() -> int:
    """Run PyInstaller and assemble ``dist/DownloadSorter/`` (without ZIP or setup)."""
    print("=" * 60)
    print(f"  Building Download Sorter v{VERSION} Standalone App Stage")
    print("=" * 60)

    # 1. Clean previous build artifacts
    if RELEASE_DIR.exists():
        print(f"[*] Removing previous dist directory: {RELEASE_DIR}")
        shutil.rmtree(RELEASE_DIR, ignore_errors=True)

    spec_file = ROOT / "DownloadSorter.spec"
    if not spec_file.exists():
        print(f"[ERROR] Spec file not found at {spec_file}")
        return 1

    # 2. Ensure application icon is generated
    ico_path = ROOT / "DownloadSorter.ico"
    print(f"[*] Generating application icon: {ico_path.name}...")
    installer_builder.generate_app_icon(ico_path)

    # 3. Invoke PyInstaller
    print("[*] Running PyInstaller...")
    import version_resource

    version_resource.write(ROOT / version_resource.VERSION_FILE)
    cmd = [sys.executable, "-m", "PyInstaller", str(spec_file), "--noconfirm"]
    res = subprocess.run(cmd, cwd=str(ROOT))
    if res.returncode != 0:
        print(f"[ERROR] PyInstaller failed with exit code {res.returncode}")
        return res.returncode

    if _verify_app_executables() != 0:
        return 1

    print("[OK] Standalone executables compiled:\n     - DownloadSorter.exe\n     - DownloadSorter-cli.exe")

    # 4. Create CMD launcher wrapper for easy command line access
    cmd_launcher = RELEASE_DIR / "DownloadSorter.cmd"
    cmd_launcher.write_text('@echo off\n"%~dp0DownloadSorter-cli.exe" %*\n', encoding="utf-8")
    print(f"[*] Generated CLI launcher wrapper: {cmd_launcher.name}")

    # 5. Copy scripts, configs, and documentation into release directory
    extra_files = [
        # The uninstaller that "Installed apps" runs, and the two helpers it calls.
        # (setup.bat and the install_*.ps1 scripts set up the 2.x Python version and
        # are not shipped: 3.0 registers startup and the right-click menu itself.)
        "uninstall.bat",
        "uninstall.ps1",
        "uninstall_context_menu.ps1",
        "uninstall_startup.ps1",
        "README.md",
        "LICENSE",
        "FLOWCHART.md",
        "CHANGELOG.md",
        "CONTRIBUTING.md",
        "SECURITY.md",
        "BETA_TESTING.md",
        "DownloadSorter.ico",
    ]
    for filename in extra_files:
        src = ROOT / filename
        if src.exists():
            shutil.copy2(src, RELEASE_DIR / filename)
            print(f"[*] Bundled: {filename}")
        else:
            print(f"[WARNING] Optional release file not found to bundle: {filename}")

    ext_src = ROOT / "chrome_extension"
    if ext_src.is_dir():
        shutil.copytree(ext_src, RELEASE_DIR / "chrome_extension", dirs_exist_ok=True)
        print("[*] Bundled: chrome_extension/")

    # 6. Replace catalog-only VC++ runtime DLLs with embedded-signed Microsoft redistributables
    replace_vcpp_runtime_dlls(RELEASE_DIR)

    print(f"[OK] App stage ready at {RELEASE_DIR}")
    return 0


def write_checksums(
    dist_dir: Path | None = None,
    *,
    zip_path: Path | None = None,
    setup_path: Path | None = None,
) -> Path:
    """Compute SHA-256 checksums for the setup installer and ZIP archive."""
    resolved_dist = Path(dist_dir) if dist_dir is not None else DIST_DIR
    resolved_zip = Path(zip_path) if zip_path is not None else (resolved_dist / ZIP_NAME)
    resolved_setup = Path(setup_path) if setup_path is not None else (resolved_dist / SETUP_NAME)
    checksum_file = resolved_dist / "checksums.txt"

    lines: list[str] = []
    if resolved_setup.exists():
        lines.append(f"{calculate_sha256(resolved_setup)}  {resolved_setup.name}")
    if resolved_zip.exists():
        lines.append(f"{calculate_sha256(resolved_zip)}  {resolved_zip.name}")
    checksum_file.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return checksum_file


def build_package_stage(use_inno: bool = False) -> int:
    """Create the ZIP archive, setup installer, and ``checksums.txt`` from ``dist/DownloadSorter/``."""
    if not RELEASE_DIR.is_dir():
        print(f"[ERROR] App stage directory not found at {RELEASE_DIR} (run --stage app first)")
        return 1
    if _verify_app_executables() != 0:
        return 1

    print("=" * 60)
    print(f"  Packaging Download Sorter v{VERSION} Distribution")
    print("=" * 60)

    # 1. Package into ZIP archive
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = DIST_DIR / ZIP_NAME
    if zip_path.exists():
        zip_path.unlink()

    print(f"[*] Creating ZIP distribution archive: {zip_path.name}...")
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for file in sorted(RELEASE_DIR.rglob("*")):
            if file.is_file():
                arcname = file.relative_to(DIST_DIR)
                zf.write(file, arcname)

    zip_size_mb = round(zip_path.stat().st_size / (1024 * 1024), 2)
    print(f"[OK] ZIP archive created ({zip_size_mb} MB)")

    # 2. Build Setup Executable
    setup_path = DIST_DIR / SETUP_NAME
    if setup_path.exists():
        setup_path.unlink()

    print(f"[*] Building single-file setup executable: {setup_path.name}...")
    installer_builder.build_setup_exe(
        dist_dir=RELEASE_DIR,
        version=VERSION,
        output_exe=setup_path,
        use_inno=use_inno,
    )
    setup_size_mb = round(setup_path.stat().st_size / (1024 * 1024), 2)
    print(f"[OK] Setup executable created ({setup_size_mb} MB)")

    # 3. Compute SHA-256 Checksums
    checksum_file = write_checksums(DIST_DIR, zip_path=zip_path, setup_path=setup_path)
    setup_checksum = calculate_sha256(setup_path) if setup_path.exists() else None
    zip_checksum = calculate_sha256(zip_path)

    print("=" * 60)
    print("  Build Succeeded!")
    if setup_path.exists():
        print(f"  Setup Exe:    {setup_path}")
        print(f"  Setup SHA256: {setup_checksum}")
    print(f"  Distribution: {zip_path}")
    print(f"  ZIP SHA256:   {zip_checksum}")
    print(f"  Checksum Log: {checksum_file}")
    print("=" * 60)
    return 0


def build_standalone(use_inno: bool = False, stage: str = "all") -> int:
    if stage not in ("all", "app", "package"):
        raise ValueError(f"Unknown build stage: {stage!r}")
    if stage in ("all", "app"):
        rc = build_app_stage()
        if rc != 0:
            return rc
    if stage in ("all", "package"):
        rc = build_package_stage(use_inno=use_inno)
        if rc != 0:
            return rc
    return 0


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Build the Download Sorter Windows release.")
    parser.add_argument(
        "--inno",
        action="store_true",
        help="Build the setup file with Inno Setup (installer.iss) instead of the single-file installer.",
    )
    parser.add_argument(
        "--stage",
        choices=("all", "app", "package"),
        default="all",
        help="Run only 'app' (PyInstaller + dist/DownloadSorter), 'package' (ZIP, setup, checksums), or 'all' (default).",
    )
    parsed = parser.parse_args()
    sys.exit(build_standalone(use_inno=parsed.inno, stage=parsed.stage))
