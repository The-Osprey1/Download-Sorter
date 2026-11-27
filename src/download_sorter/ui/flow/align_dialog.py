"""Dry-run Align Downloads confirmation sheet for Sorting Flow."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from download_sorter.ui.motion import InteractiveRibbonBtn, make_vector_icon
from download_sorter.ui.theme import STYLESHEET


class AlignPreviewDialog(QDialog):
    """Calm dry-run preview sheet shown before aligning loose Downloads files."""

    def __init__(
        self,
        watch_root: Path,
        plan_items: list[tuple[Path, Any]],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Align Downloads with Sorting Flow")
        self.resize(560, 440)
        self.setStyleSheet(STYLESHEET)

        self._watch_root = watch_root
        self._movable: list[tuple[Path, Any]] = [
            (p, dec) for p, dec in plan_items if getattr(dec, "action", "") == "move" and dec.relative_dest is not None
        ]
        self._checks: list[tuple[QCheckBox, Path, Any]] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        # Header
        hdr = QVBoxLayout()
        hdr.setSpacing(3)
        lbl_t = QLabel("Align Downloads with Sorting Flow")
        lbl_t.setStyleSheet("color: #F5F5F7; font-size: 16px; font-weight: 700;")
        hdr.addWidget(lbl_t)

        target_dirs = {str(dec.relative_dest.parent).replace("/", "\\") for _, dec in self._movable}
        new_dirs = [d for d in target_dirs if d and d != "." and not (watch_root / d).exists()]
        new_note = f" · {len(new_dirs)} new folder{'s' if len(new_dirs) != 1 else ''} will be created" if new_dirs else ""
        self.lbl_sub = QLabel(
            f"Ready to organize {len(self._movable)} file{'s' if len(self._movable) != 1 else ''} "
            f"into {len(target_dirs)} folder{'s' if len(target_dirs) != 1 else ''}{new_note}. "
            "Every move can be undone in one click."
            if self._movable
            else "Downloads is already aligned with Sorting Flow."
        )
        self.lbl_sub.setObjectName("MutedText")
        self.lbl_sub.setWordWrap(True)
        hdr.addWidget(self.lbl_sub)
        root.addLayout(hdr)

        # Scrollable diff list
        card = QFrame()
        card.setObjectName("SurfaceCard")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(6, 6, 6, 6)
        cl.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet(
            "QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }"
            "QScrollBar:vertical { background: transparent; width: 5px; margin: 4px 0; }"
            "QScrollBar::handle:vertical { background: rgba(255, 255, 255, 0.14); border-radius: 2px; min-height: 24px; }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical, QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; height: 0px; }"
        )
        host = QWidget()
        hl = QVBoxLayout(host)
        hl.setContentsMargins(4, 2, 4, 2)
        hl.setSpacing(4)

        for path, dec in self._movable:
            row = QFrame()
            row.setStyleSheet(
                "QFrame { background-color: rgba(255, 255, 255, 0.03); "
                "border: 1px solid rgba(255, 255, 255, 0.06); border-radius: 8px; }"
            )
            rl = QHBoxLayout(row)
            rl.setContentsMargins(10, 6, 10, 6)
            rl.setSpacing(8)

            chk = QCheckBox()
            chk.setChecked(True)
            chk.setAccessibleName(f"Include {path.name}")
            chk.toggled.connect(self._sync_button_label)
            rl.addWidget(chk)

            lbl_name = QLabel(path.name)
            lbl_name.setStyleSheet("color: #F5F5F7; font-size: 12.5px; font-weight: 500; border: none; background: transparent;")
            rl.addWidget(lbl_name, 1)

            dest_rel = str(dec.relative_dest.parent).replace("/", "\\").replace("\\", " › ")
            lbl_dest = QLabel(f"→  {dest_rel}")
            lbl_dest.setStyleSheet("color: #60A5FA; font-size: 11.5px; font-weight: 600; border: none; background: transparent;")
            rl.addWidget(lbl_dest)

            hl.addWidget(row)
            self._checks.append((chk, path, dec))

        hl.addStretch(1)
        scroll.setWidget(host)
        cl.addWidget(scroll, 1)
        root.addWidget(card, 1)

        # Footer controls
        foot = QHBoxLayout()
        foot.setSpacing(8)
        btn_toggle = QPushButton("Toggle All")
        btn_toggle.setObjectName("GhostButton")
        btn_toggle.clicked.connect(self._toggle_all)
        foot.addWidget(btn_toggle)
        foot.addStretch(1)

        btn_cancel = QPushButton("Cancel")
        btn_cancel.setObjectName("GhostButton")
        btn_cancel.clicked.connect(self.reject)
        foot.addWidget(btn_cancel)

        self.btn_confirm = InteractiveRibbonBtn("Move Files", make_vector_icon("check", "#FFFFFF", 13))
        self.btn_confirm.setObjectName("PrimaryButton")
        self.btn_confirm.clicked.connect(self.accept)
        foot.addWidget(self.btn_confirm)
        root.addLayout(foot)

        self._sync_button_label()

    def _toggle_all(self) -> None:
        all_checked = all(chk.isChecked() for chk, _, _ in self._checks)
        for chk, _, _ in self._checks:
            chk.setChecked(not all_checked)

    def _sync_button_label(self) -> None:
        selected = sum(1 for chk, _, _ in self._checks if chk.isChecked())
        noun = "File" if selected == 1 else "Files"
        self.btn_confirm.setText(f"Move {selected} {noun}")
        self.btn_confirm.setEnabled(selected > 0)

    def selected_items(self) -> list[tuple[Path, Any]]:
        """Return only the ``(path, decision)`` pairs checked by the user."""
        return [(path, dec) for chk, path, dec in self._checks if chk.isChecked()]
