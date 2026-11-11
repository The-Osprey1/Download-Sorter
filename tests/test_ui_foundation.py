"""The shared pieces every page is built from: style roles, icons, file kinds and small widgets.

Pages are restyled by giving widgets a role (a badge, a switch, a tinted card)
instead of writing colors into each one. These tests keep those roles defined,
readable and in step with the suite's colors.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QCheckBox, QLabel, QVBoxLayout, QWidget

import ribbon_popup
import ui_categories
import ui_icons
import ui_style
import ui_widgets
from suite_theme import ACCENTS, COLORS

ROOT = Path(__file__).resolve().parent.parent


def _luminance(color: str) -> float:
    def channel(value: int) -> float:
        value /= 255
        return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4

    red, green, blue = (int(color.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * channel(red) + 0.7152 * channel(green) + 0.0722 * channel(blue)


def contrast(foreground: str, background: str) -> float:
    light, dark = sorted((_luminance(foreground), _luminance(background)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


# ---------------------------------------------------------------------------
# file kinds (shared by the Qt window and the Tk ribbon)
# ---------------------------------------------------------------------------

KIND_CASES = [
    ("bracket.step", "CAD", "cad", "#69E6AD"),
    ("spindle.nc", "Haas", "cnc", "#D74A4A"),
    ("plate.gcode", "Print Files", "print", "#E8833A"),
    ("lecture.pdf", "School", "pdf", "#FA7183"),
    ("notes.docx", "Documents", "doc", "#61A5FF"),
    ("budget.xlsx", "Spreadsheets", "sheet", "#69E6AD"),
    ("render.png", "Images", "image", "#A855F7"),
    ("clip.mp4", "Videos", "video", "#EC4899"),
    ("track.mp3", "Audio", "audio", "#8B5CF6"),
    ("archive.zip", "Archives", "archive", "#06B6D4"),
    ("installer.exe", "Installers", "installer", "#F43F5E"),
    ("script.py", "Code", "code", "#F5AD62"),
    # no useful extension: the folder name decides
    ("README", "Spreadsheets", "sheet", "#69E6AD"),
    ("part", "3D Models\\STL", "cad", "#69E6AD"),
    ("misc", "Other", "default", "#8B94A7"),
    ("misc", "", "default", "#69E6AD"),
]


@pytest.mark.parametrize("name, folder, kind, color", KIND_CASES)
def test_file_kinds_are_the_same_in_the_window_and_the_ribbon(name, folder, kind, color):
    assert ui_categories.category_info(Path(name), folder) == (kind, color)
    assert ribbon_popup.get_category_info(Path(name), folder) == (kind, color)
    assert ribbon_popup.get_accent_color(Path(name), folder) == color
    assert ui_categories.kind_of(name, folder) == kind


def test_every_kind_has_a_label_a_color_and_an_icon():
    names = set(ui_icons.names())
    for kind in ui_categories.KINDS:
        assert ui_categories.LABEL_BY_KIND[kind]
        assert re.fullmatch(r"#[0-9A-Fa-f]{6}", ui_categories.COLOR_BY_KIND[kind])
        assert ui_icons.category_icon_name(kind) == f"cat_{kind}"
        assert f"cat_{kind}" in names
    assert ui_icons.category_icon_name("no-such-kind") == "cat_default"


def test_the_suites_own_category_colors_match_its_tokens():
    assert ui_categories.MINT == ACCENTS["downloadSorter"]
    assert ui_categories.RED == ACCENTS["haasStudio"]
    assert ui_categories.ORANGE == ACCENTS["printLibrary"]
    assert ui_categories.CORAL == COLORS["critical"]
    assert ui_categories.BLUE == COLORS["info"]
    assert ui_categories.AMBER == COLORS["warning"]
    assert ui_categories.SLATE == COLORS["textMuted"]


# ---------------------------------------------------------------------------
# icons
# ---------------------------------------------------------------------------

def test_every_icon_renders_something(qapp):
    for name in ui_icons.names():
        image = ui_icons.pixmap(name, size=24).toImage()
        assert not image.isNull(), name
        assert any(image.pixelColor(x, y).alpha() > 0
                   for x in range(0, image.width(), 2) for y in range(0, image.height(), 2)), f"{name} is blank"


# ---------------------------------------------------------------------------
# style sheet roles
# ---------------------------------------------------------------------------

def test_stylesheet_defines_the_roles_pages_use():
    sheet = ui_style.stylesheet()
    for needle in (
        'QLabel[role="badge"]', 'QLabel[role="badge"][kind="success"]', 'QLabel[role="badge"][kind="critical"]',
        'QFrame#card[tone="success"]', 'QFrame#card[tone="warning"]', 'QFrame#card[hero="true"]',
        'QCheckBox[role="switch"]::indicator', 'QRadioButton[role="segment"]', "QProgressBar::chunk",
    ):
        assert needle in sheet, needle
    assert "qlineargradient(" in sheet, "the hero card gradient comes from the surfaceCard token"
    assert "{c[" not in sheet and "{ACCENT" not in sheet and "None" not in sheet


def test_caption_text_is_readable_on_every_surface():
    sheet = ui_style.stylesheet()
    caption = re.search(r'QLabel\[role="caption"\] \{ color: (#[0-9A-Fa-f]{6})', sheet)
    assert caption, "caption rule not found"
    assert caption.group(1).lower() == COLORS["textMuted"].lower(), "captions use textMuted, not textFaint"
    for surface in ("canvas", "surface", "surfaceRaised", "surfaceHover"):
        assert contrast(caption.group(1), COLORS[surface]) >= 4.5, surface
        assert contrast(COLORS["text"], COLORS[surface]) >= 7, surface


def test_switch_pictures_are_written_to_files():
    images = ui_style._switch_images()
    assert set(images) == {"off", "on", "off_focus", "on_focus", "off_disabled", "on_disabled"}
    for path in images.values():
        assert "<svg" in Path(path).read_text(encoding="utf-8")


def test_new_ui_modules_use_tokens_not_raw_colors():
    # ui_categories is the one place with its own palette (the extra hues have no token).
    for name in ("ui_style.py", "ui_widgets.py", "ui_icons.py"):
        source = (ROOT / name).read_text(encoding="utf-8")
        assert not re.findall(r"#[0-9A-Fa-f]{6}\b", source), f"{name} has a raw color; use a suite_theme token"


# ---------------------------------------------------------------------------
# builders and widgets
# ---------------------------------------------------------------------------

def _host(*widgets: QWidget) -> QWidget:
    host = QWidget()
    host.setStyleSheet(ui_style.stylesheet() + f"QWidget#host {{ background: {COLORS['canvas']}; }}")
    host.setObjectName("host")
    layout = QVBoxLayout(host)
    for widget in widgets:
        layout.addWidget(widget)
    return host


def _has_color(image, color: str) -> bool:
    wanted = color.lower()
    return any(image.pixelColor(x, y).name().lower() == wanted
               for x in range(image.width()) for y in range(image.height()))


def test_badge_and_tone_set_the_properties_the_stylesheet_reads(qapp):
    tag = ui_style.badge("Learned", "success")
    assert tag.text() == "Learned"
    assert tag.property("role") == "badge" and tag.property("kind") == "success"
    card, _ = ui_style.card()
    ui_style.set_tone(card, "warning")
    assert card.property("tone") == "warning"
    ui_style.set_tone(card, None)
    assert card.property("tone") == ""


def test_switch_row_is_a_checkbox_that_also_toggles_from_its_text(qapp):
    row, switch = ui_style.switch_row("Start with Windows", "Sorts new downloads without opening the app.")
    assert isinstance(switch, QCheckBox)
    assert switch.property("role") == "switch"
    assert switch.focusPolicy() == Qt.FocusPolicy.TabFocus
    assert switch.accessibleName() == "Start with Windows"
    assert switch.accessibleDescription()

    seen: list[bool] = []
    switch.toggled.connect(seen.append)
    title = row.findChildren(QLabel)[0]
    QTest.mouseClick(title, Qt.MouseButton.LeftButton)
    assert switch.isChecked() and seen == [True]
    QTest.mouseClick(title, Qt.MouseButton.LeftButton)
    assert not switch.isChecked() and seen == [True, False]

    switch.setEnabled(False)
    QTest.mouseClick(title, Qt.MouseButton.LeftButton)
    assert not switch.isChecked(), "a disabled switch ignores clicks on its text"


def test_switch_draws_its_on_and_off_pictures(qapp):
    row, switch = ui_style.switch_row("Option")
    host = _host(row)
    host.resize(300, 80)
    host.show()
    qapp.processEvents()
    mint = ACCENTS["downloadSorter"]
    assert not _has_color(host.grab().toImage(), mint), "off: no accent color"
    switch.setChecked(True)
    qapp.processEvents()
    assert _has_color(host.grab().toImage(), mint), "on: the track is the accent color"
    host.close()


def test_stepper_announces_the_current_step_and_paints(qapp):
    stepper = ui_widgets.Stepper(["Your folders", "How it works", "Finish"])
    assert stepper.accessibleName() == "Step 1 of 3: Your folders"
    stepper.set_current(2)
    assert stepper.current() == 2 and stepper.accessibleName() == "Step 3 of 3: Finish"
    stepper.set_current(99)
    assert stepper.current() == 2, "clamped to the last step"
    stepper.set_current(-4)
    assert stepper.current() == 0
    stepper.resize(stepper.sizeHint())
    image = stepper.grab().toImage()
    assert not image.isNull() and image.width() >= stepper.sizeHint().width()
    assert _has_color(image, ACCENTS["downloadSorter"]), "the current step is filled with the accent color"


def test_category_tile_for_every_kind(qapp):
    for kind in ui_categories.KINDS:
        tile = ui_widgets.category_tile(kind)
        assert (tile.width(), tile.height()) == (28, 28)
        assert not tile.pixmap().isNull()
        assert tile.accessibleName() == ui_categories.LABEL_BY_KIND[kind]
    assert not ui_widgets.category_tile("no-such-kind").pixmap().isNull()


def test_row_menu_button_builds_its_menu_from_actions(qapp):
    called: list[str] = []
    button = ui_widgets.RowMenuButton("File actions")
    assert button.accessibleName() == "File actions"
    button.set_actions([
        ("Put back", "undo", lambda: called.append("put back"), True),
        ("Move to...", "move", lambda: called.append("move"), True),
        None,
        ("Show in folder", None, lambda: called.append("show"), False),
    ])
    actions = button.menu().actions()
    assert [a.text() for a in actions if not a.isSeparator()] == ["Put back", "Move to...", "Show in folder"]
    assert actions[2].isSeparator()
    assert [a.isEnabled() for a in actions if not a.isSeparator()] == [True, True, False]
    actions[1].trigger()
    assert called == ["move"]


def test_clickable_frame_reacts_to_a_click_and_to_the_keyboard(qapp):
    frame = ui_widgets.ClickableFrame()
    frame.resize(80, 30)
    frame.show()
    seen: list[str] = []
    frame.clicked.connect(lambda: seen.append("clicked"))
    assert frame.focusPolicy() == Qt.FocusPolicy.TabFocus

    QTest.mouseClick(frame, Qt.MouseButton.LeftButton)
    QTest.keyClick(frame, Qt.Key.Key_Space)
    QTest.keyClick(frame, Qt.Key.Key_Return)
    QTest.keyClick(frame, Qt.Key.Key_A)

    assert seen == ["clicked"] * 3
    frame.close()


def test_notice_bar_takes_no_room_until_it_has_something_to_say(qapp):
    host = QWidget()
    layout = QVBoxLayout(host)
    bar = ui_widgets.NoticeBar(host)
    layout.addWidget(bar)
    host.show()
    qapp.processEvents()
    assert not bar.isVisible() and bar.sizeHint().height() > 0

    clicked: list[int] = []
    bar.show_message("Saved.", "success", "Open", lambda: clicked.append(1))
    qapp.processEvents()
    assert bar.isVisible() and bar.banner.text() == "Saved."

    bar.banner._action_btn.click()
    assert clicked == [1] and not bar.isVisible()
    host.close()


def test_padded_icon_is_the_size_of_the_other_icons_in_its_column(qapp):
    from PySide6.QtCore import QSize

    icon = ui_widgets.padded_icon("folder", size=28)
    assert not icon.isNull()
    assert icon.actualSize(QSize(28, 28)) == QSize(28, 28)
    assert not ui_widgets.category_tile_icon("pdf").isNull()
