"""Unified entry point for Download Sorter v3.0.

Handles Windows GUI stdio re-attachment when frozen with ``console=False``
and delegates cleanly to ``download_sorter.cli.main``.
"""

from __future__ import annotations

from pathlib import Path
import sys

import about

__version__ = about.__version__


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
    # ensure strict console encodings (e.g. cp932/cp1252) never crash on Unicode filenames.
    if sys.stdout is not None and sys.stderr is not None and hasattr(sys.stdout, "write") and hasattr(sys.stderr, "write"):
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure"):
                try:
                    stream.reconfigure(errors="replace")
                except Exception:
                    pass
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


# Commands that open a window or the tray — keep stdio safe for frozen execution.
def main(argv: list[str] | None = None) -> int:
    init_stdio()
    if not getattr(sys, "frozen", False):
        src_dir = str(Path(__file__).resolve().parent / "src")
        if src_dir not in sys.path:
            sys.path.insert(0, src_dir)

    from download_sorter.cli import main as cli_main

    try:
        return int(cli_main(argv))
    finally:
        for stream in (sys.stdout, sys.stderr):
            if stream and not getattr(stream, "closed", True):
                try:
                    stream.flush()
                except Exception:
                    pass


if __name__ == "__main__":
    raise SystemExit(main())
