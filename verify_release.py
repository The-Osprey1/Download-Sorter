"""Release preflight gatekeeper for Download Sorter v3.

Run from the project root with the project interpreter:

    python verify_release.py                 # everything, including built artifacts
    python verify_release.py --skip-artifacts  # before build_release.py has run
    python verify_release.py --skip-tests      # quick re-check of a finished build
    python verify_release.py --require-signed  # fail if any shipped binary is unsigned

Stages (the script exits 1 at the first stage that fails):

1. Version parity: ``about.py`` (the single source of truth), the package's
   ``src/download_sorter/about.py``, ``pyproject.toml``, the newest released
   ``CHANGELOG.md`` heading, and ``installer.iss`` when present.
2. CLI surface: every documented flag is registered in ``download_sorter.cli``.
3. Source smoke test: ``main.py --version`` and a read-only ``--simulate``.
4. Test suite: ``pytest tests_v3`` exits 0.
5. Built artifacts: both executables, the ZIP, the setup file and ``checksums.txt``
   exist, the checksums match, and the executables pass ``--version`` / ``--simulate``.

Nothing here touches the real Downloads folder or the real state directory:
``--simulate`` runs against throwaway folders.
"""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

if sys.platform == "win32":
    for _stream in (sys.stdout, sys.stderr):
        try:
            if _stream is not None and hasattr(_stream, "reconfigure"):
                _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
TESTS_DIR = "tests_v3"
DIST = ROOT / "dist"

REQUIRED_CLI_FLAGS = [
    "--version",
    "--status",
    "--explain",
    "--simulate",
    "--sort-now",
    "--file-held",
    "--undo",
    "--home",
    "--tray",
    "--dialog",
    "--quit",
]

SUBPROCESS_TIMEOUT = 120


def _read_assigned_version(path: Path) -> str | None:
    """Read ``__version__ = "x.y.z"`` from a file without importing it."""
    if not path.is_file():
        return None
    m = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', path.read_text(encoding="utf-8"), re.MULTILINE)
    return m.group(1) if m else None


def collect_versions() -> dict[str, str | None]:
    versions: dict[str, str | None] = {}
    canonical = _read_assigned_version(ROOT / "about.py")
    versions["about.py"] = canonical
    versions["src/download_sorter/about.py"] = _read_assigned_version(SRC / "download_sorter" / "about.py")

    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    literal = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    if literal:
        versions["pyproject.toml"] = literal.group(1)
    elif re.search(r'version\s*=\s*\{\s*attr\s*=\s*"about\.__version__"\s*\}', pyproject):
        # Dynamic version: setuptools reads it from about.py at build time.
        versions["pyproject.toml"] = canonical
    else:
        versions["pyproject.toml"] = None

    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    # The newest released version; an "Unreleased" section above it is allowed.
    m = re.search(r"^## \[(?!Unreleased\])([^\]]+)\]", changelog, re.MULTILINE)
    versions["CHANGELOG.md"] = m.group(1) if m else None

    iss = ROOT / "installer.iss"
    if iss.is_file():
        m = re.search(r'#define\s+MyAppVersion\s+"([^"]+)"', iss.read_text(encoding="utf-8"))
        versions["installer.iss"] = m.group(1) if m else None
    return versions


def check_versions() -> tuple[list[str], str]:
    versions = collect_versions()
    canonical = versions.get("about.py")
    errors: list[str] = []
    if not canonical:
        return ["about.py has no __version__"], ""
    for name, value in versions.items():
        if value != canonical:
            errors.append(f"{name}: {value!r} (expected {canonical!r})")
    return errors, canonical


def check_cli_flags() -> list[str]:
    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))
    try:
        from download_sorter.cli import build_parser
    except Exception as exc:
        return [f"could not import download_sorter.cli: {exc}"]
    registered: set[str] = set()
    for action in build_parser()._actions:
        registered.update(action.option_strings)
    return [f"{flag} is not registered in cli.py" for flag in REQUIRED_CLI_FLAGS if flag not in registered]


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess[str]:  # noqa: ANN003
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=SUBPROCESS_TIMEOUT,
        **kwargs,
    )


def _smoke(launcher: list[str], label: str, version: str, *, simulate: bool) -> list[str]:
    """``--version`` (and optionally ``--simulate``) against throwaway folders."""
    errors: list[str] = []
    try:
        res = _run([*launcher, "--version"])
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [f"{label} --version could not run: {exc}"]
    out = res.stdout + res.stderr
    if res.returncode != 0 or version not in out:
        errors.append(f"{label} --version failed (rc={res.returncode}): {out.strip()[:300]}")

    if simulate:
        with tempfile.TemporaryDirectory(prefix="ds_verify_") as tmp:
            data_dir = Path(tmp) / "data"
            watch_dir = Path(tmp) / "Downloads"
            data_dir.mkdir()
            watch_dir.mkdir()
            (watch_dir / "preflight_report.pdf").write_bytes(b"%PDF-1.4 preflight")
            (watch_dir / "preflight_photo.jpg").write_bytes(b"\xff\xd8\xff preflight")
            try:
                res = _run(
                    [*launcher, "--simulate", "--data-dir", str(data_dir), "--watch-dir", str(watch_dir)]
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                return [*errors, f"{label} --simulate could not run: {exc}"]
            out = res.stdout + res.stderr
            still_there = all((watch_dir / n).is_file() for n in ("preflight_report.pdf", "preflight_photo.jpg"))
            if res.returncode != 0 or "preflight_report.pdf" not in out:
                errors.append(f"{label} --simulate failed (rc={res.returncode}): {out.strip()[:300]}")
            elif not still_there:
                errors.append(f"{label} --simulate moved files; it must be read-only")
    return errors


def run_tests() -> tuple[bool, str, str]:
    # No extra -q: pyproject already passes one, and a second hides the summary line.
    cmd = [sys.executable, "-X", "faulthandler", "-m", "pytest", TESTS_DIR]
    res = subprocess.run(
        cmd, cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    output = (res.stdout + "\n" + res.stderr).strip()
    summary = ""
    for line in reversed(output.splitlines()):
        if re.search(r"\d+\s+(passed|failed|error)", line):
            summary = line.strip().strip("=").strip()
            break
    # Exit code 0 is the only pass condition: it also rules out collection errors.
    return res.returncode == 0, summary or f"exit code {res.returncode}", output


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def check_signatures(
    version: str,
    *,
    require_signed: bool = False,
    pe_runner=None,
) -> tuple[list[str], str]:
    """Inspect embedded Authenticode signatures on built binaries, scripts, and the setup file.

    Returns ``(errors, summary_line)``. When ``require_signed`` is False, unsigned
    or catalog-only files are reported in the summary without failing preflight.
    """
    import pe_signing

    app_dir = DIST / "DownloadSorter"
    setup_file = DIST / f"DownloadSorter-Setup-v{version}-windows-x64.exe"
    if sys.platform != "win32" and pe_runner is None:
        if require_signed:
            return ["Authenticode verification requires Windows (--require-signed)"], "unavailable on non-Windows"
        return [], "skipped on non-Windows"

    runner_kwargs = {"runner": pe_runner} if pe_runner is not None else {}
    app_infos = pe_signing.inspect_pe_signatures(app_dir, **runner_kwargs)
    setup_infos = (
        pe_signing.inspect_pe_signatures(DIST, paths=[setup_file], **runner_kwargs)
        if setup_file.is_file()
        else []
    )
    all_infos = [*app_infos, *setup_infos]
    signed_count = sum(1 for info in all_infos if info.is_valid)
    unsigned_app = [info for info in app_infos if not info.is_valid]
    unsigned_setup = [info for info in setup_infos if not info.is_valid]
    unsigned_all = [*unsigned_app, *unsigned_setup]

    summary = (
        f"signatures: {signed_count}/{len(all_infos)} signable file(s) with embedded Authenticode, "
        f"{len(unsigned_all)} unsigned/non-embedded"
    )
    if not require_signed:
        return [], summary

    errors: list[str] = []
    for info in unsigned_app:
        kind = "script" if info.path.suffix.lower() == ".ps1" else "program/library file"
        detail = f"{info.status}, SignatureType={info.signature_type}" if info.signature_type else info.status
        errors.append(f"unsigned {kind}: dist/DownloadSorter/{info.rel_path} ({detail})")
    for info in unsigned_setup:
        detail = f"{info.status}, SignatureType={info.signature_type}" if info.signature_type else info.status
        errors.append(f"unsigned setup file: {info.path.name} ({detail})")
    return errors, summary


def check_artifacts(
    version: str,
    *,
    require_signed: bool = False,
    pe_runner=None,
) -> list[str]:
    gui_exe = DIST / "DownloadSorter" / "DownloadSorter.exe"
    cli_exe = DIST / "DownloadSorter" / "DownloadSorter-cli.exe"
    zip_file = DIST / f"DownloadSorter-v{version}-windows-x64.zip"
    setup_file = DIST / f"DownloadSorter-Setup-v{version}-windows-x64.exe"
    checksums = DIST / "checksums.txt"

    errors = [
        f"missing artifact: {p.relative_to(ROOT)}"
        for p in (gui_exe, cli_exe, zip_file, setup_file, checksums)
        if not p.is_file()
    ]
    if errors:
        return errors

    listed: dict[str, str] = {}
    for line in checksums.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) >= 2:
            listed[parts[-1]] = parts[0].lower()
    for artifact in (zip_file, setup_file):
        expected = listed.get(artifact.name)
        if expected is None:
            errors.append(f"checksums.txt has no entry for {artifact.name}")
        elif expected != _sha256(artifact):
            errors.append(f"checksums.txt does not match {artifact.name} (stale build?)")

    sig_errors, sig_summary = check_signatures(
        version,
        require_signed=require_signed,
        pe_runner=pe_runner,
    )
    print(f"  [INFO] {sig_summary}")
    errors += sig_errors

    errors += _smoke([str(cli_exe)], cli_exe.name, version, simulate=True)
    errors += _smoke([str(gui_exe)], gui_exe.name, version, simulate=True)
    return errors


def _report(errors: list[str], ok_message: str) -> bool:
    if errors:
        print(f"  [FAIL] {len(errors)} problem(s):")
        for err in errors:
            print(f"         - {err}")
        return False
    print(f"  [PASS] {ok_message}")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Download Sorter release preflight.")
    parser.add_argument("--skip-tests", action="store_true", help="Do not run the pytest suite.")
    parser.add_argument(
        "--skip-artifacts", action="store_true", help="Do not check dist/ (use before build_release.py)."
    )
    parser.add_argument(
        "--require-signed",
        action="store_true",
        help="Fail artifact checks if any .exe/.dll/.pyd/.ps1 in dist/DownloadSorter/ or the setup file lacks a valid embedded Authenticode signature.",
    )
    args = parser.parse_args(argv)

    os.chdir(ROOT)
    print("=" * 68)
    print("      Download Sorter -- Release Preflight Verification")
    print("=" * 68)

    print("\n[1/5] Version parity...")
    version_errors, version = check_versions()
    if not _report(version_errors, f"all version sources agree on v{version}"):
        return 1

    print("\n[2/5] CLI flags registered in cli.py...")
    if not _report(check_cli_flags(), f"all {len(REQUIRED_CLI_FLAGS)} flags registered"):
        return 1

    print("\n[3/5] Source smoke test (main.py --version, --simulate)...")
    source_launcher = [sys.executable, str(ROOT / "main.py")]
    if not _report(
        _smoke(source_launcher, "main.py", version, simulate=True),
        "main.py reports the version and simulates read-only",
    ):
        return 1

    print(f"\n[4/5] Test suite ({TESTS_DIR})...")
    if args.skip_tests:
        print("  [SKIP] --skip-tests")
    else:
        ok, summary, output = run_tests()
        if not ok:
            print(f"  [FAIL] pytest {TESTS_DIR}: {summary}")
            print(output)
            return 1
        print(f"  [PASS] pytest {TESTS_DIR}: {summary}")

    print("\n[5/5] Built artifacts...")
    if args.skip_artifacts:
        print("  [SKIP] --skip-artifacts")
    elif not _report(
        check_artifacts(version, require_signed=args.require_signed),
        "executables, ZIP, setup file and checksums present, matching and runnable",
    ):
        return 1

    skipped = [name for name, flag in (("tests", args.skip_tests), ("artifacts", args.skip_artifacts)) if flag]
    print("\n" + "=" * 68)
    if skipped:
        print(f"  PREFLIGHT PASSED for v{version} (skipped: {', '.join(skipped)}) -- not a full release check")
    else:
        print(f"  PREFLIGHT PASSED -- v{version} is ready to release")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    sys.exit(main())
