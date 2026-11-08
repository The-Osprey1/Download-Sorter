"""Tray icon for the download sorter: pause, sort now, undo, open folders.

Run this instead of watch.py and the watcher lives inside it, so there is one
thing to start at login and one icon to click.

    pythonw tray.py
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import uuid
from pathlib import Path

from PIL import Image, ImageDraw
import pystray

import about
import watch
from sorter import (
    HISTORY_FILE,
    IncrementalHistoryReader,
    LOG_DIR,
    LOG_FILE,
    SERVICE_DISABLED_FILE,
    is_paused,
    load_config,
    set_paused,
    setup_logging,
    sort_existing,
    sort_existing_documents,
    watch_root,
    write_runtime_state,
)

ROOT = Path(__file__).resolve().parent
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
if not PYTHON.exists():
    PYTHON = Path(sys.executable)
    if PYTHON.name.lower() == "pythonw.exe":
        sibling = PYTHON.with_name("python.exe")
        if sibling.exists():
            PYTHON = sibling
_HISTORY_READER = IncrementalHistoryReader(HISTORY_FILE)

# Align with the shared design tokens (suite_theme.py): ACCENTS['downloadSorter'] = #69E6AD
ACCENT = (23, 27, 36)            # dark surface body
ACCENT_LIGHT = (105, 230, 173)   # suite mint tab & arrow
PAUSED_GREY = (101, 112, 132)    # textFaint


def make_icon(paused: bool = False, held_count: int = 0) -> Image.Image:
    """A folder with a down arrow, greyed out while paused, with held badge if held_count > 0."""
    body = PAUSED_GREY if paused else ACCENT
    tab = PAUSED_GREY if paused else ACCENT_LIGHT
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((6, 18, 58, 54), radius=6, fill=body, outline=tab, width=2)
    draw.rounded_rectangle((6, 11, 28, 22), radius=3, fill=tab)
    if paused:
        draw.rectangle((24, 28, 30, 46), fill=(255, 255, 255))
        draw.rectangle((34, 28, 40, 46), fill=(255, 255, 255))
    else:
        draw.polygon([(32, 46), (21, 33), (43, 33)], fill=tab)
        draw.rectangle((28, 24, 36, 34), fill=tab)
    if held_count > 0:
        # Amber/orange indicator badge dot on top-right (COLORS["warning"] #F5AD62)
        badge_fill = (245, 173, 98)
        badge_border = (11, 13, 18)
        draw.ellipse((42, 6, 58, 22), fill=badge_fill, outline=badge_border, width=2)
    return image


def moves_today() -> int:
    """How many files the sorter has filed since midnight."""
    return _HISTORY_READER.moves_today()


def open_in_explorer(path: Path) -> None:
    try:
        os.startfile(str(path))  # noqa: S606 - opening the user's own folder
    except OSError as exc:
        logging.warning("Could not open %s: %s", path, exc)


def run_script(name: str, *args: str) -> subprocess.CompletedProcess | None:
    """Run one of the sibling command-line tools without flashing a console."""
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        if getattr(sys, "frozen", False):
            flag_map = {
                "rules_manager.py": ["--gui"],
                "sort_undo.py": ["--undo"],
                "sort_once.py": ["--sort"],
                "status.py": ["--status"],
                "gcode_peek.py": ["--peek"],
                "unzip_3d.py": ["--unzip-3d"],
                "unzip_archive.py": ["--unzip"],
                "clean_old.py": ["--clean"],
            }
            mapped = flag_map.get(name, [name])
            return subprocess.run(
                [sys.executable, *mapped, *args],
                capture_output=True, text=True, creationflags=flags, timeout=300,
            )
        return subprocess.run(
            [str(PYTHON), str(ROOT / name), *args],
            capture_output=True, text=True, creationflags=flags, timeout=300,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logging.warning("Could not run %s: %s", name, exc)
        return None


def spawn_window(*args: str) -> None:
    """Spawn a GUI window asynchronously without blocking the tray process."""
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    cli_args = list(args)
    if cli_args and cli_args[0] == "rules_manager.py":
        cli_args = cli_args[1:]
        if not cli_args:
            cli_args = ["--gui"]
    if getattr(sys, "frozen", False):
        cmd = [sys.executable, *cli_args]
    else:
        python_bin = Path(sys.executable).resolve()
        if python_bin.name.lower() == "pythonw.exe":
            pass
        elif python_bin.name.lower() == "python.exe":
            pythonw = python_bin.with_name("pythonw.exe")
            if pythonw.exists():
                python_bin = pythonw
        else:
            python_bin = PYTHON
        cmd = [str(python_bin), str(ROOT / "main.py"), *cli_args]
    try:
        subprocess.Popen(cmd, creationflags=flags)
    except Exception as exc:
        logging.warning("Could not spawn window %s: %s", args, exc)


class SorterTray:
    def __init__(self, wait_for_setup: bool = False) -> None:
        self.wait_for_setup = wait_for_setup
        self.config = load_config()
        self.downloads = watch_root(self.config)
        self.stop_event = threading.Event()
        self.watcher_failed = False
        self.icon = pystray.Icon(
            "download-sorter",
            icon=make_icon(is_paused(), self._held_count()),
            title=self.tooltip(),
            menu=self.build_menu(),
        )

    # ---- presentation ----

    def _held_count(self) -> int:
        try:
            import hold_manager
            return hold_manager.get_held_summary().get("total_held", 0)
        except Exception:
            return 0

    def tooltip(self) -> str:
        if watch.SETUP_PENDING.is_set():
            state = "Waiting for you to finish setup"
        else:
            state = "Paused" if is_paused() else "Watching Downloads"
        held = self._held_count()
        held_line = f"\n{held} 3MF(s) held in Downloads" if held > 0 else ""
        return f"{about.APP_NAME} - {state}\n{moves_today()} file(s) filed today{held_line}\n{about.CREDIT}"

    def refresh(self) -> None:
        self.icon.icon = make_icon(is_paused(), self._held_count())
        self.icon.title = self.tooltip()
        self.icon.update_menu()

    def build_menu(self) -> pystray.Menu:
        return pystray.Menu(
            pystray.MenuItem("Open Download Sorter", self.on_open_home, default=True),
            pystray.MenuItem(
                "Finish setup...",
                self.on_finish_setup,
                visible=lambda _i: watch.SETUP_PENDING.is_set(),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(lambda _i: f"{moves_today()} file(s) filed today", None, enabled=False),
            pystray.MenuItem(lambda _i: f"{self._held_count()} 3MF(s) held in Downloads", None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Sort Downloads now", self.on_sort_now),
            pystray.MenuItem("Undo last batch", self.on_undo),
            pystray.MenuItem(
                lambda _i: "Resume sorting" if is_paused() else "Pause sorting",
                self.on_toggle_pause,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Configure Rules & Customize...", self.on_open_rules_manager),
            pystray.MenuItem("Remodel Messy Folders...", self.on_remodel),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Open Downloads", self.on_open_downloads),
            pystray.MenuItem("Open log folder", self.on_open_logs),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(f"About {about.APP_NAME}", self.on_about),
            pystray.MenuItem(about.CREDIT, None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self.on_quit),
        )

    def notify(self, message: str, title: str = "Download Sorter") -> None:
        try:
            self.icon.notify(message, title)
        except Exception:  # notifications are a nicety, never a failure
            logging.debug("Tray notification unavailable")

    # ---- menu actions ----

    def on_open_home(self, _icon=None, _item=None) -> None:
        threading.Thread(target=lambda: spawn_window("--home"), daemon=True).start()

    def on_finish_setup(self, _icon=None, _item=None) -> None:
        threading.Thread(target=lambda: spawn_window("--welcome"), daemon=True).start()

    def on_toggle_pause(self, _icon=None, _item=None) -> None:
        paused = not is_paused()
        set_paused(paused)
        watch.WAKE_EVENT.set()
        logging.info("Sorting %s from the tray", "paused" if paused else "resumed")
        self.refresh()
        self.notify(
            "Sorting is paused. New downloads stay in Downloads until you resume."
            if paused else
            "Sorting is on. New downloads are filed a few seconds after they finish."
        )

    def on_sort_now(self, _icon=None, _item=None) -> None:
        def work() -> None:
            batch_id = uuid.uuid4().hex[:8]
            moved = sort_existing(self.config, batch_id=batch_id)
            moved += sort_existing_documents(self.config, batch_id=batch_id)
            self.refresh()
            self.notify(f"Filed {moved} file(s)." if moved else "Nothing needed sorting.")
        threading.Thread(target=work, daemon=True).start()

    def on_undo(self, _icon=None, _item=None) -> None:
        def work() -> None:
            result = run_script("sort_undo.py")
            self.refresh()
            if result is None:
                self.notify("Undo could not run - see the log.")
            else:
                tail = (result.stdout or result.stderr or "").strip().splitlines()
                self.notify(tail[-1] if tail else "Nothing to undo.")
        threading.Thread(target=work, daemon=True).start()

    def on_open_rules_manager(self, _icon=None, _item=None) -> None:
        def work() -> None:
            run_script("rules_manager.py")
        threading.Thread(target=work, daemon=True).start()

    def on_remodel(self, _icon=None, _item=None) -> None:
        def work() -> None:
            run_script("rules_manager.py", "--tab", "remodel")
        threading.Thread(target=work, daemon=True).start()

    def on_open_downloads(self, _icon=None, _item=None) -> None:
        open_in_explorer(self.downloads)

    def on_open_logs(self, _icon=None, _item=None) -> None:
        open_in_explorer(LOG_DIR if LOG_DIR.exists() else LOG_FILE.parent)

    def on_about(self, _icon=None, _item=None) -> None:
        self.notify(f"{about.__version__} - {about.CREDIT}\n{about.REPO_URL}", f"About {about.APP_NAME}")

    def on_quit(self, _icon=None, _item=None) -> None:
        logging.info("Tray quit requested; stopping the watcher")
        SERVICE_DISABLED_FILE.parent.mkdir(parents=True, exist_ok=True)
        SERVICE_DISABLED_FILE.touch(exist_ok=True)
        self.stop_event.set()
        watch.WAKE_EVENT.set()
        self.icon.stop()

    # ---- lifecycle ----

    def on_sorted(self, count: int) -> None:
        self.refresh()

    def start_watcher(self) -> None:
        def work() -> None:
            try:
                watch.run(
                    stop=self.stop_event,
                    on_sorted=self.on_sorted,
                    wait_for_setup=self.wait_for_setup,
                )
                if not self.stop_event.is_set():
                    raise RuntimeError("Watcher returned without a shutdown request")
            except Exception as exc:
                self.watcher_failed = True
                logging.exception("Watcher stopped unexpectedly")
                write_runtime_state("crashed", f"{type(exc).__name__}: {exc}")
                self.notify("The watcher stopped. See the log for details.")
                self.icon.stop()
        threading.Thread(target=work, daemon=True, name="sorter-watch").start()

    def run(self) -> int:
        self.start_watcher()
        self.icon.run()
        self.stop_event.set()
        watch.WAKE_EVENT.set()
        return 1 if self.watcher_failed else 0


def main() -> int:
    if about.handle_version_flag():
        return 0
    setup_logging()
    lock = watch.acquire_single_instance_lock()
    if not lock:
        logging.info("Download Sorter is already running. Opening Home Dashboard.")
        spawn_window("--home")
        return 0
    config = load_config()
    first_run = not config.get("onboarding_completed", False)
    if first_run:
        # Nothing is sorted until the wizard is finished, so the user's profile
        # and "organize existing files" choice apply from the very first move.
        logging.info("First run detected: spawning Welcome Wizard; sorting waits for it.")
        spawn_window("--welcome")
    try:
        import safe_ops
        safe_ops.recover_pending_journal(min_age_seconds=10.0)
    except Exception:
        logging.exception("Failed to run pending journal recovery")
    SERVICE_DISABLED_FILE.unlink(missing_ok=True)
    logging.info("Download Sorter tray starting (PID %s)", os.getpid())
    write_runtime_state("starting", "tray initializing")
    try:
        result = SorterTray(wait_for_setup=first_run).run()
    except BaseException as exc:
        logging.exception("Download Sorter tray crashed")
        write_runtime_state("crashed", f"{type(exc).__name__}: {exc}")
        raise
    if result == 0:
        logging.info("Download Sorter tray stopped cleanly")
        write_runtime_state("stopped", "clean shutdown")
    return result


if __name__ == "__main__":
    sys.exit(main())
