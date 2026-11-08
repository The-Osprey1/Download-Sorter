"""Release notes come from the changelog section of the version being released."""

from __future__ import annotations

from pathlib import Path

import pytest

import about
import release_notes

CHANGELOG = """# Changelog

## [2.1.0] - 2026-11-08

### Added
* New thing.

---

## [2.0.2] - 2026-11-04

### Fixed
* Old thing.
"""


def test_takes_only_the_requested_section():
    body = release_notes.changelog_section(CHANGELOG, "2.1.0")
    assert "New thing." in body
    assert "Old thing." not in body
    assert not body.endswith("---")


def test_last_section_works_too():
    assert release_notes.changelog_section(CHANGELOG, "2.0.2").endswith("Old thing.")


def test_missing_version_is_an_error():
    with pytest.raises(ValueError):
        release_notes.changelog_section(CHANGELOG, "9.9.9")


def test_notes_start_with_install_steps():
    notes = release_notes.build_notes("2.1.0", CHANGELOG)
    assert notes.startswith("## Install")
    assert "DownloadSorter-Setup-v2.1.0-windows-x64.exe" in notes
    assert "Run anyway" in notes
    assert "## What's new" in notes and "New thing." in notes


def test_the_real_changelog_has_notes_for_this_version():
    """Fails when the version was bumped without a changelog section."""
    text = (Path(release_notes.ROOT) / "CHANGELOG.md").read_text(encoding="utf-8")
    assert release_notes.changelog_section(text, about.__version__)


def test_command_line(capsys):
    assert release_notes.main([about.__version__]) == 0
    assert "## What's new" in capsys.readouterr().out
    assert release_notes.main(["0.0.1"]) == 1


def test_writes_utf8_file(tmp_path):
    target = tmp_path / "out" / "notes.md"
    assert release_notes.main([about.__version__, "--output", str(target)]) == 0
    assert target.read_text(encoding="utf-8").startswith("## Install")
