"""Tests for Chrome MV3 extension bundle, Native Messaging bridge, and IPC integration."""

from __future__ import annotations

import base64
import hashlib
import io
import json

from download_sorter import chrome_bridge
from download_sorter.config import Config, Rule, save_config
from download_sorter.paths import AppPaths
from download_sorter.quick_switcher import get_recent_file
from download_sorter.service import SorterService
from download_sorter.ui.main_window import MainWindow
from download_sorter.ui.tray_app import build_ipc_handler


def test_chrome_extension_manifest_key_matches_extension_id() -> None:
    ext_dir = chrome_bridge.find_extension_dir()
    assert ext_dir is not None
    manifest = json.loads((ext_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["manifest_version"] == 3
    for perm in ("downloads", "nativeMessaging", "storage", "tabs", "contextMenus"):
        assert perm in manifest["permissions"]

    der = base64.b64decode(manifest["key"])
    digest = hashlib.sha256(der).hexdigest()[:32]
    derived_id = "".join(chr(ord("a") + int(ch, 16)) for ch in digest)
    assert derived_id == chrome_bridge.EXTENSION_ID


def test_native_messaging_framing_and_routing(app_paths: AppPaths) -> None:
    cfg = Config.with_starter_defaults()
    cfg.rules.insert(
        0,
        Rule(
            id="printables",
            name="Printables Models",
            destination="3D Printing/Printables",
            domains=["printables.com"],
            extensions=[".stl"],
        ),
    )
    save_config(cfg, app_paths)

    req_pdf = {
        "action": "route",
        "filename": "Bank_Statement_Nov.pdf",
        "finalUrl": "https://bank.example/dl",
    }
    in_buf = io.BytesIO()
    chrome_bridge.write_native_message(in_buf, req_pdf)
    in_buf.seek(0)
    out_buf = io.BytesIO()

    assert chrome_bridge.run_native_host(app_paths, stdin=in_buf, stdout=out_buf) == 0
    out_buf.seek(0)
    resp = chrome_bridge.read_native_message(out_buf)
    assert resp is not None
    assert resp["ok"] is True
    assert resp["decision"] == "suggest"
    assert resp["relative_dest"].endswith("/Bank_Statement_Nov.pdf")
    assert "\\" not in resp["relative_dest"]
    assert resp["conflict_action"] == "uniquify"

    # Domain rule routing
    stl_resp = chrome_bridge.route_chrome_download(
        {"filename": "bracket.stl", "finalUrl": "https://www.printables.com/model/123"},
        app_paths,
    )
    assert stl_resp["decision"] == "suggest"
    assert stl_resp["relative_dest"] == "3D Printing/Printables/bracket.stl"

    # Chrome-routed .3mf goes straight to sorted subfolder (no hold needed when Chrome routes directly)
    three_mf_resp = chrome_bridge.route_chrome_download({"filename": "plate1.3mf"}, app_paths)
    assert three_mf_resp["decision"] == "suggest"
    assert three_mf_resp["relative_dest"].endswith("/plate1.3mf")

    # Paused sorter falls back to Chrome default
    cfg.paused = True
    save_config(cfg, app_paths)
    paused_resp = chrome_bridge.route_chrome_download({"filename": "invoice.pdf"}, app_paths)
    assert paused_resp["decision"] == "default"
    assert paused_resp["paused"] is True


def test_completed_chrome_download_records_db_quick_switcher_and_ipc(v3_qapp, app_paths: AppPaths) -> None:  # noqa: ANN001
    cfg = Config.with_starter_defaults()
    cfg.tutorial_completed = True
    save_config(cfg, app_paths)

    dest = app_paths.downloads_dir / "Documents" / "PDFs" / "statement.pdf"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b"%PDF-sample")

    # 1. Direct offline completion recording (no daemon IPC)
    res_direct = chrome_bridge.record_chrome_completed(
        {
            "filename": "statement.pdf",
            "full_path": str(dest),
            "matched_rule": "Documents",
            "reason": "Extension PDF",
            "source_url": "https://example.com/statement.pdf",
        },
        app_paths,
        try_ipc=False,
    )
    assert res_direct["ok"] is True
    recent = get_recent_file(data_dir=app_paths.data_dir)
    assert recent is not None and recent["name"] == "statement.pdf"

    # Refuses paths outside watch_root
    outside = app_paths.data_dir / "escape.pdf"
    outside.write_bytes(b"bad")
    bad = chrome_bridge.record_chrome_completed({"full_path": str(outside)}, app_paths, try_ipc=False)
    assert bad["ok"] is False

    # 2. Live IPC handler ('chrome_routed') notifies on_moves_completed & DB
    svc = SorterService(app_paths)
    win = MainWindow(svc)
    seen_moves: list[tuple[str, list[dict]]] = []
    svc.on_moves_completed = lambda b, m: seen_moves.append((b, m))
    try:
        handler = build_ipc_handler(
            svc,
            win,
            parent=win,
            on_show_window=lambda: None,
            on_quit=lambda: None,
        )
        dest2 = app_paths.downloads_dir / "Images" / "photo.png"
        dest2.parent.mkdir(parents=True, exist_ok=True)
        dest2.write_bytes(b"pngdata")

        ipc_resp = handler(
            {
                "cmd": "chrome_routed",
                "filename": "photo.png",
                "dest_path": str(dest2),
                "matched_rule": "Images",
                "reason": "Routed directly by Chrome Extension",
                "source_url": "https://images.example/photo.png",
            }
        )
        assert ipc_resp["ok"] is True
        assert len(seen_moves) == 1
        assert seen_moves[0][1][0]["file_name"] == "photo.png"
        active = svc.db.list_active_moves(limit=5)
        assert active[0]["filename"] == "photo.png"

        # 3. Live IPC 'set_paused' and 'reload_config'
        pause_ipc = handler({"cmd": "set_paused", "paused": True})
        assert pause_ipc["ok"] is True
        assert svc.paused is True
        reload_ipc = handler({"cmd": "reload_config"})
        assert reload_ipc["ok"] is True
    finally:
        win.close()
        svc.stop(timeout=1.0)


def test_tab_url_fallback_tokens_and_new_native_actions(app_paths: AppPaths) -> None:
    cfg = Config.with_starter_defaults()
    cfg.rules.insert(
        0,
        Rule(
            id="gh_assets",
            name="GitHub Releases",
            destination="Dev/{host}/{ext}",
            domains=["github.com"],
            extensions=[".zip"],
        ),
    )
    save_config(cfg, app_paths)

    # CDN finalUrl misses domain, but active tabUrl matches github.com and expands {host}/{ext}
    cdn_resp = chrome_bridge.route_chrome_download(
        {
            "filename": "release-v1.zip",
            "finalUrl": "https://objects.githubusercontent.com/github-production-release-asset/123",
            "tabUrl": "https://github.com/devyn/download-sorter/releases",
            "tabTitle": "Releases · devyn/download-sorter",
        },
        app_paths,
    )
    assert cdn_resp["ok"] is True
    assert cdn_resp["decision"] == "suggest"
    assert cdn_resp["relative_dest"] == "Dev/objects.githubusercontent.com/zip/release-v1.zip"
    assert cdn_resp["source_host"] == "objects.githubusercontent.com"

    # list_folders returns sorted relative destinations
    folders_resp = chrome_bridge.handle_native_request({"action": "list_folders"}, app_paths)
    assert folders_resp["ok"] is True
    assert "Documents" in folders_resp["folders"]

    # create_rule adds a new rule and avoids duplicates
    created = chrome_bridge.create_chrome_rule(
        {"domain": "https://www.makerworld.com/en/models/1", "destination": "3D Models/MakerWorld"},
        app_paths,
        try_ipc=False,
    )
    assert created["ok"] is True
    assert created["created"] is True
    assert created["rule"]["domains"] == ["makerworld.com"]

    dup = chrome_bridge.create_chrome_rule(
        {"domain": "makerworld.com", "destination": "3D Models/MakerWorld"},
        app_paths,
        try_ipc=False,
    )
    assert dup["ok"] is True
    assert dup["created"] is False

    # toggle_pause flips and persists config.paused
    p1 = chrome_bridge.toggle_pause_from_chrome({}, app_paths, try_ipc=False)
    assert p1["ok"] is True and p1["paused"] is True
    p2 = chrome_bridge.toggle_pause_from_chrome({"paused": False}, app_paths, try_ipc=False)
    assert p2["ok"] is True and p2["paused"] is False

    # undo_last runs cleanly even with no moves
    undo_resp = chrome_bridge.undo_last_from_chrome(app_paths, try_ipc=False)
    assert undo_resp["ok"] is True
