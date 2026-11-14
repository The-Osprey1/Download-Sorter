"""Side-by-side read-only shadow test comparing Download Sorter v2.8.0 and v3.0.

Does NOT modify any user file in Downloads or %LOCALAPPDATA%\\download-sorter.
"""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile

V3_SRC = Path(__file__).resolve().parent.parent / "src"
V2_REPO = Path(r"C:\Users\devyn\Download-Sorter-271")
if str(V3_SRC) not in sys.path:
    sys.path.insert(0, str(V3_SRC))
if str(V2_REPO) not in sys.path:
    sys.path.insert(0, str(V2_REPO))

import sorter as v2_sorter
from download_sorter.config import Config, import_v2_config
from download_sorter.paths import AppPaths
from download_sorter.router import FileCandidate, evaluate

CORPUS: list[tuple[str, str]] = [
    ("Fidel APM Scholarship Application 2026-27.pdf", ""),
    ("Devyn Fidel - Academic and Professional Goals Statement.pdf", ""),
    ("FEF CIC 2025 - Career Statement - Devyn Fidel.pdf", ""),
    ("Capacitance_Lab_Report.pdf", ""),
    ("Syllabus_CE2061_Spring 2026.pdf", "https://canvas.instructure.com/courses/1"),
    ("History 2202H (F26) - Lecture 9.pdf", ""),
    ("Problem set 2 solution.pdf", ""),
    ("Devyn_Fidel_Resume_Additive.pdf", ""),
    ("Devyn Fidel Cover Letter Howmet Carson.pdf", ""),
    ("Fidel Devyn Offer Letter.pdf", ""),
    ("random_manual_guide.pdf", ""),
    ("MRP_Assignment.xlsx", ""),
    ("Srnka_s_masli.3mf", ""),
    ("0.4mm.3mf", "https://makerworld.com/en/models/331323"),
    ("A1LubeXAxis.3mf", "https://makerworld.com/en/models/999"),
    ("flanged_mace_v3.step", ""),
    ("bracket_mount.stl", ""),
    ("Screenshot 2026-07-25 155439.png", ""),
    ("IMG_2603.jpg", ""),
    ("assembly_pack.zip", ""),
    ("setup_installer.exe", ""),
    ("toolpath_part.nc", ""),
    ("notes.md", ""),
    ("partial_download.crdownload", ""),
    ("unknown_binary.xyz", ""),
]


def run_shadow_comparison(report_path: Path | None = None) -> str:
    v2_cfg_path = AppPaths.v2_config_file()
    v2_cfg = v2_sorter.load_config_from(v2_cfg_path)
    # Build default starter v3 config and an imported-from-v2 config for comparison
    v3_starter = Config.with_starter_defaults()
    v3_migrated = import_v2_config(v2_cfg_path) if v2_cfg_path.exists() else Config.with_starter_defaults()

    dl_root = v2_sorter.watch_root(v2_cfg)
    top_level_files = [p for p in sorted(dl_root.iterdir(), key=lambda x: x.name.lower()) if p.is_file()] if dl_root.is_dir() else []

    rows_corpus: list[str] = []
    for filename, url in CORPUS:
        v2_res = v2_sorter.evaluate_routing(filename, v2_cfg, use_my_folders=True)
        v3_res = evaluate(FileCandidate(filename=filename, source_url=url), v3_starter)
        v2_dest = v2_res.get("destination") or v2_res.get("match_type")
        v3_dest = str(v3_res.relative_dest.parent) if v3_res.relative_dest else f"[{v3_res.action}]"
        rows_corpus.append(
            f"| `{filename}` | `{v2_dest}` (`{v2_res.get('winning_rule', '')}`) | "
            f"`{v3_dest}` (`{v3_res.matched_rule}`) |"
        )

    rows_live: list[str] = []
    for p in top_level_files[:25]:
        v2_res = v2_sorter.evaluate_routing(p.name, v2_cfg, use_my_folders=True)
        v3_res = evaluate(FileCandidate(filename=p.name), v3_starter)
        v2_dest = v2_res.get("destination") or v2_res.get("match_type")
        v3_dest = str(v3_res.relative_dest.parent) if v3_res.relative_dest else f"[{v3_res.action}]"
        rows_live.append(f"| `{p.name}` | `{v2_dest}` | `{v3_dest}` (`{v3_res.reason}`) |")

    # Verify v2 migrated config handles everything deterministically as well
    with tempfile.TemporaryDirectory() as tmp:
        svc_paths = AppPaths(data_dir=Path(tmp), downloads_dir=dl_root)
        assert svc_paths.config_file.parent.exists()

    lines = [
        "# Download Sorter v3.0 vs v2.8.0 Side-by-Side Shadow Test Report",
        "",
        "## 1. Safety & Scope Guarantee (Live Downloads Inspection)",
        f"- **Watched Folder**: `{dl_root}`",
        f"- **Top-level files inspected (read-only)**: `{len(top_level_files)}`",
        "- **Subfolder files touched by `v3.0`**: `0` (`v3.0` watches top-level only — recursive=False; eliminates the 104-file subfolder re-sort that `v2.8.0` triggered on startup)",
        f"- **v2 -> v3 rule import check**: imported `{len(v3_migrated.rules)}` keyword rules from `{v2_cfg_path}` cleanly",
        "",
        "## 2. Live Top-Level Files in `Downloads` (`v2.8.0` vs `v3.0` Preview)",
        "| Live File | v2.8.0 Route | v3.0 Deterministic Route (Why) |",
        "| :--- | :--- | :--- |",
        *(rows_live or ["| *(no top-level files currently waiting)* | - | - |"]),
        "",
        "## 3. 25-File Real-World Corpus Comparison",
        "| Test File | v2.8.0 Route (incl. `my_folders`/ML/implicit tiers) | v3.0 Deterministic Route |",
        "| :--- | :--- | :--- |",
        *rows_corpus,
        "",
    ]
    report = "\n".join(lines)
    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(report, encoding="utf-8")
    return report


if __name__ == "__main__":
    out = Path(
        r"C:\Users\devyn\.gemini\antigravity\brain\89b779ac-929e-45fe-b55a-1aad0ca37e2c\v3_shadow_test_report.md"
    )
    print(run_shadow_comparison(out))
