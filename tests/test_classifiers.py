"""Unit tests for Milestone 3 classification helpers in classifiers.py."""

from __future__ import annotations

import builtins
import io
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import classifiers


# ---------------------------------------------------------------------------
# 1. Zone.Identifier ADS & source_url Override
# ---------------------------------------------------------------------------

class TestZoneIdentifier:
    def test_parse_zone_identifier_text(self):
        raw = (
            "[ZoneTransfer]\r\n"
            "ZoneId=3\r\n"
            "ReferrerUrl=https://makerworld.com/en/models/12345\r\n"
            "HostUrl=https://cdn.makerworld.com/model.3mf\r\n"
        )
        zi = classifiers.parse_zone_identifier_text(raw)
        assert zi.zone_id == 3
        assert zi.domain == "cdn.makerworld.com"
        assert zi.source == "ads"

    def test_source_url_override_for_sandbox_parity(self):
        zi = classifiers.read_zone_identifier(
            file_path=None,
            source_url="https://www.printables.com/model/999-bracket",
        )
        assert zi.source == "override"
        assert zi.domain == "printables.com"
        match = classifiers.classify_source_domain(zi)
        assert match is not None
        assert match.destination == "3D Models"

    def test_classify_source_domain_edu_and_custom(self):
        zi_edu = classifiers.read_zone_identifier(source_url="https://canvas.wmich.edu/courses/123")
        m_edu = classifiers.classify_source_domain(zi_edu)
        assert m_edu is not None
        assert m_edu.destination == "Documents\\School"

        zi_custom = classifiers.read_zone_identifier(source_url="https://files.acme.io/spec.pdf")
        m_custom = classifiers.classify_source_domain(zi_custom, domain_rules={"acme.io": "Work\\Acme"})
        assert m_custom is not None
        assert m_custom.destination == "Work\\Acme"

    def test_read_zone_identifier_missing_ads_returns_empty(self, tmp_path: Path):
        f = tmp_path / "plain.txt"
        f.write_text("hello", encoding="utf-8")
        zi = classifiers.read_zone_identifier(f)
        assert zi.source == "none"
        assert zi.domain == ""

    def test_read_zone_identifier_reads_ads_stream(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        f = tmp_path / "download.bin"
        f.write_bytes(b"data")
        real_open = builtins.open

        def fake_open(path, *args, **kwargs):
            if str(path).endswith(":Zone.Identifier"):
                return io.StringIO("[ZoneTransfer]\nZoneId=3\nHostUrl=https://github.com/org/repo.zip\n")
            return real_open(path, *args, **kwargs)

        monkeypatch.setattr(builtins, "open", fake_open)
        zi = classifiers.read_zone_identifier(f)
        assert zi.source == "ads"
        assert zi.domain == "github.com"
        match = classifiers.classify_source_domain(zi)
        assert match is not None
        assert match.destination == "Code"


# ---------------------------------------------------------------------------
# 2. Magic-Byte / MIME Sniffing & ZIP Container Disambiguation
# ---------------------------------------------------------------------------

class TestMagicSniffing:
    def test_sniff_pdf_png_jpg_exe_stl(self, tmp_path: Path):
        pdf = tmp_path / "unknown.bin"
        pdf.write_bytes(b"%PDF-1.7\n%...")
        res_pdf = classifiers.sniff_magic(pdf)
        assert res_pdf is not None
        assert res_pdf.detected_ext == ".pdf"
        assert res_pdf.mime_type == "application/pdf"

        png = tmp_path / "noext"
        png.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")
        res_png = classifiers.sniff_magic(png)
        assert res_png is not None
        assert res_png.detected_ext == ".png"

        stl = tmp_path / "model.dat"
        stl.write_bytes(b"solid bracket\n  facet normal 0 0 1\nendsolid\n")
        res_stl = classifiers.sniff_magic(stl)
        assert res_stl is not None
        assert res_stl.detected_ext == ".stl"

    def test_disambiguate_zip_containers(self, tmp_path: Path):
        # 3MF masquerading as .zip
        p_3mf = tmp_path / "part.zip"
        with zipfile.ZipFile(p_3mf, "w") as zf:
            zf.writestr("3D/3dmodel.model", "<model/>")
        res_3mf = classifiers.sniff_magic(p_3mf)
        assert res_3mf is not None
        assert res_3mf.detected_ext == ".3mf"
        assert res_3mf.container_kind == "3mf"

        # DOCX masquerading as .bin
        p_docx = tmp_path / "doc.bin"
        with zipfile.ZipFile(p_docx, "w") as zf:
            zf.writestr("[Content_Types].xml", "<Types/>")
            zf.writestr("word/document.xml", "<w:document/>")
        res_docx = classifiers.sniff_magic(p_docx)
        assert res_docx is not None
        assert res_docx.detected_ext == ".docx"
        assert res_docx.container_kind == "ooxml_word"

        # Plain ZIP
        p_zip = tmp_path / "bundle.dat"
        with zipfile.ZipFile(p_zip, "w") as zf:
            zf.writestr("readme.txt", "hello")
        res_zip = classifiers.sniff_magic(p_zip)
        assert res_zip is not None
        assert res_zip.detected_ext == ".zip"
        assert res_zip.container_kind == "zip"


# ---------------------------------------------------------------------------
# 3. Pre-Routing ZIP Archive Content Peek
# ---------------------------------------------------------------------------

class TestZipPeek:
    def test_peek_3d_archive(self, tmp_path: Path):
        z = tmp_path / "download.zip"
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr("__MACOSX/._base.stl", "junk")
            zf.writestr("models/base.stl", "solid")
            zf.writestr("models/lid.3mf", "3mf")
            zf.writestr("readme.txt", "notes")
        res = classifiers.peek_zip_contents(z)
        assert res.valid is True
        assert res.file_count == 3
        assert res.dominant_category == "3d"
        assert res.suggested_destination == "3D Models"
        assert res.has_path_traversal is False

    def test_peek_keyword_match_inside_archive(self, tmp_path: Path):
        z = tmp_path / "bundle.zip"
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr("week3/ME3210_Homework_3.pdf", "pdf")
            zf.writestr("week3/data.csv", "1,2")
        rules = [{"name": "School", "folder": "Documents\\School", "keywords": ["me3210", "homework"], "enabled": True}]
        res = classifiers.peek_zip_contents(z, keyword_rules=rules)
        assert res.dominant_category == "keyword_match"
        assert res.suggested_destination == "Documents\\School"
        assert res.matched_keyword_rule == "School"

    def test_peek_detects_zip_slip_and_bad_zip(self, tmp_path: Path):
        z = tmp_path / "evil.zip"
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr("../../Windows/System32/bad.dll", "x")
        res = classifiers.peek_zip_contents(z)
        assert res.valid is True
        assert res.has_path_traversal is True

        bad = tmp_path / "corrupt.zip"
        bad.write_bytes(b"not a zip")
        assert classifiers.peek_zip_contents(bad).valid is False


# ---------------------------------------------------------------------------
# 4. Fuzzy & Normalized Filename Token Matching
# ---------------------------------------------------------------------------

class TestFuzzyFilenameMatching:
    def test_camelcase_delimiters_and_copy_suffix(self):
        tokens = classifiers.normalize_filename_tokens("ME-3210_BankStatement_HW3 (2).pdf")
        assert "me3210" in tokens
        assert "bank" in tokens
        assert "statement" in tokens
        assert "hw" in tokens
        assert "homework" in tokens

    def test_fuzzy_and_alias_matching(self):
        ok_alias, score_alias, reason_alias = classifiers.fuzzy_matches_keyword("ME3210_hw_04.pdf", "homework")
        assert ok_alias is True
        assert score_alias == 1.0
        assert reason_alias == "normalized_exact"

        ok_fuzzy, score_fuzzy, reason_fuzzy = classifiers.fuzzy_matches_keyword("Fall_Sylabus_2026.pdf", "syllabus")
        assert ok_fuzzy is True
        assert score_fuzzy >= 0.84
        assert reason_fuzzy == "fuzzy_token"

        ok_multi, _, _ = classifiers.fuzzy_matches_keyword("BankStatement-2026 (1).pdf", "bank_statement")
        assert ok_multi is True

    def test_short_token_guard_prevents_false_positives(self):
        ok, _, _ = classifiers.fuzzy_matches_keyword("hi_there.txt", "hw")
        assert ok is False


# ---------------------------------------------------------------------------
# 5. Pillow Image Inspection & Optional PDF Page-1 Extraction
# ---------------------------------------------------------------------------

class TestImageAndPdfInspection:
    def test_image_screenshot_vs_camera_vs_render(self, tmp_path: Path):
        # Screenshot (1920x1080 PNG, no EXIF)
        ss_path = tmp_path / "capture_01.png"
        Image.new("RGB", (1920, 1080), color=(10, 20, 30)).save(ss_path, format="PNG")
        ss_res = classifiers.inspect_image_metadata(ss_path)
        assert ss_res.available is True
        assert ss_res.category == "screenshot"
        assert ss_res.suggested_destination == "Images\\Screenshots"

        # Camera photo (EXIF Make/Model)
        cam_path = tmp_path / "IMG_1024.jpg"
        img = Image.new("RGB", (800, 600), color=(50, 50, 50))
        exif = img.getexif()
        exif[0x010F] = "Apple"  # Make
        exif[0x0110] = "iPhone 15 Pro"  # Model
        img.save(cam_path, format="JPEG", exif=exif)
        cam_res = classifiers.inspect_image_metadata(cam_path)
        assert cam_res.category == "camera_photo"
        assert cam_res.camera_make == "Apple"
        assert cam_res.suggested_destination == "Images\\Photos"

        # CAD render (Software EXIF)
        cad_path = tmp_path / "bracket_v2.png"
        img2 = Image.new("RGB", (1024, 1024), color=(100, 100, 100))
        exif2 = img2.getexif()
        exif2[0x0131] = "Blender 4.2"  # Software
        img2.save(cad_path, format="PNG", exif=exif2)
        cad_res = classifiers.inspect_image_metadata(cad_path)
        assert cad_res.category == "cad_render"
        assert cad_res.suggested_destination == "Images\\Renders"

    def test_pdf_graceful_degradation_when_pypdf_missing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(classifiers, "_HAS_PYPDF", False)
        monkeypatch.setattr(classifiers, "PdfReader", None)
        pdf_path = tmp_path / "scan.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\n")
        res = classifiers.extract_pdf_page1_text(pdf_path)
        assert res.available is False
        assert res.error == "pypdf_not_installed"

    def test_match_pdf_keywords_with_mock_reader(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        class FakePage:
            def extract_text(self):
                return "ME 3210 Thermodynamics Homework 5 Due Friday"

        class FakeReader:
            def __init__(self, _path):
                self.is_encrypted = False
                self.metadata = SimpleNamespace(title="Course Assignment")
                self.pages = [FakePage()]

        monkeypatch.setattr(classifiers, "_HAS_PYPDF", True)
        monkeypatch.setattr(classifiers, "PdfReader", FakeReader)

        pdf_path = tmp_path / "Scan_20260903.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\n")
        rules = [{"name": "School", "folder": "Documents\\School", "keywords": ["me3210", "homework"], "enabled": True}]
        hit = classifiers.match_pdf_keywords(pdf_path, rules)
        assert hit is not None
        assert hit["destination"] == "Documents\\School"
        assert hit["reason"].startswith("pdf_page1_")
