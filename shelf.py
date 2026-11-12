"""Recent files shelf (#25): the last sorted files, ready to pick from any app.

Ctrl+Shift+Space (or Settings / the tray menu) opens a small list of the files Download Sorter
filed most recently. 3D files (.3mf, .stl, .step) are listed first while a slicer or CAD app is
open. Double-click (or Enter) opens the file's folder with it selected; "Copy path" puts the full
path on the clipboard, so it can be pasted into a file dialog in an app that does not use the
standard Windows Open/Save window.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

THREE_D_SUFFIXES = (".3mf", ".stl", ".step", ".stp", ".f3d", ".f3z", ".obj")
THREE_D_APPS = (
    "bambu-studio.exe", "bambu-studio-worker.exe", "orca-slicer.exe", "prusa-slicer.exe",
    "cura.exe", "fusion360.exe", "fusion.exe", "inventor.exe", "solidworks.exe",
)
SHELF_LIMIT = 15


def three_d_app_open() -> bool:
    import hold_manager
    return hold_manager.is_slicer_running(list(THREE_D_APPS))


def recent_files(limit: int = SHELF_LIMIT, three_d_first: bool | None = None) -> list[dict]:
    """The most recently filed files that still exist, newest first; 3D files lead when a
    slicer or CAD app is open (the order inside each group stays newest first)."""
    import config_manager

    records = [r for r in config_manager.get_recent_activity(limit=max(60, limit * 4)) if r.get("exists")]
    if three_d_first is None:
        three_d_first = three_d_app_open()
    if three_d_first:
        records.sort(key=lambda r: 0 if str(r.get("dest", "")).lower().endswith(THREE_D_SUFFIXES) else 1)
    return records[:limit]


def open_in_explorer(path: str | Path) -> None:
    """Show the file selected in its folder (a path with a comma is passed safely)."""
    target = os.path.abspath(str(path))
    if sys.platform == "win32":
        # Same form ribbon_popup uses: the path is one quoted argument, so spaces work.
        subprocess.Popen(f'explorer.exe /select,"{target}"')
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", target])


def copy_file(path: str | Path) -> bool:
    """Put the file itself on the clipboard, so Ctrl+V pastes it into Explorer or most apps."""
    import batch_handoff
    return bool(batch_handoff.copy_files_to_clipboard([str(path)]))


def drag_file(path: str | Path, hwnd: int | None = None) -> int:
    """Start a native file drag (the drop target gets the real file). Returns 0 when cancelled."""
    import batch_handoff
    return int(batch_handoff.start_file_drag([str(path)], hwnd=hwnd) or 0)


def main(argv: list[str] | None = None) -> int:
    import tkinter as tk
    from tkinter import ttk

    files = recent_files()
    root = tk.Tk()
    root.title("Recent files")
    root.geometry("560x420")
    root.attributes("-topmost", True)

    heading = ttk.Label(root, text="Recently filed: drag a file out, double-click to show it, or Ctrl+C to copy")
    heading.pack(anchor="w", padx=12, pady=(12, 4))
    box = tk.Listbox(root, activestyle="dotbox", font=("Segoe UI", 10), selectmode="browse")
    box.pack(fill="both", expand=True, padx=12)
    for rec in files:
        box.insert("end", f"{rec.get('filename', '')}    ({rec.get('folder', '')})")
    if not files:
        box.insert("end", "Nothing has been filed yet.")

    def chosen() -> dict | None:
        selection = box.curselection()
        if not selection or selection[0] >= len(files):
            return None
        return files[selection[0]]

    def show(_event=None) -> None:
        rec = chosen()
        if rec:
            try:
                open_in_explorer(rec["dest"])
            except OSError as exc:
                logging.warning("Could not show %s: %s", rec.get("dest"), exc)

    def copy_selected(_event=None) -> None:
        rec = chosen()
        if rec:
            copy_file(rec["dest"])

    drag = {"index": None, "started": False}

    def press(event) -> None:
        drag["index"] = box.nearest(event.y)
        drag["started"] = False

    def motion(_event) -> None:
        index = drag["index"]
        if drag["started"] or index is None or index >= len(files):
            return
        drag["started"] = True
        try:
            drag_file(files[index]["dest"], hwnd=root.winfo_id())
        except Exception:
            logging.exception("Could not start the file drag")

    def release(_event) -> None:
        drag["index"] = None
        drag["started"] = False

    buttons = ttk.Frame(root)
    buttons.pack(fill="x", padx=12, pady=10)
    ttk.Button(buttons, text="Show in Explorer", command=show).pack(side="left")
    ttk.Button(buttons, text="Copy file", command=copy_selected).pack(side="left", padx=8)
    ttk.Button(buttons, text="Close", command=root.destroy).pack(side="right")

    box.bind("<Double-Button-1>", show)
    box.bind("<Return>", show)
    box.bind("<Control-c>", copy_selected)
    box.bind("<ButtonPress-1>", press)
    box.bind("<B1-Motion>", motion)
    box.bind("<ButtonRelease-1>", release)
    root.bind("<Escape>", lambda _e: root.destroy())
    if files:
        box.selection_set(0)
        box.focus_set()
    root.mainloop()
    return 0
