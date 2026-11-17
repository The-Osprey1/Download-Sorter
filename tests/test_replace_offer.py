"""Offer to replace an older file when a new download has the same name."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

import replace_offer
import sort_undo
import sorter

PDF_DIR = ("Documents", "PDFs")


@pytest.fixture
def downloads(tmp_path) -> Path:
    root = tmp_path / "Downloads"
    root.mkdir()
    return root


@pytest.fixture
def config(downloads) -> dict:
    cfg = sorter.get_default_config()
    cfg["downloads_dir"] = str(downloads)
    cfg["watch"] = {"folder": str(downloads), "settle_seconds": 0, "min_age_minutes": 0}
    cfg["notifications"] = False
    cfg["ribbon_enabled"] = False
    cfg["document_sources"] = []
    cfg["hold"]["enabled"] = False
    return cfg


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch, finished_downloads):
    monkeypatch.setattr(replace_offer, "PENDING_FILE", tmp_path / "pending_replacements.json")
    recycled: list[Path] = []

    def fake_recycle(path: Path) -> bool:
        recycled.append(Path(path))
        Path(path).unlink()
        return True

    monkeypatch.setattr(replace_offer, "_recycle", fake_recycle)
    return recycled


def _make(path: Path, data: bytes, age: float = 30.0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    old = time.time() - age
    os.utime(path, (old, old))
    return path


@pytest.mark.parametrize("name,expected", [
    ("report (1).pdf", "report.pdf"),
    ("report (12).pdf", "report.pdf"),
    ("backup (2).tar.gz", "backup.tar.gz"),
    ("README (1)", "README"),
    ("report.pdf", "report.pdf"),
    ("Chapter (1) notes.pdf", "Chapter (1) notes.pdf"),
])
def test_original_name(name, expected):
    assert replace_offer.original_name(name) == expected


def test_conflict_keeps_both_and_records_offer(downloads, config):
    old = _make(downloads.joinpath(*PDF_DIR, "report.pdf"), b"%PDF old")
    new = _make(downloads / "report (1).pdf", b"%PDF new contents")

    result = sorter.move_file(new, config, check_age=False)

    assert result == downloads.joinpath(*PDF_DIR, "report (2).pdf")
    assert old.read_bytes() == b"%PDF old"
    offers = replace_offer._read()
    assert len(offers) == 1
    entry = next(iter(offers.values()))
    assert Path(entry["old"]) == old and Path(entry["new"]) == result


def test_identical_file_is_still_quarantined_without_offer(downloads, config):
    _make(downloads.joinpath(*PDF_DIR, "report.pdf"), b"%PDF same")
    dup = _make(downloads / "report (1).pdf", b"%PDF same")
    sorter.move_file(dup, config, check_age=False)
    assert list((downloads / sorter.DUPLICATES_DIR_NAME).rglob("*.pdf"))
    assert replace_offer._read() == {}


def test_browser_suffix_kept_when_no_original(downloads, config):
    new = _make(downloads / "Chapter (1).pdf", b"%PDF")
    result = sorter.move_file(new, config, check_age=False)
    assert result.name == "Chapter (1).pdf"
    assert replace_offer._read() == {}


def test_accept_replaces_and_undo_renames_back(downloads, config, isolated):
    old = _make(downloads.joinpath(*PDF_DIR, "report.pdf"), b"%PDF old")
    new = _make(downloads / "report (1).pdf", b"%PDF new contents")
    filed = sorter.move_file(new, config, check_age=False)
    offer_id = next(iter(replace_offer._read()))

    final = replace_offer.accept(offer_id)

    assert final == old
    assert old.read_bytes() == b"%PDF new contents"
    assert not filed.exists()
    assert isolated == [old]
    assert replace_offer._read() == {}

    moves = [r for r in sort_undo.get_active_moves(sort_undo.load_history()) if r.get("op") == "replace"]
    assert moves
    assert sort_undo.undo_moves(moves[-1:]) == 1
    assert filed.exists() and filed.read_bytes() == b"%PDF new contents"


def test_accept_refuses_if_file_changed(downloads, config, isolated):
    old = _make(downloads.joinpath(*PDF_DIR, "report.pdf"), b"%PDF old")
    sorter.move_file(_make(downloads / "report.pdf", b"%PDF newer"), config, check_age=False)
    offer_id = next(iter(replace_offer._read()))
    old.write_bytes(b"%PDF edited meanwhile")

    assert replace_offer.accept(offer_id) is None
    assert old.read_bytes() == b"%PDF edited meanwhile"
    assert isolated == []


def test_decline_changes_nothing(downloads, config, isolated):
    old = _make(downloads.joinpath(*PDF_DIR, "report.pdf"), b"%PDF old")
    filed = sorter.move_file(_make(downloads / "report.pdf", b"%PDF newer"), config, check_age=False)
    offer_id = next(iter(replace_offer._read()))
    replace_offer.decline(offer_id)
    assert old.exists() and filed.exists() and isolated == []
    assert replace_offer.accept(offer_id) is None


def test_setting_off_behaves_as_before(downloads, config):
    config["replace"] = {"offer": False}
    _make(downloads.joinpath(*PDF_DIR, "report.pdf"), b"%PDF old")
    result = sorter.move_file(_make(downloads / "report (1).pdf", b"%PDF new"), config, check_age=False)
    assert result.parent == downloads.joinpath(*PDF_DIR)
    assert result.name != "report.pdf"
    assert replace_offer._read() == {}


def test_ribbon_builds_headless(downloads, config):
    import tkinter as tk
    import ribbon_popup

    _make(downloads.joinpath(*PDF_DIR, "report.pdf"), b"%PDF old")
    sorter.move_file(_make(downloads / "report.pdf", b"%PDF newer"), config, check_age=False)
    offer_id = next(iter(replace_offer._read()))
    try:
        popup = ribbon_popup.ReplaceRibbonPopup(offer_id, enable_animation=False)
    except tk.TclError as exc:
        pytest.skip(f"no display: {exc}")
    try:
        assert [lbl.cget("text") for lbl in popup.action_labels] == ["Replace", "Keep both"]
        assert popup.title_lbl.cget("text") == "Replace older copy?"
    finally:
        popup.root.destroy()
