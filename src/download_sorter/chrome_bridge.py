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
from download_sorter.config import Rule, load_config, save_config
from download_sorter.db import STATUS_COMPLETED, StateDB
from download_sorter.dest import _clean_parts, is_under_watch_root, to_relative_dest
from download_sorter.fs_ops import execute_move
from download_sorter.ipc import send_ipc_command
from download_sorter.paths import AppPaths
from download_sorter.router import FileCandidate, RouteDecision, evaluate, extract_host

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
_GENERIC_EXTS = {"", ".bin", ".download", ".jfif", ".tmp", ".dat"}
_MIME_TO_EXT: dict[str, str] = {
    "application/pdf": ".pdf",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/svg+xml": ".svg",
    "application/zip": ".zip",
    "application/x-zip-compressed": ".zip",
    "application/x-7z-compressed": ".7z",
    "application/json": ".json",
    "text/csv": ".csv",
    "text/plain": ".txt",
    "model/stl": ".stl",
    "model/3mf": ".3mf",
    "application/vnd.ms-package.3dmanufacturing-3dmodel+xml": ".3mf",
    "video/mp4": ".mp4",
    "audio/mpeg": ".mp3",
}


def _repair_filename_with_mime(filename: str, mime: str) -> str:
    """Repair missing or generic extensions (e.g. .jfif, .bin, no ext) from Chrome MIME type."""
    clean_mime = (mime or "").split(";")[0].strip().lower()
    inferred = _MIME_TO_EXT.get(clean_mime)
    if not inferred:
        return filename
    p = Path(filename)
    if p.suffix.lower() in _GENERIC_EXTS:
        return f"{p.stem}{inferred}" if p.suffix else f"{filename}{inferred}"
    return filename


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


def _manifest_target_exists(manifest_file: Path) -> bool:
    if not manifest_file.is_file():
        return False
    try:
        payload = json.loads(manifest_file.read_text(encoding="utf-8"))
        target_raw = str(payload.get("path") or "").strip() if isinstance(payload, dict) else ""
        return bool(target_raw) and Path(target_raw).is_file()
    except (OSError, json.JSONDecodeError):
        return False


def is_chrome_host_registered(data_dir: Path | None = None) -> bool:
    """Return True when the HKCU Chrome Native Messaging key points to an existing manifest and target."""
    if sys.platform != "win32":
        return _manifest_target_exists(chrome_manifest_path(data_dir))
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REGISTRY_HOST_KEYS[0], 0, winreg.KEY_READ) as key:
            val, _ = winreg.QueryValueEx(key, "")
            return bool(val) and _manifest_target_exists(Path(str(val)))
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

    repaired = _repair_filename_with_mime(filename, str(req.get("mime") or ""))
    filename_changed = repaired != filename
    filename = repaired

    source_url = str(req.get("finalUrl") or req.get("url") or req.get("referrer") or "").strip()
    tab_url = str(req.get("tabUrl") or req.get("tab_url") or "").strip()
    tab_title = str(req.get("tabTitle") or req.get("tab_title") or "").strip()
    source_host = extract_host(source_url) or extract_host(tab_url)
    decision = evaluate(
        FileCandidate(filename=filename, source_url=source_url, tab_url=tab_url, tab_title=tab_title),
        cfg,
    )
    if decision.action != "move" or decision.relative_dest is None:
        return {
            "ok": True,
            "decision": "default",
            "matched_rule": decision.matched_rule,
            "match_type": decision.match_type,
            "reason": decision.reason,
            "source_host": source_host,
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
        "source_host": source_host,
        "repaired_filename": filename if filename_changed else "",
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


def toggle_pause_from_chrome(
    req: dict[str, Any],
    paths: AppPaths | None = None,
    *,
    try_ipc: bool = True,
) -> dict[str, Any]:
    """Toggle (or explicitly set) Auto-Sort pause state and sync with the running desktop app."""
    app_paths = paths or AppPaths.default()
    cfg = load_config(app_paths)
    new_paused = bool(req["paused"]) if "paused" in req else not bool(cfg.paused)
    cfg.paused = new_paused
    save_config(cfg, app_paths)

    if try_ipc:
        send_ipc_command("set_paused", payload={"paused": new_paused}, timeout_ms=350, reply_timeout_ms=900)

    return {"ok": True, "paused": new_paused, "version": __version__}


def list_chrome_folders(paths: AppPaths | None = None) -> dict[str, Any]:
    """Return sorted relative destination folders for the Chrome extension context menu/popup."""
    app_paths = paths or AppPaths.default()
    cfg = load_config(app_paths)
    watch_dir = cfg.resolved_watch_folder(app_paths)
    seen: dict[str, str] = {}

    def _add(raw_folder: str) -> None:
        cleaned = to_relative_dest(raw_folder, fallback="")
        if not cleaned or "{" in cleaned:
            return
        posix = cleaned.replace("\\", "/")
        key = posix.lower()
        if key not in seen:
            seen[key] = posix

    for rule in cfg.rules:
        if rule.enabled and rule.destination:
            _add(rule.destination)
    for cat in cfg.category_map.values():
        if cat:
            _add(cat)
    if watch_dir.is_dir():
        try:
            for entry in sorted(watch_dir.iterdir(), key=lambda p: p.name.lower()):
                if entry.is_dir() and not entry.name.startswith((".", "_")):
                    _add(entry.name)
        except OSError:
            pass

    folders = sorted(seen.values(), key=lambda s: (s.count("/"), s.lower()))
    return {"ok": True, "folders": folders[:20], "version": __version__}


def create_chrome_rule(
    req: dict[str, Any],
    paths: AppPaths | None = None,
    *,
    try_ipc: bool = True,
) -> dict[str, Any]:
    """Create (or reuse) a domain routing rule from the Chrome extension."""
    app_paths = paths or AppPaths.default()
    raw_domain = str(req.get("domain") or req.get("url") or "").strip()
    domain = extract_host(raw_domain)
    if not domain:
        return {"ok": False, "error": "Invalid or missing domain"}

    raw_dest = str(req.get("destination") or "").strip()
    destination = to_relative_dest(raw_dest, fallback="")
    if not destination:
        return {"ok": False, "error": "Invalid or missing destination folder"}

    extensions: list[str] = []
    for raw_ext in req.get("extensions") or []:
        ext = str(raw_ext or "").strip().lower()
        if ext and not ext.startswith("."):
            ext = "." + ext
        if ext and ext not in extensions:
            extensions.append(ext)

    cfg = load_config(app_paths)
    for existing in cfg.rules:
        if (
            existing.enabled
            and domain in [extract_host(d) for d in existing.domains]
            and to_relative_dest(existing.destination, fallback="").lower() == destination.lower()
            and existing.extensions == extensions
        ):
            return {
                "ok": True,
                "created": False,
                "rule": {
                    "id": existing.id,
                    "name": existing.name,
                    "destination": existing.destination,
                    "domains": existing.domains,
                },
                "rules_count": sum(1 for r in cfg.rules if r.enabled),
            }

    rule_name = str(req.get("name") or "").strip() or f"{domain} → {destination.replace(chr(92), '/')}"
    new_rule = Rule(
        id=f"chrome-{uuid.uuid4().hex[:8]}",
        name=rule_name,
        destination=destination,
        domains=[domain],
        extensions=extensions,
        enabled=True,
    )
    cfg.rules.insert(0, new_rule)
    save_config(cfg, app_paths)

    if try_ipc:
        send_ipc_command("reload_config", timeout_ms=350, reply_timeout_ms=900)

    return {
        "ok": True,
        "created": True,
        "rule": {
            "id": new_rule.id,
            "name": new_rule.name,
            "destination": new_rule.destination,
            "domains": new_rule.domains,
        },
        "rules_count": sum(1 for r in cfg.rules if r.enabled),
    }


def undo_last_from_chrome(
    paths: AppPaths | None = None,
    *,
    try_ipc: bool = True,
) -> dict[str, Any]:
    """Undo the most recent sort/Chrome-routed batch via IPC or offline SorterService."""
    app_paths = paths or AppPaths.default()
    if try_ipc:
        resp = send_ipc_command("undo", timeout_ms=450, reply_timeout_ms=4000)
        if resp is not None:
            return resp
    from download_sorter.service import SorterService

    svc = SorterService(app_paths)
    try:
        res = svc.undo_last_batch()
        return {"ok": True, **res, "via": "direct"}
    finally:
        svc.stop(timeout=0.5)


def check_chrome_duplicate(req: dict[str, Any], paths: AppPaths | None = None) -> dict[str, Any]:
    """Check whether a download URL+size or filename already exists in StateDB history."""
    app_paths = paths or AppPaths.default()
    filename = _clean_basename(str(req.get("filename") or ""))
    source_url = str(req.get("finalUrl") or req.get("url") or "").strip()
    file_size = int(req.get("fileSize") or 0)

    db = StateDB(app_paths.db_file)
    try:
        moves = db.list_active_moves(limit=250)
    finally:
        db.close()

    for m in moves:
        dest = Path(str(m.get("dest_path") or ""))
        if not dest.is_file():
            continue
        same_url = bool(source_url and m.get("source_url") == source_url)
        same_size = file_size <= 0 or int(m.get("size_bytes") or 0) == file_size
        if same_url and same_size:
            return {
                "ok": True,
                "duplicate": True,
                "existing_path": str(dest),
                "reason": f"Already downloaded to {dest.parent.name}",
            }
        if filename and str(m.get("filename") or "").lower() == filename.lower() and file_size > 0 and int(m.get("size_bytes") or 0) == file_size:
            return {
                "ok": True,
                "duplicate": True,
                "existing_path": str(dest),
                "reason": f"Identical file already in {dest.parent.name}",
            }
    return {"ok": True, "duplicate": False, "existing_path": "", "reason": ""}


def reroute_chrome_file(
    req: dict[str, Any],
    paths: AppPaths | None = None,
    *,
    try_ipc: bool = True,
) -> dict[str, Any]:
    """Move an already-downloaded file inside Downloads to a different subfolder."""
    app_paths = paths or AppPaths.default()
    cfg = load_config(app_paths)
    watch_dir = cfg.resolved_watch_folder(app_paths)

    raw_src = str(req.get("full_path") or "").strip()
    if not raw_src and req.get("relative_dest"):
        src_path = (watch_dir / Path(str(req["relative_dest"]))).resolve()
    elif raw_src:
        src_path = Path(raw_src).resolve()
    else:
        return {"ok": False, "error": "Missing source path"}

    if not is_under_watch_root(src_path, watch_dir) or not src_path.is_file():
        return {"ok": False, "error": "Source file not found inside Downloads"}

    new_folder = to_relative_dest(str(req.get("new_folder") or ""), fallback="")
    if not new_folder:
        return {"ok": False, "error": "Invalid destination folder"}

    rel_dest = Path(new_folder) / src_path.name
    decision = RouteDecision(
        action="move",
        relative_dest=rel_dest,
        reason=f"Re-routed from Chrome popup to '{new_folder}'",
        matched_rule="Chrome Re-route",
        match_type="chrome_reroute",
    )
    db = StateDB(app_paths.db_file)
    batch_id = f"reroute-{uuid.uuid4().hex[:8]}"
    try:
        rec = execute_move(
            src_path,
            decision,
            batch_id=batch_id,
            db=db,
            watch_root=watch_dir,
            settle_seconds=0.0,
            source_url=str(req.get("source_url") or ""),
        )
        db.checkpoint()
    finally:
        db.close()

    if rec is None:
        return {"ok": False, "error": "File is locked or already at destination"}

    try:
        from download_sorter.learning import record_manual_route

        record_manual_route(
            destination=new_folder,
            data_dir=app_paths.data_dir,
            source_url=str(req.get("source_url") or ""),
            ext=src_path.suffix,
            filename=src_path.name,
        )
    except Exception:
        pass

    if try_ipc:
        send_ipc_command("reload_config", timeout_ms=250, reply_timeout_ms=600)

    folder_display = new_folder.replace("\\", "/")
    return {
        "ok": True,
        "batch_id": batch_id,
        "dest_path": str(rec.get("dest_path") or ""),
        "folder_display": folder_display,
        "relative_dest": f"{folder_display}/{src_path.name}",
    }


def sort_now_from_chrome(
    paths: AppPaths | None = None,
    *,
    try_ipc: bool = True,
) -> dict[str, Any]:
    """Sort top-level Downloads now via IPC or offline SorterService."""
    app_paths = paths or AppPaths.default()
    if try_ipc:
        resp = send_ipc_command("sort_now", timeout_ms=400, reply_timeout_ms=10000)
        if resp is not None:
            return {
                "ok": bool(resp.get("ok", True)),
                "moved_count": int(resp.get("moved", 0) or 0),
                "via": "ipc",
            }
    from download_sorter.service import SorterService

    svc = SorterService(app_paths)
    try:
        res = svc.scan_top_level_now(settle_seconds=0.0)
        return {"ok": True, "moved_count": len(res.get("moved", [])), "via": "direct"}
    finally:
        svc.stop(timeout=0.5)


def reveal_chrome_file(
    req: dict[str, Any],
    paths: AppPaths | None = None,
    *,
    launch: bool = True,
) -> dict[str, Any]:
    """Open File Explorer highlighting a routed file or subfolder inside Downloads."""
    app_paths = paths or AppPaths.default()
    cfg = load_config(app_paths)
    watch_dir = cfg.resolved_watch_folder(app_paths).resolve()

    candidates: list[Path] = []
    raw_full = str(req.get("full_path") or "").strip()
    if raw_full:
        try:
            candidates.append(Path(raw_full).resolve())
        except OSError:
            pass
    for raw_rel in (str(req.get("relative_dest") or "").strip(), str(req.get("folder") or "").strip()):
        if not raw_rel:
            continue
        if raw_rel.lower() == "downloads":
            candidates.append(watch_dir)
        else:
            clean_rel = to_relative_dest(raw_rel, fallback="")
            if clean_rel:
                candidates.append((watch_dir / clean_rel).resolve())

    target: Path | None = None
    for cand in candidates:
        if (cand == watch_dir or is_under_watch_root(cand, watch_dir)) and cand.exists():
            target = cand
            break

    if target is None:
        return {"ok": False, "error": "File or folder not found inside Downloads"}

    if launch and sys.platform == "win32":
        if target.is_file():
            subprocess.Popen(["explorer.exe", f'/select,"{target}"'])
        elif hasattr(os, "startfile"):
            os.startfile(str(target))
    return {"ok": True, "revealed": str(target), "is_dir": target.is_dir()}


def list_recent_chrome_moves(
    paths: AppPaths | None = None,
    *,
    limit: int = 6,
) -> dict[str, Any]:
    """Return recent active moves from StateDB so the Chrome popup reflects desktop sorts too."""
    app_paths = paths or AppPaths.default()
    cfg = load_config(app_paths)
    watch_dir = cfg.resolved_watch_folder(app_paths).resolve()
    db = StateDB(app_paths.db_file)
    try:
        rows = db.list_active_moves(limit=max(1, min(25, int(limit))))
    finally:
        db.close()

    moves: list[dict[str, Any]] = []
    for row in rows:
        dest_raw = str(row.get("dest_path") or "")
        dest_path = Path(dest_raw) if dest_raw else watch_dir
        try:
            rel_posix = dest_path.resolve().relative_to(watch_dir).as_posix()
        except ValueError:
            rel_posix = str(row.get("filename") or dest_path.name)
        folder_display = rel_posix.rsplit("/", 1)[0] if "/" in rel_posix else "Downloads"
        source_url = str(row.get("source_url") or "")
        moves.append(
            {
                "filename": str(row.get("filename") or dest_path.name),
                "folder": folder_display,
                "relative_dest": rel_posix,
                "full_path": dest_raw,
                "source_url": source_url,
                "source_host": extract_host(source_url),
                "is_duplicate": bool(row.get("is_duplicate")),
                "matched_rule": str(row.get("matched_rule") or ""),
                "at": int(round(float(row.get("moved_at") or 0.0) * 1000.0)),
            }
        )
    return {"ok": True, "moves": moves}


def test_chrome_tab(req: dict[str, Any], paths: AppPaths | None = None) -> dict[str, Any]:
    """Dry-run evaluate what rule would match downloads from the current active tab."""
    app_paths = paths or AppPaths.default()
    cfg = load_config(app_paths)
    tab_url = str(req.get("tabUrl") or req.get("tab_url") or "").strip()
    tab_title = str(req.get("tabTitle") or req.get("tab_title") or "").strip()
    sample_name = _clean_basename(str(req.get("filename") or "sample.pdf")) or "sample.pdf"
    host = extract_host(tab_url)
    decision = evaluate(
        FileCandidate(filename=sample_name, source_url=tab_url, tab_url=tab_url, tab_title=tab_title),
        cfg,
    )
    folder = ""
    if decision.relative_dest is not None:
        rel_posix = _to_chrome_relative(decision.relative_dest)
        folder = rel_posix.rsplit("/", 1)[0] if "/" in rel_posix else "Downloads"
    smart_hint = ""
    if decision.match_type != "rule" and (cfg.ai_smart_hints_enabled or cfg.ai_auto_route_fallback):
        try:
            from download_sorter.learning import TokenRouteLearner

            db = StateDB(app_paths.db_file)
            try:
                learner = TokenRouteLearner()
                learner.train_from_db_and_store(db, app_paths.data_dir)
            finally:
                db.close()
            min_conf = max(0.50, min(0.95, float(cfg.ai_min_confidence) / 100.0))
            pred = learner.predict_folder(
                sample_name,
                source_url=tab_url,
                ext=Path(sample_name).suffix,
                min_confidence=min_conf,
            )
            if pred is not None:
                smart_hint = pred.destination.replace("\\", "/")
        except Exception:
            pass
    return {
        "ok": True,
        "host": host,
        "action": decision.action,
        "folder": folder or "Downloads",
        "matched_rule": decision.matched_rule,
        "match_type": decision.match_type,
        "reason": decision.reason,
        "has_domain_rule": decision.match_type == "rule",
        "smart_hint": smart_hint,
    }


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
    if action == "toggle_pause":
        return toggle_pause_from_chrome(req, app_paths)
    if action == "list_folders":
        return list_chrome_folders(app_paths)
    if action == "create_rule":
        return create_chrome_rule(req, app_paths)
    if action == "undo_last":
        return undo_last_from_chrome(app_paths)
    if action == "sort_now":
        return sort_now_from_chrome(app_paths)
    if action == "reveal_file":
        return reveal_chrome_file(req, app_paths)
    if action == "recent_moves":
        return list_recent_chrome_moves(app_paths, limit=int(req.get("limit") or 6))
    if action == "check_duplicate":
        return check_chrome_duplicate(req, app_paths)
    if action == "reroute_file":
        return reroute_chrome_file(req, app_paths)
    if action == "test_tab":
        return test_chrome_tab(req, app_paths)
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
