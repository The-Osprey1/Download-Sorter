import io
import json
from pathlib import Path
import zipfile
from PIL import Image

out_dir = Path("tests/fixtures/3mf")
out_dir.mkdir(parents=True, exist_ok=True)

# Minimal 1x1 PNG
img_small = Image.new("RGBA", (64, 64), (100, 200, 255, 255))
buf_small = io.BytesIO()
img_small.save(buf_small, format="PNG")
small_png = buf_small.getvalue()

# 1. MakerWorld model
with zipfile.ZipFile(out_dir / "makerworld_model.3mf", "w") as z:
    slice_info = (
        '<config>\n'
        '  <header_item key="Origin" value="https://makerworld.com/en/models/498213"/>\n'
        '  <header_item key="X-BBL-Client-Type" value="BambuStudio"/>\n'
        '  <header_item key="DesignModelId" value="498213"/>\n'
        '  <header_item key="DesignProfileId" value="123456"/>\n'
        '</config>'
    )
    z.writestr("Metadata/slice_info.config", slice_info)
    project_settings = json.dumps({
        "profile_title": "Articulated Dragon",
        "designer": "DevynFidel",
        "designer_user_id": "8888",
        "filament_type": ["PLA"],
        "filament_colour": ["#FF0000"],
    })
    z.writestr("Metadata/project_settings.config", project_settings)
    z.writestr("Metadata/plate_1.png", small_png)
    z.writestr("3D/3dmodel.model", '<?xml version="1.0" encoding="UTF-8"?><model unit="millimeter" xml:lang="en-US" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02"><resources></resources><build></build></model>')

# 2. Printables model
with zipfile.ZipFile(out_dir / "printables_model.3mf", "w") as z:
    slice_info = '<config><header_item key="Origin" value="https://www.printables.com/model/123-gear"/></config>'
    z.writestr("Metadata/slice_info.config", slice_info)
    z.writestr("Metadata/plate_1.png", small_png)
    z.writestr("3D/3dmodel.model", '<?xml version="1.0"?><model></model>')

# 3. Thingiverse model
with zipfile.ZipFile(out_dir / "thingiverse_model.3mf", "w") as z:
    settings = json.dumps({"origin": "https://www.thingiverse.com/thing:99999"})
    z.writestr("Metadata/project_settings.config", settings)
    z.writestr("3D/3dmodel.model", '<?xml version="1.0"?><model></model>')

# 4. Bambu local project (NOT MakerWorld)
with zipfile.ZipFile(out_dir / "bambu_local_project.3mf", "w") as z:
    slice_info = (
        '<config>\n'
        '  <header_item key="X-BBL-Client-Type" value="BambuStudio"/>\n'
        '  <header_item key="X-BBL-Client-Version" value="01.09.03.50"/>\n'
        '  <header_item key="DesignerUserId" value="1001"/>\n'
        '</config>'
    )
    z.writestr("Metadata/slice_info.config", slice_info)
    z.writestr("Metadata/plate_1.png", small_png)
    z.writestr("3D/3dmodel.model", '<?xml version="1.0"?><model></model>')

# 5. OrcaSlicer project
with zipfile.ZipFile(out_dir / "orcaslicer_project.3mf", "w") as z:
    slice_info = '<config><header_item key="X-BBL-Client-Type" value="OrcaSlicer"/></config>'
    z.writestr("Metadata/slice_info.config", slice_info)
    z.writestr("3D/3dmodel.model", '<?xml version="1.0"?><model></model>')

# 6. PrusaSlicer project
with zipfile.ZipFile(out_dir / "prusaslicer_project.3mf", "w") as z:
    z.writestr("Metadata/Prusa_Slicer.ini", "[print:*0.20mm*]\nlayer_height = 0.2\n")
    z.writestr("3D/3dmodel.model", '<?xml version="1.0"?><model></model>')

# 7. Zip bomb (>200MB uncompressed)
with zipfile.ZipFile(out_dir / "zip_bomb.3mf", "w", compression=zipfile.ZIP_DEFLATED) as z:
    # 210 MB of zeros compresses to ~200 KB
    z.writestr("3D/huge_data.bin", b"\x00" * (210 * 1024 * 1024))

# 8. Path traversal
with zipfile.ZipFile(out_dir / "traversal.3mf", "w") as z:
    z.writestr("../escaped.txt", "evil content")

# 9. Malformed XML
with zipfile.ZipFile(out_dir / "malformed_xml.3mf", "w") as z:
    z.writestr("3D/3dmodel.model", "<model><unclosed><bad></model>")

# 10. Oversized thumbnail (>1024x1024)
img_huge = Image.new("RGBA", (2048, 2048), (255, 128, 0, 255))
buf_huge = io.BytesIO()
img_huge.save(buf_huge, format="PNG")
huge_png = buf_huge.getvalue()
with zipfile.ZipFile(out_dir / "oversized_thumb.3mf", "w") as z:
    z.writestr("Metadata/plate_1.png", huge_png)
    z.writestr("3D/3dmodel.model", '<?xml version="1.0"?><model></model>')

print("Generated 10 3MF test fixtures successfully.")
