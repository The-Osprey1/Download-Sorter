"""Tests for the one-screen Folders designer screen."""

from __future__ import annotations

from download_sorter import folder_designer as fd
from download_sorter.config import Config, Rule, save_config
from download_sorter.paths import AppPaths
from download_sorter.service import SorterService


def _window(app_paths: AppPaths, v3_qapp, cfg: Config | None = None):  # noqa: ANN001, ANN202
    from download_sorter.ui.main_window import MainWindow

    cfg = cfg or Config.with_starter_defaults()
    cfg.settle_seconds = 0.0
    save_config(cfg, app_paths)
    svc = SorterService(app_paths)
    win = MainWindow(svc)
    win.show()
    v3_qapp.processEvents()
    return win, svc


def _tree_paths(tab) -> list[str]:  # noqa: ANN001
    from PySide6.QtWidgets import QTreeWidgetItemIterator

    from download_sorter.ui.folders.folders_tab import ROLE_PATH

    out = []
    it = QTreeWidgetItemIterator(tab.tree)
    while it.value() is not None:
        out.append(str(it.value().data(0, ROLE_PATH)))
        it += 1
    return out


def test_folders_is_the_first_tab_and_lists_folders(app_paths: AppPaths, v3_qapp) -> None:  # noqa: ANN001
    win, svc = _window(app_paths, v3_qapp)
    try:
        assert win.tabs.widget(0) is win.folders_tab
        assert win.tabs.tabText(0) == "Folders"
        paths = _tree_paths(win.folders_tab)
        assert "Documents" in paths
        assert win.flow_tab.isHidden()
    finally:
        win.close()
        svc.stop(timeout=1.0)


def test_selecting_a_folder_shows_its_sentence_and_preview(app_paths: AppPaths, v3_qapp) -> None:  # noqa: ANN001
    (app_paths.downloads_dir / "Lease_2026.pdf").write_bytes(b"x")
    (app_paths.downloads_dir / "photo.png").write_bytes(b"x")
    win, svc = _window(app_paths, v3_qapp)
    tab = win.folders_tab
    try:
        tab.scan_disk()
        v3_qapp.processEvents()
        tab.select_path("Documents")
        v3_qapp.processEvents()
        page = tab.page_folder
        assert page.path == "Documents"
        assert page.title.text() == "Documents"
        tokens = [page.sentence.itemAt(i).widget() for i in range(page.sentence.count())]
        texts = [w.text() for w in tokens if w is not None]
        assert "Put" in texts and any("Documents" in t for t in texts)
        landing, _clash = tab.preview_for("Documents")
        assert "Lease_2026.pdf" in landing
        assert "photo.png" not in landing
        assert "Tidy 2 files" == tab.btn_tidy.text()
        assert len(tab.planned_moves()) == 2
    finally:
        win.close()
        svc.stop(timeout=1.0)


def test_edits_commit_and_undo(app_paths: AppPaths, v3_qapp) -> None:  # noqa: ANN001
    win, svc = _window(app_paths, v3_qapp)
    tab = win.folders_tab
    try:
        assert tab.apply(lambda cfg: fd.add_folder(cfg, "School"))
        v3_qapp.processEvents()
        assert "School" in _tree_paths(tab)
        assert tab.selected_path() == "School"
        # Words make it automatic
        tab.page_folder._set(fd.FolderCues(keywords=["syllabus"]))
        v3_qapp.processEvents()
        assert fd.folder_cues(svc.config, "School").keywords == ["syllabus"]
        # A refused edit leaves settings alone and says why
        before = svc.config.to_dict()
        assert tab.apply(lambda cfg: fd.add_folder(cfg, "school")) is False
        assert svc.config.to_dict() == before
        assert "already has" in win.lbl_footer.text()
        # Undo from the last message puts the previous settings back
        tab.apply(lambda cfg: fd.rename_folder(cfg, "School", "Classes"))
        v3_qapp.processEvents()
        assert "Classes" in _tree_paths(tab)
        win.banner._run_action()
        v3_qapp.processEvents()
        assert "School" in _tree_paths(tab)
    finally:
        win.close()
        svc.stop(timeout=1.0)


def test_rename_moves_files_on_disk_and_undo_puts_them_back(app_paths: AppPaths, v3_qapp) -> None:  # noqa: ANN001
    cfg = Config.with_starter_defaults()
    cfg = fd.add_folder(cfg, "Work").config
    cfg.rules.insert(0, Rule(id="u1", name="Work", destination="Work", keywords=["memo"]))
    work = app_paths.downloads_dir / "Work"
    work.mkdir()
    (work / "memo.txt").write_text("hi", encoding="utf-8")
    win, svc = _window(app_paths, v3_qapp, cfg)
    tab = win.folders_tab
    try:
        tab.apply(lambda c: fd.rename_folder(c, "Work", "Jobs"))
        v3_qapp.processEvents()
        assert (app_paths.downloads_dir / "Jobs" / "memo.txt").is_file()
        assert any(r.id == "u1" and r.destination == "Jobs" for r in svc.config.rules)
        win.banner._run_action()
        v3_qapp.processEvents()
        assert (app_paths.downloads_dir / "Work" / "memo.txt").is_file()
        assert any(r.id == "u1" and r.destination == "Work" for r in svc.config.rules)
    finally:
        win.close()
        svc.stop(timeout=1.0)


def test_drag_rules_refuse_loops(app_paths: AppPaths, v3_qapp) -> None:  # noqa: ANN001
    win, svc = _window(app_paths, v3_qapp)
    tab = win.folders_tab
    try:
        tab.apply(lambda c: fd.add_folder(c, "Parent"))
        tab.apply(lambda c: fd.add_folder(c, "Child", "Parent"))
        assert tab._can_drop("Parent", "Parent\\Child") is False
        assert tab._can_drop("Parent\\Child", "Documents") is True
        tab.move_folder("Parent\\Child", "Documents")
        v3_qapp.processEvents()
        assert "Documents\\Child" in _tree_paths(tab)
    finally:
        win.close()
        svc.stop(timeout=1.0)


def test_handled_for_you_pages(app_paths: AppPaths, v3_qapp) -> None:  # noqa: ANN001
    from download_sorter.ui.folders.folders_tab import PAGE_OTHER, PAGE_WAITING

    win, svc = _window(app_paths, v3_qapp)
    tab = win.folders_tab
    try:
        tab.show_page(PAGE_OTHER)
        v3_qapp.processEvents()
        tab.page_other.r_leave.setChecked(True)
        assert svc.config.fallback_mode == "leave"
        tab.page_other.r_other.setChecked(True)
        assert svc.config.fallback_mode == "other"
        tab.show_page(PAGE_WAITING)
        v3_qapp.processEvents()
        assert tab.row_wait.isChecked()
    finally:
        win.close()
        svc.stop(timeout=1.0)


def test_tidy_review_dialog_groups_by_folder(v3_qapp) -> None:  # noqa: ANN001
    from download_sorter.ui.folders import TidyReviewDialog

    dlg = TidyReviewDialog([("a.pdf", "Documents"), ("b.pdf", "Documents"), ("c.png", "Images")])
    texts = [dlg.list.item(i).text() for i in range(dlg.list.count())]
    assert any(t.startswith("Downloads › Documents") and "(2)" in t for t in texts)
    assert any("c.png" in t for t in texts)
    dlg.close()
