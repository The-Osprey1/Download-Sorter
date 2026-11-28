"""Tests for Folder Architecture progressive disclosure, connected box roles, and Phase 1 local ML."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QWidget

from download_sorter import about, router
from download_sorter.architecture import (
    scan_folder_architecture,
)
from download_sorter.config import Config, Rule, load_config, save_config
from download_sorter.learning import (
    TokenRouteLearner,
    get_ai_model_summary,
    get_rule_suggestions,
    record_manual_route,
)
from download_sorter.paths import AppPaths
from download_sorter.service import SorterService
from download_sorter.ui import motion
from download_sorter.ui.main_window import (
    ExperimentalAIInsightsDialog,
    MainWindow,
    RuleEditorDialog,
)


def _mouse_event(etype: QEvent.Type, pos: QPointF, btn: Qt.MouseButton = Qt.MouseButton.LeftButton) -> QMouseEvent:
    btns = btn if etype != QEvent.Type.MouseButtonRelease else Qt.MouseButton.NoButton
    return QMouseEvent(etype, pos, pos, btn, btns, Qt.KeyboardModifier.NoModifier)


def _click_canvas(canvas, pt: QPointF) -> None:  # noqa: ANN001
    canvas.mousePressEvent(_mouse_event(QEvent.Type.MouseButtonPress, pt))
    canvas.mouseReleaseEvent(_mouse_event(QEvent.Type.MouseButtonRelease, pt))




# ---------------------------------------------------------------------------
# 1. Progressive Disclosure (Click Tier 1 -> show only its lower tiers)
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# 2. Connected Box Visual Roles & Rendering
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# 3. Phase 1 Local Statistical ML (TokenRouteLearner + TF-IDF Scan Folders)
# ---------------------------------------------------------------------------

def test_token_route_learner_suggestions_and_predictions(app_paths: AppPaths) -> None:
    cfg = Config.with_starter_defaults()
    dd = app_paths.data_dir

    # Record repeated manual routes with filename tokens across different hosts
    for fname in ("acme_receipt_jan.pdf", "coffee_receipt_feb.pdf", "hotel_receipt_mar.pdf"):
        record_manual_route(
            destination=r"Finance\Receipts",
            data_dir=dd,
            filename=fname,
        )
    for fname in ("physics_syllabus_fall.pdf", "chemistry_syllabus_spring.pdf"):
        record_manual_route(
            destination=r"School\Courses",
            data_dir=dd,
            filename=fname,
        )

    suggestions = get_rule_suggestions(cfg, dd, threshold=2)
    kw_suggestions = [s for s in suggestions if s.get("kind") == "keyword"]
    assert any(s["keyword"] == "receipt" and s["destination"] == r"Finance\Receipts" for s in kw_suggestions)
    assert any(s["keyword"] == "syllabus" and s["destination"] == r"School\Courses" for s in kw_suggestions)

    # Train statistical learner from persisted store and verify explainable prediction
    learner = TokenRouteLearner()
    assert learner.train_from_store(dd) == 5

    pred = learner.predict_folder("airline_receipt_apr.pdf")
    assert pred is not None
    assert pred.destination == r"Finance\Receipts"
    assert pred.confidence >= 0.60
    assert any("receipt" in r for r in pred.top_reasons)

    # Unseen / ambiguous filename should return None rather than guessing wildly
    unknown = learner.predict_folder("totally_unrelated_xyz.bin")
    assert unknown is None


def test_scan_folder_architecture_tfidf_ranks_distinctive_keywords(app_paths: AppPaths) -> None:
    dl = app_paths.downloads_dir
    f_tax = dl / "Taxes"
    f_tax.mkdir(parents=True, exist_ok=True)
    for i in range(3):
        (f_tax / f"deduction_report_{i}.pdf").write_bytes(b"%PDF-1.4 tax")

    f_eng = dl / "Engineering"
    f_eng.mkdir(parents=True, exist_ok=True)
    for i in range(3):
        (f_eng / f"schematic_report_{i}.pdf").write_bytes(b"%PDF-1.4 eng")

    res = scan_folder_architecture(dl)
    arch = res["architecture"]
    tax_node = arch.find_by_name_and_tier("Taxes", 1)
    eng_node = arch.find_by_name_and_tier("Engineering", 1)
    assert tax_node is not None and eng_node is not None
    # Distinctive per-folder tokens should rank first; shared token 'report' has 50% purity and lower IDF
    assert tax_node.keywords and tax_node.keywords[0] == "deduction"
    assert eng_node.keywords and eng_node.keywords[0] == "schematic"


# ---------------------------------------------------------------------------
# 4. Multi-Row Bus Routing, Collapse Affordance & End-to-End ML Wiring
# ---------------------------------------------------------------------------





def test_accept_keyword_rule_suggestion_and_service_explain_smart_hint(
    v3_qapp, app_paths: AppPaths, monkeypatch  # noqa: ANN001
) -> None:
    for fname in ("acme_receipt_jan.xyz", "coffee_receipt_feb.xyz", "hotel_receipt_mar.xyz"):
        record_manual_route(
            destination=r"Finance\Receipts",
            data_dir=app_paths.data_dir,
            filename=fname,
        )
    for fname in ("alpha_manual_1.xyz", "beta_manual_2.xyz"):
        record_manual_route(
            destination=r"Manuals",
            data_dir=app_paths.data_dir,
            filename=fname,
        )

    save_config(Config.with_starter_defaults(), app_paths)
    svc = SorterService(app_paths)
    try:
        # 1. SorterService.explain() appends Smart Hint on fallback matches
        dec = svc.explain("airline_receipt_apr.xyz")
        assert dec.match_type == "fallback"
        assert any(step.stage == "Smart Hint" and "Finance/Receipts" in step.detail for step in dec.trace)

        # 2. MainWindow._accept_rule_suggestion pre-fills keyword rule from learned suggestion
        win = MainWindow(svc)
        try:
            win.show()
            v3_qapp.processEvents()
            assert win._current_suggestion is not None
            assert win._current_suggestion.get("keyword") == "receipt"

            captured: dict[str, object] = {}

            def _fake_exec(dlg_self: RuleEditorDialog) -> int:
                draft_rule = dlg_self.build_rule()
                captured["name"] = draft_rule.name
                captured["keywords"] = list(draft_rule.keywords)
                captured["destination"] = draft_rule.destination
                return QDialog.DialogCode.Accepted

            monkeypatch.setattr(RuleEditorDialog, "exec", _fake_exec)
            win._accept_rule_suggestion()
            assert captured["name"] == "Files with 'receipt'"
            assert captured["keywords"] == ["receipt"]
            assert captured["destination"] == r"Finance\Receipts"
        finally:
            win.close()
    finally:
        svc.stop(timeout=1.0)


# ---------------------------------------------------------------------------
# 5. Fluid Animations, Visible Version Badge & Subfolder Merging
# ---------------------------------------------------------------------------

def test_fade_in_detaches_opacity_effect_and_version_badges_visible(
    v3_qapp, app_paths: AppPaths, monkeypatch  # noqa: ANN001
) -> None:
    monkeypatch.setattr(motion, "animations_enabled", lambda: True)
    w = QWidget()
    w.resize(120, 60)
    w.show()
    motion.fade_in(w, duration=20)
    assert w.graphicsEffect() is not None
    QTest.qWait(70)
    v3_qapp.processEvents()
    assert w.graphicsEffect() is None
    w.close()

    save_config(Config.with_starter_defaults(), app_paths)
    svc = SorterService(app_paths)
    win = MainWindow(svc)
    try:
        win.show()
        v3_qapp.processEvents()
        expected_v = f"v{about.__version__}"
        assert expected_v in win.windowTitle()
        assert win.lbl_version_badge.text() == expected_v
        assert win.lbl_footer_version.text() == expected_v
        # Version badge stays visible even when window shrinks narrow
        win.resize(900, 600)
        v3_qapp.processEvents()
        assert win.lbl_version_badge.isVisibleTo(win)
    finally:
        win.close()
        svc.stop(timeout=1.0)




# ---------------------------------------------------------------------------
# 6. Subfolder Removal Confirmation ("Are You Sure?") & Experimental AI Card
# ---------------------------------------------------------------------------



def test_experimental_ai_settings_auto_route_insights_and_reset(
    v3_qapp, app_paths: AppPaths  # noqa: ANN001
) -> None:
    cfg = Config.with_starter_defaults()
    cfg.fallback_mode = "leave"
    save_config(cfg, app_paths)

    for idx in range(4):
        record_manual_route(
            destination=r"Finance\Receipts",
            data_dir=app_paths.data_dir,
            source_url="https://billing.example.com/inv.bin",
            ext=".bin",
            filename=f"receipt_invoice_{idx}.bin",
        )

    dl = app_paths.downloads_dir
    unmatched_file = dl / "uber_receipt_oct.bin"
    unmatched_file.write_bytes(b"receipt-data")

    svc = SorterService(app_paths)
    win = MainWindow(svc)
    try:
        # Enable Experimental AI Auto-Route & adjust threshold via Settings controls
        win.chk_ai_smart_hints.setChecked(True)
        win.chk_ai_auto_route.setChecked(True)
        win.spin_ai_confidence.setValue(60)
        win.chk_ai_scan_keywords.setChecked(False)
        win._save_settings_form()

        reloaded = load_config(app_paths)
        assert reloaded.ai_smart_hints_enabled is True
        assert reloaded.ai_auto_route_fallback is True
        assert reloaded.ai_min_confidence == 60
        assert reloaded.ai_smart_scan_keywords is False
        assert win.lbl_ai_badge.text() == "AUTO-ROUTE ON"

        # Explain & scan_top_level_now upgrade fallback to ai_smart_route when enabled
        dec = svc.explain("uber_receipt_oct.bin", "https://billing.example.com/oct")
        assert dec.action == "move"
        assert dec.match_type == "ai_smart_route"
        assert str(dec.relative_dest).startswith(r"Finance\Receipts")

        res = svc.scan_top_level_now(settle_seconds=0.0)
        assert len(res["moved"]) == 1
        assert (dl / "Finance" / "Receipts" / "uber_receipt_oct.bin").is_file()

        # Insights dialog inspects live model stats & clears learned memory cleanly
        extra_file = dl / "receipt_taxi.bin"
        extra_file.write_bytes(b"taxi")
        dlg = ExperimentalAIInsightsDialog(svc, parent=win)
        assert "5 moves" in dlg.lbl_model_pill.text()
        assert dlg.tbl_predictions.rowCount() == 1
        assert "Finance/Receipts" in dlg.tbl_predictions.item(0, 1).text()
        assert dlg.reset_memory(confirm=False) is True
        assert get_rule_suggestions(reloaded, app_paths.data_dir) == []
        summary = get_ai_model_summary(svc.db, app_paths.data_dir)
        assert summary["sample_count"] == 1  # only the 1 StateDB move remains after clearing manual memory
        dlg.close()

        plain_scan = scan_folder_architecture(dl, use_tfidf=False)
        assert plain_scan["folder_count"] >= 1
    finally:
        win.close()
        svc.stop(timeout=1.0)


# ---------------------------------------------------------------------------
# 7. Lazy Candidate Evaluation Parity & Precomputed Plan Reuse
# ---------------------------------------------------------------------------

def test_lazy_candidate_evaluation_parity_and_precomputed_plan_reuse(
    app_paths: AppPaths, monkeypatch  # noqa: ANN001
) -> None:
    dl = app_paths.downloads_dir
    (dl / "Other").mkdir(parents=True, exist_ok=True)
    xyz_file = dl / "Other" / "patch_001.xyz"
    xyz_file.write_bytes(b"synth")
    pdf_file = dl / "model_notes.pdf"
    pdf_file.write_bytes(b"%PDF-1.4")
    syll_file = dl / "course_syllabus_fall.pdf"
    syll_file.write_bytes(b"%PDF-1.4")

    cfg = Config.with_starter_defaults(templates=())
    cfg.rules = [
        Rule(
            id="r-dom",
            name="MakerWorld PDFs",
            destination=r"Documents\MakerDocs",
            domains=["makerworld.com"],
            extensions=[".pdf"],
        ),
        Rule(
            id="r-kw",
            name="Syllabi",
            destination=r"Documents\Syllabi",
            keywords=["syllabus"],
            extensions=[".pdf"],
        ),
    ]
    save_config(cfg, app_paths)

    zone_reads: list[str] = []

    def _fake_zone(p) -> str:  # noqa: ANN001
        zone_reads.append(p.name)
        return "https://makerworld.com/en/models/42" if p.name == "model_notes.pdf" else ""

    monkeypatch.setattr(router, "read_zone_url", _fake_zone)

    # 1. Lazy Zone.Identifier defers ADS reads unless domain rule could actually apply
    c_xyz_lazy = router.candidate_from_path(xyz_file, lazy_zone=True)
    d_xyz_lazy = router.evaluate(c_xyz_lazy, cfg)
    assert zone_reads == []  # .xyz failed extension check before inspecting Zone.Identifier
    c_xyz_eager = router.candidate_from_path(xyz_file, lazy_zone=False)
    d_xyz_eager = router.evaluate(c_xyz_eager, cfg)
    assert (d_xyz_lazy.action, d_xyz_lazy.relative_dest, d_xyz_lazy.matched_rule) == (
        d_xyz_eager.action,
        d_xyz_eager.relative_dest,
        d_xyz_eager.matched_rule,
    )

    zone_reads.clear()
    c_pdf_lazy = router.candidate_from_path(pdf_file, lazy_zone=True)
    d_pdf_lazy = router.evaluate(c_pdf_lazy, cfg)
    assert zone_reads == ["model_notes.pdf"]
    assert d_pdf_lazy.action == "move"
    assert str(d_pdf_lazy.relative_dest) == r"Documents\MakerDocs\model_notes.pdf"

    zone_reads.clear()
    c_syll_lazy = router.candidate_from_path(syll_file, lazy_zone=True)
    d_syll_lazy = router.evaluate(c_syll_lazy, cfg)
    c_syll_eager = router.candidate_from_path(syll_file, lazy_zone=False)
    d_syll_eager = router.evaluate(c_syll_eager, cfg)
    assert (d_syll_lazy.action, d_syll_lazy.relative_dest, d_syll_lazy.matched_rule) == (
        d_syll_eager.action,
        d_syll_eager.relative_dest,
        d_syll_eager.matched_rule,
    )

    # 2. Progress reporting + precomputed_plan reuse avoids scanning twice on Apply
    svc = SorterService(app_paths)
    try:
        ticks: list[tuple[int, int, str]] = []
        plan = svc.preview_architecture_shift(
            cfg, on_progress=lambda done, total, label: ticks.append((done, total, label))
        )
        assert len(ticks) >= 2
        assert len(plan["moves_planned"]) == 2

        monkeypatch.setattr(
            svc,
            "preview_architecture_shift",
            lambda *a, **kw: (_ for _ in ()).throw(AssertionError("Should reuse precomputed_plan")),
        )
        apply_res = svc.apply_architecture_and_shift(cfg, precomputed_plan=plan)
        assert apply_res["shifted_count"] == 2
        assert (dl / "Documents" / "MakerDocs" / "model_notes.pdf").is_file()
        assert (dl / "Documents" / "Syllabi" / "course_syllabus_fall.pdf").is_file()
    finally:
        svc.stop(timeout=1.0)




def test_preview_architecture_shift_preserves_unmanaged_project_subfolders(app_paths: AppPaths) -> None:
    dl = app_paths.downloads_dir
    assert dl is not None

    cfg = Config.with_starter_defaults()
    cfg.use_type_subfolders = True
    save_config(cfg, app_paths)

    # Multi-file project / extracted app subfolders inside managed roots must NEVER be flattened
    ferrowaves = dl / "Code" / "FerroWave"
    ferrowaves.mkdir(parents=True, exist_ok=True)
    (ferrowaves / ".gitignore").write_text("build/\n", encoding="utf-8")
    (ferrowaves / "readme_asset.str").write_text("data", encoding="utf-8")
    (ferrowaves / "diagram.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    extracted = dl / "Installers" / "_Extracted"
    extracted.mkdir(parents=True, exist_ok=True)
    (extracted / "data.meta").write_text("meta", encoding="utf-8")

    # Misplaced file directly inside managed Tier 1 Documents MUST still shift into Documents/PDF
    misplaced = dl / "Documents" / "misplaced.pdf"
    misplaced.parent.mkdir(parents=True, exist_ok=True)
    misplaced.write_bytes(b"%PDF-1.4\n")

    # Unknown partial download with PDF bytes must still be ignored (not sniff-routed)
    crdownload = dl / "partial_manual.download"
    crdownload.write_bytes(b"%PDF-1.7\n")

    svc = SorterService(app_paths)
    try:
        plan = svc.preview_architecture_shift(cfg)
        planned_names = {m["src"].name for m in plan["moves_planned"]}
        assert "misplaced.pdf" in planned_names
        assert "readme_asset.str" not in planned_names
        assert "diagram.png" not in planned_names
        assert "data.meta" not in planned_names
        assert "partial_manual.download" not in planned_names
    finally:
        svc.stop(timeout=1.0)
