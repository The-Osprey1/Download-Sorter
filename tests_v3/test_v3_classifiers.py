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
    # By default, zip_peek_enabled is False so .zip files route normally to Archives
    d_default = evaluate(FileCandidate(filename="print_bundle.zip", path=z3d), cfg)
    assert d_default.action == "move"
    assert str(d_default.relative_dest).replace("\\", "/") == "Archives/print_bundle.zip"

    # When zip_peek_enabled=True, 3D archive routes to 3D Models
    cfg.zip_peek_enabled = True
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
    cfg.zip_peek_enabled = True
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


def test_ignore_guard_blocks_tmp_before_magic_sniffing(app_paths: AppPaths) -> None:
    cfg = Config.with_starter_defaults()
    tmp_pdf = app_paths.downloads_dir / "active_download.tmp"
    tmp_pdf.write_bytes(b"%PDF-1.7\n1 0 obj\n")
    dec = evaluate(FileCandidate(filename="active_download.tmp", path=tmp_pdf), cfg)
    assert dec.action == "ignore"
    assert dec.matched_rule == "Ignore Guard"


def test_mp3_magic_sniffing_rejects_ff_padding_and_accepts_valid_frames(app_paths: AppPaths) -> None:
    # Binary mesh data padded with 0xFF must NOT be misclassified as MP3 audio
    mesh_dat = app_paths.downloads_dir / "MyMesh.dat"
    mesh_dat.write_bytes(b"\xff" * 64)
    assert sniff_magic_extension(mesh_dat) == ""

    # Valid MPEG-1 Layer III frame header (sync=0xFFE, ver=11, layer=01, bitrate=1001, sr=00)
    raw_mp3 = app_paths.downloads_dir / "track.bin"
    raw_mp3.write_bytes(b"\xff\xfb\x90\x00" + b"\x00" * 32)
    assert sniff_magic_extension(raw_mp3) == ".mp3"

    # Standard ID3v2-tagged MP3
    id3_mp3 = app_paths.downloads_dir / "tagged.bin"
    id3_mp3.write_bytes(b"ID3\x03\x00\x00\x00\x00\x00\x00")
    assert sniff_magic_extension(id3_mp3) == ".mp3"


def test_plural_and_safe_compound_keyword_matching() -> None:
    # Plural/singular equivalence
    assert _keyword_matches("paper-computer-icons-thumbnail.jpg", "icon") is True
    assert _keyword_matches("dnd_figurines_pack.stl", "figurine") is True
    assert _keyword_matches("tabletop_miniatures.3mf", "miniature") is True
    assert _keyword_matches("q1_invoices.pdf", "invoice") is True
    # Safe unambiguous compound suffixes
    assert _keyword_matches("makerworldlogo.png", "logo") is True
    assert _keyword_matches("desktopscreenshot.png", "screenshot") is True
    # Safety guards: ambiguous substrings must NOT match
    assert _keyword_matches("silicon_carbide_chart.png", "icon") is False
    assert _keyword_matches("A1mini_Screen_Cover.3mf", "miniature") is False


def test_preset_subfolders_no_ghost_folders_and_accurate_routing(app_paths: AppPaths) -> None:
    from download_sorter.architecture import (
        FolderArchitecture,
        TIER2_SUGGESTIONS_BY_PARENT,
        apply_architecture_to_config,
    )

    arch = FolderArchitecture(tier_count=2)
    img_node = arch.add_premade_node("t1_images", tier=1)
    m3d_node = arch.add_premade_node("t1_3d_models", tier=1)
    doc_node = arch.add_premade_node("t1_documents", tier=1)
    assert img_node and m3d_node and doc_node

    def _add_sug(parent_key: str, group: str, name: str, parent_id: str) -> None:
        sug = next(s for s in TIER2_SUGGESTIONS_BY_PARENT[parent_key][group] if s["name"] == name)
        arch.add_custom_node(
            name=sug["name"],
            tier=2,
            parent_id=parent_id,
            kind=sug["kind"],
            extensions=list(sug["extensions"]),
            keywords=list(sug["keywords"]),
            exclude_keywords=list(sug.get("exclude_keywords", ())),
            domains=list(sug["domains"]),
        )

    _add_sug("images", "file_type", "SVG & Vector", img_node.id)
    _add_sug("images", "file_type", "GIF", img_node.id)
    _add_sug("images", "classification", "Screenshots", img_node.id)
    _add_sug("images", "classification", "Logos & Icons", img_node.id)
    _add_sug("3d models", "file_type", "3MF", m3d_node.id)
    _add_sug("3d models", "file_type", "STL", m3d_node.id)
    _add_sug("3d models", "classification", "Miniatures & Figurines", m3d_node.id)
    _add_sug("documents", "file_type", "PDF", doc_node.id)
    _add_sug("documents", "classification", "Bills & Receipts", doc_node.id)

    cfg = apply_architecture_to_config(arch, Config.new_install())
    # Round-trip through JSON dict preserves exact type_subfolders without resurrecting defaults
    cfg = Config.from_dict(cfg.to_dict())

    # 1. No ghost Tier 2 folders for PNG / JPG / TXT that aren't on the canvas
    assert ".png" not in cfg.type_subfolders
    assert ".jpg" not in cfg.type_subfolders
    assert ".txt" not in cfg.type_subfolders
    assert str(evaluate(FileCandidate("random_pic.png"), cfg).relative_dest).replace("\\", "/") == "Images/random_pic.png"
    assert str(evaluate(FileCandidate("notes.txt"), cfg).relative_dest).replace("\\", "/") == "Documents/notes.txt"

    # 2. Miniatures & Figurines ignores Bambu A1 Mini printer parts & Mac Mini risers
    for non_mini in [
        "A1 Mini Bed Chain.3mf",
        "A1mini_Screen_Cover.3mf",
        "M2 Mac Mini Riser.3mf",
        "ESP8266_clock_Digital_Clock_(ESP8266_D1_mini).gcode.3mf",
        "Mini_Tripod_Pro.gcode.3mf",
        "Siraya Tech TPU Air 250C 75A A1mini 0.4mm 20251021.3mf",
    ]:
        d = evaluate(FileCandidate(non_mini), cfg)
        assert str(d.relative_dest).replace("\\", "/") == f"3D Models/3MF/{non_mini}"

    # ...while catching real figurines / dragons / dioramas / helmets (even if sliced for A1mini)
    for real_mini in [
        "AstraMinotaurCharacter1.3mf",
        "Baby Valentine Dragon Fully Colored_a1mini.3mf",
        "Haku_Dragon_Diorama_(Spirited_Away)_AMS.3mf",
        "CC_Fullsize_Cut_Helmet SPECIALSTL.stl",
    ]:
        d = evaluate(FileCandidate(real_mini), cfg)
        assert str(d.relative_dest).replace("\\", "/") == f"3D Models/Miniatures & Figurines/{real_mini}"

    # 3. Logos & Icons catches JPG, compound-suffix PNG, plural icons, and 4K logos ahead of Screenshots
    for logo_name in [
        "Copy of updated ams logo.jpg",
        "vista_metals_logo.jpeg",
        "makerworldlogo.png",
        "paper-computer-icons-thumbnail.jpg",
    ]:
        d = evaluate(FileCandidate(logo_name), cfg)
        assert str(d.relative_dest).replace("\\", "/") == f"Images/Logos & Icons/{logo_name}"

    logo_4k = app_paths.downloads_dir / "ChatGPT-Logo.png"
    Image.new("RGB", (3840, 2160), color=(0, 0, 0)).save(logo_4k, format="PNG")
    assert str(evaluate(FileCandidate("ChatGPT-Logo.png", path=logo_4k), cfg).relative_dest).replace("\\", "/") == "Images/Logos & Icons/ChatGPT-Logo.png"

    # 4. Image Metadata Assist catches iPhone 1290x2796 PNG screenshot, ignores 1920x1080 WEBP banner
    iphone_ss = app_paths.downloads_dir / "IMG_4196.png"
    Image.new("RGB", (1290, 2796), color=(20, 30, 40)).save(iphone_ss, format="PNG")
    assert str(evaluate(FileCandidate("IMG_4196.png", path=iphone_ss), cfg).relative_dest).replace("\\", "/") == "Images/Screenshots/IMG_4196.png"

    hero_webp = app_paths.downloads_dir / "dex_sp_hero_landscape.webp"
    Image.new("RGB", (1920, 1080), color=(20, 30, 40)).save(hero_webp, format="WEBP")
    assert str(evaluate(FileCandidate("dex_sp_hero_landscape.webp", path=hero_webp), cfg).relative_dest).replace("\\", "/") == "Images/dex_sp_hero_landscape.webp"

    # 5. Bills & Receipts does NOT hijack academic Statement of Purpose PDFs
    sop = evaluate(FileCandidate("Statement of Purpose - Materials Science PhD.pdf"), cfg)
    assert str(sop.relative_dest).replace("\\", "/") == "Documents/PDF/Statement of Purpose - Materials Science PhD.pdf"
    bank_stmt = evaluate(FileCandidate("Chase_Bank_Statement_2026.pdf"), cfg)
    assert str(bank_stmt.relative_dest).replace("\\", "/") == "Documents/Bills & Receipts/Chase_Bank_Statement_2026.pdf"
