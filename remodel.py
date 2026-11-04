"""Staged Machine Remodel Engine for Download Sorter.

Offers a safe, staged remodel of messy machine file architectures (Downloads, Desktop,
Documents, or custom folders) with:
1. Deep protection for engineering/simulation data (SOLIDCast, MAGMASOFT, KiCad,
   Arduino, CAD assemblies, 14-day active folders, dev repos, and .download-sorter-keep).
2. 3-stage deduplication (size -> 64KB hash -> full SHA-256 + inode check) with
   smart keeper priority and quarantine.
3. Review cards (Organize / Keep intact / Skip).
4. 30-day quarantine (_Remodel Quarantine\\remodel_<id>\\).
5. Searchable 'Where did my files go.html' receipt in the remodeled root.
6. 1-click Undo and crash recovery via safe_ops write-ahead journal.
"""

from __future__ import annotations

import argparse
import copy
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import sys
import time
from typing import Any, Iterable

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

import about
try:
    import hold_manager
except ImportError:
    hold_manager = None
import safe_ops
import sorter

# ---------------------------------------------------------------------------
# Protection Signatures
# ---------------------------------------------------------------------------

PROTECTED_DIR_MARKERS: set[str] = {
    ".git", ".svn", ".hg", ".venv", "venv", "node_modules", ".cargo",
    ".rustup", ".obsidian", "__pycache__", ".idea", ".vscode", "build", "dist"
}

SOLIDCAST_EXTENSIONS: set[str] = {
    ".fsimodel9", ".fsimodel", ".afs", ".fsimaterials", ".gdproj",
    ".abin", ".lok"
}

SOLIDCAST_FILENAMES: set[str] = {
    "flowcast.set", "inuse.lok"
}

MAGMASOFT_EXTENSIONS: set[str] = {
    ".magma", ".magmainteract"
}

CAD_ASSEMBLY_EXTENSIONS: set[str] = {
    ".sldprt", ".sldasm", ".ipt", ".iam", ".f3d", ".step", ".stp",
    ".catpart", ".catproduct", ".prt", ".asm"
}

ELECTRONIC_CAD_EXTENSIONS: set[str] = {
    ".kicad_pro", ".kicad_pcb", ".kicad_sch", ".kicad_mod",
    ".ino", ".pde"
}

KEEP_MARKER_NAME: str = ".download-sorter-keep"
REMODEL_DIR_NAME: str = "remodels"
QUARANTINE_DIR_NAME: str = "_Remodel Quarantine"
RECEIPT_FILENAME: str = "Where did my files go.html"


def remodel_history_dir() -> Path:
    target = Path(sorter.LOG_DIR) / REMODEL_DIR_NAME
    target.mkdir(parents=True, exist_ok=True)
    return target


# ---------------------------------------------------------------------------
# File Index / Inode helper (Windows NTFS)
# ---------------------------------------------------------------------------

def get_file_identity(path: Path | str) -> tuple[int, int, int] | None:
    """Return (volume_serial, file_index_high, file_index_low) for NTFS hardlink detection."""
    if sys.platform != "win32":
        try:
            st = os.stat(path)
            return (st.st_dev, st.st_ino, 0)
        except OSError:
            return None

    try:
        class BY_HANDLE_FILE_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("dwFileAttributes", wintypes.DWORD),
                ("ftCreationTime", wintypes.FILETIME),
                ("ftLastAccessTime", wintypes.FILETIME),
                ("ftLastWriteTime", wintypes.FILETIME),
                ("dwVolumeSerialNumber", wintypes.DWORD),
                ("nFileSizeHigh", wintypes.DWORD),
                ("nFileSizeLow", wintypes.DWORD),
                ("nNumberOfLinks", wintypes.DWORD),
                ("nFileIndexHigh", wintypes.DWORD),
                ("nFileIndexLow", wintypes.DWORD),
            ]

        GENERIC_READ = 0x80000000
        FILE_SHARE_READ = 1
        FILE_SHARE_WRITE = 2
        OPEN_EXISTING = 3
        FILE_FLAG_BACKUP_SEMANTICS = 0x02000000

        handle = ctypes.windll.kernel32.CreateFileW(
            str(Path(path).resolve()),
            0,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            None,
            OPEN_EXISTING,
            FILE_FLAG_BACKUP_SEMANTICS,
            None,
        )
        if handle == -1 or handle == 0:
            return None
        info = BY_HANDLE_FILE_INFORMATION()
        ok = ctypes.windll.kernel32.GetFileInformationByHandle(handle, ctypes.byref(info))
        ctypes.windll.kernel32.CloseHandle(handle)
        if ok:
            return (info.dwVolumeSerialNumber, info.nFileIndexHigh, info.nFileIndexLow)
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Protection Inspection
# ---------------------------------------------------------------------------

@dataclass
class ProtectionCheck:
    is_protected: bool
    reason: str = ""
    rule_category: str = ""


def check_folder_protection(
    folder: Path,
    active_days: float = 14.0,
    root: Path | None = None,
    category_folders: Iterable[str] = (),
) -> ProtectionCheck:
    """Evaluate deep engineering and activity protection rules for a folder."""
    folder = folder.resolve()
    if not folder.exists() or not folder.is_dir():
        return ProtectionCheck(True, "Folder does not exist or is not a directory", "missing")

    # 1. Explicit keep marker
    if (folder / KEEP_MARKER_NAME).exists():
        return ProtectionCheck(True, f"Found explicit marker {KEEP_MARKER_NAME}", "keep_marker")

    # Check ancestors for keep marker
    if root:
        for parent in folder.parents:
            if (parent / KEEP_MARKER_NAME).exists():
                return ProtectionCheck(True, f"Ancestor contains {KEEP_MARKER_NAME}", "keep_marker")
            if parent == root.resolve():
                break

    # 2. VCS / Dev environment markers
    for part in folder.parts:
        if part in PROTECTED_DIR_MARKERS:
            return ProtectionCheck(True, f"Developer / VCS environment ({part})", "dev_env")

    # Check if folder name is already an organized category
    cat_names = {c.lower() for c in category_folders}
    if folder.name.lower() in cat_names:
        return ProtectionCheck(True, f"Already an organized category ({folder.name})", "organized_category")

    # 3. Inspect items within the folder (non-recursive shallow inspection for project signatures)
    try:
        entries = list(folder.iterdir())
    except (PermissionError, OSError) as exc:
        return ProtectionCheck(True, f"Inaccessible folder: {exc}", "permission")

    cad_count = 0
    solidcast_found = False
    magma_found = False
    kicad_found = False
    exe_found = False
    latest_mtime = 0.0

    for item in entries:
        try:
            st = item.stat()
            if st.st_mtime > latest_mtime:
                latest_mtime = st.st_mtime
        except OSError:
            pass

        name_lower = item.name.lower()
        ext_lower = item.suffix.lower()

        # SOLIDCast
        if ext_lower in SOLIDCAST_EXTENSIONS or name_lower in SOLIDCAST_FILENAMES or name_lower.startswith("$mesh"):
            solidcast_found = True
        # MAGMASOFT
        if ext_lower in MAGMASOFT_EXTENSIONS or "magmainteract" in name_lower:
            magma_found = True
        # KiCad / Arduino
        if ext_lower in ELECTRONIC_CAD_EXTENSIONS:
            kicad_found = True
        # Portable apps
        if ext_lower == ".exe" or item.name in ("App", "Data"):
            exe_found = True
        # CAD Assembly
        if ext_lower in CAD_ASSEMBLY_EXTENSIONS:
            cad_count += 1

    if solidcast_found:
        return ProtectionCheck(True, "SOLIDCast casting simulation model files detected", "solidcast")
    if magma_found:
        return ProtectionCheck(True, "MAGMASOFT casting simulation project files detected", "magmasoft")
    if kicad_found:
        return ProtectionCheck(True, "KiCad PCB or Arduino electronic design files detected", "electronics")
    if cad_count >= 2 or any(e.suffix.lower() in (".sldasm", ".iam", ".catproduct") for e in entries):
        return ProtectionCheck(True, f"CAD Assembly / Multi-part bundle ({cad_count} CAD files)", "cad_assembly")
    if exe_found:
        return ProtectionCheck(True, "Portable software application folder (.exe / App)", "portable_app")

    # 4. Activity Check (modified within active_days)
    if active_days > 0 and latest_mtime > 0:
        age_days = (time.time() - latest_mtime) / 86400.0
        if age_days <= active_days:
            return ProtectionCheck(
                True,
                f"Active folder (contains files modified {round(age_days, 1)} days ago <= {active_days}d)",
                "active_recent",
            )

    return ProtectionCheck(False, "", "unprotected")


# ---------------------------------------------------------------------------
# 3-Stage Deduplication Engine
# ---------------------------------------------------------------------------

def compute_fast_hash(path: Path, block_size: int = 65536) -> str:
    """First 64 KB hash for high-throughput candidate elimination."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        chunk = f.read(block_size)
        h.update(chunk)
    return h.hexdigest()


def compute_full_hash(path: Path) -> str:
    """Full SHA-256 hash."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(131072):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class DuplicateGroup:
    hash_val: str
    size: int
    keeper: Path
    duplicates: list[Path]


def is_in_protected_paths(p: Path, protected_paths: Iterable[Path]) -> bool:
    """Check if path is identical to or contained within any protected path."""
    try:
        p_res = p.resolve()
        for prot in protected_paths:
            prot_res = prot.resolve()
            if p_res == prot_res:
                return True
            try:
                p_res.relative_to(prot_res)
                return True
            except ValueError:
                pass
    except Exception:
        pass
    return False


def find_duplicates(
    files: list[Path],
    organized_destinations: Iterable[Path] = (),
    protected_paths: Iterable[Path] = (),
) -> list[DuplicateGroup]:
    """Execute 3-stage deduplication with smart keeper election and hardlink deduplication."""
    # Stage 1: Group by exact size
    by_size: dict[int, list[Path]] = {}
    for p in files:
        try:
            sz = p.stat().st_size
            if sz > 0:  # Skip zero-byte files from deduplication
                by_size.setdefault(sz, []).append(p)
        except OSError:
            pass

    size_candidates = {sz: plist for sz, plist in by_size.items() if len(plist) > 1}
    if not size_candidates:
        return []

    # Stage 2: Group by first 64KB hash
    by_fast_hash: dict[tuple[int, str], list[Path]] = {}
    for sz, plist in size_candidates.items():
        for p in plist:
            try:
                fh = compute_fast_hash(p)
                by_fast_hash.setdefault((sz, fh), []).append(p)
            except OSError:
                pass

    fast_candidates = {k: plist for k, plist in by_fast_hash.items() if len(plist) > 1}
    if not fast_candidates:
        return []

    # Stage 3: Full SHA-256 hash
    by_full_hash: dict[str, list[Path]] = {}
    for (sz, _fh), plist in fast_candidates.items():
        for p in plist:
            try:
                full = compute_full_hash(p)
                by_full_hash.setdefault(full, []).append(p)
            except OSError:
                pass

    org_set = {str(d.resolve()).lower() for d in organized_destinations}
    prot_list = list(protected_paths)

    groups: list[DuplicateGroup] = []
    for fhash, plist in by_full_hash.items():
        if len(plist) < 2:
            continue

        # Inode deduplication: collapse hardlinks sharing the same physical file on disk
        unique_nodes: list[Path] = []
        seen_inodes: set[tuple[int, int, int]] = set()
        for p in plist:
            ident = get_file_identity(p)
            if ident:
                if ident in seen_inodes:
                    continue
                seen_inodes.add(ident)
            unique_nodes.append(p)

        if len(unique_nodes) < 2:
            continue

        # Elect the keeper:
        # 1. Inside a protected folder (must remain intact)
        # 2. Inside an already organized category folder
        # 3. Oldest mtime
        def keeper_score(p: Path) -> tuple[int, float]:
            prot = is_in_protected_paths(p, prot_list)
            resolved_str = str(p.resolve()).lower()
            is_org = any(resolved_str.startswith(o) for o in org_set)
            tier = 0 if prot else (1 if is_org else 2)
            try:
                mtime = p.stat().st_mtime
            except OSError:
                mtime = 0.0
            return (tier, mtime)

        sorted_files = sorted(unique_nodes, key=keeper_score)
        keeper = sorted_files[0]
        # Never designate files inside protected paths as duplicates to quarantine!
        dupes = [p for p in sorted_files[1:] if not is_in_protected_paths(p, prot_list)]
        if not dupes:
            continue

        try:
            sz = keeper.stat().st_size
        except OSError:
            sz = 0

        groups.append(DuplicateGroup(
            hash_val=fhash,
            size=sz,
            keeper=keeper,
            duplicates=dupes,
        ))

    return groups


# ---------------------------------------------------------------------------
# Remodel Plan & Execution Models
# ---------------------------------------------------------------------------

@dataclass
class FolderCard:
    folder_path: Path
    action: str  # "organize", "keep_intact", "skip"
    file_count: int
    total_size_mb: float
    protection: ProtectionCheck
    preview_categories: dict[str, int] = field(default_factory=dict)


@dataclass
class RemodelPlan:
    remodel_id: str
    target_root: Path
    created_at: str
    cards: list[FolderCard]
    loose_files_to_organize: list[tuple[Path, Path]]  # (src, planned_dest)
    duplicates_to_quarantine: list[tuple[Path, Path, str]]  # (src, quarantine_dest, keeper_path)
    protected_items: list[tuple[Path, str]]  # (path, reason)
    total_files_scanned: int
    reclaimable_duplicate_bytes: int


# ---------------------------------------------------------------------------
# Remodel Engine Core
# ---------------------------------------------------------------------------

class RemodelEngine:
    def __init__(self, config: dict | None = None) -> None:
        self.config = config if config is not None else sorter.load_config()

    def scan(
        self,
        target_root: Path | str,
        scope: str = "downloads",
        active_folder_days: float | None = None,
        desktop_min_age_days: float | None = None,
    ) -> RemodelPlan:
        """Scan a target root and build a structured RemodelPlan."""
        root = Path(target_root).resolve()
        remodel_id = uuid_id = f"remodel_{time.strftime('%Y%m%d_%H%M%S')}_{os.getpid()}"
        created_at = datetime.now(timezone.utc).isoformat()

        cfg_remodel = self.config.get("remodel", {})
        act_days = active_folder_days if active_folder_days is not None else float(cfg_remodel.get("active_folder_days", 14))
        desk_days = desktop_min_age_days if desktop_min_age_days is not None else float(cfg_remodel.get("desktop_min_age_days", 30))

        cat_folders = sorter.collect_category_folders(self.config)
        cards: list[FolderCard] = []
        protected_items: list[tuple[Path, str]] = []
        loose_files: list[Path] = []
        all_scanned_files: list[Path] = []

        if not root.exists():
            return RemodelPlan(remodel_id, root, created_at, [], [], [], [], 0, 0)

        # 1. Inspect top-level subdirectories
        try:
            top_entries = list(root.iterdir())
        except (PermissionError, OSError) as exc:
            logging.error("Failed to read root directory %s: %s", root, exc)
            return RemodelPlan(remodel_id, root, created_at, [], [], [], [], 0, 0)

        for entry in top_entries:
            if entry.name.startswith("_Remodel Quarantine") or entry.name == ".batch_stage":
                continue

            if entry.is_dir():
                prot = check_folder_protection(entry, active_days=act_days, root=root, category_folders=cat_folders)
                if prot.is_protected:
                    protected_items.append((entry, prot.reason))
                    card_action = "keep_intact"
                else:
                    card_action = "organize"

                # Calculate folder statistics and category preview
                f_count = 0
                f_bytes = 0
                cat_preview: dict[str, int] = {}
                try:
                    for sub in entry.rglob("*"):
                        if sub.is_file() and not sub.name.startswith("~$") and sub.name != KEEP_MARKER_NAME:
                            f_count += 1
                            try:
                                f_bytes += sub.stat().st_size
                            except OSError:
                                pass
                            all_scanned_files.append(sub)
                            # Preview category
                            routed = sorter.evaluate_routing(sub.name, self.config, file_path=sub)
                            dest_name = Path(routed.get("destination", "Other")).parts[0] if routed.get("destination") else "Other"
                            cat_preview[dest_name] = cat_preview.get(dest_name, 0) + 1
                except (PermissionError, OSError):
                    pass

                cards.append(FolderCard(
                    folder_path=entry,
                    action=card_action,
                    file_count=f_count,
                    total_size_mb=round(f_bytes / (1024 * 1024), 2),
                    protection=prot,
                    preview_categories=cat_preview,
                ))

            elif entry.is_file():
                # Loose file in root
                name_lower = entry.name.lower()
                ext_lower = entry.suffix.lower()

                # Skip desktop shortcuts if scanning desktop
                if scope == "desktop":
                    if ext_lower in (".lnk", ".url", ".ini"):
                        continue
                    # Desktop age filter
                    try:
                        age_days = (time.time() - entry.stat().st_mtime) / 86400.0
                        if age_days < desk_days:
                            continue
                    except OSError:
                        continue

                if name_lower.startswith("~$") or name_lower == KEEP_MARKER_NAME or name_lower == RECEIPT_FILENAME.lower():
                    continue

                # Exclude actively held 3MF files from being organized
                if hold_manager is not None:
                    try:
                        if hold_manager.is_held(entry):
                            protected_items.append((entry, "Actively held in Downloads (3MF grace period)"))
                            continue
                    except Exception:
                        pass

                loose_files.append(entry)
                all_scanned_files.append(entry)

        # 2. Deduplication across all scanned files
        prot_paths = [p for p, _ in protected_items]
        dup_groups = find_duplicates(all_scanned_files, protected_paths=prot_paths)
        duplicates_to_quarantine: list[tuple[Path, Path, str]] = []
        reclaim_bytes = 0
        duplicate_paths: set[Path] = set()

        quarantine_base = root / QUARANTINE_DIR_NAME / remodel_id / "Duplicates"

        for grp in dup_groups:
            for dupe in grp.duplicates:
                if is_in_protected_paths(dupe, prot_paths):
                    continue
                duplicate_paths.add(dupe)
                reclaim_bytes += grp.size
                try:
                    rel_p = dupe.relative_to(root)
                except ValueError:
                    rel_p = Path(dupe.name)
                quarantine_target = quarantine_base / rel_p
                duplicates_to_quarantine.append((dupe, quarantine_target, str(grp.keeper)))

        # 3. Compute planned destination for loose files (excluding duplicates)
        loose_to_organize: list[tuple[Path, Path]] = []
        for lf in loose_files:
            if lf in duplicate_paths:
                continue
            routed = sorter.evaluate_routing(lf.name, self.config, file_path=lf)
            if routed.get("match_type") == "ignored":
                continue
            dest_dir = sorter.folder_path(root, routed.get("destination", "Other"))
            dest_target = dest_dir / lf.name
            loose_to_organize.append((lf, dest_target))

        return RemodelPlan(
            remodel_id=remodel_id,
            target_root=root,
            created_at=created_at,
            cards=cards,
            loose_files_to_organize=loose_to_organize,
            duplicates_to_quarantine=duplicates_to_quarantine,
            protected_items=protected_items,
            total_files_scanned=len(all_scanned_files),
            reclaimable_duplicate_bytes=reclaim_bytes,
        )

    def apply(
        self,
        plan: RemodelPlan,
        card_overrides: dict[str, str] | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Execute the remodel plan via safe_ops journal and produce HTML receipt + manifest."""
        card_actions = {str(c.folder_path): c.action for c in plan.cards}
        if card_overrides:
            card_actions.update(card_overrides)

        executed_moves: list[dict[str, Any]] = []
        skipped_items: list[dict[str, Any]] = []

        # 1. Quarantine duplicates
        for src, q_dest, keeper in plan.duplicates_to_quarantine:
            if dry_run:
                executed_moves.append({
                    "src": str(src),
                    "dest": str(q_dest),
                    "action": "quarantine_duplicate",
                    "keeper": keeper,
                    "status": "dry_run",
                })
            else:
                try:
                    final = safe_ops.quarantine(src, q_dest.parent, batch_id=plan.remodel_id)
                    executed_moves.append({
                        "src": str(src),
                        "dest": str(final),
                        "action": "quarantine_duplicate",
                        "keeper": keeper,
                        "status": "done",
                    })
                except Exception as exc:
                    logging.warning("Failed quarantining duplicate %s: %s", src, exc)
                    skipped_items.append({"path": str(src), "reason": f"Quarantine error: {exc}"})

        # 2. Organize loose files
        for src, planned in plan.loose_files_to_organize:
            if dry_run:
                executed_moves.append({
                    "src": str(src),
                    "dest": str(planned),
                    "action": "organize_loose",
                    "status": "dry_run",
                })
            else:
                try:
                    final = safe_ops.move(src, planned, batch_id=plan.remodel_id, settle_seconds=0.0)
                    executed_moves.append({
                        "src": str(src),
                        "dest": str(final),
                        "action": "organize_loose",
                        "status": "done",
                    })
                except Exception as exc:
                    logging.warning("Failed moving loose file %s: %s", src, exc)
                    skipped_items.append({"path": str(src), "reason": f"Move error: {exc}"})

        # 3. Process folder cards
        for card in plan.cards:
            folder_key = str(card.folder_path)
            action = card_actions.get(folder_key, card.action)

            if action == "keep_intact":
                skipped_items.append({
                    "path": folder_key,
                    "reason": f"Kept intact: {card.protection.reason or 'User opted to keep intact'}",
                })
                continue
            elif action == "skip":
                skipped_items.append({
                    "path": folder_key,
                    "reason": "User skipped folder",
                })
                continue

            # action == "organize"
            try:
                for sub in list(card.folder_path.rglob("*")):
                    if not sub.is_file() or sub.name.startswith("~$") or sub.name == KEEP_MARKER_NAME:
                        continue
                    # Skip files already quarantined as duplicates
                    if any(m["src"] == str(sub) for m in executed_moves):
                        continue

                    routed = sorter.evaluate_routing(sub.name, self.config, file_path=sub)
                    if routed.get("match_type") == "ignored":
                        continue
                    dest_dir = sorter.folder_path(plan.target_root, routed.get("destination", "Other"))
                    dest_target = dest_dir / sub.name

                    if dry_run:
                        executed_moves.append({
                            "src": str(sub),
                            "dest": str(dest_target),
                            "action": "organize_folder_item",
                            "status": "dry_run",
                        })
                    else:
                        try:
                            final = safe_ops.move(sub, dest_target, batch_id=plan.remodel_id, settle_seconds=0.0)
                            executed_moves.append({
                                "src": str(sub),
                                "dest": str(final),
                                "action": "organize_folder_item",
                                "status": "done",
                            })
                        except Exception as exc:
                            skipped_items.append({"path": str(sub), "reason": f"Move error: {exc}"})

                # Clean up empty parent directory if empty
                if not dry_run:
                    try:
                        for d in sorted(card.folder_path.rglob("*"), key=lambda p: len(p.parts), reverse=True):
                            if d.is_dir() and not any(d.iterdir()):
                                d.rmdir()
                        if not any(card.folder_path.iterdir()):
                            card.folder_path.rmdir()
                    except Exception:
                        pass
            except Exception as exc:
                logging.warning("Error organizing folder %s: %s", card.folder_path, exc)

        manifest = {
            "remodel_id": plan.remodel_id,
            "target_root": str(plan.target_root),
            "created_at": plan.created_at,
            "dry_run": dry_run,
            "author": about.CREDIT,
            "executed_moves": executed_moves,
            "skipped_items": skipped_items,
            "reclaimable_duplicate_bytes": plan.reclaimable_duplicate_bytes,
        }

        if not dry_run:
            # Write machine-readable manifest to %LOCALAPPDATA%\download-sorter\remodels\
            manifest_file = remodel_history_dir() / f"{plan.remodel_id}.json"
            manifest_file.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

            # Write Where did my files go.html receipt into the remodeled root
            receipt_path = plan.target_root / RECEIPT_FILENAME
            generate_html_receipt(manifest, receipt_path)

        return manifest

    def undo(self, remodel_id: str | None = None) -> dict[str, Any]:
        """Undo a completed remodel, restoring all files to their exact original locations."""
        h_dir = remodel_history_dir()
        if remodel_id:
            m_file = h_dir / f"{remodel_id}.json"
        else:
            candidates = sorted(h_dir.glob("remodel_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
            if not candidates:
                return {"ok": False, "message": "No remodel history found to undo."}
            m_file = candidates[0]

        if not m_file.exists():
            return {"ok": False, "message": f"Remodel manifest {m_file.name} not found."}

        try:
            manifest = json.loads(m_file.read_text(encoding="utf-8"))
        except Exception as exc:
            return {"ok": False, "message": f"Corrupted manifest: {exc}"}

        moves = manifest.get("executed_moves", [])
        restored_count = 0
        failed_count = 0

        for item in reversed(moves):
            src_orig = Path(item["src"])
            dest_current = Path(item["dest"])

            if not dest_current.exists():
                failed_count += 1
                continue

            try:
                # Use safe_ops.restore to bring file back safely
                entry = {"src": str(src_orig), "dest": str(dest_current), "op": "move"}
                restored = safe_ops.restore(entry, settle_seconds=0.0)
                if restored:
                    restored_count += 1
                else:
                    failed_count += 1
            except Exception as exc:
                logging.error("Failed restoring %s: %s", dest_current, exc)
                failed_count += 1

        # Clean up HTML receipt if present
        target_root = Path(manifest.get("target_root", ""))
        receipt_file = target_root / RECEIPT_FILENAME
        if receipt_file.exists():
            try:
                receipt_file.unlink()
            except Exception:
                pass

        # Clean empty quarantine directory if empty
        q_dir = target_root / QUARANTINE_DIR_NAME / manifest.get("remodel_id", "")
        if q_dir.exists():
            try:
                shutil.rmtree(q_dir, ignore_errors=True)
            except Exception:
                pass

        manifest["undone_at"] = datetime.now(timezone.utc).isoformat()
        manifest["restored_count"] = restored_count
        m_file.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        return {
            "ok": True,
            "remodel_id": manifest.get("remodel_id"),
            "restored_count": restored_count,
            "failed_count": failed_count,
            "message": f"Restored {restored_count} file(s) back to their original locations.",
        }


# ---------------------------------------------------------------------------
# HTML Receipt Generator (Self-Contained & Searchable)
# ---------------------------------------------------------------------------

def generate_html_receipt(manifest: dict[str, Any], output_path: Path | str) -> None:
    """Generate a clean, searchable HTML receipt with Devyn Personal Suite styling."""
    moves = manifest.get("executed_moves", [])
    skipped = manifest.get("skipped_items", [])
    remodel_id = manifest.get("remodel_id", "remodel")
    date_str = manifest.get("created_at", time.ctime())
    author = manifest.get("author", about.CREDIT)

    rows_html = []
    for m in moves:
        src = m.get("src", "")
        dest = m.get("dest", "")
        action = m.get("action", "")
        if action == "quarantine_duplicate":
            badge = '<span class="badge badge-warning">Duplicate Quarantined</span>'
        else:
            badge = '<span class="badge badge-success">Organized</span>'
        rows_html.append(f"""
        <tr>
            <td>{badge}</td>
            <td class="file-path">{src}</td>
            <td class="file-path">{dest}</td>
        </tr>
        """)

    for s in skipped:
        p = s.get("path", "")
        r = s.get("reason", "Protected")
        rows_html.append(f"""
        <tr>
            <td><span class="badge badge-info">Protected</span></td>
            <td class="file-path">{p}</td>
            <td class="file-path text-muted">Kept untouched: {r}</td>
        </tr>
        """)

    tbody = "\n".join(rows_html)

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Where Did My Files Go? — Download Sorter Remodel Receipt</title>
<style>
    :root {{
        --bg: #0B0D12;
        --surface: #171B24;
        --surface-hover: #1D222C;
        --border: #2A303B;
        --text: #EDF1F8;
        --text-muted: #8B94A7;
        --accent: #69E6AD;
        --success: #69E6AD;
        --warning: #F5AD62;
        --font-mono: "Cascadia Code", Consolas, monospace;
    }}
    body {{
        margin: 0;
        padding: 32px;
        background: var(--bg);
        color: var(--text);
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
        line-height: 1.5;
    }}
    .container {{
        max-width: 1100px;
        margin: 0 auto;
    }}
    header {{
        border-bottom: 1px solid var(--border);
        padding-bottom: 20px;
        margin-bottom: 24px;
    }}
    h1 {{
        margin: 0 0 8px 0;
        font-size: 26px;
        color: var(--accent);
    }}
    .meta {{
        color: var(--text-muted);
        font-size: 13px;
    }}
    .notice {{
        background: var(--surface);
        border-left: 4px solid var(--accent);
        padding: 16px;
        border-radius: 4px;
        margin-bottom: 24px;
        font-size: 14px;
    }}
    .search-box {{
        width: 100%;
        box-sizing: border-box;
        padding: 12px 16px;
        font-size: 15px;
        background: var(--surface);
        border: 1px solid var(--border);
        border-radius: 6px;
        color: var(--text);
        margin-bottom: 20px;
    }}
    .search-box:focus {{
        outline: none;
        border-color: var(--accent);
    }}
    table {{
        width: 100%;
        border-collapse: collapse;
        font-size: 13px;
        background: var(--surface);
        border-radius: 6px;
        overflow: hidden;
    }}
    th, td {{
        padding: 12px 16px;
        text-align: left;
        border-bottom: 1px solid var(--border);
    }}
    th {{
        background: #12151C;
        color: var(--text-muted);
        font-weight: 600;
        text-transform: uppercase;
        font-size: 11px;
        letter-spacing: 0.5px;
    }}
    tr:hover {{
        background: var(--surface-hover);
    }}
    .file-path {{
        font-family: var(--font-mono);
        word-break: break-all;
    }}
    .badge {{
        display: inline-block;
        padding: 3px 8px;
        border-radius: 4px;
        font-size: 11px;
        font-weight: 600;
        text-transform: uppercase;
    }}
    .badge-success {{ background: rgba(105, 230, 173, 0.15); color: var(--success); }}
    .badge-warning {{ background: rgba(245, 173, 98, 0.15); color: var(--warning); }}
    .badge-info {{ background: rgba(105, 230, 173, 0.15); color: var(--accent); }}
    .text-muted {{ color: var(--text-muted); }}
    footer {{
        margin-top: 40px;
        padding-top: 20px;
        border-top: 1px solid var(--border);
        text-align: center;
        font-size: 12px;
        color: var(--text-muted);
    }}
</style>
</head>
<body>
<div class="container">
    <header>
        <h1>Where Did My Files Go?</h1>
        <div class="meta">
            Remodel ID: <strong>{remodel_id}</strong> · Executed: <strong>{date_str}</strong>
        </div>
    </header>

    <div class="notice">
        <strong>Need to undo this remodel?</strong> You can restore every file to its exact original location at any time by running:
        <br><code style="background:var(--bg); border:1px solid var(--border); padding:3px 6px; border-radius:3px; font-family:var(--font-mono);">download-sorter-remodel --undo {remodel_id}</code> or opening the Rules Manager &gt; Machine Remodel tab.
    </div>

    <input type="text" id="searchInput" class="search-box" placeholder="Search filenames, paths, or actions..." onkeyup="filterTable()">

    <table id="receiptTable">
        <thead>
            <tr>
                <th style="width: 150px;">Status</th>
                <th>Original Location</th>
                <th>New Location</th>
            </tr>
        </thead>
        <tbody>
            {tbody}
        </tbody>
    </table>

    <footer>
        Download Sorter v{about.__version__} · {about.CREDIT}
    </footer>
</div>

<script>
function filterTable() {{
    var input = document.getElementById("searchInput");
    var filter = input.value.toLowerCase();
    var table = document.getElementById("receiptTable");
    var tr = table.getElementsByTagName("tr");

    for (var i = 1; i < tr.length; i++) {{
        var text = tr[i].innerText.toLowerCase();
        tr[i].style.display = text.indexOf(filter) > -1 ? "" : "none";
    }}
}}
</script>
</body>
</html>"""

    Path(output_path).write_text(html_content, encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI Entrypoint
# ---------------------------------------------------------------------------

def main() -> int:
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="Staged Machine Remodel Engine for messy PCs.")
    parser.add_argument("-V", "--version", action="version", version=about.version_string())
    parser.add_argument("target", nargs="?", default="", help="Target folder root to remodel (default: Downloads).")
    parser.add_argument("--scan", action="store_true", help="Scan and display candidate folder cards without touching disk.")
    parser.add_argument("--dry-run", action="store_true", help="Simulate remodel actions and output summary.")
    parser.add_argument("--apply", action="store_true", help="Execute the remodel safely.")
    parser.add_argument("--undo", nargs="?", const="", help="Undo a previous remodel by ID (or the latest remodel if no ID specified).")
    parser.add_argument("--scope", choices=["downloads", "desktop", "documents", "custom"], default="downloads", help="Scope of the remodel.")
    args = parser.parse_args()

    engine = RemodelEngine()

    if args.undo is not None:
        target_id = args.undo if args.undo != "" else None
        res = engine.undo(target_id)
        if res.get("ok"):
            print(f"✓ Remodel Undo Successful: {res['message']}")
            return 0
        else:
            print(f"✕ Undo Failed: {res.get('message')}", file=sys.stderr)
            return 1

    target_path = Path(args.target).resolve() if args.target else sorter.watch_root(engine.config)

    print(f"\n[Scanning {args.scope.upper()}] {target_path} ...")
    plan = engine.scan(target_path, scope=args.scope)

    print(f"\nScan Complete: {plan.total_files_scanned} files inspected across {len(plan.cards)} subfolders.")
    print("-" * 65)

    if plan.cards:
        print("FOLDER REVIEW CARDS:")
        for c in plan.cards:
            status_tag = f"[{c.action.upper()}]"
            prot_desc = f" ({c.protection.reason})" if c.protection.is_protected else ""
            print(f"  • {c.folder_path.name:<25} {c.file_count:>4} files ({c.total_size_mb:>6.1f} MB) {status_tag}{prot_desc}")

    if plan.duplicates_to_quarantine:
        dupe_mb = round(plan.reclaimable_duplicate_bytes / (1024 * 1024), 2)
        print(f"\nDEDUPLICATION NOTICE:")
        print(f"  {len(plan.duplicates_to_quarantine)} redundant duplicate file(s) found ({dupe_mb} MB reclaimable).")
        print(f"  Target quarantine: {target_path / QUARANTINE_DIR_NAME / plan.remodel_id}")

    if plan.loose_files_to_organize:
        print(f"\nLOOSE ROOT FILES:")
        print(f"  {len(plan.loose_files_to_organize)} loose file(s) will be organized into category subfolders.")

    if args.scan:
        print("\nScan completed. Run with --apply to execute or open Rules Manager > Machine Remodel.\n")
        return 0

    if args.apply or args.dry_run:
        print(f"\n{'[SIMULATING REMODEL]' if args.dry_run else '[EXECUTING REMODEL]'} ...")
        receipt = engine.apply(plan, dry_run=args.dry_run)
        executed = len(receipt.get("executed_moves", []))
        skipped = len(receipt.get("skipped_items", []))
        print(f"✓ Completed: {executed} file move(s) recorded, {skipped} item(s) protected.")
        if not args.dry_run:
            print(f"  HTML Receipt: {target_path / RECEIPT_FILENAME}")
            print(f"  Undo Command: download-sorter-remodel --undo {plan.remodel_id}")
        return 0

    print("\nNo action flag specified. Use --scan, --dry-run, or --apply.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
