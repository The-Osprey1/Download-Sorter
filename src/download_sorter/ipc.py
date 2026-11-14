"""Single-instance local socket IPC (PySide6 QLocalServer / QLocalSocket)."""

from __future__ import annotations

from collections.abc import Callable
import json
import sys
from typing import Any

from PySide6.QtCore import QObject
from PySide6.QtNetwork import QLocalServer, QLocalSocket


def _desktop_scoped_ipc_name(base: str = "DownloadSorterV3_IPC") -> str:
    """Scope IPC by Windows desktop name so non-Default desktops never hijack the user's desktop."""
    if sys.platform != "win32":
        return base
    try:
        import ctypes

        u32 = ctypes.windll.user32
        k32 = ctypes.windll.kernel32
        hdesk = u32.GetThreadDesktop(k32.GetCurrentThreadId())
        if hdesk:
            buf = ctypes.create_unicode_buffer(256)
            if u32.GetUserObjectInformationW(hdesk, 2, buf, ctypes.sizeof(buf), None):
                desk = "".join(ch for ch in buf.value if ch.isalnum() or ch in ("_", "-"))
                if desk and desk.lower() != "default":
                    return f"{base}_{desk}"
    except Exception:
        pass
    return base


DEFAULT_IPC_NAME = _desktop_scoped_ipc_name()


class DeferredReply:
    """A reply that will be ready later.

    A handler returns one of these for work that must not run on the GUI thread
    (sorting, undo). The connection stays open; ``resolve()`` - called on the GUI
    thread when the work is done - writes the JSON reply and closes it.
    """

    def __init__(self) -> None:
        self._send: Callable[[dict[str, Any]], None] | None = None
        self._response: dict[str, Any] | None = None

    @property
    def resolved(self) -> bool:
        return self._response is not None

    def resolve(self, response: dict[str, Any]) -> None:
        if self._response is not None:
            return  # only the first answer is sent
        self._response = dict(response)
        if self._send is not None:
            self._send(self._response)

    def _bind(self, send: Callable[[dict[str, Any]], None]) -> None:
        self._send = send
        if self._response is not None:  # resolved before the server got it back
            send(self._response)


class IPCServer(QObject):
    """Receive JSON commands from secondary CLI invocations in the single running process.

    ``handler(request)`` returns either the reply dict (answered at once) or a
    ``DeferredReply`` (answered when the handler resolves it).
    """

    def __init__(
        self,
        handler: Callable[[dict[str, Any]], "dict[str, Any] | DeferredReply"],
        server_name: str = DEFAULT_IPC_NAME,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._handler = handler
        self.server_name = server_name
        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._on_connection)
        # Connections that are open right now, so stop() can close them before
        # the server goes away.
        self._sockets: set[QLocalSocket] = set()

    def start(self) -> bool:
        QLocalServer.removeServer(self.server_name)
        return self._server.listen(self.server_name)

    def stop(self) -> None:
        """Stop listening and drop every open connection.

        A connection left open here would still have events queued for it (data
        to read, its own delayed delete) when the server is destroyed, and Qt
        then touches freed memory the next time events are processed.
        """
        self._server.close()
        for sock in list(self._sockets):
            self._forget(sock)
            try:
                sock.readyRead.disconnect(self._on_ready_read)
                sock.disconnected.disconnect(self._on_disconnected)
            except (RuntimeError, TypeError):
                pass
            try:
                sock.abort()
                sock.deleteLater()
            except RuntimeError:
                pass  # already deleted on the Qt side

    def _forget(self, sock: QLocalSocket) -> None:
        self._sockets.discard(sock)

    def _on_connection(self) -> None:
        while self._server.hasPendingConnections():
            sock = self._server.nextPendingConnection()
            if sock is None:
                continue
            # Bound methods, not lambdas: a lambda holding the socket keeps it (and
            # this server) alive in Python after Qt has already deleted them.
            self._sockets.add(sock)
            sock.readyRead.connect(self._on_ready_read)
            sock.disconnected.connect(self._on_disconnected)
            if sock.bytesAvailable() > 0:
                self._handle_socket(sock)

    def _on_ready_read(self) -> None:
        sock = self.sender()
        if isinstance(sock, QLocalSocket):
            self._handle_socket(sock)

    def _on_disconnected(self) -> None:
        sock = self.sender()
        if isinstance(sock, QLocalSocket):
            self._forget(sock)
            sock.deleteLater()

    def _handle_socket(self, sock: QLocalSocket) -> None:
        raw = bytes(sock.readAll()).decode("utf-8", errors="ignore").strip()
        if not raw:
            return
        try:
            req = json.loads(raw.splitlines()[0])
            if not isinstance(req, dict):
                req = {"cmd": ""}
            resp = self._handler(req)
        except Exception as exc:
            resp = {"ok": False, "error": str(exc)}
        if isinstance(resp, DeferredReply):
            resp._bind(lambda late, s=sock: self._send_reply(s, late))
            return
        self._send_reply(sock, resp)

    @staticmethod
    def _send_reply(sock: QLocalSocket, resp: dict[str, Any]) -> None:
        """Write the reply and hang up. Must run on the GUI thread (socket's thread)."""
        try:
            import shiboken6

            if not shiboken6.isValid(sock):
                return  # the caller gave up and the socket is already gone
            if sock.state() != QLocalSocket.LocalSocketState.ConnectedState:
                return
            sock.write((json.dumps(resp) + "\n").encode("utf-8"))
            sock.flush()
            sock.disconnectFromServer()
        except RuntimeError:
            pass


def send_ipc_command(
    cmd: str,
    payload: dict[str, Any] | None = None,
    *,
    server_name: str = DEFAULT_IPC_NAME,
    timeout_ms: int = 1200,
    reply_timeout_ms: int | None = None,
) -> dict[str, Any] | None:
    """Send a JSON command to a running Download Sorter v3 instance, or None if not running.

    ``timeout_ms`` covers connecting and sending; ``reply_timeout_ms`` (default: the
    same) is how long to wait for the answer. Commands that move files are answered
    only when the work is finished, so their callers pass a longer reply timeout.
    """
    sock = QLocalSocket()
    sock.connectToServer(server_name)
    if not sock.waitForConnected(timeout_ms):
        return None
    req = {"cmd": cmd, **(payload or {})}
    sock.write((json.dumps(req) + "\n").encode("utf-8"))
    sock.flush()
    sock.waitForBytesWritten(timeout_ms)
    if not sock.waitForReadyRead(timeout_ms if reply_timeout_ms is None else reply_timeout_ms):
        sock.abort()
        return None
    raw = bytes(sock.readAll()).decode("utf-8", errors="ignore").strip()
    sock.abort()
    if not raw:
        return None
    try:
        data = json.loads(raw.splitlines()[0])
        return data if isinstance(data, dict) else None
    except ValueError:
        return None
