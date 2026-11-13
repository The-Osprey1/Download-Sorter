"""Unified entry point and CLI dispatcher for Download Sorter.

Enables both single-binary standalone execution (PyInstaller) and modular
command-line routing for background daemon, GUI, batch sorter, and utilities.
"""

from __future__ import annotations

import sys
from pathlib import Path

import about

__version__ = about.__version__

HELP_TEXT = """\
Download Sorter - Local-first automated Downloads and Documents organizer.

Usage:
  DownloadSorter [COMMAND] [OPTIONS]
  DownloadSorter-cli [COMMAND] [OPTIONS]

Commands:
  (default / no args)   Launch system tray monitor with embedded background watcher
  --tray, -t            Launch system tray monitor explicitly
  --home                Open Home & Recent Activity dashboard
  --welcome             Launch first-run Welcome & Setup Wizard
  --gui, -g, --rules    Launch PySide6 Visual Rules Manager & Live Sandbox GUI
  --sort, -s, --once    Sort Downloads once (--dry-run, --target <dir>, --notify)
  --undo, -u            Reversibly undo the most recent sort batch (--notify, --file <path>)
  --peek, -p <file>     Inspect 3D print file / CNC toolpath and extract embedded thumbnail
  --unzip-3d <zip>      Unpack 3D models from archive into Downloads
  --unzip <zip>         Safely unpack archive into Extracted/ with wrapper stripping
  --status              Print daemon health, memory usage, and recent moves
  --file-held           File every held .3mf now, skipping the hold (--include-kept: "Keep here" files too)
  --clean               Clean old installers/archives to Windows Recycle Bin
  --consolidate, --flatten Consolidate nested subfolders into official flat categories
  --remodel             Scan or safely remodel messy PC folder architectures
  --dialog <hwnd>       Spawn floating Quick Switcher pill for active File Dialog
  --quit                Ask a running Download Sorter (tray and windows) to close
  --version, -v         Display Download Sorter version
  --help, -h            Show this help message
"""


def init_stdio() -> None:
    """Ensure standard input, output, and error streams are properly connected.

    When running as a PyInstaller-frozen executable with console=False, Python sets
    sys.stdout and sys.stderr to None. This function:
    1. Reconnects to OS pipe/file redirection handles (e.g. CI, tests, PowerShell pipes, batch redirection).
    2. Attaches to the parent terminal console if invoked interactively from CMD or PowerShell.
    3. Falls back to a safe stream pointing to os.devnull so print() and sys.stdout.write() never throw.
    """
    if sys.platform != "win32":
        return

    # If both stdout and stderr are already connected (e.g. standard python interpreter or pytest capsys),
    # do not replace them.
    if sys.stdout is not None and sys.stderr is not None and hasattr(sys.stdout, "write") and hasattr(sys.stderr, "write"):
        return

    import ctypes
    import os
    from ctypes import wintypes

    k32 = ctypes.windll.kernel32
    k32.GetStdHandle.argtypes = [wintypes.DWORD]
    k32.GetStdHandle.restype = wintypes.HANDLE
    k32.AttachConsole.argtypes = [wintypes.DWORD]
    k32.AttachConsole.restype = wintypes.BOOL

    STD_OUTPUT_HANDLE = 0xFFFFFFF5  # -11 (DWORD)
    STD_ERROR_HANDLE = 0xFFFFFFF4   # -12 (DWORD)
    ATTACH_PARENT_PROCESS = 0xFFFFFFFF

    # 1. Check if OS standard handles already exist (piped, redirected to file, or captured by subprocess)
    if sys.stdout is None or not hasattr(sys.stdout, "write"):
        h_out = k32.GetStdHandle(STD_OUTPUT_HANDLE)
        if h_out and h_out != wintypes.HANDLE(-1).value and h_out != 0:
            try:
                import msvcrt
                fd_out = msvcrt.open_osfhandle(h_out, os.O_TEXT)
                sys.stdout = open(fd_out, "w", encoding="utf-8", errors="replace")
            except Exception:
                pass

    if sys.stderr is None or not hasattr(sys.stderr, "write"):
        h_err = k32.GetStdHandle(STD_ERROR_HANDLE)
        if h_err and h_err != wintypes.HANDLE(-1).value and h_err != 0:
            try:
                import msvcrt
                fd_err = msvcrt.open_osfhandle(h_err, os.O_TEXT)
                sys.stderr = open(fd_err, "w", encoding="utf-8", errors="replace")
            except Exception:
                pass

    # 2. If sys.stdout is still None or not connected, try attaching to parent console
    if sys.stdout is None:
        if k32.AttachConsole(ATTACH_PARENT_PROCESS):
            try:
                sys.stdout = open("CONOUT$", "w", encoding="utf-8", errors="replace")
                sys.stderr = open("CONOUT$", "w", encoding="utf-8", errors="replace")
            except Exception:
                pass

    # 3. Fallback dummy streams so print() never fails if running fully headless/windowed
    if sys.stdout is None:
        try:
            sys.stdout = open(os.devnull, "w", encoding="utf-8")
        except Exception:
            pass
    if sys.stderr is None:
        try:
            sys.stderr = open(os.devnull, "w", encoding="utf-8")
        except Exception:
            pass


# Commands that open a window or the tray. An unexpected error in these is
# shown to the person (installed app only); in the others it is only saved.
_WINDOW_COMMANDS = {"", "--tray", "-t", "--home", "--welcome", "--gui", "-g", "--rules", "--shelf"}


def _install_crash_handler(cmd: str) -> None:
    try:
        import problem_report
        frozen = bool(getattr(sys, "frozen", False))
        problem_report.install_crash_handler(
            where=f"command {cmd}" if cmd else "tray",
            show_dialog=frozen and cmd in _WINDOW_COMMANDS,
        )
    except Exception:
        pass  # reporting problems must never be one


def main(argv: list[str] | None = None) -> int:
    init_stdio()

    args = list(argv) if argv is not None else list(sys.argv[1:])
    if argv is None:
        _install_crash_handler(args[0].lower() if args else "")

    if not args:
        import tray
        return tray.main()

    cmd = args[0].lower()

    if cmd in ("--help", "-h", "/?"):
        print(HELP_TEXT)
        if sys.stdout and not sys.stdout.closed:
            try:
                sys.stdout.flush()
            except Exception:
                pass
        return 0

    if cmd == "--quit":
        # Used by the installer before it replaces the app's files.
        import quit_signal
        quit_signal.request_quit()
        return 0

    if cmd in ("--version", "-v"):
        print(about.version_string())
        if sys.stdout and not sys.stdout.closed:
            try:
                sys.stdout.flush()
            except Exception:
                pass
        return 0

    if cmd in ("--check-update", "--check-updates"):
        import update_check
        print(f"Checking for updates (current version: {about.__version__})...")
        rel = update_check.check(None, force=True)
        if rel:
            v = update_check.display_version(rel)
            print(f"Download Sorter {v} is available!")
            print(f"Release page: {rel.get('url')}")
            if rel.get("installer_url"):
                print(f"Installer: {rel.get('installer_url')}")
            return 0
        print(f"Download Sorter is up to date (version {about.__version__}).")
        return 0

    orig_argv = sys.argv
    try:
        # For commands where the command name is itself an option argument (like --dialog),
        # keep the option name for the target parser. Otherwise strip the sub-command prefix.
        if cmd in ("--dialog", "--switcher-dialog"):
            sys.argv = [sys.argv[0], "--dialog"] + [a for a in args[1:] if a != "--dialog"]
            import quick_switcher
            return quick_switcher.main()

        sys.argv = [sys.argv[0]] + args[1:]

        if cmd in ("--tray", "-t"):
            import tray
            return tray.main()

        # These commands' main() functions all take their arguments. A TypeError
        # that comes out of one is a bug inside that command, so it is not caught
        # here: calling main() again would run the whole command a second time
        # (for --undo, undoing another batch) and hide the first error.
        if cmd in ("--home",):
            import rules_manager
            return rules_manager.main(args)

        if cmd in ("--welcome",):
            import rules_manager
            return rules_manager.main(args)

        if cmd in ("--shelf",):
            import shelf
            return shelf.main(args)

        if cmd in ("--find",):
            import file_index
            return file_index.main(args[1:])

        if cmd in ("--gui", "-g", "--rules"):
            import rules_manager
            return rules_manager.main()

        if cmd in ("--sort", "-s", "--once"):
            import sort_once
            return sort_once.main(args[1:])

        if cmd in ("--undo", "-u"):
            import sort_undo
            return sort_undo.main(args[1:])

        if cmd in ("--ribbon-summary", "--ribbon"):
            import ribbon_popup
            forward_args = ["--summary"] + [a for a in args[1:] if a != "--summary"] if cmd == "--ribbon-summary" else args[1:]
            return ribbon_popup.main(forward_args)

        if cmd in ("--peek", "-p"):
            import gcode_peek
            return gcode_peek.main()

        if cmd in ("--unzip-3d",):
            import unzip_3d
            return unzip_3d.main()

        if cmd in ("--unzip",):
            import unzip_archive
            return unzip_archive.main()

        if cmd in ("--status",):
            import status
            return status.main()

        if cmd in ("--file-held",):
            import hold_manager
            return hold_manager.main(args)

        if cmd in ("--clean",):
            import clean_old
            return clean_old.main()

        if cmd in ("--consolidate", "--flatten"):
            import consolidate
            return consolidate.main(args[1:])

        if cmd in ("--remodel",):
            import watch
            if not watch.is_instance_running():
                try:
                    import safe_ops
                    safe_ops.recover_pending_journal(min_age_seconds=10.0)
                except Exception:
                    pass
            import remodel
            return remodel.main()

        if cmd in ("--watch", "-w"):
            import watch
            return watch.main()

        print(f"Unknown command: {args[0]}")
        print("Run with --help to see available commands.")
        return 2
    finally:
        if sys.stdout and not sys.stdout.closed:
            try:
                sys.stdout.flush()
            except Exception:
                pass
        if sys.stderr and not sys.stderr.closed:
            try:
                sys.stderr.flush()
            except Exception:
                pass
        sys.argv = orig_argv


if __name__ == "__main__":
    sys.exit(main())
