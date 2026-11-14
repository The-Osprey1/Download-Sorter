"""Recency-Gated Quick Switcher & Recent File Insert engine for Download Sorter v3.

Identifies active Windows File Open/Upload dialogs (#32770) within the recency window
of freshly sorted downloads and injects or offers recently downloaded files with zero friction.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import json
import logging
import os
from pathlib import Path
import shutil
import struct
import sys
import tempfile
import threading
import time
from typing import Any, Callable, Iterable
import uuid

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    try:
        _k32 = ctypes.windll.kernel32
        _u32 = ctypes.windll.user32
        _s32 = ctypes.windll.shell32

        _k32.GlobalAlloc.restype = wintypes.HGLOBAL
        _k32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        _k32.GlobalLock.restype = ctypes.c_void_p
        _k32.GlobalLock.argtypes = [wintypes.HGLOBAL]
        _k32.GlobalUnlock.restype = wintypes.BOOL
        _k32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        _k32.GlobalFree.restype = wintypes.HGLOBAL
        _k32.GlobalFree.argtypes = [wintypes.HGLOBAL]

        _u32.OpenClipboard.restype = wintypes.BOOL
        _u32.OpenClipboard.argtypes = [wintypes.HWND]
        _u32.CloseClipboard.restype = wintypes.BOOL
        _u32.CloseClipboard.argtypes = []
        _u32.EmptyClipboard.restype = wintypes.BOOL
        _u32.EmptyClipboard.argtypes = []
        _u32.SetClipboardData.restype = wintypes.HANDLE
        _u32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
        _u32.GetClipboardData.restype = wintypes.HANDLE
        _u32.GetClipboardData.argtypes = [wintypes.UINT]

        _u32.GetWindowThreadProcessId.restype = wintypes.DWORD
        _u32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        _u32.PostMessageW.restype = wintypes.BOOL
        _u32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        _u32.IsWindow.restype = wintypes.BOOL
        _u32.IsWindow.argtypes = [wintypes.HWND]
        _u32.IsWindowVisible.restype = wintypes.BOOL
        _u32.IsWindowVisible.argtypes = [wintypes.HWND]

        _s32.DragQueryFileW.restype = wintypes.UINT
        _s32.DragQueryFileW.argtypes = [wintypes.HANDLE, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT]
    except (AttributeError, TypeError):
        pass

from download_sorter.paths import AppPaths

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STATE_NAME = "recent_files.json"
STAGE_DIR_NAME = ".sorter_stage"

THREE_D_EXTENSIONS: tuple[str, ...] = (".3mf", ".stl", ".step", ".stp", ".f3d", ".f3z")
THREE_D_MAX_AGE_SEC: float = 12 * 3600.0  # 12 hours for CAD / 3D printing workflows
DEFAULT_RECENCY_TTL: float = 300.0        # 5 minutes for general downloads

CF_HDROP = 15
DROPEFFECT_COPY = 1
TYMED_HGLOBAL = 1
WM_SETTEXT = 0x000C
WM_GETTEXT = 0x000D
WM_GETTEXTLENGTH = 0x000E
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_COMMAND = 0x0111
WM_CLOSE = 0x0010
VK_RETURN = 0x0D
IDOK = 1
IDCANCEL = 2

ADMIN_WARNING = "Can't reach admin windows — drag instead"
TOO_MANY_WARNING = "Too many files — drag instead"

_THREAD_LOCK = threading.RLock()


# ---------------------------------------------------------------------------
# State File Management
# ---------------------------------------------------------------------------

def resolve_state_file(data_dir: Path | str | None = None) -> Path:
    if data_dir is not None:
        return Path(data_dir) / STATE_NAME
    try:
        return AppPaths.default().recent_state_file
    except Exception:
        return Path(tempfile.gettempdir()) / f"download_sorter_v3_{STATE_NAME}"


@contextmanager
def state_lock(data_dir: Path | str | None = None, timeout: float = 2.0):
    """Cross-process file lock around read-modify-write of the recent state file."""
    with _THREAD_LOCK:
        handle = None
        locked = False
        msvcrt = None
        if sys.platform == "win32":
            try:
                import msvcrt  # type: ignore[import-not-found]

                lock_path = resolve_state_file(data_dir).with_suffix(".lock")
                lock_path.parent.mkdir(parents=True, exist_ok=True)
                handle = open(lock_path, "a+")
                deadline = time.time() + timeout
                while True:
                    try:
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                        locked = True
                        break
                    except OSError:
                        if time.time() >= deadline:
                            break
                        time.sleep(0.02)
            except OSError:
                handle = None
        try:
            yield
        finally:
            if handle is not None:
                try:
                    if locked and msvcrt is not None:
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                finally:
                    handle.close()


def _read_state_data(data_dir: Path | str | None = None) -> dict[str, Any] | None:
    target = resolve_state_file(data_dir)
    if not target.exists():
        return None
    for _attempt in range(3):
        try:
            with open(target, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else None
        except (OSError, ValueError):
            time.sleep(0.04)
    return None


def _write_state_data(data: dict[str, Any], data_dir: Path | str | None = None) -> bool:
    target = resolve_state_file(data_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f"{target.stem}.{os.getpid()}.{uuid.uuid4().hex[:6]}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
    except OSError as exc:
        logging.debug("Could not write recent state tmp: %s", exc)
        return False

    for _attempt in range(4):
        try:
            os.replace(tmp, target)
            return True
        except OSError:
            time.sleep(0.05)
    try:
        tmp.unlink(missing_ok=True)
    except OSError:
        pass
    return False


def _items_from_state(state: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not state:
        return []
    items = state.get("items")
    if isinstance(items, list):
        return [dict(i) for i in items if isinstance(i, dict) and "path" in i]
    if "path" in state and "timestamp" in state:
        return [{k: state[k] for k in ("path", "name", "folder", "category", "color", "timestamp") if k in state}]
    return []


def _file_exists(item: dict[str, Any]) -> bool:
    try:
        p = Path(item["path"])
        return p.exists() and p.is_file()
    except (OSError, KeyError):
        return False


def _compose_state(items: list[dict[str, Any]], batch_id: str) -> dict[str, Any]:
    latest = items[-1]
    return {
        "schema": 2,
        "path": latest["path"],
        "name": latest.get("name", Path(latest["path"]).name),
        "folder": latest.get("folder", ""),
        "category": latest.get("category", "default"),
        "color": latest.get("color", "#69E6AD"),
        "timestamp": latest["timestamp"],
        "batch_id": batch_id,
        "batch_started": items[0]["timestamp"],
        "items": items,
    }


# ---------------------------------------------------------------------------
# Recency Registration & Queries
# ---------------------------------------------------------------------------

def record_recent_file(
    dest_path: str | Path,
    downloads_root: str | Path | None = None,
    batch_id: str | None = None,
    *,
    settings: dict[str, Any] | None = None,
    now: float | None = None,
    data_dir: Path | str | None = None,
) -> None:
    """Record a freshly sorted file into the recent file buffer, with batch coalescing."""
    dest = Path(dest_path).resolve()
    if not dest.exists() or not dest.is_file():
        return

    dl_root = Path(downloads_root).resolve() if downloads_root else Path.home() / "Downloads"
    try:
        rel = dest.relative_to(dl_root)
        folder_disp = str(rel.parent) if rel.parent != Path(".") else "Downloads"
    except ValueError:
        folder_disp = dest.parent.name

    category = "default"
    color = "#69E6AD"
    try:
        from download_sorter.ui.toasts import category_info
        color, _badge, category = category_info(dest, folder_disp)
    except Exception:
        ext = dest.suffix.lower()
        if ext in THREE_D_EXTENSIONS:
            category = "cad"
            color = "#69E6AD"

    timestamp = time.time() if now is None else float(now)
    cfg = settings or {}
    gap = float(cfg.get("gap_seconds", 25.0))
    span = float(cfg.get("max_span_seconds", 120.0))
    cap = int(cfg.get("max_files", 50))

    item: dict[str, Any] = {
        "path": str(dest),
        "name": dest.name,
        "folder": folder_disp,
        "category": category,
        "color": color,
        "timestamp": timestamp,
    }
    if batch_id:
        item["source_batch"] = str(batch_id)

    with state_lock(data_dir):
        state = _read_state_data(data_dir)
        items = [i for i in _items_from_state(state) if _file_exists(i)]
        state_batch = str((state or {}).get("batch_id") or uuid.uuid4().hex[:8])

        joined = False
        if items:
            for existing in items:
                if existing["path"] == item["path"] and timestamp - float(existing["timestamp"]) < gap:
                    return  # already recorded recently
            last, first = items[-1], items[0]
            same_pass = bool(batch_id) and last.get("source_batch") == str(batch_id)
            by_time = (
                timestamp - float(last["timestamp"]) <= gap
                and timestamp - float(first["timestamp"]) <= span
            )
            joined = same_pass or by_time

        if joined:
            items = [i for i in items if i["path"] != item["path"]]
            if len(items) >= cap:
                items = items[len(items) - cap + 1 :]
        else:
            items = []
            state_batch = uuid.uuid4().hex[:8]

        items.append(item)
        _write_state_data(_compose_state(items, state_batch), data_dir)

    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SHAddToRecentDocs(0x00000003, str(dest))
        except Exception:
            pass


def get_recent_file(
    max_age_sec: float = DEFAULT_RECENCY_TTL,
    data_dir: Path | str | None = None,
) -> dict[str, Any] | None:
    """Return the most recently sorted offering, with 12h extension for 3D/CAD files."""
    state = _read_state_data(data_dir)
    items = [i for i in _items_from_state(state) if _file_exists(i)]
    if not items:
        return None

    latest = items[-1]
    try:
        age = time.time() - float(latest["timestamp"])
    except (TypeError, ValueError, KeyError):
        return None

    limit = max_age_sec
    if str(latest.get("path", "")).lower().endswith(THREE_D_EXTENSIONS):
        limit = max(max_age_sec, THREE_D_MAX_AGE_SEC)

    if age > limit:
        return None

    data = dict(latest)
    data["age_sec"] = age
    data["items"] = items
    data["batch_id"] = str((state or {}).get("batch_id") or "")
    data["batch_count"] = len(items)
    return data


get_recent_batch = get_recent_file


def clear_recent_file(data_dir: Path | str | None = None) -> None:
    target = resolve_state_file(data_dir)
    try:
        if target.exists():
            target.unlink(missing_ok=True)
    except OSError:
        pass


def invalidate_recent_file(undone_path: str | Path, data_dir: Path | str | None = None) -> None:
    """Remove an undone file from the recent file offerings."""
    consume_items([undone_path], data_dir=data_dir)


def consume_items(paths: Iterable[str | Path], data_dir: Path | str | None = None) -> None:
    """Remove consumed/handed-off files from the batch buffer, clearing state if empty."""
    gone = {str(Path(p).resolve()) for p in paths}
    with state_lock(data_dir):
        state = _read_state_data(data_dir)
        if not state:
            return
        batch = str(state.get("batch_id") or uuid.uuid4().hex[:8])
        items = [
            i for i in _items_from_state(state)
            if str(Path(i["path"]).resolve()) not in gone and _file_exists(i)
        ]
        if not items:
            clear_recent_file(data_dir)
            return
        _write_state_data(_compose_state(items, batch), data_dir)


# ---------------------------------------------------------------------------
# Win32 Dialog Discovery & Safe Verification
# ---------------------------------------------------------------------------

def is_file_dialog(hwnd: int) -> bool:
    """Return True if hwnd is an active Open/Upload File Dialog (#32770), skipping Save dialogs."""
    if sys.platform != "win32" or isinstance(hwnd, bool) or not isinstance(hwnd, int) or hwnd <= 0 or hwnd > 0xFFFFFFFFFFFFFFFF:
        return False

    user32 = ctypes.windll.user32
    try:
        if not user32.IsWindow(hwnd) or not user32.IsWindowVisible(hwnd):
            return False

        class_name = (ctypes.c_wchar * 256)()
        user32.GetClassNameW(hwnd, class_name, 256)
        if class_name.value != "#32770":
            return False

        title = (ctypes.c_wchar * 256)()
        user32.GetWindowTextW(hwnd, title, 256)
        title_lower = title.value.lower()
        if (
            title_lower.startswith(("save", "speichern", "enregistrer", "guardar"))
            or "save as" in title_lower
            or "save file" in title_lower
            or "speichern unter" in title_lower
            or "enregistrer sous" in title_lower
            or "guardar como" in title_lower
        ):
            return False

        has_edit = False
        is_save_btn = False

        def enum_child(h: int, _: int) -> bool:
            nonlocal has_edit, is_save_btn
            cn = (ctypes.c_wchar * 256)()
            user32.GetClassNameW(h, cn, 256)
            cname = cn.value
            if cname in ("ComboBoxEx32", "Edit"):
                has_edit = True
            elif cname == "Button":
                btn_txt = (ctypes.c_wchar * 256)()
                user32.GetWindowTextW(h, btn_txt, 256)
                txt = btn_txt.value.strip().lower().replace("&", "")
                save_verbs = ("save", "speichern", "enregistrer", "guardar")
                if txt in save_verbs or any(txt.startswith(f"{v} ") for v in save_verbs):
                    is_save_btn = True
            return True

        WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        user32.EnumChildWindows(hwnd, WNDENUMPROC(enum_child), 0)

        if not has_edit or is_save_btn:
            return False

        return True
    except Exception:
        return False


def find_dialog_edit_control(dialog_hwnd: int) -> int | None:
    """Find the filename Edit control inside the Windows file dialog (#32770)."""
    if sys.platform != "win32" or isinstance(dialog_hwnd, bool) or not isinstance(dialog_hwnd, int) or dialog_hwnd <= 0 or dialog_hwnd > 0xFFFFFFFFFFFFFFFF:
        return None

    user32 = ctypes.windll.user32
    target_edit = None
    fallback_edit = None

    try:
        if not user32.IsWindow(dialog_hwnd):
            return None

        def enum_child(hwnd: int, _: int) -> bool:
            nonlocal target_edit, fallback_edit
            class_name = (ctypes.c_wchar * 256)()
            user32.GetClassNameW(hwnd, class_name, 256)
            if class_name.value == "Edit":
                if user32.IsWindowVisible(hwnd) and user32.IsWindowEnabled(hwnd):
                    ctrl_id = user32.GetDlgCtrlID(hwnd)
                    # 1148 (0x47C) = ComboBoxEx32 filename box; 1152 (0x480) = Edit filename box
                    if ctrl_id in (1148, 1152):
                        target_edit = hwnd
                        return False
                    parent = user32.GetParent(hwnd)
                    if parent and user32.GetDlgCtrlID(parent) in (1148, 1152):
                        target_edit = hwnd
                        return False
                    if fallback_edit is None:
                        fallback_edit = hwnd
            return True

        WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        user32.EnumChildWindows(dialog_hwnd, WNDENUMPROC(enum_child), 0)
        return target_edit or fallback_edit
    except Exception:
        return None


def inject_file_into_dialog(dialog_hwnd: int, file_path: str | Path) -> bool:
    """Inject a file path into the dialog's edit control and trigger Open/IDOK."""
    if sys.platform != "win32" or isinstance(dialog_hwnd, bool) or not isinstance(dialog_hwnd, int) or dialog_hwnd <= 0 or dialog_hwnd > 0xFFFFFFFFFFFFFFFF:
        return False

    target = Path(file_path).resolve()
    if not target.exists():
        return False

    edit_hwnd = find_dialog_edit_control(dialog_hwnd)
    if not edit_hwnd:
        return False

    user32 = ctypes.windll.user32
    path_str = str(target)

    try:
        user32.SetForegroundWindow(dialog_hwnd)
    except Exception:
        pass

    try:
        user32.SendMessageW(edit_hwnd, WM_SETTEXT, 0, path_str)
        time.sleep(0.06)

        user32.PostMessageW(edit_hwnd, WM_KEYDOWN, VK_RETURN, 0)
        user32.PostMessageW(edit_hwnd, WM_KEYUP, VK_RETURN, 0)
        user32.PostMessageW(dialog_hwnd, WM_COMMAND, IDOK, 0)
        return True
    except Exception as exc:
        logging.debug("Error injecting file into dialog: %s", exc)
        return False


# ---------------------------------------------------------------------------
# UIPI Elevation Checking
# ---------------------------------------------------------------------------

def _token_elevated(process_handle: int) -> bool | None:
    advapi32 = ctypes.windll.advapi32
    token = wintypes.HANDLE()
    advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    if not advapi32.OpenProcessToken(process_handle, 0x0008, ctypes.byref(token)):  # TOKEN_QUERY
        return None
    try:
        elevation = wintypes.DWORD(0)
        size = wintypes.DWORD(0)
        advapi32.GetTokenInformation.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
        ]
        ok = advapi32.GetTokenInformation(token, 20, ctypes.byref(elevation), 4, ctypes.byref(size))  # TokenElevation
        return bool(elevation.value) if ok else None
    finally:
        ctypes.windll.kernel32.CloseHandle(token)


def window_is_elevated(hwnd: int) -> bool | None:
    if sys.platform != "win32" or isinstance(hwnd, bool) or not isinstance(hwnd, int) or hwnd <= 0 or hwnd > 0xFFFFFFFFFFFFFFFF:
        return None
    try:
        user32 = ctypes.windll.user32
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        pid = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return None
        kernel32 = ctypes.windll.kernel32
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        handle = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return True  # cannot open -> treat as elevated
        try:
            return _token_elevated(handle)
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return None


def self_is_elevated() -> bool:
    if sys.platform != "win32":
        return False
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        return bool(_token_elevated(kernel32.GetCurrentProcess()))
    except Exception:
        return False


def blocked_by_uipi(hwnd: int) -> bool:
    """Return True if hwnd runs elevated while current process is non-elevated."""
    try:
        return bool(window_is_elevated(hwnd)) and not self_is_elevated()
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Batch Staging & Multi-File Dialog Injection
# ---------------------------------------------------------------------------

@dataclass
class InjectionPlan:
    kind: str  # "single" | "same_folder" | "staged" | "drag_only"
    paths: list[Path]
    folder: Path | None = None
    names: list[str] = field(default_factory=list)
    stage_dir: Path | None = None
    message: str = ""


@dataclass
class InjectResult:
    ok: bool
    mode: str  # "single" | "multi" | "fallback-latest" | "blocked" | "failed"
    message: str = ""
    injected: list[str] = field(default_factory=list)


def quote_names(names: Iterable[str]) -> str:
    """Standard Win32 Common Dialog multi-file syntax: "file1" "file2"."""
    return " ".join(f'"{n}"' for n in names)


def total_size_mb(paths: Iterable[Path]) -> float:
    total = 0
    for p in paths:
        try:
            total += Path(p).stat().st_size
        except OSError:
            pass
    return total / (1024 * 1024)


def make_hidden(path: Path) -> None:
    if sys.platform == "win32":
        try:
            ctypes.windll.kernel32.SetFileAttributesW(str(path), 0x02)  # FILE_ATTRIBUTE_HIDDEN
        except Exception:
            pass


def same_volume(a: Path, b: Path) -> bool:
    try:
        return Path(a).resolve().drive.upper() == Path(b).resolve().drive.upper()
    except Exception:
        return True


def plan_injection(
    paths: Iterable[str | Path],
    *,
    downloads_root: str | Path | None = None,
    settings: dict[str, Any] | None = None,
) -> InjectionPlan:
    cfg = settings or {}
    resolved = [Path(p).resolve() for p in paths if Path(p).exists()]
    if not resolved:
        return InjectionPlan("drag_only", [], message="Nothing to inject")
    if len(resolved) == 1:
        return InjectionPlan("single", resolved, folder=resolved[0].parent, names=[resolved[0].name])
    folders = {p.parent for p in resolved}
    if len(folders) == 1:
        folder = next(iter(folders))
        return InjectionPlan("same_folder", resolved, folder=folder, names=[p.name for p in resolved])

    root = Path(downloads_root).resolve() if downloads_root else Path.home() / "Downloads"
    same_vol = all(same_volume(p, root) for p in resolved)
    small = total_size_mb(resolved) < float(cfg.get("stage_copy_limit_mb", 200.0))
    if same_vol or small:
        return InjectionPlan("staged", resolved, folder=root / STAGE_DIR_NAME, names=[p.name for p in resolved])
    return InjectionPlan("drag_only", resolved, message=TOO_MANY_WARNING)


def stage_batch(
    paths: Iterable[str | Path],
    batch_id: str,
    downloads_root: str | Path | None = None,
    settings: dict[str, Any] | None = None,
) -> tuple[Path, list[Path]]:
    """Hardlink (or copy under size limit) files into one hidden folder."""
    cfg = settings or {}
    root = Path(downloads_root).resolve() if downloads_root else Path.home() / "Downloads"
    stage = root / STAGE_DIR_NAME / (batch_id or uuid.uuid4().hex[:8])
    stage.mkdir(parents=True, exist_ok=True)
    make_hidden(stage.parent)

    staged: list[Path] = []
    copy_limit_mb = float(cfg.get("stage_copy_limit_mb", 200.0))

    for src in (Path(p).resolve() for p in paths):
        dest = stage / src.name
        try:
            if sys.platform == "win32":
                os.link(src, dest)
            else:
                dest.symlink_to(src)
            staged.append(dest)
        except OSError:
            if total_size_mb([src]) < copy_limit_mb:
                shutil.copy2(src, dest)
                staged.append(dest)
            else:
                raise
    return stage, staged


def prune_stages(downloads_root: str | Path | None = None, max_age_hours: float = 24.0) -> int:
    """Delete stage folders older than max_age_hours."""
    root = (Path(downloads_root).resolve() if downloads_root else Path.home() / "Downloads") / STAGE_DIR_NAME
    removed = 0
    if not root.is_dir():
        return 0
    cutoff = time.time() - max_age_hours * 3600.0
    for child in root.iterdir():
        try:
            if child.is_dir() and child.stat().st_mtime < cutoff:
                shutil.rmtree(child, ignore_errors=True)
                removed += 1
        except OSError:
            continue
    return removed


def wait_until(predicate: Callable[[], bool], timeout: float = 2.0, interval: float = 0.04) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if predicate():
                return True
        except Exception:
            pass
        time.sleep(interval)
    return False


def find_owned_error_dialog(dialog_hwnd: int, wait: float = 0.5) -> int:
    if sys.platform != "win32" or not dialog_hwnd:
        return 0
    user32 = ctypes.windll.user32
    found = {"hwnd": 0}
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def enum_proc(hwnd: int, _: int) -> bool:
        if hwnd == dialog_hwnd or not user32.IsWindowVisible(hwnd):
            return True
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, cls, 64)
        if cls.value == "#32770" and user32.GetWindow(hwnd, 4) == dialog_hwnd:  # GW_OWNER = 4
            found["hwnd"] = hwnd
            return False
        return True

    def scan() -> bool:
        found["hwnd"] = 0
        user32.EnumWindows(WNDENUMPROC(enum_proc), 0)
        return bool(found["hwnd"])

    wait_until(scan, timeout=wait, interval=0.03)
    return found["hwnd"]


def dismiss_dialog(hwnd: int) -> None:
    if sys.platform != "win32" or isinstance(hwnd, bool) or not isinstance(hwnd, int) or hwnd <= 0 or hwnd > 0xFFFFFFFFFFFFFFFF:
        return
    try:
        user32 = ctypes.windll.user32
        try:
            user32.PostMessageW.restype = wintypes.BOOL
            user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
            user32.IsWindow.restype = wintypes.BOOL
            user32.IsWindow.argtypes = [wintypes.HWND]
        except (AttributeError, TypeError):
            pass
        user32.PostMessageW(hwnd, WM_COMMAND, IDOK, 0)
        time.sleep(0.05)
        if user32.IsWindow(hwnd):
            user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
    except Exception:
        return


def inject_files_into_dialog(
    dialog_hwnd: int,
    paths: Iterable[str | Path],
    *,
    batch_id: str | None = None,
    downloads_root: str | Path | None = None,
    settings: dict[str, Any] | None = None,
) -> InjectResult:
    """Select several files in an Open dialog; degrades gracefully to single-file fallback."""
    if sys.platform != "win32" or isinstance(dialog_hwnd, bool) or not isinstance(dialog_hwnd, int) or dialog_hwnd <= 0 or dialog_hwnd > 0xFFFFFFFFFFFFFFFF:
        return InjectResult(False, "failed", "Dialog unavailable")

    cfg = settings or {}
    plan = plan_injection(paths, downloads_root=downloads_root, settings=cfg)
    if plan.kind == "drag_only":
        return InjectResult(False, "failed", plan.message or TOO_MANY_WARNING)
    if blocked_by_uipi(dialog_hwnd):
        return InjectResult(False, "blocked", ADMIN_WARNING)

    latest = plan.paths[-1]
    if plan.kind == "single":
        ok = inject_file_into_dialog(dialog_hwnd, latest)
        return InjectResult(ok, "single", "" if ok else "Dialog unavailable", [str(latest)] if ok else [])

    folder, names, injected = plan.folder, plan.names, [str(p) for p in plan.paths]
    if plan.kind == "staged":
        try:
            prune_stages(downloads_root, float(cfg.get("stage_prune_hours", 24.0)))
            stage, staged = stage_batch(plan.paths, batch_id or uuid.uuid4().hex[:8], downloads_root, cfg)
        except Exception as exc:
            logging.debug("Batch staging failed: %s", exc)
            ok = inject_file_into_dialog(dialog_hwnd, latest)
            return InjectResult(ok, "fallback-latest", TOO_MANY_WARNING, [str(latest)] if ok else [])
        folder, names = stage, [p.name for p in staged]

    edit = find_dialog_edit_control(dialog_hwnd)
    if not edit or folder is None:
        return InjectResult(False, "failed", "Dialog unavailable")

    user32 = ctypes.windll.user32
    try:
        user32.SetForegroundWindow(dialog_hwnd)
    except Exception:
        pass

    def _get_text(h: int) -> str:
        length = user32.SendMessageW(h, WM_GETTEXTLENGTH, 0, 0)
        buf = ctypes.create_unicode_buffer(length + 2)
        user32.SendMessageW(h, WM_GETTEXT, length + 1, buf)
        return buf.value

    def _press_enter(d_h: int, e_h: int) -> None:
        user32.PostMessageW(e_h, WM_KEYDOWN, VK_RETURN, 0)
        user32.PostMessageW(e_h, WM_KEYUP, VK_RETURN, 0)
        user32.PostMessageW(d_h, WM_COMMAND, IDOK, 0)

    # 1. Navigate to folder
    user32.SendMessageW(edit, WM_SETTEXT, 0, str(folder))
    _press_enter(dialog_hwnd, edit)
    navigated = wait_until(lambda: _get_text(edit).strip() != str(folder), timeout=2.0)
    if not navigated:
        user32.SendMessageW(edit, WM_SETTEXT, 0, "")
        ok = inject_file_into_dialog(dialog_hwnd, latest)
        return InjectResult(ok, "fallback-latest", "Could not open folder — injected latest", [str(latest)] if ok else [])

    # 2. Enter quoted names
    user32.SendMessageW(edit, WM_SETTEXT, 0, quote_names(names))
    _press_enter(dialog_hwnd, edit)
    popup = find_owned_error_dialog(dialog_hwnd, wait=0.5)
    if popup:
        dismiss_dialog(popup)
        user32.SendMessageW(edit, WM_SETTEXT, 0, "")
        ok = inject_file_into_dialog(dialog_hwnd, latest)
        return InjectResult(
            ok,
            "fallback-latest",
            "This dialog takes one file — injected latest. Drag the pill for all.",
            [str(latest)] if ok else [],
        )

    return InjectResult(True, "multi", "", injected)


# ---------------------------------------------------------------------------
# Windows Clipboard (CF_HDROP) & OLE Drag-and-Drop
# ---------------------------------------------------------------------------

def build_dropfiles(paths: Iterable[str | Path]) -> bytes:
    """Build a DROPFILES struct header followed by double-null-terminated UTF-16LE paths."""
    wide = "".join(f"{p}\0" for p in paths) + "\0"
    header = struct.pack("<IiiII", 20, 0, 0, 0, 1)  # pFiles=20, pt.x=0, pt.y=0, fNC=0, fWide=1
    return header + wide.encode("utf-16-le")


def copy_files_to_clipboard(paths: Iterable[str | Path]) -> bool:
    """Copy file paths to the Windows clipboard as CF_HDROP payload (Ctrl+V pasteable)."""
    if sys.platform != "win32":
        return False
    resolved = [str(Path(p).resolve()) for p in paths if Path(p).exists()]
    if not resolved:
        return False

    k32 = ctypes.windll.kernel32
    u32 = ctypes.windll.user32

    k32.GlobalAlloc.restype = wintypes.HGLOBAL
    k32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    k32.GlobalLock.restype = ctypes.c_void_p
    k32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    k32.GlobalUnlock.restype = wintypes.BOOL
    k32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    k32.GlobalFree.restype = wintypes.HGLOBAL
    k32.GlobalFree.argtypes = [wintypes.HGLOBAL]

    u32.OpenClipboard.restype = wintypes.BOOL
    u32.OpenClipboard.argtypes = [wintypes.HWND]
    u32.CloseClipboard.restype = wintypes.BOOL
    u32.CloseClipboard.argtypes = []
    u32.EmptyClipboard.restype = wintypes.BOOL
    u32.EmptyClipboard.argtypes = []
    u32.SetClipboardData.restype = wintypes.HANDLE
    u32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]

    payload = build_dropfiles(resolved)

    h_global = k32.GlobalAlloc(0x0002 | 0x0040, len(payload))  # GMEM_MOVEABLE | GMEM_ZEROINIT
    if not h_global:
        return False
    ptr = k32.GlobalLock(h_global)
    if not ptr:
        k32.GlobalFree(h_global)
        return False
    ctypes.memmove(ptr, payload, len(payload))
    k32.GlobalUnlock(h_global)

    effect_payload = struct.pack("<I", DROPEFFECT_COPY)
    h_effect = k32.GlobalAlloc(0x0002 | 0x0040, len(effect_payload))
    if h_effect:
        e_ptr = k32.GlobalLock(h_effect)
        if e_ptr:
            ctypes.memmove(e_ptr, effect_payload, len(effect_payload))
            k32.GlobalUnlock(h_effect)

    opened = False
    for _ in range(10):
        if u32.OpenClipboard(None):
            opened = True
            break
        time.sleep(0.03)

    if not opened:
        k32.GlobalFree(h_global)
        if h_effect:
            k32.GlobalFree(h_effect)
        return False

    try:
        u32.EmptyClipboard()
        u32.SetClipboardData(CF_HDROP, h_global)
        if h_effect:
            fmt = u32.RegisterClipboardFormatW("Preferred DropEffect")
            u32.SetClipboardData(fmt, h_effect)
        return True
    finally:
        u32.CloseClipboard()


def read_clipboard_files() -> list[str]:
    """Diagnostic/test helper: read CF_HDROP file paths currently on clipboard."""
    if sys.platform != "win32":
        return []
    u32 = ctypes.windll.user32
    s32 = ctypes.windll.shell32

    u32.OpenClipboard.restype = wintypes.BOOL
    u32.OpenClipboard.argtypes = [wintypes.HWND]
    u32.CloseClipboard.restype = wintypes.BOOL
    u32.CloseClipboard.argtypes = []
    u32.GetClipboardData.restype = wintypes.HANDLE
    u32.GetClipboardData.argtypes = [wintypes.UINT]

    s32.DragQueryFileW.restype = wintypes.UINT
    s32.DragQueryFileW.argtypes = [wintypes.HANDLE, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT]

    if not u32.OpenClipboard(None):
        return []
    try:
        hdrop = u32.GetClipboardData(CF_HDROP)
        if not hdrop:
            return []
        count = s32.DragQueryFileW(hdrop, 0xFFFFFFFF, None, 0)
        out = []
        for i in range(count):
            length = s32.DragQueryFileW(hdrop, i, None, 0)
            buf = ctypes.create_unicode_buffer(length + 1)
            s32.DragQueryFileW(hdrop, i, buf, length + 1)
            out.append(buf.value)
        return out
    finally:
        u32.CloseClipboard()


def _com_call(obj: Any, index: int, restype: Any, argtypes: list[Any], *args: Any) -> Any:
    vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    prototype = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)
    return prototype(vtbl[index])(obj, *args)


def create_file_data_object(paths: Iterable[str | Path]) -> Any:
    """Create a native Windows shell IDataObject carrying CF_HDROP."""
    if sys.platform != "win32":
        return None
    resolved = [str(Path(p).resolve()) for p in paths if Path(p).exists()]
    if not resolved:
        return None

    shell32, ole32 = ctypes.windll.shell32, ctypes.windll.ole32
    shell32.SHParseDisplayName.argtypes = [
        wintypes.LPCWSTR, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), wintypes.ULONG, ctypes.c_void_p,
    ]
    shell32.CIDLData_CreateFromIDArray.argtypes = [
        ctypes.c_void_p, wintypes.UINT, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
    ]
    ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    shell32.ILFindLastID.argtypes = [ctypes.c_void_p]
    shell32.ILFindLastID.restype = ctypes.c_void_p

    parents = {os.path.dirname(p).lower() for p in resolved}
    same_dir = len(parents) == 1
    pidls = (ctypes.c_void_p * len(resolved))()
    owned: list[int] = []
    folder_pidl = ctypes.c_void_p()

    try:
        if same_dir:
            if shell32.SHParseDisplayName(os.path.dirname(resolved[0]), None, ctypes.byref(folder_pidl), 0, None) != 0:
                same_dir = False
                folder_pidl = ctypes.c_void_p()
        for idx, path in enumerate(resolved):
            pidl = ctypes.c_void_p()
            if shell32.SHParseDisplayName(path, None, ctypes.byref(pidl), 0, None) != 0 or not pidl.value:
                return None
            owned.append(pidl.value)
            pidls[idx] = shell32.ILFindLastID(pidl.value) if same_dir else pidl.value
        data_object = ctypes.c_void_p()
        hr = shell32.CIDLData_CreateFromIDArray(
            folder_pidl if same_dir else None, len(resolved), pidls, ctypes.byref(data_object)
        )
        return data_object if hr == 0 and data_object.value else None
    finally:
        for val in owned:
            ole32.CoTaskMemFree(val)
        if folder_pidl.value:
            ole32.CoTaskMemFree(folder_pidl.value)


def start_file_drag(paths: Iterable[str | Path], hwnd: int | None = None) -> int:
    """Initiate a native Windows OLE file drag (CF_HDROP). Returns drop effect."""
    if sys.platform != "win32":
        return 0
    ole32, shell32 = ctypes.windll.ole32, ctypes.windll.shell32
    try:
        ole32.OleInitialize(None)
    except Exception:
        pass
    data_object = create_file_data_object(paths)
    if data_object is None:
        return 0
    try:
        shell32.SHDoDragDrop.argtypes = [
            wintypes.HWND, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
        ]
        effect = wintypes.DWORD(0)
        hr = shell32.SHDoDragDrop(hwnd, data_object, None, DROPEFFECT_COPY, ctypes.byref(effect))
        return int(effect.value) if hr in (0, 0x00040100) else 0
    finally:
        try:
            _com_call(data_object, 2, ctypes.c_ulong, [])  # Release
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Background Window Monitor Daemon
# ---------------------------------------------------------------------------

class QuickSwitcherMonitor(threading.Thread):
    """Background daemon thread polling foreground windows for #32770 dialogs."""

    def __init__(
        self,
        on_dialog_detected: Callable[[int, dict[str, Any]], None] | None = None,
        check_interval: float = 0.25,
        stop_event: threading.Event | None = None,
        data_dir: Path | str | None = None,
    ) -> None:
        super().__init__(daemon=True, name="QuickSwitcherMonitor-v3")
        self.on_dialog_detected = on_dialog_detected
        self.check_interval = check_interval
        self.stop_event = stop_event or threading.Event()
        self.data_dir = data_dir
        self.handled_dialogs: set[int] = set()

    def run(self) -> None:
        if sys.platform != "win32":
            return
        user32 = ctypes.windll.user32
        while not self.stop_event.is_set():
            try:
                # Clean up handles that are no longer valid windows
                self.handled_dialogs = {
                    h for h in self.handled_dialogs
                    if user32.IsWindow(h) and user32.IsWindowVisible(h)
                }
                fg = user32.GetForegroundWindow()
                if fg and fg not in self.handled_dialogs:
                    if is_file_dialog(fg):
                        recent = get_recent_file(data_dir=self.data_dir)
                        if recent:
                            self.handled_dialogs.add(fg)
                            if self.on_dialog_detected is not None:
                                self.on_dialog_detected(fg, recent)
            except Exception as exc:
                logging.debug("QuickSwitcherMonitor poll exception: %s", exc)

            self.stop_event.wait(self.check_interval)

    def stop(self) -> None:
        self.stop_event.set()
