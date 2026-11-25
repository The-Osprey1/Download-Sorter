"""Tests for Download Sorter v4.0 Sorting Flow pipeline and unified UI."""

from __future__ import annotations

from pathlib import Path

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
