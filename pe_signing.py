"""Inspect, stage, and restore Authenticode signatures for Download Sorter release binaries."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

PE_EXTENSIONS = (".exe", ".dll", ".pyd")
_SUBPROCESS_TIMEOUT = 120


@dataclass(frozen=True)
class SignatureInfo:
    """Signature inspection result for a single Windows PE binary."""

    path: Path
    rel_path: str
    status: str
    signer: str = ""

    @property
    def is_valid(self) -> bool:
        return self.status == "Valid"


def find_pe_files(root_dir: Path) -> list[Path]:
    """Return all ``.exe``, ``.dll``, and ``.pyd`` files under ``root_dir``, deterministically sorted."""
    root = Path(root_dir)
    if not root.is_dir():
        return []
    found = [
        p
        for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() in PE_EXTENSIONS
    ]
    found.sort(key=lambda p: p.relative_to(root).as_posix().lower())
    return found


def _find_powershell() -> str:
    return shutil.which("pwsh") or shutil.which("powershell") or "powershell"


def _run_authenticode_query(
    files: Sequence[Path],
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> list[dict[str, str]]:
    """Query ``Get-AuthenticodeSignature`` for ``files`` in a single PowerShell call."""
    if not files:
        return []

    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        suffix=".txt",
        delete=False,
    ) as tmp:
        list_path = Path(tmp.name)
        for path in files:
            tmp.write(str(path) + "\n")

    ps_script = (
        "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
        "$ErrorActionPreference = 'Stop'; "
        f"$items = @(Get-Content -LiteralPath '{str(list_path).replace(chr(39), chr(39) * 2)}' -Encoding UTF8 | "
        "Where-Object { $_ -ne '' } | ForEach-Object { "
        "$p = [string]$_; "
        "$sig = Get-AuthenticodeSignature -LiteralPath $p; "
        "$subj = if ($sig.SignerCertificate) { [string]$sig.SignerCertificate.Subject } else { '' }; "
        "[PSCustomObject]@{ path = $p; status = [string]$sig.Status; signer = $subj } "
        "}); "
        "$items | ConvertTo-Json -Compress"
    )

    try:
        res = runner(
            [_find_powershell(), "-NoProfile", "-NonInteractive", "-Command", ps_script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_SUBPROCESS_TIMEOUT,
        )
    finally:
        list_path.unlink(missing_ok=True)

    if res.returncode != 0:
        detail = (res.stderr or res.stdout or "").strip()
        raise RuntimeError(f"PowerShell Get-AuthenticodeSignature failed (rc={res.returncode}): {detail}")

    raw = (res.stdout or "").strip()
    if not raw:
        return []
    parsed = json.loads(raw)
    if isinstance(parsed, dict):
        return [parsed]
    if isinstance(parsed, list):
        return [item for item in parsed if isinstance(item, dict)]
    raise RuntimeError(f"Unexpected JSON from Get-AuthenticodeSignature: {type(parsed).__name__}")


def inspect_pe_signatures(
    root_dir: Path,
    *,
    paths: Sequence[Path] | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> list[SignatureInfo]:
    """Return signature status for each ``.exe``, ``.dll``, and ``.pyd`` under ``root_dir``."""
    root = Path(root_dir)
    targets = [Path(p) for p in paths] if paths is not None else find_pe_files(root)
    if not targets:
        return []

    records = _run_authenticode_query(targets, runner=runner)
    by_norm_path: dict[str, dict[str, str]] = {}
    for rec in records:
        raw_path = rec.get("path", "")
        if isinstance(raw_path, dict):
            raw_path = raw_path.get("value", "")
        by_norm_path[os.path.normcase(os.path.normpath(str(raw_path)))] = rec

    results: list[SignatureInfo] = []
    for path in targets:
        norm = os.path.normcase(os.path.normpath(str(path)))
        rec = by_norm_path.get(norm, {})
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            rel = path.name
        results.append(
            SignatureInfo(
                path=path,
                rel_path=rel,
                status=str(rec.get("status") or "UnknownError"),
                signer=str(rec.get("signer") or ""),
            )
        )
    return results


def list_unsigned_files(
    root_dir: Path,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> list[SignatureInfo]:
    """Return every PE binary under ``root_dir`` without a ``Valid`` Authenticode signature."""
    return [info for info in inspect_pe_signatures(root_dir, runner=runner) if not info.is_valid]


def partition_pe_signatures(
    root_dir: Path,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> tuple[list[SignatureInfo], list[SignatureInfo]]:
    """Split PE binaries under ``root_dir`` into ``(signed, unsigned)`` lists."""
    all_info = inspect_pe_signatures(root_dir, runner=runner)
    signed = [info for info in all_info if info.is_valid]
    unsigned = [info for info in all_info if not info.is_valid]
    return signed, unsigned


def stage_unsigned_files(
    dist_app_dir: Path,
    stage_dir: Path,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> tuple[list[SignatureInfo], list[SignatureInfo]]:
    """Copy only unsigned PE files from ``dist_app_dir`` into ``stage_dir``, keeping relative paths."""
    src_root = Path(dist_app_dir)
    dst_root = Path(stage_dir)
    if not src_root.is_dir():
        raise FileNotFoundError(f"App distribution directory does not exist: {src_root}")

    signed, unsigned = partition_pe_signatures(src_root, runner=runner)
    if dst_root.exists():
        shutil.rmtree(dst_root)
    dst_root.mkdir(parents=True, exist_ok=True)

    for info in unsigned:
        dest = dst_root / info.rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(info.path, dest)

    return signed, unsigned


def restore_signed_files(signed_dir: Path, dist_app_dir: Path) -> list[str]:
    """Copy signed PE files from ``signed_dir`` back into ``dist_app_dir`` at matching paths."""
    src_root = Path(signed_dir)
    dst_root = Path(dist_app_dir)
    if not src_root.is_dir():
        raise FileNotFoundError(f"Signed artifact directory does not exist: {src_root}")
    if not dst_root.is_dir():
        raise FileNotFoundError(f"Target app directory does not exist: {dst_root}")

    restored: list[str] = []
    for src_file in find_pe_files(src_root):
        rel = src_file.relative_to(src_root)
        dest = (dst_root / rel).resolve()
        # Guard against unexpected traversal paths.
        if not dest.is_relative_to(dst_root.resolve()):
            raise ValueError(f"Refusing to restore outside {dst_root}: {rel}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_file, dest)
        restored.append(rel.as_posix())
    return restored


def _write_github_output(count: int) -> None:
    gh_out = os.environ.get("GITHUB_OUTPUT")
    if not gh_out:
        return
    with open(gh_out, "a", encoding="utf-8") as fh:
        fh.write(f"count={count}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect and stage unsigned Download Sorter PE files.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="List unsigned .exe/.dll/.pyd files under a directory.")
    p_list.add_argument("directory", nargs="?", type=Path, default=Path("dist") / "DownloadSorter")
    p_list.add_argument("--json", action="store_true", help="Print full signature summary as JSON.")

    p_stage = sub.add_parser("stage", help="Copy unsigned PE files to a staging folder for signing.")
    p_stage.add_argument("dist_dir", nargs="?", type=Path, default=Path("dist") / "DownloadSorter")
    p_stage.add_argument("stage_dir", nargs="?", type=Path, default=Path("dist") / "unsigned-app")
    p_stage.add_argument("--github-output", action="store_true", help="Write count=<n> to GITHUB_OUTPUT.")

    p_restore = sub.add_parser("restore", help="Copy signed PE files back into the app folder.")
    p_restore.add_argument("signed_dir", type=Path)
    p_restore.add_argument("dist_dir", nargs="?", type=Path, default=Path("dist") / "DownloadSorter")

    args = parser.parse_args(argv)

    if args.command == "list":
        signed, unsigned = partition_pe_signatures(args.directory)
        if args.json:
            payload = {
                "directory": str(args.directory),
                "signed_count": len(signed),
                "unsigned_count": len(unsigned),
                "unsigned": [
                    {"rel_path": u.rel_path, "status": u.status, "path": str(u.path)}
                    for u in unsigned
                ],
            }
            print(json.dumps(payload, indent=2))
        else:
            print(f"Signed: {len(signed)}, Unsigned: {len(unsigned)} under {args.directory}")
            for u in unsigned:
                print(f"  {u.rel_path} ({u.status})")
        return 0

    if args.command == "stage":
        signed, unsigned = stage_unsigned_files(args.dist_dir, args.stage_dir)
        print(
            f"Staged {len(unsigned)} unsigned PE file(s) to {args.stage_dir} "
            f"(kept {len(signed)} publisher-signed file(s) untouched)."
        )
        for u in unsigned:
            print(f"  + {u.rel_path} ({u.status})")
        if args.github_output:
            _write_github_output(len(unsigned))
        return 0

    if args.command == "restore":
        restored = restore_signed_files(args.signed_dir, args.dist_dir)
        print(f"Restored {len(restored)} signed PE file(s) into {args.dist_dir}:")
        for rel in restored:
            print(f"  <- {rel}")
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
