"""Inspect 3D printing slicer metadata and thumbnails from .gcode, .bgcode, and .3mf files."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import zipfile
from pathlib import Path


def format_duration(seconds: float | int) -> str:
    seconds = int(seconds)
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    parts = []
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0 or hours > 0:
        parts.append(f"{minutes}m")
    parts.append(f"{secs}s")
    return " ".join(parts)


def parse_3mf(path: Path) -> dict:
    meta: dict = {
        "format": "3MF",
        "file": path.name,
        "path": str(path.resolve()),
        "size_mb": round(path.stat().st_size / (1024 * 1024), 2),
        "slicer": "Unknown Slicer",
        "slicer_version": None,
        "filament_type": None,
        "filament_color": None,
        "filament_weight_g": None,
        "filament_length_m": None,
        "print_time": None,
        "layer_height": None,
        "nozzle_diameter": None,
        "nozzle_temp": None,
        "bed_temp": None,
        "bed_type": None,
        "has_thumbnail": False,
        "thumbnail_data": None,
    }

    try:
        with zipfile.ZipFile(path, "r") as z:
            names = z.namelist()

            # Check for thumbnails
            for thumb_name in ["Metadata/plate_1.png", "Metadata/plate_no_light_1.png", "Metadata/thumbnail.png", "thumbnail.png"]:
                if thumb_name in names:
                    meta["has_thumbnail"] = True
                    meta["thumbnail_name"] = thumb_name
                    meta["thumbnail_data"] = z.read(thumb_name)
                    break

            # Slicer Client Info
            if "Metadata/slice_info.config" in names:
                slice_info = z.read("Metadata/slice_info.config").decode("utf-8", errors="ignore")
                client_type = re.search(r'header_item\s+key="X-BBL-Client-Type"\s+value="([^"]+)"', slice_info)
                client_ver = re.search(r'header_item\s+key="X-BBL-Client-Version"\s+value="([^"]+)"', slice_info)
                if client_type:
                    meta["slicer"] = "Bambu Studio / OrcaSlicer"
                if client_ver:
                    meta["slicer_version"] = client_ver.group(1)

            # Plate 1 JSON
            if "Metadata/plate_1.json" in names:
                try:
                    p1 = json.loads(z.read("Metadata/plate_1.json").decode("utf-8", errors="ignore"))
                    if "layer_height" in p1:
                        meta["layer_height"] = round(float(p1["layer_height"]), 3)
                    if "nozzle_diameter" in p1:
                        meta["nozzle_diameter"] = p1["nozzle_diameter"]
                    if "bed_type" in p1:
                        meta["bed_type"] = p1["bed_type"]
                    if "prediction" in p1 and p1["prediction"]:
                        meta["print_time"] = format_duration(p1["prediction"])
                except Exception:
                    pass

            # Project Settings Config
            if "Metadata/project_settings.config" in names:
                try:
                    cfg = json.loads(z.read("Metadata/project_settings.config").decode("utf-8", errors="ignore"))
                    if "filament_type" in cfg and cfg["filament_type"]:
                        meta["filament_type"] = ", ".join(cfg["filament_type"]) if isinstance(cfg["filament_type"], list) else str(cfg["filament_type"])
                    if "filament_colour" in cfg and cfg["filament_colour"]:
                        meta["filament_color"] = ", ".join(cfg["filament_colour"]) if isinstance(cfg["filament_colour"], list) else str(cfg["filament_colour"])
                    if "nozzle_temperature" in cfg and cfg["nozzle_temperature"]:
                        val = cfg["nozzle_temperature"]
                        meta["nozzle_temp"] = f"{val[0]}°C" if isinstance(val, list) else f"{val}°C"
                    if "nozzle_diameter" in cfg and cfg["nozzle_diameter"] and not meta["nozzle_diameter"]:
                        val = cfg["nozzle_diameter"]
                        meta["nozzle_diameter"] = val[0] if isinstance(val, list) else val
                    if "curr_bed_type" in cfg and cfg["curr_bed_type"] and not meta["bed_type"]:
                        meta["bed_type"] = cfg["curr_bed_type"]
                    if "layer_height" in cfg and cfg["layer_height"] and not meta["layer_height"]:
                        meta["layer_height"] = cfg["layer_height"]
                except Exception:
                    pass

            # Model settings config
            if "Metadata/model_settings.config" in names:
                try:
                    m_text = z.read("Metadata/model_settings.config").decode("utf-8", errors="ignore")
                    time_match = re.search(r'estimated_time="([^"]+)"', m_text)
                    if time_match:
                        meta["print_time"] = format_duration(float(time_match.group(1)))
                except Exception:
                    pass
    except Exception as exc:
        meta["error"] = str(exc)

    return meta


def parse_gcode(path: Path) -> dict:
    meta: dict = {
        "format": "GCODE",
        "file": path.name,
        "path": str(path.resolve()),
        "size_mb": round(path.stat().st_size / (1024 * 1024), 2),
        "slicer": "Unknown Slicer",
        "filament_type": None,
        "filament_weight_g": None,
        "filament_length_m": None,
        "print_time": None,
        "layer_height": None,
        "nozzle_diameter": None,
        "nozzle_temp": None,
        "bed_temp": None,
        "has_thumbnail": False,
        "thumbnail_data": None,
    }

    try:
        # Read header (first 100KB) and footer (last 100KB) for comments
        content_lines: list[str] = []
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for _ in range(500):
                line = f.readline()
                if not line:
                    break
                content_lines.append(line)

        # Seek to tail
        file_size = path.stat().st_size
        if file_size > 100000:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                f.seek(max(0, file_size - 100000))
                content_lines.extend(f.readlines())

        full_header_footer = "".join(content_lines)

        # Detect Slicer
        if "PrusaSlicer" in full_header_footer:
            meta["slicer"] = "PrusaSlicer"
        elif "BambuStudio" in full_header_footer or "Bambu Studio" in full_header_footer:
            meta["slicer"] = "Bambu Studio"
        elif "OrcaSlicer" in full_header_footer:
            meta["slicer"] = "OrcaSlicer"
        elif "Cura" in full_header_footer:
            meta["slicer"] = "UltiMaker Cura"

        # Patterns
        patterns = {
            "print_time": [
                r";\s*estimated printing time \(normal mode\)\s*=\s*(.+)",
                r";\s*TIME:\s*(\d+)",
                r";\s*estimated_time\s*=\s*(.+)",
                r";\s*print_time\s*=\s*(.+)",
            ],
            "filament_weight_g": [
                r";\s*total filament used \[g\]\s*=\s*([\d\.]+)",
                r";\s*filament used \[g\]\s*=\s*([\d\.]+)",
                r";\s*Filament weight\s*=\s*([\d\.]+)g",
            ],
            "filament_length_m": [
                r";\s*filament used \[m\]\s*=\s*([\d\.]+)",
                r";\s*Filament length\s*=\s*([\d\.]+)m",
            ],
            "filament_type": [
                r";\s*filament_type\s*=\s*([^\r\n;]+)",
                r";\s*Filament type\s*=\s*([^\r\n;]+)",
            ],
            "layer_height": [
                r";\s*layer_height\s*=\s*([\d\.]+)",
                r";\s*Layer height:\s*([\d\.]+)",
            ],
            "nozzle_diameter": [
                r";\s*nozzle_diameter\s*=\s*([\d\.]+)",
                r";\s*Nozzle diameter:\s*([\d\.]+)",
            ],
            "nozzle_temp": [
                r";\s*temperature\s*=\s*(\d+)",
                r";\s*first_layer_temperature\s*=\s*(\d+)",
            ],
            "bed_temp": [
                r";\s*bed_temperature\s*=\s*(\d+)",
                r";\s*first_layer_bed_temperature\s*=\s*(\d+)",
            ],
        }

        for key, regexes in patterns.items():
            for r in regexes:
                match = re.search(r, full_header_footer, re.IGNORECASE)
                if match:
                    val = match.group(1).strip()
                    if key == "print_time" and val.isdigit():
                        meta[key] = format_duration(int(val))
                    elif key in ("nozzle_temp", "bed_temp") and not val.endswith("°C"):
                        meta[key] = f"{val}°C"
                    else:
                        meta[key] = val
                    break

        if "; thumbnail begin" in full_header_footer:
            meta["has_thumbnail"] = True

    except Exception as exc:
        meta["error"] = str(exc)

    return meta


def print_metadata(meta: dict) -> None:
    print("\n" + "=" * 50)
    print(f"  3D Print Slicer Inspector: {meta['file']}")
    print("=" * 50)
    print(f"  Format:          {meta['format']} ({meta['size_mb']} MB)")
    print(f"  Slicer:          {meta['slicer']}" + (f" v{meta['slicer_version']}" if meta.get('slicer_version') else ""))
    print(f"  Estimated Time:  {meta.get('print_time') or 'Not specified'}")
    print(f"  Filament Type:   {meta.get('filament_type') or 'Not specified'}" + (f" ({meta['filament_color']})" if meta.get('filament_color') else ""))
    if meta.get("filament_weight_g"):
        print(f"  Material Weight: {meta['filament_weight_g']} g" + (f" ({meta['filament_length_m']} m)" if meta.get('filament_length_m') else ""))
    print(f"  Layer Height:    {meta.get('layer_height') or 'Not specified'}" + (" mm" if meta.get('layer_height') else ""))
    nozzle_diam = meta.get("nozzle_diameter")
    if nozzle_diam:
        try:
            nozzle_diam = f"{round(float(nozzle_diam), 2)} mm"
        except (ValueError, TypeError):
            nozzle_diam = f"{nozzle_diam} mm"
    nozzle_str = nozzle_diam or "Not specified"
    if meta.get("nozzle_temp"):
        nozzle_str += f" @ {meta['nozzle_temp']}"
    print(f"  Nozzle:          {nozzle_str}")
    if meta.get("bed_temp") or meta.get("bed_type"):
        bed_desc = []
        if meta.get("bed_type"):
            bed_desc.append(meta["bed_type"])
        if meta.get("bed_temp"):
            bed_desc.append(f"@{meta['bed_temp']}")
        print(f"  Bed:             {' '.join(bed_desc)}")
    print(f"  Has Thumbnail:   {'Yes' if meta['has_thumbnail'] else 'No'}")
    print("=" * 50 + "\n")


def find_latest_3d_file() -> Path | None:
    downloads = Path(os.environ.get("USERPROFILE", "")) / "Downloads"
    candidates: list[Path] = []
    for ext in ("*.gcode", "*.3mf", "*.bgcode"):
        candidates.extend(downloads.rglob(ext))
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def main() -> int:
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    parser = argparse.ArgumentParser(description="Inspect 3D print metadata and thumbnails from .gcode and .3mf files.")
    parser.add_argument("file", nargs="?", type=str, help="Path to .gcode, .bgcode, or .3mf file")
    parser.add_argument("-l", "--latest", action="store_true", help="Inspect the most recent 3D print file in Downloads")
    parser.add_argument("-t", "--thumb", action="store_true", help="Extract and open embedded thumbnail image")
    parser.add_argument("-j", "--json", action="store_true", help="Output metadata as raw JSON")
    args = parser.parse_args()

    target: Path | None = None
    if args.latest:
        target = find_latest_3d_file()
        if not target:
            print("No .gcode or .3mf files found in Downloads.")
            return 1
    elif args.file:
        target = Path(args.file)
    else:
        # Default: try latest file
        target = find_latest_3d_file()
        if not target:
            parser.print_help()
            return 1

    if not target.exists():
        print(f"File not found: {target}")
        return 1

    ext = target.suffix.lower()
    if ext == ".3mf":
        meta = parse_3mf(target)
    elif ext in (".gcode", ".bgcode"):
        meta = parse_gcode(target)
    else:
        print(f"Unsupported format '{ext}'. Expected .3mf, .gcode, or .bgcode.")
        return 1

    if args.json:
        # Don't dump binary thumbnail in JSON stdout
        out = {k: v for k, v in meta.items() if k != "thumbnail_data"}
        print(json.dumps(out, indent=2))
        return 0

    print_metadata(meta)

    if args.thumb and meta.get("thumbnail_data"):
        thumb_out = target.parent / f"{target.stem}_thumbnail.png"
        thumb_out.write_bytes(meta["thumbnail_data"])
        print(f"Extracted thumbnail to: {thumb_out}")
        try:
            os.startfile(thumb_out)
        except Exception:
            pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
