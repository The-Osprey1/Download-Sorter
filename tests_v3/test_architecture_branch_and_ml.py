"""Tests for Folder Architecture progressive disclosure, connected box roles, and Phase 1 local ML."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QComboBox, QDialog, QWidget

from download_sorter import about, router
from download_sorter.architecture import FolderArchitecture, get_tier2_suggestions, scan_folder_architecture
from download_sorter.config import Config, Rule, load_config, save_config
from download_sorter.learning import TokenRouteLearner, get_ai_model_summary, get_rule_suggestions, record_manual_route
from download_sorter.paths import AppPaths
from download_sorter.service import SorterService
from download_sorter.ui import motion
from download_sorter.ui.architecture_canvas import (
    ArchitectureTab,
    CustomFolderNodeDialog,
    DeleteFolderDialog,
    FolderArchitectureCanvas,
    MergeFolderDialog,
)
from download_sorter.ui.main_window import ExperimentalAIInsightsDialog, MainWindow, RuleEditorDialog


def _mouse_event(etype: QEvent.Type, pos: QPointF, btn: Qt.MouseButton = Qt.MouseButton.LeftButton) -> QMouseEvent:
    btns = btn if etype != QEvent.Type.MouseButtonRelease else Qt.MouseButton.NoButton
    return QMouseEvent(etype, pos, pos, btn, btns, Qt.KeyboardModifier.NoModifier)


def _click_canvas(canvas, pt: QPointF) -> None:  # noqa: ANN001
    canvas.mousePressEvent(_mouse_event(QEvent.Type.MouseButtonPress, pt))
    canvas.mouseReleaseEvent(_mouse_event(QEvent.Type.MouseButtonRelease, pt))


def _make_tab(app_paths: AppPaths) -> tuple[SorterService, ArchitectureTab]:
    cfg = Config.with_starter_defaults()
    save_config(cfg, app_paths)
    svc = SorterService(app_paths)
    tab = ArchitectureTab(svc)
    tab.resize(1020, 680)
    tab.show()
    return svc, tab


# ---------------------------------------------------------------------------
# 1. Progressive Disclosure (Click Tier 1 -> show only its lower tiers)
# ---------------------------------------------------------------------------

def test_progressive_disclosure_click_tier1_and_view_toggle(v3_qapp, app_paths: AppPaths) -> None:  # noqa: ANN001
    svc, tab = _make_tab(app_paths)
    try:
        v3_qapp.processEvents()
        canvas = tab.canvas
        arch = tab._arch

        # Default: Branch mode, collapsed -> no Tier 2+ nodes laid out yet
        assert canvas.expand_all_tiers() is False
        assert tab.btn_toggle_view.text() == "View: Branch"
        assert canvas.expanded_tier1_id() is None
        tier2_nodes = [n for n in arch.nodes if n.tier >= 2]
        assert tier2_nodes, "Starter architecture should include Tier 2 folders"
        for n in tier2_nodes:
            assert n.id not in canvas._node_rects

        # Find two Tier 1 parents that each have children
        t1_parents = [n for n in arch.nodes_in_tier(1) if arch.children_of(n.id)]
        assert len(t1_parents) >= 2
        p1, p2 = t1_parents[0], t1_parents[1]

        # Click p1 -> ONLY p1's descendants appear in _node_rects
        _click_canvas(canvas, canvas._node_rects[p1.id].center())
        v3_qapp.processEvents()
        assert canvas.expanded_tier1_id() == p1.id
        p1_kids = {c.id for c in arch.children_of(p1.id)}
        p2_kids = {c.id for c in arch.children_of(p2.id)}
        assert p1_kids.issubset(canvas._node_rects.keys())
        assert p2_kids.isdisjoint(canvas._node_rects.keys())

        # Click p2 -> switches cleanly to p2's descendants only
        _click_canvas(canvas, canvas._node_rects[p2.id].center())
        v3_qapp.processEvents()
        assert canvas.expanded_tier1_id() == p2.id
        assert p2_kids.issubset(canvas._node_rects.keys())
        assert p1_kids.isdisjoint(canvas._node_rects.keys())

        # Click Tier 0 Root -> collapses lower tiers
        _click_canvas(canvas, canvas._node_rects["__root__"].center())
        v3_qapp.processEvents()
        assert canvas.expanded_tier1_id() is None
        for n in tier2_nodes:
            assert n.id not in canvas._node_rects

        # Toggle View: All -> every Tier 2+ node is laid out
        tab.btn_toggle_view.click()
        v3_qapp.processEvents()
        assert canvas.expand_all_tiers() is True
        assert tab.btn_toggle_view.text() == "View: All"
        for n in tier2_nodes:
            assert n.id in canvas._node_rects

        # Toggle back to View: Branch
        tab.btn_toggle_view.click()
        v3_qapp.processEvents()
        assert canvas.expand_all_tiers() is False
        assert tab.btn_toggle_view.text() == "View: Branch"
    finally:
        tab.close()
        svc.stop(timeout=1.0)


# ---------------------------------------------------------------------------
# 2. Connected Box Visual Roles & Rendering
# ---------------------------------------------------------------------------

def test_connected_box_roles_for_parent_child_and_leaf(v3_qapp, app_paths: AppPaths) -> None:  # noqa: ANN001
    svc, tab = _make_tab(app_paths)
    try:
        v3_qapp.processEvents()
        canvas = tab.canvas
        arch = tab._arch

        t1_parents = [n for n in arch.nodes_in_tier(1) if arch.children_of(n.id)]
        t1_leaves = [n for n in arch.nodes_in_tier(1) if not arch.children_of(n.id)]
        assert t1_parents and t1_leaves
        parent = t1_parents[0]
        leaf = t1_leaves[0]
        child = arch.children_of(parent.id)[0]

        # Selecting parent lights up children and dims unrelated Tier 1 cards
        canvas.select_node(parent.id)
        v3_qapp.processEvents()
        focus_id, roles, accents = canvas._connection_roles()
        assert focus_id == parent.id
        assert roles[parent.id] == "focus"
        assert roles[child.id] == "connected_child"
        assert accents[child.id] == parent.accent_hex
        assert roles[leaf.id] == "dimmed"

        # Selecting child lights up parent as connected_parent and dims unrelated cards
        canvas.select_node(child.id)
        v3_qapp.processEvents()
        focus_id, roles, _accents = canvas._connection_roles()
        assert focus_id == child.id
        assert roles[child.id] == "focus"
        assert roles[parent.id] == "connected_parent"
        assert roles[leaf.id] == "dimmed"

        # Selecting a leaf with no connections must NOT dim other cards
        canvas.select_node(leaf.id)
        v3_qapp.processEvents()
        _fid, roles, _accents = canvas._connection_roles()
        assert roles[leaf.id] == "focus"
        assert "dimmed" not in roles.values()

        # Offscreen paint with active connections succeeds cleanly
        canvas.select_node(parent.id)
        pix = canvas.grab()
        assert not pix.isNull()
    finally:
        tab.close()
        svc.stop(timeout=1.0)


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

def test_multirow_bus_path_uses_channel_and_avoids_row0() -> None:
    p1 = QPointF(200.0, 320.0)
    p2 = QPointF(200.0, 520.0)
    bus_y = 340.0
    channel_x = 110.0
    row_bus_y = 505.0

    path = FolderArchitectureCanvas._build_orthogonal_bus_path(
        p1,
        p2,
        bus_y,
        row_idx=1,
        channel_x=channel_x,
        row_bus_y=row_bus_y,
    )
    pts = [path.elementAt(i) for i in range(path.elementCount())]
    # Path must visit the inter-column channel (x == 110) and must not drop straight at x=200 from bus_y
    assert any(abs(el.x - channel_x) < 1.0 for el in pts)
    assert any(abs(el.y - row_bus_y) < 1.0 for el in pts)


def test_collapse_pill_and_sub_badge_collapse_expanded_branch(v3_qapp, app_paths: AppPaths) -> None:  # noqa: ANN001
    svc, tab = _make_tab(app_paths)
    try:
        v3_qapp.processEvents()
        canvas = tab.canvas
        docs = next(n for n in tab._arch.nodes_in_tier(1) if tab._arch.children_of(n.id))

        # Expand Documents
        _click_canvas(canvas, canvas._node_rects[docs.id].center())
        v3_qapp.processEvents()
        assert canvas.expanded_tier1_id() == docs.id
        assert canvas._tier_collapse_rect is not None

        # Click 'Collapse ▴' pill in Tier 2 header
        _click_canvas(canvas, canvas._tier_collapse_rect.center())
        v3_qapp.processEvents()
        assert canvas.expanded_tier1_id() is None
        assert canvas._tier_collapse_rect is None

        # Expand again and click the 'N sub ▾' badge on the Documents card
        _click_canvas(canvas, canvas._node_rects[docs.id].center())
        v3_qapp.processEvents()
        assert docs.id in canvas._sub_pill_rects
        _click_canvas(canvas, canvas._sub_pill_rects[docs.id].center())
        v3_qapp.processEvents()
        assert canvas.expanded_tier1_id() is None
    finally:
        tab.close()
        svc.stop(timeout=1.0)


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


def test_subfolder_merge_model_disk_undo_dialog_and_shift_drag(
    v3_qapp, app_paths: AppPaths, monkeypatch  # noqa: ANN001
) -> None:
    # 1. Model-level merge combines rules, reparents children, and guards cycles
    arch = FolderArchitecture(tier_count=3)
    docs = arch.add_custom_node(name="Documents", tier=1, extensions=[".pdf"])
    bills = arch.add_custom_node(
        name="Bills",
        tier=2,
        parent_id=docs.id,
        extensions=[".pdf"],
        keywords=["bill"],
        domains=["utility.example"],
    )
    sub_b = arch.add_custom_node(name="2026", tier=3, parent_id=bills.id, keywords=["2026"])
    receipts = arch.add_custom_node(
        name="Receipts",
        tier=2,
        parent_id=docs.id,
        extensions=[".csv"],
        keywords=["receipt"],
        domains=["shop.example"],
    )

    assert arch.can_merge(docs.id, bills.id) is False
    assert arch.can_merge(bills.id, docs.id) is False
    assert arch.can_merge(bills.id, receipts.id) is True

    merged = arch.merge_nodes(bills.id, receipts.id, new_name="Bills & Receipts")
    assert merged is not None
    assert merged.id == receipts.id
    assert merged.name == "Bills & Receipts"
    assert arch.get_node(bills.id) is None
    assert set(merged.extensions) == {".csv", ".pdf"}
    assert set(merged.keywords) == {"receipt", "bill"}
    assert set(merged.domains) == {"shop.example", "utility.example"}
    assert sub_b.parent_id == receipts.id
    assert sub_b.tier == 3

    # 2. Disk-level merge moves files and supports Undo
    dl = app_paths.downloads_dir
    src_folder = dl / "Documents" / "Bills" / "Jan"
    src_folder.mkdir(parents=True, exist_ok=True)
    bill_file = src_folder / "electric_bill.pdf"
    bill_file.write_bytes(b"%PDF-bill")

    save_config(Config.with_starter_defaults(), app_paths)
    svc = SorterService(app_paths)
    try:
        res = svc.merge_folders_on_disk(r"Documents\Bills", r"Documents\Receipts")
        assert res["ok"] is True
        assert res["moved_count"] == 1
        target_file = dl / "Documents" / "Receipts" / "Jan" / "electric_bill.pdf"
        assert target_file.is_file()
        assert not (dl / "Documents" / "Bills").exists()

        undo_res = svc.undo_last_batch()
        assert undo_res["restored"] == 1
        assert bill_file.is_file()
        assert not target_file.is_file()
    finally:
        svc.stop(timeout=1.0)

    # 3. UI: MergeFolderDialog has zero QComboBox and merges via ArchitectureTab + Shift-drag halo
    svc2, tab = _make_tab(app_paths)
    try:
        v3_qapp.processEvents()
        docs_t1 = tab._arch.find_by_name_and_tier("Documents", 1)
        assert docs_t1 is not None
        kids = tab._arch.children_of(docs_t1.id)
        assert len(kids) >= 2
        src_k, dst_k = kids[0], kids[1]

        dlg = MergeFolderDialog(tab._arch, src_k, parent=tab)
        assert dlg.findChildren(QComboBox) == []
        assert dlg.selected_target_id() is not None
        dlg.close()

        tab.canvas.select_node(src_k.id)
        v3_qapp.processEvents()
        r_src = tab.canvas._node_rects[src_k.id]
        r_dst = tab.canvas._node_rects[dst_k.id]

        # Shift+Drag over another folder yields "merge" drop state
        tab.canvas._dragging_id = src_k.id
        tab.canvas._evaluate_reparent_hover(r_dst.center(), shift_held=True)
        assert tab.canvas._drop_target_id == dst_k.id
        assert tab.canvas._drop_state == "merge"
        tab.canvas._dragging_id = None
        tab.canvas._drop_target_id = None
        tab.canvas._drop_state = None

        # Perform merge via tab handler
        orig_dst_name = dst_k.name
        tab._on_nodes_merged(src_k.id, dst_k.id, new_name=f"{orig_dst_name} Combined", move_disk=False)
        v3_qapp.processEvents()
        assert tab._arch.get_node(src_k.id) is None
        assert tab._arch.get_node(dst_k.id).name == f"{orig_dst_name} Combined"
        assert tab._dirty is True
        assert r_src != r_dst
    finally:
        tab.close()
        svc2.stop(timeout=1.0)


# ---------------------------------------------------------------------------
# 6. Subfolder Removal Confirmation ("Are You Sure?") & Experimental AI Card
# ---------------------------------------------------------------------------

def test_subfolder_delete_confirmation_evacuation_undo_and_promote(
    v3_qapp, app_paths: AppPaths, monkeypatch  # noqa: ANN001
) -> None:
    svc, tab = _make_tab(app_paths)
    try:
        tab._on_load_preset("standard")
        v3_qapp.processEvents()
        docs = tab._arch.find_by_name_and_tier("Documents", 1)
        assert docs is not None
        tax_node = tab._arch.add_custom_node(name="TaxArchive", tier=2, parent_id=docs.id)
        child_2025 = tab._arch.add_custom_node(name="2025", tier=3, parent_id=tax_node.id)
        tab._redraw_canvas()

        dl = app_paths.downloads_dir
        tax_dir = dl / "Documents" / "TaxArchive"
        tax_dir.mkdir(parents=True, exist_ok=True)
        tax_file = tax_dir / "tax_return_2025.pdf"
        tax_file.write_bytes(b"%PDF-tax-2025")

        disk_info = svc.inspect_folder_on_disk(r"Documents\TaxArchive")
        assert disk_info["exists"] is True
        assert disk_info["file_count"] == 1
        assert "tax_return_2025.pdf" in disk_info["sample_files"]

        # Verify DeleteFolderDialog renders warning, zero QComboBox, and safe evacuation default
        dlg = DeleteFolderDialog(tab._arch, tax_node, disk_info, parent=tab)
        assert dlg.findChildren(QComboBox) == []
        assert dlg.lbl_disk_warning is not None
        assert "1 file(s) on disk" in dlg.lbl_disk_warning.text()
        assert "tax_return_2025.pdf" in dlg.lbl_disk_warning.text()
        assert dlg.file_action() == ("evacuate", "Documents")
        assert dlg.should_promote_children() is True
        dlg.close()

        # Cancel leaves node and disk file untouched
        monkeypatch.setattr(DeleteFolderDialog, "exec", lambda self: int(QDialog.DialogCode.Rejected))
        tab._delete_node_by_id(tax_node.id)
        assert tab._arch.get_node(tax_node.id) is not None
        assert tax_file.is_file()

        # Confirm removal evacuates files to parent folder, removes empty dir, and promotes child
        monkeypatch.setattr(DeleteFolderDialog, "exec", lambda self: int(QDialog.DialogCode.Accepted))
        tab._delete_node_by_id(tax_node.id)
        v3_qapp.processEvents()

        assert tab._arch.get_node(tax_node.id) is None
        promoted = tab._arch.get_node(child_2025.id)
        assert promoted is not None
        assert promoted.tier == 2
        assert promoted.parent_id == docs.id

        evacuated_file = dl / "Documents" / "tax_return_2025.pdf"
        assert evacuated_file.is_file()
        assert not tax_dir.exists()

        # File move is fully Undoable back into Documents\TaxArchive
        undo_res = svc.undo_last_batch()
        assert undo_res["restored"] == 1
        assert tax_file.is_file()
        assert not evacuated_file.is_file()
    finally:
        tab.close()
        svc.stop(timeout=1.0)


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


def test_tier2_subfolder_suggestions_by_file_type_and_classification_and_custom_input(v3_qapp, app_paths: AppPaths) -> None:
    # 1. All 9 default Tier 1 folders have both By File Type and By Classification suggestions
    for tier1_name in (
        "Documents",
        "3D Models",
        "CAD",
        "Images",
        "Videos",
        "Audio",
        "Archives",
        "Installers",
        "Code",
    ):
        ft = get_tier2_suggestions(tier1_name, mode="file_type")
        cls = get_tier2_suggestions(tier1_name, mode="classification")
        assert ft, f"Expected file_type suggestions for {tier1_name}"
        assert cls, f"Expected classification suggestions for {tier1_name}"

    audio_ft_names = [s["name"] for s in get_tier2_suggestions("Audio", mode="file_type")]
    audio_cls_names = [s["name"] for s in get_tier2_suggestions("Audio", mode="classification")]
    assert {"MP3", "WAV", "MP4"}.issubset(set(audio_ft_names))
    assert {"Songs", "Ringtones", "Recordings"}.issubset(set(audio_cls_names))

    svc, tab = _make_tab(app_paths)
    try:
        audio = next(n for n in tab._arch.nodes if n.tier == 1 and n.name == "Audio")
        canvas = tab.canvas
        popup = canvas._suggestion_popup

        # 2. Spawning a child under Audio opens the suggestion dropdown alongside inline typing
        tab._on_spawn_child_inline(audio.id, "New Subfolder")
        assert canvas._inline_edit.isVisible()
        assert popup.isVisible()
        spawned_id = canvas.selected_node_id
        assert spawned_id is not None

        # Filter to By Classification and verify Songs appears, then pick Songs
        QTest.mouseClick(popup._mode_btns["classification"], Qt.MouseButton.LeftButton)
        assert popup._mode == "classification"
        songs_row = next(
            i
            for i in range(popup.list_widget.count())
            if isinstance(popup.list_widget.item(i).data(Qt.ItemDataRole.UserRole), dict)
            and popup.list_widget.item(i).data(Qt.ItemDataRole.UserRole)["name"] == "Songs"
        )
        songs_item = popup.list_widget.item(songs_row)
        popup._on_item_clicked(songs_item)

        songs_node = tab._arch.get_node(spawned_id)
        assert songs_node is not None
        assert songs_node.name == "Songs"
        assert "song" in songs_node.keywords
        assert ".mp3" in songs_node.extensions
        assert not popup.isVisible()

        # 3. Spawning another child and typing a custom name works cleanly
        tab._on_spawn_child_inline(audio.id, "New Subfolder")
        assert canvas._inline_edit.isVisible()
        assert popup.isVisible()
        custom_id = canvas.selected_node_id
        canvas._inline_edit.setText("My Custom Audio")
        QTest.keyClick(canvas._inline_edit, Qt.Key.Key_Return)
        custom_node = tab._arch.get_node(custom_id)
        assert custom_node is not None
        assert custom_node.name == "My Custom Audio"
        assert not popup.isVisible()

        # 4. 1-click batch add all File Types under Audio
        tab._on_spawn_child_inline(audio.id, "New Subfolder")
        QTest.mouseClick(popup.btn_add_types, Qt.MouseButton.LeftButton)
        audio_children = {c.name for c in tab._arch.children_of(audio.id)}
        assert {"Songs", "My Custom Audio", "MP3", "WAV", "MP4", "FLAC", "AAC & OGG"}.issubset(audio_children)

        # 5. CustomFolderNodeDialog shows the same By File Type / By Classification picker with zero QComboBox
        dlg = CustomFolderNodeDialog(tab._arch, default_tier=2, default_parent_id=audio.id, parent=tab)
        assert dlg._sugg_wrap.isVisible() or dlg.list_suggestions.count() > 0
        assert dlg.findChildren(QComboBox) == []
        assert tab.findChildren(QComboBox) == []

        ring_row = next(
            i
            for i in range(dlg.list_suggestions.count())
            if dlg.list_suggestions.item(i).data(Qt.ItemDataRole.UserRole)["name"] == "Ringtones"
        )
        dlg._apply_dialog_suggestion(dlg.list_suggestions.item(ring_row))
        assert dlg.edit_name.text() == "Ringtones"
        assert "ringtone" in dlg.edit_keywords.text()
        dlg.close()
    finally:
        tab.close()
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
