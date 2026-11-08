"""The release must always produce the same kind of setup file.

build_setup_exe used to switch to Inno Setup whenever ISCC.exe happened to be
installed on the build machine. The two installers take different silent
options (/S versus /VERYSILENT), so the README's /S instructions only worked
for one of them. The single-file installer is now built unless Inno Setup is
asked for explicitly.
"""

from __future__ import annotations

from pathlib import Path

import installer_builder


def _fake_build(monkeypatch, tmp_path):
    calls = {"inno": 0, "single_file": 0}

    def fake_run(cmd, *args, **kwargs):
        calls["inno"] += 1
        raise AssertionError("Inno Setup must not run unless asked for")

    def fake_csharp(output_exe, **kwargs):
        calls["single_file"] += 1
        return Path(output_exe)

    monkeypatch.setattr(installer_builder, "find_iscc_compiler", lambda: tmp_path / "ISCC.exe")
    monkeypatch.setattr(installer_builder.subprocess, "run", fake_run)
    monkeypatch.setattr(installer_builder, "build_csharp_installer", fake_csharp)
    monkeypatch.setattr(installer_builder, "generate_app_icon", lambda *a, **k: None)
    return calls


def test_single_file_installer_is_built_even_when_inno_setup_is_installed(monkeypatch, tmp_path):
    calls = _fake_build(monkeypatch, tmp_path)

    out = installer_builder.build_setup_exe(dist_dir=tmp_path, version="9.9.9",
                                            output_exe=tmp_path / "setup.exe")

    assert out == tmp_path / "setup.exe"
    assert calls == {"inno": 0, "single_file": 1}


def test_single_file_installer_accepts_both_silent_spellings():
    source = Path(installer_builder.__file__).read_text(encoding="utf-8")
    for option in ('"/S"', '"/silent"', '"/verysilent"', '"/D="', '"/dir="'):
        assert option in source, f"installer does not accept {option}"
