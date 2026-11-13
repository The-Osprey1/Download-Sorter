"""The tray icon on Qt's QSystemTrayIcon, replacing pystray (RFC #2).

The rest of the app already uses PySide6 for its windows; this drops the second tray toolkit.
``Menu`` / ``MenuItem`` keep the small model tray.py builds its menu from (text that can be a
function, ``default``, ``enabled``, ``visible``, and submenus whose entries are read when they
open), and ``Icon`` offers the same calls tray.py made
on pystray's icon: ``icon`` and ``title`` assignment, ``update_menu()``, ``notify()``, ``run()``
and ``stop()``. Qt objects are only created in ``run()``, so building the tray (as the tests do)
needs no running Qt. Calls from other threads (the watcher, the quit listener) are passed to the
Qt thread through queued signals.
"""

from __future__ import annotations

import io
import logging
from typing import Any, Callable


class MenuItem:
    """One menu entry. ``text``, ``enabled`` and ``visible`` may be values or functions of the item."""

    def __init__(self, text: Any, action: Callable | None = None, *, default: bool = False,
                 enabled: Any = True, visible: Any = True, separator: bool = False) -> None:
        self._text = text
        # An item whose action is a Menu opens that menu as a submenu.
        self.submenu = action if isinstance(action, Menu) else None
        self.action = None if self.submenu is not None else action
        self.default = default
        self._enabled = enabled
        self._visible = visible
        self.separator = separator

    @staticmethod
    def _value(value: Any, item: "MenuItem") -> Any:
        return value(item) if callable(value) else value

    @property
    def text(self) -> str:
        return str(self._value(self._text, self))

    @property
    def enabled(self) -> bool:
        return bool(self._value(self._enabled, self)) and (self.action is not None or self.submenu is not None)

    @property
    def visible(self) -> bool:
        return bool(self._value(self._visible, self))

    def __call__(self, icon: Any = None) -> None:
        if self.action is not None:
            self.action(icon, self)


class Menu:
    """A list of items, or one function returning them (read each time the menu is shown)."""

    def __init__(self, *items: Any) -> None:
        if len(items) == 1 and callable(items[0]) and not isinstance(items[0], MenuItem):
            self._factory: Callable[[], Any] | None = items[0]
            self._items: tuple = ()
        else:
            self._factory = None
            self._items = tuple(items)

    @property
    def items(self) -> tuple:
        return tuple(self._factory()) if self._factory is not None else self._items

    def __iter__(self):
        """The visible entries."""
        return iter([i for i in self.items if i.separator or i.visible])

    @property
    def default_item(self) -> MenuItem | None:
        return next((i for i in self.items if i.default and not i.separator), None)


SEPARATOR = MenuItem("-", None, separator=True)


def _qicon(image: Any):
    """A QIcon from a PIL image (what tray.make_icon draws)."""
    from PySide6.QtGui import QIcon, QPixmap

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    pixmap = QPixmap()
    pixmap.loadFromData(buffer.getvalue(), "PNG")
    return QIcon(pixmap)


class Icon:
    def __init__(self, name: str, icon: Any = None, title: str = "", menu: Menu | None = None) -> None:
        self.name = name
        self._image = icon
        self._title = title
        self.menu = menu or Menu()
        self._tray = None
        self._app = None
        self._bridge = None
        self._stop_requested = False

    # ---- the same surface tray.py used on pystray's icon ----
    @property
    def icon(self) -> Any:
        return self._image

    @icon.setter
    def icon(self, image: Any) -> None:
        self._image = image
        self._post("refresh")

    @property
    def title(self) -> str:
        return self._title

    @title.setter
    def title(self, text: str) -> None:
        self._title = text
        self._post("refresh")

    def update_menu(self) -> None:
        self._post("refresh")

    def notify(self, message: str, title: str = "") -> None:
        if self._bridge is None:
            logging.debug("Tray not running; notification skipped: %s", message)
            return
        self._bridge.notify.emit(title or self.name, message)

    def stop(self) -> None:
        self._stop_requested = True
        self._post("stop")

    def run(self) -> None:
        """Show the icon and run until stop(). Must be called on the main thread."""
        from PySide6.QtCore import QObject, Qt, Signal
        from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

        if self._stop_requested:
            return
        app = QApplication.instance() or QApplication([])
        app.setQuitOnLastWindowClosed(False)
        self._app = app

        icon_ref = self

        class Bridge(QObject):
            command = Signal(str)
            notify = Signal(str, str)
            call = Signal(object)

        bridge = Bridge()
        tray = QSystemTrayIcon()
        qmenu = QMenu()

        def populate(target, menu: Menu) -> None:
            for item in menu.items:
                if item.separator:
                    target.addSeparator()
                    continue
                if not item.visible:
                    continue
                if item.submenu is not None:
                    sub = target.addMenu(item.text)
                    sub.aboutToShow.connect(lambda s=sub, m=item.submenu: (s.clear(), populate(s, m)))
                    populate(sub, item.submenu)
                    continue
                action = target.addAction(item.text)
                action.setEnabled(item.enabled)
                if item.default:
                    font = action.font()
                    font.setBold(True)
                    action.setFont(font)
                action.triggered.connect(lambda _checked=False, it=item: it(icon_ref))

        def rebuild() -> None:
            qmenu.clear()
            populate(qmenu, icon_ref.menu)

        def refresh() -> None:
            if icon_ref._image is not None:
                tray.setIcon(_qicon(icon_ref._image))
            tray.setToolTip(icon_ref._title)
            rebuild()

        def on_command(name: str) -> None:
            if name == "stop":
                tray.hide()
                app.quit()
            elif name == "refresh":
                refresh()

        def on_activated(reason) -> None:
            if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
                default = icon_ref.menu.default_item
                if default is not None:
                    default(icon_ref)

        def on_call(work) -> None:
            try:
                work()
            except Exception:
                logging.exception("A task handed to the tray's window thread failed")

        bridge.command.connect(on_command, Qt.ConnectionType.QueuedConnection)
        bridge.call.connect(on_call, Qt.ConnectionType.QueuedConnection)
        bridge.notify.connect(
            lambda title, message: tray.showMessage(title, message, QSystemTrayIcon.MessageIcon.Information, 6000),
            Qt.ConnectionType.QueuedConnection)
        qmenu.aboutToShow.connect(rebuild)  # texts like "3 files filed today" are read when it opens
        tray.activated.connect(on_activated)
        tray.setContextMenu(qmenu)
        refresh()
        tray.show()
        self._tray, self._bridge = tray, bridge
        try:
            if not self._stop_requested:
                app.exec()
        finally:
            tray.hide()
            self._tray = self._bridge = None

    def run_on_ui(self, work) -> bool:
        """Run ``work()`` on the thread that owns the tray's windows (from any thread).
        Returns False when the tray is not running, so the caller can do it another way."""
        if self._bridge is None:
            return False
        self._bridge.call.emit(work)
        return True

    def _post(self, command: str) -> None:
        if self._bridge is not None:
            self._bridge.command.emit(command)

    @property
    def visible(self) -> bool:
        return bool(self._tray is not None and self._tray.isVisible())
