"""Tests for MainWindow, Live Why Tester, ToastManager, IPC, and CLI."""

from __future__ import annotations

import uuid

import about
from download_sorter import cli
from download_sorter.config import Config, save_config
from download_sorter.ipc import IPCServer
from download_sorter.paths import AppPaths
from download_sorter.service import SorterService
from download_sorter.ui.main_window import MainWindow
from download_sorter.ui.toasts import ToastManager


def test_main_window_live_tester_templates_and_simulator(v3_qapp, app_paths: AppPaths) -> None:
    cfg = Config(rules=[], settle_seconds=0.0)
    save_config(cfg, app_paths)

    dl = app_paths.downloads_dir
    assert dl is not None
    sample = dl / "Capacitance_Lab_Report.pdf"
    sample.write_text("pdf content", encoding="utf-8")

    svc = SorterService(app_paths)
    win = MainWindow(svc)

    # Apply School template via UI action
    win._apply_template("school")
    assert any(r.name == "School coursework" for r in svc.config.rules)

    # Live "Why" tester reacts immediately to text input
    win.edit_test_name.setText("Capacitance_Lab_Report.pdf")
    assert "Documents\\School" in win.lbl_test_result.text()
    assert win.list_test_trace.count() >= 2

    # Read-only simulation previews move without moving file on disk
    win._run_simulation()
    assert win.tbl_sim.rowCount() == 1
    assert sample.exists()

    # In-process toast creation
    toasts = ToastManager()
    toast = toasts.show_moves(
        "b-test",
        [{"filename": "Capacitance_Lab_Report.pdf", "dest_path": str(dl / "Documents" / "School" / "Capacitance_Lab_Report.pdf"), "matched_rule": "School coursework"}],
    )
    assert toast is not None
    toast.close()
    win.close()


def test_ipc_roundtrip(v3_qapp) -> None:
    import os
    from pathlib import Path
    import subprocess
    import sys
    import time

    pipe = f"TestDownloadSorterV3_{uuid.uuid4().hex[:8]}"
    srv = IPCServer(lambda req: {"ok": True, "echo": req.get("cmd")}, server_name=pipe)
    assert srv.start()
    src_dir = str(Path(__file__).resolve().parent.parent / "src")
    env = {**os.environ, "PYTHONPATH": src_dir}
    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            f"import json; from download_sorter.ipc import send_ipc_command; "
            f"print(json.dumps(send_ipc_command('ping', server_name={pipe!r})))",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        deadline = time.monotonic() + 5.0
        while proc.poll() is None and time.monotonic() < deadline:
            v3_qapp.processEvents()
            time.sleep(0.01)
        out, err = proc.communicate(timeout=2)
        assert proc.returncode == 0, err
        assert out.strip() == '{"ok": true, "echo": "ping"}'
    finally:
        srv.stop()
        v3_qapp.processEvents()  # drain socket events while the server is still alive


def test_cli_explain_status_simulate(app_paths: AppPaths, capsys) -> None:
    dl = app_paths.downloads_dir
    assert dl is not None
    (dl / "bracket.3mf").write_text("3mf", encoding="utf-8")
    (dl / "\u5361_sample.pdf").write_text("pdf", encoding="utf-8")

    assert cli.main(["--data-dir", str(app_paths.data_dir), "--watch-dir", str(dl), "--explain", "Capacitance_Lab_Report.pdf"]) == 0
    out = capsys.readouterr().out
    assert "Documents\\School" in out

    assert cli.main(["--data-dir", str(app_paths.data_dir), "--watch-dir", str(dl), "--simulate"]) == 0
    sim_out = capsys.readouterr().out
    assert "bracket.3mf" in sim_out
    assert "3D Models\\3MF" in sim_out

    assert cli.main(["--data-dir", str(app_paths.data_dir), "--watch-dir", str(dl), "--status"]) == 0
    status_out = capsys.readouterr().out
    assert f"Download Sorter v{about.__version__}" in status_out


def test_main_entry_version_and_held_3mf_ui_and_folder_cli(
    v3_qapp,
    app_paths: AppPaths,
    tmp_path,
    capsys,
    monkeypatch,
) -> None:
    import main as root_main

    monkeypatch.setattr("download_sorter.ui.main_window.is_startup_enabled", lambda: False)
    monkeypatch.setattr("download_sorter.ui.main_window.is_context_menu_enabled", lambda: False)

    assert root_main.main(["--version"]) == 0
    assert f"Download Sorter {about.__version__}" in capsys.readouterr().out

    dl = app_paths.downloads_dir
    assert dl is not None
    held_file = dl / "print_part.3mf"
    held_file.write_text("3mf bytes", encoding="utf-8")

    cfg = Config.with_starter_defaults()
    cfg.settle_seconds = 0.0
    cfg.hold_3mf_enabled = True
    cfg.hold_3mf_days = 7
    save_config(cfg, app_paths)

    svc = SorterService(app_paths)
    win = MainWindow(svc)
    assert win.btn_file_held.isEnabled()
    assert "(1)" in win.btn_file_held.text()
    assert win.chk_hold_3mf.isChecked()
    assert win.spin_hold_days.value() == 7

    win._on_file_held_now()
    assert not held_file.exists()
    assert (dl / "3D Models" / "3MF" / "print_part.3mf").exists()
    assert not win.btn_file_held.isEnabled()

    # Explorer positional folder argument sorts target folder even when main config is paused
    cfg.paused = True
    save_config(cfg, app_paths)
    extra_dir = tmp_path / "RightClickFolder"
    extra_dir.mkdir()
    doc = extra_dir / "Syllabus_Fall.pdf"
    doc.write_text("pdf", encoding="utf-8")
    assert cli.main(["--data-dir", str(app_paths.data_dir), str(extra_dir)]) == 0
    assert not doc.exists()
    assert (extra_dir / "Documents" / "School" / "Syllabus_Fall.pdf").exists()
    win.close()


def test_autosort_toggle_banner_and_animations(
    v3_qapp,
    app_paths: AppPaths,
    monkeypatch,
) -> None:
    from download_sorter.ui.motion import AnimatedBanner, fade_in, flash_rows

    monkeypatch.setattr("download_sorter.ui.main_window.is_startup_enabled", lambda: False)
    monkeypatch.setattr("download_sorter.ui.main_window.is_context_menu_enabled", lambda: False)

    cfg = Config.with_starter_defaults()
    cfg.paused = True
    save_config(cfg, app_paths)

    svc = SorterService(app_paths)
    win = MainWindow(svc)
    assert "Turn Auto-Sort ON" in win.btn_pause.text()
    assert "Turn Auto-Sort ON" in win.btn_home_autosort.text()
    assert not win.chk_auto_sort.isChecked()

    win._toggle_pause()
    assert not svc.paused
    assert win.chk_auto_sort.isChecked()
    assert "ON" in win.btn_pause.text()
    assert "ON" in win.banner.text()

    win.chk_auto_sort.setChecked(False)
    win._save_settings_form()
    assert svc.paused
    assert "Turn Auto-Sort ON" in win.btn_home_autosort.text()

    banner = AnimatedBanner(win)
    banner.show_message("Sorted 2 files.", kind="success")
    assert banner.text() == "Sorted 2 files."
    fade_in(win.lbl_test_result, duration=50)
    flash_rows(win.tbl_sim, [], duration=50)
    v3_qapp.processEvents()
    win.close()


def test_first_run_walkthrough_hides_instructions_and_persists(
    v3_qapp,
    app_paths: AppPaths,
    monkeypatch,
) -> None:
    from download_sorter.config import load_config
    from download_sorter.ui.main_window import WalkthroughDialog

    monkeypatch.setattr("download_sorter.ui.main_window.is_startup_enabled", lambda: False)
    monkeypatch.setattr("download_sorter.ui.main_window.is_context_menu_enabled", lambda: False)

    cfg = Config.with_starter_defaults()
    cfg.tutorial_completed = False
    save_config(cfg, app_paths)

    svc = SorterService(app_paths)
    win = MainWindow(svc)
    assert win.instructions_widget.isHidden() is False

    # Exercise the 3-step frameless WalkthroughDialog navigation
    dlg = WalkthroughDialog(svc.watch_root, svc.config.hold_3mf_days, win)
    assert dlg.pages.currentIndex() == 0
    assert dlg._dots[0].property("active") == "true"
    dlg._on_next_clicked()
    assert dlg.pages.currentIndex() == 1
    assert dlg._dots[1].property("active") == "true"
    dlg._on_next_clicked()
    assert dlg.pages.currentIndex() == 2
    assert "Finish" in dlg.btn_next.text()
    dlg.close()

    # Completing the walkthrough persists tutorial_completed and hides instructions
    win.complete_walkthrough()
    assert svc.config.tutorial_completed is True
    assert load_config(app_paths).tutorial_completed is True
    assert win.instructions_widget.isHidden() is True
    win.close()

    # Reopening the window loads persisted state and keeps instructions hidden
    svc2 = SorterService(app_paths)
    win2 = MainWindow(svc2)
    assert win2.instructions_widget.isHidden() is True
    win2.close()


def test_sorty_mascot_ribbon_icons_and_show_and_tell_canvas(
    v3_qapp,
    app_paths: AppPaths,
    monkeypatch,
) -> None:
    from download_sorter.ui.main_window import WalkthroughDialog
    from download_sorter.ui.motion import (
        SortyMascotWidget,
        WalkthroughDiagramCanvas,
        make_vector_icon,
        slide_toggle,
    )

    monkeypatch.setattr("download_sorter.ui.main_window.is_startup_enabled", lambda: False)
    monkeypatch.setattr("download_sorter.ui.main_window.is_context_menu_enabled", lambda: False)

    # Vector icons render cleanly for ribbon and tab icons
    for icon_name in ("home", "rules", "settings", "bolt", "undo", "folder", "cube", "pause", "play", "help", "check"):
        ic = make_vector_icon(icon_name, "#F3F4F6", 16)
        assert not ic.isNull()

    # Sorty mascot poses & 30fps tick + QPixmap rendering
    mascot = SortyMascotWidget(64)
    for step_idx, expected_pose in ((0, "waving"), (1, "safe"), (2, "celebrating")):
        mascot.set_step(step_idx)
        assert mascot.pose == expected_pose
        mascot._step()
        px = mascot.to_pixmap(64, 64)
        assert not px.isNull() and px.width() == 64

    mascot.set_pose("sleeping")
    assert mascot.pose == "sleeping"
    assert not mascot.to_pixmap(48, 48).isNull()

    # Animated show-and-tell diagram canvas renders every step cleanly
    canvas = WalkthroughDiagramCanvas()
    for step_idx in (0, 1, 2):
        canvas.set_step(step_idx)
        assert canvas.step == step_idx
        canvas._tick()
        shot = canvas.grab()
        assert not shot.isNull()

    # MainWindow header mascot + paused/active sync + WalkthroughDialog integration
    cfg = Config.with_starter_defaults()
    cfg.paused = False
    cfg.tutorial_completed = False
    save_config(cfg, app_paths)

    svc = SorterService(app_paths)
    win = MainWindow(svc)
    assert isinstance(win.header_mascot, SortyMascotWidget)
    assert win.header_mascot.pose == "smart"
    assert not win.btn_sort_now.icon().isNull()
    assert not win.tabs.tabIcon(0).isNull()

    # Pausing transitions header mascot to sleeping
    win._toggle_pause()
    assert win.header_mascot.pose == "sleeping"
    win._toggle_pause()
    assert win.header_mascot.pose == "smart"

    # Walkthrough modal wires mascot poses, Sorty speech bubble, and animated arrow stage
    dlg = WalkthroughDialog(svc.watch_root, svc.config.hold_3mf_days, win)
    assert isinstance(dlg.mascot_widget, SortyMascotWidget)
    assert isinstance(dlg.stage_canvas, WalkthroughDiagramCanvas)
    assert "Sorty" in dlg.lbl_mascot_speech.text()
    assert dlg.mascot_widget.pose == "waving"
    assert dlg.stage_canvas.step == 0

    dlg._on_next_clicked()
    assert dlg.mascot_widget.pose == "safe"
    assert dlg.stage_canvas.step == 1

    dlg._on_next_clicked()
    assert dlg.mascot_widget.pose == "celebrating"
    assert dlg.stage_canvas.step == 2
    dlg.close()

    slide_toggle(win.instructions_widget, False, animate=False)
    assert win.instructions_widget.isHidden() is True
    win.close()


def test_top_right_side_ribbon_category_art_and_hover_pause(
    v3_qapp,
    tmp_path,
) -> None:
    import time
    from pathlib import Path
    from PySide6.QtCore import QEvent, QPointF
    from PySide6.QtGui import QEnterEvent
    from download_sorter.ui.toasts import CategoryArtWidget, ToastManager, category_info

    # Category art resolves cleanly across file types
    assert category_info(Path("part.3mf"), "3D Models")[2] == "print"
    assert category_info(Path("bracket.step"), "CAD")[2] == "cad"
    assert category_info(Path("invoice.pdf"), "Documents")[2] == "pdf"
    assert category_info(Path("demo.py"), "Code")[2] == "code"

    for kind in ("cad", "cnc", "print", "pdf", "doc", "sheet", "image", "video", "audio", "archive", "installer", "code", "default"):
        art = CategoryArtWidget(kind=kind)
        assert not art.grab().isNull()

    undone: list[str] = []
    mgr = ToastManager(on_undo=undone.append)

    t1 = mgr.show_moves(
        "b-1",
        [{"file_name": "bracket_assembly.3mf", "dest_path": str(tmp_path / "3D Models" / "bracket_assembly.3mf"), "folder_display": "3D Models"}],
        duration_ms=4000,
    )
    t2 = mgr.show_moves(
        "b-2",
        [{"file_name": "invoice_2026.pdf", "dest_path": str(tmp_path / "Documents" / "invoice_2026.pdf"), "folder_display": "Documents"}],
        duration_ms=4000,
    )
    assert t1 is not None and t2 is not None
    assert len(mgr._active) == 2
    assert isinstance(t1.art_widget, CategoryArtWidget)
    assert t1.art_widget._kind == "print"
    assert t2.art_widget._kind == "pdf"

    # Anchored in top-right corner with vertical slot stacking
    pos1 = t1._anchor_top_right(0)
    pos2 = t2._anchor_top_right(1)
    assert pos1.y() < 250
    assert pos1.x() > 200
    assert pos2.y() > pos1.y()

    # Hover pauses timeout countdown; leaving resumes it
    t1._remaining_fraction = 0.8
    t1._elapsed_ms = 800.0
    t1.enterEvent(QEnterEvent(QPointF(10, 10), QPointF(10, 10), QPointF(10, 10)))
    assert t1._hovered is True
    time.sleep(0.05)
    t1._on_tick()
    assert t1._remaining_fraction == 0.8

    t1.leaveEvent(QEvent(QEvent.Type.Leave))
    assert t1._hovered is False
    time.sleep(0.05)
    t1._on_tick()
    assert t1._remaining_fraction < 0.8

    t1.undo_btn.click()
    assert undone == ["b-1"]
    t2.close_btn.click()
    v3_qapp.processEvents()


def test_folder_architecture_tiers_premade_custom_shift_undo_and_defaults(
    v3_qapp,
    app_paths: AppPaths,
    monkeypatch,
) -> None:
    from download_sorter.architecture import (
        ARCHITECTURE_PRESETS,
        PREMADE_FOLDER_CATALOG,
        apply_architecture_to_config,
    )
    from download_sorter.config import load_config

    monkeypatch.setattr("download_sorter.ui.main_window.is_startup_enabled", lambda: False)
    monkeypatch.setattr("download_sorter.ui.main_window.is_context_menu_enabled", lambda: False)

    assert len(PREMADE_FOLDER_CATALOG) >= 40
    assert len(ARCHITECTURE_PRESETS) >= 4

    dl = app_paths.downloads_dir
    assert dl is not None

    # Create loose downloads, previously sorted managed files, and a private unmanaged folder
    loose_pdf = dl / "Acme_Contract_2026.pdf"
    loose_pdf.write_text("pdf", encoding="utf-8")
    old_managed_dir = dl / "Documents" / "PDF"
    old_managed_dir.mkdir(parents=True, exist_ok=True)
    old_managed_pdf = old_managed_dir / "Quarterly_Contract_Signed.pdf"
    old_managed_pdf.write_text("pdf", encoding="utf-8")

    private_dir = dl / "MyPrivateTaxDocs"
    private_dir.mkdir(parents=True, exist_ok=True)
    private_file = private_dir / "tax_return_2025.pdf"
    private_file.write_text("private", encoding="utf-8")

    cfg = Config.with_starter_defaults()
    cfg.settle_seconds = 0.0
    cfg.hold_3mf_enabled = False
    save_config(cfg, app_paths)

    svc = SorterService(app_paths)
    win = MainWindow(svc)
    assert win.tabs.count() == 3
    assert win.tabs.tabText(0) == "Sorting Flow"
    assert not win.arch_tab.btn_arch_undo.isHidden()
    assert not win.arch_tab.btn_arch_defaults.isHidden()

    # Add a 3rd tier, a premade Tier 1 root ("Finance"), and a custom Tier 3 child folder with rules
    win.arch_tab._on_add_tier()
    assert win.arch_tab._arch.tier_count == 3

    fin_node = win.arch_tab._arch.add_premade_node("t1_finance", tier=1)
    assert fin_node is not None
    contracts_node = win.arch_tab._arch.add_custom_node(
        name="Contracts",
        tier=2,
        parent_id=fin_node.id,
        extensions=[".pdf"],
        keywords=["contract"],
        domains=["acme.com"],
    )
    signed_tier3 = win.arch_tab._arch.add_custom_node(
        name="Signed2026",
        tier=3,
        parent_id=contracts_node.id,
        extensions=[".pdf"],
        keywords=["contract"],
    )
    assert win.arch_tab._arch.node_relative_path(signed_tier3.id) == r"Finance\Contracts\Signed2026"

    target_cfg = apply_architecture_to_config(win.arch_tab._arch, svc.config)
    plan = svc.preview_architecture_shift(target_cfg)
    planned_names = {m["src"].name for m in plan["moves_planned"]}
    assert "Acme_Contract_2026.pdf" in planned_names
    assert "Quarterly_Contract_Signed.pdf" in planned_names
    assert "tax_return_2025.pdf" not in planned_names

    # Apply architecture & automatically shift files into expected folders
    res = svc.apply_architecture_and_shift(target_cfg, shift_files=True, create_empty_folders=True)
    assert res["shifted_count"] >= 2
    expected_dir = dl / "Finance" / "Contracts" / "Signed2026"
    assert (expected_dir / "Acme_Contract_2026.pdf").exists()
    assert (expected_dir / "Quarterly_Contract_Signed.pdf").exists()
    assert not loose_pdf.exists()
    assert not old_managed_pdf.exists()
    # Unmanaged user folder must remain untouched
    assert private_file.exists()

    # Always-available Undo reverses both file shifts and architecture config snapshot
    win._on_undo_last()
    assert loose_pdf.exists()
    assert old_managed_pdf.exists()
    assert not (expected_dir / "Acme_Contract_2026.pdf").exists()
    assert load_config(app_paths).architecture.get("tier_count", 2) == 2

    # Re-apply then test Return to Defaults
    svc.apply_architecture_and_shift(target_cfg, shift_files=True)
    assert (expected_dir / "Acme_Contract_2026.pdf").exists()
    reset_res = svc.reset_to_defaults_and_shift(shift_files=True)
    assert reset_res["shifted_count"] >= 2
    assert (dl / "Documents" / "PDF" / "Acme_Contract_2026.pdf").exists()
    assert (dl / "Documents" / "PDF" / "Quarterly_Contract_Signed.pdf").exists()
    assert private_file.exists()
    win.close()


def test_sorty_click_tips_and_cute_animation(
    v3_qapp,
    app_paths: AppPaths,
    monkeypatch,
) -> None:
    from download_sorter.ui.main_window import WalkthroughDialog
    from download_sorter.ui.motion import SortyMascotWidget

    monkeypatch.setattr("download_sorter.ui.main_window.is_startup_enabled", lambda: False)
    monkeypatch.setattr("download_sorter.ui.main_window.is_context_menu_enabled", lambda: False)

    assert len(SortyMascotWidget.SORTY_TIPS) >= 5
    cfg = Config.with_starter_defaults()
    save_config(cfg, app_paths)

    svc = SorterService(app_paths)
    win = MainWindow(svc)

    seen_tips: list[str] = []
    win.header_mascot.clicked.connect(seen_tips.append)

    t1 = win.header_mascot.trigger_cute_tip()
    t2 = win.header_mascot.trigger_cute_tip()
    assert t1 != t2
    assert seen_tips == [t1, t2]
    assert "Sorty Tip:" in win.banner.text()
    assert t2 in win.lbl_footer.text()

    # Simulate mid-animation cute frame rendering
    win.header_mascot._cute_prog = 0.45
    win.header_mascot._step()
    px = win.header_mascot.to_pixmap(54, 54)
    assert not px.isNull() and px.width() == 54

    # Walkthrough mascot click also updates Sorty speech label
    dlg = WalkthroughDialog(svc.watch_root, svc.config.hold_3mf_days, win)
    wt_tip = dlg.mascot_widget.trigger_cute_tip()
    assert wt_tip in dlg.lbl_mascot_speech.text()
    dlg.close()
    win.close()


def test_upgrade_carry_over_and_export_setup(
    v3_qapp,
    app_paths: AppPaths,
    tmp_path,
    monkeypatch,
) -> None:
    import json
    from download_sorter.config import carry_over_setup, export_setup_file
    from download_sorter.ui.main_window import UpgradeCarryOverDialog

    monkeypatch.setattr("download_sorter.ui.main_window.is_startup_enabled", lambda: False)
    monkeypatch.setattr("download_sorter.ui.main_window.is_context_menu_enabled", lambda: False)

    legacy_path = tmp_path / "v2_config.json"
    legacy_payload = {
        "schema_version": 2,
        "watch_folder": "%USERPROFILE%\\Downloads",
        "settle_seconds": 3.5,
        "default_folder": "UnsortedStuff",
        "notifications": True,
        "hold": {"enabled": True, "hold_days": 21},
        "type_folders": {
            "enabled": True,
            "names": {".pdf": "PortableDocs", ".stl": "Meshes"},
        },
        "makerworld_destination": "3D Printing\\MakerWorld",
        "gcode_destination": "CNC\\GCode",
        "haas_destination": "CNC\\Haas",
        "keyword_rules": [
            {
                "name": "Invoices 2026",
                "keywords": ["invoice", "receipt"],
                "folder": "Finance\\Invoices",
                "enabled": True,
            }
        ],
        "extension_rules": [
            {"extensions": [".dxf"], "folder": "CNC\\DXF", "enabled": True},
            {"extensions": [".log"], "folder": "Logs", "enabled": False},
        ],
    }
    legacy_path.write_text(json.dumps(legacy_payload, indent=2), encoding="utf-8")

    # Full carry-over preserves every rule, category override, hold policy, and architecture
    carried, summary = carry_over_setup(legacy_path, Config.with_starter_defaults(), merge_rules_only=False)
    assert summary["source_schema"] == 2
    assert any(r.name == "Invoices 2026" and r.destination == "Finance\\Invoices" for r in carried.rules)
    assert any(d.lower() == "makerworld.com" for r in carried.rules for d in r.domains)
    assert carried.category_map.get(".dxf") == "CNC\\DXF"
    assert carried.category_map.get(".gcode") == "CNC\\GCode"
    assert carried.category_map.get(".nc") == "CNC\\Haas"
    assert ".log" not in carried.category_map
    assert carried.type_subfolders.get(".pdf") == "PortableDocs"
    assert carried.hold_3mf_enabled is True
    assert carried.hold_3mf_days == 21
    assert summary["arch_nodes"] >= 3

    # Export portable setup JSON and round-trip via carry_over_setup
    backup_path = tmp_path / "exported_v3_setup.json"
    export_setup_file(carried, backup_path)
    assert backup_path.exists()
    reloaded, reload_summary = carry_over_setup(backup_path, merge_rules_only=False)
    assert any(r.name == "Invoices 2026" for r in reloaded.rules)
    assert reload_summary["tier_count"] == summary["tier_count"]

    # UpgradeCarryOverDialog previews and accepts cleanly
    svc = SorterService(app_paths)
    win = MainWindow(svc)
    dlg = UpgradeCarryOverDialog(svc, win)
    dlg.edit_source.setText(str(legacy_path))
    assert dlg.btn_apply.isEnabled()
    assert "Invoices" in dlg.lbl_preview.text() or "custom rule" in dlg.lbl_preview.text()
    dlg._on_accept()
    assert dlg.carried_config is not None
    assert any(r.name == "Invoices 2026" for r in dlg.carried_config.rules)
    dlg.close()
    win.close()


def test_top_to_bottom_architecture_and_auto_create_basic(v3_qapp, app_paths: AppPaths) -> None:
    cfg = Config.with_starter_defaults()
    cfg.settle_seconds = 0.0
    save_config(cfg, app_paths)

    dl = app_paths.downloads_dir
    assert dl is not None
    (dl / "report.pdf").write_text("pdf", encoding="utf-8")
    (dl / "photo.png").write_text("png", encoding="utf-8")

    svc = SorterService(app_paths)
    win = MainWindow(svc)
    win.show()
    win.arch_tab.setGeometry(0, 0, 960, 660)
    win.arch_tab.show()
    win.arch_tab.canvas.resize(920, 620)
    v3_qapp.processEvents()

    canvas = win.arch_tab.canvas
    assert 0 in canvas._tier_band_rects
    assert 1 in canvas._tier_band_rects
    # Expand a Tier 1 parent folder to reveal its Tier 2 band
    t1_parent = next(n for n in win.arch_tab._arch.nodes_in_tier(1) if win.arch_tab._arch.children_of(n.id))
    canvas.select_node(t1_parent.id)
    v3_qapp.processEvents()
    assert 2 in canvas._tier_band_rects
    assert canvas._tier_band_rects[0].top() < canvas._tier_band_rects[1].top() < canvas._tier_band_rects[2].top()

    # Tier 1 cards are arranged horizontally across rows inside Tier 1 band rather than stacked in 1 tall column
    tier1_nodes = [n for n in win.arch_tab._arch.nodes if n.tier == 1]
    tier1_rects = [canvas._node_rects[n.id] for n in tier1_nodes if n.id in canvas._node_rects]
    assert len(tier1_rects) >= 4
    unique_rows = {round(r.y()) for r in tier1_rects}
    assert len(unique_rows) < len(tier1_rects)

    # Auto-Create Basic builds a clean standard starter foundation + matching local file folders
    win.arch_tab.btn_auto_create.click()
    v3_qapp.processEvents()
    assert win.arch_tab._dirty is True
    assert win.arch_tab._arch.tier_count == 2
    assert any(n.name == "Documents" and n.tier == 1 for n in win.arch_tab._arch.nodes)
    assert any(n.name == "Images" and n.tier == 1 for n in win.arch_tab._arch.nodes)
    assert "Auto-created" in win.lbl_footer.text()
    win.close()
    v3_qapp.processEvents()


def test_ribbon_timer_setting_and_close_bug_fix(v3_qapp, app_paths: AppPaths) -> None:
    cfg = Config.with_starter_defaults()
    cfg.settle_seconds = 0.0
    save_config(cfg, app_paths)

    svc = SorterService(app_paths)
    win = MainWindow(svc)
    win.show()
    v3_qapp.processEvents()

    # Settings exposes Ribbon Timer setting, persists it, and applies it to ribbons
    win.spin_ribbon_timeout.setValue(4.0)
    win._save_settings_form()
    assert svc.config.ribbon_timeout_seconds == 4.0

    win._on_preview_ribbon()
    v3_qapp.processEvents()
    active = list(win._preview_toast_mgr._active)
    assert len(active) == 1
    toast = active[0]
    assert toast._duration_ms == 4000
    assert toast._hard_timeout_timer.isActive()

    # Clicking the X close button dismisses immediately even without window focus
    toast.close_btn.click()
    v3_qapp.processEvents()
    assert toast._closing is True
    assert not toast.isVisible()
    assert toast not in win._preview_toast_mgr._active

    # Preview again and test Close Ribbons button
    win._on_preview_ribbon()
    v3_qapp.processEvents()
    assert len(win._preview_toast_mgr._active) == 1
    win.btn_close_ribbons.click()
    v3_qapp.processEvents()
    assert len(win._preview_toast_mgr._active) == 0

    # Banner close button also hides immediately
    win.banner.show_message("Closeable test notice", kind="info")
    v3_qapp.processEvents()
    assert win.banner.isVisible()
    win.banner._close_btn.click()
    v3_qapp.processEvents()
    assert not win.banner.isVisible()
    win.close()
    v3_qapp.processEvents()


def test_sorty_party_easter_egg(v3_qapp, app_paths: AppPaths) -> None:
    cfg = Config.with_starter_defaults()
    cfg.settle_seconds = 0.0
    save_config(cfg, app_paths)

    svc = SorterService(app_paths)
    win = MainWindow(svc)
    win.show()
    v3_qapp.processEvents()

    # Method 1: Secret keyword in Custom Rules Live Tester
    win.edit_test_name.setText("sorty")
    v3_qapp.processEvents()
    assert win.header_mascot._party_mode is True
    assert win._confetti.isVisible()
    assert "EASTER EGG" in win.lbl_test_result.text()
    assert "Easter Egg" in win.banner.text()

    # Reset mascot & confetti cleanly, then trigger via 5 rapid clicks on Sorty
    win.header_mascot._end_party_mode()
    win._confetti.hide()
    assert win.header_mascot._party_mode is False

    for _ in range(5):
        win.header_mascot.trigger_cute_tip()
    v3_qapp.processEvents()
    assert win.header_mascot._party_mode is True
    assert win._confetti.isVisible()
    assert "Easter Egg" in win.banner.text()
    px = win.header_mascot.to_pixmap(54, 54)
    assert not px.isNull()
    win.close()
    v3_qapp.processEvents()
