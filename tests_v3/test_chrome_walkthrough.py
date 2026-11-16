"""Tests for the 4-step Chrome Extension picture guide walkthrough dialog and Settings entry points."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QApplication, QDialog

from download_sorter.paths import AppPaths


def test_chrome_guide_assets_exist_and_load(v3_qapp) -> None:
    from PySide6.QtGui import QPixmap
    from download_sorter.ui.chrome_walkthrough import CHROME_GUIDE_STEPS, guide_asset_path

    assert len(CHROME_GUIDE_STEPS) == 4
    for step in CHROME_GUIDE_STEPS:
        path = guide_asset_path(step.image_name)
        assert path is not None and path.is_file(), f"Missing asset: {step.image_name}"
        px = QPixmap(str(path))
        assert not px.isNull(), f"Invalid pixmap: {step.image_name}"
        assert px.width() >= 1000 and px.height() >= 500


def test_chrome_walkthrough_navigation_and_actions(
    v3_qapp,
    app_paths: AppPaths,
    monkeypatch,
) -> None:
    from download_sorter.ui import chrome_walkthrough as cw
    from download_sorter.ui.chrome_walkthrough import ChromeExtensionWalkthroughDialog

    monkeypatch.setattr(cw, "is_chrome_host_registered", lambda *a, **k: False)
    installed_calls: list[Path | None] = []

    def _fake_install(data_dir=None, **kwargs):
        installed_calls.append(data_dir)
        monkeypatch.setattr(cw, "is_chrome_host_registered", lambda *a, **k: True)
        return {"ok": True, "host_target": "DownloadSorter-cli.exe"}

    monkeypatch.setattr(cw, "install_chrome_native_host", _fake_install)

    updated_callbacks: list[bool] = []
    dlg = ChromeExtensionWalkthroughDialog(
        app_paths.data_dir,
        on_host_updated=lambda: updated_callbacks.append(True),
    )

    # Step 1
    assert dlg.current_step == 0
    assert dlg.lbl_step_badge.text() == "STEP 1 OF 4"
    assert not dlg.btn_back.isEnabled()
    assert dlg._dots[0].property("active") == "true"
    assert not dlg.lbl_picture.pixmap().isNull()

    dlg.btn_copy_chrome_url.click()
    assert QApplication.clipboard().text() == "chrome://extensions"
    assert dlg.lbl_url_status.text() == "COPIED TO CLIPBOARD"

    # Step 2
    dlg.btn_next.click()
    assert dlg.current_step == 1
    assert dlg.lbl_step_badge.text() == "STEP 2 OF 4"
    assert dlg.btn_back.isEnabled()
    assert dlg._dots[0].property("done") == "true"
    assert dlg._dots[1].property("active") == "true"

    dlg.btn_step2_copy.click()
    copied_path = Path(QApplication.clipboard().text())
    assert copied_path.name == "chrome_extension"
    assert (copied_path / "manifest.json").is_file()
    assert dlg.lbl_step2_status.text() == "READY TO PASTE (CTRL+V)"

    # Step 3
    dlg.btn_next.click()
    assert dlg.current_step == 2
    assert dlg.lbl_step_badge.text() == "STEP 3 OF 4"
    assert "Select Folder" in dlg.lbl_title.text()

    # Back works cleanly
    dlg.btn_back.click()
    assert dlg.current_step == 1
    dlg.btn_next.click()
    assert dlg.current_step == 2

    # Step 4
    dlg.btn_next.click()
    assert dlg.current_step == 3
    assert dlg.lbl_step_badge.text() == "STEP 4 OF 4"
    assert dlg.btn_next.text() == "Done"
    assert dlg.lbl_host_status.text() == "HOST NOT REGISTERED"

    dlg.btn_repair_host.click()
    assert installed_calls == [app_paths.data_dir]
    assert updated_callbacks == [True]
    assert dlg.lbl_host_status.text() == "HOST REGISTERED"

    dlg.btn_next.click()
    assert dlg.result() == QDialog.DialogCode.Accepted
    dlg.close()


def test_mainwindow_chrome_guide_button_and_link(
    v3_qapp,
    app_paths: AppPaths,
    monkeypatch,
) -> None:
    from download_sorter.service import SorterService
    from download_sorter.ui import main_window as mw
    from download_sorter.ui.main_window import MainWindow, WalkthroughDialog
    from download_sorter.ui.motion import make_vector_icon

    monkeypatch.setattr(mw, "is_startup_enabled", lambda: False)
    monkeypatch.setattr(mw, "is_context_menu_enabled", lambda: False)
    monkeypatch.setattr(mw, "is_chrome_host_registered", lambda *a, **k: True)

    opened_calls: list[Path | None] = []
    monkeypatch.setattr(
        mw.ChromeExtensionWalkthroughDialog,
        "exec",
        lambda self: (opened_calls.append(self._data_dir), QDialog.DialogCode.Accepted)[1],
    )

    assert not make_vector_icon("copy", "#F5F5F7", 14).isNull()

    svc = SorterService(app_paths)
    win = MainWindow(svc)

    # 1. Settings '?' Setup Guide button opens the Chrome picture walkthrough
    win.btn_chrome_guide.click()
    assert len(opened_calls) == 1

    # 2. Clickable hint link opens the Chrome picture walkthrough
    win.lbl_chrome_hint.linkActivated.emit("guide")
    assert len(opened_calls) == 2

    # 3. Step 3 of general WalkthroughDialog exposes Chrome Guide shortcut
    general = WalkthroughDialog(svc.watch_root, svc.config.hold_3mf_days, win)
    assert general.btn_chrome_setup.isHidden() is True
    general._on_next_clicked()
    general._on_next_clicked()
    assert general.btn_chrome_setup.isHidden() is False
    general.btn_chrome_setup.click()
    assert len(opened_calls) == 3

    win.close()
