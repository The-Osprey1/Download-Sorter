"""Inspect, stage, and restore Authenticode signatures for Download Sorter release files."""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

PE_EXTENSIONS = (".exe", ".dll", ".pyd")
SCRIPT_EXTENSIONS = (".ps1",)
SIGNABLE_EXTENSIONS = PE_EXTENSIONS + SCRIPT_EXTENSIONS
_SUBPROCESS_TIMEOUT = 120


@dataclass(frozen=True)
class SignatureInfo:
    """Signature inspection result for a single Windows executable, library, or script."""

    path: Path
    rel_path: str
    status: str
    signature_type: str = ""
    signer: str = ""

    @property
    def is_valid(self) -> bool:
        """True only for a valid embedded Authenticode signature (catalog-only is False)."""
        return self.status == "Valid" and self.signature_type == "Authenticode"


def find_signable_files(
    root_dir: Path,
    *,
    extensions: tuple[str, ...] = SIGNABLE_EXTENSIONS,
) -> list[Path]:
    """Return all signable release files (``.exe``, ``.dll``, ``.pyd``, ``.ps1``) sorted by relative path."""
    root = Path(root_dir)
    if not root.is_dir():
        return []
    found = [
        p
        for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() in extensions
    ]
    found.sort(key=lambda p: p.relative_to(root).as_posix().lower())
    return found


def find_pe_files(root_dir: Path) -> list[Path]:
    """Return only PE binary files (``.exe``, ``.dll``, ``.pyd``) under ``root_dir``."""
    return find_signable_files(root_dir, extensions=PE_EXTENSIONS)


def _find_powershell() -> str:
    return shutil.which("pwsh") or shutil.which("powershell") or "powershell"


def _run_authenticode_query(
    files: Sequence[Path],
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> list[dict[str, str]]:
    """Query ``Get-AuthenticodeSignature`` for ``files`` in a single PowerShell call."""
    if not files:
        return []
    run_cmd = runner or subprocess.run

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
        "[PSCustomObject]@{ "
        "path = $p; "
        "status = [string]$sig.Status; "
        "signature_type = [string]$sig.SignatureType; "
        "signer = $subj "
        "} "
        "}); "
        "$items | ConvertTo-Json -Compress"
    )

    try:
        res = run_cmd(
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
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> list[SignatureInfo]:
    """Return signature status for each signable file (``.exe``, ``.dll``, ``.pyd``, ``.ps1``) under ``root_dir``."""
    root = Path(root_dir)
    targets = [Path(p) for p in paths] if paths is not None else find_signable_files(root)
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
                signature_type=str(rec.get("signature_type") or ""),
                signer=str(rec.get("signer") or ""),
            )
        )
    return results


def list_unsigned_files(
    root_dir: Path,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> list[SignatureInfo]:
    """Return every signable file under ``root_dir`` without a valid embedded Authenticode signature."""
    return [info for info in inspect_pe_signatures(root_dir, runner=runner) if not info.is_valid]


def partition_pe_signatures(
    root_dir: Path,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> tuple[list[SignatureInfo], list[SignatureInfo]]:
    """Split signable files under ``root_dir`` into ``(signed, unsigned)`` lists."""
    all_info = inspect_pe_signatures(root_dir, runner=runner)
    signed = [info for info in all_info if info.is_valid]
    unsigned = [info for info in all_info if not info.is_valid]
    return signed, unsigned


def stage_unsigned_files(
    dist_app_dir: Path,
    stage_dir: Path,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> tuple[list[SignatureInfo], list[SignatureInfo]]:
    """Copy only unsigned/catalog-only files from ``dist_app_dir`` into ``stage_dir``, keeping relative paths."""
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
    """Copy signed files from ``signed_dir`` back into ``dist_app_dir`` at matching paths."""
    src_root = Path(signed_dir)
    dst_root = Path(dist_app_dir)
    if not src_root.is_dir():
        raise FileNotFoundError(f"Signed artifact directory does not exist: {src_root}")
    if not dst_root.is_dir():
        raise FileNotFoundError(f"Target app directory does not exist: {dst_root}")

    restored: list[str] = []
    for src_file in find_signable_files(src_root):
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


def _format_sig_reason(info: SignatureInfo) -> str:
    if info.signature_type and info.signature_type != "None":
        return f"{info.status}, SignatureType={info.signature_type}"
    return info.status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Inspect and stage unsigned Download Sorter binaries (.exe/.dll/.pyd) and scripts (.ps1)."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="List signed and unsigned files under a directory.")
    p_list.add_argument("directory", nargs="?", type=Path, default=Path("dist") / "DownloadSorter")
    p_list.add_argument("--json", action="store_true", help="Print full signature and signer details as JSON.")

    p_stage = sub.add_parser("stage", help="Copy unsigned/catalog-only files to a staging folder for signing.")
    p_stage.add_argument("dist_dir", nargs="?", type=Path, default=Path("dist") / "DownloadSorter")
    p_stage.add_argument("stage_dir", nargs="?", type=Path, default=Path("dist") / "unsigned-app")
    p_stage.add_argument("--github-output", action="store_true", help="Write count=<n> to GITHUB_OUTPUT.")

    p_restore = sub.add_parser("restore", help="Copy signed files back into the app folder.")
    p_restore.add_argument("signed_dir", type=Path)
    p_restore.add_argument("dist_dir", nargs="?", type=Path, default=Path("dist") / "DownloadSorter")

    args = parser.parse_args(argv)

    if args.command == "list":
        signed, unsigned = partition_pe_signatures(args.directory)
        signer_counts = dict(sorted(Counter(s.signer or "(unknown)" for s in signed).items()))
        if args.json:
            payload = {
                "directory": str(args.directory),
                "signed_count": len(signed),
                "unsigned_count": len(unsigned),
                "signers": signer_counts,
                "signed": [
                    {
                        "rel_path": s.rel_path,
                        "status": s.status,
                        "signature_type": s.signature_type,
                        "signer": s.signer,
                    }
                    for s in signed
                ],
                "unsigned": [
                    {
                        "rel_path": u.rel_path,
                        "status": u.status,
                        "signature_type": u.signature_type,
                        "signer": u.signer,
                        "path": str(u.path),
                    }
                    for u in unsigned
                ],
            }
            print(json.dumps(payload, indent=2))
        else:
            print(
                f"Signed (embedded Authenticode): {len(signed)}, "
                f"Needs signature: {len(unsigned)} under {args.directory}"
            )
            if signer_counts:
                print("Signers:")
                for signer, count in signer_counts.items():
                    print(f"  [{count}] {signer}")
            if unsigned:
                print("Files needing signature:")
                for u in unsigned:
                    print(f"  {u.rel_path} ({_format_sig_reason(u)})")
        return 0

    if args.command == "stage":
        signed, unsigned = stage_unsigned_files(args.dist_dir, args.stage_dir)
        print(
            f"Staged {len(unsigned)} file(s) needing signature to {args.stage_dir} "
            f"(kept {len(signed)} embedded-Authenticode file(s) untouched)."
        )
        for u in unsigned:
            print(f"  + {u.rel_path} ({_format_sig_reason(u)})")
        if args.github_output:
            _write_github_output(len(unsigned))
        return 0

    if args.command == "restore":
        restored = restore_signed_files(args.signed_dir, args.dist_dir)
        print(f"Restored {len(restored)} signed file(s) into {args.dist_dir}:")
        for rel in restored:
            print(f"  <- {rel}")
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
