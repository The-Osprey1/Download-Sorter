"""Regression tests for the bugs listed in issue #24 (review of ui/polish-and-workflows)."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import clean_old
import hold_manager
import remodel
import sorter
from rules_manager import _parse_sites


def test_config_json_matches_default_layout():
    """Fresh setups seed from config.json, so its rules must match the code defaults."""
    seed = json.loads((Path(sorter.ROOT) / "config.json").read_text(encoding="utf-8"))
    defaults = sorter.DEFAULT_CONFIG
    assert seed["extension_rules"] == defaults["extension_rules"]
    assert seed["makerworld_destination"] == defaults["makerworld_destination"]
    assert seed["hold"]["slicers"] == defaults["hold"]["slicers"]
    assert "type_folders" in seed and "my_folders" in seed


def test_hold_manager_and_sorter_share_one_slicer_list():
    assert hold_manager.DEFAULT_SLICERS == sorter.DEFAULT_SLICERS
    assert "bambu-studio-worker.exe" in sorter.DEFAULT_SLICERS
    assert sorter.V2_DEFAULTS["hold"]["slicers"] == sorter.DEFAULT_SLICERS


def test_receipt_escapes_file_names(tmp_path):
    out = tmp_path / "receipt.html"
    manifest = {
        "remodel_id": "r1",
        "executed_moves": [{"src": "C:/a/<b> & c.pdf", "dest": "C:/x/y.pdf", "action": "move"}],
        "skipped_items": [{"path": "C:/p/<script>.txt", "reason": "R & D <kept>"}],
    }
    remodel.generate_html_receipt(manifest, out)
    text = out.read_text(encoding="utf-8")
    assert "C:/a/&lt;b&gt; &amp; c.pdf" in text
    assert "&lt;script&gt;.txt" in text
    assert "<b> &" not in text


def test_parse_sites_keeps_bare_domains():
    assert _parse_sites("https://www.MakerWorld.com/models/1, printables.com ,, yourbank.com:443") == [
        "makerworld.com", "printables.com", "yourbank.com",
    ]
    assert _parse_sites("  ") == []


def test_find_old_files_looks_into_subfolders_and_uses_arrival(tmp_path):
    root = tmp_path / "Installers"
    deep = root / "Windows"
    deep.mkdir(parents=True)
    old = deep / "setup.exe"
    old.write_text("x", encoding="utf-8")
    past = time.time() - 40 * 86400
    os.utime(old, (past, past))
    fresh = root / "new.exe"
    fresh.write_text("y", encoding="utf-8")
    found = {p.name for p, _age, _size in clean_old.find_old_files(root, 30)}
    assert found == {"setup.exe"}


def test_forget_restored_drops_only_that_file(tmp_path, monkeypatch):
    monkeypatch.setattr(sorter, "LOG_DIR", str(tmp_path))
    a = tmp_path / "a.pdf"
    b = tmp_path / "b.pdf"
    a.write_text("a", encoding="utf-8")
    b.write_text("b", encoding="utf-8")
    sorter.remember_restored(a)
    sorter.remember_restored(b)
    sorter.forget_restored(a)
    assert not sorter.is_restored_by_undo(a)
    assert sorter.is_restored_by_undo(b)


def test_quarantine_batches_past_window_go_to_recycle_bin(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sorter, "LOG_DIR", str(tmp_path / "log"))
    binned: list[str] = []
    monkeypatch.setattr(clean_old, "send_to_recycle_bin", lambda p: binned.append(p.name) or True)

    downloads = tmp_path / "Downloads"
    old_batch = downloads / "_Remodel Quarantine" / "old"
    new_batch = downloads / "_Remodel Quarantine" / "new"
    old_batch.mkdir(parents=True)
    new_batch.mkdir(parents=True)
    past = time.time() - 45 * 86400
    os.utime(old_batch, (past, past))

    cfg = sorter.get_default_config()
    cfg["downloads_dir"] = str(downloads)
    cfg["watch"] = {"folder": str(downloads)}
    cfg["remodel"] = {"enabled": True, "quarantine_days": 30}

    assert clean_old.expire_quarantine(cfg) == 1
    assert binned == ["old"]
    # Once a day: a second call straight away does nothing.
    assert clean_old.expire_quarantine(cfg) == 0
