"""v3.0.0 sign-off: the last GUI-thread paths, busy-file backoff, and the installer
exercised against a file that really is locked.

- IPC ``sort_now`` / ``file_held`` / ``undo`` answer through a ``DeferredReply`` and
  never run their work on the GUI thread; the toast Undo button uses the window's
  background undo.
- A file that cannot be moved is retried with a growing pause and logged once.
- The compiled C# installer replaces a file it cannot overwrite by renaming it aside,
  and removes the leftover on the next run.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import inspect
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
import uuid

import pytest

from download_sorter.config import Config, save_config
from download_sorter.ipc import DeferredReply, IPCServer
from download_sorter.paths import AppPaths
import download_sorter.service as service_module
from download_sorter.service import BUSY_RETRY_MAX_SECONDS, SorterService

ROOT = Path(__file__).resolve().parent.parent


def _service(app_paths: AppPaths, **cfg_overrides) -> SorterService:  # noqa: ANN003
    cfg = Config.with_starter_defaults()
    cfg.settle_seconds = 0.0
    cfg.tutorial_completed = True
    for key, value in cfg_overrides.items():
        setattr(cfg, key, value)
    save_config(cfg, app_paths)
    return SorterService(app_paths)


def _pump(app, condition, timeout: float = 5.0) -> bool:  # noqa: ANN001
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if condition():
            return True
        time.sleep(0.005)
    return False


def _ipc_client(pipe: str, cmd: str, reply_timeout_ms: int = 8000) -> subprocess.Popen[str]:
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    code = (
        "import json; from download_sorter.ipc import send_ipc_command; "
        f"print(json.dumps(send_ipc_command({cmd!r}, server_name={pipe!r}, reply_timeout_ms={reply_timeout_ms})))"
    )
    return subprocess.Popen(
        [sys.executable, "-c", code], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env
    )


# ---------------------------------------------------------------------------
# Task 1: non-blocking IPC
# ---------------------------------------------------------------------------
def test_deferred_reply_resolves_once_in_either_order() -> None:
    sent: list[dict] = []
    late = DeferredReply()
    assert late.resolved is False
    late._bind(sent.append)
    assert sent == []
    late.resolve({"ok": True, "n": 1})
    late.resolve({"ok": False})  # ignored: one request, one answer
    assert sent == [{"ok": True, "n": 1}] and late.resolved is True

    early = DeferredReply()
    early.resolve({"ok": True, "n": 2})  # resolved before the server binds (inline/pytest mode)
    early._bind(sent.append)
    assert sent[-1] == {"ok": True, "n": 2}


def test_ipc_server_keeps_socket_open_until_deferred_reply_is_resolved(v3_qapp) -> None:  # noqa: ANN001
    pipe = f"TestDownloadSorterV3_{uuid.uuid4().hex[:8]}"
    pending: list[DeferredReply] = []

    def handler(req: dict) -> DeferredReply:
        reply = DeferredReply()
        pending.append(reply)
        return reply  # returns at once: the GUI thread is free while the work runs

    srv = IPCServer(handler, server_name=pipe)
    assert srv.start()
    proc = _ipc_client(pipe, "sort_now")
    try:
        assert _pump(v3_qapp, lambda: bool(pending)), "request never reached the handler"
        # The event loop keeps turning and the caller keeps waiting: no reply yet.
        _pump(v3_qapp, lambda: False, timeout=0.3)
        assert proc.poll() is None, "client got an answer before the work finished"

        pending[0].resolve({"ok": True, "moved": 7})
        assert _pump(v3_qapp, lambda: proc.poll() is not None)
        out, err = proc.communicate(timeout=2)
        assert proc.returncode == 0, err
        assert json.loads(out) == {"ok": True, "moved": 7}
    finally:
        if proc.poll() is None:
            proc.kill()
        srv.stop()


def test_ipc_server_survives_a_caller_that_gave_up(v3_qapp) -> None:  # noqa: ANN001
    pipe = f"TestDownloadSorterV3_{uuid.uuid4().hex[:8]}"
    pending: list[DeferredReply] = []
    srv = IPCServer(lambda req: pending.append(DeferredReply()) or pending[-1], server_name=pipe)
    assert srv.start()
    proc = _ipc_client(pipe, "undo", reply_timeout_ms=150)  # hangs up long before the answer
    try:
        assert _pump(v3_qapp, lambda: bool(pending))
        assert _pump(v3_qapp, lambda: proc.poll() is not None)
        assert proc.communicate(timeout=2)[0].strip() == "null"
        _pump(v3_qapp, lambda: False, timeout=0.2)  # socket disconnects and is deleted
        pending[0].resolve({"ok": True})  # must not raise on the dead socket

        # And the server still answers the next caller.
        second = _ipc_client(pipe, "ping")
        assert _pump(v3_qapp, lambda: len(pending) == 2)
        pending[1].resolve({"ok": True, "again": True})
        assert _pump(v3_qapp, lambda: second.poll() is not None)
        assert json.loads(second.communicate(timeout=2)[0]) == {"ok": True, "again": True}
    finally:
        if proc.poll() is None:
            proc.kill()
        srv.stop()


@pytest.fixture
def ipc_rig(v3_qapp, app_paths: AppPaths):  # noqa: ANN001, ANN201
    from download_sorter.ui.main_window import MainWindow
    from download_sorter.ui.tray_app import build_ipc_handler

    svc = _service(app_paths)
    win = MainWindow(svc)
    events: list[str] = []
    handler = build_ipc_handler(
        svc,
        win,
        parent=v3_qapp,
        on_show_window=lambda: events.append("show"),
        on_quit=lambda: events.append("quit"),
    )
    yield svc, win, handler, events
    win.close()
    svc.stop(timeout=1.0)


def test_ipc_fast_commands_answer_immediately(ipc_rig) -> None:  # noqa: ANN001
    svc, _win, handler, events = ipc_rig
    assert handler({"cmd": "ping"}) == {"ok": True, "paused": svc.paused, "watch_root": str(svc.watch_root)}
    assert handler({"cmd": "SHOW_WINDOW"}) == {"ok": True}
    assert handler({"cmd": "quit"}) == {"ok": True}
    assert events == ["show", "quit"]
    assert handler({"cmd": "nope"}) == {"ok": False, "error": "Unknown command 'nope'"}


def test_ipc_work_commands_reply_through_deferred_reply(ipc_rig, app_paths: AppPaths) -> None:  # noqa: ANN001
    svc, win, handler, _events = ipc_rig
    dl = app_paths.downloads_dir
    assert dl is not None
    (dl / "report.pdf").write_bytes(b"pdf")
    sent: list[dict] = []

    reply = handler({"cmd": "sort_now"})
    assert isinstance(reply, DeferredReply) and reply.resolved  # inline under pytest
    reply._bind(sent.append)
    assert sent == [{"ok": True, "moved": 1}]
    assert win.tbl_activity.rowCount() == 1, "window must be refreshed before the reply goes out"

    undo = handler({"cmd": "undo"})
    assert isinstance(undo, DeferredReply)
    undo._bind(sent.append)
    assert sent[-1]["ok"] is True and sent[-1]["restored"] == 1 and sent[-1]["skipped_locked"] == 0
    assert (dl / "report.pdf").is_file() and win.tbl_activity.rowCount() == 0

    held = handler({"cmd": "file_held"})
    assert isinstance(held, DeferredReply)
    held._bind(sent.append)
    assert sent[-1] == {"ok": True, "moved": 0}


@pytest.mark.parametrize("cmd", ["sort_now", "file_held", "undo"])
def test_ipc_work_commands_never_run_on_the_gui_thread(
    ipc_rig, v3_qapp, monkeypatch: pytest.MonkeyPatch, cmd: str  # noqa: ANN001
) -> None:
    svc, win, handler, _events = ipc_rig
    main_thread = threading.get_ident()
    method = {"sort_now": "scan_top_level_now", "file_held": "file_held_3mf_now", "undo": "undo_last_batch"}[cmd]
    gate = threading.Event()
    ran_on: list[int] = []
    real = getattr(svc, method)

    def slow(*a, **k):  # noqa: ANN002, ANN003
        ran_on.append(threading.get_ident())
        gate.wait(5.0)  # a long sort: the GUI thread must stay free meanwhile
        return real(*a, **k)

    monkeypatch.setattr(svc, method, slow)
    refreshed: list[int] = []
    real_refresh = win.refresh_all
    monkeypatch.setattr(win, "refresh_all", lambda *a, **k: (refreshed.append(1), real_refresh(*a, **k))[1])
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)  # behave like the real app

    started = time.monotonic()
    reply = handler({"cmd": cmd})
    assert time.monotonic() - started < 1.0, "handler blocked the GUI thread on the service call"
    assert isinstance(reply, DeferredReply) and not reply.resolved
    sent: list[dict] = []
    reply._bind(sent.append)

    assert _pump(v3_qapp, lambda: bool(ran_on))
    assert ran_on[0] != main_thread
    assert handler({"cmd": "ping"})["ok"] is True  # still responsive while the work runs
    assert sent == []

    gate.set()
    assert _pump(v3_qapp, lambda: bool(sent))
    assert sent[0]["ok"] is True
    assert refreshed, "window was not refreshed when the work finished"
    assert _pump(v3_qapp, lambda: not win._refresh_running)


def test_ipc_work_failure_is_reported_not_swallowed(
    ipc_rig, v3_qapp, monkeypatch: pytest.MonkeyPatch  # noqa: ANN001
) -> None:
    svc, win, handler, _events = ipc_rig

    def broken() -> dict:
        raise OSError("disk went away")

    monkeypatch.setattr(svc, "scan_top_level_now", broken)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    sent: list[dict] = []
    handler({"cmd": "sort_now"})._bind(sent.append)
    assert _pump(v3_qapp, lambda: bool(sent))
    assert sent == [{"ok": False, "error": "OSError: disk went away"}]
    assert _pump(v3_qapp, lambda: not win._refresh_running)


def test_toast_undo_uses_the_windows_background_undo() -> None:
    from download_sorter.ui import tray_app

    assert not hasattr(tray_app, "_undo_and_refresh"), "synchronous GUI-thread undo helper is back"
    source = inspect.getsource(tray_app.run_app)
    assert "on_undo=lambda _b: window._on_undo_last()" in source
    assert "service.undo_last_batch()" not in source


def test_cli_waits_for_work_replies(monkeypatch: pytest.MonkeyPatch, capsys) -> None:  # noqa: ANN001
    from download_sorter import cli

    calls: list[tuple[str, dict]] = []

    def fake_send(cmd: str, payload=None, **kwargs):  # noqa: ANN001, ANN003, ANN202
        calls.append((cmd, kwargs))
        return {"ok": True, "moved": 2, "restored": 3}

    monkeypatch.setattr(cli, "send_ipc_command", fake_send)
    assert cli.main(["--sort-now"]) == 0
    assert cli.main(["--file-held"]) == 0
    assert cli.main(["--undo"]) == 0
    assert [c[0] for c in calls] == ["sort_now", "file_held", "undo"]
    assert all(kw.get("reply_timeout_ms", 0) >= 60_000 for _c, kw in calls)
    out = capsys.readouterr().out
    assert "Moved 2 file(s)." in out and "Restored 3 file(s)." in out


def test_only_one_copy_can_hold_the_instance_lock(tmp_path: Path) -> None:
    from download_sorter.ui.tray_app import acquire_instance_lock

    first = acquire_instance_lock(tmp_path, "TestPipe")
    assert first is not None
    assert acquire_instance_lock(tmp_path, "TestPipe") is None, "a second copy must not start"
    assert acquire_instance_lock(tmp_path, "OtherDesktopPipe") is not None  # scoped by IPC name
    first.unlock()
    again = acquire_instance_lock(tmp_path, "TestPipe")
    assert again is not None
    again.unlock()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows file locking")
def test_instance_lock_is_released_when_its_process_dies(tmp_path: Path) -> None:
    from download_sorter.ui.tray_app import acquire_instance_lock

    env = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "QT_QPA_PLATFORM": "offscreen"}
    code = (
        "import sys, time; from pathlib import Path; "
        "from download_sorter.ui.tray_app import acquire_instance_lock; "
        f"lock = acquire_instance_lock(Path({str(tmp_path)!r}), 'TestPipe'); "
        "print('held' if lock else 'refused', flush=True); time.sleep(60)"
    )
    holder = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True, env=env)
    try:
        assert holder.stdout is not None and holder.stdout.readline().strip() == "held"
        assert acquire_instance_lock(tmp_path, "TestPipe") is None
    finally:
        holder.kill()  # no clean shutdown: a crash or Task Manager
        holder.wait(timeout=10)
    lock = None
    deadline = time.monotonic() + 5.0
    while lock is None and time.monotonic() < deadline:
        lock = acquire_instance_lock(tmp_path, "TestPipe")
        time.sleep(0.05)
    assert lock is not None, "a killed copy must not block the next start"
    lock.unlock()


def test_installer_relaunches_exactly_one_copy_with_a_real_flag() -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location("_v3_signoff_builder_src", ROOT / "installer_builder.py")
    assert spec is not None and spec.loader is not None
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    source = builder._generate_csharp_source("3.0.0", "A", "C", "https://example.invalid", "Download Sorter")

    from download_sorter.cli import build_parser

    registered = {opt for action in build_parser()._actions for opt in action.option_strings}
    launches = re.findall(r'Process\.Start\((?:exe|exePath)(?:, "([^"]*)")?\)', source)
    assert launches == ["--home", "--home"], launches  # update relaunch + interactive finish page
    assert all(flag in registered for flag in launches)
    assert "--welcome" not in source and "--welcome" not in (ROOT / "installer.iss").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Task 3: busy-file backoff and log de-duplication
# ---------------------------------------------------------------------------
class _FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _background_cycle(svc: SorterService, settle: float) -> dict | None:
    """One turn of the background loop: take what is due and process it."""
    due = svc._take_due_paths()
    if not due:
        return None
    return svc._process_paths(due, settle_seconds=settle, requeue_unsettled=True)


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 share-mode locking")
def test_locked_file_backs_off_and_clears_once_unlocked(
    app_paths: AppPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    from download_sorter import fs_ops

    # The fake clock drives the backoff; the file itself counts as long settled.
    monkeypatch.setattr(fs_ops, "is_file_settled", lambda *a, **k: True)
    svc = _service(app_paths)
    clock = _FakeClock()
    svc._clock = clock
    dl = app_paths.downloads_dir
    assert dl is not None
    busy = dl / "open-in-reader.pdf"
    busy.write_bytes(b"pdf")
    settle = 2.0

    with open(busy, "rb"):  # a PDF reader has it open
        svc._pending.add(busy)
        first = _background_cycle(svc, settle)
        assert first is not None and first["skipped_busy"] == 1 and first["moved"] == []
        assert busy in svc._pending and svc._busy_backoff[busy][0] == 1

        # Immediate cycles do nothing: the file is not due yet.
        for _ in range(5):
            assert _background_cycle(svc, settle) is None
        clock.advance(3.9)  # 2.0 * 2^1 = 4.0s
        assert _background_cycle(svc, settle) is None
        assert svc._next_wait_seconds() == pytest.approx(0.1, abs=0.01)

        clock.advance(0.2)
        second = _background_cycle(svc, settle)
        assert second is not None and second["skipped_busy"] == 1
        assert svc._busy_backoff[busy][0] == 2

        clock.advance(7.9)  # 2.0 * 2^2 = 8.0s
        assert _background_cycle(svc, settle) is None
        clock.advance(0.2)
        assert _background_cycle(svc, settle) is not None

        # The pause keeps doubling but never exceeds the cap.
        for _ in range(8):
            clock.advance(BUSY_RETRY_MAX_SECONDS + 1)
            assert _background_cycle(svc, settle) is not None
        attempts, next_retry, _msg = svc._busy_backoff[busy]
        assert attempts == 11
        assert next_retry - clock.now == pytest.approx(BUSY_RETRY_MAX_SECONDS)
        assert svc._next_wait_seconds() == 30.0  # loop sleep stays bounded
        assert busy.is_file()

    # Reader closed: the next due retry moves it and forgets the backoff.
    clock.advance(BUSY_RETRY_MAX_SECONDS + 1)
    moved = _background_cycle(svc, settle)
    assert moved is not None and len(moved["moved"]) == 1
    assert not busy.exists()
    assert svc._busy_backoff == {} and busy not in svc._pending
    assert svc._next_wait_seconds() == 30.0


def test_move_failure_is_logged_once_until_the_error_changes(
    app_paths: AppPaths, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    svc = _service(app_paths)
    clock = _FakeClock()
    svc._clock = clock
    dl = app_paths.downloads_dir
    assert dl is not None
    stuck = dl / "stuck.pdf"
    stuck.write_bytes(b"pdf")
    error = {"exc": PermissionError(13, "Access is denied")}

    def failing_move(*_a, **_k):  # noqa: ANN002, ANN003
        raise error["exc"]

    monkeypatch.setattr(service_module, "execute_move", failing_move)
    caplog.set_level(logging.DEBUG)

    def warnings() -> list[str]:
        return [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING and "stuck.pdf" in r.getMessage()]

    svc._pending.add(stuck)
    for _ in range(6):
        assert _background_cycle(svc, 1.5) is not None
        clock.advance(BUSY_RETRY_MAX_SECONDS + 1)
    assert len(warnings()) == 1, "the same failure must not be logged on every retry"
    assert "Access is denied" in warnings()[0]
    assert any(r.levelno == logging.DEBUG and "Still could not move" in r.getMessage() for r in caplog.records)

    error["exc"] = OSError(28, "No space left on device")  # a different problem is news again
    assert _background_cycle(svc, 1.5) is not None
    clock.advance(BUSY_RETRY_MAX_SECONDS + 1)
    assert _background_cycle(svc, 1.5) is not None
    assert len(warnings()) == 2 and "No space left" in warnings()[1]


def test_backoff_is_cleared_by_manual_sort_deletion_and_file_changes(
    app_paths: AppPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = _service(app_paths, settle_seconds=2.0)
    clock = _FakeClock()
    svc._clock = clock
    dl = app_paths.downloads_dir
    assert dl is not None
    a = dl / "a.pdf"
    b = dl / "b.pdf"
    a.write_bytes(b"a")
    b.write_bytes(b"b")

    real_execute = service_module.execute_move
    monkeypatch.setattr(service_module, "execute_move", lambda *x, **k: None)  # "busy"
    svc._pending.update({a, b})
    assert _background_cycle(svc, 2.0)["skipped_busy"] == 2
    for _ in range(3):  # push both well into the backoff
        clock.advance(BUSY_RETRY_MAX_SECONDS + 1)
        _background_cycle(svc, 2.0)
    assert svc._busy_backoff[a][0] == 4 and _background_cycle(svc, 2.0) is None

    # A filesystem event for the file (it was just written/closed) brings the retry
    # forward to one settle period, without resetting the attempt count.
    svc._enqueue(a)
    attempts, next_retry, _msg = svc._busy_backoff[a]
    assert attempts == 4 and next_retry - clock.now == pytest.approx(2.0)
    assert svc._busy_backoff[b][1] - clock.now > 30.0  # the other file is unaffected

    # A file the user deleted is forgotten on its next turn.
    b.unlink()
    clock.advance(BUSY_RETRY_MAX_SECONDS + 1)
    _background_cycle(svc, 2.0)
    assert b not in svc._busy_backoff and b not in svc._pending

    # An explicit "Sort now" starts from a clean slate and moves the file.
    monkeypatch.setattr(service_module, "execute_move", real_execute)
    assert a in svc._busy_backoff
    res = svc.scan_top_level_now(settle_seconds=0.0)
    assert len(res["moved"]) == 1 and svc._busy_backoff == {}


def test_manual_sort_does_not_back_off_or_queue_busy_files(
    app_paths: AppPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = _service(app_paths)
    dl = app_paths.downloads_dir
    assert dl is not None
    (dl / "a.pdf").write_bytes(b"a")
    monkeypatch.setattr(service_module, "execute_move", lambda *x, **k: None)
    res = svc.scan_top_level_now(settle_seconds=0.0)
    assert res["skipped_busy"] == 1
    assert svc._busy_backoff == {} and svc._pending == set()


# ---------------------------------------------------------------------------
# Task 2: the compiled installer against a genuinely locked file
# ---------------------------------------------------------------------------
GENERIC_READ = 0x80000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_DELETE = 0x00000004
OPEN_EXISTING = 3
_OLD_SUFFIX = re.compile(r"\.old_[0-9a-f]{32}$")


def _open_like_a_running_exe(path: Path) -> int:
    """Hold ``path`` the way Windows holds a running program's image: it can be
    read and renamed, but not overwritten or truncated (no FILE_SHARE_WRITE)."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    handle = kernel32.CreateFileW(
        str(path), GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_DELETE, None, OPEN_EXISTING, 0x80, None
    )
    assert handle not in (None, wintypes.HANDLE(-1).value), ctypes.WinError(ctypes.get_last_error())
    return handle


def _close_handle(handle: int) -> None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    assert kernel32.CloseHandle(handle)


@pytest.fixture(scope="module")
def tiny_installer(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The real installer program, compiled around a three-file stand-in payload."""
    import importlib.util

    if sys.platform != "win32":
        pytest.skip("Windows installer")
    spec = importlib.util.spec_from_file_location("_v3_signoff_installer_builder", ROOT / "installer_builder.py")
    assert spec is not None and spec.loader is not None
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    if builder.find_csc_compiler() is None:
        pytest.skip("csc.exe (.NET Framework compiler) not available")

    work = tmp_path_factory.mktemp("installer_build")
    payload = work / "payload"
    (payload / "_internal").mkdir(parents=True)
    (payload / "DownloadSorter.exe").write_bytes(b"NEW BUILD - main program")
    (payload / "DownloadSorter-cli.exe").write_bytes(b"NEW BUILD - cli")
    (payload / "_internal" / "library.dll").write_bytes(b"NEW BUILD - library")
    return builder.build_csharp_installer(work / "TinySetup.exe", payload, version="3.0.0")


def _run_installer(setup: Path, target: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(setup), "--silent", "--no-shortcuts", "--no-registry", f"/D={target}"],
        capture_output=True,
        text=True,
        timeout=120,
    )


def _renamed_aside(target: Path) -> list[Path]:
    return sorted(
        (p for p in target.rglob("*") if p.is_file() and _OLD_SUFFIX.search(p.name)),
        key=lambda p: p.name,
    )


@pytest.mark.skipif(sys.platform != "win32", reason="Windows installer")
def test_installer_replaces_a_locked_file_by_renaming_it_aside(tiny_installer: Path, tmp_path: Path) -> None:
    target = tmp_path / "Programs" / "DownloadSorter"
    (target / "_internal").mkdir(parents=True)
    locked_exe = target / "DownloadSorter.exe"
    locked_dll = target / "_internal" / "library.dll"
    locked_exe.write_bytes(b"OLD BUILD - still running")
    locked_dll.write_bytes(b"OLD BUILD - still loaded")
    (target / "DownloadSorter-cli.exe").write_bytes(b"OLD BUILD - cli, not in use")

    handles = [_open_like_a_running_exe(locked_exe), _open_like_a_running_exe(locked_dll)]
    try:
        # Sanity: this really is a file that cannot be overwritten.
        with pytest.raises(PermissionError):
            locked_exe.write_bytes(b"x")

        started = time.monotonic()
        first = _run_installer(tiny_installer, target)
        elapsed = time.monotonic() - started
        assert first.returncode == 0, first.stderr

        # The new files are in place under their real names...
        assert locked_exe.read_bytes() == b"NEW BUILD - main program"
        assert locked_dll.read_bytes() == b"NEW BUILD - library"
        assert (target / "DownloadSorter-cli.exe").read_bytes() == b"NEW BUILD - cli"
        # ...and each locked original was renamed aside, still intact and still held.
        aside = _renamed_aside(target)
        assert [p.name.split(".old_")[0] for p in aside] == ["DownloadSorter.exe", "library.dll"]
        assert {p.read_bytes() for p in aside} == {b"OLD BUILD - still running", b"OLD BUILD - still loaded"}
        assert all(p.parent == orig.parent for p, orig in zip(aside, [locked_exe, locked_dll]))
        # Four plain retries (50+100+200+400 ms) come before the rename, per locked file.
        assert elapsed >= 0.7, f"finished in {elapsed:.2f}s: the retry backoff did not run"
        assert elapsed < 60
    finally:
        for handle in handles:
            _close_handle(handle)

    # Second run, nothing locked any more: the leftovers are cleaned up.
    assert len(_renamed_aside(target)) == 2
    second = _run_installer(tiny_installer, target)
    assert second.returncode == 0, second.stderr
    assert _renamed_aside(target) == []
    assert locked_exe.read_bytes() == b"NEW BUILD - main program"
    assert sorted(p.name for p in target.rglob("*") if p.is_file()) == [
        "DownloadSorter-cli.exe",
        "DownloadSorter.exe",
        "library.dll",
    ]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows installer")
def test_installer_cleanup_leaves_lookalike_user_files_alone(tiny_installer: Path, tmp_path: Path) -> None:
    target = tmp_path / "Programs" / "DownloadSorter"
    target.mkdir(parents=True)
    stale = target / ("DownloadSorter.exe.old_" + "a" * 32)
    not_ours = target / "notes.old_backup"
    short = target / "thing.old_1234"
    for path in (stale, not_ours, short):
        path.write_bytes(b"x")

    res = _run_installer(tiny_installer, target)
    assert res.returncode == 0, res.stderr
    assert not stale.exists()
    assert not_ours.exists() and short.exists(), "only <name>.old_<32 hex> leftovers may be deleted"


# ---------------------------------------------------------------------------
# First run: nothing is sorted before the welcome steps are done
# ---------------------------------------------------------------------------
def _wait_for(condition, timeout: float = 5.0) -> bool:  # noqa: ANN001
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.02)
    return False


def test_new_install_sorts_nothing_until_the_welcome_steps_are_done(app_paths: AppPaths) -> None:
    svc = _service(app_paths, tutorial_completed=False, settle_seconds=0.05)
    dl = app_paths.downloads_dir
    assert dl is not None
    existing = dl / "already-here.pdf"
    existing.write_bytes(b"was in Downloads before the app was installed")

    svc.start()
    try:
        time.sleep(0.6)  # several loop turns
        arriving = dl / "arrives-during-setup.pdf"
        arriving.write_bytes(b"downloaded while the welcome steps are open")
        time.sleep(0.8)
        assert existing.is_file() and arriving.is_file(), "sorted before the user finished the welcome steps"
        assert svc.db.stats()["total_moves"] == 0

        # The user asked for it: a manual clean-up works even now.
        assert svc.simulate_top_level(), "preview must still work"

        # Welcome steps finished (or skipped): the waiting files are sorted.
        import copy

        cfg = copy.deepcopy(svc.config)
        cfg.tutorial_completed = True
        svc.update_config(cfg)
        assert _wait_for(lambda: not existing.exists() and not arriving.exists())
        assert svc.db.stats()["active_moves"] == 2
    finally:
        svc.stop(timeout=2.0)


def test_manual_sort_works_before_the_welcome_steps(app_paths: AppPaths) -> None:
    svc = _service(app_paths, tutorial_completed=False)
    dl = app_paths.downloads_dir
    assert dl is not None
    (dl / "report.pdf").write_bytes(b"pdf")
    assert svc._first_run_pending() is True
    assert len(svc.scan_top_level_now(settle_seconds=0.0)["moved"]) == 1


def test_closing_the_welcome_steps_any_way_counts_as_seen(
    v3_qapp, app_paths: AppPaths, monkeypatch: pytest.MonkeyPatch  # noqa: ANN001
) -> None:
    from download_sorter.ui import main_window as mw

    svc = _service(app_paths, tutorial_completed=False)
    win = mw.MainWindow(svc)
    # Esc / closing the tour returns "rejected"; sorting must not stay switched off forever.
    monkeypatch.setattr(mw.WalkthroughDialog, "exec", lambda self: mw.QDialog.DialogCode.Rejected)
    win.open_walkthrough()
    assert svc.config.tutorial_completed is True and svc._first_run_pending() is False
    win.close()


def test_first_run_opens_the_window_even_when_started_in_the_tray() -> None:
    from download_sorter.ui import tray_app

    source = inspect.getsource(tray_app.run_app)
    assert "if not service.config.tutorial_completed:" in source
    assert source.index("if not service.config.tutorial_completed:") < source.index("service.start()")
