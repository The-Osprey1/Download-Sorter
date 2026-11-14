"""CLI entry point for Download Sorter v3 (--status, --explain, --simulate, --sort-now, --undo, --home, --tray, --quit)."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from download_sorter import __version__
from download_sorter.config import load_config
from download_sorter.db import StateDB
from download_sorter.fs_ops import undo_batch
from download_sorter.ipc import DEFAULT_IPC_NAME, send_ipc_command
from download_sorter.paths import AppPaths
from download_sorter.router import FileCandidate, evaluate
from download_sorter.service import SorterService

# The running app answers these once the files have actually been moved.
_WORK_REPLY_TIMEOUT_MS = 120_000


def _print_explain(filename: str, url: str, paths: AppPaths) -> int:
    cfg = load_config(paths)
    dec = evaluate(FileCandidate(filename=filename, source_url=url), cfg)
    print(f"File:     {filename}")
    if url:
        print(f"URL:      {url}")
    print(f"Action:   {dec.action.upper()}")
    print(f"Dest:     {dec.relative_dest.parent if dec.relative_dest else '(stays in Downloads)'}")
    print(f"Why:      {dec.reason}")
    print("Trace:")
    for step in dec.trace:
        tag = "MATCH" if step.matched else "SKIP "
        print(f"  [{tag}] {step.stage} | {step.label}: {step.detail}")
    return 0


def _print_simulate(paths: AppPaths) -> int:
    svc = SorterService(paths)
    results = svc.simulate_top_level()
    print(f"Read-only top-level simulation on: {svc.watch_root}")
    print(f"Top-level files inspected: {len(results)} (subfolders untouched)")
    for path, dec in results:
        dest = str(dec.relative_dest.parent) if dec.relative_dest else "(stay)"
        print(f"  - {path.name:<48} -> {dec.action:<6} {dest:<24} | {dec.reason}")
    return 0


def _print_status(paths: AppPaths, ipc_name: str = DEFAULT_IPC_NAME) -> int:
    cfg = load_config(paths)
    db = StateDB(paths.db_file)
    stats = db.stats()
    pong = send_ipc_command("ping", server_name=ipc_name, timeout_ms=400)
    print(f"Download Sorter v{__version__}")
    print(f"Daemon:        {'RUNNING' if pong else 'STOPPED'}")
    print(f"Mode:          {'PAUSED' if cfg.paused else 'ACTIVE'}")
    print(f"Watch Root:    {cfg.resolved_watch_folder(paths)} (top-level only)")
    print(f"Rules:         {len(cfg.rules)} ordered rule(s)")
    print(f"Active Moves:  {stats['active_moves']} (undone: {stats['undone_moves']})")
    print(f"Data Dir:      {paths.data_dir}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="download-sorter-v3", description="Download Sorter v3.0 CLI")
    parser.add_argument("--version", "-v", action="store_true", help="Print version and exit")
    parser.add_argument("--status", action="store_true", help="Show configuration and DB status")
    parser.add_argument("--explain", metavar="FILENAME", help="Trace why a filename routes to its destination")
    parser.add_argument("--url", default="", help="Optional source URL/domain for --explain")
    parser.add_argument("--simulate", action="store_true", help="Read-only preview of top-level watched folder")
    parser.add_argument("--sort-now", "--sort", "--once", "-s", action="store_true", help="Sort top-level watched files once")
    parser.add_argument("--file-held", action="store_true", help="File held .3MF files into their destinations now")
    parser.add_argument("--undo", "-u", action="store_true", help="Undo the latest active batch")
    parser.add_argument("--home", action="store_true", help="Open single-process UI window + tray")
    parser.add_argument("--tray", "-t", action="store_true", help="Start single-process tray in background")
    parser.add_argument("--dialog", type=int, help="Target dialog HWND to dock Quick Switcher against")
    parser.add_argument("--quit", action="store_true", help="Stop running Download Sorter v3 process")
    parser.add_argument("--data-dir", type=Path, help="Override state directory")
    parser.add_argument("--watch-dir", type=Path, help="Override watched Downloads directory")
    parser.add_argument("folder", nargs="?", type=Path, help="Optional folder to sort once via Explorer context menu")
    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(errors="replace")
            except Exception:
                pass
    args = build_parser().parse_args(argv)
    if args.version:
        print(f"Download Sorter {__version__}")
        return 0

    default = AppPaths.default()
    paths = AppPaths(
        data_dir=args.data_dir or default.data_dir,
        downloads_dir=args.watch_dir,
    )

    if args.explain:
        return _print_explain(args.explain, args.url, paths)
    if args.simulate:
        return _print_simulate(paths)
    if args.status:
        return _print_status(paths)
    if args.dialog:
        from download_sorter.quick_switcher import get_recent_file
        from download_sorter.ui.quick_switcher_pill import QuickSwitcherPill

        recent = get_recent_file(data_dir=paths.data_dir)
        if not recent:
            return 1
        pill = QuickSwitcherPill(args.dialog, recent)
        pill.run()
        return 0
    if args.quit:
        resp = send_ipc_command("quit")
        print("Stopped." if resp else "Not running.")
        return 0
    if args.file_held:
        resp = send_ipc_command("file_held", reply_timeout_ms=_WORK_REPLY_TIMEOUT_MS)
        if resp is not None:
            print(f"Filed {resp.get('moved', 0)} held .3MF file(s).")
            return 0
        res = SorterService(paths).file_held_3mf_now()
        print(f"Filed {len(res['moved'])} held .3MF file(s).")
        return 0
    if args.sort_now:
        resp = send_ipc_command("sort_now", reply_timeout_ms=_WORK_REPLY_TIMEOUT_MS)
        if resp is not None:
            print(f"Moved {resp.get('moved', 0)} file(s).")
            return 0
        res = SorterService(paths).scan_top_level_now()
        print(f"Moved {len(res['moved'])} file(s).")
        return 0
    if args.undo:
        resp = send_ipc_command("undo", reply_timeout_ms=_WORK_REPLY_TIMEOUT_MS)
        if resp is not None:
            print(f"Restored {resp.get('restored', 0)} file(s).")
            return 0
        res = undo_batch(StateDB(paths.db_file))
        print(f"Restored {res['restored']} file(s).")
        return 0
    if args.folder is not None:
        target = args.folder.resolve()
        if not target.is_dir():
            print(f"Not a directory: {args.folder}")
            return 1
        folder_paths = AppPaths(data_dir=paths.data_dir, downloads_dir=target)
        svc = SorterService(folder_paths)
        svc.config.paused = False
        res = svc.scan_top_level_now(settle_seconds=0.0)
        print(f"Sorted {target}: moved {len(res['moved'])} file(s).")
        return 0

    from download_sorter.ui.tray_app import run_app

    return run_app(paths, show_window=(not args.tray or bool(args.home)))


if __name__ == "__main__":
    raise SystemExit(main())
