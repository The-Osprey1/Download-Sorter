"""Tests for new Chrome bridge, auto-unzip, PDF content sniffing, Detox, learning, and Quick Search features."""

from __future__ import annotations

import os
from pathlib import Path
import time
import zipfile
import zlib

from download_sorter import chrome_bridge
from download_sorter.config import Config, Rule, save_config
from download_sorter.db import StateDB
from download_sorter.fs_ops import extract_zip_safely, prune_empty_folders, scan_detox_candidates
from download_sorter.learning import dismiss_suggestion, get_rule_suggestions, record_manual_route
from download_sorter.paths import AppPaths
from download_sorter.router import FileCandidate, evaluate, extract_pdf_text_preview
from download_sorter.service import SorterService
from download_sorter.ui.quick_search_palette import build_search_index, filter_search_entries


def test_mime_repair_duplicate_check_reroute_and_test_tab(app_paths: AppPaths) -> None:
    cfg = Config.with_starter_defaults()
    cfg.rules.insert(
        0,
        Rule(
            id="gh",
            name="GitHub",
            destination="Dev/GitHub",
            domains=["github.com"],
        ),
    )
    save_config(cfg, app_paths)

    # MIME extension repair on extensionless / .jfif downloads
    repaired_resp = chrome_bridge.route_chrome_download(
        {
            "filename": "avatar.jfif",
            "mime": "image/jpeg",
            "finalUrl": "https://cdn.example/avatar.jfif",
        },
        app_paths,
    )
    assert repaired_resp["ok"] is True
    assert repaired_resp["repaired_filename"] == "avatar.jpg"

    # test_tab dry-run preview
    tab_resp = chrome_bridge.handle_native_request(
        {
            "action": "test_tab",
            "tabUrl": "https://github.com/devyn/repo",
            "tabTitle": "Repo",
        },
        app_paths,
    )
    assert tab_resp["ok"] is True
    assert tab_resp["host"] == "github.com"
    assert tab_resp["matched_rule"] == "GitHub"

    # Record a completed file, then check_duplicate and reroute_file
    dl_file = app_paths.downloads_dir / "Documents" / "PDFs" / "invoice.pdf"
    dl_file.parent.mkdir(parents=True, exist_ok=True)
    dl_file.write_bytes(b"%PDF-1.4 sample")

    chrome_bridge.record_chrome_completed(
        {
            "filename": "invoice.pdf",
            "full_path": str(dl_file),
            "source_url": "https://billing.example/invoice.pdf",
            "file_size": dl_file.stat().st_size,
        },
        app_paths,
        try_ipc=False,
    )

    dup = chrome_bridge.check_chrome_duplicate(
        {
            "filename": "invoice.pdf",
            "finalUrl": "https://billing.example/invoice.pdf",
            "fileSize": dl_file.stat().st_size,
        },
        app_paths,
    )
    assert dup["ok"] is True
    assert dup["duplicate"] is True

    reroute = chrome_bridge.reroute_chrome_file(
        {
            "full_path": str(dl_file),
            "new_folder": "Taxes/2026",
            "source_url": "https://billing.example/invoice.pdf",
        },
        app_paths,
        try_ipc=False,
    )
    assert reroute["ok"] is True
    assert (app_paths.downloads_dir / "Taxes" / "2026" / "invoice.pdf").is_file()


def test_safe_auto_unzip_and_zip_slip_guard(app_paths: AppPaths) -> None:
    cfg = Config.with_starter_defaults()
    cfg.settle_seconds = 0.0
    cfg.zip_keep_original = False
    cfg.rules.insert(
        0,
        Rule(
            id="models_zip",
            name="Printables Packs",
            destination="3D Models/Packs",
            keywords=["pack"],
            extensions=[".zip"],
            auto_unzip=True,
        ),
    )
    save_config(cfg, app_paths)

    zip_path = app_paths.downloads_dir / "cool_pack.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("part_a.stl", b"solid a\nendsolid a\n")
        zf.writestr("nested/part_b.stl", b"solid b\nendsolid b\n")

    svc = SorterService(app_paths)
    try:
        res = svc.scan_top_level_now()
        assert len(res["moved"]) == 1
        assert (app_paths.downloads_dir / "3D Models" / "Packs" / "cool_pack" / "part_a.stl").is_file()
        assert (app_paths.downloads_dir / "3D Models" / "Packs" / "cool_pack" / "nested" / "part_b.stl").is_file()
        assert (app_paths.downloads_dir / "Archives" / "_Extracted" / "cool_pack.zip").is_file()
    finally:
        svc.stop(timeout=0.5)

    # Zip-slip traversal must be blocked
    evil_zip = app_paths.downloads_dir / "evil.zip"
    with zipfile.ZipFile(evil_zip, "w") as zf:
        zf.writestr("../../escape.txt", b"bad")
    db = StateDB(app_paths.db_file)
    try:
        bad = extract_zip_safely(evil_zip, app_paths.downloads_dir / "Other", app_paths.downloads_dir, db=db)
        assert bad["ok"] is False
    finally:
        db.close()


def test_pdf_content_keyword_sniffing(app_paths: AppPaths) -> None:
    # Build a minimal PDF containing a FlateDecode stream with literal text "(Monthly Invoice)"
    compressed = zlib.compress(b"BT (Monthly Invoice #402) Tj ET")
    pdf_bytes = b"%PDF-1.4\n1 0 obj\n<< /Length " + str(len(compressed)).encode() + b" >>\nstream\n" + compressed + b"\nendstream\nendobj\n%%EOF"
    pdf_path = app_paths.downloads_dir / "download.pdf"
    pdf_path.write_bytes(pdf_bytes)

    preview = extract_pdf_text_preview(pdf_path)
    assert "Monthly Invoice" in preview

    cfg = Config.with_starter_defaults()
    cfg.rules.insert(
        0,
        Rule(
            id="pdf_inv",
            name="PDF Invoices",
            destination="Documents/Invoices",
            extensions=[".pdf"],
            content_keywords=["monthly invoice"],
        ),
    )
    decision = evaluate(FileCandidate(filename="download.pdf", path=pdf_path), cfg)
    assert decision.action == "move"
    assert decision.matched_rule == "PDF Invoices"
    assert str(decision.relative_dest).replace("\\", "/") == "Documents/Invoices/download.pdf"


def test_detox_scan_and_prune_empty_folders(app_paths: AppPaths) -> None:
    old_exe = app_paths.downloads_dir / "Software" / "old_setup.exe"
    old_exe.parent.mkdir(parents=True, exist_ok=True)
    old_exe.write_bytes(b"MZ" + b"\x00" * 100)
    old_ts = time.time() - (45 * 86400)
    os.utime(old_exe, (old_ts, old_ts))

    empty_dir = app_paths.downloads_dir / "EmptyTemp"
    empty_dir.mkdir(parents=True, exist_ok=True)

    scan = scan_detox_candidates(app_paths.downloads_dir, old_installer_days=30)
    assert len(scan["old_installers"]) == 1
    assert scan["old_installers"][0]["name"] == "old_setup.exe"
    assert str(empty_dir.resolve()) in scan["empty_folders"]

    pruned = prune_empty_folders(scan["empty_folders"], app_paths.downloads_dir)
    assert str(empty_dir.resolve()) in pruned["removed"]
    assert not empty_dir.exists()


def test_rule_learning_and_quick_search_index(app_paths: AppPaths) -> None:
    cfg = Config.with_starter_defaults()
    for _ in range(2):
        record_manual_route(
            destination="AI Models/HuggingFace",
            data_dir=app_paths.data_dir,
            source_url="https://huggingface.co/models/test",
            ext=".safetensors",
        )
    suggestions = get_rule_suggestions(cfg, app_paths.data_dir, threshold=2)
    assert len(suggestions) == 1
    assert suggestions[0]["host"] == "huggingface.co"

    dismiss_suggestion(suggestions[0]["key"], app_paths.data_dir)
    assert get_rule_suggestions(cfg, app_paths.data_dir, threshold=2) == []

    sample = app_paths.downloads_dir / "Documents" / "budget_2026.xlsx"
    sample.parent.mkdir(parents=True, exist_ok=True)
    sample.write_bytes(b"xlsx")
    db = StateDB(app_paths.db_file)
    try:
        entries = build_search_index(db, app_paths.downloads_dir)
        hits = filter_search_entries(entries, "budget 2026")
        assert len(hits) == 1
        assert hits[0]["name"] == "budget_2026.xlsx"
    finally:
        db.close()


def test_zip_bomb_verification_and_global_auto_unzip_settings(v3_qapp, app_paths: AppPaths) -> None:  # noqa: ANN001
    from download_sorter.ui.main_window import MainWindow

    # 1. Synthetic high-ratio zip bomb (>100:1 on >= 1 MB stream) must abort & clean up
    bomb_zip = app_paths.downloads_dir / "bomb.zip"
    with zipfile.ZipFile(bomb_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("zeros.bin", b"\x00" * (2 * 1024 * 1024))

    db = StateDB(app_paths.db_file)
    try:
        res = extract_zip_safely(
            bomb_zip,
            app_paths.downloads_dir / "Archives",
            app_paths.downloads_dir,
            db=db,
            max_compression_ratio=100.0,
        )
        assert res["ok"] is False
        assert res["zip_bomb"] is True
        assert not (app_paths.downloads_dir / "Archives" / "bomb").exists()
        assert bomb_zip.is_file()
    finally:
        db.close()

    # 2. Symlink entry in zip must be blocked
    sym_zip = app_paths.downloads_dir / "symlink.zip"
    with zipfile.ZipFile(sym_zip, "w") as zf:
        zi = zipfile.ZipInfo("link.txt")
        zi.external_attr = (0o120777 << 16)
        zf.writestr(zi, "/etc/passwd")
    sym_res = extract_zip_safely(sym_zip, app_paths.downloads_dir / "Archives", app_paths.downloads_dir)
    assert sym_res["ok"] is False
    assert sym_res["zip_bomb"] is True
    bomb_zip.unlink()
    sym_zip.unlink()

    # 3. Global auto_unzip_all + zip_keep_original=True via SorterService
    cfg = Config.with_starter_defaults()
    cfg.settle_seconds = 0.0
    cfg.auto_unzip_all = True
    cfg.zip_keep_original = True
    save_config(cfg, app_paths)

    normal_zip = app_paths.downloads_dir / "photos_bundle.zip"
    with zipfile.ZipFile(normal_zip, "w") as zf:
        zf.writestr("readme.txt", b"hello world")

    svc = SorterService(app_paths)
    win = MainWindow(svc)
    try:
        sort_res = svc.scan_top_level_now()
        assert len(sort_res["moved"]) == 1
        moved_dest = Path(sort_res["moved"][0]["dest_path"])
        assert (moved_dest.parent / "photos_bundle" / "readme.txt").is_file()
        assert moved_dest.is_file()

        # 4. Settings tab round-trip for new MacToggles
        assert win.chk_auto_unzip_all.isChecked() is True
        assert win.chk_zip_keep_original.isChecked() is True
        win.chk_auto_unzip_all.setChecked(False)
        win._save_settings_form()
        assert svc.config.auto_unzip_all is False
    finally:
        win.close()
        svc.stop(timeout=0.5)
