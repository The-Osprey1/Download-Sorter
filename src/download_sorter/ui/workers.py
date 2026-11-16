"""Background workers that keep disk, ADS and SQLite work off the Qt GUI thread.

``run_async(fn, on_done)`` runs ``fn`` on the shared ``QThreadPool`` and delivers its
result to ``on_done`` on the GUI thread. ``fn`` must not touch widgets and should
return plain Python data (a snapshot); ``on_done`` is the only place widgets change.

Under pytest (``PYTEST_CURRENT_TEST``), or when no Qt application exists, the same
call runs inline: ``fn`` then ``on_done`` before ``run_async`` returns, and exceptions
propagate to the caller. Code written against this helper therefore behaves exactly
like straight-line synchronous code in unit tests.
"""

from __future__ import annotations

from collections.abc import Callable
import logging
import os
from typing import Any

from PySide6.QtCore import QCoreApplication, QObject, QRunnable, Qt, QThreadPool, Signal, Slot


def async_enabled() -> bool:
    """True when work should really be offloaded (a live app, not a unit test)."""
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    return QCoreApplication.instance() is not None


class WorkerSignals(QObject):
    """Lives on the GUI thread; carries a worker's progress and result back to it."""

    finished = Signal(object)
    error = Signal(str)
    progress = Signal(int, int, str)

    def __init__(
        self,
        on_done: Callable[[Any], None] | None,
        on_error: Callable[[str], None] | None,
        parent: QObject | None = None,
        on_progress: Callable[[int, int, str], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self._on_done = on_done
        self._on_error = on_error
        self._on_progress = on_progress
        # Queued explicitly: the emit happens on a pool thread, the slot must not.
        self.finished.connect(self._deliver_result, Qt.ConnectionType.QueuedConnection)
        self.error.connect(self._deliver_error, Qt.ConnectionType.QueuedConnection)
        self.progress.connect(self._deliver_progress, Qt.ConnectionType.QueuedConnection)

    def emit_progress(self, done: int, total: int, label: str = "") -> None:
        """Thread-safe progress emission from a worker thread."""
        try:
            self.progress.emit(int(done), int(total), str(label))
        except RuntimeError:
            # Owning window was closed while task was running.
            pass

    @Slot(int, int, str)
    def _deliver_progress(self, done: int, total: int, label: str) -> None:
        if self._on_progress is not None:
            self._on_progress(done, total, label)

    @Slot(object)
    def _deliver_result(self, result: Any) -> None:
        try:
            if self._on_done is not None:
                self._on_done(result)
        finally:
            self._release()

    @Slot(str)
    def _deliver_error(self, message: str) -> None:
        try:
            if self._on_error is not None:
                self._on_error(message)
            else:
                logging.error("Background task failed: %s", message)
        finally:
            self._release()

    def _release(self) -> None:
        self._on_done = None
        self._on_error = None
        self._on_progress = None
        _LIVE_SIGNALS.discard(self)
        self.deleteLater()


# Keeps parentless signal objects alive until their result has been delivered.
_LIVE_SIGNALS: set[WorkerSignals] = set()


class AsyncTask(QRunnable):
    """Runs ``fn(*args, **kwargs)`` on a pool thread and reports through ``signals``."""

    def __init__(self, fn: Callable[..., Any], signals: WorkerSignals, *args: Any, **kwargs: Any) -> None:
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.signals = signals
        self.setAutoDelete(True)

    def run(self) -> None:  # executed on a QThreadPool thread
        try:
            result = self.fn(*self.args, **self.kwargs)
        except Exception as exc:
            logging.exception("Background task raised")
            self._emit(self.signals.error, f"{type(exc).__name__}: {exc}")
            return
        self._emit(self.signals.finished, result)

    def _emit(self, signal: Any, payload: Any) -> None:
        try:
            signal.emit(payload)
        except RuntimeError:
            # The owning window was closed while the task ran; nobody is listening.
            _LIVE_SIGNALS.discard(self.signals)


def run_async(
    fn: Callable[..., Any],
    on_done: Callable[[Any], None] | None = None,
    on_error: Callable[[str], None] | None = None,
    *,
    parent: QObject | None = None,
    force_async: bool = False,
    on_progress: Callable[[int, int, str], None] | None = None,
    pass_progress: bool = False,
) -> bool:
    """Run ``fn`` off the GUI thread and hand its result to ``on_done`` on the GUI thread.

    Returns True when the work was dispatched to the pool, False when it ran inline.
    ``parent`` ties delivery to a widget's lifetime: if it is destroyed first, the
    result is dropped instead of being delivered to a dead window. When
    ``pass_progress`` is True, ``fn`` is invoked with a thread-safe ``(done, total, label)``
    reporter callback.
    """
    if not (force_async or async_enabled()):
        progress_cb = on_progress if on_progress is not None else (lambda _d, _t, _s="": None)
        result = fn(progress_cb) if pass_progress else fn()
        if on_done is not None:
            on_done(result)
        return False

    signals = WorkerSignals(on_done, on_error, parent, on_progress=on_progress)
    _LIVE_SIGNALS.add(signals)
    args = (signals.emit_progress,) if pass_progress else ()
    QThreadPool.globalInstance().start(AsyncTask(fn, signals, *args))
    return True
