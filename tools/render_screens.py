"""Render Download Sorter's windows to PNG files, for design review and the README.

Everything runs offscreen against throwaway state: LOCALAPPDATA and USERPROFILE
point at a temp folder before anything else is imported, so your real settings,
history and folders are never read or changed.

    .\\.venv\\Scripts\\python.exe tools\\render_screens.py --out C:\\temp\\shots
    .\\.venv\\Scripts\\python.exe tools\\render_screens.py --out C:\\temp\\shots --only home,rules --scale 1.5
    .\\.venv\\Scripts\\python.exe tools\\render_screens.py --out C:\\temp\\shots --min

Groups: home, rules, cleanup, settings, wizard, dialogs, banners, roles (default: all).
"roles" is one sheet with every shared style role (badges, tones, switches, ...).
Besides the PNGs, ``manifest.json`` lists each window's size and the smallest
size its layout asks for, which is what the layout-fit test checks.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

MAIN_SIZE = (1040, 760)
MAIN_MIN = (900, 640)
WIZARD_SIZE = (760, 680)
WIZARD_MIN = (720, 640)
GROUPS = ("home", "rules", "cleanup", "settings", "wizard", "dialogs", "banners", "roles")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", required=True, help="Folder for the PNG files")
    parser.add_argument("--only", default="", help="Comma-separated groups to render (default: all)")
    parser.add_argument("--scale", type=float, default=1.0, help="Display scale, for example 1.5 for 150%%")
    parser.add_argument("--min", action="store_true", help="Render at the smallest allowed window sizes")
    return parser.parse_args()


ARGS = parse_args()
SANDBOX = Path(tempfile.mkdtemp(prefix="download-sorter-shots-"))

# All of this must be in place before sorter is imported: it builds its state
# folder (history, settings) from LOCALAPPDATA when it loads.
os.environ["LOCALAPPDATA"] = str(SANDBOX / "appdata")
os.environ["USERPROFILE"] = str(SANDBOX / "home")
os.environ["QT_QPA_PLATFORM"] = "offscreen"
# Qt's offscreen platform ships no fonts: without this every letter is drawn as a box.
_fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
if _fonts.is_dir():
    os.environ.setdefault("QT_QPA_FONTDIR", str(_fonts))
os.environ["DOWNLOAD_SORTER_REDUCED_MOTION"] = "1"
os.environ["DOWNLOAD_SORTER_NO_UPDATE_CHECK"] = "1"
os.environ["DOWNLOAD_SORTER_NO_DIALOGS"] = "1"
if ARGS.scale != 1.0:
    os.environ["QT_SCALE_FACTOR"] = str(ARGS.scale)

sys.path.insert(0, str(ROOT))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget  # noqa: E402

import config_manager  # noqa: E402
import onboarding  # noqa: E402
import rules_manager  # noqa: E402
import sorter  # noqa: E402
import ui_motion  # noqa: E402
import ui_style  # noqa: E402

# ---------------------------------------------------------------------------
# sample data
# ---------------------------------------------------------------------------

# (file name, folder under Downloads, minutes ago)
SORTED_FILES = [
    ("boarding pass.pdf", "Documents/PDF", 1),
    ("IMG_2041.jpg", "Images/JPG", 3),
    ("beach.jpg", "Images/JPG", 3),
    ("lease agreement.pdf", "Documents/PDF", 12),
    ("setup-tool.exe", "Installers", 35),
    ("March invoice.pdf", "Documents/Invoices & Receipts", 62),
    ("bracket_v3.stl", "3D Models/STL", 95),
    ("song.mp3", "Audio", 180),
    ("parts.zip", "Archives", 400),
]

RULES = [
    {"name": "Invoices & Receipts", "folder": "Documents\\Invoices & Receipts",
     "match": ["invoice", "receipt"], "extensions": [".pdf"], "enabled": True},
    {"name": "Banking (learned)", "folder": "Banking", "match": ["statement"],
     "sites": ["yourbank.com"], "extensions": [], "enabled": True, "origin": "learned"},
    {"name": "IME 4030", "folder": "IME 4030", "match": ["ime4030", "lecture"],
     "extensions": [".pdf", ".pptx"], "enabled": True, "origin": "scan"},
    {"name": "School websites", "folder": "Documents\\School", "match": [],
     "sites": ["instructure.com", "blackboard.com"], "extensions": [], "enabled": True},
    {"name": "Boarding passes", "folder": "Documents\\Travel", "match": ["boarding"],
     "extensions": [".pdf"], "enabled": False},
]

# Folders the person made inside Downloads (the first welcome step offers them).
USER_FOLDERS = {
    "Invoices": ["invoice_march.pdf", "Receipt 2026-03.pdf", "invoice_april.pdf"],
    "IME 4030": ["lecture 3 notes.pdf", "hw2.pdf", "ime4030_syllabus.pdf"],
    "Music": ["track_a.mp3", "track_b.mp3"],
    "New folder": [],
}


def _touch(path: Path, text: str = "demo", minutes_ago: float = 5) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    when = time.time() - minutes_ago * 60
    os.utime(path, (when, when))
    return path


def seed(sandbox: Path) -> dict:
    """A Downloads folder with history, folders of the person's own and a few waiting files."""
    downloads = sandbox / "Downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    (sandbox / "home").mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)

    sorter.LOG_DIR.mkdir(parents=True, exist_ok=True)
    lines = []
    for index, (name, folder, minutes) in enumerate(SORTED_FILES):
        dest = _touch(downloads / Path(folder) / name, minutes_ago=minutes)
        lines.append(json.dumps({
            "id": uuid.uuid4().hex,
            "batch_id": f"demo{index // 3:02d}",
            "timestamp": (now - timedelta(minutes=minutes)).isoformat(),
            "src": str(downloads / name),
            "dest": str(dest),
            "planned_id": uuid.uuid4().hex,
        }))
    Path(sorter.HISTORY_FILE).write_text("\n".join(reversed(lines)) + "\n", encoding="utf-8")

    for folder, names in USER_FOLDERS.items():
        (downloads / folder).mkdir(parents=True, exist_ok=True)
        for name in names:
            _touch(downloads / folder / name, minutes_ago=60 * 24 * 3)
    for name in ("quarterly_budget.xlsx", "holiday photo.jpg"):  # still waiting in Downloads
        _touch(downloads / name, minutes_ago=3)

    cfg = sorter.get_default_config()
    cfg["downloads_dir"] = str(downloads)
    cfg["watch"] = {**(cfg.get("watch") or {}), "folder": str(downloads)}
    cfg["onboarding_completed"] = True
    cfg["keyword_rules"] = [dict(rule) for rule in RULES]
    Path(sorter.USER_CONFIG_PATH).write_text(json.dumps(cfg), encoding="utf-8")
    return cfg


def seed_messy_folder(sandbox: Path) -> Path:
    """A folder for "Clean up a folder": loose files, an unsorted folder, a named one, a code project."""
    messy = sandbox / "Messy"
    old = 60 * 24 * 60  # 60 days, so nothing counts as recently changed
    for rel, text in (
        ("old report.pdf", "report"), ("photo.jpg", "photo"), ("photo copy.jpg", "photo"),
        ("New folder/notes.txt", "notes"), ("New folder/scan 1.png", "scan"), ("New folder/scan 2.png", "scan2"),
        ("IME 4030/lecture 1.pdf", "lecture"), ("IME 4030/lecture 2.pdf", "lecture2"),
        ("my-app/package.json", "{}"), ("my-app/.git/HEAD", "ref"), ("Misc/setup.exe", "exe"),
    ):
        _touch(messy / rel, text, minutes_ago=old)
    for folder in (messy / "New folder", messy / "IME 4030", messy / "my-app", messy / "Misc", messy):
        when = time.time() - old * 60
        os.utime(folder, (when, when))
    return messy


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------

class Shooter:
    def __init__(self, app: QApplication, out: Path) -> None:
        self.app = app
        self.out = out
        self.manifest: list[dict] = []
        out.mkdir(parents=True, exist_ok=True)

    def settle(self, rounds: int = 8) -> None:
        for _ in range(rounds):
            self.app.processEvents()
            time.sleep(0.03)

    def shoot(self, widget: QWidget, name: str, size: tuple[int, int], extra: dict | None = None) -> None:
        widget.resize(*size)
        widget.show()
        self.settle()
        pixmap = widget.grab()
        path = self.out / f"{name}.png"
        saved = pixmap.save(str(path))
        hint = widget.minimumSizeHint()
        entry = {
            "name": name, "file": path.name, "saved": bool(saved),
            "asked": list(size), "actual": [widget.width(), widget.height()],
            "min_hint": [hint.width(), hint.height()],
        }
        entry.update(extra or {})
        self.manifest.append(entry)
        flag = "" if (widget.width(), widget.height()) == size else f"  (window grew to {widget.width()}x{widget.height()})"
        print(f"  {name}: {size[0]}x{size[1]}{flag}")

    def write_manifest(self) -> None:
        (self.out / "manifest.json").write_text(json.dumps(self.manifest, indent=2), encoding="utf-8")


def new_window(initial_view: str) -> rules_manager.RulesManagerDialog:
    return rules_manager.RulesManagerDialog(initial_view=initial_view)


def page_hints(window: rules_manager.RulesManagerDialog) -> dict:
    stack = window._view_stack
    hints = {}
    for index, page_id in enumerate(window.PAGES):
        hint = stack.widget(index).minimumSizeHint()
        hints[page_id] = [hint.width(), hint.height()]
    return {"pages": hints}


def close(widget: QWidget) -> None:
    if hasattr(widget, "_set_dirty"):
        widget._set_dirty(False)
    widget.close()
    widget.deleteLater()


def render_home(shooter: Shooter, size: tuple[int, int], cfg: dict) -> None:
    print("home")
    window = new_window("home")
    shooter.settle(20)  # lets the background "files waiting" count arrive
    shooter.shoot(window, "home_running", size, page_hints(window))

    sorter.is_paused = lambda: True
    window._home_widget.refresh_status()
    window._refresh_sorter_status()
    shooter.shoot(window, "home_paused", size)
    sorter.is_paused = lambda: False

    config_manager.is_sorter_running = lambda: False
    window._home_widget.refresh_status()
    window._refresh_sorter_status()
    shooter.shoot(window, "home_off", size)
    config_manager.is_sorter_running = lambda: True
    window._refresh_sorter_status()

    history = Path(sorter.HISTORY_FILE)
    saved = history.read_text(encoding="utf-8")
    history.unlink()
    window._home_widget.refresh_data()
    window._home_widget.refresh_status()
    window._refresh_sorter_status()
    shooter.shoot(window, "home_empty", size)
    history.write_text(saved, encoding="utf-8")
    close(window)


def render_rules(shooter: Shooter, size: tuple[int, int]) -> None:
    print("rules")
    window = new_window("rules")
    shooter.shoot(window, "rules_name_rules", size)
    window._sandbox._input.setText("March invoice.pdf")
    shooter.shoot(window, "rules_sandbox_match", size)
    window._sandbox._input.setText("holiday-photo-final.xyz")
    shooter.shoot(window, "rules_sandbox_nomatch", size)
    window._sandbox._input.setText("")
    window._tabs.setCurrentWidget(window._extension_table)
    shooter.shoot(window, "rules_file_types", size)
    close(window)


def render_cleanup(shooter: Shooter, size: tuple[int, int], messy: Path) -> None:
    print("cleanup")
    window = new_window("cleanup")
    shooter.shoot(window, "cleanup_empty", size)
    widget = window._remodel_widget
    widget._scope_combo.setCurrentIndex(3)
    widget._custom_path_edit.setText(str(messy))
    plan = widget._engine.scan(messy, scope="custom")
    widget._on_scan_finished(plan)
    shooter.shoot(window, "cleanup_scanned", size)
    close(window)


def render_settings(shooter: Shooter, size: tuple[int, int]) -> None:
    print("settings")
    window = new_window("settings")
    shooter.shoot(window, "settings", size)
    tall = (size[0], 1900)
    shooter.shoot(window, "settings_full", tall)
    close(window)


def render_wizard(shooter: Shooter, size: tuple[int, int], cfg: dict, sandbox: Path) -> None:
    print("wizard")
    empty = sandbox / "EmptyDownloads"
    empty.mkdir(parents=True, exist_ok=True)
    empty_cfg = json.loads(json.dumps(cfg))
    empty_cfg["downloads_dir"] = str(empty)
    empty_cfg["watch"]["folder"] = str(empty)

    for label, config in (("with_folders", cfg), ("no_folders", empty_cfg)):
        wizard = onboarding.WelcomeWizardDialog(config=config)
        for step in range(3):
            wizard._current_step = step
            wizard._stack.setCurrentIndex(step)
            wizard._update_navigation()
            hints = {"step_hints": [[wizard._stack.widget(i).minimumSizeHint().width(),
                                     wizard._stack.widget(i).minimumSizeHint().height()]
                                    for i in range(wizard._stack.count())]}
            shooter.shoot(wizard, f"wizard_{label}_step{step + 1}", size, hints)
        wizard.close()
        wizard.deleteLater()


def render_dialogs(shooter: Shooter) -> None:
    print("dialogs")
    edit = rules_manager.RuleEditDialog(rule=dict(RULES[1]), rule_type="keyword")
    shooter.shoot(edit, "dialog_rule_editor", (540, edit.sizeHint().height()))
    edit.close()
    library = rules_manager.PresetLibraryDialog()
    shooter.shoot(library, "dialog_rule_library", (620, 540))
    library.close()


def render_banners(shooter: Shooter) -> None:
    print("banners")
    host = QWidget()
    host.setStyleSheet(ui_style.stylesheet())
    layout = QVBoxLayout(host)
    layout.setContentsMargins(24, 24, 24, 24)
    layout.setSpacing(12)
    banners = []
    for kind, text, action in (
        ("success", "Sorted 8 files into 3 folders: Images (4), Documents (3), Installers (1).", "Undo"),
        ("info", "Download Sorter 2.4.0 is available. Your rules and settings are kept.", "Download and install"),
        ("warning", "Sorting is paused. New downloads will stay in Downloads until you resume.", ""),
        ("error", "Download Sorter didn't start. Try again, or open it from the Start menu.", "Try again"),
    ):
        banner = ui_motion.Banner(host)
        layout.addWidget(banner)
        banners.append((banner, kind, text, action))
    layout.addStretch()
    host.resize(720, 300)
    host.show()
    for banner, kind, text, action in banners:
        banner.show_message(text, kind, action, (lambda: None) if action else None, timeout_ms=0)
    shooter.shoot(host, "banners", (720, 300))
    host.close()


def render_roles(shooter: Shooter) -> None:
    """One sheet with every shared style role, to check them side by side."""
    print("roles")
    from PySide6.QtWidgets import QButtonGroup, QHBoxLayout, QProgressBar, QRadioButton

    import ui_categories
    import ui_widgets
    from suite_theme import COLORS

    host = QWidget()
    host.setObjectName("host")
    host.setStyleSheet(ui_style.stylesheet() + f"QWidget#host {{ background: {COLORS['canvas']}; }}")
    root = QVBoxLayout(host)
    root.setContentsMargins(24, 20, 24, 20)
    root.setSpacing(14)

    root.addWidget(ui_style.label("Badges", "section"))
    row = QHBoxLayout()
    for kind in ("neutral", "success", "info", "warning", "critical"):
        row.addWidget(ui_style.badge(kind.title(), kind))
    row.addStretch()
    root.addLayout(row)

    root.addWidget(ui_style.label("Card tones (plain, hero, success, warning, info, critical)", "section"))
    cards = QHBoxLayout()
    for tone in (None, "hero", "success", "warning", "info", "critical"):
        frame, lay = ui_style.card(margins=(12, 10, 12, 10), spacing=4)
        lay.addWidget(ui_style.label(tone or "plain", "section"))
        lay.addWidget(ui_style.label("Some card text", "muted"))
        lay.addWidget(ui_style.label("A small caption", "caption"))
        if tone == "hero":
            frame.setProperty("hero", "true")
        elif tone:
            ui_style.set_tone(frame, tone)
        cards.addWidget(frame)
    root.addLayout(cards)

    root.addWidget(ui_style.label("Switches (off, on, disabled off, disabled on, focused)", "section"))
    switches = []
    for index, (checked, enabled) in enumerate(((False, True), (True, True), (False, False), (True, False), (True, True))):
        switch_row, switch = ui_style.switch_row(
            f"Option {index + 1}", "A one-line description of what this does.")
        switch.setChecked(checked)
        switch.setEnabled(enabled)
        root.addWidget(switch_row)
        switches.append(switch)

    root.addWidget(ui_style.label("Segmented choice", "section"))
    segments = QHBoxLayout()
    group = QButtonGroup(host)
    for index, text in enumerate(("Organize by file type", "Keep intact", "Skip for now")):
        radio = QRadioButton(text)
        radio.setProperty("role", "segment")
        radio.setChecked(index == 1)
        group.addButton(radio, index + 1)
        segments.addWidget(radio)
    segments.addStretch()
    root.addLayout(segments)

    root.addWidget(ui_style.label("Step indicator", "section"))
    steps = QHBoxLayout()
    for current in (0, 1, 2):
        stepper = ui_widgets.Stepper(["Your folders", "How it works", "Finish"])
        stepper.set_current(current)
        steps.addWidget(stepper)
    steps.addStretch()
    root.addLayout(steps)

    root.addWidget(ui_style.label("File kinds and the row menu", "section"))
    tiles = QHBoxLayout()
    for kind in ui_categories.KINDS:
        tiles.addWidget(ui_widgets.category_tile(kind, 32))
    menu_button = ui_widgets.RowMenuButton("File actions")
    tiles.addWidget(menu_button)
    tiles.addStretch()
    root.addLayout(tiles)

    bars = QHBoxLayout()
    for value in (35, 0):
        bar = QProgressBar()
        bar.setTextVisible(False)
        bar.setFixedHeight(4)
        bar.setRange(0, 100 if value else 0)
        bar.setValue(value)
        bars.addWidget(bar)
    root.addLayout(bars)
    root.addStretch()

    host.show()
    switches[-1].setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    switches[-1].setFocus(Qt.FocusReason.TabFocusReason)
    shooter.shoot(host, "roles", (980, 940))
    host.close()


def main() -> int:
    try:
        QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    except Exception:
        pass
    app = QApplication.instance() or QApplication([])

    # The window asks the background sorter whether it is running; pretend it is.
    config_manager.is_sorter_running = lambda: True
    sorter.is_paused = lambda: False

    cfg = seed(SANDBOX)
    messy = seed_messy_folder(SANDBOX)
    wanted = {g.strip() for g in ARGS.only.split(",") if g.strip()} or set(GROUPS)
    main_size = MAIN_MIN if ARGS.min else MAIN_SIZE
    wizard_size = WIZARD_MIN if ARGS.min else WIZARD_SIZE

    out = Path(ARGS.out)
    shooter = Shooter(app, out)
    print(f"Rendering to {out} (scale {ARGS.scale}, state in {SANDBOX})")
    steps = (
        ("home", lambda: render_home(shooter, main_size, cfg)),
        ("rules", lambda: render_rules(shooter, main_size)),
        ("cleanup", lambda: render_cleanup(shooter, main_size, messy)),
        ("settings", lambda: render_settings(shooter, main_size)),
        ("wizard", lambda: render_wizard(shooter, wizard_size, cfg, SANDBOX)),
        ("dialogs", lambda: render_dialogs(shooter)),
        ("banners", lambda: render_banners(shooter)),
        ("roles", lambda: render_roles(shooter)),
    )
    failures = 0
    for group, run in steps:
        if group not in wanted:
            continue
        try:
            run()
        except Exception as exc:  # keep going: one broken page shouldn't hide the others
            failures += 1
            print(f"  !! {group} failed: {type(exc).__name__}: {exc}")
    shooter.write_manifest()
    shutil.rmtree(SANDBOX, ignore_errors=True)
    print(f"Done: {len(shooter.manifest)} images, {failures} failed.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
