"""
PDF geo-referenced processing: extract GPTS corners and rasterize to PNG.
Works with OCAD PDF exports that contain geo-viewport metadata.
"""

import io
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import fitz  # PyMuPDF
from PIL import Image


def slugify(name: str) -> str:
    """Convert filename to a URL-safe slug."""
    name = Path(name).stem.lower()
    name = re.sub(r"[^a-z0-9]+", "-", name)
    return name.strip("-")


def extract_gpts(pdf_path: str) -> dict:
    """
    Extract GPTS (Geographic Points) from a geo-referenced PDF.
    Returns corners as {nw, ne, se, sw} with {lat, lng}.

    GPTS format in PDF: [lat1 lng1 lat2 lng2 lat3 lng3 lat4 lng4]
    Order follows LPTS [0 1, 0 0, 1 0, 1 1] = NW, SW, SE, NE
    """
    doc = fitz.open(pdf_path)
    page = doc[0]
    xref = page.xref
    page_dict = doc.xref_get_key(xref, "")

    if page_dict[0] != "dict":
        doc.close()
        raise ValueError("Cannot read page dictionary")

    raw = page_dict[1]

    # Find GPTS array in the page dict
    gpts_match = re.search(r"/GPTS\s*\[([^\]]+)\]", raw)
    if not gpts_match:
        doc.close()
        raise ValueError(
            "No GPTS (geo-points) found in PDF. "
            "Make sure the PDF was exported from OCAD with geo-referencing enabled."
        )

    values = [float(v) for v in gpts_match.group(1).split()]
    if len(values) != 8:
        doc.close()
        raise ValueError(f"Expected 8 GPTS values, got {len(values)}")

    # LPTS order: [0 1] [0 0] [1 0] [1 1] = NW, SW, SE, NE
    corners = {
        "nw": {"lat": values[0], "lng": values[1]},
        "sw": {"lat": values[2], "lng": values[3]},
        "se": {"lat": values[4], "lng": values[5]},
        "ne": {"lat": values[6], "lng": values[7]},
    }

    doc.close()
    return corners


def rasterize_pdf(pdf_path: str, output_path: str, dpi: int = 300) -> tuple[int, int]:
    """
    Rasterize first page of PDF to opaque PNG at given DPI.
    Returns (width, height) in pixels.
    """
    doc = fitz.open(pdf_path)
    page = doc[0]
    zoom = dpi / 72
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat, alpha=True)

    img = Image.open(io.BytesIO(pix.tobytes("png")))
    # Composite onto white background (remove transparency)
    bg = Image.new("RGB", img.size, (255, 255, 255))
    if img.mode == "RGBA":
        bg.paste(img, mask=img.split()[3])
    else:
        bg.paste(img)
    bg.save(output_path, "PNG")

    size = bg.size
    doc.close()
    return size


def create_thumbnail(png_path: str, thumb_path: str, max_width: int = 400):
    """Create a thumbnail from the rasterized map."""
    img = Image.open(png_path)
    ratio = max_width / img.width
    new_size = (max_width, int(img.height * ratio))
    img = img.resize(new_size, Image.LANCZOS)
    img.save(thumb_path, "JPEG", quality=80)


def process_pdf(pdf_path: str, maps_dir: str, title: str | None = None, original_filename: str | None = None) -> dict:
    """
    Full pipeline: extract geo-data, rasterize, create thumbnail, write config.
    Returns the config dict.
    """
    pdf_path = str(pdf_path)
    filename = original_filename or Path(pdf_path).name
    slug = slugify(filename)
    map_dir = Path(maps_dir) / slug
    map_dir.mkdir(parents=True, exist_ok=True)

    # Extract geo corners
    corners = extract_gpts(pdf_path)

    # Rasterize
    png_path = map_dir / "map.png"
    image_size = rasterize_pdf(pdf_path, str(png_path), dpi=300)

    # Thumbnail
    thumb_path = map_dir / "thumb.jpg"
    create_thumbnail(str(png_path), str(thumb_path))

    # Detect scale from filename (e.g. "Rzeszow_3000.pdf" → 3000)
    scale_match = re.search(r"(\d{3,5})", filename)
    scale = int(scale_match.group(1)) if scale_match else None

    config = {
        "id": slug,
        "title": title or filename.replace(".pdf", "").replace("_", " "),
        "scale": scale,
        "filename": filename,
        "imageSize": list(image_size),
        "corners": corners,
        "createdAt": datetime.now(timezone.utc).isoformat(),
    }

    config_path = map_dir / "config.json"
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)

    return config
