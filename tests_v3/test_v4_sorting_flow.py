"""Tests for Download Sorter v4.0 Sorting Flow pipeline and unified UI."""

from __future__ import annotations

from pathlib import Path

import pytest

from download_sorter.config import Config, Rule, save_config
from download_sorter.paths import AppPaths
from download_sorter.pipeline import build_rule_from_cues, infer_teach_cues, trace_file
from download_sorter.service import SorterService


def test_pipeline_trace_steps_and_shadowing() -> None:
    cfg = Config.with_starter_defaults()
    cfg.hold_3mf_enabled = True
    cfg.hold_3mf_days = 7
    cfg.rules = [
        Rule(
            id="r_top",
            name="Stripe Invoices",
            destination="Finance\\Invoices",
            keywords=["invoice"],
            domains=["stripe.com"],
            enabled=True,
        ),
        Rule(
            id="r_shadow",
            name="All Invoices",
            destination="Documents\\PDF",
            keywords=["invoice"],
            exclude_keywords=["draft"],
            enabled=True,
        ),
    ]

    # Step 1: Ignore guard
    t_ing = trace_file("big_archive.zip.crdownload", cfg)
    assert t_ing.winning_step == 1
    assert t_ing.outcome == "ignore"
    assert t_ing.hold_id == "h-ignore"

    # Step 1: Active .3mf hold
    t_3mf = trace_file("benchy_plate1.3mf", cfg, simulate_held_3mf=True)
    assert t_3mf.winning_step == 1
    assert t_3mf.outcome == "hold"
    assert t_3mf.hold_id == "h-3mf"

    # Step 2: Explicit rule + shadowed rule tracking
    t_rule = trace_file("March_Invoice_2026.pdf", cfg, source_url="https://dashboard.stripe.com/inv")
    assert t_rule.winning_step == 2
    assert t_rule.outcome == "move"
    assert t_rule.matched_rule_id == "r_top"
    assert "r_shadow" in t_rule.shadowed_rule_ids
    assert t_rule.dest_folder == "Finance\\Invoices"

    # Step 2: Excluded keyword reporting on r_shadow when r_top is disabled
    cfg.rules[0].enabled = False
    t_excl = trace_file("March_Invoice_draft.pdf", cfg)
    assert t_excl.blocked_rules.get("r_shadow") == "draft"

    # Step 3: Type bucket fallback
    t_type = trace_file("vacation_photo.png", cfg)
    assert t_type.winning_step == 3
    assert t_type.outcome == "move"
    assert t_type.matched_bucket_id == "t-imgs"


def test_teach_cues_and_rule_builder(tmp_path: Path) -> None:
    sample = tmp_path / "Acme_Invoice_2026.pdf"
    sample.write_bytes(b"%PDF-1.4")

    cues = infer_teach_cues(sample.name, source_url="https://billing.stripe.com/receipt/123")
    assert cues["ext"] == ".pdf"
    assert cues["host"] == "billing.stripe.com"
    word_values = {c["value"] for c in cues["word_cues"]}
    assert "acme" in word_values
    assert "invoice" in word_values

    rule = build_rule_from_cues(
        title="Stripe Invoices",
        destination="Finance\\Invoices",
        words=["invoice"],
        extensions=[".pdf"],
        domains=["stripe.com"],
    )
    assert rule.destination == "Finance\\Invoices"
    assert "stripe.com" in rule.domains
    assert "invoice" in rule.keywords


def test_sorting_flow_tab_live_trace_focus_teach_and_activity_trace(
    v3_qapp,
    app_paths: AppPaths,
) -> None:
    from download_sorter.ui.main_window import MainWindow

    cfg = Config.with_starter_defaults()
    cfg.rules.insert(
        0,
        Rule(
            id="r_inv",
            name="Stripe Invoices",
            destination="Finance\\Invoices",
            keywords=["invoice"],
            domains=["stripe.com"],
            enabled=True,
        ),
    )
    save_config(cfg, app_paths)

    svc = SorterService(app_paths)
    win = MainWindow(svc)
    try:
        win.show()
        v3_qapp.processEvents()

        # 3-tab structure with Sorting Flow first
        assert win.tabs.count() == 3
        assert win.tabs.tabText(0) == "Sorting Flow"
        assert win.tabs.tabText(1) == "Activity"
        assert win.tabs.tabText(2) == "Preferences"
        assert hasattr(win, "btn_watch_switcher")

        # Live trace updates
        win.flow_tab.tester_bar.set_test_file("March_Invoice.pdf", "https://stripe.com/docs")
        v3_qapp.processEvents()
        assert win.flow_tab._last_trace is not None
        assert win.flow_tab._last_trace.matched_rule_id == "r_inv"
        assert win.flow_tab._last_trace.dest_folder == "Finance\\Invoices"

        # Folder focus mode and Escape/Reset clear
        win.flow_tab._on_folder_clicked("Finance\\Invoices")
        v3_qapp.processEvents()
        assert win.flow_tab._focused_folder == "Finance\\Invoices"
        win.flow_tab.clear_folder_focus()
        v3_qapp.processEvents()
        assert win.flow_tab._focused_folder is None

        # Teach-by-Example drop onto a folder creates a teach draft and commits a rule
        sample = app_paths.downloads_dir / "Receipt_Oct_2026.pdf"
        sample.write_bytes(b"receipt")
        win.flow_tab._on_file_dropped_on_folder(sample, "Finance\\Receipts")
        v3_qapp.processEvents()
        assert win.flow_tab.spine.has_teach_draft() is True

        new_rule = Rule(
            id="r_taught",
            name="Receipts",
            destination="Finance\\Receipts",
            keywords=["receipt"],
            enabled=True,
        )
        win.flow_tab._on_teach_committed(new_rule, sample, False)
        v3_qapp.processEvents()
        assert any(r.id == "r_taught" for r in svc.config.rules)

        # Activity -> Trace in Flow jumps back to Sorting Flow with that file loaded
        win.tabs.setCurrentWidget(win._home_tab)
        v3_qapp.processEvents()
        win.flow_tab.trace_from_activity("custom_trace_file.pdf", "https://example.org/file")
        win.tabs.setCurrentWidget(win.flow_tab)
        v3_qapp.processEvents()
        fname, url, _ = win.flow_tab.tester_bar.current_inputs()
        assert fname == "custom_trace_file.pdf"
        assert url == "https://example.org/file"
    finally:
        win.close()


@pytest.mark.shipped_defaults
def test_v4_clean_install_minimal_defaults(v3_qapp, app_paths: AppPaths) -> None:
    from download_sorter.ui.main_window import MainWindow

    save_config(Config.new_install(), app_paths)
    svc = SorterService(app_paths)
    win = MainWindow(svc)
    try:
        win.show()
        v3_qapp.processEvents()

        # Clean install is a 2-step flow (Holds & Grouping strictly opt-in)
        assert svc.config.hold_3mf_enabled is False
        assert svc.config.project_grouping_enabled is False
        assert win.flow_tab.spine.step1_row.isHidden() is True
        assert win.flow_tab.spine.badge_2.text() == "1"
        assert win.flow_tab.spine.badge_3.text() == "2"

        # Universal starter tree hides 3D Models & CAD on a clean install
        assert "Documents" in win.flow_tab.tree._row_widgets
        assert "3D Models" not in win.flow_tab.tree._row_widgets
        assert "CAD" not in win.flow_tab.tree._row_widgets

        # Opting into .3mf holds in Preferences promotes Sorting Flow cleanly to 3 steps
        win.chk_hold_3mf.setChecked(True)
        win._save_settings_form()
        v3_qapp.processEvents()
        assert svc.config.hold_3mf_enabled is True
        assert win.flow_tab.spine.step1_row.isHidden() is False
        assert win.flow_tab.spine.badge_1.text() == "1"
        assert win.flow_tab.spine.badge_2.text() == "2"
        assert win.flow_tab.spine.badge_3.text() == "3"
    finally:
        win.close()


def test_v4_preferences_sidebar_disk_sync_and_align_dialog(v3_qapp, app_paths: AppPaths) -> None:
    from download_sorter.ui.flow.align_dialog import AlignPreviewDialog
    from download_sorter.ui.main_window import MainWindow

    dl = app_paths.downloads_dir
    assert dl is not None
    f_pdf = dl / "Statement_2026.pdf"
    f_png = dl / "Screenshot_1.png"
    f_pdf.write_bytes(b"%PDF-1.4")
    f_png.write_bytes(b"PNG")
    custom_dir = dl / "MyCustomFolder"
    custom_dir.mkdir()
    (custom_dir / "keep.txt").write_text("hi", encoding="utf-8")

    save_config(Config.new_install(), app_paths)
    svc = SorterService(app_paths)
    win = MainWindow(svc)
    try:
        win.show()
        v3_qapp.processEvents()

        # 2-pane Preferences sidebar navigation and auto_align_existing persistence
        win.tabs.setCurrentWidget(win._settings_tab)
        for idx, key in enumerate(("general", "sorting", "notifications", "advanced")):
            win.select_preferences_section(key)
            assert win._prefs_stack.currentIndex() == idx
            assert win._prefs_nav_buttons[key].isChecked() is True

        win.chk_auto_align_existing.setChecked(True)
        win._save_settings_form()
        assert svc.config.auto_align_existing is True

        # FolderTreePanel live disk alignment bar + custom folder listing
        win.flow_tab._on_disk_scan_ready(
            {
                "scanned": True,
                "loose_count": 2,
                "existing_folders": {"MyCustomFolder": 1},
                "custom_top_folders": [("MyCustomFolder", 1)],
            }
        )
        v3_qapp.processEvents()
        assert "2 loose files" in win.flow_tab.tree.lbl_sync_status.text()
        assert win.flow_tab.tree.btn_align_now.isHidden() is False
        assert "MyCustomFolder" in win.flow_tab.tree._row_widgets

        win.flow_tab._on_disk_scan_ready(
            {
                "scanned": True,
                "loose_count": 0,
                "existing_folders": {"Documents": 1, "Documents\\PDF": 1},
                "custom_top_folders": [],
            }
        )
        v3_qapp.processEvents()
        assert "In sync" in win.flow_tab.tree.lbl_sync_status.text()
        assert win.flow_tab.tree.btn_align_now.isHidden() is True

        # AlignPreviewDialog selective filtering
        sim = svc.simulate_top_level()
        dlg = AlignPreviewDialog(dl, sim, parent=win)
        assert len(dlg.selected_items()) == 2
        dlg._checks[0][0].setChecked(False)
        assert len(dlg.selected_items()) == 1
        assert "Move 1 File" in dlg.btn_confirm.text()
        dlg.close()

        # Double-clicking tbl_sim traces the file in Sorting Flow
        win.tabs.setCurrentWidget(win._home_tab)
        win._run_simulation(sim)
        v3_qapp.processEvents()
        win._on_sim_trace_row(win.tbl_sim.model().index(0, 0))
        v3_qapp.processEvents()
        assert win.tabs.currentWidget() is win.flow_tab

        # Every Sorting Flow -> MainWindow / ArchitectureTab action target exists and is callable
        for attr in (
            "open_ai_insights_dialog",
            "_on_sort_tiers_now",
            "_on_sort_now",
            "_open_align_preview_dialog",
            "open_carry_over_dialog",
        ):
            assert callable(getattr(win, attr))
        for attr in (
            "sync_from_service",
            "_on_scan_folders",
            "_on_detect_projects_for_selected",
            "_open_snapshots_dialog",
            "_on_merge_selected_clicked",
            "_delete_node_by_id",
            "_edit_node_by_id",
            "_on_return_to_defaults",
        ):
            assert callable(getattr(win.arch_tab, attr))
    finally:
        win.close()


def test_scan_folder_architecture_prevents_small_folder_extension_hijack(tmp_path: Path) -> None:
    from download_sorter.architecture import apply_architecture_to_config, scan_folder_architecture
    from download_sorter.router import FileCandidate, evaluate

    # Dominant type folder has 10 .3mf files; small project folder has 2 .3mf files with no shared keywords
    m3d = tmp_path / "3D Models" / "3MF"
    m3d.mkdir(parents=True)
    for i in range(10):
        (m3d / f"model_{i}.3mf").write_bytes(b"3mf")
    proj = tmp_path / "CAD" / "Cast in Steel"
    proj.mkdir(parents=True)
    (proj / "alpha.3mf").write_bytes(b"3mf")
    (proj / "beta.3mf").write_bytes(b"3mf")

    scanned = scan_folder_architecture(tmp_path)["architecture"]
    cfg = apply_architecture_to_config(scanned, Config.new_install())

    dec = evaluate(FileCandidate("random_plate.3mf"), cfg)
    assert dec.relative_dest is not None
    assert str(dec.relative_dest.parent).replace("/", "\\") == "3D Models\\3MF"


def test_scan_and_shift_protect_game_bundles_extension_ids_and_curated_folders(
    app_paths: AppPaths,
) -> None:
    from download_sorter.architecture import apply_architecture_to_config, scan_folder_architecture
    from download_sorter.router import FileCandidate, evaluate

    dl = app_paths.downloads_dir
    assert dl is not None

    # 1. Installed game bundle under Games/ must never be raided or scanned into rules
    game_dir = dl / "Games" / "SampleGame"
    game_dir.mkdir(parents=True)
    (game_dir / "SampleGame.exe").write_bytes(b"MZ")
    (game_dir / "ReadMe.txt").write_text("do not move", encoding="utf-8")
    (game_dir / "Icon.ico").write_bytes(b"ico")

    # 2. Curated topic subfolder with PDFs (some lacking inferred keywords) + generic PDF bucket
    school = dl / "Documents" / "School & Coursework"
    school.mkdir(parents=True)
    (school / "homework_1.pdf").write_bytes(b"%PDF")
    (school / "homework_2.pdf").write_bytes(b"%PDF")
    (school / "Capacitance_Lab_Report.pdf").write_bytes(b"%PDF")

    pdf_dir = dl / "Documents" / "PDF"
    pdf_dir.mkdir(parents=True)
    (pdf_dir / "general_1.pdf").write_bytes(b"%PDF")
    (pdf_dir / "general_2.pdf").write_bytes(b"%PDF")
    (pdf_dir / "homework_3_from_pdf.pdf").write_bytes(b"%PDF")

    # 3. Standard Installers root with a couple of setup .exes still routes ALL .exe files
    inst = dl / "Installers"
    inst.mkdir(parents=True)
    (inst / "setup_a.exe").write_bytes(b"MZ")
    (inst / "setup_b.exe").write_bytes(b"MZ")

    scanned = scan_folder_architecture(dl)["architecture"]
    svc = SorterService(app_paths)
    try:
        target_cfg = apply_architecture_to_config(scanned, Config(rules=[], hold_3mf_enabled=False))

        # Any .exe still routes to Installers (Tier-1 category_map preserved)
        exe_dec = evaluate(FileCandidate("random_tool_v2.exe"), target_cfg)
        assert exe_dec.relative_dest is not None
        assert str(exe_dec.relative_dest.parent).replace("/", "\\") == "Installers"

        plan = svc.preview_architecture_shift(target_cfg, root=dl)
        planned_srcs = {m["src"].name for m in plan["moves_planned"]}

        # Game files untouched; curated Capacitance_Lab_Report.pdf NOT demoted to Documents\PDF;
        # matching homework_3_from_pdf.pdf UPGRADED from Documents\PDF -> Documents\School & Coursework!
        assert "ReadMe.txt" not in planned_srcs
        assert "Icon.ico" not in planned_srcs
        assert "Capacitance_Lab_Report.pdf" not in planned_srcs
        assert "homework_3_from_pdf.pdf" in planned_srcs
    finally:
        svc.stop()


def test_bridge_overlay_deleted_widget_crash_regression_and_mactoggle_sync(
    v3_qapp,
    app_paths: AppPaths,
) -> None:
    from PySide6.QtWidgets import QFrame
    from shiboken6 import delete as qt_delete

    from download_sorter.ui.flow.bridge_overlay import BridgeWire
    from download_sorter.ui.main_window import MainWindow
    from download_sorter.ui.settings_widgets import MacToggle

    # 1. MacToggle must reflect setChecked(True) even when blockSignals(True) is active
    sw = MacToggle(quiet=True)
    sw.blockSignals(True)
    sw.setChecked(True)
    sw.blockSignals(False)
    sw.grab()
    assert sw._thumb_pos == 1.0

    # 2. BridgeOverlay.paintEvent must never raise RuntimeError or crash when a C++ QFrame is deleted
    save_config(Config.new_install(), app_paths)
    svc = SorterService(app_paths)
    win = MainWindow(svc)
    try:
        win.show()
        v3_qapp.processEvents()

        ghost_src = QFrame(win.flow_tab.workspace)
        ghost_tgt = QFrame(win.flow_tab.workspace)
        ghost_src.show()
        ghost_tgt.show()
        win.flow_tab.overlay._wires = [BridgeWire(source=ghost_src, target=ghost_tgt)]
        win.flow_tab.overlay.set_drag_wire(ghost_src, snap_target=ghost_tgt)

        # Destroy underlying C++ QFrames before paintEvent runs
        qt_delete(ghost_src)
        qt_delete(ghost_tgt)

        # Must silently prune dead wrappers and end QPainter cleanly
        win.flow_tab.overlay.grab()
        win.flow_tab.refresh()
        v3_qapp.processEvents()
        assert win.flow_tab.overlay._wires == [] or all(
            w.source is not None for w in win.flow_tab.overlay._wires
        )
    finally:
        win.close()
        svc.stop()


def test_sorting_flow_drag_and_drop_adjustments(
    v3_qapp,
    app_paths: AppPaths,
) -> None:
    from PySide6.QtCore import QMimeData, QPointF, Qt
    from PySide6.QtGui import QDropEvent

    from download_sorter.router import FileCandidate, evaluate
    from download_sorter.ui.flow.folder_tree_panel import MIME_FOLDER_PATH
    from download_sorter.ui.flow.spine_panel import MIME_BUCKET_ID, MIME_RULE_ID
    from download_sorter.ui.main_window import MainWindow

    cfg = Config.new_install()
    cfg.rules = [
        Rule(id="r_a", name="Broad Invoices", destination="Documents", keywords=["invoice"], enabled=True),
        Rule(id="r_b", name="Stripe Invoices", destination="Documents\\PDF", keywords=["invoice", "stripe"], enabled=True),
    ]
    save_config(cfg, app_paths)

    svc = SorterService(app_paths)
    win = MainWindow(svc)
    try:
        win.show()
        v3_qapp.processEvents()
        flow = win.flow_tab

        # 1. Reorder rule via QDropEvent (drop r_b onto upper half of r_a -> r_b moves first)
        row_a = flow.spine.rules_layout.itemAt(0).widget()
        assert row_a is not None
        mime_reorder = QMimeData()
        mime_reorder.setData(MIME_RULE_ID, b"r_b")
        drop_ev = QDropEvent(
            QPointF(10.0, 2.0),
            Qt.DropAction.MoveAction,
            mime_reorder,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        row_a.dropEvent(drop_ev)
        v3_qapp.processEvents()
        v3_qapp.processEvents()
        assert [r.id for r in svc.config.rules] == ["r_b", "r_a"]

        # 2. Drag a Rule onto a Folder in the Downloads tree to re-route its destination
        target_row = flow.tree.folder_widget("Archives")
        assert target_row is not None
        mime_route_rule = QMimeData()
        mime_route_rule.setData(MIME_RULE_ID, b"r_b")
        drop_rule_folder = QDropEvent(
            QPointF(12.0, 12.0),
            Qt.DropAction.MoveAction,
            mime_route_rule,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        target_row.dropEvent(drop_rule_folder)
        v3_qapp.processEvents()
        v3_qapp.processEvents()
        assert svc.config.rules[0].destination == "Archives"

        # 3. Drag a File-Type bucket (Images -> t-imgs) onto a subfolder (Documents\Word)
        sub_row = flow.tree.folder_widget("Documents\\Word")
        assert sub_row is not None
        mime_bucket = QMimeData()
        mime_bucket.setData(MIME_BUCKET_ID, b"t-imgs")
        drop_bucket = QDropEvent(
            QPointF(12.0, 12.0),
            Qt.DropAction.MoveAction,
            mime_bucket,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        sub_row.dropEvent(drop_bucket)
        v3_qapp.processEvents()
        v3_qapp.processEvents()
        dec_img = evaluate(FileCandidate("photo.png"), svc.config)
        assert dec_img.relative_dest is not None
        assert str(dec_img.relative_dest.parent).replace("/", "\\") == "Documents\\Word"

        # Reset bucket back to default Images
        assert flow.reset_bucket_route("t-imgs") is True
        v3_qapp.processEvents()
        dec_img_reset = evaluate(FileCandidate("photo.png"), svc.config)
        assert dec_img_reset.relative_dest is not None
        assert str(dec_img_reset.relative_dest.parent).replace("/", "\\").startswith("Images")

        # 4. Drag a virtual subfolder (Documents\Presentations) onto Downloads header to promote to top-level,
        #    then nest it inside Archives via folder-on-folder drop!
        mime_promote = QMimeData()
        mime_promote.setData(MIME_FOLDER_PATH, b"Documents\\Presentations")
        drop_promote = QDropEvent(
            QPointF(10.0, 8.0),
            Qt.DropAction.MoveAction,
            mime_promote,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        flow.tree.hdr_drop.dropEvent(drop_promote)
        v3_qapp.processEvents()
        v3_qapp.processEvents()
        assert "Presentations" in flow.tree._row_widgets

        arch_row = flow.tree.folder_widget("Archives")
        assert arch_row is not None
        mime_nest = QMimeData()
        mime_nest.setData(MIME_FOLDER_PATH, b"Presentations")
        drop_nest = QDropEvent(
            QPointF(10.0, 10.0),
            Qt.DropAction.MoveAction,
            mime_nest,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        arch_row.dropEvent(drop_nest)
        v3_qapp.processEvents()
        v3_qapp.processEvents()
        assert "Archives\\Presentations" in flow.tree._row_widgets
    finally:
        win.close()
        svc.stop()

