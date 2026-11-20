"""Single-process system tray host + IPC server + in-process ToastManager."""

from __future__ import annotations

from collections.abc import Callable
import logging
import os
from pathlib import Path
import sys
import time
from typing import Any
import uuid

from PySide6.QtCore import QLockFile, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QIcon, QPixmap, QPainter, QColor
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from download_sorter import quiet_time
from download_sorter.diagnostics import configure_file_logging, install_crash_logging
from download_sorter.db import STATUS_COMPLETED
from download_sorter.dest import is_under_watch_root
from download_sorter.ipc import DEFAULT_IPC_NAME, DeferredReply, IPCServer, send_ipc_command
from download_sorter.paths import AppPaths
from download_sorter.service import SorterService
from download_sorter.ui.accessibility import install_screen_reader_names
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


def make_moves_ribbon_handler(
    service: Any,
    toasts: Any,
    *,
    is_quiet_time: Callable[[], bool] = quiet_time.is_quiet_time,
) -> Callable[[str, list[dict[str, Any]]], None]:
    """What happens when files are sorted in the background: show the ribbon, unless
    ribbons are turned off or a full-screen app, presentation or lock screen is up.
    The files are sorted either way, and listed on Home with Undo."""

    def _on_moves(batch_id: str, moves: list[dict[str, Any]]) -> None:
        cfg = service.config
        if not cfg.notifications_enabled:
            return
        if is_quiet_time():
            logging.info("Ribbon for %d sorted file(s) skipped: full-screen or presentation", len(moves))
            return
        toasts.show_moves(batch_id, moves, duration_ms=int(cfg.ribbon_timeout_seconds * 1000))

    return _on_moves


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
        if cmd == "set_paused":
            paused = bool(req.get("paused", not service.paused))
            service.set_paused(paused)
            window.refresh_all()
            return {"ok": True, "paused": service.paused}
        if cmd == "reload_config":
            service.reload_config()
            window.refresh_all()
            return {"ok": True, "rules_count": sum(1 for r in service.config.rules if r.enabled)}
        if cmd == "chrome_routed":
            watch_dir = service.watch_root.resolve()
            raw_dest = str(req.get("dest_path") or "").strip()
            if not raw_dest:
                return {"ok": False, "error": "Missing dest_path"}
            dest_path = Path(raw_dest).resolve()
            if not is_under_watch_root(dest_path, watch_dir):
                return {"ok": False, "error": "Destination outside watch root"}
            filename = str(req.get("filename") or dest_path.name)
            src_path = str(req.get("src_path") or (watch_dir / filename))
            matched_rule = str(req.get("matched_rule") or "Chrome Extension")
            match_type = str(req.get("match_type") or "chrome")
            reason = str(req.get("reason") or "Routed directly by Chrome Extension")
            source_url = str(req.get("source_url") or "")
            size_bytes = int(req.get("size_bytes") or (dest_path.stat().st_size if dest_path.is_file() else 0))
            batch_id = f"chrome-{uuid.uuid4().hex[:8]}"
            now_ts = time.time()
            move_id = service.db.record_move(
                batch_id=batch_id,
                filename=filename,
                src_path=src_path,
                dest_path=str(dest_path),
                reason=reason,
                matched_rule=matched_rule,
                match_type=match_type,
                source_url=source_url,
                size_bytes=size_bytes,
                is_duplicate=False,
                moved_at=now_ts,
                status=STATUS_COMPLETED,
            )
            service.db.checkpoint()
            if service.config.quick_switcher_enabled and dest_path.is_file():
                try:
                    from download_sorter.quick_switcher import record_recent_file

                    record_recent_file(
                        str(dest_path),
                        downloads_root=watch_dir,
                        batch_id=batch_id,
                        settings={
                            "timeout_seconds": service.config.quick_switcher_timeout_seconds,
                            "3d_timeout_hours": service.config.quick_switcher_3d_timeout_hours,
                        },
                        data_dir=service.paths.data_dir,
                    )
                except Exception as exc:
                    logging.debug("Quick Switcher chrome_routed error: %s", exc)
            try:
                folder_display = str(dest_path.parent.relative_to(watch_dir))
            except ValueError:
                folder_display = str(dest_path.parent)
            rec = {
                "id": move_id,
                "batch_id": batch_id,
                "file_name": filename,
                "filename": filename,
                "src_path": src_path,
                "dest_path": str(dest_path),
                "folder_display": folder_display,
                "reason": reason,
                "matched_rule": matched_rule,
                "match_type": match_type,
                "source_url": source_url,
                "size_bytes": size_bytes,
                "moved_at": now_ts,
            }
            if service.on_moves_completed is not None:
                try:
                    service.on_moves_completed(batch_id, [rec])
                except Exception:
                    logging.exception("Error emitting chrome_routed move notification")
            else:
                window.refresh_all()
            return {"ok": True, "move_id": move_id, "batch_id": batch_id}
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
    configure_file_logging(app_paths.log_file)
    install_crash_logging()

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
    install_screen_reader_names(app)
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
        on_replace_old=window._on_toast_replace_old,
        on_delete_locate_old=window._on_toast_delete_locate_old,
        on_keep_both=window._on_toast_keep_both,
    )

    window.moves_received.connect(make_moves_ribbon_handler(service, toasts))
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
    if not os.environ.get("PYTEST_CURRENT_TEST"):
        try:
            from download_sorter.chrome_bridge import install_chrome_native_host

            install_chrome_native_host(app_paths.data_dir)
        except Exception as exc:
            logging.debug("Could not sync Chrome Native Messaging host on startup: %s", exc)
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

