"""Top Live File X-Ray Tester Bar for Sorting Flow."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from download_sorter.pipeline import PipelineTrace
from download_sorter.router import read_zone_url
from download_sorter.ui.motion import InteractiveRibbonBtn, make_vector_icon
from download_sorter.ui.text_scale import scaled_px

_EXAMPLES: tuple[tuple[str, str, str], ...] = (
    ("Invoice PDF (Stripe)", "March_Invoice_2026.pdf", "https://stripe.com/receipts"),
    ("3D Print Project (.3mf)", "benchy_plate1.3mf", "https://makerworld.com/en/models/123"),
    ("Vacation Photo (.jpg)", "Coastline_Trip.jpg", ""),
    ("Unknown Extension (AI)", "w2_tax_scan.bin", "https://irs.gov/forms"),
    ("In-Progress Download", "Ubuntu_26.iso.crdownload", "https://ubuntu.com/download"),
)


class TesterBar(QFrame):
    """Roomy filename + source website tester with OS drag-and-drop and plain-English verdict."""

    test_changed = Signal(str, str)
    exit_focus_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SurfaceCard")
        self.setAcceptDrops(True)
        self._suppress_signals = False
        self._last_dropped_path: Path | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        top_row = QHBoxLayout()
        top_row.setSpacing(8)

        input_shell = QFrame()
        input_shell.setObjectName("TesterInputShell")
        input_shell.setStyleSheet(
            "#TesterInputShell { background-color: #161618; border: 1px solid #2C2C2E; border-radius: 10px; }"
            "#TesterInputShell:focus-within { border-color: #0A84FF; }"
        )
        in_row = QHBoxLayout(input_shell)
        in_row.setContentsMargins(10, 4, 6, 4)
        in_row.setSpacing(8)

        self.edit_filename = QLineEdit("March_Invoice_2026.pdf")
        self.edit_filename.setPlaceholderText("Drop or type any file name to trace (nothing moves)...")
        self.edit_filename.setStyleSheet("border: none; background: transparent; padding: 3px 0;")
        self.edit_filename.textChanged.connect(self._on_inputs_edited)
        in_row.addWidget(self.edit_filename, 3)

        site_pill = QFrame()
        site_pill.setStyleSheet(
            "background-color: #1C1C1E; border: 1px solid #2C2C2E; border-radius: 7px;"
        )
        site_row = QHBoxLayout(site_pill)
        site_row.setContentsMargins(8, 2, 8, 2)
        site_row.setSpacing(5)
        lbl_globe = QLabel("From:")
        lbl_globe.setStyleSheet("color: #8E8E93; font-size: 11px; border: none;")
        self.edit_url = QLineEdit("stripe.com")
        self.edit_url.setPlaceholderText("source site")
        self.edit_url.setMinimumWidth(scaled_px(130))
        self.edit_url.setMaximumWidth(scaled_px(190))
        self.edit_url.setStyleSheet("border: none; background: transparent; color: #AEAEB2; font-size: 12px;")
        self.edit_url.textChanged.connect(self._on_inputs_edited)
        site_row.addWidget(lbl_globe)
        site_row.addWidget(self.edit_url)
        in_row.addWidget(site_pill)

        top_row.addWidget(input_shell, 1)

        self.btn_examples = InteractiveRibbonBtn("Examples ▾", make_vector_icon("sparkle", "#AEAEB2", 13))
        self.btn_examples.setObjectName("GhostButton")
        self.btn_examples.setToolTip("Load a sample file to see how Sorting Flow handles it")
        self.menu_examples = QMenu(self.btn_examples)
        for label, fname, url in _EXAMPLES:
            act = self.menu_examples.addAction(f"{label}  —  {fname}")
            act.triggered.connect(lambda _=False, f=fname, u=url: self.set_test_file(f, u))
        self.btn_examples.clicked.connect(
            lambda: self.menu_examples.exec(
                self.btn_examples.mapToGlobal(self.btn_examples.rect().bottomLeft())
            )
        )
        top_row.addWidget(self.btn_examples)

        root.addLayout(top_row)

        # Verdict / Folder-Focus Strip
        self.verdict_frame = QFrame()
        self.verdict_frame.setObjectName("VerdictStrip")
        self.verdict_frame.setStyleSheet(
            "#VerdictStrip { background-color: rgba(10, 132, 255, 0.08); border-radius: 9px; }"
        )
        v_row = QHBoxLayout(self.verdict_frame)
        v_row.setContentsMargins(12, 7, 10, 7)
        v_row.setSpacing(8)

        self.lbl_dot = QLabel("●")
        self.lbl_dot.setStyleSheet("color: #0A84FF; font-size: 10px;")
        self.lbl_verdict = QLabel("")
        self.lbl_verdict.setStyleSheet("color: #F5F5F7; font-size: 12.5px;")
        self.lbl_verdict.setWordWrap(True)
        self.lbl_step_tag = QLabel("")
        self.lbl_step_tag.setStyleSheet("color: #8E8E93; font-size: 11.5px; font-weight: 600;")

        self.btn_back_trace = QPushButton("Back to trace · Esc")
        self.btn_back_trace.setVisible(False)
        self.btn_back_trace.setStyleSheet(
            "padding: 3px 9px; font-size: 11.5px; border-radius: 6px; "
            "background-color: #1C1C1E; border: 1px solid #3A3A3E; color: #F5F5F7;"
        )
        self.btn_back_trace.clicked.connect(self.exit_focus_requested.emit)

        v_row.addWidget(self.lbl_dot)
        v_row.addWidget(self.lbl_verdict, 1)
        v_row.addWidget(self.lbl_step_tag)
        v_row.addWidget(self.btn_back_trace)
        root.addWidget(self.verdict_frame)

    def current_inputs(self) -> tuple[str, str, Path | None]:
        return (
            self.edit_filename.text().strip(),
            self.edit_url.text().strip(),
            self._last_dropped_path,
        )

    def set_test_file(self, filename: str, source_url: str = "", *, path: Path | None = None) -> None:
        self._suppress_signals = True
        try:
            self._last_dropped_path = path
            self.edit_filename.setText(filename)
            self.edit_url.setText(source_url)
        finally:
            self._suppress_signals = False
        self.test_changed.emit(filename.strip(), source_url.strip())

    def _on_inputs_edited(self) -> None:
        if self._suppress_signals:
            return
        self._last_dropped_path = None
        self.test_changed.emit(self.edit_filename.text().strip(), self.edit_url.text().strip())

    def show_trace(self, trace: PipelineTrace) -> None:
        self.btn_back_trace.setVisible(False)
        self.lbl_step_tag.setVisible(trace.winning_step > 0)
        if trace.outcome in ("hold", "ignore"):
            dot_col = "#818CF8"
            bg = "rgba(129, 140, 248, 0.10)"
        else:
            dot_col = "#0A84FF"
            bg = "rgba(10, 132, 255, 0.08)"
        self.verdict_frame.setStyleSheet(f"#VerdictStrip {{ background-color: {bg}; border-radius: 9px; }}")
        self.lbl_dot.setStyleSheet(f"color: {dot_col}; font-size: 10px;")
        self.lbl_verdict.setText(trace.human_reason)
        self.lbl_step_tag.setText(f"Step {trace.winning_step} of 3" if trace.winning_step else "")

    def show_folder_focus(self, folder_path: str, rule_count: int, type_count: int) -> None:
        crumb = folder_path.replace("\\", " › ")
        if rule_count == 0 and type_count == 0:
            summary = "Nothing routes here yet"
        else:
            summary = f"{rule_count} rule(s), {type_count} file type group(s) feed here"
        self.verdict_frame.setStyleSheet(
            "#VerdictStrip { background-color: rgba(10, 132, 255, 0.08); border-radius: 9px; }"
        )
        self.lbl_dot.setStyleSheet("color: #0A84FF; font-size: 10px;")
        self.lbl_verdict.setText(f"Inspecting Downloads › {crumb}  ·  {summary}")
        self.lbl_step_tag.setVisible(False)
        self.btn_back_trace.setVisible(True)

    # OS File Drag-and-Drop (Read-only trace, reads NTFS Zone.Identifier)
    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            urls = [u for u in event.mimeData().urls() if u.isLocalFile()]
            if urls:
                event.acceptProposedAction()
                self.setStyleSheet("QFrame#SurfaceCard { border: 1px dashed #0A84FF; }")
                return
        super().dragEnterEvent(event)

    def dragLeaveEvent(self, event) -> None:  # noqa: N802
        self.setStyleSheet("")
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802
        self.setStyleSheet("")
        if event.mimeData().hasUrls():
            for u in event.mimeData().urls():
                if u.isLocalFile():
                    p = Path(u.toLocalFile())
                    if p.is_file():
                        zone_url = read_zone_url(p)
                        self.set_test_file(p.name, zone_url, path=p)
                        event.acceptProposedAction()
                        return
        super().dropEvent(event)
