"""Ideas borrowed from similar tools, rebuilt for Download Sorter.

- Course websites as a starter folder (like a browser add-on that sorts by site).
- A warning when Windows Storage Sense would delete sorted files in Downloads.
- Old installers and zips to the Recycle Bin once a day, if turned on (Hazel).
- A preview before the first big sort (AI File Sorter's review table).
- Pictures into a folder per month (Hazel, organize).
- Rule words found at the top of a PDF (File Juggler, organize).
- Optional tidy names and dates in front, undone by Undo (Hazel).
"""

from __future__ import annotations

import json
import os
import sys
import time
import types
from pathlib import Path

import pytest

import clean_old
import config_manager
import consolidate
import folder_scan
import learning
import sort_undo
import sorter


@pytest.fixture
def downloads(tmp_path) -> Path:
    root = tmp_path / "Downloads"
    root.mkdir()
    return root.resolve()


@pytest.fixture
def config(downloads) -> dict:
    cfg = sorter.get_default_config()
    cfg["downloads_dir"] = str(downloads)
    cfg["watch"] = {"folder": str(downloads), "settle_seconds": 0, "min_age_minutes": 0,
                    "auto_unzip": False, "auto_unpack_3d_zips": False}
    cfg["notifications"] = False
    cfg["ribbon_enabled"] = False
    cfg["quick_switcher_enabled"] = False
    cfg["hold"]["enabled"] = False
    return cfg


def _old(path: Path, data: bytes | str = "data", days: float = 0.01) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, bytes):
        path.write_bytes(data)
    else:
        path.write_text(data, encoding="utf-8")
    past = time.time() - days * 86400
    os.utime(path, (past, past))
    return path


def _pdf(text: str) -> bytes:
    """A one-page PDF with a line of text near the top."""
    stream = f"BT /F1 18 Tf 40 760 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


# ---------------------------------------------------------------------------
# Course websites
# ---------------------------------------------------------------------------

def test_course_websites_starter_files_by_site(downloads, config, monkeypatch):
    monkeypatch.setattr(learning, "_read_zone_identifier", lambda p: (
        "[ZoneTransfer]\nReferrerUrl=https://myschool.instructure.com/courses/1\n" if "week" in Path(p).name else ""))
    new = folder_scan.apply_scan(config, {"folders": []}, starters=["school_sites"])
    handout = _old(downloads / "week3.pdf")
    other = _old(downloads / "flyer.pdf")

    assert sorter.evaluate_routing(handout.name, new, file_path=handout)["destination"] == "Documents\\School"
    assert sorter.evaluate_routing(other.name, new, file_path=other)["destination"] == "Documents\\PDF"


# ---------------------------------------------------------------------------
# Storage Sense
# ---------------------------------------------------------------------------

def _fake_winreg(values: dict[tuple[str, str], int]):
    class Key:
        def __init__(self, path):
            self.path = path

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def open_key(_root, subkey, *_a):
        if not any(k[0] == subkey for k in values):
            raise OSError("missing")
        return Key(subkey)

    def query(key, name):
        if (key.path, name) not in values:
            raise OSError("missing")
        return values[(key.path, name)], 4

    return types.SimpleNamespace(HKEY_LOCAL_MACHINE=1, HKEY_CURRENT_USER=2, KEY_READ=0,
                                 OpenKey=open_key, QueryValueEx=query)


USER = r"Software\Microsoft\Windows\CurrentVersion\StorageSense\Parameters\StoragePolicy"
POLICY = r"SOFTWARE\Policies\Microsoft\Windows\StorageSense"


@pytest.mark.parametrize("values, expected", [
    ({}, None),
    ({(USER, "01"): 1, (USER, "512"): 30}, 30),
    ({(USER, "01"): 0, (USER, "512"): 30}, None),     # Storage Sense itself is off
    ({(USER, "01"): 1, (USER, "512"): 0}, None),      # "Never"
    ({(POLICY, "ConfigStorageSenseDownloadsCleanupThreshold"): 14, (USER, "01"): 0}, 14),
    ({(POLICY, "AllowStorageSenseGlobal"): 0, (USER, "01"): 1, (USER, "512"): 30}, None),
])
def test_storage_sense_downloads_setting(monkeypatch, values, expected):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "winreg", _fake_winreg(values))
    assert config_manager.storage_sense_downloads_days() == expected


def _close_widget(qapp, widget) -> None:
    from PySide6.QtCore import QEvent

    widget.close()
    widget.deleteLater()
    qapp.processEvents()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)


def test_settings_warns_about_storage_sense(qapp, monkeypatch):
    onboarding = pytest.importorskip("onboarding")
    monkeypatch.setattr(onboarding.suite_data, "storage_sense_downloads_days", lambda: 30)
    widget = onboarding.SettingsWidget(config=sorter.get_default_config())
    try:
        assert not widget._storage_sense_lbl.isHidden()
        assert "30 days" in widget._storage_sense_lbl.text()
        monkeypatch.setattr(onboarding.suite_data, "storage_sense_downloads_days", lambda: None)
        widget.refresh_data()
        assert widget._storage_sense_lbl.isHidden()
    finally:
        _close_widget(qapp, widget)


# ---------------------------------------------------------------------------
# Old installers and zips to the Recycle Bin
# ---------------------------------------------------------------------------

def test_daily_cleanup_only_when_turned_on_and_once_a_day(downloads, config, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    binned: list[Path] = []
    monkeypatch.setattr(clean_old, "send_to_recycle_bin", lambda p: binned.append(p) or True)
    _old(downloads / "Installers" / "old_setup.exe", "x" * 10, days=45)
    _old(downloads / "Installers" / "new_setup.exe", days=2)
    _old(downloads / "Archives" / "old.zip", days=60)
    _old(downloads / "Documents" / "old.pdf", days=90)

    assert clean_old.auto_cleanup(config) is None, "off by default"
    config["cleanup"]["enabled"] = True
    result = clean_old.auto_cleanup(config, now=1_000_000_000)
    assert result["count"] == 2
    assert sorted(p.name for p in binned) == ["old.zip", "old_setup.exe"]
    assert clean_old.auto_cleanup(config, now=1_000_000_000 + 3600) is None, "already ran today"
    assert clean_old.auto_cleanup(config, now=1_000_000_000 + 25 * 3600) is not None


def test_cleanup_does_nothing_off_windows(config):
    config["cleanup"]["enabled"] = True
    if sys.platform != "win32":
        assert clean_old.auto_cleanup(config) is None


# ---------------------------------------------------------------------------
# Preview before the first big sort
# ---------------------------------------------------------------------------

def test_first_sort_summary_counts_by_folder(downloads):
    onboarding = pytest.importorskip("onboarding")
    plan = [(downloads / "a.jpg", downloads / "Images" / "a.jpg"),
            (downloads / "b.jpg", downloads / "Images" / "b.jpg"),
            (downloads / "c.pdf", downloads / "Documents" / "c.pdf")]
    assert onboarding.first_sort_summary(plan, downloads) == [("Images", 2), ("Documents", 1)]


@pytest.mark.parametrize("answer", [True, False])
def test_finish_asks_before_the_first_sort(qapp, monkeypatch, downloads, config, answer):
    onboarding = pytest.importorskip("onboarding")
    _old(downloads / "photo.jpg")
    saved: list[dict] = []
    monkeypatch.setattr(onboarding.suite_data, "save_sorter_config", lambda cfg, *a, **k: saved.append(cfg))
    monkeypatch.setattr(onboarding.suite_data, "set_startup_enabled", lambda *_a, **_k: True)
    monkeypatch.setattr(onboarding.suite_data, "set_context_menu_enabled", lambda *_a, **_k: True)
    monkeypatch.setattr(onboarding, "_sorter_is_waiting_for_setup", lambda: True)
    asked = []
    monkeypatch.setattr(onboarding.WelcomeWizardDialog, "_confirm_first_sort",
                        lambda self, plan: asked.append(plan) or answer)
    dlg = onboarding.WelcomeWizardDialog(config=config)
    try:
        assert dlg._chk_sort_now.isChecked()
        dlg._on_finish_clicked()
    finally:
        _close_widget(qapp, dlg)

    assert [src.name for src, _dest in asked[0]] == ["photo.jpg"]
    assert ("skip_files_before" in saved[-1]) is (not answer), "Not now leaves existing files alone"


# ---------------------------------------------------------------------------
# Pictures by month
# ---------------------------------------------------------------------------

def test_pictures_by_month_uses_the_date_taken(downloads, config):
    from PIL import Image

    config["photos"]["by_month"] = True
    image = Image.new("RGB", (4, 4))
    exif = Image.Exif()
    exif[306] = "2023:07:04 10:00:00"
    photo = downloads / "IMG_0001.jpg"
    image.save(photo, exif=exif)
    shot = _old(downloads / "screenshot.png", b"not really a png")
    os.utime(shot, (1_700_000_000, 1_700_000_000))   # November 2023

    assert sorter.move_file(photo, config, check_age=False).parent == downloads / "Images" / "2023-07"
    assert sorter.move_file(shot, config, check_age=False).parent.name == "2023-11"


def test_pictures_go_by_kind_unless_months_are_turned_on(downloads, config):
    photo = _old(downloads / "a.jpg")
    assert sorter.move_file(photo, config, check_age=False).parent == downloads / "Images" / "JPG"


def test_consolidate_keeps_month_folders(downloads, config):
    config["photos"]["by_month"] = True
    kept = _old(downloads / "Images" / "2024-05" / "beach.jpg")
    _old(downloads / "Images" / "Wallpapers" / "w.jpg")

    consolidate.consolidate_downloads(target_root=downloads, config=config)

    assert kept.exists()
    assert not (downloads / "Images" / "Wallpapers").exists()


# ---------------------------------------------------------------------------
# Words at the top of a PDF
# ---------------------------------------------------------------------------

def test_rule_words_at_the_top_of_a_pdf(downloads, config):
    pytest.importorskip("pypdf")
    config["keyword_rules"] = [{"name": "Invoices", "folder": "Invoices", "match": ["invoice"]}]
    invoice = _old(downloads / "document (3).pdf", _pdf("INVOICE #4471 from Example Shop"))
    letter = _old(downloads / "letter.pdf", _pdf("Dear student, welcome back"))

    assert sorter.evaluate_routing(invoice.name, config, file_path=invoice)["destination"] == "Documents\\PDF"
    config["pdf_text"]["enabled"] = True
    result = sorter.evaluate_routing(invoice.name, config, file_path=invoice)
    assert result["destination"] == "Invoices"
    assert "top of the PDF" in result["matched_trigger"]
    assert sorter.evaluate_routing(letter.name, config, file_path=letter)["destination"] == "Documents\\PDF"


def test_unreadable_or_large_pdfs_are_skipped(downloads, config):
    config["pdf_text"] = {"enabled": True, "max_mb": 0.000001}
    big = _old(downloads / "big.pdf", _pdf("INVOICE"))
    broken = _old(downloads / "broken.pdf", b"%PDF-1.4 not really")
    assert sorter.pdf_head_text(big, config) == ""
    config["pdf_text"]["max_mb"] = 10
    assert sorter.pdf_head_text(broken, config) == ""


# ---------------------------------------------------------------------------
# Tidy names and dates in front
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name, tidy, add_date, expected", [
    ("My_File (1).pdf", True, False, "My File.pdf"),
    ("report - Copy.docx", True, False, "report.docx"),
    ("photo (2) (3).jpg", True, False, "photo.jpg"),
    ("main.py", True, True, "main.py"),                  # code keeps its name
    ("bracket.stl", True, True, "bracket.stl"),          # CAD and 3D files too
    ("Invoice.pdf", False, True, "2023-11-14 Invoice.pdf"),
    ("2025-01-02 Invoice.pdf", False, True, "2025-01-02 Invoice.pdf"),
    ("My_File (1).pdf", False, False, "My_File (1).pdf"),
])
def test_filed_name(tmp_path, config, name, tidy, add_date, expected):
    path = _old(tmp_path / name)
    os.utime(path, (1_700_000_000, 1_700_000_000))
    config["rename"] = {"tidy": tidy, "add_date": add_date}
    assert sorter.filed_name(path, config) == expected


def test_undo_puts_the_old_name_back(downloads, config):
    config["rename"] = {"tidy": True, "add_date": False}
    original = _old(downloads / "Lab_Report (1).docx")

    dest = sorter.move_file(original, config, batch_id="r1", check_age=False)

    assert dest == downloads / "Documents" / "Word" / "Lab Report.docx"
    assert sort_undo.main(["--batch", "r1"]) == 0
    assert original.exists() and not dest.exists()


def test_a_renamed_duplicate_is_still_recognized(downloads, config):
    config["rename"] = {"tidy": True, "add_date": False}
    _old(downloads / "Documents" / "Word" / "Lab Report.docx", "same")
    again = _old(downloads / "Lab_Report (1).docx", "same")

    sorter.move_file(again, config, batch_id="r2", check_age=False)

    assert not (downloads / "Documents" / "Word" / "Lab Report (2).docx").exists()
    assert (downloads / "_Duplicates").exists()


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

def test_settings_extra_options_save(qapp, monkeypatch):
    onboarding = pytest.importorskip("onboarding")
    saved: list[dict] = []
    monkeypatch.setattr(onboarding.suite_data, "save_sorter_config",
                        lambda cfg, *a, **k: saved.append(json.loads(json.dumps(cfg))))
    widget = onboarding.SettingsWidget(config=sorter.get_default_config())
    try:
        checks = widget._option_checks
        layout = {("type_folders", "enabled"), ("my_folders", "enabled")}
        assert set(checks) == layout | {("photos", "by_month"), ("pdf_text", "enabled"), ("rename", "tidy"),
                                        ("rename", "add_date"), ("cleanup", "enabled")}
        assert all(checks[key].isChecked() for key in layout), "the folder layout is on by default"
        assert not any(c.isChecked() for key, c in checks.items() if key not in layout), "extras are off by default"
        checks[("type_folders", "enabled")].setChecked(False)
        assert saved[-1]["type_folders"]["enabled"] is False
        assert saved[-1]["my_folders"]["enabled"] is True
        checks[("photos", "by_month")].setChecked(True)
        assert saved[-1]["photos"]["by_month"] is True
        checks[("rename", "add_date")].setChecked(True)
        assert saved[-1]["rename"] == {"tidy": False, "add_date": True}
    finally:
        _close_widget(qapp, widget)
