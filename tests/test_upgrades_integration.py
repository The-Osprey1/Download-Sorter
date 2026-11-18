"""Integration tests for wired Milestones 2, 3, and 4 upgrades in download-sorter."""

from __future__ import annotations

import json
import os
import time
import zipfile
from pathlib import Path

import pytest

import safe_ops
import sort_undo
import sorter
import unzip_3d


@pytest.fixture()
def isolated_sorter_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    downloads = tmp_path / "Downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    hist = state_dir / "history.jsonl"
    active_proj = state_dir / "active_project.json"
    dedup_db = state_dir / "dedup_index.db"

    monkeypatch.setattr(sorter, "HISTORY_FILE", hist)
    monkeypatch.setattr(sorter, "LOG_DIR", state_dir)
    monkeypatch.setattr(sorter, "ACTIVE_PROJECT_FILE", active_proj)
    sorter._DEDUP_INDEX_CACHE.clear()

    return {
        "downloads": downloads,
        "state_dir": state_dir,
        "history": hist,
        "active_project": active_proj,
        "dedup_db": dedup_db,
    }


def _base_config(env: dict[str, Path]) -> dict:
    cfg = sorter.get_default_config()
    cfg["watch"]["folder"] = str(env["downloads"])
    cfg["watch"]["settle_seconds"] = 0.0
    cfg["dedup_index_path"] = str(env["dedup_db"])
    cfg["notifications"] = False
    cfg["ribbon_enabled"] = False
    cfg["quick_switcher_enabled"] = False
    return cfg


def test_evaluate_routing_source_url_magic_fuzzy_and_zip_peek(
    isolated_sorter_env: dict[str, Path],
) -> None:
    downloads = isolated_sorter_env["downloads"]
    cfg = _base_config(isolated_sorter_env)
    cfg["keyword_rules"] = [
        {"name": "School", "folder": "Documents\\School", "match": ["homework"], "enabled": True}
    ]
    cfg["zip_content_routing"] = True

    # 1. Source URL domain routing (Live Sandbox parity)
    res_url = sorter.evaluate_routing(
        "part_export.bin",
        cfg,
        source_url="https://makerworld.com/en/models/12345",
    )
    assert res_url["match_type"] == "source_domain"
    assert res_url["destination"] == "3D Models"

    # 2. Magic-byte sniffing for extensionless PDF
    extless = downloads / "mystery_doc"
    extless.write_bytes(b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n")
    res_magic = sorter.evaluate_routing(extless.name, cfg, file_path=extless)
    assert res_magic["destination"] == "Documents\\PDFs"

    # 3. Fuzzy / alias keyword matching ('hw' -> 'homework')
    fuzzy_cfg = dict(cfg)
    fuzzy_cfg["fuzzy_keyword_matching"] = True
    res_fuzzy = sorter.evaluate_routing("ME-3210_hw1.pdf", fuzzy_cfg)
    assert res_fuzzy["match_type"] == "keyword"
    assert res_fuzzy["destination"] == "Documents\\School"

    # 4. Pre-routing ZIP peek
    zpath = downloads / "bundle.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        z.writestr("bracket.stl", "solid bracket\nendsolid bracket\n")
    res_zip = sorter.evaluate_routing(zpath.name, cfg, file_path=zpath)
    assert res_zip["match_type"] == "zip_peek"
    assert res_zip["destination"] == "3D Models"


def test_move_file_renamed_duplicate_quarantined_via_dedup_index(
    isolated_sorter_env: dict[str, Path],
) -> None:
    downloads = isolated_sorter_env["downloads"]
    cfg = _base_config(isolated_sorter_env)
    payload = b"solid cube\n" + (b"Z" * 250_000)

    first = downloads / "cube_v1.stl"
    first.write_bytes(payload)
    dest1 = sorter.move_file(first, cfg, batch_id="b1")
    assert dest1 is not None and dest1.exists()

    # Renamed duplicate arrives later
    second = downloads / "totally_different_name.stl"
    second.write_bytes(payload)
    dest2 = sorter.move_file(second, cfg, batch_id="b2")
    assert dest2 == dest1
    assert not second.exists()

    # Quarantined in _Duplicates (never permanently deleted)
    dups = list((downloads / "_Duplicates").rglob("totally_different_name.stl"))
    assert len(dups) == 1


def test_conflict_policy_keep_newest(isolated_sorter_env: dict[str, Path]) -> None:
    downloads = isolated_sorter_env["downloads"]
    cfg = _base_config(isolated_sorter_env)
    cfg["conflict_policy"] = "keep_newest"

    older = downloads / "spec.pdf"
    older.write_bytes(b"%PDF-1.4\nold")
    old_ts = time.time() - 100.0
    os.utime(older, (old_ts, old_ts))
    dest1 = sorter.move_file(older, cfg, batch_id="b1")
    assert dest1 is not None

    newer = downloads / "spec.pdf"
    newer.write_bytes(b"%PDF-1.4\nnew-version")
    dest2 = sorter.move_file(newer, cfg, batch_id="b2")
    assert dest2 == dest1
    assert dest2.read_bytes() == b"%PDF-1.4\nnew-version"


def test_unzip_3d_staging_manifest_and_undo(isolated_sorter_env: dict[str, Path]) -> None:
    downloads = isolated_sorter_env["downloads"]
    cfg = _base_config(isolated_sorter_env)
    zpath = downloads / "models_pack.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        z.writestr("a.stl", "solid a\nendsolid a\n")
        z.writestr("b.stl", "solid b\nendsolid b\n")

    extracted = unzip_3d.unpack_3d_archive(zpath, cfg, batch_id="u3d")
    assert len(extracted) == 2
    assert all(p.exists() for p in extracted)
    assert not (downloads / ".staging" / "u3d").exists()

    sorter.record_extraction(zpath, extracted, batch_id="u3d")
    records = safe_ops.read_journal()
    extract_recs = [r for r in records if r.get("action") == "extract"]
    assert len(extract_recs) == 1
    assert len(extract_recs[0]["extracted_files"]) == 2

    removed = sort_undo.undo_extraction_manifest(extract_recs[0])
    assert removed == 2
    assert not any(p.exists() for p in extracted)


def test_idle_catchup_sweep_expires_project_and_sorts_missed(
    isolated_sorter_env: dict[str, Path],
) -> None:
    downloads = isolated_sorter_env["downloads"]
    cfg = _base_config(isolated_sorter_env)
    ap_file = isolated_sorter_env["active_project"]
    ap_file.write_text(
        json.dumps({
            "enabled": True,
            "project_name": "ExpiredProj",
            "target_directory": str(downloads / "Projects" / "Old"),
            "expires_at": time.time() - 60.0,
        }),
        encoding="utf-8",
    )
    stale_stage = safe_ops.staging_dir_for(downloads, "stale_batch")
    old_ts = time.time() - 600.0
    os.utime(stale_stage, (old_ts, old_ts))

    missed = downloads / "missed_part.stl"
    missed.write_bytes(b"solid m\nendsolid m\n")

    summary = sorter.idle_catchup_sweep(cfg)
    assert summary["downloads_moved"] >= 1
    assert summary["cleaned_staging"] >= 1
    assert not stale_stage.exists()
    # Expired project must not receive the file; it should route to 3D Models\STL
    assert (downloads / "3D Models" / "STL" / "missed_part.stl").exists()
