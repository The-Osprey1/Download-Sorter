"""Tests for direct-manipulation Folder Architecture editor (drag-drop, spawn, inline edit, undo/redo, pills)."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import (
    QMouseEvent,
)

from download_sorter.architecture import FolderArchitecture


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


# ---------------------------------------------------------------------------
# 3. Direct-manipulation drag-and-drop reparenting, cycle halo, root promotion
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 4. '+' Handle Drag-to-Spawn, Wire-Reparent, Inline Rename & Hover Delete
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 5. Draggable Template Shelf Drop, Filter, Collapse & Local Undo/Redo
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 6. Strict 900x560 non-clipping check across Blueprint pills & collapsed shelf
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 7. Blueprint pills, max-depth template drop rejection, color swatches & Ctrl+Z
# ---------------------------------------------------------------------------
