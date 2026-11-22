"""Unit tests for v3.5.0 content classifier upgrades in download_sorter.router."""

from __future__ import annotations

import zipfile

from PIL import Image

from download_sorter.config import Config, Rule
from download_sorter.paths import AppPaths
from download_sorter.router import (
    FileCandidate,
    _keyword_matches,
    evaluate,
    inspect_image_metadata,
    normalize_filename_tokens,
    peek_zip_contents,
    sniff_magic_extension,
    sniff_magic_mime,
)


def test_sniff_magic_mime_and_zip_container_disambiguation(app_paths: AppPaths, create_test_file) -> None:
    pdf = create_test_file("mystery.bin", b"%PDF-1.7\n1 0 obj\n")
    ext, mime = sniff_magic_mime(pdf)
    assert ext == ".pdf"
    assert mime == "application/pdf"

    stl = create_test_file("bracket.dat", b"solid bracket\n  facet normal 0 0 1\nendsolid\n")
    assert sniff_magic_mime(stl) == (".stl", "model/stl")

    # .3mf masquerading as .zip is disambiguated before extension fallback
    fake_3mf = app_paths.downloads_dir / "model_pack.zip"
    with zipfile.ZipFile(fake_3mf, "w") as zf:
        zf.writestr("3D/3dmodel.model", "<model/>")
    assert sniff_magic_extension(fake_3mf) == ".3mf"
    cfg = Config.with_starter_defaults()
    d_3mf = evaluate(FileCandidate(filename="model_pack.zip", path=fake_3mf), cfg)
    assert d_3mf.action == "move"
    assert str(d_3mf.relative_dest).replace("\\", "/").startswith("3D Models/")

    # .docx / .xlsx / .pptx disambiguation
    for inner, expected_ext in (
        ("word/document.xml", ".docx"),
        ("xl/workbook.xml", ".xlsx"),
        ("ppt/presentation.xml", ".pptx"),
    ):
        p = app_paths.downloads_dir / f"doc_{expected_ext[1:]}.bin"
        with zipfile.ZipFile(p, "w") as zf:
            zf.writestr("[Content_Types].xml", "<Types/>")
            zf.writestr(inner, "<root/>")
        assert sniff_magic_extension(p) == expected_ext


def test_peek_zip_contents_and_evaluate_routing(app_paths: AppPaths) -> None:
    # 3D archive with __MACOSX noise routes to 3D Models
    z3d = app_paths.downloads_dir / "print_bundle.zip"
    with zipfile.ZipFile(z3d, "w") as zf:
        zf.writestr("__MACOSX/._part.stl", "junk")
        zf.writestr("parts/base.stl", "solid")
        zf.writestr("parts/lid.3mf", "3mf")
        zf.writestr("readme.txt", "notes")
    peek = peek_zip_contents(z3d)
    assert peek.valid is True
    assert peek.file_count == 3
    assert peek.dominant_category == "3d"
    assert peek.suggested_destination == "3D Models"
    assert peek.has_path_traversal is False

    cfg = Config.with_starter_defaults()
    d = evaluate(FileCandidate(filename="print_bundle.zip", path=z3d), cfg)
    assert d.action == "move"
    assert str(d.relative_dest).replace("\\", "/") == "3D Models/print_bundle.zip"

    # Inner keyword match routes archive to matching rule destination
    z_hw = app_paths.downloads_dir / "canvas_export.zip"
    with zipfile.ZipFile(z_hw, "w") as zf:
        zf.writestr("week4/ME3210_Homework_4.pdf", "%PDF-1.4")
    cfg.rules.insert(
        0,
        Rule(id="school", name="School", destination="Documents/School", keywords=["me3210", "homework"]),
    )
    d_hw = evaluate(FileCandidate(filename="canvas_export.zip", path=z_hw), cfg)
    assert d_hw.matched_rule == "School"
    assert str(d_hw.relative_dest).replace("\\", "/") == "Documents/School/canvas_export.zip"


def test_peek_zip_contents_zip_slip_falls_back_safely(app_paths: AppPaths) -> None:
    evil = app_paths.downloads_dir / "evil.zip"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("../../Windows/System32/bad.dll", "x")
    peek = peek_zip_contents(evil)
    assert peek.valid is True
    assert peek.has_path_traversal is True

    cfg = Config.with_starter_defaults()
    d = evaluate(FileCandidate(filename="evil.zip", path=evil), cfg)
    assert d.action == "move"
    assert str(d.relative_dest).replace("\\", "/").startswith("Archives/")

    bad = app_paths.downloads_dir / "corrupt.zip"
    bad.write_bytes(b"not a zip")
    assert peek_zip_contents(bad).valid is False


def test_normalized_tokens_aliases_and_whole_word_guards() -> None:
    tokens = normalize_filename_tokens("ME-3210_BankStatement_HW3 (2).pdf")
    assert "bank" in tokens
    assert "statement" in tokens
    assert "hw" in tokens
    assert "homework" in tokens

    # Alias expansion
    assert _keyword_matches("ME3210_hw_04 - Copy (1).pdf", "homework") is True
    assert _keyword_matches("Fall_Syl_2026.pdf", "syllabus") is True

    # Existing whole-word safety guards remain intact
    assert _keyword_matches("opencv_tutorial.pdf", "cv") is False
    assert _keyword_matches("presumed_innocent.pdf", "resume") is False
    assert _keyword_matches("electure_notes.pdf", "lecture") is False


def test_inspect_image_metadata_categories(app_paths: AppPaths) -> None:
    # Screenshot (1920x1080 PNG, no EXIF)
    ss_path = app_paths.downloads_dir / "capture_01.png"
    Image.new("RGB", (1920, 1080), color=(10, 20, 30)).save(ss_path, format="PNG")
    ss = inspect_image_metadata(ss_path)
    assert ss.available is True
    assert ss.category == "screenshot"
    assert ss.suggested_destination == "Images/Screenshots"

    # Camera photo (EXIF Make/Model)
    cam_path = app_paths.downloads_dir / "IMG_1024.jpg"
    img = Image.new("RGB", (800, 600), color=(50, 50, 50))
    exif = img.getexif()
    exif[0x010F] = "Apple"
    exif[0x0110] = "iPhone 15 Pro"
    img.save(cam_path, format="JPEG", exif=exif)
    cam = inspect_image_metadata(cam_path)
    assert cam.category == "camera_photo"
    assert cam.camera_make == "Apple"
    assert cam.suggested_destination == "Images/Photos"

    # CAD render (Software EXIF)
    cad_path = app_paths.downloads_dir / "bracket_v2.png"
    img2 = Image.new("RGB", (1024, 1024), color=(100, 100, 100))
    exif2 = img2.getexif()
    exif2[0x0131] = "Blender 4.2"
    img2.save(cad_path, format="PNG", exif=exif2)
    cad = inspect_image_metadata(cad_path)
    assert cad.category == "cad_render"
    assert cad.suggested_destination == "Images/Renders"

    # Corrupt/missing file degrades gracefully
    bad_img = app_paths.downloads_dir / "broken.png"
    bad_img.write_bytes(b"not an image")
    assert inspect_image_metadata(bad_img).category == "error"
