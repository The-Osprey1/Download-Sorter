"""Tests for GuidedFolderDialog and upgraded CustomFolderNodeDialog."""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QMimeData, QPointF, Qt, QUrl
from PySide6.QtGui import QDropEvent, QFont, QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QAbstractButton, QApplication, QComboBox, QLineEdit, QPushButton

from download_sorter import router
from download_sorter.architecture import FolderArchitecture
from download_sorter.config import Config
from download_sorter.paths import AppPaths
from download_sorter.pipeline import trace_file
from download_sorter.router import FileCandidate, evaluate
from download_sorter.ui.accessibility import name_controls_for_screen_readers
from download_sorter.ui.architecture_canvas import CustomFolderNodeDialog
from download_sorter.ui.flow.folder_builder import (
    UNIVERSAL_QUICK_PRESETS,
    GuidedFolderDialog,
    build_plain_english_summary,
    normalize_domain_token,
    normalize_domain_tokens,
    normalize_ext_token,
    normalize_ext_tokens,
    validate_folder_name,
)


# ---------------------------------------------------------------------------
# 1. Normalization Helpers & Plain-English Exact AND/OR Truth
# ---------------------------------------------------------------------------


def test_normalization_helpers_and_plain_english_and_or_truth() -> None:
    assert normalize_ext_token("pdf") == ".pdf"
    assert normalize_ext_token("*.STL") == ".stl"
    assert normalize_ext_token(".3MF") == ".3mf"
    assert normalize_ext_token("   ") == ""
    assert normalize_ext_tokens("pdf, docx / .CSV; pdf") == [".pdf", ".docx", ".csv"]

    assert normalize_domain_token("https://dashboard.stripe.com/pay?session=123") == "stripe.com"
    assert normalize_domain_token("https://www.makerworld.com/en/models/999") == "makerworld.com"
    assert normalize_domain_token("https://calpoly.instructure.com/courses/42/files") == "instructure.com"
    assert normalize_domain_token("https://classroom.google.com/u/0/c/123") == "classroom.google.com"
    assert normalize_domain_token("https://orders.amazon.co.uk/gp/css") == "amazon.co.uk"
    assert normalize_domain_tokens("https://dashboard.stripe.com/pay, paypal.com") == [
        "stripe.com",
        "paypal.com",
    ]

    sentence = build_plain_english_summary(
        rel_path=r"Documents\Bills & Receipts",
        extensions=[".pdf", ".csv"],
        keywords=["invoice", "receipt"],
        domains=["stripe.com"],
        exclude_keywords=["draft"],
    )
    assert "Downloads › Documents › Bills & Receipts" in sentence
    assert ".pdf or .csv files" in sentence
    # Exact AND/OR truth: extensions filter files AND ('invoice' OR 'receipt' OR stripe.com)
    assert "that contain 'invoice' or 'receipt', or come from stripe.com" in sentence
    assert "skipping files with 'draft'" in sentence
    assert ".pdf or 'invoice'" not in sentence.lower()

    manual_sentence = build_plain_english_summary(
        rel_path="Client Vault",
        extensions=[".pdf"],
        keywords=["secret"],
        domains=[],
        manual_only=True,
    )
    assert "manual folder" in manual_sentence.lower()
    assert "Downloads › Client Vault" in manual_sentence


# ---------------------------------------------------------------------------
# 2. Location Choice Pills, Default Parent Pre-selection & Name Validation
# ---------------------------------------------------------------------------


def test_location_modes_default_parent_and_validation_guards(v3_qapp) -> None:  # noqa: ANN001
    arch = FolderArchitecture.from_preset("standard")
    dlg = GuidedFolderDialog(arch, default_parent_path="Documents")
    try:
        # Zero technical "Tier 1 / Tier 2" jargon anywhere in GuidedFolderDialog
        visible_texts = [
            b.text() for b in dlg.findChildren(QAbstractButton) if b.text()
        ]
        assert not any("tier 1" in t.lower() or "tier 2" in t.lower() for t in visible_texts)

        # default_parent_path pre-selects Inside an existing folder -> Documents
        assert dlg.btn_loc_nested.isChecked()
        assert not dlg.btn_loc_root.isChecked()
        assert "Downloads › Documents ›" in dlg.lbl_path_badge.text()

        # Validate empty, illegal Windows chars, DOS reserved names, and duplicate siblings
        ok_empty, _ = validate_folder_name("")
        assert ok_empty is False
        ok_dots, _ = validate_folder_name("...")
        assert ok_dots is False

        for bad in ("Tax:2026", "A/B", "A\\B", "What?", "Star*", 'Quote"', "Pipe|", "<Tag>"):
            dlg.edit_name.setText(bad)
            ok, err = dlg.validate_name()
            assert ok is False, f"Expected {bad!r} to be rejected"
            assert "cannot contain" in err

        for reserved in ("CON", "prn", "AUX", "NUL", "COM1", "LPT9", "con.txt"):
            dlg.edit_name.setText(reserved)
            ok, err = dlg.validate_name()
            assert ok is False, f"Expected DOS reserved name {reserved!r} to be rejected"
            assert "reserved" in err.lower()

        # Duplicate sibling under Documents ("PDF" already exists under Documents in standard preset)
        dlg.edit_name.setText("pdf")
        ok_dup, err_dup = dlg.validate_name()
        assert ok_dup is False
        assert "already exists" in err_dup.lower()

        # Switching to Top-level in Downloads makes "PDF" valid (since PDF is not a Tier 1 sibling)
        dlg.btn_loc_root.click()
        ok_root, _ = dlg.validate_name()
        assert ok_root is True
        assert dlg.lbl_path_badge.text() == "Downloads › pdf"

        # Duplicate top-level sibling ("Documents" exists at top-level) is blocked
        dlg.edit_name.setText("Documents")
        ok_top_dup, _ = dlg.validate_name()
        assert ok_top_dup is False
    finally:
        dlg.close()


# ---------------------------------------------------------------------------
# 3. Completeness Guard, Manual Mode Intent Switch & Narrating CTA
# ---------------------------------------------------------------------------


def test_completeness_guard_manual_mode_and_narrating_cta(v3_qapp) -> None:  # noqa: ANN001
    arch = FolderArchitecture.from_preset("standard")
    dlg = GuidedFolderDialog(arch)
    try:
        dlg.edit_name.setText("Tax Vault")
        v3_qapp.processEvents()

        # With 0 routing cues in auto mode, primary button MUST stay disabled
        assert dlg.btn_mode_auto.isChecked()
        assert dlg.btn_save.isEnabled() is False
        assert dlg.btn_save.text() == "Pick at least 1 rule above"
        assert "at least one file type, filename word, or website" in dlg.lbl_completeness_hint.text().lower()

        # Switching to Manual mode enables Create immediately with clean empty cues
        dlg.btn_mode_manual.click()
        v3_qapp.processEvents()
        assert dlg.chk_manual_only.isChecked() is True
        assert dlg.btn_save.isEnabled() is True
        assert dlg.btn_save.text() == "Create Manual Folder"
        manual_data = dlg.result_data()
        assert manual_data["manual_only"] is True
        assert manual_data["extensions"] == []
        assert manual_data["keywords"] == []
        assert manual_data["domains"] == []

        # Switching back to Sort automatically re-engages the completeness guard
        dlg.btn_mode_auto.click()
        v3_qapp.processEvents()
        assert dlg.btn_save.isEnabled() is False

        # Clicking a File Type pill satisfies the guard and narrates folder creation
        dlg._type_pill_btns[".pdf"].click()
        v3_qapp.processEvents()
        assert dlg.btn_save.isEnabled() is True
        assert dlg.btn_save.text() == "Create 'Tax Vault' Folder"
    finally:
        dlg.close()


# ---------------------------------------------------------------------------
# 4. Quick-Fill Suggestions, Ghost Chips, Auto-Dot Ext & Auto-URL Chips
# ---------------------------------------------------------------------------


def test_quick_fill_suggestions_ghost_chips_and_auto_normalized_inputs(v3_qapp) -> None:  # noqa: ANN001
    arch = FolderArchitecture.from_preset("standard")
    dlg = GuidedFolderDialog(arch, default_parent_path="Documents")
    try:
        # Verify universal presets cover required setups
        preset_names = {p["name"] for p in UNIVERSAL_QUICK_PRESETS}
        assert {
            "Bills & Receipts",
            "School & Coursework",
            "Resumes & Career",
            "Screenshots",
            "Contracts & Legal",
            "3D Print Projects",
            "Manuals & Guides",
        }.issubset(preset_names)

        # Clicking "Bills & Receipts" pre-fills name, extensions, keywords, excludes, and domains
        assert "Bills & Receipts" in dlg._quick_suggestion_btns
        dlg._quick_suggestion_btns["Bills & Receipts"].click()
        v3_qapp.processEvents()

        data = dlg.result_data()
        assert data["name"] == "Bills & Receipts"
        assert data["parent_path"] == "Documents"
        assert data["rel_path"] == r"Documents\Bills & Receipts"
        assert ".pdf" in data["extensions"]
        assert "invoice" in data["keywords"]
        assert "receipt" in data["keywords"]
        assert dlg.btn_save.isEnabled() is True

        # Reset and test typing custom name -> dashed ghost suggestion chips + custom inputs
        dlg2 = GuidedFolderDialog(arch)
        try:
            dlg2.edit_name.setText("Wallpapers")
            v3_qapp.processEvents()

            ghost_kw_texts = [
                b.text() for b in dlg2.kw_ghost_wrap.findChildren(QPushButton)
            ]
            assert any("wallpaper" in t.lower() for t in ghost_kw_texts)

            # Custom extension without leading dot -> auto-normalized to .epub
            dlg2.edit_custom_ext.setText("epub")
            QTest.keyClick(dlg2.edit_custom_ext, Qt.Key.Key_Return)
            assert ".epub" in dlg2.result_data()["extensions"]

            # Comma-commit in Filename Words input -> adds chip and clears input
            dlg2.edit_keyword_input.setText("ultrawide")
            QTest.keyClicks(dlg2.edit_keyword_input, ",")
            assert dlg2.edit_keyword_input.text() == ""
            assert "ultrawide" in dlg2.result_data()["keywords"]

            # Exclude keyword chip
            dlg2.btn_toggle_excludes.click()
            dlg2.edit_exclude_input.setText("thumbnail")
            QTest.keyClick(dlg2.edit_exclude_input, Qt.Key.Key_Return)
            assert "thumbnail" in dlg2.result_data()["exclude_keywords"]

            # Full URL in Website Source -> normalized to domain chip
            dlg2.edit_domain_input.setText("https://dashboard.stripe.com/pay/inv_99")
            QTest.keyClick(dlg2.edit_domain_input, Qt.Key.Key_Return)
            assert "stripe.com" in dlg2.result_data()["domains"]

            # Removable chips remove cleanly on click
            dlg2.remove_keyword("ultrawide")
            assert "ultrawide" not in dlg2.result_data()["keywords"]
            dlg2.remove_domain("stripe.com")
            assert "stripe.com" not in dlg2.result_data()["domains"]
        finally:
            dlg2.close()
    finally:
        dlg.close()


# ---------------------------------------------------------------------------
# 5. Sample-File Drop Target, Live Downloads Preview & Align-Now Checkbox
# ---------------------------------------------------------------------------


def test_sample_file_drop_and_live_downloads_preview_with_align_now(
    v3_qapp, app_paths: AppPaths, monkeypatch  # noqa: ANN001
) -> None:
    dl = app_paths.downloads_dir
    inv1 = dl / "March_Invoice_2026.pdf"
    inv2 = dl / "Stripe_Invoice_Apr.pdf"
    other = dl / "benchy_plate.3mf"
    inv1.write_bytes(b"%PDF-1.4 invoice 1")
    inv2.write_bytes(b"%PDF-1.4 invoice 2")
    other.write_bytes(b"3mf-bytes")

    monkeypatch.setattr(
        router,
        "read_zone_url",
        lambda p: "https://dashboard.stripe.com/pay/123" if p.name == "March_Invoice_2026.pdf" else "",
    )
    # Also patch folder_builder.read_zone_url so initial watch_root scan and drop use fake zone URL
    import download_sorter.ui.flow.folder_builder as fb_mod

    monkeypatch.setattr(
        fb_mod,
        "read_zone_url",
        lambda p: "https://dashboard.stripe.com/pay/123" if p.name == "March_Invoice_2026.pdf" else "",
    )

    arch = FolderArchitecture.from_preset("standard")
    dlg = GuidedFolderDialog(arch, watch_root=dl, default_parent_path="Documents")
    try:
        dlg.edit_name.setText("Client Invoices")

        # Simulate dropping inv1 onto the dialog
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(inv1))])
        drop_ev = QDropEvent(
            QPointF(40.0, 40.0),
            Qt.DropAction.CopyAction,
            mime,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        dlg.dropEvent(drop_ev)
        v3_qapp.processEvents()

        data = dlg.result_data()
        assert ".pdf" in data["extensions"]
        assert "invoice" in data["keywords"]
        assert "stripe.com" in data["domains"]

        # Both invoice PDFs match; benchy_plate.3mf does not
        matched_names = {p.name for p in dlg.matching_loose_files()}
        assert matched_names == {"March_Invoice_2026.pdf", "Stripe_Invoice_Apr.pdf"}
        assert not dlg.chk_align_now.isHidden()
        assert "2 loose files in Downloads match right now" in dlg.lbl_live_matches.text()

        # Opting into chk_align_now updates result_data and narrating CTA
        assert dlg.result_data()["align_now"] is False
        dlg.chk_align_now.setChecked(True)
        v3_qapp.processEvents()
        assert dlg.result_data()["align_now"] is True
        assert "Sort 2 Files" in dlg.btn_save.text()

        # When 0 files match, show reassuring green copy and hide chk_align_now
        dlg._extensions = [".epub"]
        dlg._keywords = ["handbook"]
        dlg._domains = []
        dlg._sync_cue_chips()
        dlg._refresh_preview_and_cta()
        assert dlg.matching_loose_files() == []
        assert dlg.chk_align_now.isHidden()
        assert dlg.result_data()["align_now"] is False
        assert "No loose files in Downloads match right now" in dlg.lbl_live_matches.text()
        assert "example_handbook.epub" in dlg.lbl_live_matches.text()
    finally:
        dlg.close()


# ---------------------------------------------------------------------------
# 6. apply_to_architecture Routing in evaluate() & trace_file() + Edit Mode
# ---------------------------------------------------------------------------


def test_apply_to_architecture_routes_in_evaluate_and_trace_file_and_edits(v3_qapp) -> None:  # noqa: ANN001
    base_cfg = Config.with_starter_defaults()
    base_cfg.hold_3mf_enabled = False
    orig_arch = FolderArchitecture.from_preset("standard")
    orig_node_count = len(orig_arch.nodes)
    orig_rule_count = len(base_cfg.rules)

    # 1. Nested keyword+domain folder routes in both evaluate() and trace_file()
    dlg = GuidedFolderDialog(orig_arch, config=base_cfg, default_parent_path="Documents")
    try:
        dlg.apply_quick_suggestion("Bills & Receipts")
        new_arch, new_cfg, rel_path = dlg.apply_to_architecture(orig_arch, base_cfg)
    finally:
        dlg.close()

    # Pure functional update: caller's inputs untouched
    assert len(orig_arch.nodes) == orig_node_count
    assert len(base_cfg.rules) == orig_rule_count
    assert rel_path == r"Documents\Bills & Receipts"

    dec_kw = evaluate(FileCandidate(filename="Q4_Invoice_2026.pdf"), new_cfg)
    assert dec_kw.action == "move"
    assert str(dec_kw.relative_dest) == r"Documents\Bills & Receipts\Q4_Invoice_2026.pdf"

    tr_dom = trace_file("statement_april.csv", new_cfg, source_url="https://dashboard.stripe.com/pay")
    assert tr_dom.winning_step == 2
    assert tr_dom.dest_folder == r"Documents\Bills & Receipts"

    # Excluded phrase ("personal statement") is skipped cleanly
    dec_excl = evaluate(FileCandidate(filename="personal_statement_grad_school.pdf"), new_cfg)
    assert str(dec_excl.relative_dest) != r"Documents\Bills & Receipts\personal_statement_grad_school.pdf"

    # 2. Overlapping domain/keyword custom folder beats starter pack rule (tpl-makerworld)
    dlg_3d = GuidedFolderDialog(new_arch, config=new_cfg)
    try:
        dlg_3d.apply_quick_suggestion("3D Print Projects")
        arch_3d, cfg_3d, rel_3d = dlg_3d.apply_to_architecture(new_arch, new_cfg)
    finally:
        dlg_3d.close()

    assert rel_3d == "3D Print Projects"
    dec_3d = evaluate(
        FileCandidate(filename="ams_riser.3mf", source_url="https://makerworld.com/en/models/1"),
        cfg_3d,
    )
    assert str(dec_3d.relative_dest) == r"3D Print Projects\ams_riser.3mf"

    # 3. Pure extension-only custom folder routes cleanly via category_map
    dlg_ext = GuidedFolderDialog(arch_3d, config=cfg_3d)
    try:
        dlg_ext.edit_name.setText("Reading Room")
        dlg_ext.add_extension("epub")
        arch_ext, cfg_ext, rel_ext = dlg_ext.apply_to_architecture(arch_3d, cfg_3d)
    finally:
        dlg_ext.close()

    assert rel_ext == "Reading Room"
    tr_epub = trace_file("designing_data_intensive_apps.epub", cfg_ext)
    assert tr_epub.dest_folder == "Reading Room"

    # 4. Edit mode updates node name, parent, and rules without duplicating
    bills_node = arch_ext.find_by_name_and_tier("Bills & Receipts", 2)
    assert bills_node is not None
    dlg_edit = GuidedFolderDialog(arch_ext, config=cfg_ext, node=bills_node)
    try:
        assert dlg_edit.edit_name.text() == "Bills & Receipts"
        assert dlg_edit.btn_loc_nested.isChecked()
        dlg_edit.btn_loc_root.click()
        dlg_edit.edit_name.setText("Finance & Taxes")
        dlg_edit.add_keyword("1099_int")
        arch_edited, cfg_edited, rel_edited = dlg_edit.apply_to_architecture(arch_ext, cfg_ext)
    finally:
        dlg_edit.close()

    assert rel_edited == "Finance & Taxes"
    assert arch_edited.find_by_name_and_tier("Bills & Receipts", 2) is None
    assert arch_edited.find_by_name_and_tier("Finance & Taxes", 1) is not None
    dec_edited = evaluate(FileCandidate(filename="2026_1099_int.pdf"), cfg_edited)
    assert str(dec_edited.relative_dest) == r"Finance & Taxes\2026_1099_int.pdf"


# ---------------------------------------------------------------------------
# 7. Upgraded CustomFolderNodeDialog Backward Compatibility & Normalization
# ---------------------------------------------------------------------------


def test_custom_folder_node_dialog_preserves_contract_and_normalizes(v3_qapp) -> None:  # noqa: ANN001
    arch = FolderArchitecture.from_preset("standard")
    docs = arch.find_by_name_and_tier("Documents", 1)
    assert docs is not None

    dlg = CustomFolderNodeDialog(arch, default_tier=2, default_parent_id=docs.id)
    try:
        assert dlg.findChildren(QComboBox) == []
        # All legacy attributes preserved
        for attr in (
            "edit_name",
            "edit_exts",
            "edit_keywords",
            "edit_domains",
            "list_parents",
            "list_suggestions",
            "_tier_btns",
            "_kind_btns",
            "_sugg_mode_btns",
        ):
            assert hasattr(dlg, attr), f"Missing attribute {attr}"

        # Empty cues show gentle completeness guidance
        assert "Tip:" in dlg.lbl_completeness_guidance.text()

        dlg.edit_name.setText("Stripe Invoices")
        dlg.edit_exts.setText("pdf, csv")
        dlg.edit_keywords.setText("invoice, receipt")
        dlg.edit_domains.setText("https://dashboard.stripe.com/pay, https://www.paypal.com/activity")
        dlg._normalize_exts_field()
        dlg._normalize_domains_field()

        assert dlg.edit_exts.text() == ".pdf, .csv"
        assert dlg.edit_domains.text() == "stripe.com, paypal.com"
        vals = dlg.values()
        assert vals["name"] == "Stripe Invoices"
        assert vals["tier"] == 2
        assert vals["parent_id"] == docs.id
        assert vals["extensions"] == [".pdf", ".csv"]
        assert vals["keywords"] == ["invoice", "receipt"]
        assert vals["domains"] == ["stripe.com", "paypal.com"]
        assert "Downloads › Documents › Stripe Invoices" in dlg.plain_english_summary()
        assert "that contain 'invoice' or 'receipt', or come from stripe.com or paypal.com" in dlg.plain_english_summary()
        assert "Ready" in dlg.lbl_completeness_guidance.text()
    finally:
        dlg.close()


# ---------------------------------------------------------------------------
# 8. Screen-Reader Names, Zero Dropdowns & Zero Button Clipping Invariants
# ---------------------------------------------------------------------------


def test_screen_reader_names_zero_dropdowns_and_zero_clipping(v3_qapp) -> None:  # noqa: ANN001
    fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    old_font = v3_qapp.font()
    if (fonts / "segoeui.ttf").exists():
        for fname in ("segoeui.ttf", "segoeuib.ttf", "seguisb.ttf"):
            if (fonts / fname).exists():
                QFontDatabase.addApplicationFont(str(fonts / fname))
        v3_qapp.setFont(QFont("Segoe UI", 10))

    arch = FolderArchitecture.from_preset("standard")
    dlg = GuidedFolderDialog(arch, default_parent_path="Documents")
    try:
        dlg.apply_quick_suggestion("Bills & Receipts")
        dlg.show()
        v3_qapp.processEvents()
        name_controls_for_screen_readers(dlg)

        assert dlg.findChildren(QComboBox) == []

        unnamed_btns = [
            b.objectName() or repr(b)
            for b in dlg.findChildren(QAbstractButton)
            if not b.objectName().startswith("qt_")
            and not b.isHidden()
            and not (b.accessibleName() or b.text().strip())
        ]
        assert unnamed_btns == []

        unnamed_inputs = [
            e.objectName() or repr(e)
            for e in dlg.findChildren(QLineEdit)
            if not e.isHidden() and not (e.accessibleName() or e.placeholderText().strip())
        ]
        assert unnamed_inputs == []

        for w, h in ((680, 520), (900, 560), (1100, 760)):
            for _ in range(3):
                dlg.resize(w, h)
                QApplication.processEvents()
            clipped = [
                f"{b.text()!r} {b.width()} < {b.sizeHint().width()}"
                for b in dlg.findChildren(QPushButton)
                if b.isVisible() and b.text() and b.width() + 1 < b.sizeHint().width()
            ]
            assert clipped == [], f"Clipped buttons at {w}x{h}: {clipped}"
    finally:
        dlg.close()
        v3_qapp.setFont(old_font)


# ---------------------------------------------------------------------------
# 9. Manual Folder premade_key == "__manual__" Roundtrip & Spine Rule Sync
# ---------------------------------------------------------------------------


def test_manual_premade_key_roundtrip_and_spine_rule_merge_on_edit(v3_qapp) -> None:  # noqa: ANN001
    from download_sorter.config import Rule

    arch = FolderArchitecture.from_preset("standard")
    cfg = Config.new_install()

    # 1. Creating a Manual folder sets premade_key == "__manual__"
    dlg_manual = GuidedFolderDialog(arch, config=cfg)
    try:
        dlg_manual.edit_name.setText("Client Vault")
        dlg_manual.btn_mode_manual.click()
        arch_m, cfg_m, rel_m = dlg_manual.apply_to_architecture(arch, cfg)
    finally:
        dlg_manual.close()

    assert rel_m == "Client Vault"
    vault_node = arch_m.find_by_name_and_tier("Client Vault", 1)
    assert vault_node is not None
    assert vault_node.premade_key == "__manual__"
    assert any(
        n.get("name") == "Client Vault" and n.get("premade_key") == "__manual__"
        for n in cfg_m.architecture.get("nodes", [])
    )

    # 2. Re-opening Edit Folder & Rules on a "__manual__" node starts in Manual mode with Save enabled
    dlg_reopen = GuidedFolderDialog(arch_m, config=cfg_m, node=vault_node)
    try:
        assert dlg_reopen.btn_mode_manual.isChecked() is True
        assert dlg_reopen.chk_manual_only.isChecked() is True
        assert dlg_reopen.btn_save.isEnabled() is True
        assert dlg_reopen.btn_save.text() == "Save Manual Folder"

        # Switching to automatic mode + adding cues clears premade_key == "__manual__"
        dlg_reopen.btn_mode_auto.click()
        dlg_reopen.add_extension("pdf")
        dlg_reopen.add_keyword("nda")
        arch_auto, cfg_auto, _ = dlg_reopen.apply_to_architecture(arch_m, cfg_m)
    finally:
        dlg_reopen.close()

    vault_auto = arch_auto.find_by_name_and_tier("Client Vault", 1)
    assert vault_auto is not None
    assert vault_auto.premade_key == ""
    dec_nda = evaluate(FileCandidate(filename="mutual_nda_signed.pdf"), cfg_auto)
    assert str(dec_nda.relative_dest) == r"Client Vault\mutual_nda_signed.pdf"

    # 3. Editing a folder that has a simple SpinePanel rule merges & updates its cues cleanly
    spine_node = arch_auto.add_custom_node(name="Receipts", tier=1)
    cfg_auto.rules.insert(
        0,
        Rule(
            id="spine_r1",
            name="Receipts rule",
            destination="Receipts",
            extensions=[".pdf"],
            keywords=["receipt", "temp_kw"],
            exclude_keywords=["draft"],
            domains=["https://dashboard.stripe.com/pay"],
        ),
    )
    dlg_spine = GuidedFolderDialog(arch_auto, config=cfg_auto, node=spine_node)
    try:
        loaded = dlg_spine.result_data()
        assert loaded["extensions"] == [".pdf"]
        assert set(loaded["keywords"]) == {"receipt", "temp_kw"}
        assert loaded["exclude_keywords"] == ["draft"]
        assert loaded["domains"] == ["stripe.com"]

        # Remove temp_kw and verify stale SpinePanel rule doesn't resurrect it
        dlg_spine.remove_keyword("temp_kw")
        _arch_s, cfg_s, _ = dlg_spine.apply_to_architecture(arch_auto, cfg_auto)
    finally:
        dlg_spine.close()

    dec_kept = evaluate(FileCandidate(filename="coffee_receipt.pdf"), cfg_s)
    assert str(dec_kept.relative_dest) == r"Receipts\coffee_receipt.pdf"
    dec_removed = evaluate(FileCandidate(filename="temp_kw_only.pdf"), cfg_s)
    assert str(dec_removed.relative_dest) != r"Receipts\temp_kw_only.pdf"

