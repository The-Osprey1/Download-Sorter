"""Tests for automatic multi-file project detection and Folder Architecture UI integration."""

from __future__ import annotations

from pathlib import Path

from download_sorter.architecture import FolderArchitecture, apply_architecture_to_config
from download_sorter.config import Config, save_config
from download_sorter.paths import AppPaths
from download_sorter.project_detect import detect_projects_in_folder
from download_sorter.router import FileCandidate, evaluate
from download_sorter.service import SorterService
from download_sorter.ui.architecture_canvas import ArchitectureTab, DetectProjectsDialog


def _touch(path: Path, data: bytes = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_detect_projects_cross_extension_absorption_and_anti_noise(app_paths: AppPaths) -> None:
    cad = app_paths.downloads_dir / "CAD"
    # Project 1: Horsemans Axe across .f3d, .step, .sldprt (including variant spelling Horseman)
    _touch(cad / "Fusion 360" / "Horsemans Axe Head V26.f3d")
    _touch(cad / "Fusion 360" / "Horsemans Axe Pommel.f3d")
    _touch(cad / "STEP" / "Horsemans Axe Shaft Pattern.step")
    _touch(cad / "STEP" / "Horseman Head.step")
    _touch(cad / "SolidWorks" / "horseman_ax_profile_rev16.sldprt")

    # Project 2: Compact/spaced variant merging (DriftPad + Drift Pad)
    _touch(cad / "Fusion 360" / "DriftPadCase V3.f3d")
    _touch(cad / "Fusion 360" / "DriftPadPCB V2.f3d")
    _touch(cad / "STEP" / "Drift Pad Top V2.step")

    # Project 3: Structured engineering code (26-K&C)
    _touch(cad / "STEP" / "26-K&C-Wheel Mount.step")
    _touch(cad / "STEP" / "26-K&C-String Pot Bracket.step")
    _touch(cad / "STEP" / "26-K&C-Suspension Arm.step")

    # Noise: date inside filename must NOT become a code project ("12-14" or "14-24REV")
    _touch(cad / "SolidWorks" / "Blade2-12-14-24Rev.sldprt")
    _touch(cad / "SolidWorks" / "Blade2-12-14-24Rev (1).sldprt")
    _touch(cad / "SolidWorks" / "Blade2-12-14-24Rev (2).sldprt")

    # Noise: generic single nouns across unrelated items must NOT become standalone projects
    _touch(cad / "STEP" / "Spool Holder.step")
    _touch(cad / "STEP" / "Mouse Holder.step")
    _touch(cad / "STEP" / "Tool Holder.step")

    # Noise: pure numbered copies of a single file (only 1 distinct base stem)
    _touch(cad / "STEP" / "Clock Stand.step")
    _touch(cad / "STEP" / "Clock Stand (1).step")
    _touch(cad / "STEP" / "Clock Stand (2).step")
    _touch(cad / "STEP" / "Clock Stand (3).step")

    projects = detect_projects_in_folder(cad, min_files=3)
    by_name = {p.name.lower(): p for p in projects}

    assert "horsemans axe" in by_name
    axe = by_name["horsemans axe"]
    assert axe.file_count == 5
    assert set(axe.extensions) == {".f3d", ".sldprt", ".step"}

    assert any("drift" in n for n in by_name)
    drift = next(p for n, p in by_name.items() if "drift" in n)
    assert drift.file_count == 3

    assert "26-k&c" in by_name
    assert by_name["26-k&c"].anchor_kind == "code"
    assert by_name["26-k&c"].file_count == 3

    # Anti-noise assertions
    assert not any("12-14" in n or "24rev" in n for n in by_name)
    assert "holder" not in by_name
    assert "clock stand" not in by_name

    # Verified keywords actually route files when compiled into Config
    arch = FolderArchitecture(tier_count=2)
    cad_node = arch.add_premade_node("t1_cad", tier=1)
    assert cad_node is not None
    arch.add_premade_node("t2_step", tier=2, parent_id=cad_node.id)
    arch.add_custom_node(
        name=axe.name,
        tier=2,
        parent_id=cad_node.id,
        extensions=list(axe.extensions),
        keywords=list(axe.keywords),
    )
    cfg = apply_architecture_to_config(arch, Config.new_install())
    d1 = evaluate(FileCandidate("Horsemans Axe Head V27.f3d"), cfg)
    assert str(d1.relative_dest).replace("\\", "/") == f"CAD/{axe.name}/Horsemans Axe Head V27.f3d"
    d2 = evaluate(FileCandidate("Random_Part.step"), cfg)
    assert str(d2.relative_dest).replace("\\", "/") == "CAD/STEP/Random_Part.step"


def test_detect_projects_respects_exclude_subdirs(app_paths: AppPaths) -> None:
    imgs = app_paths.downloads_dir / "Images"
    # Existing classification subfolder should be skipped when listed in exclude_subdirs
    for name in ["Alpha Logo.png", "Beta Logo.png", "Gamma Logo.png", "Delta Logo.png"]:
        _touch(imgs / "Logos & Icons" / name)
    # Real project outside excluded subfolder
    for name in ["Fruity Balance.png", "Fruity Chorus.png", "Fruity Flanger.png", "Fruity Limiter.png"]:
        _touch(imgs / name)

    projs = detect_projects_in_folder(imgs, min_files=3, exclude_subdirs={"logos & icons"})
    names = {p.name.lower() for p in projs}
    assert "fruity" in names
    assert "logo" not in names


def test_detect_projects_dialog_and_architecture_tab_integration(v3_qapp, app_paths: AppPaths) -> None:
    cfg = Config.new_install()
    save_config(cfg, app_paths)

    cad = app_paths.downloads_dir / "CAD"
    _touch(cad / "STEP" / "Flanged Mace Iteration 1.step")
    _touch(cad / "STEP" / "Flanged Mace Iteration 2.step")
    _touch(cad / "Fusion 360" / "Flanged Mace Gating.f3d")

    svc = SorterService(app_paths)
    tab = ArchitectureTab(svc)
    cad_node = next((n for n in tab._arch.nodes_in_tier(1) if n.name.lower() == "cad"), None)
    assert cad_node is not None
    tab.canvas.select_node(cad_node.id)
    assert tab.btn_detect_projects.isEnabled()

    projs = detect_projects_in_folder(cad, min_files=3)
    assert len(projs) == 1
    assert projs[0].name == "Flanged Mace"

    dlg = DetectProjectsDialog(
        cad_node,
        cad,
        projs,
        exclude_subdirs=set(),
        existing_child_names=set(),
        min_files=3,
        parent=tab,
    )
    chosen = dlg.selected_projects()
    assert len(chosen) == 1
    assert chosen[0]["name"] == "Flanged Mace"

    added = tab.apply_detected_projects(cad_node.id, chosen)
    assert len(added) == 1
    assert any(c.name == "Flanged Mace" for c in tab._arch.children_of(cad_node.id))

    # Undo removes the added project cleanly
    tab.btn_diag_undo.click()
    assert not any(c.name == "Flanged Mace" for c in tab._arch.children_of(cad_node.id))
    dlg.close()
    tab.close()
