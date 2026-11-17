"""Tests for Auto-Scan Folder Architecture and upgraded sorting methods."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import zipfile

from download_sorter.architecture import (
    ArchNode,
    FolderArchitecture,
    delete_architecture_snapshot,
    list_architecture_snapshots,
    load_architecture_snapshot,
    merge_architectures,
    save_architecture_snapshot,
    scan_folder_architecture,
)
from download_sorter.config import Config, Rule
from download_sorter.dest import read_exif_date, resolve_destination_tokens
from download_sorter.paths import AppPaths
from download_sorter.router import (
    FileCandidate,
    evaluate,
    rule_specificity_score,
    sniff_magic_extension,
    sort_rules_by_specificity,
)


def test_scan_folder_architecture_and_merge(app_paths: AppPaths) -> None:
    root = app_paths.downloads_dir
    # Create a realistic multi-tier Downloads tree
    (root / "School" / "CS101").mkdir(parents=True)
    (root / "School" / "CS101" / "homework_week1.pdf").write_bytes(b"%PDF-1.4")
    (root / "School" / "CS101" / "homework_week2.pdf").write_bytes(b"%PDF-1.4")
    (root / "3D Models").mkdir(parents=True)
    (root / "3D Models" / "bracket.stl").write_bytes(b"solid")
    (root / "3D Models" / "mount.stl").write_bytes(b"solid")
    (root / "_Duplicates").mkdir(parents=True)
    (root / "_Duplicates" / "dup.pdf").write_bytes(b"x")

    res = scan_folder_architecture(root)
    arch: FolderArchitecture = res["architecture"]
    assert res["folder_count"] == 3  # School, CS101, 3D Models (_Duplicates skipped)
    names = {n.name: n for n in arch.nodes}
    assert "School" in names
    assert "CS101" in names
    assert "3D Models" in names
    assert names["CS101"].tier == 2
    assert names["CS101"].parent_id == names["School"].id
    assert ".pdf" in names["CS101"].extensions
    assert "homework" in names["CS101"].keywords
    assert ".stl" in names["3D Models"].extensions

    # Merge into existing architecture without duplicating existing nodes
    existing = FolderArchitecture(
        tier_count=2,
        nodes=[ArchNode(id="ex1", name="School", tier=1, kind="school", extensions=[".docx"])],
    )
    merged = merge_architectures(existing, arch)
    merged_names = [n.name for n in merged.nodes]
    assert merged_names.count("School") == 1
    assert "CS101" in merged_names
    assert "3D Models" in merged_names
    # Existing School node keeps its custom extension
    school_node = next(n for n in merged.nodes if n.name == "School")
    assert ".docx" in school_node.extensions


def test_magic_byte_sniffing_extensionless_and_3mf(app_paths: AppPaths) -> None:
    pdf_no_ext = app_paths.downloads_dir / "mystery_download"
    pdf_no_ext.write_bytes(b"%PDF-1.7\n1 0 obj\n")
    assert sniff_magic_extension(pdf_no_ext) == ".pdf"

    png_bin = app_paths.downloads_dir / "image.bin"
    png_bin.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    assert sniff_magic_extension(png_bin) == ".png"

    fake_3mf = app_paths.downloads_dir / "plate.bin"
    with zipfile.ZipFile(fake_3mf, "w") as zf:
        zf.writestr("3D/3dmodel.model", "<model/>")
    assert sniff_magic_extension(fake_3mf) == ".3mf"

    cfg = Config.with_starter_defaults()
    decision = evaluate(FileCandidate(filename="mystery_download", path=pdf_no_ext), cfg)
    assert decision.action == "move"
    assert str(decision.relative_dest).replace("\\", "/").startswith("Documents/")


def test_camelcase_unordered_and_fuzzy_keyword_matching() -> None:
    cfg = Config.with_starter_defaults()
    cfg.rules.insert(
        0,
        Rule(
            id="tax",
            name="Tax Returns",
            destination="Documents/Taxes",
            keywords=["tax return"],
            extensions=[".pdf"],
        ),
    )
    # CamelCase + unordered tokens
    d1 = evaluate(FileCandidate(filename="2026_ReturnTaxFinal.pdf"), cfg)
    assert d1.matched_rule == "Tax Returns"

    # Fuzzy single-word match (>= 5 chars, minor typo)
    cfg.rules.insert(
        0,
        Rule(
            id="syl",
            name="Syllabi",
            destination="School/Syllabi",
            keywords=["syllabus"],
        ),
    )
    d2 = evaluate(FileCandidate(filename="CS101_Sylabus_Fall.pdf"), cfg)
    assert d2.matched_rule == "Syllabi"


def test_rule_specificity_sorting_and_exif_tokens(app_paths: AppPaths) -> None:
    broad = Rule(id="r1", name="All PDFs", destination="Docs", extensions=[".pdf"])
    medium = Rule(id="r2", name="Invoice PDFs", destination="Invoices", keywords=["invoice"], extensions=[".pdf"])
    narrow = Rule(
        id="r3",
        name="Stripe Invoices",
        destination="Stripe",
        domains=["stripe.com"],
        keywords=["invoice"],
        extensions=[".pdf"],
    )
    ordered = sort_rules_by_specificity([broad, medium, narrow])
    assert [r.id for r in ordered] == ["r3", "r2", "r1"]
    assert rule_specificity_score(narrow) > rule_specificity_score(medium) > rule_specificity_score(broad)

    # EXIF date token parsing from JPEG header bytes
    jpg = app_paths.downloads_dir / "IMG_001.jpg"
    jpg.write_bytes(b"\xff\xd8\xff\xe1\x00\x20Exif\x00\x002024:07:19 14:30:00\x00")
    dt = read_exif_date(jpg)
    assert dt == datetime(2024, 7, 19, 14, 30, 0)
    expanded = resolve_destination_tokens("Images/{exif_year}/{exif_month}", exif_date=dt)
    assert expanded == "Images/2024/07"


def test_architecture_snapshots_and_borrowed_rule_features(app_paths: AppPaths) -> None:
    # 1. Folder Architecture Snapshots save/list/load/delete
    arch = FolderArchitecture(
        tier_count=2,
        nodes=[
            ArchNode(id="n1", name="Work", tier=1, kind="doc"),
            ArchNode(id="n2", name="Invoices", tier=2, parent_id="n1", extensions=[".pdf"]),
        ],
    )
    snap = save_architecture_snapshot(arch, "Work Setup", app_paths.data_dir, note="Before cleanup")
    listed = list_architecture_snapshots(app_paths.data_dir)
    assert len(listed) == 1
    assert listed[0]["name"] == "Work Setup"

    restored = load_architecture_snapshot(snap["id"], app_paths.data_dir)
    assert restored is not None
    assert [n.name for n in restored.nodes] == ["Work", "Invoices"]
    assert delete_architecture_snapshot(snap["id"], app_paths.data_dir) is True
    assert list_architecture_snapshots(app_paths.data_dir) == []

    # 2. Exclude keywords, regex named capture groups, ALL match_mode, size/age bounds & rename template
    cfg = Config.with_starter_defaults()
    cfg.rules.insert(
        0,
        Rule(
            id="client_inv",
            name="Client Invoices",
            destination="Clients/{client}",
            regex_pattern=r"^(?P<client>[A-Za-z]+)_INV_\d+",
            exclude_keywords=["draft"],
            domains=["billing.example"],
            extensions=[".pdf"],
            match_mode="all",
            min_size_mb=0.5,
            min_age_days=2,
            rename_template="{client}_{stem_slug}.{ext}",
        ),
    )

    # Excluded by 'draft'
    d_draft = evaluate(
        FileCandidate(
            filename="Acme_INV_102_draft.pdf",
            source_url="https://billing.example/f",
            size_bytes=2 * 1024 * 1024,
            age_days=5.0,
        ),
        cfg,
    )
    assert d_draft.matched_rule != "Client Invoices"

    # Fails ALL mode when domain is missing
    d_no_domain = evaluate(
        FileCandidate(
            filename="Acme_INV_102.pdf",
            size_bytes=2 * 1024 * 1024,
            age_days=5.0,
        ),
        cfg,
    )
    assert d_no_domain.matched_rule != "Client Invoices"

    # Passes all conditions -> expands {client} destination & rename_template
    d_ok = evaluate(
        FileCandidate(
            filename="Acme_INV_102.pdf",
            source_url="https://billing.example/f",
            size_bytes=2 * 1024 * 1024,
            age_days=5.0,
        ),
        cfg,
    )
    assert d_ok.matched_rule == "Client Invoices"
    assert str(d_ok.relative_dest).replace("\\", "/") == "Clients/Acme/Acme_acme-inv-102.pdf"


def test_emblems_and_token_chip_bar_render_cleanly(v3_qapp) -> None:  # noqa: ANN001
    from PySide6.QtWidgets import QLineEdit
    from download_sorter.ui.emblems import (
        DetoxEmblemWidget,
        FolderScanRadarWidget,
        SnapshotEmblemWidget,
        TokenChipBar,
    )

    for w in (
        FolderScanRadarWidget(size=56, scanning=True),
        SnapshotEmblemWidget(size=48),
        DetoxEmblemWidget(size=48, active=True),
    ):
        w.resize(w.sizeHint())
        pm = w.grab()
        assert not pm.isNull()
        w.close()

    edit = QLineEdit()
    bar = TokenChipBar(edit)
    bar._insert_token("{stem_slug}")
    assert edit.text() == "{stem_slug}"
    bar.close()
