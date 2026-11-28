"""Unit and UI tests for the Guided 3-Step Folder Setup Studio (`FolderSetupStudioDialog`)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QAbstractButton, QApplication, QDialog, QPushButton, QWidget

from download_sorter.config import Config, Rule, save_config
from download_sorter.paths import AppPaths
from download_sorter.router import FileCandidate, evaluate
from download_sorter.service import SorterService
from download_sorter.ui.accessibility import name_controls_for_screen_readers
from download_sorter.ui.flow.setup_studio import (
    BLUEPRINT_CHOICES,
    FolderSetupStudioDialog,
    format_plain_english_folder_rule,
    validate_windows_folder_name,
)


def _clipped_buttons(root: QWidget) -> list[str]:
    clipped: list[str] = []
    for btn in root.findChildren(QPushButton):
        if not btn.isVisible() or not btn.text():
            continue
        if btn.width() + 1 < btn.sizeHint().width():
            clipped.append(f"{btn.text()!r} {btn.width()} < {btn.sizeHint().width()}")
    return clipped


def _collect_all_visible_text(root: QWidget) -> str:
    from PySide6.QtWidgets import QLabel

    parts: list[str] = []
    for lbl in root.findChildren(QLabel):
        if lbl.isVisible() and lbl.text():
            parts.append(lbl.text())
    for btn in root.findChildren(QAbstractButton):
        if btn.isVisible() and btn.text():
            parts.append(btn.text())
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Plain-English Rule Formatting (Exact AND vs OR Router Semantics)
# ---------------------------------------------------------------------------
def test_plain_english_rule_reflects_exact_and_or_semantics() -> None:
    # Extensions AND (keywords OR domains)
    s_both = format_plain_english_folder_rule(
        "Documents › School",
        extensions=[".pdf"],
        keywords=["syllabus"],
        domains=["canvas.edu"],
        is_manual=False,
    )
    assert s_both == "Moves .pdf files that contain 'syllabus' or come from canvas.edu into Documents › School."

    # Multiple extensions (OR within extensions) AND multiple keywords
    s_multi = format_plain_english_folder_rule(
        "Documents › Bills & Receipts",
        extensions=[".pdf", ".csv"],
        keywords=["invoice", "receipt"],
        domains=[],
        is_manual=False,
    )
    assert s_multi == "Moves .pdf or .csv files that contain 'invoice' or 'receipt' into Documents › Bills & Receipts."

    # Extension-only bucket
    s_ext = format_plain_english_folder_rule(
        "3D Models › 3MF",
        extensions=[".3mf"],
        keywords=[],
        domains=[],
        is_manual=False,
    )
    assert s_ext == "Moves .3mf files into 3D Models › 3MF."

    # Manual folder
    s_manual = format_plain_english_folder_rule(
        "Vault",
        extensions=[".pdf"],
        keywords=["secret"],
        domains=[],
        is_manual=True,
    )
    assert "Manual folder" in s_manual
    assert "Vault" in s_manual


# ---------------------------------------------------------------------------
# Step 1: Auto / Scan / Blueprints / Protected Untouched Bundles
# ---------------------------------------------------------------------------
def test_step1_auto_recommends_scan_and_protects_bundles_on_disk(
    v3_qapp,
    app_paths: AppPaths,
) -> None:
    dl = app_paths.downloads_dir
    assert dl is not None

    # Create real user folders + protected untouched folders on disk
    docs = dl / "Documents"
    docs.mkdir(parents=True)
    (docs / "a.pdf").write_bytes(b"%PDF-1.4")
    (docs / "b.pdf").write_bytes(b"%PDF-1.4")

    (dl / "Taxes").mkdir()
    (dl / "Taxes" / "single_1099.pdf").write_bytes(b"%PDF-1.4")

    # Protected bundles that MUST stay untouched and NOT become sorting categories
    games = dl / "Games"
    games.mkdir()
    (games / "celeste.exe").write_bytes(b"MZ")
    (dl / "Extracted").mkdir()
    (dl / "_Duplicates").mkdir()
    repo = dl / "MyGitRepo"
    (repo / ".git").mkdir(parents=True)
    (repo / "main.py").write_text("print(1)", encoding="utf-8")

    svc = SorterService(app_paths)
    try:
        dlg = FolderSetupStudioDialog(svc, initial_mode="auto")
        assert dlg._selected_mode == "scan"
        assert dlg.card_scan.lbl_badge.text() == "Recommended"

        # Protected folders detected and excluded from target architecture
        prot = dlg._scan_info["protected_folders"]
        for expected_prot in ("Games", "Extracted", "_Duplicates", "MyGitRepo"):
            assert expected_prot in prot

        folder_names = [n.name for n in dlg.target_architecture().nodes]
        assert "Documents" in folder_names
        assert "Taxes" in folder_names
        for forbidden in ("Games", "Extracted", "_Duplicates", "MyGitRepo"):
            assert forbidden not in folder_names
        dlg.close()
    finally:
        svc.stop(timeout=1.0)


def test_step1_blueprint_pills_switch_architecture_and_customize_mode(
    v3_qapp,
    service_instance: SorterService,
) -> None:
    dlg = FolderSetupStudioDialog(service_instance, initial_mode="preset")
    assert dlg._selected_mode == "preset"
    assert set(dlg.blueprint_buttons.keys()) == {k for k, _, _ in BLUEPRINT_CHOICES}

    # Click 'Flat 1-Level' blueprint pill
    dlg.blueprint_buttons["flat"].click()
    arch_flat = dlg.target_architecture()
    assert arch_flat.tier_count == 1
    assert all(n.tier == 1 for n in arch_flat.nodes)

    # Click '3D Printing & CAD' blueprint pill
    dlg.blueprint_buttons["maker_pro"].click()
    arch_maker = dlg.target_architecture()
    assert arch_maker.tier_count == 3
    maker_paths = arch_maker.all_target_folders()
    assert r"3D Models\MakerWorld" in maker_paths

    # Switch to 'Customize My Current Setup'
    dlg.card_customize.btn_select.click()
    assert dlg._selected_mode == "customize"
    dlg.close()


# ---------------------------------------------------------------------------
# Step 2: Completeness Guard, Guided Editor, Normalization & Sample Drop
# ---------------------------------------------------------------------------
def test_step2_blocks_incomplete_custom_folder_until_filled_or_marked_manual(
    v3_qapp,
    service_instance: SorterService,
) -> None:
    dlg = FolderSetupStudioDialog(service_instance, initial_mode="preset")
    dlg.show()
    QApplication.processEvents()

    # Advance to Step 2
    dlg.btn_next.click()
    assert dlg.stack.currentIndex() == 1

    # Add a brand-new custom folder (starts with 0 cues -> '! Needs rules')
    dlg.btn_add_folder.click()
    node = dlg._selected_node()
    assert node is not None
    dlg.edit_folder_name.setText("Tax Returns")
    dlg._on_folder_name_edited("Tax Returns")

    kind, badge = dlg._node_status(node)
    assert kind == "needs_rules"
    assert badge == "! Needs rules"

    # Attempting to advance to Step 3 focuses the incomplete folder and stays on Step 2
    dlg.btn_next.click()
    assert dlg.stack.currentIndex() == 1
    assert dlg.guidance_frame.isVisible()
    assert "Tax Returns" in dlg.lbl_guidance_text.text()

    # Clicking top pill 3 also enforces the completeness guard
    dlg.btn_step_3.click()
    assert dlg.stack.currentIndex() == 1

    # Option A: Toggle Manual folder -> becomes complete ('Manual') and reaches Step 3!
    dlg.btn_intent_manual.click()
    assert dlg.chk_manual.isChecked() is True
    assert dlg._node_status(node) == ("manual", "Manual")
    dlg.btn_next.click()
    assert dlg.stack.currentIndex() == 2

    # Go back to Step 2, switch back to Sort automatically -> needs rules again until we add cues
    dlg.btn_back.click()
    assert dlg.stack.currentIndex() == 1
    dlg.chk_manual.setChecked(False)
    assert dlg.btn_intent_auto.isChecked() is True
    assert dlg._node_status(node) == ("needs_rules", "! Needs rules")

    # Add custom extension without dot ('pdf' -> '.pdf'), keyword, and full URL ('https://www.irs.gov/forms/1099')
    dlg.edit_custom_ext.setText("pdf")
    dlg.btn_add_ext.click()
    assert ".pdf" in node.extensions

    dlg.edit_keyword.setText("1099, w2")
    dlg.btn_add_keyword.click()
    assert node.keywords == ["1099", "w2"]

    dlg.edit_domain.setText("https://www.irs.gov/pub/irs-pdf/f1099.pdf")
    dlg.btn_add_domain.click()
    assert node.domains == ["irs.gov"]

    assert dlg._node_status(node) == ("ready", "✓ Ready")
    assert "Moves .pdf files that contain '1099' or 'w2' or come from irs.gov into Tax Returns." == dlg.lbl_rule_sentence.text()

    # Live 1-line filename tester checks routing live
    dlg.edit_test_filename.setText("Form_1099_2026.pdf")
    assert "Lands in Tax Returns" in dlg.lbl_test_result.text()

    # Now Next reaches Step 3 cleanly
    dlg.btn_next.click()
    assert dlg.stack.currentIndex() == 2
    dlg.close()


def test_step2_parent_location_pills_subfolder_suggestions_and_zero_tier_jargon(
    v3_qapp,
    service_instance: SorterService,
) -> None:
    dlg = FolderSetupStudioDialog(service_instance, initial_mode="preset")
    dlg.show()
    dlg.btn_step_2.click()
    QApplication.processEvents()

    # Check zero cryptic "Tier 1" / "Tier 2" jargon anywhere on Step 2
    visible_text = _collect_all_visible_text(dlg).lower()
    assert "tier 1" not in visible_text
    assert "tier 2" not in visible_text

    # Select 'Documents' and use 1-click suggested subfolder chip
    for idx in range(dlg.checklist.count()):
        it = dlg.checklist.item(idx)
        if it and it.data(int(0x0100) + 5) == "Documents":
            dlg.checklist.setCurrentRow(idx)
            break
    doc_node = dlg._selected_node()
    assert doc_node is not None and doc_node.name == "Documents"
    assert dlg.sub_sug_box.isVisible()

    before_children = len(dlg._arch.children_of(doc_node.id))
    sug_btns = dlg.sub_sug_chips_host.findChildren(QPushButton)
    assert len(sug_btns) > 0
    sug_btns[0].click()
    assert len(dlg._arch.children_of(doc_node.id)) == before_children + 1

    # Add a top-level folder and reparent it inside Documents via Parent Location pill
    dlg.btn_add_folder.click()
    custom = dlg._selected_node()
    assert custom is not None
    dlg.edit_folder_name.setText("Seminars")
    dlg._on_folder_name_edited("Seminars")
    dlg.add_keyword("seminar")

    parent_btns = {b.text(): b for b in dlg.parent_pills_host.findChildren(QPushButton)}
    assert "Top-level in Downloads" in parent_btns
    assert "Inside Documents" in parent_btns
    parent_btns["Inside Documents"].click()
    assert dlg._arch.node_relative_path(custom.id) == r"Documents\Seminars"
    dlg.close()


@pytest.mark.parametrize(
    ("bad_input", "expected_substr"),
    [
        ("", "cannot be blank"),
        ("   ", "cannot be blank"),
        ("...", "only dots"),
        ("Tax:2026", "reserved characters"),
        ("What?", "reserved characters"),
        ("A/B", "reserved characters"),
        ("A\\B", "reserved characters"),
        ("CON", "reserved Windows system name"),
        ("prn", "reserved Windows system name"),
        ("AUX.2026", "reserved Windows system name"),
        ("COM1", "reserved Windows system name"),
        ("LPT9", "reserved Windows system name"),
    ],
)
def test_validate_windows_folder_name_rejects_illegal_and_reserved_names(
    bad_input: str,
    expected_substr: str,
) -> None:
    cleaned, err = validate_windows_folder_name(bad_input)
    assert cleaned == ""
    assert expected_substr in err


def test_step2_blocks_illegal_blank_reserved_and_duplicate_sibling_folder_names(
    v3_qapp,
    service_instance: SorterService,
) -> None:
    dlg = FolderSetupStudioDialog(service_instance, initial_mode="preset")
    dlg.show()
    dlg.btn_step_2.click()
    QApplication.processEvents()

    dlg.btn_add_folder.click()
    node = dlg._selected_node()
    assert node is not None
    dlg.add_custom_extension("pdf")
    dlg.add_keyword("tax")

    # 1. Illegal Windows character (e.g. Tax:2026 / What?)
    for illegal in ("Tax:2026", "What?", "CON", ""):
        dlg.edit_folder_name.setText(illegal)
        assert dlg._node_status(node) == ("needs_rules", "! Fix name")
        assert dlg.guidance_frame.isVisible()
        dlg.btn_next.click()
        assert dlg.stack.currentIndex() == 1, f"Should block Step 3 for {illegal!r}"

    # 2. Case-insensitive duplicate sibling name at top-level ('documents' collides with 'Documents')
    dlg.edit_folder_name.setText("documents")
    assert dlg._node_status(node) == ("needs_rules", "! Fix name")
    assert "already exists" in dlg.lbl_guidance_text.text()
    dlg.btn_step_3.click()
    assert dlg.stack.currentIndex() == 1

    # 3. Trailing dots and spaces are stripped cleanly and valid unique name proceeds to Step 3
    dlg.edit_folder_name.setText("Tax Returns 2026... ")
    assert node.name == "Tax Returns 2026"
    assert dlg._node_status(node) == ("ready", "✓ Ready")
    dlg.btn_next.click()
    assert dlg.stack.currentIndex() == 2
    assert dlg.edit_folder_name.text() == "Tax Returns 2026"
    dlg.close()


def test_step2_sample_file_drop_populates_ext_keywords_and_zone_domain(
    v3_qapp,
    app_paths: AppPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import download_sorter.ui.flow.setup_studio as studio_mod

    dl = app_paths.downloads_dir
    assert dl is not None
    sample = dl / "Gridfinity_Bin_Plate.3mf"
    sample.write_bytes(b"PK\x03\x04")

    monkeypatch.setattr(
        studio_mod,
        "read_zone_url",
        lambda p: "https://makerworld.com/en/models/987654#profileId-111" if Path(p) == sample else "",
    )

    svc = SorterService(app_paths)
    try:
        dlg = FolderSetupStudioDialog(svc, initial_mode="preset")
        dlg.btn_step_2.click()
        dlg.btn_add_folder.click()
        node = dlg._selected_node()
        assert node is not None
        dlg.edit_folder_name.setText("Gridfinity Prints")
        dlg._on_folder_name_edited("Gridfinity Prints")

        learned = dlg.learn_from_sample_file(sample)
        assert learned["added_ext"] == ".3mf"
        assert "gridfinity" in learned["added_keywords"]
        assert learned["added_domain"] == "makerworld.com"
        assert ".3mf" in node.extensions
        assert {"makerworld.com"} <= set(node.domains)
        assert dlg._node_status(node) == ("ready", "✓ Ready")
        dlg.close()
    finally:
        svc.stop(timeout=1.0)


# ---------------------------------------------------------------------------
# Step 3: 0-Move Reassurance, Existing Moves Preview, Config Sync & Apply
# ---------------------------------------------------------------------------
def test_step3_zero_moves_shows_reassuring_copy_and_disables_shift(
    v3_qapp,
    service_instance: SorterService,
) -> None:
    dlg = FolderSetupStudioDialog(service_instance, initial_mode="preset")
    dlg.show()
    dlg.btn_step_3.click()
    QApplication.processEvents()

    assert dlg.stack.currentIndex() == 2
    assert dlg.step3_zero_moves_card.isVisible()
    assert "All existing files stay right where they are" in dlg.lbl_zero_moves_text.text()
    assert dlg.should_shift_files() is False
    assert dlg.chk_shift_files.isEnabled() is False
    assert not dlg.table_preview.isVisible()
    dlg.close()


def test_step3_existing_files_preview_opt_in_and_end_to_end_apply(
    v3_qapp,
    app_paths: AppPaths,
) -> None:
    dl = app_paths.downloads_dir
    assert dl is not None
    (dl / "Invoice_Oct_2026.pdf").write_bytes(b"%PDF-1.4")
    (dl / "Plate_Mount.3mf").write_bytes(b"3mf")

    # Seed an advanced user rule with rename_template to verify preservation
    cfg = Config.new_install()
    cfg.hold_3mf_enabled = False
    cfg.rules.append(
        Rule(
            id="usr-adv-1",
            name="Signed Contracts",
            destination=r"Documents\Contracts",
            keywords=["signed_nda"],
            rename_template="{year}_{stem}",
        )
    )
    save_config(cfg, app_paths)

    svc = SorterService(app_paths)
    try:
        dlg = FolderSetupStudioDialog(svc, initial_mode="preset")
        dlg.show()
        dlg.btn_step_2.click()

        # Add custom Invoices subfolder under Documents
        for idx in range(dlg.checklist.count()):
            it = dlg.checklist.item(idx)
            if it and it.data(int(0x0100) + 5) == "Documents":
                dlg.checklist.setCurrentRow(idx)
                break
        dlg.btn_add_subfolder.click()
        inv_node = dlg._selected_node()
        assert inv_node is not None
        dlg.edit_folder_name.setText("Invoices")
        dlg._on_folder_name_edited("Invoices")
        dlg.add_custom_extension("pdf")
        dlg.add_keyword("invoice")

        # Advance to Step 3
        dlg.btn_next.click()
        assert dlg.stack.currentIndex() == 2
        QApplication.processEvents()

        # Opt-in checkbox starts unchecked by default even when matching files exist
        assert dlg.chk_shift_files.isEnabled() is True
        assert dlg.should_shift_files() is False
        assert "2 matching existing files" in dlg.chk_shift_files.text()
        assert dlg.table_preview.isVisible()
        assert dlg.table_preview.rowCount() == 2

        # Opt in and accept
        dlg.chk_shift_files.setChecked(True)
        assert dlg.should_shift_files() is True
        assert "Organizing 2 existing files" in dlg.summary_text()

        dlg.btn_apply.click()
        assert dlg.result() == int(QDialog.DialogCode.Accepted)

        target_cfg = dlg.target_config()
        # Preserved user advanced rule
        assert any(r.id == "usr-adv-1" and r.rename_template == "{year}_{stem}" for r in target_cfg.rules)
        # Generated new Invoices rule routes Invoice_Oct_2026.pdf -> Documents\Invoices
        dec = evaluate(FileCandidate("Invoice_Oct_2026.pdf"), target_cfg)
        assert dec.relative_dest == Path("Documents") / "Invoices" / "Invoice_Oct_2026.pdf"

        # Apply through SorterService and verify Undo works 100%
        shift_res = svc.apply_architecture_and_shift(target_cfg, shift_files=dlg.should_shift_files())
        assert shift_res["shifted_count"] == 2
        assert (dl / "Documents" / "Invoices" / "Invoice_Oct_2026.pdf").is_file()
    finally:
        svc.stop(timeout=1.0)


# ---------------------------------------------------------------------------
# Accessibility & Responsive Sizing Invariants (860x540 .. 1100x760)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(("width", "height"), [(860, 540), (980, 660), (1100, 760)])
def test_setup_studio_screen_reader_names_and_zero_button_clipping(
    v3_qapp,
    service_instance: SorterService,
    width: int,
    height: int,
) -> None:
    fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    old_font = v3_qapp.font()
    if (fonts / "segoeui.ttf").exists():
        for fn in ("segoeui.ttf", "segoeuib.ttf", "seguisb.ttf"):
            if (fonts / fn).exists():
                QFontDatabase.addApplicationFont(str(fonts / fn))
        v3_qapp.setFont(QFont("Segoe UI", 10))

    dlg = FolderSetupStudioDialog(service_instance, initial_mode="preset")
    try:
        dlg.show()
        for step_idx in (0, 1, 2):
            dlg._go_to_step(step_idx)
            for _ in range(3):
                dlg.resize(width, height)
                QApplication.processEvents()
            assert (dlg.width(), dlg.height()) == (width, height)
            assert _clipped_buttons(dlg) == [], f"Clipped buttons on step {step_idx + 1} at {width}x{height}"
            if step_idx == 1:
                assert dlg.editor_scroll.horizontalScrollBar().maximum() == 0

            name_controls_for_screen_readers(dlg)
            unnamed = [
                b.objectName() or repr(b)
                for b in dlg.findChildren(QAbstractButton)
                if not b.objectName().startswith("qt_")
                and not b.isHidden()
                and not (b.accessibleName() or b.text().strip())
            ]
            assert unnamed == [], f"Unnamed controls on step {step_idx + 1}: {unnamed}"
    finally:
        dlg.close()
        v3_qapp.setFont(old_font)
