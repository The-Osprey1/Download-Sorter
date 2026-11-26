"""Sorting Flow tab combining TesterBar, SpinePanel, FolderTreePanel, and BridgeOverlay."""

from __future__ import annotations

import copy
import os
from pathlib import Path
import threading

from PySide6.QtCore import QFileSystemWatcher, QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from download_sorter.architecture import FolderArchitecture, apply_architecture_to_config, architecture_from_config
from download_sorter.config import Config, Rule
from download_sorter.pipeline import TYPE_BUCKETS, PipelineTrace, bucket_target_folders, trace_file
from download_sorter.router import read_zone_url
from download_sorter.service import SorterService
from download_sorter.ui.flow.bridge_overlay import BridgeOverlay, BridgeWire
from download_sorter.ui.flow.folder_tree_panel import FolderTreePanel
from download_sorter.ui.flow.spine_panel import MIME_BUCKET_ID, MIME_RULE_ID, SpinePanel
from download_sorter.ui.flow.tester_bar import TesterBar


class _FlowWorkspace(QWidget):
    """Workspace container that tracks cursor position during drag-and-drop for live Bezier wires."""

    drag_cursor_moved = Signal(QPoint)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        mime = event.mimeData()
        if mime.hasFormat(MIME_RULE_ID) or mime.hasFormat(MIME_BUCKET_ID):
            event.acceptProposedAction()
            self.drag_cursor_moved.emit(self.mapToGlobal(event.position().toPoint()))
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        mime = event.mimeData()
        if mime.hasFormat(MIME_RULE_ID) or mime.hasFormat(MIME_BUCKET_ID):
            event.acceptProposedAction()
            self.drag_cursor_moved.emit(self.mapToGlobal(event.position().toPoint()))
            return
        super().dragMoveEvent(event)


class SortingFlowTab(QWidget):
    """Unified Sorting Flow view replacing scattered rules, canvas, and routing settings."""

    config_committed = Signal(object)  # Config
    action_requested = Signal(str, str)  # delegated to MainWindow (dialogs, packs, detox, etc.)
    teach_move_requested = Signal(Path, str)  # (src_path, dest_relpath)
    _disk_scan_ready = Signal(dict)

    def __init__(self, service: SorterService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self._focused_folder: str | None = None
        self._peek_wires: list[BridgeWire] = []
        self._active_drag_source: QWidget | None = None
        self._active_drag_color: str = "#0A84FF"
        self._last_trace: PipelineTrace | None = None
        self._last_sig: str = ""
        self._pending_arch: FolderArchitecture | None = None
        self._pending_cfg: Config | None = None
        self._pending_folder_renames: list[tuple[str, str]] = []
        self._pending_changes = 0
        self._disk_state: dict = {
            "scanned": False,
            "loose_count": 0,
            "existing_folders": {},
            "custom_top_folders": [],
        }
        self._disk_scan_in_flight = False
        self._disk_scan_ready.connect(self._on_disk_scan_ready)
        self._fs_watcher = QFileSystemWatcher(self)
        self._fs_watcher.directoryChanged.connect(lambda _p: self._fs_debounce.start())
        self._fs_debounce = QTimer(self)
        self._fs_debounce.setSingleShot(True)
        self._fs_debounce.setInterval(350)
        self._fs_debounce.timeout.connect(self._trigger_disk_scan)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 4, 0, 0)
        root.setSpacing(10)

        self.tester_bar = TesterBar(self)
        self.tester_bar.test_changed.connect(lambda *_: self.refresh())
        self.tester_bar.exit_focus_requested.connect(self.clear_folder_focus)
        root.addWidget(self.tester_bar)

        # Workspace housing left spine, right folder tree, and transparent Bezier overlay
        self.workspace = _FlowWorkspace(self)
        self.workspace.drag_cursor_moved.connect(self._on_workspace_drag_cursor)
        self._ws_layout = QHBoxLayout(self.workspace)
        self._ws_layout.setContentsMargins(0, 0, 0, 0)
        self._ws_layout.setSpacing(40)

        self.spine = SpinePanel(self.workspace)
        self.spine.config_mutated.connect(self._on_config_mutated)
        self.spine.rule_port_hovered.connect(self._on_spine_port_hovered)
        self.spine.port_hover_left.connect(self._on_hover_left)
        self.spine.flow_drag_started.connect(self._on_flow_drag_started)
        self.spine.flow_drag_finished.connect(self._on_flow_drag_finished)
        self.spine.bucket_routed.connect(self.retarget_bucket_to_folder)
        self.spine.bucket_reset.connect(self.reset_bucket_route)
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
        self.tree.rule_dropped_on_folder.connect(self.retarget_rule_to_folder)
        self.tree.bucket_dropped_on_folder.connect(self.retarget_bucket_to_folder)
        self.tree.folder_dropped_on_folder.connect(self.move_folder_in_tree)
        self.tree.drag_hover_folder.connect(self._on_drag_hover_folder)
        self.tree.drag_hover_left.connect(self._on_drag_hover_left)
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
        QTimer.singleShot(0, self.seed_initial_trace)
        QTimer.singleShot(30, self._trigger_disk_scan)

    def _sync_fs_watcher(self) -> None:
        try:
            watch_str = str(self.service.watch_root)
            if self.service.watch_root.is_dir() and watch_str not in self._fs_watcher.directories():
                if self._fs_watcher.directories():
                    self._fs_watcher.removePaths(self._fs_watcher.directories())
                self._fs_watcher.addPath(watch_str)
        except OSError:
            pass

    def _trigger_disk_scan(self) -> None:
        if self._disk_scan_in_flight:
            return
        self._sync_fs_watcher()
        root = self.service.watch_root
        self._disk_scan_in_flight = True

        def _worker() -> None:
            ignored_exts = {".crdownload", ".part", ".tmp", ".download", ".opdownload", ".filepart", ".!qb"}
            loose_count = 0
            existing: dict[str, int] = {}
            custom_tops: list[tuple[str, int]] = []
            try:
                if root.is_dir():
                    with os.scandir(root) as it:
                        for entry in it:
                            name = entry.name
                            if name.startswith(".") or name.startswith("~$"):
                                continue
                            if entry.is_file(follow_symlinks=False):
                                ext = Path(name).suffix.lower()
                                if ext not in ignored_exts:
                                    loose_count += 1
                            elif entry.is_dir(follow_symlinks=False) and not name.startswith("_"):
                                top_count = 0
                                try:
                                    with os.scandir(entry.path) as sub_it:
                                        for sub in sub_it:
                                            if sub.name.startswith("."):
                                                continue
                                            if sub.is_file(follow_symlinks=False):
                                                top_count += 1
                                            elif sub.is_dir(follow_symlinks=False):
                                                sub_count = 0
                                                try:
                                                    with os.scandir(sub.path) as leaf_it:
                                                        for lf in leaf_it:
                                                            if lf.is_file(follow_symlinks=False):
                                                                sub_count += 1
                                                except OSError:
                                                    pass
                                                existing[f"{name}\\{sub.name}"] = sub_count
                                                top_count += sub_count
                                except OSError:
                                    pass
                                existing[name] = top_count
                                custom_tops.append((name, top_count))
            except OSError:
                pass
            custom_tops.sort(key=lambda item: item[0].lower())
            self._disk_scan_ready.emit(
                {
                    "scanned": True,
                    "loose_count": loose_count,
                    "existing_folders": existing,
                    "custom_top_folders": custom_tops,
                }
            )

        threading.Thread(target=_worker, daemon=True).start()

    def _on_disk_scan_ready(self, state: dict) -> None:
        self._disk_scan_in_flight = False
        self._disk_state = state
        if self._active_drag_source is None:
            self.refresh()

    def seed_initial_trace(self) -> None:
        """Seed the Tester Bar once with the newest real non-temp file in watch_root, if any."""
        try:
            root = self.service.watch_root
            if root.is_dir():
                ignored_exts = {".crdownload", ".part", ".tmp", ".download", ".opdownload"}
                candidates = [
                    p
                    for p in root.iterdir()
                    if p.is_file() and not p.name.startswith(".") and p.suffix.lower() not in ignored_exts
                ]
                if candidates:
                    newest = max(candidates, key=lambda p: p.stat().st_mtime)
                    url = read_zone_url(newest)
                    self.tester_bar.set_test_file(newest.name, url, path=newest)
                    return
        except OSError:
            pass
        if self.service.config.rules:
            self.tester_bar.set_test_file("March_Invoice_2026.pdf", "stripe.com")
        else:
            self.tester_bar.set_test_file("March_Invoice_2026.pdf", "")

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
        arch = self._pending_arch.clone() if self._pending_arch is not None else architecture_from_config(config)
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
        for cname, _cnt in (self._disk_state.get("custom_top_folders") or []):
            if cname and cname not in out:
                out.append(cname)
        return out

    def _count_feeders(self, folder_path: str, config: Config) -> tuple[int, int]:
        norm = folder_path.replace("/", "\\").strip("\\")
        rc = sum(1 for r in config.rules if r.enabled and r.destination.replace("/", "\\").strip("\\") == norm)
        tc = sum(1 for b in TYPE_BUCKETS if norm in bucket_target_folders(b, config))
        return rc, tc

    def refresh(self) -> None:
        # Always drop transient hover wires BEFORE rebuilding widgets via deleteLater()
        self._peek_wires = []
        self.overlay.clear_wires()

        cfg = self._pending_cfg if self._pending_cfg is not None else self.service.config
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

        show_step1 = bool(cfg.hold_3mf_enabled or cfg.project_grouping_enabled or trace.outcome == "hold")
        if self._focused_folder is not None:
            rc, tc = self._count_feeders(self._focused_folder, cfg)
            self.tester_bar.show_folder_focus(self._focused_folder, rc, tc)
        else:
            self.tester_bar.show_trace(trace, total_steps=3 if show_step1 else 2)

        self.spine.rebuild(
            cfg,
            trace,
            focused_folder=self._focused_folder,
            folder_choices=choices,
            disk_state=self._disk_state,
        )
        draft_target = self.spine._teach_draft["target_folder"] if self.spine._teach_draft else None
        self.tree.rebuild(
            cfg,
            trace,
            focused_folder=self._focused_folder,
            draft_target=draft_target,
            disk_state=self._disk_state,
            override_arch=self._pending_arch,
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

        cfg = self._pending_cfg if self._pending_cfg is not None else self.service.config
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

    # ------------------------------------------------------------------
    # Interactive Drag-and-Drop Flow Adjustments
    # ------------------------------------------------------------------
    def _on_flow_drag_started(self, port_widget: object, color: str) -> None:
        self._peek_wires = []
        self._active_drag_source = port_widget if isinstance(port_widget, QWidget) else None
        self._active_drag_color = color
        self.overlay.set_drag_wire(self._active_drag_source, None, color=color)

    def _on_flow_drag_finished(self) -> None:
        self._active_drag_source = None
        self.overlay.clear_drag_wire()
        QTimer.singleShot(0, lambda: self._redraw_wires(animate=False))

    def _on_workspace_drag_cursor(self, global_pos: QPoint) -> None:
        if self._active_drag_source is not None:
            self.overlay.set_drag_wire(
                self._active_drag_source,
                global_pos,
                snap_target=None,
                color=self._active_drag_color,
            )

    def _on_drag_hover_folder(self, folder_widget: object, global_pos: object) -> None:
        if self._active_drag_source is not None and isinstance(folder_widget, QWidget):
            gp = global_pos if isinstance(global_pos, QPoint) else None
            self.overlay.set_drag_wire(
                self._active_drag_source,
                gp,
                snap_target=folder_widget,
                color=self._active_drag_color,
            )

    def _on_drag_hover_left(self) -> None:
        if self._active_drag_source is not None:
            self.overlay.set_drag_wire(
                self._active_drag_source,
                self.overlay._drag_cursor_global,
                snap_target=None,
                color=self._active_drag_color,
            )

    def reorder_rule(self, dragged_id: str, target_id: str, *, insert_after: bool = False) -> bool:
        """Move ``dragged_id`` before or after ``target_id`` in rule priority order."""
        cfg = copy.deepcopy(self.service.config)
        src_idx = next((i for i, r in enumerate(cfg.rules) if r.id == dragged_id), -1)
        dst_idx = next((i for i, r in enumerate(cfg.rules) if r.id == target_id), -1)
        if src_idx < 0 or dst_idx < 0 or src_idx == dst_idx:
            return False
        moved = cfg.rules.pop(src_idx)
        new_dst_idx = next((i for i, r in enumerate(cfg.rules) if r.id == target_id), dst_idx)
        pos = max(0, min(len(cfg.rules), new_dst_idx + (1 if insert_after else 0)))
        cfg.rules.insert(pos, moved)
        self.config_committed.emit(cfg)
        self.refresh()
        return True

    def retarget_rule_to_folder(self, rule_id: str, folder_path: str) -> bool:
        """Re-route an existing custom rule onto ``folder_path`` via drag-and-drop."""
        norm = folder_path.replace("/", "\\").strip("\\")
        if not rule_id or not norm:
            return False
        cfg = copy.deepcopy(self.service.config)
        matched_name = ""
        for r in cfg.rules:
            if r.id == rule_id:
                if r.destination.replace("/", "\\").strip("\\") == norm:
                    return False
                r.destination = norm
                matched_name = r.name
                break
        if not matched_name:
            return False
        if "\\" in norm:
            self.tree._expanded_parents.add(norm.split("\\", 1)[0])
        self._focused_folder = None
        self.config_committed.emit(cfg)
        self.refresh()
        self.action_requested.emit(
            "flow_notice",
            f"Routed rule '{matched_name}' → {norm.replace(chr(92), ' › ')}",
        )
        return True

    def retarget_bucket_to_folder(self, bucket_id: str, folder_path: str) -> bool:
        """Re-route a Step 3 file-type bucket onto ``folder_path`` via drag-and-drop."""
        norm = folder_path.replace("/", "\\").strip("\\")
        bucket = next((b for b in TYPE_BUCKETS if b.id == bucket_id), None)
        if bucket is None or not norm:
            return False

        cfg = copy.deepcopy(self.service.config)
        for ext in bucket.extensions:
            cfg.category_map[ext] = norm
            if "\\" in norm:
                # Dropping onto a specific subfolder routes directly into that subfolder
                cfg.type_subfolders.pop(ext, None)

        if cfg.architecture:
            arch = architecture_from_config(cfg)
            for node in arch.nodes:
                node.extensions = [e for e in node.extensions if e not in bucket.extensions]
            arch.ensure_path_chain(norm, extensions=list(bucket.extensions), is_custom=True)
            cfg.architecture = arch.to_dict()

        if "\\" in norm:
            self.tree._expanded_parents.add(norm.split("\\", 1)[0])
        self._focused_folder = None
        self.config_committed.emit(cfg)
        self.refresh()
        self.action_requested.emit(
            "flow_notice",
            f"Routed {bucket.name} → {norm.replace(chr(92), ' › ')}",
        )
        return True

    def reset_bucket_route(self, bucket_id: str) -> bool:
        """Restore default category routing for ``bucket_id``."""
        bucket = next((b for b in TYPE_BUCKETS if b.id == bucket_id), None)
        if bucket is None:
            return False
        defaults = Config.new_install()
        cfg = copy.deepcopy(self.service.config)
        for ext in bucket.extensions:
            if ext in defaults.category_map:
                cfg.category_map[ext] = defaults.category_map[ext]
            if ext in defaults.type_subfolders:
                cfg.type_subfolders[ext] = defaults.type_subfolders[ext]
        if cfg.architecture:
            arch = architecture_from_config(cfg)
            for node in arch.nodes:
                node.extensions = [e for e in node.extensions if e not in bucket.extensions]
            arch.ensure_path_chain(bucket.default_folder, extensions=list(bucket.extensions), is_custom=False)
            cfg.architecture = arch.to_dict()
        self.config_committed.emit(cfg)
        self.refresh()
        self.action_requested.emit("flow_notice", f"Reset {bucket.name} routing to {bucket.default_folder}")
        return True

    @staticmethod
    def _rewrite_prefix(path_val: str, old_prefix: str, new_prefix: str) -> str:
        norm = path_val.replace("/", "\\").strip("\\")
        old_low = old_prefix.lower()
        if norm.lower() == old_low:
            return new_prefix
        if norm.lower().startswith(old_low + "\\"):
            return new_prefix + norm[len(old_prefix) :]
        return path_val

    def move_folder_in_tree(self, src_folder: str, target_parent_folder: str) -> bool:
        """Nest or promote a folder in the Downloads tree via drag-and-drop."""
        src = src_folder.replace("/", "\\").strip("\\")
        dst_parent = target_parent_folder.replace("/", "\\").strip("\\")
        if not src:
            return False
        if dst_parent and (src.lower() == dst_parent.lower() or dst_parent.lower().startswith(src.lower() + "\\")):
            return False

        leaf = src.split("\\")[-1]
        new_rel = f"{dst_parent}\\{leaf}" if dst_parent else leaf
        if new_rel.lower() == src.lower():
            return False

        base_cfg = copy.deepcopy(self._pending_cfg if self._pending_cfg is not None else self.service.config)
        arch = self._pending_arch.clone() if self._pending_arch is not None else architecture_from_config(base_cfg)

        src_node = None
        dst_node = None
        for n in arch.nodes:
            rel = arch.node_relative_path(n.id).replace("/", "\\").strip("\\")
            if rel.lower() == src.lower():
                src_node = n
            if dst_parent and rel.lower() == dst_parent.lower():
                dst_node = n

        if src_node is None:
            src_node = arch.ensure_path_chain(src, is_custom=True)
        if dst_parent and dst_node is None:
            dst_node = arch.ensure_path_chain(dst_parent, is_custom=True)
        if src_node is None:
            return False
        if not arch.reparent_node(src_node.id, dst_node.id if dst_node is not None else None):
            return False

        updated_cfg = apply_architecture_to_config(arch, base_cfg)
        for r in updated_cfg.rules:
            r.destination = self._rewrite_prefix(r.destination, src, new_rel)
        for ext, cat in list(updated_cfg.category_map.items()):
            updated_cfg.category_map[ext] = self._rewrite_prefix(cat, src, new_rel)

        if dst_parent:
            self.tree._expanded_parents.add(dst_parent.split("\\", 1)[0])

        src_disk = self.service.watch_root / src
        has_files_on_disk = False
        try:
            if src_disk.is_dir():
                has_files_on_disk = any(True for _ in src_disk.rglob("*") if _.is_file())
        except OSError:
            has_files_on_disk = False

        if has_files_on_disk:
            self._pending_arch = arch
            self._pending_cfg = updated_cfg
            self._pending_folder_renames.append((src, new_rel))
            self._pending_changes += 1
            dest_label = dst_parent.replace("\\", " › ") if dst_parent else "Downloads (Top-level)"
            self.tree.set_pending_changes(self._pending_changes, f"Move '{leaf}' → {dest_label}")
            self.refresh()
        else:
            # Pure virtual or empty directory: apply cleanly right away
            if src_disk.is_dir():
                try:
                    src_disk.rmdir()
                except OSError:
                    pass
            self._pending_arch = None
            self._pending_cfg = None
            self._pending_folder_renames.clear()
            self._pending_changes = 0
            self.tree.set_pending_changes(0)
            self.config_committed.emit(updated_cfg)
            self.refresh()
            dest_label = new_rel.replace("\\", " › ")
            self.action_requested.emit("flow_notice", f"Moved folder '{leaf}' → {dest_label}")
        return True

    # ------------------------------------------------------------------
    # Standard Flow Event Handlers
    # ------------------------------------------------------------------
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
        if self._active_drag_source is not None or not isinstance(port_widget, QWidget):
            return
        norm = dest_folder.replace("/", "\\").strip("\\")
        tgt = self.tree.folder_widget(norm)
        if tgt is not None:
            self._peek_wires = [BridgeWire(source=port_widget, target=tgt, color="#0A84FF", dashed=True, opacity=0.65)]
            self._redraw_wires(animate=False)

    def _on_folder_hovered(self, folder_path: str) -> None:
        if self._active_drag_source is not None or self._focused_folder is not None:
            return
        tgt = self.tree.folder_widget(folder_path)
        if tgt is None:
            return
        cfg = self._pending_cfg if self._pending_cfg is not None else self.service.config
        ports = self.spine.ports_for_folder(folder_path, cfg)
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
            new_node = arch.add_custom_node(
                name=new_name,
                tier=min(3, arch.get_node(parent_id).tier + 1),
                parent_id=parent_id,
            )
            cfg = apply_architecture_to_config(arch, cfg)
            self._focused_folder = arch.node_relative_path(new_node.id).replace("/", "\\")
            self.config_committed.emit(cfg)
        else:
            self._focused_folder = f"{parent_path}\\{new_name}"
        self.refresh()

    def _on_file_dropped_on_folder(self, path: Path, folder_path: str) -> None:
        url = read_zone_url(path)
        self._focused_folder = None
        self.tester_bar.set_test_file(path.name, url, path=path)
        self.spine.start_teach_draft(path, folder_path, url)
        self.refresh()

    def _on_teach_committed(self, rule: Rule, arg2: object, arg3: object = None) -> None:
        # Support both SpinePanel signal (rule, also_move: bool, source_path: Path)
        # and direct test invocation (rule, source_path: Path, also_move: bool)
        if isinstance(arg2, Path):
            source_path: object = arg2
            also_move = bool(arg3)
        else:
            also_move = bool(arg2)
            source_path = arg3
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

    def discard_pending_folder_shift(self) -> None:
        self._pending_arch = None
        self._pending_cfg = None
        self._pending_folder_renames.clear()
        self._pending_changes = 0
        self.tree.set_pending_changes(0)
        self.refresh()

    def _on_tree_tool_action(self, action: str) -> None:
        if action == "discard_shift":
            self.discard_pending_folder_shift()
            return
        if action == "apply_shift":
            self.action_requested.emit("tree_tool:apply_shift", "")
            return
        self.action_requested.emit(f"tree_tool:{action}", "")
