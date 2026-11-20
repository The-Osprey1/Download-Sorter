"""Screen readers announce controls by their accessible name. Icon-only buttons have
no text, so without a name they are read out as just "button"."""

from __future__ import annotations

from PySide6.QtWidgets import QDialog, QLineEdit, QPushButton, QVBoxLayout, QWidget, QAbstractButton

from download_sorter.ui.accessibility import install_screen_reader_names, name_controls_for_screen_readers


def _panel(*widgets: QWidget) -> QWidget:
    root = QWidget()
    layout = QVBoxLayout(root)
    for w in widgets:
        layout.addWidget(w)
    return root


def test_icon_only_button_is_named_from_its_tooltip(v3_qapp) -> None:
    btn = QPushButton("")
    btn.setToolTip("Undo the last sort")
    root = _panel(btn)
    name_controls_for_screen_readers(root)
    assert btn.accessibleName() == "Undo the last sort"


def test_button_text_is_used_without_shortcut_marks(v3_qapp) -> None:
    save = QPushButton("&Save Settings")
    bills = QPushButton("Bills && receipts")
    root = _panel(save, bills)
    name_controls_for_screen_readers(root)
    assert save.accessibleName() == "Save Settings"
    assert bills.accessibleName() == "Bills & receipts"


def test_symbol_only_button_is_named_from_its_tooltip(v3_qapp) -> None:
    close = QPushButton("×")
    close.setToolTip("Dismiss")
    root = _panel(close)
    name_controls_for_screen_readers(root)
    assert close.accessibleName() == "Dismiss"


def test_a_name_set_by_hand_is_kept(v3_qapp) -> None:
    btn = QPushButton("")
    btn.setToolTip("Tooltip")
    btn.setAccessibleName("Open Downloads folder")
    root = _panel(btn)
    name_controls_for_screen_readers(root)
    assert btn.accessibleName() == "Open Downloads folder"


def test_text_box_is_named_from_its_placeholder(v3_qapp) -> None:
    box = QLineEdit()
    box.setPlaceholderText("Type any filename to test")
    root = _panel(box)
    name_controls_for_screen_readers(root)
    assert box.accessibleName() == "Type any filename to test"


def test_windows_opened_later_get_names_too(v3_qapp) -> None:
    install_screen_reader_names(v3_qapp)
    dlg = QDialog()
    btn = QPushButton("", dlg)
    btn.setToolTip("Close this guide")
    QVBoxLayout(dlg).addWidget(btn)
    dlg.show()
    v3_qapp.processEvents()
    assert btn.accessibleName() == "Close this guide"
    dlg.close()


def test_every_button_in_the_main_window_can_be_announced(service_instance, v3_qapp) -> None:
    from download_sorter.ui.main_window import MainWindow

    win = MainWindow(service_instance)
    unnamed = [
        b.objectName() or repr(b)
        for b in win.findChildren(QAbstractButton)
        # Qt's own parts (table corner buttons) and buttons hidden until they get a label are skipped.
        if not b.objectName().startswith("qt_") and not b.isHidden()
        and not (b.accessibleName() or b.text().strip())
    ]
    assert unnamed == []
    win.close()


def test_settings_switches_are_named_after_their_row(v3_qapp) -> None:
    from PySide6.QtWidgets import QPushButton as _Btn

    from download_sorter.ui.settings_widgets import MacToggle, SettingRow

    toggle = MacToggle()
    row = SettingRow("Automatic Sorting", "Sort new downloads a few seconds after they finish", controls=toggle)
    box = QLineEdit()
    browse = _Btn("Browse...")
    folder_row = SettingRow("Folder to Organize", controls=[box, browse])
    assert toggle.accessibleName() == "Automatic Sorting"
    assert box.accessibleName() == "Folder to Organize"
    assert browse.accessibleName() in ("", "Browse...")  # keeps its own label
    assert row.lbl_title.buddy() is toggle
    assert folder_row.lbl_title.buddy() is box
