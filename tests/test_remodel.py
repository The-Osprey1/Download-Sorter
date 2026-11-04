"""Unit and integration tests for Staged Machine Remodel Engine."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from unittest.mock import patch

import about
import remodel
import safe_ops
import sorter


class TestMachineRemodel(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.sandbox = Path(self.temp_dir.name).resolve()
        self.config = sorter.get_default_config()
        self.config["downloads_dir"] = str(self.sandbox)
        self.config["watch"] = {"folder": str(self.sandbox), "settle_seconds": 0, "min_age_minutes": 0}
        self.engine = remodel.RemodelEngine(self.config)

    def tearDown(self) -> None:
        # Clean up any remodel manifests created during test
        h_dir = remodel.remodel_history_dir()
        for f in h_dir.glob("remodel_*.json"):
            try:
                f.unlink()
            except Exception:
                pass
        self.temp_dir.cleanup()

    def _build_messy_sandbox(self) -> dict[str, Path]:
        paths: dict[str, Path] = {}

        # 1. Loose files
        p_pdf = self.sandbox / "Annual_Report.pdf"
        p_pdf.write_text("dummy pdf report")
        paths["loose_pdf"] = p_pdf

        p_installer = self.sandbox / "VSCodeSetup.exe"
        p_installer.write_text("installer bytes")
        paths["loose_exe"] = p_installer

        # 2. Duplicate file in two places
        dup_content = "identical content in two locations" * 100
        p_dup1 = self.sandbox / "Document_Copy1.txt"
        p_dup1.write_text(dup_content)
        paths["dup1"] = p_dup1

        p_sub = self.sandbox / "Messy_Folder"
        p_sub.mkdir()
        p_dup2 = p_sub / "Document_Copy2.txt"
        p_dup2.write_text(dup_content)
        paths["dup2"] = p_dup2

        p_img = p_sub / "Vacation_Photo.png"
        p_img.write_text("png image data")
        paths["messy_img"] = p_img

        # Set older mtime so Messy_Folder is not protected by recent activity
        old_time = time.time() - (30 * 86400)
        os.utime(p_dup1, (old_time, old_time))
        os.utime(p_dup2, (old_time, old_time))
        os.utime(p_img, (old_time, old_time))

        # 3. SOLIDCast Simulation Folder
        sc_dir = self.sandbox / "Foundry_Turbine_Sim"
        sc_dir.mkdir()
        (sc_dir / "turbine.fsimodel9").write_text("solidcast model")
        (sc_dir / "turbine.afs").write_text("solidcast afs")
        (sc_dir / "FLOWCast.set").write_text("flowcast settings")
        paths["solidcast_dir"] = sc_dir

        # 4. MAGMASOFT Simulation Folder
        magma_dir = self.sandbox / "Engine_Block_Casting"
        magma_dir.mkdir()
        (magma_dir / "engine.magma").write_text("magma project")
        paths["magma_dir"] = magma_dir

        # 5. Git Dev Repository
        git_dir = self.sandbox / "My_Rust_App"
        (git_dir / ".git").mkdir(parents=True)
        (git_dir / "main.rs").write_text("fn main() {}")
        paths["git_dir"] = git_dir

        # 6. Explicit Keep Marker Folder
        keep_dir = self.sandbox / "Permanent_Archive"
        keep_dir.mkdir()
        (keep_dir / remodel.KEEP_MARKER_NAME).write_text("keep this folder")
        (keep_dir / "important.doc").write_text("important")
        paths["keep_dir"] = keep_dir

        # 7. Recent Active Project Folder (modified 2 days ago)
        active_dir = self.sandbox / "Active_Coursework"
        active_dir.mkdir()
        p_active = active_dir / "homework.pdf"
        p_active.write_text("active homework")
        recent_time = time.time() - (2 * 86400)
        os.utime(p_active, (recent_time, recent_time))
        paths["active_dir"] = active_dir

        return paths

    def test_protection_rules_recognition(self) -> None:
        paths = self._build_messy_sandbox()
        plan = self.engine.scan(self.sandbox)

        self.assertGreater(plan.total_files_scanned, 5)

        # Map cards by folder name
        card_map = {c.folder_path.name: c for c in plan.cards}

        # SOLIDCast protected
        self.assertIn("Foundry_Turbine_Sim", card_map)
        self.assertTrue(card_map["Foundry_Turbine_Sim"].protection.is_protected)
        self.assertIn("SOLIDCast", card_map["Foundry_Turbine_Sim"].protection.reason)
        self.assertEqual(card_map["Foundry_Turbine_Sim"].action, "keep_intact")

        # MAGMASOFT protected
        self.assertIn("Engine_Block_Casting", card_map)
        self.assertTrue(card_map["Engine_Block_Casting"].protection.is_protected)
        self.assertIn("MAGMASOFT", card_map["Engine_Block_Casting"].protection.reason)

        # Git Repo protected
        self.assertIn("My_Rust_App", card_map)
        self.assertTrue(card_map["My_Rust_App"].protection.is_protected)

        # Explicit keep marker protected
        self.assertIn("Permanent_Archive", card_map)
        self.assertTrue(card_map["Permanent_Archive"].protection.is_protected)

        # Active recent folder (<14d) protected
        self.assertIn("Active_Coursework", card_map)
        self.assertTrue(card_map["Active_Coursework"].protection.is_protected)
        self.assertIn("Active folder", card_map["Active_Coursework"].protection.reason)

        # Messy_Folder should be an organize candidate
        self.assertIn("Messy_Folder", card_map)
        self.assertFalse(card_map["Messy_Folder"].protection.is_protected)
        self.assertEqual(card_map["Messy_Folder"].action, "organize")

    def test_3_stage_deduplication(self) -> None:
        self._build_messy_sandbox()
        plan = self.engine.scan(self.sandbox)

        # Should identify 1 duplicate to quarantine
        self.assertEqual(len(plan.duplicates_to_quarantine), 1)
        src, q_dest, keeper = plan.duplicates_to_quarantine[0]
        self.assertIn("Document_Copy", src.name)
        self.assertIn(remodel.QUARANTINE_DIR_NAME, str(q_dest))
        self.assertTrue(Path(keeper).exists())

    def test_remodel_apply_and_receipt(self) -> None:
        self._build_messy_sandbox()
        plan = self.engine.scan(self.sandbox)
        manifest = self.engine.apply(plan)

        self.assertIsNotNone(manifest)
        self.assertEqual(manifest["remodel_id"], plan.remodel_id)

        # Check that loose files were organized into category folders
        doc_folder = self.sandbox / "Documents"
        installer_folder = self.sandbox / "Installers"
        self.assertTrue((doc_folder / "PDFs" / "Annual_Report.pdf").exists() or (doc_folder / "Annual_Report.pdf").exists() or (self.sandbox / "Academics" / "Annual_Report.pdf").exists())
        self.assertTrue((installer_folder / "VSCodeSetup.exe").exists())

        # Check duplicate quarantine
        q_root = self.sandbox / remodel.QUARANTINE_DIR_NAME / plan.remodel_id / "Duplicates"
        self.assertTrue(q_root.exists())

        # Check HTML receipt
        receipt = self.sandbox / "Where did my files go.html"
        self.assertTrue(receipt.exists())
        content = receipt.read_text(encoding="utf-8")
        self.assertIn("Where Did My Files Go?", content)
        self.assertIn(about.CREDIT, content)
        self.assertIn(plan.remodel_id, content)

        # Check protected items were kept intact
        sc_file = self.sandbox / "Foundry_Turbine_Sim" / "turbine.fsimodel9"
        self.assertTrue(sc_file.exists())
        git_file = self.sandbox / "My_Rust_App" / "main.rs"
        self.assertTrue(git_file.exists())

    def test_1_click_undo_restores_original_tree(self) -> None:
        paths = self._build_messy_sandbox()
        plan = self.engine.scan(self.sandbox)
        self.engine.apply(plan)

        # Loose PDF was moved
        self.assertFalse(paths["loose_pdf"].exists())

        # Undo the remodel
        undo_res = self.engine.undo(plan.remodel_id)
        self.assertTrue(undo_res.get("ok"))
        self.assertGreater(undo_res.get("restored_count", 0), 0)

        # Original files restored to original locations!
        self.assertTrue(paths["loose_pdf"].exists())
        self.assertTrue(paths["loose_exe"].exists())
        self.assertTrue(paths["dup1"].exists())
        self.assertTrue(paths["dup2"].exists())

        # Receipt removed
        self.assertFalse((self.sandbox / "Where did my files go.html").exists())

    def test_scan_excludes_previous_receipt(self) -> None:
        """BUG-07: Scan must exclude 'Where did my files go.html' from loose file organization."""
        old_receipt = self.sandbox / "Where did my files go.html"
        old_receipt.write_text("old html receipt content")
        plan = self.engine.scan(self.sandbox)
        planned_names = [src.name.lower() for src, _ in plan.loose_files_to_organize]
        self.assertNotIn("where did my files go.html", planned_names)

    def test_scan_preserves_held_3mf_files(self) -> None:
        """BUG-08: Actively held 3MF files must not be moved as loose files during remodel."""
        import hold_manager
        held_file = self.sandbox / "Plate1_Model.3mf"
        held_file.write_text("3mf content")
        hold_manager.hold_file(held_file, self.config, planned_dest=self.sandbox / "out.3mf")
        self.assertTrue(hold_manager.is_held(held_file))

        plan = self.engine.scan(self.sandbox)
        planned_srcs = [str(src.resolve()) for src, _ in plan.loose_files_to_organize]
        self.assertNotIn(str(held_file.resolve()), planned_srcs)
        # Should be in protected items
        prot_paths = [str(p.resolve()) for p, _ in plan.protected_items]
        self.assertIn(str(held_file.resolve()), prot_paths)

    def test_duplicate_quarantine_protection_for_protected_folders(self) -> None:
        """BUG-15: Files inside protected or kept folders must never be quarantined as duplicates."""
        kept_dir = self.sandbox / "Kept_Sim"
        kept_dir.mkdir()
        (kept_dir / remodel.KEEP_MARKER_NAME).write_text("keep")

        content = "critical simulation mesh data" * 60
        f_in_kept = kept_dir / "mesh.bin"
        f_in_kept.write_text(content)

        f_loose = self.sandbox / "mesh_copy.bin"
        f_loose.write_text(content)

        plan = self.engine.scan(self.sandbox)
        # Kept file should NEVER be in duplicates_to_quarantine
        for dupe, q_dest, keeper in plan.duplicates_to_quarantine:
            self.assertNotEqual(str(dupe.resolve()), str(f_in_kept.resolve()))

    def test_remodel_cli_v_flag(self) -> None:
        """CLI argument parser accepts -V for version banner symmetry."""
        import subprocess
        import sys
        proc = subprocess.run(
            [sys.executable, str(Path(remodel.__file__).resolve()), "-V"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn(about.CREDIT, proc.stdout)


    def test_untouched_git_repo_protection_and_no_git_head_duplication(self) -> None:
        """Old/untouched Git repos must be protected and .git/HEAD must never be quarantined."""
        repo_dir = self.sandbox / "Old_Untouched_Repo"
        git_dir = repo_dir / ".git"
        git_dir.mkdir(parents=True)
        head_file = git_dir / "HEAD"
        head_file.write_text("ref: refs/heads/main\n")
        code_file = repo_dir / "app.py"
        code_file.write_text("print('hello world')\n")

        # Create another git repo with identical HEAD
        repo2 = self.sandbox / "Another_Repo"
        (repo2 / ".git").mkdir(parents=True)
        head2 = repo2 / ".git" / "HEAD"
        head2.write_text("ref: refs/heads/main\n")

        # Set old mtimes (60 days ago) so 14-day activity rule does NOT protect them
        old_time = time.time() - (60 * 86400)
        for f in (head_file, code_file, head2):
            os.utime(f, (old_time, old_time))

        plan = self.engine.scan(self.sandbox)
        card_map = {c.folder_path.name: c for c in plan.cards}

        # Both repos must be recognized as protected Git repos!
        self.assertTrue(card_map["Old_Untouched_Repo"].protection.is_protected)
        self.assertIn("Git repository", card_map["Old_Untouched_Repo"].protection.reason)
        self.assertEqual(card_map["Old_Untouched_Repo"].action, "keep_intact")

        # .git/HEAD must NEVER be in duplicates_to_quarantine!
        for dupe, q_dest, keeper in plan.duplicates_to_quarantine:
            self.assertNotIn("HEAD", dupe.name)
            self.assertNotIn(".git", str(dupe))

        # Applying remodel must keep repo files intact
        self.engine.apply(plan)
        self.assertTrue(code_file.exists())
        self.assertTrue(head_file.exists())
        self.assertFalse((self.sandbox / "Code" / "app.py").exists())

    def test_remodel_undo_preserves_unrestored_quarantine_files(self) -> None:
        """Undo must NEVER wipe quarantine folder if any files fail to restore."""
        paths = self._build_messy_sandbox()
        plan = self.engine.scan(self.sandbox)
        manifest = self.engine.apply(plan)

        q_dir = self.sandbox / remodel.QUARANTINE_DIR_NAME / plan.remodel_id
        self.assertTrue(q_dir.exists())

        # Simulate a locked/failed restore by patching safe_ops.restore to return None for one item
        with patch("safe_ops.restore", return_value=None):
            undo_res = self.engine.undo(plan.remodel_id)

        self.assertGreater(undo_res.get("failed_count", 0), 0)
        self.assertFalse(undo_res.get("ok"))
        self.assertTrue(undo_res.get("partial"))
        self.assertIn("remain safely preserved in quarantine", undo_res.get("message", ""))
        # Quarantine directory and unrestored files MUST be safely preserved!
        self.assertTrue(q_dir.exists())
        remaining = list(q_dir.rglob("*.txt"))
        self.assertGreater(len(remaining), 0)
        # HTML receipt retained for recovery
        self.assertTrue((self.sandbox / remodel.RECEIPT_FILENAME).exists())

    def test_nested_git_repo_inside_arbitrary_folder_kept_whole(self) -> None:
        """Repos nested inside another folder (e.g. Stuff\\inner-repo) must be kept whole."""
        stuff_dir = self.sandbox / "Stuff"
        inner_repo = stuff_dir / "inner-repo"
        git_dir = inner_repo / ".git"
        git_dir.mkdir(parents=True)
        head_file = git_dir / "HEAD"
        head_file.write_text("ref: refs/heads/main\n")

        src_dir = inner_repo / "src"
        src_dir.mkdir(parents=True)
        app_file = src_dir / "app.py"
        app_file.write_text("print('nested repo app')\n")

        loose_in_stuff = stuff_dir / "notes.txt"
        loose_in_stuff.write_text("random note\n")

        # Set mtimes > 14 days ago (e.g. 60 days)
        old_time = time.time() - (60 * 86400)
        for f in (head_file, app_file, loose_in_stuff):
            os.utime(f, (old_time, old_time))

        plan = self.engine.scan(self.sandbox)

        # Confirm that inner-repo is detected as protected
        prot_paths = [p for p, _ in plan.protected_items]
        self.assertTrue(any("inner-repo" in str(p) for p in prot_paths))

        # Confirm app.py is NOT scheduled to be moved
        all_move_srcs = [str(src) for src, _ in plan.loose_files_to_organize]
        self.assertNotIn(str(app_file), all_move_srcs)

        # Apply remodel
        manifest = self.engine.apply(plan)

        # app.py and .git/HEAD must remain completely intact in Stuff/inner-repo!
        self.assertTrue(app_file.exists(), "src/app.py must remain intact inside inner-repo")
        self.assertTrue(head_file.exists(), ".git/HEAD must remain intact inside inner-repo")
        self.assertFalse((self.sandbox / "Code" / "app.py").exists(), "app.py must not be scattered to Code/")


if __name__ == "__main__":
    unittest.main()
