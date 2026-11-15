"""Single-process system tray host + IPC server + in-process ToastManager."""

from __future__ import annotations

from collections.abc import Callable
import logging
import os
from pathlib import Path
import sys
import time
from typing import Any

from PySide6.QtCore import QLockFile, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QIcon, QPixmap, QPainter, QColor
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from download_sorter.ipc import DEFAULT_IPC_NAME, DeferredReply, IPCServer, send_ipc_command
from download_sorter.paths import AppPaths
from download_sorter.service import SorterService
from download_sorter.ui.main_window import MainWindow
from download_sorter.ui.toasts import ToastManager
from download_sorter.ui.workers import run_async


def _build_default_icon() -> QIcon:
    pix = QPixmap(64, 64)
    pix.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor("#2D6CDF"))
    painter.setPen(QColor("#6FA3FF"))
    painter.drawRoundedRect(4, 4, 56, 56, 14, 14)
    painter.setPen(QColor("#FFFFFF"))
    font = painter.font()
    font.setBold(True)
    font.setPointSize(20)
    painter.setFont(font)
    painter.drawText(pix.rect(), 0x84, "v3")  # AlignCenter
    painter.end()
    return QIcon(pix)


def _open_folder(folder: Path) -> None:
    if sys.platform == "win32" and hasattr(os, "startfile"):
        os.startfile(str(folder))


def _present_window(window: MainWindow) -> None:
    window.setWindowState(
        (window.windowState() & ~Qt.WindowState.WindowMinimized) | Qt.WindowState.WindowActive
    )
    window.showNormal()
    window.show()
    window.raise_()
    window.activateWindow()


def acquire_instance_lock(data_dir: Path, ipc_name: str = DEFAULT_IPC_NAME) -> QLockFile | None:
    """Claim the right to be the one running copy. None means another copy holds it.

    Asking the running copy over IPC is not enough on its own: two copies started in
    the same instant both find nobody listening yet, and both would start watching.
    The lock file is held open for the life of the process and Windows releases it
    when the process ends, however it ends.
    """
    safe_name = "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in ipc_name)
    lock = QLockFile(str(Path(data_dir) / f"{safe_name}.lock"))
    lock.setStaleLockTime(0)  # never treat a live holder as stale just because it is old
    return lock if lock.tryLock(0) else None


def build_ipc_handler(
    service: SorterService,
    window: MainWindow,
    *,
    parent: QObject | None,
    on_show_window: Callable[[], None],
    on_quit: Callable[[], None],
) -> Callable[[dict[str, Any]], "dict[str, Any] | DeferredReply"]:
    """The command handler behind the single-instance IPC server.

    ``ping``, ``show_window`` and ``quit`` answer immediately. ``sort_now``,
    ``file_held`` and ``undo`` move files, so they run on the worker pool and the
    reply is written when they finish; the GUI thread is never blocked on them.
    Under pytest ``run_async`` is inline, so the reply is already resolved on return.
    """

    def _in_background(
        work: Callable[[], dict[str, Any]],
        describe: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> DeferredReply:
        reply = DeferredReply()

        def _done(res: dict[str, Any]) -> None:
            try:
                window.refresh_all()
            finally:
                reply.resolve({"ok": True, **describe(res)})

        def _failed(message: str) -> None:
            try:
                window.refresh_all()
            finally:
                reply.resolve({"ok": False, "error": message})

        run_async(work, _done, _failed, parent=parent)
        return reply

    def _handle_ipc(req: dict[str, Any]) -> "dict[str, Any] | DeferredReply":
        cmd = str(req.get("cmd", "")).lower()
        if cmd == "ping":
            return {"ok": True, "paused": service.paused, "watch_root": str(service.watch_root)}
        if cmd == "show_window":
            on_show_window()
            return {"ok": True}
        if cmd == "sort_now":
            return _in_background(service.scan_top_level_now, lambda res: {"moved": len(res["moved"])})
        if cmd == "file_held":
            return _in_background(service.file_held_3mf_now, lambda res: {"moved": len(res["moved"])})
        if cmd == "undo":
            return _in_background(service.undo_last_batch, lambda res: dict(res))
        if cmd == "quit":
            on_quit()
            return {"ok": True}
        return {"ok": False, "error": f"Unknown command '{cmd}'"}

    return _handle_ipc


def run_app(
    paths: AppPaths | None = None,
    *,
    show_window: bool = True,
    ipc_name: str = DEFAULT_IPC_NAME,
) -> int:
    """Start the single-process Download Sorter v3 tray + window + watcher."""
    app_paths = paths or AppPaths.default()
    app_paths.ensure_dirs()
    logging.basicConfig(
        filename=str(app_paths.log_file),
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    hello = "show_window" if show_window else "ping"
    if send_ipc_command(hello, server_name=ipc_name) is not None:
        return 0

    instance_lock = acquire_instance_lock(app_paths.data_dir, ipc_name)
    if instance_lock is None:
        # Another copy is starting up right now. Hand our request to it once it is
        # listening, and never become a second watcher.
        for _ in range(40):
            if send_ipc_command(hello, server_name=ipc_name, timeout_ms=500) is not None:
                break
            time.sleep(0.25)
        return 0

    app = QApplication.instance() or QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    icon = _build_default_icon()
    app.setWindowIcon(icon)

    service = SorterService(app_paths)
    window = MainWindow(service)
    if not service.config.tutorial_completed:
        # First run: nothing is sorted until the welcome steps have been seen, so
        # show them even when started in the background (--tray, start with Windows).
        show_window = True
    toasts = ToastManager(
        # Same path as the window's Undo button: runs on the worker pool, then
        # refreshes the window and reports what was (and was not) restored.
        on_undo=lambda _b: window._on_undo_last(),
        on_open_folder=_open_folder,
    )

    window.moves_received.connect(
        lambda b, m: toasts.show_moves(
            b, m, duration_ms=int(service.config.ribbon_timeout_seconds * 1000)
        )
        if service.config.notifications_enabled
        else None
    )
    service.on_moves_completed = window.moves_received.emit

    class _QuickSwitcherBridge(QObject):
        dialog_detected = Signal(int, dict)

    qs_bridge = _QuickSwitcherBridge(app)
    active_pill = None

    def _show_quick_switcher(hwnd: int, data: dict[str, Any]) -> None:
        nonlocal active_pill
        if not service.config.quick_switcher_enabled:
            return
        if active_pill is not None and not active_pill.is_closed:
            if active_pill.dialog_hwnd == hwnd:
                return
            active_pill.close()
        try:
            from download_sorter.ui.quick_switcher_pill import QuickSwitcherPill

            cfg = service.config
            active_pill = QuickSwitcherPill(
                hwnd,
                data,
                hotkey_select=cfg.quick_switcher_hotkey,
                hotkey_all=cfg.quick_switcher_hotkey_all,
                hotkey_copy=cfg.quick_switcher_hotkey_copy,
            )
            active_pill.root.show()
        except Exception as exc:
            logging.debug("Could not display QuickSwitcherPill: %s", exc)

    qs_bridge.dialog_detected.connect(_show_quick_switcher)
    service.on_dialog_detected = qs_bridge.dialog_detected.emit

    def _quit_from_ipc() -> None:
        service.stop()
        app.quit()

    _handle_ipc = build_ipc_handler(
        service,
        window,
        parent=app,
        on_show_window=lambda: _present_window(window),
        on_quit=_quit_from_ipc,
    )
    ipc = IPCServer(_handle_ipc, server_name=ipc_name, parent=app)
    ipc.start()

    tray = QSystemTrayIcon(icon, app)
    tray.setToolTip("Download Sorter v3.0")
    menu = QMenu()
    act_open = QAction("Open Download Sorter", menu)
    act_open.triggered.connect(lambda: _present_window(window))
    act_sort = QAction("Sort Top-Level Now", menu)
    act_sort.triggered.connect(window._on_sort_now)
    act_file_held = QAction("File Held .3MF Files Now", menu)
    act_file_held.triggered.connect(window._on_file_held_now)
    act_undo = QAction("Undo Last Batch", menu)
    act_undo.triggered.connect(window._on_undo_last)
    act_pause = QAction("Pause / Resume", menu)
    act_pause.triggered.connect(window._toggle_pause)
    act_quit = QAction("Quit", menu)
    def _cleanup_and_quit() -> None:
        nonlocal active_pill
        if active_pill is not None:
            active_pill.close()
            active_pill = None
        ipc.stop()
        service.stop()
        tray.hide()
        app.quit()

    act_quit.triggered.connect(_cleanup_and_quit)

    menu.addAction(act_open)
    menu.addSeparator()
    menu.addAction(act_sort)
    menu.addAction(act_file_held)
    menu.addAction(act_undo)
    menu.addAction(act_pause)
    menu.addSeparator()
    menu.addAction(act_quit)
    tray.setContextMenu(menu)
    tray.activated.connect(
        lambda reason: _present_window(window)
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick
        else None
    )
    tray.show()

    service.start()
    if show_window:
        _present_window(window)

    # Look for a newer version shortly after start, then a few times a day for a
    # copy that stays open for weeks. GitHub is asked at most once in 24 hours.
    QTimer.singleShot(30_000, window.check_for_updates)
    update_timer = QTimer(app)
    update_timer.setInterval(6 * 60 * 60 * 1000)
    update_timer.timeout.connect(window.check_for_updates)
    update_timer.start()

    exit_code = app.exec()
    ipc.stop()
    service.stop()
    tray.hide()
    instance_lock.unlock()
    return int(exit_code)

