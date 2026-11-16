"""Interactive 4-step picture guide modal for loading the Download Sorter Chrome extension."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import os
from pathlib import Path
import sys

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QKeyEvent, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from download_sorter.chrome_bridge import (
    EXTENSION_ID as CHROME_EXTENSION_ID,
    find_extension_dir,
    install_chrome_native_host,
    is_chrome_host_registered,
)
from download_sorter.ui.motion import (
    InteractiveRibbonBtn,
    SortyMascotWidget,
    animations_enabled,
    fade_in,
    make_badge,
    make_vector_icon,
    refresh_style,
)


@dataclass(frozen=True)
class ChromeGuideStep:
    """Metadata and visual asset descriptor for a single Chrome setup step."""

    badge: str
    title: str
    description: str
    image_name: str
    footnote: str


CHROME_GUIDE_STEPS: tuple[ChromeGuideStep, ...] = (
    ChromeGuideStep(
        badge="STEP 1 OF 4",
        title="Open Extensions & Turn ON Developer Mode",
        description=(
            "Open Google Chrome (or Edge / Brave), paste chrome://extensions into the address bar, "
            "and flip Developer mode ON in the top-right corner."
        ),
        image_name="step1_dev_mode.png",
        footnote="Tip: Browsers block external chrome:// links — click 'Copy' below and paste with Ctrl+V.",
    ),
    ChromeGuideStep(
        badge="STEP 2 OF 4",
        title="Copy Extension Path & Click 'Load unpacked'",
        description=(
            "Click 'Copy Extension Path' below first so the path is on your clipboard, "
            "then click 'Load unpacked' in Chrome's top-left toolbar."
        ),
        image_name="step2_load_unpacked.png",
        footnote="The extension lives inside your Download Sorter installation under chrome_extension.",
    ),
    ChromeGuideStep(
        badge="STEP 3 OF 4",
        title="Paste Path into Windows 'Select Folder'",
        description=(
            "Method A: Click blank top bar (or Ctrl+L) → Ctrl+V → Enter.  "
            "Method B: Click bottom 'Folder:' box → Ctrl+V.  Then click Select Folder."
        ),
        image_name="step3_select_folder.png",
        footnote="Both methods navigate to the exact chrome_extension folder — use whichever feels easiest!",
    ),
    ChromeGuideStep(
        badge="STEP 4 OF 4",
        title="Connected & Ready for Direct Sorting",
        description=(
            "The Download Sorter icon is now active in Chrome! New downloads save straight into "
            "sorted subfolders while keeping Chrome's download bubble draggable."
        ),
        image_name="step4_connected.png",
        footnote=f"Extension ID: {CHROME_EXTENSION_ID} · Native Messaging Host stays synced automatically.",
    ),
)

_PICTURE_WIDTH = 640
_PICTURE_HEIGHT = 357


def guide_asset_path(filename: str) -> Path | None:
    """Resolve a bundled Chrome setup graphic in both source and PyInstaller builds."""
    candidates: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "download_sorter" / "ui" / "assets" / "chrome_guide" / filename)
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        candidates.extend(
            [
                exe_dir / "_internal" / "download_sorter" / "ui" / "assets" / "chrome_guide" / filename,
                exe_dir / "assets" / "chrome_guide" / filename,
            ]
        )
    candidates.append(Path(__file__).resolve().parent / "assets" / "chrome_guide" / filename)

    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


class ChromeExtensionWalkthroughDialog(QDialog):
    """Apple-minimal 4-step picture guide with arrows for installing the Chrome extension."""

    def __init__(
        self,
        data_dir: Path | None = None,
        *,
        on_host_updated: Callable[[], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._data_dir = data_dir
        self._on_host_updated = on_host_updated
        self._drag_offset: QPoint | None = None
        self._step_index = 0

        self.setObjectName("WalkthroughModal")
        self.setWindowTitle("Chrome Extension Setup Guide")
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(736, 680)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)

        container = QFrame(self)
        container.setObjectName("ModalContainer")
        shadow = QGraphicsDropShadowEffect(container)
        shadow.setBlurRadius(32)
        shadow.setOffset(0, 10)
        shadow.setColor(QColor(0, 0, 0, 175))
        container.setGraphicsEffect(shadow)
        outer.addWidget(container)

        layout = QVBoxLayout(container)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(12)

        # Header with Sorty mascot + step counter + title + description
        header_row = QHBoxLayout()
        header_row.setSpacing(14)

        self.mascot_widget = SortyMascotWidget(size=52, parent=container)
        self.mascot_widget.setToolTip("Chrome Extension Setup Assistant")
        header_row.addWidget(self.mascot_widget, 0, Qt.AlignmentFlag.AlignTop)

        header_text = QVBoxLayout()
        header_text.setSpacing(3)

        self.lbl_step_badge = QLabel("")
        self.lbl_step_badge.setStyleSheet(
            "color: #69E6AD; font-size: 11px; font-weight: 700; letter-spacing: 0.7px;"
        )
        header_text.addWidget(self.lbl_step_badge)

        self.lbl_title = QLabel("")
        self.lbl_title.setWordWrap(True)
        self.lbl_title.setStyleSheet("font-size: 17px; font-weight: 600; color: #F5F5F7;")
        header_text.addWidget(self.lbl_title)

        self.lbl_description = QLabel("")
        self.lbl_description.setWordWrap(True)
        self.lbl_description.setStyleSheet("color: #AEAEB2; font-size: 12.5px; line-height: 1.35;")
        header_text.addWidget(self.lbl_description)

        header_row.addLayout(header_text, 1)
        layout.addLayout(header_row)

        # Picture stage card
        self.picture_frame = QFrame(container)
        self.picture_frame.setObjectName("ModalBanner")
        self.picture_frame.setFixedSize(_PICTURE_WIDTH, _PICTURE_HEIGHT)
        pic_layout = QVBoxLayout(self.picture_frame)
        pic_layout.setContentsMargins(0, 0, 0, 0)
        pic_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.lbl_picture = QLabel(self.picture_frame)
        self.lbl_picture.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_picture.setFixedSize(_PICTURE_WIDTH - 2, _PICTURE_HEIGHT - 2)
        pic_layout.addWidget(self.lbl_picture)
        layout.addWidget(self.picture_frame, 0, Qt.AlignmentFlag.AlignCenter)

        # Contextual 1-click actions per step
        self.action_stack = QStackedWidget()
        self.action_stack.setFixedHeight(38)
        self.action_stack.addWidget(self._build_step1_actions())
        self.action_stack.addWidget(self._build_copy_open_actions("step2"))
        self.action_stack.addWidget(self._build_copy_open_actions("step3"))
        self.action_stack.addWidget(self._build_step4_actions())
        layout.addWidget(self.action_stack)

        self.lbl_footnote = QLabel("")
        self.lbl_footnote.setWordWrap(True)
        self.lbl_footnote.setObjectName("MutedText")
        layout.addWidget(self.lbl_footnote)
        layout.addStretch(1)

        # Footer: StepDots on left, Close / Back / Next on right
        footer = QHBoxLayout()
        footer.setSpacing(8)
        self._dots: list[QLabel] = []
        for _ in CHROME_GUIDE_STEPS:
            dot = QLabel("")
            dot.setObjectName("StepDot")
            self._dots.append(dot)
            footer.addWidget(dot)

        footer.addStretch(1)

        self.btn_close = InteractiveRibbonBtn("Close")
        self.btn_close.setObjectName("GhostButton")
        self.btn_close.clicked.connect(self.reject)

        self.btn_back = InteractiveRibbonBtn("Back", make_vector_icon("arrow_left", "#F5F5F7", 15))
        self.btn_back.clicked.connect(lambda: self.go_to_step(self._step_index - 1))

        self.btn_next = InteractiveRibbonBtn("Next", make_vector_icon("arrow_right", "#FFFFFF", 15))
        self.btn_next.setObjectName("PrimaryButton")
        self.btn_next.clicked.connect(self._on_next_clicked)

        footer.addWidget(self.btn_close)
        footer.addWidget(self.btn_back)
        footer.addWidget(self.btn_next)
        layout.addLayout(footer)

        self.go_to_step(0)

    def _build_step1_actions(self) -> QWidget:
        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        self.btn_copy_chrome_url = InteractiveRibbonBtn(
            "Copy chrome://extensions",
            make_vector_icon("copy", "#FFFFFF", 14),
        )
        self.btn_copy_chrome_url.setObjectName("PrimaryButton")
        self.btn_copy_chrome_url.clicked.connect(self._on_copy_chrome_url)
        row.addWidget(self.btn_copy_chrome_url)

        self.lbl_url_status = make_badge("Paste in Chrome address bar", "neutral")
        row.addWidget(self.lbl_url_status)
        row.addStretch(1)
        return bar

    def _build_copy_open_actions(self, prefix: str) -> QWidget:
        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        ext_dir = find_extension_dir()
        btn_copy = InteractiveRibbonBtn(
            "Copy Extension Path",
            make_vector_icon("copy", "#FFFFFF", 14),
        )
        btn_copy.setObjectName("PrimaryButton")
        btn_copy.setEnabled(ext_dir is not None)
        row.addWidget(btn_copy)

        btn_open = InteractiveRibbonBtn(
            "Reveal in File Explorer",
            make_vector_icon("folder", "#F5F5F7", 14),
        )
        btn_open.setEnabled(ext_dir is not None)
        btn_open.clicked.connect(self._on_open_extension_folder)
        row.addWidget(btn_open)

        lbl_status = make_badge(
            str(ext_dir.name) if ext_dir else "Extension folder not found",
            "neutral" if ext_dir else "warning",
        )
        row.addWidget(lbl_status)
        row.addStretch(1)

        btn_copy.clicked.connect(lambda: self._on_copy_extension_path(btn_copy, lbl_status))
        setattr(self, f"btn_{prefix}_copy", btn_copy)
        setattr(self, f"btn_{prefix}_open", btn_open)
        setattr(self, f"lbl_{prefix}_status", lbl_status)
        return bar

    def _build_step4_actions(self) -> QWidget:
        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        self.lbl_host_status = make_badge("CHECKING...", "neutral")
        row.addWidget(self.lbl_host_status)

        self.btn_repair_host = InteractiveRibbonBtn(
            "Verify / Register Chrome Host",
            make_vector_icon("bolt", "#FFFFFF", 14),
        )
        self.btn_repair_host.clicked.connect(self._on_register_host)
        row.addWidget(self.btn_repair_host)
        row.addStretch(1)
        self._refresh_host_badge()
        return bar

    def _refresh_host_badge(self) -> None:
        registered = is_chrome_host_registered(self._data_dir)
        if registered:
            self.lbl_host_status.setText("HOST REGISTERED")
            self.lbl_host_status.setProperty("variant", "success")
            self.btn_repair_host.setText("Repair Chrome Host")
        else:
            self.lbl_host_status.setText("HOST NOT REGISTERED")
            self.lbl_host_status.setProperty("variant", "warning")
            self.btn_repair_host.setText("Install Chrome Host")
        refresh_style(self.lbl_host_status)

    def _on_copy_chrome_url(self) -> None:
        cb = QApplication.clipboard()
        if cb is not None:
            cb.setText("chrome://extensions")
        self.btn_copy_chrome_url.setText("Copied! Paste in Chrome")
        self.btn_copy_chrome_url.setIcon(make_vector_icon("check", "#FFFFFF", 14))
        self.lbl_url_status.setText("COPIED TO CLIPBOARD")
        self.lbl_url_status.setProperty("variant", "success")
        refresh_style(self.lbl_url_status)

    def _on_copy_extension_path(self, button: InteractiveRibbonBtn, badge: QLabel) -> None:
        ext_dir = find_extension_dir()
        if ext_dir is None:
            QMessageBox.warning(self, "Chrome Extension", "Could not find the chrome_extension folder.")
            return
        cb = QApplication.clipboard()
        if cb is not None:
            cb.setText(str(ext_dir))
        button.setText("Path Copied!")
        button.setIcon(make_vector_icon("check", "#FFFFFF", 14))
        badge.setText("READY TO PASTE (CTRL+V)")
        badge.setProperty("variant", "success")
        refresh_style(badge)

    def _on_open_extension_folder(self) -> None:
        ext_dir = find_extension_dir()
        if ext_dir is None:
            QMessageBox.warning(self, "Chrome Extension", "Could not find the chrome_extension folder.")
            return
        if sys.platform == "win32" and hasattr(os, "startfile"):
            os.startfile(str(ext_dir))

    def _on_register_host(self) -> None:
        install_chrome_native_host(self._data_dir)
        self._refresh_host_badge()
        if self._on_host_updated is not None:
            self._on_host_updated()

    def _render_step_picture(self, image_name: str) -> None:
        asset = guide_asset_path(image_name)
        if asset is None:
            self.lbl_picture.setPixmap(QPixmap())
            self.lbl_picture.setText(f"Guide preview image unavailable ({image_name}).")
            return
        px = QPixmap(str(asset))
        if px.isNull():
            self.lbl_picture.setPixmap(QPixmap())
            self.lbl_picture.setText(f"Could not render preview image ({image_name}).")
            return
        dpr = max(1.0, float(self.devicePixelRatioF() or 1.0))
        target_w = int((_PICTURE_WIDTH - 4) * dpr)
        target_h = int((_PICTURE_HEIGHT - 4) * dpr)
        scaled = px.scaled(
            target_w,
            target_h,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        scaled.setDevicePixelRatio(dpr)
        self.lbl_picture.setText("")
        self.lbl_picture.setPixmap(scaled)

    @property
    def current_step(self) -> int:
        return self._step_index

    def go_to_step(self, index: int) -> None:
        """Switch the walkthrough view to step ``index`` (clamped to valid range)."""
        idx = max(0, min(len(CHROME_GUIDE_STEPS) - 1, int(index)))
        self._step_index = idx
        step = CHROME_GUIDE_STEPS[idx]

        self.lbl_step_badge.setText(step.badge)
        self.lbl_title.setText(step.title)
        self.lbl_description.setText(step.description)
        self.lbl_footnote.setText(step.footnote)
        self.action_stack.setCurrentIndex(idx)
        self._render_step_picture(step.image_name)

        # Keep Sorty pose in sync (Step 4 shows celebration/smart pose)
        self.mascot_widget.set_step(min(2, idx))

        if idx == len(CHROME_GUIDE_STEPS) - 1:
            self._refresh_host_badge()

        for i, dot in enumerate(self._dots):
            dot.setProperty("active", "true" if i == idx else "false")
            dot.setProperty("done", "true" if i < idx else "false")
            refresh_style(dot)

        self.btn_back.setEnabled(idx > 0)
        if idx == len(CHROME_GUIDE_STEPS) - 1:
            self.btn_next.setText("Done")
            self.btn_next.setIcon(make_vector_icon("check", "#FFFFFF", 15))
        else:
            self.btn_next.setText("Next")
            self.btn_next.setIcon(make_vector_icon("arrow_right", "#FFFFFF", 15))

        if animations_enabled() and not os.environ.get("PYTEST_CURRENT_TEST"):
            fade_in(self.lbl_picture, 180)

    def _on_next_clicked(self) -> None:
        if self._step_index < len(CHROME_GUIDE_STEPS) - 1:
            self.go_to_step(self._step_index + 1)
        else:
            self.accept()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Right:
            if self._step_index < len(CHROME_GUIDE_STEPS) - 1:
                self.go_to_step(self._step_index + 1)
            return
        if event.key() == Qt.Key.Key_Left:
            if self._step_index > 0:
                self.go_to_step(self._step_index - 1)
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_offset is not None and (event.buttons() & Qt.MouseButton.LeftButton):
            self.move(event.globalPosition().toPoint() - self._drag_offset)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_offset = None
        super().mouseReleaseEvent(event)
