"""Offer to replace an older file when a new download has the same name."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

import replace_offer
import safe_ops
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
    monkeypatch.setattr(replace_offer, "PENDING_DIR", tmp_path / "pending_replacements")


def _make(path: Path, data: bytes, age: float = 30.0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    old = time.time() - age
    os.utime(path, (old, old))
    return path


def _offer_ids() -> list[str]:
    if not replace_offer.PENDING_DIR.exists():
        return []
    return [p.stem for p in replace_offer.PENDING_DIR.glob("*.json")]


@pytest.mark.parametrize("name,expected", [
    ("report (1).pdf", "report.pdf"),
    ("report (12).pdf", "report.pdf"),
    ("report(1).pdf", "report.pdf"),
    ("backup (2).tar.gz", "backup.tar.gz"),
    ("README (1)", "README"),
    ("report.pdf", "report.pdf"),
    ("Tax Return (2024).pdf", "Tax Return (2024).pdf"),
    ("Movie (1999).mkv", "Movie (1999).mkv"),
    ("report (0).pdf", "report (0).pdf"),
    ("report (01).pdf", "report (01).pdf"),
    ("notes (1).final.pdf", "notes (1).final.pdf"),
    ("Chapter (1) notes.pdf", "Chapter (1) notes.pdf"),
])
def test_original_name(name, expected):
    assert replace_offer.original_name(name) == expected


def test_conflict_keeps_both_and_records_offer(downloads, config):
    old = _make(downloads.joinpath(*PDF_DIR, "report.pdf"), b"%PDF old")
    new = _make(downloads / "report (1).pdf", b"%PDF new contents")

    result = sorter.move_file(new, config, check_age=False)

    assert result == downloads.joinpath(*PDF_DIR, "report (1).pdf")
    assert old.read_bytes() == b"%PDF old"
    ids = _offer_ids()
    assert len(ids) == 1
    entry = replace_offer.pending_offer(ids[0])
    assert Path(entry["old"]) == old and Path(entry["new"]) == result


def test_identical_file_is_still_quarantined_without_offer(downloads, config):
    _make(downloads.joinpath(*PDF_DIR, "report.pdf"), b"%PDF same")
    dup = _make(downloads / "report (1).pdf", b"%PDF same")
    sorter.move_file(dup, config, check_age=False)
    assert list((downloads / sorter.DUPLICATES_DIR_NAME).rglob("*.pdf"))
    assert _offer_ids() == []


def test_browser_suffix_kept_when_no_original(downloads, config):
    new = _make(downloads / "Chapter (1).pdf", b"%PDF")
    result = sorter.move_file(new, config, check_age=False)
    assert result.name == "Chapter (1).pdf"
    assert _offer_ids() == []


def test_accept_quarantines_old_and_undo_restores_both(downloads, config):
    old = _make(downloads.joinpath(*PDF_DIR, "report.pdf"), b"%PDF old")
    new = _make(downloads / "report (1).pdf", b"%PDF new contents")
    filed = sorter.move_file(new, config, batch_id="b1", check_age=False)
    offer_id = _offer_ids()[0]

    final, status = replace_offer.accept(offer_id)

    assert status == "replaced"
    assert final == old
    assert old.read_bytes() == b"%PDF new contents"
    assert not filed.exists()
    assert list((downloads / sorter.DUPLICATES_DIR_NAME).rglob("report.pdf"))
    assert _offer_ids() == []

    moves = [r for r in sort_undo.get_active_moves(sort_undo.load_history()) if r.get("batch_id") == "b1"]
    assert len(moves) >= 2
    assert sort_undo.undo_moves(list(reversed(moves))) >= 2
    assert old.read_bytes() == b"%PDF old"
    assert new.read_bytes() == b"%PDF new contents"


def test_accept_refuses_if_file_changed(downloads, config):
    old = _make(downloads.joinpath(*PDF_DIR, "report.pdf"), b"%PDF old")
    sorter.move_file(_make(downloads / "report.pdf", b"%PDF newer"), config, check_age=False)
    offer_id = _offer_ids()[0]
    old.write_bytes(b"%PDF edited meanwhile")

    final, status = replace_offer.accept(offer_id)
    assert final is None and status == "changed"
    assert old.read_bytes() == b"%PDF edited meanwhile"


def test_accept_keeps_offer_when_locked(downloads, config, monkeypatch):
    old = _make(downloads.joinpath(*PDF_DIR, "report.pdf"), b"%PDF old")
    sorter.move_file(_make(downloads / "report.pdf", b"%PDF newer"), config, check_age=False)
    offer_id = _offer_ids()[0]
    monkeypatch.setattr(safe_ops, "can_open_exclusive", lambda p: False)

    final, status = replace_offer.accept(offer_id)
    assert final is None and status == "locked"
    assert replace_offer.pending_offer(offer_id) is not None
    assert old.read_bytes() == b"%PDF old"


def test_accept_reports_quarantine_failure(downloads, config, monkeypatch):
    old = _make(downloads.joinpath(*PDF_DIR, "report.pdf"), b"%PDF old")
    sorter.move_file(_make(downloads / "report.pdf", b"%PDF newer"), config, check_age=False)
    offer_id = _offer_ids()[0]
    monkeypatch.setattr(safe_ops, "quarantine", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))

    final, status = replace_offer.accept(offer_id)
    assert final is None and status == "quarantine_failed"
    assert old.read_bytes() == b"%PDF old"


def test_accept_stops_if_old_still_exists_after_quarantine(downloads, config, monkeypatch):
    old = _make(downloads.joinpath(*PDF_DIR, "report.pdf"), b"%PDF old")
    filed = sorter.move_file(_make(downloads / "report.pdf", b"%PDF newer"), config, check_age=False)
    offer_id = _offer_ids()[0]
    monkeypatch.setattr(safe_ops, "quarantine", lambda *a, **k: old)

    final, status = replace_offer.accept(offer_id)
    assert final is None and status == "still_exists"
    assert old.exists() and filed.exists()


def test_accept_reports_rename_failure(downloads, config, monkeypatch):
    _make(downloads.joinpath(*PDF_DIR, "report.pdf"), b"%PDF old")
    sorter.move_file(_make(downloads / "report.pdf", b"%PDF newer"), config, check_age=False)
    offer_id = _offer_ids()[0]
    orig_move = safe_ops.move
    def fail_replace(*a, **k):
        if k.get("op") == "replace":
            raise OSError("denied")
        return orig_move(*a, **k)
    monkeypatch.setattr(safe_ops, "move", fail_replace)

    final, status = replace_offer.accept(offer_id)
    assert final is None and status == "rename_failed"


def test_concurrent_offers_do_not_overwrite_each_other(downloads, config):
    a_old = _make(downloads.joinpath(*PDF_DIR, "a.pdf"), b"a1")
    a_new = _make(downloads / "a (1).pdf", b"a2")
    b_old = _make(downloads.joinpath(*PDF_DIR, "b.pdf"), b"b1")
    b_new = _make(downloads / "b (1).pdf", b"b2")

    id1 = replace_offer.record_offer(a_new, a_old, downloads_root=downloads)
    id2 = replace_offer.record_offer(b_new, b_old, downloads_root=downloads)

    assert len(_offer_ids()) == 2
    replace_offer.decline(id1)
    assert replace_offer.pending_offer(id2) is not None


def test_decline_changes_nothing(downloads, config):
    old = _make(downloads.joinpath(*PDF_DIR, "report.pdf"), b"%PDF old")
    filed = sorter.move_file(_make(downloads / "report.pdf", b"%PDF newer"), config, check_age=False)
    offer_id = _offer_ids()[0]
    replace_offer.decline(offer_id)
    assert old.exists() and filed.exists()
    final, status = replace_offer.accept(offer_id)
    assert final is None and status == "missing"


def test_setting_off_behaves_as_before(downloads, config):
    config["replace"] = {"offer": False}
    _make(downloads.joinpath(*PDF_DIR, "report.pdf"), b"%PDF old")
    result = sorter.move_file(_make(downloads / "report (1).pdf", b"%PDF new"), config, check_age=False)
    assert result.parent == downloads.joinpath(*PDF_DIR)
    assert result.name != "report.pdf"
    assert _offer_ids() == []


def test_ribbon_builds_headless(downloads, config):
    tk = pytest.importorskip("tkinter")
    import ribbon_popup

    _make(downloads.joinpath(*PDF_DIR, "report.pdf"), b"%PDF old")
    sorter.move_file(_make(downloads / "report.pdf", b"%PDF newer"), config, check_age=False)
    offer_id = _offer_ids()[0]
    try:
        popup = ribbon_popup.ReplaceRibbonPopup(offer_id, enable_animation=False)
    except tk.TclError as exc:
        pytest.skip(f"no display: {exc}")
    try:
        assert [lbl.cget("text") for lbl in popup.action_labels] == ["Replace", "Keep both"]
        assert popup.title_lbl.cget("text") == "Replace older copy?"
    finally:
        popup.root.destroy()
