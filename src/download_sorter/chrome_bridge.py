"""Chrome / Chromium Native Messaging bridge for direct subfolder download routing."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import time
from typing import Any
import uuid

from download_sorter.about import __version__
from download_sorter.config import load_config
from download_sorter.db import STATUS_COMPLETED, StateDB
from download_sorter.dest import _clean_parts, is_under_watch_root
from download_sorter.ipc import send_ipc_command
from download_sorter.paths import AppPaths
from download_sorter.router import FileCandidate, evaluate

NATIVE_HOST_NAME = "com.devynfidel.download_sorter"
EXTENSION_ID = "agcbbbnkmhodmgdjlaanbgjoahmhckgf"
EXTENSION_PUBLIC_KEY = (
    "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAqQeM6aRO/Si/EW9YsCpcCFRwF0Vh"
    "wvzUWXcJQqeU+0W8+uuFwIXM/IgTbOt8s+JpYb4yOdNVjdkKS6hCNr+KZs0RG0mNPhvRn2OH"
    "tDFPLXPzhbVQovDMvRlfeoA69QFJGhf5+hO42jVH5wbKDwMxDmtaj7p2OryAYBrPY/fLdXEy"
    "0Ffs1b3gI1OjG4Mbw9QHIPdfXO1BLGM5zFp/ceasr7j+pW2sTYpH3YdP8ci+56bSS/qNTaGe"
    "Izng6lS78e1WBnJksKNEl94JHTNwZUpVRa6Od9HsmSQD1zoNne3Nb1dWJ9RujCl8MONoRgd+"
    "Dvhicxkx8ALfcPpXGTR5RMbMJQIDAQAB"
)

_REGISTRY_HOST_KEYS: tuple[str, ...] = (
    rf"Software\Google\Chrome\NativeMessagingHosts\{NATIVE_HOST_NAME}",
    rf"Software\Microsoft\Edge\NativeMessagingHosts\{NATIVE_HOST_NAME}",
    rf"Software\BraveSoftware\Brave-Browser\NativeMessagingHosts\{NATIVE_HOST_NAME}",
)
_EXT_ID_RE = re.compile(r"^[a-p]{32}$")
_MAX_MESSAGE_BYTES = 1024 * 1024


def find_extension_dir() -> Path | None:
    """Locate the bundled ``chrome_extension`` directory (source tree or frozen install)."""
    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        candidates.extend([exe_dir / "chrome_extension", exe_dir / "_internal" / "chrome_extension"])
    here = Path(__file__).resolve()
    candidates.extend(
        [
            here.parents[2] / "chrome_extension",
            here.parents[1] / "chrome_extension",
            AppPaths.default().data_dir / "chrome_extension",
        ]
    )
    for cand in candidates:
        if (cand / "manifest.json").is_file():
            return cand
    return None


def resolve_native_host_target(data_dir: Path) -> Path:
    """Return the executable (or CLI shim in dev mode) invoked by Chrome Native Messaging."""
    exe_dir = Path(sys.executable).resolve().parent
    frozen_cli = exe_dir / "DownloadSorter-cli.exe"
    if getattr(sys, "frozen", False) and frozen_cli.is_file():
        return frozen_cli

    # In development checkouts, look for a built dist CLI exe only if desired, otherwise
    # write a clean .cmd shim so Chrome can pass chrome-extension://<id>/ without arg errors.
    shim_path = data_dir / "chrome_native_host.cmd"
    shim_path.parent.mkdir(parents=True, exist_ok=True)
    src_root = Path(__file__).resolve().parents[1]
    python_exe = Path(sys.executable).resolve()
    shim_body = (
        "@echo off\r\n"
        f'set "PYTHONPATH={src_root};%PYTHONPATH%"\r\n'
        f'"{python_exe}" -m download_sorter --native-host %*\r\n'
    )
    try:
        if not shim_path.exists() or shim_path.read_text(encoding="utf-8") != shim_body:
            shim_path.write_text(shim_body, encoding="utf-8")
    except OSError:
        pass
    return shim_path


def chrome_manifest_path(data_dir: Path | None = None) -> Path:
    base = data_dir or AppPaths.default().data_dir
    return base / "chrome_native_host.json"


def install_chrome_native_host(
    data_dir: Path | None = None,
    *,
    extra_extension_ids: list[str] | None = None,
    target_path: Path | None = None,
) -> dict[str, Any]:
    """Write ``chrome_native_host.json`` and register it for Chrome, Edge, and Brave in HKCU."""
    resolved_data = data_dir or AppPaths.default().data_dir
    resolved_data.mkdir(parents=True, exist_ok=True)
    host_exe = Path(target_path) if target_path is not None else resolve_native_host_target(resolved_data)
    manifest_file = chrome_manifest_path(resolved_data)

    ext_ids = [EXTENSION_ID]
    for raw_id in extra_extension_ids or []:
        cleaned = str(raw_id or "").strip().lower()
        if _EXT_ID_RE.match(cleaned) and cleaned not in ext_ids:
            ext_ids.append(cleaned)

    manifest_payload = {
        "name": NATIVE_HOST_NAME,
        "description": "Download Sorter Native Messaging Host for direct Chrome download routing",
        "path": str(host_exe),
        "type": "stdio",
        "allowed_origins": [f"chrome-extension://{eid}/" for eid in ext_ids],
    }
    manifest_file.write_text(json.dumps(manifest_payload, indent=2), encoding="utf-8")

    registered_keys: list[str] = []
    if sys.platform == "win32":
        import winreg

        for sub_key in _REGISTRY_HOST_KEYS:
            try:
                with winreg.CreateKey(winreg.HKEY_CURRENT_USER, sub_key) as key:
                    winreg.SetValueEx(key, "", 0, winreg.REG_SZ, str(manifest_file))
                registered_keys.append(sub_key)
            except OSError as exc:
                logging.debug("Could not register %s: %s", sub_key, exc)

    return {
        "ok": True,
        "manifest_path": str(manifest_file),
        "host_target": str(host_exe),
        "extension_id": EXTENSION_ID,
        "registered_keys": registered_keys,
    }


def is_chrome_host_registered(data_dir: Path | None = None) -> bool:
    """Return True when the HKCU Chrome Native Messaging key points to an existing manifest."""
    if sys.platform != "win32":
        return chrome_manifest_path(data_dir).is_file()
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REGISTRY_HOST_KEYS[0], 0, winreg.KEY_READ) as key:
            val, _ = winreg.QueryValueEx(key, "")
            return bool(val) and Path(str(val)).is_file()
    except OSError:
        return False


def uninstall_chrome_native_host(data_dir: Path | None = None) -> bool:
    """Remove HKCU Chrome/Edge/Brave Native Messaging keys."""
    manifest_file = chrome_manifest_path(data_dir)
    if manifest_file.exists():
        try:
            manifest_file.unlink()
        except OSError:
            pass
    if sys.platform != "win32":
        return True
    import winreg

    removed_all = True
    for sub_key in _REGISTRY_HOST_KEYS:
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, sub_key)
        except FileNotFoundError:
            pass
        except OSError:
            removed_all = False
    return removed_all


def _clean_basename(raw_filename: str) -> str:
    raw = str(raw_filename or "").strip()
    if not raw:
        return ""
    # Handle either Windows or POSIX separators passed by Chrome
    name = Path(raw.replace("\\", "/")).name
    parts = _clean_parts([name]) if name else None
    return parts[0] if parts else ""


def _to_chrome_relative(rel_path: Path) -> str:
    cleaned = _clean_parts(list(rel_path.parts))
    return "/".join(cleaned) if cleaned else ""


def route_chrome_download(req: dict[str, Any], paths: AppPaths | None = None) -> dict[str, Any]:
    """Compute a Chrome-relative destination inside Downloads before Chrome writes to disk."""
    app_paths = paths or AppPaths.default()
    cfg = load_config(app_paths)
    watch_dir = cfg.resolved_watch_folder(app_paths)

    if cfg.paused:
        return {
            "ok": True,
            "decision": "default",
            "reason": "Auto-Sort is paused",
            "paused": True,
            "version": __version__,
        }

    filename = _clean_basename(str(req.get("filename") or ""))
    if not filename:
        return {"ok": True, "decision": "default", "reason": "No filename provided"}

    if cfg.hold_3mf_enabled and cfg.hold_3mf_days > 0 and Path(filename).suffix.lower() == ".3mf":
        return {
            "ok": True,
            "decision": "default",
            "matched_rule": "3MF Hold",
            "match_type": "hold",
            "reason": f"3MF grace hold ({cfg.hold_3mf_days}d window)",
            "version": __version__,
        }

    source_url = str(req.get("finalUrl") or req.get("url") or req.get("referrer") or "").strip()
    decision = evaluate(FileCandidate(filename=filename, source_url=source_url), cfg)
    if decision.action != "move" or decision.relative_dest is None:
        return {
            "ok": True,
            "decision": "default",
            "matched_rule": decision.matched_rule,
            "match_type": decision.match_type,
            "reason": decision.reason,
            "version": __version__,
        }

    rel_posix = _to_chrome_relative(decision.relative_dest)
    if not rel_posix or "/" not in rel_posix:
        return {"ok": True, "decision": "default", "reason": "Stays at top level"}

    folder_display = rel_posix.rsplit("/", 1)[0]
    return {
        "ok": True,
        "decision": "suggest",
        "relative_dest": rel_posix,
        "folder_display": folder_display,
        "matched_rule": decision.matched_rule,
        "match_type": decision.match_type,
        "reason": decision.reason,
        "conflict_action": "uniquify",
        "watch_root": str(watch_dir),
        "version": __version__,
    }


def record_chrome_completed(
    req: dict[str, Any],
    paths: AppPaths | None = None,
    *,
    try_ipc: bool = True,
) -> dict[str, Any]:
    """Record a Chrome-routed completed download into StateDB, Quick Switcher, and running UI."""
    app_paths = paths or AppPaths.default()
    cfg = load_config(app_paths)
    watch_dir = cfg.resolved_watch_folder(app_paths)

    raw_dest = str(req.get("full_path") or "").strip()
    filename = _clean_basename(str(req.get("filename") or raw_dest))
    if not raw_dest and req.get("relative_dest"):
        dest_path = (watch_dir / Path(str(req["relative_dest"]))).resolve()
    elif raw_dest:
        dest_path = Path(raw_dest).resolve()
    else:
        return {"ok": False, "error": "Missing completed download path"}

    if not is_under_watch_root(dest_path, watch_dir):
        return {"ok": False, "error": "Destination is outside watched Downloads folder"}

    if not filename:
        filename = dest_path.name
    src_path = (watch_dir / filename).resolve()

    payload = {
        "filename": dest_path.name,
        "src_path": str(src_path),
        "dest_path": str(dest_path),
        "matched_rule": str(req.get("matched_rule") or "Chrome Extension"),
        "match_type": str(req.get("match_type") or "chrome"),
        "reason": str(req.get("reason") or "Routed directly by Chrome Extension"),
        "source_url": str(req.get("source_url") or ""),
        "size_bytes": int(req.get("file_size") or (dest_path.stat().st_size if dest_path.is_file() else 0)),
    }

    if try_ipc:
        resp = send_ipc_command("chrome_routed", payload=payload, timeout_ms=450, reply_timeout_ms=1200)
        if resp is not None:
            return resp

    # Offline / standalone fallback when the daemon isn't open
    db = StateDB(app_paths.db_file)
    batch_id = f"chrome-{uuid.uuid4().hex[:8]}"
    move_id = db.record_move(
        batch_id=batch_id,
        filename=payload["filename"],
        src_path=payload["src_path"],
        dest_path=payload["dest_path"],
        reason=payload["reason"],
        matched_rule=payload["matched_rule"],
        match_type=payload["match_type"],
        source_url=payload["source_url"],
        size_bytes=payload["size_bytes"],
        is_duplicate=False,
        moved_at=time.time(),
        status=STATUS_COMPLETED,
    )
    db.checkpoint()

    if cfg.quick_switcher_enabled and dest_path.is_file():
        try:
            from download_sorter.quick_switcher import record_recent_file

            record_recent_file(
                str(dest_path),
                downloads_root=watch_dir,
                batch_id=batch_id,
                settings={
                    "timeout_seconds": cfg.quick_switcher_timeout_seconds,
                    "3d_timeout_hours": cfg.quick_switcher_3d_timeout_hours,
                },
                data_dir=app_paths.data_dir,
            )
        except Exception as exc:
            logging.debug("Quick Switcher record from Chrome extension failed: %s", exc)

    return {"ok": True, "move_id": move_id, "batch_id": batch_id, "via": "direct"}


def _open_desktop_app(paths: AppPaths) -> dict[str, Any]:
    if send_ipc_command("show_window", timeout_ms=350) is not None:
        return {"ok": True, "launched": "ipc"}
    exe_dir = Path(sys.executable).resolve().parent
    gui_exe = exe_dir / "DownloadSorter.exe"
    try:
        if getattr(sys, "frozen", False) and gui_exe.is_file():
            subprocess.Popen([str(gui_exe), "--home"], close_fds=True)
            return {"ok": True, "launched": str(gui_exe)}
        subprocess.Popen([str(Path(sys.executable).resolve()), "-m", "download_sorter", "--home"], close_fds=True)
        return {"ok": True, "launched": "python"}
    except OSError as exc:
        return {"ok": False, "error": str(exc)}


def handle_native_request(req: dict[str, Any], paths: AppPaths | None = None) -> dict[str, Any]:
    """Dispatch one Native Messaging JSON request cleanly."""
    app_paths = paths or AppPaths.default()
    action = str(req.get("action") or "status").strip().lower()

    if action == "status":
        cfg = load_config(app_paths)
        pong = send_ipc_command("ping", timeout_ms=250)
        return {
            "ok": True,
            "version": __version__,
            "paused": bool(cfg.paused),
            "hold_3mf_enabled": bool(cfg.hold_3mf_enabled),
            "hold_3mf_days": int(cfg.hold_3mf_days),
            "watch_root": str(cfg.resolved_watch_folder(app_paths)),
            "rules_count": sum(1 for r in cfg.rules if r.enabled),
            "daemon_running": bool(pong),
        }
    if action == "route":
        return route_chrome_download(req, app_paths)
    if action == "completed":
        return record_chrome_completed(req, app_paths)
    if action == "open_app":
        return _open_desktop_app(app_paths)
    return {"ok": False, "error": f"Unknown native host action '{action}'"}


def read_native_message(stream: Any) -> dict[str, Any] | None:
    """Read one length-prefixed JSON object from ``stream`` (returns None on EOF)."""
    raw_len = stream.read(4)
    if not raw_len or len(raw_len) < 4:
        return None
    msg_len = struct.unpack("<I", raw_len)[0]
    if msg_len == 0 or msg_len > _MAX_MESSAGE_BYTES:
        return None
    payload = stream.read(msg_len)
    if not payload or len(payload) < msg_len:
        return None
    parsed = json.loads(payload.decode("utf-8"))
    return parsed if isinstance(parsed, dict) else {}


def write_native_message(stream: Any, payload: dict[str, Any]) -> None:
    """Write one length-prefixed UTF-8 JSON object to ``stream`` and flush."""
    encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    stream.write(struct.pack("<I", len(encoded)))
    stream.write(encoded)
    stream.flush()


def _ensure_binary_stdio() -> None:
    if sys.platform != "win32":
        return
    import msvcrt

    for s in (sys.stdin, sys.stdout):
        try:
            if s is not None and hasattr(s, "fileno"):
                msvcrt.setmode(s.fileno(), os.O_BINARY)
        except Exception:
            pass


def run_native_host(
    paths: AppPaths | None = None,
    *,
    stdin: Any | None = None,
    stdout: Any | None = None,
) -> int:
    """Serve Chrome Native Messaging requests until stdin closes."""
    app_paths = paths or AppPaths.default()
    if stdin is None and stdout is None:
        _ensure_binary_stdio()
    in_stream = stdin if stdin is not None else sys.stdin.buffer
    out_stream = stdout if stdout is not None else sys.stdout.buffer

    while True:
        try:
            req = read_native_message(in_stream)
        except Exception as exc:
            logging.debug("Chrome native host read error: %s", exc)
            break
        if req is None:
            break
        try:
            resp = handle_native_request(req, app_paths)
        except Exception as exc:
            logging.exception("Chrome native host request error")
            resp = {"ok": False, "error": str(exc)}
        write_native_message(out_stream, resp)
    return 0
