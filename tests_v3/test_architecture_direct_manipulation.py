"""Tests for direct-manipulation Folder Architecture editor (drag-drop, spawn, inline edit, undo/redo, pills)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from PySide6.QtCore import QEvent, QMimeData, QPointF, Qt
from PySide6.QtGui import (
    QDragEnterEvent,
    QDragMoveEvent,
    QDropEvent,
    QFont,
    QFontDatabase,
    QMouseEvent,
)
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox, QPushButton

from download_sorter.architecture import FolderArchitecture
from download_sorter.config import Config, save_config
from download_sorter.paths import AppPaths
from download_sorter.service import SorterService
from download_sorter.ui.architecture_canvas import (
    MIME_TEMPLATE,
    ArchitectureTab,
    CustomFolderNodeDialog,
    PremadeFolderDialog,
)
from download_sorter.ui.main_window import MainWindow


def _mouse_event(etype: QEvent.Type, pos: QPointF, btn: Qt.MouseButton = Qt.MouseButton.LeftButton) -> QMouseEvent:
    btns = btn if etype != QEvent.Type.MouseButtonRelease else Qt.MouseButton.NoButton
    return QMouseEvent(etype, pos, pos, btn, btns, Qt.KeyboardModifier.NoModifier)


def _drag_canvas(canvas, start: QPointF, end: QPointF, *, release: bool = True) -> None:  # noqa: ANN001
    canvas.mousePressEvent(_mouse_event(QEvent.Type.MouseButtonPress, start))
    mid = QPointF((start.x() + end.x()) / 2.0, (start.y() + end.y()) / 2.0)
    canvas.mouseMoveEvent(_mouse_event(QEvent.Type.MouseMove, mid))
    canvas.mouseMoveEvent(_mouse_event(QEvent.Type.MouseMove, end))
    if release:
        canvas.mouseReleaseEvent(_mouse_event(QEvent.Type.MouseButtonRelease, end))


def _make_tab(app_paths: AppPaths) -> tuple[SorterService, ArchitectureTab]:
    cfg = Config.with_starter_defaults()
    save_config(cfg, app_paths)
    svc = SorterService(app_paths)
    tab = ArchitectureTab(svc)
    tab.resize(980, 640)
    tab.show()
    QApplication.processEvents()
    return svc, tab


# ---------------------------------------------------------------------------
# 1. Model-level reparenting, cycle detection & max-depth checks
# ---------------------------------------------------------------------------
def test_architecture_model_reparent_cycle_and_max_depth() -> None:
    arch = FolderArchitecture(tier_count=3)
    docs = arch.add_custom_node(name="Documents", tier=1, extensions=[".pdf"])
    school = arch.add_custom_node(name="School", tier=2, parent_id=docs.id)
    labs = arch.add_custom_node(name="Labs", tier=3, parent_id=school.id)
    media = arch.add_custom_node(name="Media", tier=1)

    assert arch.is_descendant_of(labs.id, docs.id) is True
    assert arch.is_descendant_of(docs.id, labs.id) is False
    assert arch.subtree_depth(docs.id) == 2
    assert arch.subtree_depth(labs.id) == 0

    # Self and cycle reparenting must be refused
    assert arch.can_reparent(docs.id, docs.id) is False
    assert arch.can_reparent(docs.id, school.id) is False
    assert arch.can_reparent(docs.id, labs.id) is False
    assert arch.reparent_node(docs.id, labs.id) is False

    # Move School (subtree depth 1) under Media (tier 1) -> School becomes Tier 2, Labs becomes Tier 3
    assert arch.can_reparent(school.id, media.id) is True
    assert arch.reparent_node(school.id, media.id) is True
    assert school.parent_id == media.id
    assert school.tier == 2
    assert labs.tier == 3
    assert arch.node_relative_path(labs.id) == r"Media\School\Labs"

    # Promote School (with Labs) to Tier 1 root -> School becomes Tier 1, Labs becomes Tier 2
    assert arch.reparent_node(school.id, None) is True
    assert school.parent_id is None
    assert school.tier == 1
    assert labs.tier == 2

    # Build a 4-tier chain and verify MAX_TIERS (5) boundary enforcement
    t1 = arch.add_custom_node(name="T1", tier=1)
    t2 = arch.add_custom_node(name="T2", tier=2, parent_id=t1.id)
    t3 = arch.add_custom_node(name="T3", tier=3, parent_id=t2.id)
    t4 = arch.add_custom_node(name="T4", tier=4, parent_id=t3.id)
    # School has subtree depth 1 (School + Labs): placing under t4 (tier 4 -> 5 + 1 = 6 > 5) must fail
    assert arch.can_reparent(school.id, t4.id) is False
    # Placing a leaf (depth 0) under t4 (tier 4 -> 5 <= 5) succeeds and expands tier_count to 5
    leaf = arch.add_custom_node(name="Leaf", tier=1)
    assert arch.can_reparent(leaf.id, t4.id) is True
    assert arch.reparent_node(leaf.id, t4.id) is True
    assert leaf.tier == 5
    assert arch.tier_count == 5
    # Cannot nest anything under a Tier 5 node
    other = arch.add_custom_node(name="Other", tier=1)
    assert arch.can_reparent(other.id, leaf.id) is False


# ---------------------------------------------------------------------------
# 2. Zero dropdowns in ArchitectureTab and its dialogs
# ---------------------------------------------------------------------------
def test_zero_dropdown_menus_in_architecture_editor(v3_qapp, app_paths: AppPaths) -> None:  # noqa: ANN001
    svc, tab = _make_tab(app_paths)
    try:
        assert tab.findChildren(QComboBox) == []

        dlg_custom = CustomFolderNodeDialog(tab._arch, parent=tab)
        assert dlg_custom.findChildren(QComboBox) == []
        dlg_custom.close()

        dlg_premade = PremadeFolderDialog(tab._arch, parent=tab)
        assert dlg_premade.findChildren(QComboBox) == []
        dlg_premade.close()
    finally:
        tab.close()
        svc.stop(timeout=1.0)


# ---------------------------------------------------------------------------
# 3. Direct-manipulation drag-and-drop reparenting, cycle halo, root promotion
# ---------------------------------------------------------------------------
def test_canvas_drag_reparent_valid_invalid_and_promote_to_root(v3_qapp, app_paths: AppPaths) -> None:  # noqa: ANN001
    svc, tab = _make_tab(app_paths)
    try:
        tab._on_load_preset("flat")
        v3_qapp.processEvents()
        docs = tab._arch.find_by_name_and_tier("Documents", 1)
        images = tab._arch.find_by_name_and_tier("Images", 1)
        assert docs is not None and images is not None

        canvas = tab.canvas
        r_docs = canvas._node_rects[docs.id]
        r_images = canvas._node_rects[images.id]

        # Drag Images onto Documents -> green valid halo during hover, reparents on drop
        _drag_canvas(canvas, r_images.center(), r_docs.center(), release=False)
        assert canvas._drop_target_id == docs.id
        assert canvas._drop_state == "valid"
        canvas.mouseReleaseEvent(_mouse_event(QEvent.Type.MouseButtonRelease, r_docs.center()))
        v3_qapp.processEvents()

        assert images.parent_id == docs.id
        assert images.tier == 2
        assert tab._arch.tier_count == 2
        assert tab._dirty is True

        # Drag Documents (parent) onto Images (its child) -> red invalid halo, stays unchanged on release
        r_docs = canvas._node_rects[docs.id]
        r_images = canvas._node_rects[images.id]
        _drag_canvas(canvas, r_docs.center(), r_images.center(), release=False)
        assert canvas._drop_target_id == images.id
        assert canvas._drop_state == "invalid"
        canvas.mouseReleaseEvent(_mouse_event(QEvent.Type.MouseButtonRelease, r_images.center()))
        v3_qapp.processEvents()

        assert docs.parent_id is None
        assert docs.tier == 1
        assert images.parent_id == docs.id

        # Drag Images onto Root card -> promotes back to Tier 1
        r_root = canvas._node_rects["__root__"]
        r_images = canvas._node_rects[images.id]
        _drag_canvas(canvas, r_images.center(), r_root.center(), release=False)
        assert canvas._drop_target_id == "__root__"
        assert canvas._drop_state == "valid"
        canvas.mouseReleaseEvent(_mouse_event(QEvent.Type.MouseButtonRelease, r_root.center()))
        v3_qapp.processEvents()

        assert images.parent_id is None
        assert images.tier == 1
    finally:
        tab.close()
        svc.stop(timeout=1.0)


# ---------------------------------------------------------------------------
# 4. '+' Handle Drag-to-Spawn, Wire-Reparent, Inline Rename & Hover Delete
# ---------------------------------------------------------------------------
def test_plus_handle_spawn_wire_reparent_inline_rename_and_delete(v3_qapp, app_paths: AppPaths) -> None:  # noqa: ANN001
    svc, tab = _make_tab(app_paths)
    try:
        tab._on_load_preset("flat")
        v3_qapp.processEvents()
        docs = tab._arch.find_by_name_and_tier("Documents", 1)
        images = tab._arch.find_by_name_and_tier("Images", 1)
        assert docs is not None and images is not None
        canvas = tab.canvas

        # Click/drag '+' handle on Documents into empty space -> spawns child & opens inline rename
        plus_pt = canvas._plus_rects[docs.id].center()
        empty_pt = QPointF(18.0, canvas.height() - 12.0)
        assert canvas._hit_node_at(empty_pt) is None
        before_count = len(tab._arch.nodes)
        _drag_canvas(canvas, plus_pt, empty_pt, release=True)
        v3_qapp.processEvents()

        assert len(tab._arch.nodes) == before_count + 1
        spawned = tab._arch.get_node(canvas.selected_node_id)
        assert spawned is not None
        assert spawned.parent_id == docs.id
        assert spawned.tier == 2
        assert canvas._inline_edit.isVisible()

        # Type new name and press Enter to commit inline rename
        canvas._inline_edit.setText("Tax Receipts")
        QTest.keyClick(canvas._inline_edit, Qt.Key.Key_Return)
        v3_qapp.processEvents()
        assert spawned.name == "Tax Receipts"
        assert not canvas._inline_edit.isVisible()

        # Double-click title area -> inline edit opens, pressing Esc cancels without modifying name
        r_spawn = canvas._node_rects[spawned.id]
        title_pt = QPointF(r_spawn.left() + 30, r_spawn.top() + 12)
        canvas.mouseDoubleClickEvent(_mouse_event(QEvent.Type.MouseButtonDblClick, title_pt))
        v3_qapp.processEvents()
        assert canvas._inline_edit.isVisible()
        canvas._inline_edit.setText("ShouldNotSave")
        QTest.keyClick(canvas._inline_edit, Qt.Key.Key_Escape)
        v3_qapp.processEvents()
        assert spawned.name == "Tax Receipts"
        assert not canvas._inline_edit.isVisible()

        # Drag '+' handle from Documents onto Images card -> wire-reparents Images under Documents
        plus_docs = canvas._plus_rects[docs.id].center()
        r_images = canvas._node_rects[images.id]
        _drag_canvas(canvas, plus_docs, r_images.center(), release=True)
        v3_qapp.processEvents()
        assert images.parent_id == docs.id
        assert images.tier == 2

        # Click hover delete '×' on spawned node -> removes it
        del_pt = canvas._delete_rects[spawned.id].center()
        canvas.mousePressEvent(_mouse_event(QEvent.Type.MouseButtonPress, del_pt))
        v3_qapp.processEvents()
        assert tab._arch.get_node(spawned.id) is None
    finally:
        tab.close()
        svc.stop(timeout=1.0)


# ---------------------------------------------------------------------------
# 5. Draggable Template Shelf Drop, Filter, Collapse & Local Undo/Redo
# ---------------------------------------------------------------------------
def test_template_shelf_drop_filtering_and_diagram_undo_redo(v3_qapp, app_paths: AppPaths) -> None:  # noqa: ANN001
    svc, tab = _make_tab(app_paths)
    try:
        tab._on_load_preset("flat")
        v3_qapp.processEvents()
        docs = tab._arch.find_by_name_and_tier("Documents", 1)
        assert docs is not None
        canvas = tab.canvas

        # Search and tier filters on TemplateShelfWidget
        tab.shelf.edit_search.setText("MakerWorld")
        assert tab.shelf.list_templates.count() == 2  # MakerWorld + Bambu Projects (references MakerWorld)
        tab.shelf.edit_search.setText("")
        tab.shelf._set_tier_filter("t1")
        t1_count = tab.shelf.list_templates.count()
        tab.shelf._set_tier_filter("all")
        assert tab.shelf.list_templates.count() > t1_count

        # Simulate external template drop ("t1_finance") onto Documents card -> creates Tier 2 child
        mime = QMimeData()
        mime.setData(MIME_TEMPLATE, json.dumps({"key": "t1_finance", "name": "Finance"}).encode("utf-8"))
        drop_pt = canvas._node_rects[docs.id].center().toPoint()
        canvas.dragEnterEvent(QDragEnterEvent(drop_pt, Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
        canvas.dragMoveEvent(QDragMoveEvent(drop_pt, Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
        assert canvas._ext_drop_node_id == docs.id
        assert canvas._ext_drop_valid is True
        canvas.dropEvent(QDropEvent(QPointF(drop_pt), Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
        v3_qapp.processEvents()

        fin_child = tab._arch.find_by_name_and_tier("Finance", 2, docs.id)
        assert fin_child is not None

        # Diagram Undo removes Finance; Diagram Redo restores it
        assert tab.btn_diag_undo.isEnabled()
        tab.undo_diagram_edit()
        assert tab._arch.find_by_name_and_tier("Finance", 2, docs.id) is None
        assert tab.btn_diag_redo.isEnabled()
        tab.redo_diagram_edit()
        fin_restored = tab._arch.find_by_name_and_tier("Finance", 2, docs.id)
        assert fin_restored is not None

        # Collapse & expand Template Shelf
        tab.shelf.toggle_collapsed()
        assert tab.shelf.width() == 40
        tab.shelf.toggle_collapsed()
        assert tab.shelf.width() == 168

        # Inline Inspector editing updates selected node & zoom works
        canvas.select_node(fin_restored.id)
        tab.edit_insp_name.setText("Tax & Finance")
        tab.edit_insp_exts.setText(".pdf, .csv")
        tab.edit_insp_keywords.setText("w2, 1099")
        tab.edit_insp_domains.setText("irs.gov")
        tab._on_inspector_fields_committed()
        assert fin_restored.name == "Tax & Finance"
        assert fin_restored.extensions == [".pdf", ".csv"]
        assert fin_restored.keywords == ["w2", "1099"]
        assert fin_restored.domains == ["irs.gov"]

        canvas.set_zoom(1.25)
        assert tab.lbl_zoom.text() == "125%"
        tab._on_auto_arrange()
        assert tab.lbl_zoom.text() == "100%"
    finally:
        tab.close()
        svc.stop(timeout=1.0)


# ---------------------------------------------------------------------------
# 6. Strict 900x560 non-clipping check across Blueprint pills & collapsed shelf
# ---------------------------------------------------------------------------
def test_architecture_tab_zero_button_clipping_at_900x560(v3_qapp, app_paths: AppPaths) -> None:  # noqa: ANN001
    fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    if not (fonts / "segoeui.ttf").exists():
        pytest.skip("Segoe UI is needed to measure the layout")
    for name in ("segoeui.ttf", "segoeuib.ttf", "seguisb.ttf"):
        if (fonts / name).exists():
            QFontDatabase.addApplicationFont(str(fonts / name))
    old_font = v3_qapp.font()
    v3_qapp.setFont(QFont("Segoe UI", 10))

    cfg = Config.with_starter_defaults()
    save_config(cfg, app_paths)
    for name in ("Bank_Statement_Oct_2026.pdf", "benchy_plate1.3mf", "setup-7zip-x64.exe"):
        (app_paths.downloads_dir / name).write_bytes(b"x" * 100)
    svc = SorterService(app_paths)
    win = MainWindow(svc)
    try:
        win.show()
        for w, h in ((900, 560), (1040, 640)):
            for _ in range(3):
                win.resize(w, h)
                QApplication.processEvents()
            win.tabs.setCurrentWidget(win.arch_tab)
            for _ in range(3):
                win.resize(w, h)
                QApplication.processEvents()
            assert (win.width(), win.height()) == (w, h)
            for preset in ("standard", "maker_pro", "flat"):
                win.arch_tab._on_load_preset(preset)
                QApplication.processEvents()
                clipped = [
                    f"{b.text()!r} {b.width()} < {b.sizeHint().width()}"
                    for b in win.findChildren(QPushButton)
                    if b.isVisible() and b.text() and b.width() + 1 < b.sizeHint().width()
                ]
                assert clipped == [], f"{w}x{h} preset={preset}: {clipped}"
            # Also verify with Template Shelf collapsed
            win.arch_tab.shelf.toggle_collapsed()
            QApplication.processEvents()
            clipped_col = [
                f"{b.text()!r} {b.width()} < {b.sizeHint().width()}"
                for b in win.findChildren(QPushButton)
                if b.isVisible() and b.text() and b.width() + 1 < b.sizeHint().width()
            ]
            assert clipped_col == [], f"{w}x{h} collapsed shelf: {clipped_col}"
            win.arch_tab.shelf.toggle_collapsed()
    finally:
        win.close()
        svc.stop(timeout=1.0)
        v3_qapp.setFont(old_font)


# ---------------------------------------------------------------------------
# 7. Blueprint pills, max-depth template drop rejection, color swatches & Ctrl+Z
# ---------------------------------------------------------------------------
def test_blueprint_pills_max_depth_template_drop_swatches_and_shortcuts(v3_qapp, app_paths: AppPaths) -> None:  # noqa: ANN001
    svc, tab = _make_tab(app_paths)
    try:
        # Blueprint pill click switches preset and updates checked state
        tab._blueprint_btns["maker_pro"].click()
        v3_qapp.processEvents()
        assert tab._active_preset == "maker_pro"
        assert tab._blueprint_btns["maker_pro"].isChecked()
        assert not tab._blueprint_btns["standard"].isChecked()
        assert tab._arch.tier_count == 3

        # Build a Tier 5 node and verify template drop onto Tier 5 shows invalid halo and rejects
        t1 = tab._arch.add_custom_node(name="L1", tier=1)
        t2 = tab._arch.add_custom_node(name="L2", tier=2, parent_id=t1.id)
        t3 = tab._arch.add_custom_node(name="L3", tier=3, parent_id=t2.id)
        t4 = tab._arch.add_custom_node(name="L4", tier=4, parent_id=t3.id)
        t5 = tab._arch.add_custom_node(name="L5", tier=5, parent_id=t4.id)
        tab._redraw_canvas()
        v3_qapp.processEvents()

        before_nodes = len(tab._arch.nodes)
        mime = QMimeData()
        mime.setData(MIME_TEMPLATE, json.dumps({"key": "t2_pdf", "name": "PDF"}).encode("utf-8"))
        drop_pt = tab.canvas._node_rects[t5.id].center().toPoint()
        tab.canvas.dragEnterEvent(QDragEnterEvent(drop_pt, Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
        tab.canvas.dragMoveEvent(QDragMoveEvent(drop_pt, Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
        assert tab.canvas._ext_drop_node_id == t5.id
        assert tab.canvas._ext_drop_valid is False
        tab.canvas.dropEvent(QDropEvent(QPointF(drop_pt), Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
        assert len(tab._arch.nodes) == before_nodes

        # Clicking an inspector color swatch updates node.kind and supports Ctrl+Z / Ctrl+Y
        tab.canvas.select_node(t1.id)
        old_kind = t1.kind
        new_kind = "cad" if old_kind != "cad" else "video"
        tab._swatch_btns[new_kind].click()
        assert t1.kind == new_kind

        tab.canvas.setFocus()
        QTest.keyClick(tab, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        t1_after_undo = tab._arch.get_node(t1.id)
        assert t1_after_undo is not None and t1_after_undo.kind == old_kind
        QTest.keyClick(tab, Qt.Key.Key_Y, Qt.KeyboardModifier.ControlModifier)
        t1_after_redo = tab._arch.get_node(t1.id)
        assert t1_after_redo is not None and t1_after_redo.kind == new_kind

        # Template shelf double-click activation adds under selected node
        tab.canvas.select_node(t1.id)
        tab.shelf.template_activated.emit("t1_archives")
        assert tab._arch.find_by_name_and_tier("Archives", 2, t1.id) is not None
    finally:
        tab.close()
        svc.stop(timeout=1.0)
