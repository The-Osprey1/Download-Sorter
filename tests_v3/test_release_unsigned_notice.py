"""An unsigned release must say so: in its release notes and on the build's summary page."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent

CHANGELOG = """\
# Changelog

## [Unreleased]

## [9.9.9] - 2026-11-16

### Added
- Something new.

## [9.9.8] - 2026-11-15

### Fixed
- Something old.
"""


def _release_notes():
    spec = importlib.util.spec_from_file_location("release_notes", ROOT / "release_notes.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["release_notes"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_notes_warn_when_unsigned_by_default() -> None:
    notes = _release_notes().build_notes("9.9.9", CHANGELOG)
    assert notes.startswith("> **This version is not code-signed.**")
    assert "Smart App Control" in notes
    assert "#smart-app-control" in notes
    assert "(the app isn't code-signed yet)" in notes
    assert "Something new." in notes and "Something old." not in notes


def test_notes_drop_the_warning_when_signed() -> None:
    notes = _release_notes().build_notes("9.9.9", CHANGELOG, signed=True)
    assert notes.startswith("## Install")
    assert "not code-signed" not in notes
    assert "isn't code-signed" not in notes
    assert "DownloadSorter-Setup-v9.9.9-windows-x64.exe" in notes
    assert "Something new." in notes


def test_command_line_signed_flag(tmp_path: Path, monkeypatch) -> None:
    mod = _release_notes()
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    (tmp_path / "CHANGELOG.md").write_text(CHANGELOG, encoding="utf-8")

    unsigned = tmp_path / "unsigned.md"
    assert mod.main(["9.9.9", "--output", str(unsigned)]) == 0
    assert "This version is not code-signed" in unsigned.read_text(encoding="utf-8")

    signed = tmp_path / "signed.md"
    assert mod.main(["--signed", "9.9.9", "--output", str(signed)]) == 0
    assert "not code-signed" not in signed.read_text(encoding="utf-8")


def test_workflow_warns_and_passes_signed_only_when_signpath_is_set_up() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

    warn_at = workflow.index("name: Warn that this build will not be code-signed")
    warn_step = workflow[warn_at:workflow.index("- name:", warn_at + 1)]
    assert "if: env.SIGNPATH_ORGANIZATION_ID == ''" in warn_step
    assert "::warning title=Release is not code-signed::" in warn_step
    assert "GITHUB_STEP_SUMMARY" in warn_step
    assert warn_at < workflow.index("name: Checkout code")

    notes_at = workflow.index("name: Write the release notes from the changelog")
    notes_step = workflow[notes_at:workflow.index("- name:", notes_at + 1)]
    assert "if ($env:SIGNPATH_ORGANIZATION_ID) { $notesArgs += \"--signed\" }" in notes_step
    assert "release_notes.py @notesArgs" in notes_step


def test_readme_has_a_code_signing_policy_without_claiming_signing_yet() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    start = readme.index("## Code signing policy")
    section = readme[start:readme.index("\n## ", start + 1)]
    assert "Releases are not code-signed yet." in section
    assert "Team roles" in section and "Approvers" in section
    assert "(#privacy)" in section
    # The SignPath attribution is only shown once the project is approved.
    visible = section.split("<!--")[0] + section.split("-->")[-1]
    assert "Free code signing provided by" not in visible
