"""Tests for two-stage release packaging, unsigned file detection, and signing workflow checks."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load_root_module(name: str):
    path = ROOT / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _fake_runner(sig_by_name: dict[str, tuple[str, str, str]]):
    """Build a fake subprocess runner mimicking PowerShell Get-AuthenticodeSignature JSON.

    Each entry maps basename -> ``(status, signature_type, signer_subject)``.
    """

    def _run(cmd, **kwargs):
        ps_cmd = cmd[-1]
        marker = "Get-Content -LiteralPath '"
        start = ps_cmd.index(marker) + len(marker)
        end = ps_cmd.index("' -Encoding UTF8", start)
        list_file = Path(ps_cmd[start:end].replace("''", "'"))
        paths = [line for line in list_file.read_text(encoding="utf-8").splitlines() if line.strip()]
        records = []
        for p_str in paths:
            name = Path(p_str).name
            status, sig_type, signer = sig_by_name.get(name, ("NotSigned", "None", ""))
            records.append(
                {
                    "path": p_str,
                    "status": status,
                    "signature_type": sig_type,
                    "signer": signer,
                }
            )
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(records), stderr="")

    return _run


def test_find_signable_files_and_require_embedded_authenticode(tmp_path: Path) -> None:
    pe_signing = _load_root_module("pe_signing")

    app_dir = tmp_path / "DownloadSorter"
    (app_dir / "_internal" / "PySide6").mkdir(parents=True)
    (app_dir / "DownloadSorter.exe").write_bytes(b"exe")
    (app_dir / "DownloadSorter-cli.exe").write_bytes(b"cli")
    (app_dir / "uninstall.ps1").write_text("Write-Host 'uninstall'", encoding="utf-8")
    (app_dir / "_internal" / "python312.dll").write_bytes(b"dll")
    (app_dir / "_internal" / "PySide6" / "QtCore.pyd").write_bytes(b"pyd")
    (app_dir / "_internal" / "PySide6" / "MSVCP140.dll").write_bytes(b"catalog-dll")
    (app_dir / "_internal" / "custom.pyd").write_bytes(b"pyd")
    (app_dir / "README.md").write_text("ignore", encoding="utf-8")

    found = pe_signing.find_signable_files(app_dir)
    assert [p.relative_to(app_dir).as_posix() for p in found] == [
        "_internal/custom.pyd",
        "_internal/PySide6/MSVCP140.dll",
        "_internal/PySide6/QtCore.pyd",
        "_internal/python312.dll",
        "DownloadSorter-cli.exe",
        "DownloadSorter.exe",
        "uninstall.ps1",
    ]

    # Catalog-only signature (Valid + Catalog) must NOT count as signed!
    runner = _fake_runner(
        {
            "python312.dll": ("Valid", "Authenticode", "CN=Python Software Foundation"),
            "QtCore.pyd": ("Valid", "Authenticode", "CN=The QT Company Oy"),
            "MSVCP140.dll": ("Valid", "Catalog", "CN=Microsoft Windows"),
            "custom.pyd": ("NotSigned", "None", ""),
            "DownloadSorter.exe": ("NotSigned", "None", ""),
            "DownloadSorter-cli.exe": ("HashMismatch", "Authenticode", "CN=Stale"),
            "uninstall.ps1": ("NotSigned", "None", ""),
        }
    )

    signed, unsigned = pe_signing.partition_pe_signatures(app_dir, runner=runner)
    assert [s.rel_path for s in signed] == ["_internal/PySide6/QtCore.pyd", "_internal/python312.dll"]
    assert [(u.rel_path, u.status, u.signature_type) for u in unsigned] == [
        ("_internal/custom.pyd", "NotSigned", "None"),
        ("_internal/PySide6/MSVCP140.dll", "Valid", "Catalog"),
        ("DownloadSorter-cli.exe", "HashMismatch", "Authenticode"),
        ("DownloadSorter.exe", "NotSigned", "None"),
        ("uninstall.ps1", "NotSigned", "None"),
    ]


def test_list_json_includes_signers_and_handles_single_dict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pe_signing = _load_root_module("pe_signing")

    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    assert pe_signing.inspect_pe_signatures(empty_dir) == []

    single_dir = tmp_path / "single"
    single_dir.mkdir()
    exe = single_dir / "only.exe"
    exe.write_bytes(b"mz")

    def _single_dict_runner(cmd, **kwargs):
        payload = {
            "path": {"value": str(exe)},
            "status": "Valid",
            "signature_type": "Authenticode",
            "signer": "CN=Test Publisher",
        }
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")

    infos = pe_signing.inspect_pe_signatures(single_dir, runner=_single_dict_runner)
    assert len(infos) == 1
    assert infos[0].is_valid
    assert infos[0].signature_type == "Authenticode"
    assert infos[0].signer == "CN=Test Publisher"

    # Also test `pe_signing.py list --json` CLI payload includes each signed file's signer
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "a.dll").write_bytes(b"a")
    (app_dir / "b.dll").write_bytes(b"b")
    (app_dir / "u.ps1").write_text("Write-Host 'hi'", encoding="utf-8")

    monkeypatch.setattr(
        pe_signing.subprocess,
        "run",
        _fake_runner(
            {
                "a.dll": ("Valid", "Authenticode", "CN=Python Software Foundation"),
                "b.dll": ("Valid", "Catalog", "CN=Microsoft Windows"),
                "u.ps1": ("NotSigned", "None", ""),
            }
        ),
    )
    assert pe_signing.main(["list", str(app_dir), "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["signed_count"] == 1
    assert out["unsigned_count"] == 2
    assert out["signers"] == {"CN=Python Software Foundation": 1}
    assert out["signed"] == [
        {
            "rel_path": "a.dll",
            "status": "Valid",
            "signature_type": "Authenticode",
            "signer": "CN=Python Software Foundation",
        }
    ]
    assert [u["rel_path"] for u in out["unsigned"]] == ["b.dll", "u.ps1"]


@pytest.mark.skipif(sys.platform != "win32", reason="Authenticode relies on Windows PowerShell")
def test_real_windows_authenticode_signature_check(tmp_path: Path) -> None:
    pe_signing = _load_root_module("pe_signing")

    unsigned_exe = tmp_path / "unsigned_dummy.exe"
    unsigned_exe.write_bytes(b"MZ" + b"\x00" * 128)
    unsigned_ps1 = tmp_path / "uninstall.ps1"
    unsigned_ps1.write_text("Write-Output 'test'", encoding="utf-8")
    # kernel32.dll is catalog-signed on Windows (Valid + Catalog -> is_valid is False!)
    win_dll = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "kernel32.dll"
    # Python executable / stdlib DLLs carry embedded Authenticode signatures
    py_exe = Path(sys.executable).resolve()

    infos = pe_signing.inspect_pe_signatures(tmp_path, paths=[unsigned_exe, unsigned_ps1, win_dll, py_exe])
    assert len(infos) == 4
    assert infos[0].path == unsigned_exe and not infos[0].is_valid
    assert infos[1].path == unsigned_ps1 and not infos[1].is_valid
    assert infos[2].path == win_dll and infos[2].status == "Valid" and infos[2].signature_type == "Catalog"
    assert not infos[2].is_valid
    assert infos[3].path == py_exe and infos[3].status == "Valid"


def test_stage_and_restore_includes_ps1_and_catalog_dlls_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pe_signing = _load_root_module("pe_signing")

    app_dir = tmp_path / "DownloadSorter"
    (app_dir / "_internal").mkdir(parents=True)
    pub_dll = app_dir / "_internal" / "python312.dll"
    pub_dll.write_bytes(b"ORIGINAL_EMBEDDED_DLL")
    cat_dll = app_dir / "_internal" / "MSVCP140.dll"
    cat_dll.write_bytes(b"CATALOG_ONLY_DLL")
    app_exe = app_dir / "DownloadSorter.exe"
    app_exe.write_bytes(b"UNSIGNED_EXE")
    sub_pyd = app_dir / "_internal" / "extra.pyd"
    sub_pyd.write_bytes(b"UNSIGNED_PYD")
    uninstall_ps1 = app_dir / "uninstall.ps1"
    uninstall_ps1.write_text("Write-Output 'bye'", encoding="utf-8")

    runner = _fake_runner(
        {
            "python312.dll": ("Valid", "Authenticode", "CN=Python Software Foundation"),
            "MSVCP140.dll": ("Valid", "Catalog", "CN=Microsoft Windows"),
            "DownloadSorter.exe": ("NotSigned", "None", ""),
            "extra.pyd": ("NotSigned", "None", ""),
            "uninstall.ps1": ("NotSigned", "None", ""),
        }
    )

    stage_dir = tmp_path / "unsigned-app"
    signed, unsigned = pe_signing.stage_unsigned_files(app_dir, stage_dir, runner=runner)
    assert len(signed) == 1
    assert len(unsigned) == 4
    assert not (stage_dir / "_internal" / "python312.dll").exists()
    assert (stage_dir / "_internal" / "MSVCP140.dll").read_bytes() == b"CATALOG_ONLY_DLL"
    assert (stage_dir / "DownloadSorter.exe").read_bytes() == b"UNSIGNED_EXE"
    assert (stage_dir / "_internal" / "extra.pyd").read_bytes() == b"UNSIGNED_PYD"
    assert (stage_dir / "uninstall.ps1").read_text(encoding="utf-8") == "Write-Output 'bye'"

    # Simulate SignPath returning signed versions of staged files
    signed_dir = tmp_path / "signed-app"
    shutil.copytree(stage_dir, signed_dir)
    (signed_dir / "DownloadSorter.exe").write_bytes(b"SIGNED_EXE")
    (signed_dir / "_internal" / "extra.pyd").write_bytes(b"SIGNED_PYD")
    (signed_dir / "_internal" / "MSVCP140.dll").write_bytes(b"SIGNED_RUNTIME_DLL")
    (signed_dir / "uninstall.ps1").write_text("Write-Output 'bye'\n# SIG # signed", encoding="utf-8")

    restored = pe_signing.restore_signed_files(signed_dir, app_dir)
    assert sorted(restored) == [
        "DownloadSorter.exe",
        "_internal/MSVCP140.dll",
        "_internal/extra.pyd",
        "uninstall.ps1",
    ]
    assert app_exe.read_bytes() == b"SIGNED_EXE"
    assert sub_pyd.read_bytes() == b"SIGNED_PYD"
    assert cat_dll.read_bytes() == b"SIGNED_RUNTIME_DLL"
    assert "# SIG # signed" in uninstall_ps1.read_text(encoding="utf-8")
    # Embedded publisher-signed file must remain untouched
    assert pub_dll.read_bytes() == b"ORIGINAL_EMBEDDED_DLL"

    gh_out = tmp_path / "gh_output.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(gh_out))
    pe_signing._write_github_output(len(unsigned))
    assert gh_out.read_text(encoding="utf-8") == "count=4\n"


def test_build_release_stage_split(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    build_release = _load_root_module("build_release")

    dist_dir = tmp_path / "dist"
    release_dir = dist_dir / "DownloadSorter"
    monkeypatch.setattr(build_release, "DIST_DIR", dist_dir)
    monkeypatch.setattr(build_release, "RELEASE_DIR", release_dir)
    monkeypatch.setattr(build_release.installer_builder, "generate_app_icon", lambda p: p)

    assert build_release.build_standalone(stage="package") == 1

    def _fake_pyinstaller(cmd, cwd=None):
        release_dir.mkdir(parents=True, exist_ok=True)
        (release_dir / "DownloadSorter.exe").write_bytes(b"APP_EXE_V1")
        (release_dir / "DownloadSorter-cli.exe").write_bytes(b"CLI_EXE_V1")
        (release_dir / "_internal").mkdir(exist_ok=True)
        (release_dir / "_internal" / "sample.dll").write_bytes(b"DLL_V1")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(build_release.subprocess, "run", _fake_pyinstaller)
    monkeypatch.setattr("version_resource.write", lambda *a, **k: None)

    def _fake_build_setup(*, dist_dir, version, output_exe, use_inno=False):
        marker = (Path(dist_dir) / "DownloadSorter.exe").read_bytes()
        Path(output_exe).write_bytes(b"SETUP|" + marker)
        return Path(output_exe)

    monkeypatch.setattr(build_release.installer_builder, "build_setup_exe", _fake_build_setup)

    assert build_release.build_standalone(stage="app") == 0
    assert (release_dir / "DownloadSorter.exe").is_file()
    assert (release_dir / "DownloadSorter.cmd").is_file()
    assert (release_dir / "uninstall.ps1").is_file()
    assert not (dist_dir / build_release.ZIP_NAME).exists()
    assert not (dist_dir / build_release.SETUP_NAME).exists()

    (release_dir / "DownloadSorter.exe").write_bytes(b"APP_EXE_SIGNED")

    assert build_release.build_standalone(stage="package") == 0
    zip_file = dist_dir / build_release.ZIP_NAME
    setup_file = dist_dir / build_release.SETUP_NAME
    checksums = dist_dir / "checksums.txt"
    assert zip_file.is_file()
    assert setup_file.read_bytes() == b"SETUP|APP_EXE_SIGNED"
    assert checksums.is_file()
    with zipfile.ZipFile(zip_file, "r") as zf:
        assert zf.read("DownloadSorter/DownloadSorter.exe") == b"APP_EXE_SIGNED"


def test_verify_release_reports_and_enforces_embedded_signatures_and_ps1(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    verify = _load_root_module("verify_release")
    version = verify.collect_versions()["about.py"]
    assert version is not None

    dist = tmp_path / "dist"
    app_dir = dist / "DownloadSorter"
    (app_dir / "_internal").mkdir(parents=True)
    (app_dir / "DownloadSorter.exe").write_bytes(b"gui")
    (app_dir / "DownloadSorter-cli.exe").write_bytes(b"cli")
    (app_dir / "uninstall.ps1").write_text("Write-Host 'uninstall'", encoding="utf-8")
    (app_dir / "_internal" / "python312.dll").write_bytes(b"py")
    (app_dir / "_internal" / "MSVCP140.dll").write_bytes(b"vc")

    zip_file = dist / f"DownloadSorter-v{version}-windows-x64.zip"
    zip_file.write_bytes(b"zip-bytes")
    setup_file = dist / f"DownloadSorter-Setup-v{version}-windows-x64.exe"
    setup_file.write_bytes(b"setup-bytes")
    (dist / "checksums.txt").write_text(
        f"{verify._sha256(setup_file)}  {setup_file.name}\n"
        f"{verify._sha256(zip_file)}  {zip_file.name}\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(verify, "DIST", dist)
    monkeypatch.setattr(verify, "_smoke", lambda *a, **k: [])

    mixed_runner = _fake_runner(
        {
            "python312.dll": ("Valid", "Authenticode", "CN=Python Software Foundation"),
            "MSVCP140.dll": ("Valid", "Catalog", "CN=Microsoft Windows"),
            "DownloadSorter.exe": ("NotSigned", "None", ""),
            "DownloadSorter-cli.exe": ("NotSigned", "None", ""),
            "uninstall.ps1": ("NotSigned", "None", ""),
            setup_file.name: ("NotSigned", "None", ""),
        }
    )

    assert verify.check_artifacts(version, require_signed=False, pe_runner=mixed_runner) == []

    errors = verify.check_artifacts(version, require_signed=True, pe_runner=mixed_runner)
    joined = "\n".join(errors)
    assert "DownloadSorter.exe" in joined
    assert "DownloadSorter-cli.exe" in joined
    assert "uninstall.ps1" in joined
    assert "MSVCP140.dll" in joined
    assert "Catalog" in joined
    assert setup_file.name in joined
    assert "python312.dll" not in joined

    all_signed_runner = _fake_runner(
        {
            "python312.dll": ("Valid", "Authenticode", "CN=Python Software Foundation"),
            "MSVCP140.dll": ("Valid", "Authenticode", "CN=SignPath Foundation"),
            "DownloadSorter.exe": ("Valid", "Authenticode", "CN=SignPath Foundation"),
            "DownloadSorter-cli.exe": ("Valid", "Authenticode", "CN=SignPath Foundation"),
            "uninstall.ps1": ("Valid", "Authenticode", "CN=SignPath Foundation"),
            setup_file.name: ("Valid", "Authenticode", "CN=SignPath Foundation"),
        }
    )
    assert verify.check_artifacts(version, require_signed=True, pe_runner=all_signed_runner) == []


def test_signpath_artifact_config_and_release_workflow_order() -> None:
    xml_path = ROOT / "packaging" / "signpath-artifact-config.xml"
    assert xml_path.is_file()
    tree = ET.parse(xml_path)
    root = tree.getroot()
    text = xml_path.read_text(encoding="utf-8")
    assert root.tag.endswith("artifact-configuration")
    assert "<pe-file-set>" in text
    assert "<powershell-file-set>" in text
    assert "*.exe" in text and "*.dll" in text and "*.pyd" in text and "*.ps1" in text
    assert "authenticode-sign" in text

    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    idx_app = workflow.index("python build_release.py --stage app")
    idx_stage = workflow.index("python pe_signing.py stage dist/DownloadSorter dist/unsigned-app")
    idx_sign_app = workflow.index("name: Sign app executables, libraries, and scripts with SignPath")
    idx_restore = workflow.index("python pe_signing.py restore dist/signed-app dist/DownloadSorter")
    idx_pkg = workflow.index("python build_release.py --stage package")
    idx_sign_setup = workflow.index("name: Sign the setup file with SignPath")
    idx_req_signed = workflow.index("python verify_release.py --skip-tests --require-signed")
    idx_update_check = workflow.index("name: Check that an update replaces a running copy and opens it again")

    assert idx_app < idx_stage < idx_sign_app < idx_restore < idx_pkg < idx_sign_setup < idx_req_signed < idx_update_check
