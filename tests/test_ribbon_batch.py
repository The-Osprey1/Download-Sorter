"""Ribbons show in the installed app, and a big batch gets one counting ribbon.

Since v2.1.0 the installed app started each ribbon as
``DownloadSorter.exe <path to ribbon_popup.py> <file>``. The app answered
"Unknown command" and quit, so no per-file ribbon ever appeared. Ribbons now go
through the app's own ``--ribbon`` command.

When many files land together, the first few still get their own ribbon; then a
single ribbon counts the rest ("Filed 37 files: Images 25 · Documents 12").
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ribbon_popup = pytest.importorskip("ribbon_popup")

import main
import sort_undo


@pytest.fixture
def ribbons(tmp_path, monkeypatch):
    """Record started ribbon processes and keep their files in tmp_path."""
    started: list[list[str]] = []
    monkeypatch.setattr(ribbon_popup, "_spawn", started.append)
    monkeypatch.setattr(ribbon_popup, "BATCH_FILE", tmp_path / "batch.json")
    monkeypatch.setattr(ribbon_popup, "SLOTS_FILE", tmp_path / "slots.json")
    monkeypatch.setattr(ribbon_popup, "RIBBON_DISMISS_FILE", tmp_path / "dismiss.json")
    ribbon_popup._burst.clear()
    yield started
    ribbon_popup._burst.clear()


def _file(root: Path, folder: str, name: str) -> Path:
    path = root / folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Starting ribbons in the installed app
# ---------------------------------------------------------------------------

def test_installed_app_starts_ribbons_through_its_own_command(ribbons, tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\Program Files\DownloadSorter\DownloadSorter.exe")
    dest = _file(tmp_path, "Documents", "report.pdf")

    ribbon_popup.notify_file_moved(dest, tmp_path)

    assert ribbons == [[sys.executable, "--ribbon", str(dest.resolve()), str(tmp_path.resolve())]]


def test_installed_app_starts_held_ribbons_through_its_own_command(ribbons, tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\Apps\DownloadSorter.exe")

    ribbon_popup.notify_file_held(tmp_path / "Model.3mf", tmp_path / "3D Models" / "Model.3mf",
                                  downloads_root=tmp_path)

    command = ribbons[0]
    assert command[:3] == [sys.executable, "--ribbon", str((tmp_path / "Model.3mf").resolve())]
    assert "--held" in command and "--planned" in command


def test_main_forwards_the_ribbon_command(monkeypatch):
    seen = []
    monkeypatch.setattr(ribbon_popup, "main", lambda argv=None: seen.append(argv) or 0)

    assert main.main(["--ribbon", "C:/Downloads/a.pdf", "C:/Downloads"]) == 0
    assert main.main(["--ribbon", "--batch"]) == 0

    assert seen == [["C:/Downloads/a.pdf", "C:/Downloads"], ["--batch"]]


# ---------------------------------------------------------------------------
# Big batches: a few single ribbons, then one that counts
# ---------------------------------------------------------------------------

def test_a_few_files_each_get_their_own_ribbon(ribbons, tmp_path):
    for i in range(3):
        ribbon_popup.notify_file_moved(_file(tmp_path, "Images", f"{i}.jpg"), tmp_path, batch_id="b1")

    assert len(ribbons) == 3
    assert all("--batch" not in command for command in ribbons)
    assert not ribbon_popup.BATCH_FILE.exists()


def test_a_big_batch_turns_into_one_counting_ribbon(ribbons, tmp_path):
    files = [_file(tmp_path, "Images", f"{i}.jpg") for i in range(25)]
    files += [_file(tmp_path, "Documents", f"{i}.pdf") for i in range(12)]

    for i, path in enumerate(files):
        ribbon_popup.notify_file_moved(path, tmp_path, batch_id="b1" if i < 20 else "b2")

    batch_ribbons = [command for command in ribbons if "--batch" in command]
    assert len(ribbons) == 4, "3 single ribbons, then 1 batch ribbon, however many files follow"
    assert len(batch_ribbons) == 1

    state = ribbon_popup.read_batch_state()
    assert state["count"] == 37
    assert state["folders"] == {"Images": 25, "Documents": 12}
    assert state["batches"] == ["b1", "b2"]
    assert ribbon_popup.batch_summary(state) == ("Filed 37 files", "Images 25 · Documents 12")

    dismissed = ribbon_popup.json.loads(ribbon_popup.RIBBON_DISMISS_FILE.read_text(encoding="utf-8"))
    assert dismissed["paths"] == [str(p.resolve()) for p in files[:3]], "the 3 single ribbons make way"


def test_a_pause_starts_a_new_batch(ribbons, tmp_path):
    for i in range(5):
        ribbon_popup.record_burst(_file(tmp_path, "Images", f"{i}.jpg"), tmp_path, now=100.0 + i)

    kind, state = ribbon_popup.record_burst(
        _file(tmp_path, "Images", "later.jpg"), tmp_path,
        now=104.0 + ribbon_popup.BATCH_GAP_SECONDS + 1,
    )

    assert kind == "single" and state["count"] == 1


def test_hold_releases_are_not_offered_for_undo(ribbons, tmp_path):
    for i in range(5):
        ribbon_popup.record_burst(_file(tmp_path, "3D Models", f"{i}.3mf"), tmp_path,
                                  batch_id="hold_release", now=200.0)
    assert ribbon_popup._burst["batches"] == []


@pytest.mark.parametrize("folders, expected", [
    ({"Images": 1}, ("Filed 1 file", "Images 1")),
    ({"Images": 5, "Audio": 5, "Videos": 2, "Code": 1, "Other": 1},
     ("Filed 14 files", "Audio 5 · Images 5 · Videos 2 · +2 more")),
])
def test_batch_summary_text(folders, expected):
    state = {"count": sum(folders.values()), "folders": folders}
    assert ribbon_popup.batch_summary(state) == expected


def test_folder_labels_are_relative_to_downloads(tmp_path):
    assert ribbon_popup._folder_label(tmp_path / "3D Models" / "a.stl", tmp_path) == "3D Models"
    assert ribbon_popup._folder_label(tmp_path / "a.txt", tmp_path) == "Downloads"


# ---------------------------------------------------------------------------
# Undo for the whole batch
# ---------------------------------------------------------------------------

def test_undo_accepts_several_batches(monkeypatch):
    records = [
        {"batch_id": "b1", "src": "C:/D/a.jpg", "dest": "C:/D/Images/a.jpg"},
        {"batch_id": "b2", "src": "C:/D/b.pdf", "dest": "C:/D/Documents/b.pdf"},
        {"batch_id": "b3", "src": "C:/D/c.zip", "dest": "C:/D/Archives/c.zip"},
    ]
    undone = []
    monkeypatch.setattr(sort_undo, "load_history", lambda: records)
    monkeypatch.setattr(sort_undo, "get_active_moves", lambda recs: list(recs))
    monkeypatch.setattr(sort_undo, "undo_moves", lambda targets, dry_run=False: undone.extend(targets) or len(targets))

    assert sort_undo.main(["--batch", "b1", "--batch", "b2"]) == 0

    assert sorted(r["batch_id"] for r in undone) == ["b1", "b2"]
