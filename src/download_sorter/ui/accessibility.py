"""Names for screen readers.

Screen readers (Narrator, NVDA, JAWS) announce a control by its accessible name.
Qt uses a button's text for that, so icon-only buttons, and buttons that drop
their text in a narrow window, were read out as just "button". This gives every
unnamed button and text box a name from its label, tooltip or placeholder.
"""

from __future__ import annotations

import re

from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QAbstractButton, QAbstractSpinBox, QApplication, QComboBox, QLineEdit, QWidget

_SPACES_RE = re.compile(r"\s+")
_FILTER_ATTR = "_ds_screen_reader_names"


def _clean(text: str) -> str:
    """Drop shortcut marks (``&Save`` -> ``Save``, ``A && B`` -> ``A & B``) and extra spaces."""
    text = str(text or "").replace("&&", "\0").replace("&", "").replace("\0", "&")
    return _SPACES_RE.sub(" ", text).strip()


def _name_for(widget: QWidget) -> str:
    if isinstance(widget, QAbstractButton):
        text = _clean(widget.text())
        if not any(ch.isalnum() for ch in text):
            # "×", "+", "?" and arrows are read out as symbol names; the tooltip says what it does.
            return _clean(widget.toolTip()) or text
        return text
    if isinstance(widget, QLineEdit):
        return _clean(widget.placeholderText()) or _clean(widget.toolTip())
    if isinstance(widget, (QComboBox, QAbstractSpinBox)):
        return _clean(widget.toolTip())
    return ""


def name_controls_for_screen_readers(root: QWidget) -> int:
    """Name every unnamed button and text box in ``root``. Returns how many were named."""
    named = 0
    for widget in [root, *root.findChildren(QWidget)]:
        if widget.accessibleName():
            continue
        name = _name_for(widget)
        if not name:
            continue
        widget.setAccessibleName(name)
        tip = _clean(widget.toolTip())
        if tip and tip != name and not widget.accessibleDescription():
            widget.setAccessibleDescription(tip)
        named += 1
    return named


class _NameOnShow(QObject):
    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt's name
        if event.type() == QEvent.Type.Show and isinstance(obj, QWidget) and obj.isWindow():
            name_controls_for_screen_readers(obj)
        return False


def install_screen_reader_names(app: QApplication) -> None:
    """Name the controls of every window as it opens (dialogs, ribbons, the file pill)."""
    if getattr(app, _FILTER_ATTR, None) is not None:
        return
    watcher = _NameOnShow(app)
    app.installEventFilter(watcher)
    setattr(app, _FILTER_ATTR, watcher)
