"""Sorting Flow tab combining TesterBar, SpinePanel, FolderTreePanel, and BridgeOverlay."""

from __future__ import annotations

import copy
from pathlib import Path

from PySide6.QtCore import QRect, Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from download_sorter.architecture import FolderArchitecture, apply_architecture_to_config, architecture_from_config
from download_sorter.config import Config, Rule
from download_sorter.pipeline import TYPE_BUCKETS, PipelineTrace, bucket_target_folders, trace_file
from download_sorter.router import read_zone_url
from download_sorter.service import SorterService
from download_sorter.ui.flow.bridge_overlay import BridgeOverlay, BridgeWire
from download_sorter.ui.flow.folder_tree_panel import FolderTreePanel
from download_sorter.ui.flow.spine_panel import SpinePanel
from download_sorter.ui.flow.tester_bar import TesterBar


class SortingFlowTab(QWidget):
    """Unified Sorting Flow view replacing scattered rules, canvas, and routing settings."""

    config_committed = Signal(object)  # Config
    action_requested = Signal(str, str)  # delegated to MainWindow (dialogs, packs, detox, etc.)
    teach_move_requested = Signal(Path, str)  # (src_path, dest_relpath)

    def __init__(self, service: SorterService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self._focused_folder: str | None = None
        self._peek_wires: list[BridgeWire] = []
        self._last_trace: PipelineTrace | None = None
        self._last_sig: str = ""
        self._pending_arch: FolderArchitecture | None = None
        self._pending_changes = 0

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 4, 0, 0)
        root.setSpacing(10)

        self.tester_bar = TesterBar(self)
        self.tester_bar.test_changed.connect(lambda *_: self.refresh())
        self.tester_bar.exit_focus_requested.connect(self.clear_folder_focus)
        root.addWidget(self.tester_bar)

        # Workspace housing left spine, right folder tree, and transparent Bezier overlay
        self.workspace = QWidget(self)
        self._ws_layout = QHBoxLayout(self.workspace)
        self._ws_layout.setContentsMargins(0, 0, 0, 0)
        self._ws_layout.setSpacing(40)

        self.spine = SpinePanel(self.workspace)
        self.spine.config_mutated.connect(self._on_config_mutated)
        self.spine.rule_port_hovered.connect(self._on_spine_port_hovered)
        self.spine.port_hover_left.connect(self._on_hover_left)
        self.spine.teach_committed.connect(self._on_teach_committed)
        self.spine.teach_cancelled.connect(self.refresh)
        self.spine.action_requested.connect(self._on_spine_action)
        self._ws_layout.addWidget(self.spine, 7)

        self.tree = FolderTreePanel(self.workspace)
        self.tree.folder_clicked.connect(self._on_folder_clicked)
        self.tree.folder_hovered.connect(self._on_folder_hovered)
        self.tree.folder_hover_left.connect(self._on_hover_left)
        self.tree.subfolder_created.connect(self._on_subfolder_created)
        self.tree.file_dropped_on_folder.connect(self._on_file_dropped_on_folder)
        self.tree.context_action.connect(self._on_tree_context_action)
        self.tree.tool_action.connect(self._on_tree_tool_action)
        self.tree.clear_focus_requested.connect(self.clear_folder_focus)
        self._ws_layout.addWidget(self.tree, 5)

        self.overlay = BridgeOverlay(self.workspace)
        self.overlay.raise_()

        # Keep Bezier bridges locked during scrolling
        self.spine.verticalScrollBar().valueChanged.connect(lambda *_: self._redraw_wires(animate=False))
        self.tree.scroll.verticalScrollBar().valueChanged.connect(lambda *_: self._redraw_wires(animate=False))

        self._esc_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        self._esc_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._esc_shortcut.activated.connect(self._on_escape)

        root.addWidget(self.workspace, 1)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._ws_layout.setSpacing(40 if event.size().width() >= 1020 else 26)
        self.overlay.setGeometry(self.workspace.rect())
        self.overlay.raise_()
        QTimer.singleShot(0, lambda: self._redraw_wires(animate=False))

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.overlay.setGeometry(self.workspace.rect())
        self.overlay.raise_()
        QTimer.singleShot(15, lambda: self._redraw_wires(animate=False))

    def set_watch_label(self, label: str) -> None:
        self.tree.set_root_label(label)

    def set_suggestion(self, suggestion: dict | None) -> None:
        self.spine.set_suggestion(suggestion)

    def trace_from_activity(self, filename: str, source_url: str = "") -> None:
        self._focused_folder = None
        self.spine.clear_teach_draft()
        self.tester_bar.set_test_file(filename, source_url)

    def clear_folder_focus(self) -> None:
        self._focused_folder = None
        self.refresh()

    def _on_escape(self) -> None:
        if self.spine.has_teach_draft():
            self.spine.clear_teach_draft()
            self.refresh()
        elif self._focused_folder is not None:
            self.clear_folder_focus()
        elif self.spine.expanded_rule_id() is not None:
            self.spine._expanded_rule_id = None
            self.tree.set_retarget_mode(False)
            self.refresh()

    def _all_folder_choices(self, config: Config) -> list[str]:
        arch = architecture_from_config(config)
        out: list[str] = []
        for node in arch.nodes_in_tier(1):
            out.append(node.name)
            for child in arch.children_of(node.id):
                rel = arch.node_relative_path(child.id).replace("/", "\\")
                if rel not in out:
                    out.append(rel)
                for grand in arch.children_of(child.id):
                    grel = arch.node_relative_path(grand.id).replace("/", "\\")
                    if grel not in out:
                        out.append(grel)
        for r in config.rules:
            dest = r.destination.replace("/", "\\").strip("\\")
            if dest and "{" not in dest and dest not in out:
                out.append(dest)
        return out

    def _count_feeders(self, folder_path: str, config: Config) -> tuple[int, int]:
        norm = folder_path.replace("/", "\\").strip("\\")
        rc = sum(1 for r in config.rules if r.enabled and r.destination.replace("/", "\\").strip("\\") == norm)
        tc = sum(1 for b in TYPE_BUCKETS if norm in bucket_target_folders(b, config))
        return rc, tc

    def refresh(self) -> None:
        cfg = self.service.config
        fname, url, dropped_path = self.tester_bar.current_inputs()
        trace = trace_file(
            fname,
            cfg,
            source_url=url,
            path=dropped_path,
            service=self.service,
        )
        self._last_trace = trace

        choices = self._all_folder_choices(cfg)
        self.tree.set_retarget_mode(self.spine.expanded_rule_id() is not None)

        if self._focused_folder is not None:
            rc, tc = self._count_feeders(self._focused_folder, cfg)
            self.tester_bar.show_folder_focus(self._focused_folder, rc, tc)
        else:
            self.tester_bar.show_trace(trace)

        self.spine.rebuild(
            cfg,
            trace,
            focused_folder=self._focused_folder,
            folder_choices=choices,
        )
        draft_target = self.spine._teach_draft["target_folder"] if self.spine._teach_draft else None
        self.tree.rebuild(
            cfg,
            trace,
            focused_folder=self._focused_folder,
            draft_target=draft_target,
        )

        sig = f"{self._focused_folder or ''}|{draft_target or ''}|{trace.winning_step}:{trace.matched_rule_id}:{trace.matched_bucket_id}:{trace.dest_folder}"
        animate = sig != self._last_sig
        self._last_sig = sig
        QTimer.singleShot(0, lambda: self._redraw_wires(animate=animate))

    def _redraw_wires(self, *, animate: bool = False) -> None:
        self.overlay.setGeometry(self.workspace.rect())
        self.overlay.raise_()
        if not self.isVisible():
            return

        cfg = self.service.config
        wires: list[BridgeWire] = []
        ghost_factor = 0.25 if self._peek_wires else 1.0

        if self._focused_folder is not None:
            target = self.tree.folder_widget(self._focused_folder)
            if target is not None:
                for port in self.spine.ports_for_folder(self._focused_folder, cfg):
                    wires.append(BridgeWire(source=port, target=target, color="#0A84FF", opacity=0.82))
        elif self._last_trace is not None:
            src_port, color = self.spine.port_for_trace(self._last_trace)
            if self.spine.has_teach_draft():
                tgt_key = self.spine._teach_draft["target_folder"]  # type: ignore[index]
            else:
                tgt_key = self._last_trace.dest_folder
            tgt_widget = self.tree.folder_widget(tgt_key) if tgt_key else None
            if src_port is not None and tgt_widget is not None:
                wires.append(
                    BridgeWire(
                        source=src_port,
                        target=tgt_widget,
                        color=color,
                        dashed=self.spine.has_teach_draft(),
                        opacity=0.90 * ghost_factor,
                        animate_pulse=animate and not self._peek_wires,
                    )
                )

        wires.extend(self._peek_wires)
        clip = QRect(0, 0, self.workspace.width(), self.workspace.height())
        self.overlay.set_wires(wires, clip_rect=clip, animate_pulse=animate and not self._peek_wires)

    def _on_config_mutated(self, new_cfg: Config) -> None:
        self.config_committed.emit(new_cfg)
        self.refresh()

    def _on_spine_action(self, action: str, arg: str) -> None:
        if action == "refresh_flow":
            self.refresh()
            return
        if action == "create_rule_for_focus" and self._focused_folder:
            cfg = copy.deepcopy(self.service.config)
            leaf = self._focused_folder.split("\\")[-1]
            new_id = f"rule_{len(cfg.rules) + 1}_{abs(hash(leaf)) % 10000}"
            cfg.rules.insert(
                0,
                Rule(
                    id=new_id,
                    name=f"{leaf} files",
                    destination=self._focused_folder,
                    keywords=[leaf.lower()],
                    enabled=True,
                ),
            )
            self._focused_folder = None
            self.spine._expanded_rule_id = new_id
            self.config_committed.emit(cfg)
            self.refresh()
            return
        self.action_requested.emit(action, arg)

    def _on_folder_clicked(self, folder_path: str) -> None:
        if self.spine.expanded_rule_id() is not None:
            self.spine.set_expanded_rule_dest(folder_path)
            return
        self._focused_folder = None if self._focused_folder == folder_path else folder_path
        self.refresh()

    def _on_spine_port_hovered(self, port_widget: object, dest_folder: str) -> None:
        if not isinstance(port_widget, QWidget):
            return
        norm = dest_folder.replace("/", "\\").strip("\\")
        tgt = self.tree.folder_widget(norm)
        if tgt is not None:
            self._peek_wires = [BridgeWire(source=port_widget, target=tgt, color="#0A84FF", dashed=True, opacity=0.65)]
            self._redraw_wires(animate=False)

    def _on_folder_hovered(self, folder_path: str) -> None:
        if self._focused_folder is not None:
            return
        tgt = self.tree.folder_widget(folder_path)
        if tgt is None:
            return
        ports = self.spine.ports_for_folder(folder_path, self.service.config)
        self._peek_wires = [
            BridgeWire(source=p, target=tgt, color="#0A84FF", dashed=True, opacity=0.58)
            for p in ports
        ]
        self._redraw_wires(animate=False)

    def _on_hover_left(self) -> None:
        if self._peek_wires:
            self._peek_wires = []
            self._redraw_wires(animate=False)

    def _on_subfolder_created(self, parent_path: str, new_name: str) -> None:
        cfg = copy.deepcopy(self.service.config)
        arch = architecture_from_config(cfg)
        parent_id = None
        for node in arch.nodes:
            if arch.node_relative_path(node.id).replace("/", "\\") == parent_path.replace("/", "\\"):
                parent_id = node.id
                break
        if parent_id is not None:
            new_node = arch.add_custom_node(name=new_name, tier=min(3, arch.get_node(parent_id).tier + 1), parent_id=parent_id)
            cfg = apply_architecture_to_config(arch, cfg)
            self._focused_folder = arch.node_relative_path(new_node.id).replace("/", "\\")
            self.config_committed.emit(cfg)
        else:
            # Ensure target folder appears in tree via focus
            self._focused_folder = f"{parent_path}\\{new_name}"
        self.refresh()

    def _on_file_dropped_on_folder(self, path: Path, folder_path: str) -> None:
        url = read_zone_url(path)
        self._focused_folder = None
        self.tester_bar.set_test_file(path.name, url, path=path)
        self.spine.start_teach_draft(path, folder_path, url)
        self.refresh()

    def _on_teach_committed(self, rule: Rule, also_move: bool, source_path: object) -> None:
        cfg = copy.deepcopy(self.service.config)
        cfg.rules.insert(0, rule)
        self.config_committed.emit(cfg)
        if also_move and isinstance(source_path, Path) and source_path.is_file():
            self.teach_move_requested.emit(source_path, rule.destination)
        self.refresh()

    def _on_tree_context_action(self, action: str, folder_path: str) -> None:
        if action == "refresh_tree":
            self.refresh()
            return
        self.action_requested.emit(f"tree:{action}", folder_path)

    def _on_tree_tool_action(self, action: str) -> None:
        if action == "discard_shift":
            self._pending_arch = None
            self._pending_changes = 0
            self.tree.set_pending_changes(0)
            self.refresh()
            return
        self.action_requested.emit(f"tree_tool:{action}", "")
