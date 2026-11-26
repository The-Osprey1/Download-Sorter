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
