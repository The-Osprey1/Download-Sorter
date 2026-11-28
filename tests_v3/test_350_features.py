"""v3.5.0 feature tests: duplicate actions, decluttered canvas, scan folders feedback, and tooltips."""

from __future__ import annotations

from pathlib import Path
import re

from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QDialog, QMessageBox

from download_sorter import about, fs_ops
from download_sorter.architecture import FolderArchitecture, scan_folder_architecture
from download_sorter.paths import AppPaths
from download_sorter.service import SorterService
from download_sorter.ui.main_window import DuplicateManagerDialog, MainWindow
from download_sorter.ui.settings_widgets import SettingCard, SettingRow
from download_sorter.ui.toasts import ToastManager


def _fake_recycle(paths: list[str]) -> bool:
    for raw in paths:
        p = Path(raw)
        if p.exists():
            p.unlink()
    return True


def _make_window(v3_qapp, app_paths: AppPaths) -> MainWindow:  # noqa: ANN001
    _ = v3_qapp
    svc = SorterService(app_paths)
    win = MainWindow(svc)
    win.show()
    return win


# ---------------------------------------------------------------------------
# 1. Duplicate Download Resolution: Replace Old / Delete & Locate / Keep Both
# ---------------------------------------------------------------------------

def test_browser_copy_suffix_and_counterpart_quarantine(app_paths: AppPaths, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(fs_ops, "_send_to_recycle_bin_win32", _fake_recycle)
    assert fs_ops.strip_browser_copy_suffix("report (1).pdf") == "report.pdf"
    assert fs_ops.strip_browser_copy_suffix("part(12).3mf") == "part.3mf"
    assert fs_ops.strip_browser_copy_suffix("clean.pdf") == "clean.pdf"

    svc = SorterService(app_paths)
    dl = app_paths.downloads_dir
    dest_pdf = dl / "Documents" / "PDF" / "report.pdf"
    dest_pdf.parent.mkdir(parents=True, exist_ok=True)
    dest_pdf.write_bytes(b"%PDF-1.4 original copy")

    incoming = dl / "report (1).pdf"
    incoming.write_bytes(b"%PDF-1.4 updated v2 download")
    res = svc.scan_top_level_now(settle_seconds=0.0)
    assert len(res["moved"]) == 1
    rec = res["moved"][0]
    assert rec["is_duplicate"] is True
    assert "Duplicates" in Path(rec["dest_path"]).parts
    assert Path(rec["existing_path"]).resolve() == dest_pdf.resolve()


def test_duplicate_actions_replace_delete_locate_and_keep_both(
    app_paths: AppPaths, monkeypatch  # noqa: ANN001
) -> None:
    monkeypatch.setattr(fs_ops, "_send_to_recycle_bin_win32", _fake_recycle)
    svc = SorterService(app_paths)
    dl = app_paths.downloads_dir
    old_doc = dl / "Documents" / "PDF" / "manual.pdf"
    old_doc.parent.mkdir(parents=True, exist_ok=True)
    old_doc.write_bytes(b"old-content-v1")

    dup_dir = dl / "Duplicates" / "2026-11"
    dup_dir.mkdir(parents=True, exist_ok=True)
    new_dup = dup_dir / "manual (1).pdf"
    new_dup.write_bytes(b"new-content-v2")

    # scan_duplicate_files should associate existing_path & existing_rel_path
    listed = svc.list_duplicate_files()
    assert len(listed) == 1
    assert Path(listed[0]["existing_path"]).resolve() == old_doc.resolve()
    assert "Documents" in listed[0]["existing_rel_path"]

    # Action A: Replace Old
    rep = svc.replace_duplicate_with_new(new_dup, old_doc)
    assert rep["ok"] is True
    assert old_doc.read_bytes() == b"new-content-v2"
    assert not new_dup.exists()
    assert not (dl / "Duplicates").exists()

    # Action B: Delete New & Locate Old
    dup2 = dup_dir / "manual (2).pdf"
    dup2.parent.mkdir(parents=True, exist_ok=True)
    dup2.write_bytes(b"extra-dup")
    del_res = svc.delete_duplicate_and_locate_old(dup2, old_doc)
    assert del_res["ok"] is True
    assert not dup2.exists()
    assert Path(del_res["existing_path"]).resolve() == old_doc.resolve()

    # Action C: Keep Both
    dup3 = dup_dir / "manual (3).pdf"
    dup3.parent.mkdir(parents=True, exist_ok=True)
    dup3.write_bytes(b"keep-alongside")
    keep_res = svc.keep_both_duplicate(dup3, old_doc)
    assert keep_res["ok"] is True
    final_copy = Path(keep_res["final_path"])
    assert final_copy.exists()
    assert final_copy.parent.resolve() == old_doc.parent.resolve()
    assert final_copy.name == "manual (2).pdf"
    assert old_doc.read_bytes() == b"new-content-v2"


def test_duplicate_toast_and_dialog_ui_actions(
    v3_qapp, app_paths: AppPaths, monkeypatch  # noqa: ANN001
) -> None:
    _ = v3_qapp
    monkeypatch.setattr(fs_ops, "_send_to_recycle_bin_win32", _fake_recycle)
    revealed: list[Path] = []
    monkeypatch.setattr("download_sorter.ui.main_window._reveal_in_explorer", revealed.append)

    calls: list[tuple[str, Path, Path | None]] = []
    mgr = ToastManager(
        on_replace_old=lambda d, o: calls.append(("replace", d, o)),
        on_delete_locate_old=lambda d, o: calls.append(("delete_locate", d, o)),
        on_keep_both=lambda d, o: calls.append(("keep_both", d, o)),
    )
    old_file = app_paths.downloads_dir / "Documents" / "PDF" / "spec.pdf"
    dup_file = app_paths.downloads_dir / "Duplicates" / "2026-11" / "spec (1).pdf"
    old_file.parent.mkdir(parents=True, exist_ok=True)
    dup_file.parent.mkdir(parents=True, exist_ok=True)
    old_file.write_bytes(b"v1")
    dup_file.write_bytes(b"v2")

    toast = mgr.show_moves(
        "b-dup",
        [
            {
                "filename": "spec (1).pdf",
                "dest_path": str(dup_file),
                "existing_path": str(old_file),
                "folder_display": "PDF",
                "is_duplicate": True,
            }
        ],
    )
    assert toast is not None
    assert toast.is_duplicate is True
    assert "DUPLICATE" in toast.eyebrow_lbl.text()
    assert toast.replace_old_btn.isEnabled()
    assert toast.delete_locate_btn.toolTip()
    assert toast.keep_both_btn.toolTip()
    toast.replace_old_btn.click()
    assert calls == [("replace", dup_file, old_file)]
    mgr.close_all()

    # DuplicateManagerDialog: 6 columns + Original Copy + Delete New & Locate Old button
    svc = SorterService(app_paths)
    dlg = DuplicateManagerDialog(svc)
    try:
        assert dlg.tbl_dups.columnCount() == 6
        assert dlg.tbl_dups.rowCount() == 1
        assert "PDF" in dlg.tbl_dups.item(0, 2).text()
        dlg.tbl_dups.selectRow(0)
        assert dlg.btn_replace_old.isEnabled()
        assert dlg.btn_delete_locate_old.isEnabled()
        assert dlg.btn_keep_both.isEnabled()
        dlg.btn_delete_locate_old.click()
        assert revealed and revealed[0].resolve() == old_file.resolve()
        assert dlg.tbl_dups.rowCount() == 0
    finally:
        dlg.close()


# ---------------------------------------------------------------------------
# 2. Folder Architecture Canvas Decluttering
# ---------------------------------------------------------------------------

def test_canvas_bottom_row_parents_focused_lines_and_tooltips(
    v3_qapp, app_paths: AppPaths  # noqa: ANN001
) -> None:
    win = _make_window(v3_qapp, app_paths)
    try:
        win.tabs.setCurrentWidget(win.arch_tab)
        canvas = win.arch_tab.canvas
        arch: FolderArchitecture = canvas._arch

        # Tier 1 parents with children must appear after leaf folders in _ordered_tier_nodes
        ordered = canvas._ordered_tier_nodes()
        t1 = ordered[1]
        seen_parent = False
        for node in t1:
            has_kids = len(arch.children_of(node.id)) > 0
            if has_kids:
                seen_parent = True
            else:
                assert not seen_parent, f"Leaf {node.name} came after a Tier 1 parent node"

        # Focused lines by default + toggle pill flips to All
        assert canvas.show_all_connectors() is False
        assert win.arch_tab.btn_toggle_lines.text() == "Lines: Focused"
        win.arch_tab.btn_toggle_lines.click()
        assert canvas.show_all_connectors() is True
        assert win.arch_tab.btn_toggle_lines.text() == "Lines: All"
        win.arch_tab.btn_toggle_lines.click()
        assert canvas.show_all_connectors() is False

        # Orthogonal path helper builds non-trivial turns for offset nodes
        path = canvas._build_orthogonal_bus_path(QPointF(100, 200), QPointF(260, 290), 235.0)
        assert path.elementCount() >= 4
        straight = canvas._build_orthogonal_bus_path(QPointF(100, 200), QPointF(100, 290), 235.0)
        assert straight.elementCount() == 2

        # Paint canvas and check rich card tooltip
        pix = canvas.grab()
        assert not pix.isNull()
        sample_node = arch.nodes[0]
        tip = canvas._node_tooltip(sample_node)
        assert sample_node.name in tip
        assert f"Tier {sample_node.tier}" in tip
    finally:
        win.close()


# ---------------------------------------------------------------------------
# 3. Scan Folders Progress & Completion Feedback
# ---------------------------------------------------------------------------

def test_scan_folders_button_runs_and_gives_feedback(
    v3_qapp, app_paths: AppPaths, monkeypatch  # noqa: ANN001
) -> None:
    dl = app_paths.downloads_dir
    custom_dir = dl / "Archive" / "CoursePackets"
    custom_dir.mkdir(parents=True, exist_ok=True)
    (custom_dir / "week1_notes.pdf").write_bytes(b"%PDF-1.4 packet")

    progress_events: list[tuple[int, int, str]] = []
    scan_res = scan_folder_architecture(
        dl,
        on_progress=lambda d, t, msg: progress_events.append((d, t, msg)),
    )
    assert scan_res["folder_count"] >= 2
    assert len(progress_events) >= 2

    win = _make_window(v3_qapp, app_paths)
    try:
        win.tabs.setCurrentWidget(win.arch_tab)

        # Simulate clicking Merge Missing inside Scan preview QDialog
        def _auto_accept_merge(dlg_self: QDialog) -> int:
            for btn in dlg_self.findChildren(type(win.arch_tab.btn_scan_folders)) or []:
                pass
            from PySide6.QtWidgets import QPushButton

            for b in dlg_self.findChildren(QPushButton):
                if "Merge Missing" in b.text():
                    b.click()
                    return QDialog.DialogCode.Accepted
            dlg_self.accept()
            return QDialog.DialogCode.Accepted

        monkeypatch.setattr(QDialog, "exec", _auto_accept_merge)
        win.arch_tab.btn_scan_folders.click()
        assert win.arch_tab.btn_scan_folders.isEnabled() is True
        assert win.arch_tab.btn_scan_folders.full_text() == "Scan Folders"
        assert "Scanned " in win.lbl_footer.text()

        # Empty downloads folder scan should also restore button cleanly
        for p in list(dl.rglob("*"))[::-1]:
            if p.is_file():
                p.unlink()
            elif p.is_dir():
                p.rmdir()
        monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.StandardButton.Ok)
        win.arch_tab.btn_scan_folders.click()
        assert win.arch_tab.btn_scan_folders.isEnabled() is True
        assert win.arch_tab.btn_scan_folders.full_text() == "Scan Folders"
    finally:
        win.close()


# ---------------------------------------------------------------------------
# 4. Hidden Subtitle Clutter, Non-Trivial Button Tooltips & Version
# ---------------------------------------------------------------------------

def test_subtitles_hidden_and_tooltips_present(v3_qapp, app_paths: AppPaths) -> None:  # noqa: ANN001
    # The version matches the newest released changelog section (not a fixed number,
    # which had to be edited by hand for every release).
    changelog = (Path(__file__).resolve().parent.parent / "CHANGELOG.md").read_text(encoding="utf-8")
    newest = re.search(r"^## \[(?!Unreleased\])([^\]]+)\]", changelog, re.MULTILINE)
    assert newest is not None and about.__version__ == newest.group(1)

    row = SettingRow("Demo Row", "Explanation moved into hover tooltip")
    assert row.lbl_subtitle.isVisible() is False
    assert "hover tooltip" in row.toolTip()

    card = SettingCard("Demo Card", "Card explanation in tooltip")
    assert card.lbl_sub.isVisible() is False
    assert "Card explanation" in card.toolTip()

    win = _make_window(v3_qapp, app_paths)
    try:
        assert win.lbl_sim_summary.isVisible() is False
        assert win.arch_tab.lbl_sel_rules.isVisible() is False

        for btn in (
            win.btn_pause,
            win.btn_home_autosort,
            win.btn_file_held,
            win.btn_preview_ribbon,
            win.arch_tab.btn_apply_shift,
            win.arch_tab.btn_edit_node,
            win.arch_tab.btn_add_child,
            win.arch_tab.btn_delete_node,
            win.arch_tab.btn_toggle_lines,
        ):
            assert btn.toolTip().strip(), f"Expected non-empty tooltip on {btn.text()}"
    finally:
        win.close()
