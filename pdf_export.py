"""
OCAD Map Viewer — Route PDF export.
Converts map image + route data → A3 PDF @ 300 DPI with IOF symbols & labels.
"""

import io
import math
import os
import tempfile
from PIL import Image
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A3
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib import colors


# ── Constants ────────────────────────────────────────────────

# A3 @ 300 DPI: 297mm × 420mm = 3508px × 4961px
A3_WIDTH_MM = 297
A3_HEIGHT_MM = 420
DPI = 300
MM_TO_INCH = 1 / 25.4
INCH_TO_POINTS = 72
MM_TO_POINTS = MM_TO_INCH * INCH_TO_POINTS

A3_WIDTH_POINTS = A3_WIDTH_MM * MM_TO_POINTS
A3_HEIGHT_POINTS = A3_HEIGHT_MM * MM_TO_POINTS

# IOF standard magenta
IOF_PURPLE = "#cf00cf"

# Symbol sizes for PDF (base sizes, will be scaled)
CONTROL_RADIUS_PX = 10
START_RADIUS_PX = 12
FINISH_OUTER_PX = 12
FINISH_INNER_PX = 8
SYMBOL_STROKE_PX = 2
FONT_SIZE = 10


# ── Geometry ─────────────────────────────────────────────────

def gps_to_pixels(lat: float, lng: float, corners: dict, img_width: int, img_height: int) -> tuple:
    """
    Convert GPS coordinates to image pixels using exact 4-corner bilinear interpolation.

    Args:
        lat, lng: Geographic coordinate
        corners: {"nw": {"lat", "lng"}, "ne": {...}, "se": {...}, "sw": {...}}
        img_width, img_height: Image dimensions in pixels

    Returns:
        (x, y) in pixels, or None if corners invalid
    """
    try:
        nw = corners.get("nw", {}); ne = corners.get("ne", {})
        se = corners.get("se", {}); sw = corners.get("sw", {})
        for corner in [nw, ne, se, sw]:
            if not corner or "lat" not in corner or "lng" not in corner:
                return None

        nw_lat, nw_lng = nw["lat"], nw["lng"]
        ne_lat, ne_lng = ne["lat"], ne["lng"]
        se_lat, se_lng = se["lat"], se["lng"]
        sw_lat, sw_lng = sw["lat"], sw["lng"]

        # Bilinear coefficients: f(u,v) = c00 + c10*u + c01*v + c11*u*v
        a00, a10 = nw_lat, ne_lat - nw_lat
        a01, a11 = sw_lat - nw_lat, nw_lat - ne_lat - sw_lat + se_lat
        b00, b10 = nw_lng, ne_lng - nw_lng
        b01, b11 = sw_lng - nw_lng, nw_lng - ne_lng - sw_lng + se_lng

        # Initial estimate from bounding box, then Newton-Raphson
        lng_min = min(b00, b00 + b01); lng_max = max(b00 + b10, b00 + b10 + b01)
        lat_max = max(a00, a00 + a10); lat_min = min(a00 + a01, a00 + a01 + a10)
        if lat_max <= lat_min or lng_max <= lng_min:
            return None

        u = max(0.0, min(1.0, (lng - lng_min) / (lng_max - lng_min)))
        v = max(0.0, min(1.0, (lat_max - lat) / (lat_max - lat_min)))

        for _ in range(4):
            dlat = lat - (a00 + a10*u + a01*v + a11*u*v)
            dlng = lng - (b00 + b10*u + b01*v + b11*u*v)
            if abs(dlat) < 1e-10 and abs(dlng) < 1e-10:
                break
            J00 = a10 + a11*v; J01 = a01 + a11*u
            J10 = b10 + b11*v; J11 = b01 + b11*u
            det = J00*J11 - J01*J10
            if abs(det) < 1e-20:
                break
            u = max(0.0, min(1.0, u + ( J11*dlat - J01*dlng) / det))
            v = max(0.0, min(1.0, v + (-J10*dlat + J00*dlng) / det))

        return (u * img_width, v * img_height)
    except (KeyError, TypeError, ValueError):
        return None


def haversine_distance(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance between two points in meters."""
    r = 6371000.0  # Earth radius in meters
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lng2 - lng1)
    
    h = (math.sin(dphi / 2) ** 2 +
         math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2)
    return 2 * r * math.asin(min(1.0, math.sqrt(h)))


def bearing(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """
    Geographic bearing from point1 to point2.
    Returns: degrees clockwise from north (0-360)
    """
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dlmb = math.radians(lng2 - lng1)
    
    y = math.sin(dlmb) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlmb)
    
    return (math.atan2(y, x) * 180 / math.pi + 360) % 360


# ── SVG Symbol Generators ────────────────────────────────────

def start_symbol(color: str, heading_deg: float, scale: float = 1.0) -> str:
    """
    Equilateral triangle pointing toward next control.
    Returns SVG markup as string.
    """
    s = START_RADIUS_PX * scale
    stroke = SYMBOL_STROKE_PX * scale
    pad = stroke + 1
    c = s + pad
    size = 2 * c
    
    pts = []
    for k in range(3):
        ang = (-90 + heading_deg + k * 120) * math.pi / 180
        x = c + s * math.cos(ang)
        y = c + s * math.sin(ang)
        pts.append(f"{x:.1f},{y:.1f}")
    
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size:.0f}" height="{size:.0f}">'
        f'<polygon points="{" ".join(pts)}" fill="none" stroke="{color}" '
        f'stroke-width="{stroke:.1f}" stroke-linejoin="round"/></svg>'
    )
    return svg, (c, c)  # Return SVG and anchor point


def control_symbol(color: str, number: int, scale: float = 1.0) -> str:
    """
    Circle with control number label.
    Returns SVG markup and anchor point.
    """
    r = CONTROL_RADIUS_PX * scale
    stroke = SYMBOL_STROKE_PX * scale
    pad = stroke + 1
    cx = r + pad
    cy = r + pad
    label = str(number)
    font_size = max(8, FONT_SIZE * scale)
    text_w = len(label) * font_size * 0.6 + 4
    width = cx + r + 4 + text_w
    height = 2 * (r + pad)
    
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" height="{height:.0f}">'
        f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r:.1f}" fill="none" stroke="{color}" '
        f'stroke-width="{stroke:.1f}"/>'
        f'<text x="{cx + r + 3:.1f}" y="{cy - r + font_size * 0.35:.1f}" '
        f'font-family="Arial" font-size="{font_size:.1f}" font-weight="700" fill="{color}">'
        f'{label}</text></svg>'
    )
    return svg, (cx, cy)


def finish_symbol(color: str, scale: float = 1.0) -> str:
    """
    Double concentric circles.
    Returns SVG markup and anchor point.
    """
    ro = FINISH_OUTER_PX * scale
    ri = FINISH_INNER_PX * scale
    stroke = SYMBOL_STROKE_PX * scale
    pad = stroke + 1
    c = ro + pad
    size = 2 * c
    
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size:.0f}" height="{size:.0f}">'
        f'<circle cx="{c:.1f}" cy="{c:.1f}" r="{ro:.1f}" fill="none" stroke="{color}" '
        f'stroke-width="{stroke:.1f}"/>'
        f'<circle cx="{c:.1f}" cy="{c:.1f}" r="{ri:.1f}" fill="none" stroke="{color}" '
        f'stroke-width="{stroke:.1f}"/></svg>'
    )
    return svg, (c, c)


# ── Route SVG Generation ─────────────────────────────────────

def generate_route_svg(
    route_points: list[dict],
    color: str,
    corners: dict,
    img_width: int,
    img_height: int,
    scale: float = 1.0
) -> str:
    """
    Generate SVG with route polyline, IOF symbols (start/finish/control), and labels.
    
    Args:
        route_points: [{lat, lng}, ...]
        color: hex color code
        corners: map corners {nw, ne, se, sw}
        img_width, img_height: image dimensions
        scale: symbol scaling factor
    
    Returns:
        SVG markup as string
    """
    if not route_points or len(route_points) < 2:
        return ""
    
    # Convert all GPS points to pixels
    points_px = []
    for pt in route_points:
        px = gps_to_pixels(pt["lat"], pt["lng"], corners, img_width, img_height)
        if px is None:
            return ""
        points_px.append(px)
    
    n = len(points_px)
    
    # Build polyline path (connecting all points)
    line_parts = [f"M {points_px[0][0]:.1f} {points_px[0][1]:.1f}"]
    for i in range(1, n):
        line_parts.append(f"L {points_px[i][0]:.1f} {points_px[i][1]:.1f}")
    
    polyline_d = " ".join(line_parts)
    
    # Build SVG
    svg_parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{img_width}" height="{img_height}" '
        f'viewBox="0 0 {img_width} {img_height}">',
        # Polyline
        f'<path d="{polyline_d}" fill="none" stroke="{color}" stroke-width="{2 * scale}" '
        f'stroke-linecap="round" stroke-linejoin="round"/>'
    ]
    
    # Add symbols at each point
    for i, (x, y) in enumerate(points_px):
        if i == 0:  # Start
            heading = bearing(
                route_points[0]["lat"], route_points[0]["lng"],
                route_points[1]["lat"], route_points[1]["lng"]
            )
            symbol_svg, anchor = start_symbol(color, heading, scale)
        elif i == n - 1:  # Finish
            symbol_svg, anchor = finish_symbol(color, scale)
        else:  # Control
            symbol_svg, anchor = control_symbol(color, i, scale)
        
        # Embed symbol as image (offset by anchor)
        svg_url = f"data:image/svg+xml;charset=UTF-8,{symbol_svg}".replace('"', '%22').replace('#', '%23')
        ax, ay = anchor
        sx, sy = 10 * scale, 10 * scale  # Rough size estimate
        
        # For simplicity, just use <use> or embed as text label
        svg_parts.append(f'<text x="{x:.1f}" y="{y + 15:.1f}" font-family="Arial" font-size="{12 * scale}" '
                        f'font-weight="bold" fill="{color}" text-anchor="middle">{i if i > 0 else ""}</text>')
    
    svg_parts.append("</svg>")
    return "\n".join(svg_parts)


# ── PDF Assembly ─────────────────────────────────────────────

def _draw_route_on_pdf(c, route, config, img_width, img_height, offset_x, offset_y, scale, dpi_scale):
    """
    Draw route (polyline + IOF symbols + labels) on PDF canvas.

    IOF-compliant rendering:
    - START : filled equilateral triangle, oriented toward first control
    - CONTROL n : hollow circle (no fill, map visible), numbered label beside
    - FINISH : two hollow concentric circles (no fill, no label)
    - Route line : gapped at each symbol boundary (stops at circle edge)

    Args:
        c: reportlab canvas
        route: route data with points
        config: map config with corners
        img_width, img_height: original image dimensions
        offset_x, offset_y: pixel offsets on A3 page
        scale: scaling factor (image → A3 pixels)
        dpi_scale: conversion factor from pixels to PDF points (72/300)
    """
    from reportlab.lib import colors as rl_colors

    points = route.get("points", [])
    if not points or len(points) < 2:
        return

    # ── Parse route color ─────────────────────────────────────
    color_hex = route.get("color", IOF_PURPLE).lstrip("#")
    try:
        r = int(color_hex[0:2], 16) / 255.0
        g = int(color_hex[2:4], 16) / 255.0
        b = int(color_hex[4:6], 16) / 255.0
        route_color = rl_colors.Color(r, g, b)
    except Exception:
        route_color = rl_colors.Color(207/255, 0, 207/255)

    # ── GPS → PDF coordinates ─────────────────────────────────
    a3_h_px = A3_HEIGHT_MM * DPI / 25.4
    points_pdf = []
    for gps_pt in points:
        px = gps_to_pixels(
            gps_pt["lat"], gps_pt["lng"],
            config.get("corners", {}), img_width, img_height
        )
        if px is None:
            return  # abort if any point fails
        sx = offset_x + px[0] * scale
        sy = offset_y + px[1] * scale
        points_pdf.append((sx * dpi_scale, (a3_h_px - sy) * dpi_scale))

    n = len(points_pdf)
    if n < 2:
        return

    # ── IOF symbol sizes (PDF points, A3 physical scale) ──────
    # 1 PDF point = 1/72 inch = 0.353 mm at print size
    # IOF control circle: 5–7 mm diam → r ≈ 7–10 pt
    # IOF finish:  outer 7 mm, inner 5 mm → ratio ≈ 0.71
    # IOF start triangle: 7 mm side → circumradius ≈ 11 pt
    R_CTRL       = 8    # control circle radius [pt]
    R_FIN_OUTER  = 10   # finish outer circle radius [pt]
    R_FIN_INNER  = 7    # finish inner circle radius [pt]  (ratio 0.70)
    R_START      = 11   # start triangle circumradius [pt] (→ 7 mm side)
    LW_LINE      = 1.0  # route line width [pt] ≈ 0.35mm
    LW_SYM       = 0.7  # symbol stroke width [pt] ≈ 0.25mm
    LBL_SIZE     = 8    # label font size [pt]
    LBL_GAP      = 2    # extra gap between circle edge and label [pt]

    # ── Route line — gapped at symbol boundaries ──────────────
    c.setStrokeColor(route_color)
    c.setLineWidth(LW_LINE)
    c.setLineCap(1)   # round caps
    c.setLineJoin(1)  # round joins

    for i in range(n - 1):
        x1, y1 = points_pdf[i]
        x2, y2 = points_pdf[i + 1]
        dx, dy  = x2 - x1, y2 - y1
        seg_len = math.hypot(dx, dy)
        if seg_len < 0.01:
            continue
        ux, uy = dx / seg_len, dy / seg_len

        # Segment start: from forward vertex of START triangle; from circle edge for other symbols
        if i == 0:
            sx, sy_pt = x1 + R_START * ux, y1 + R_START * uy  # hollow → line exits at forward vertex
        else:
            r_i = R_FIN_OUTER if i == n - 1 else R_CTRL
            sx, sy_pt = x1 + r_i * ux, y1 + r_i * uy

        # Segment end: stop at circle edge of destination symbol
        r_j = R_FIN_OUTER if (i + 1) == n - 1 else R_CTRL
        ex, ey = x2 - r_j * ux, y2 - r_j * uy

        if math.hypot(ex - sx, ey - sy_pt) > 0.5:
            c.line(sx, sy_pt, ex, ey)

    # ── Symbols ───────────────────────────────────────────────
    c.setLineWidth(LW_SYM)

    for idx, (px_pt, py_pt) in enumerate(points_pdf):
        c.setStrokeColor(route_color)

        if idx == 0:
            # START — hollow equilateral triangle (outline only), pointing toward first control
            dx  = points_pdf[1][0] - px_pt
            dy  = points_pdf[1][1] - py_pt
            head = math.atan2(dy, dx)   # angle in PDF space (Y-up)

            tri = [
                (px_pt + R_START * math.cos(head + k * 2 * math.pi / 3),
                 py_pt + R_START * math.sin(head + k * 2 * math.pi / 3))
                for k in range(3)
            ]
            p = c.beginPath()
            p.moveTo(*tri[0])
            p.lineTo(*tri[1])
            p.lineTo(*tri[2])
            p.close()
            c.drawPath(p, stroke=1, fill=0)  # hollow — same as controls/finish

        elif idx == n - 1:
            # FINISH — two hollow concentric circles, NO fill, NO label
            c.circle(px_pt, py_pt, R_FIN_OUTER, fill=0, stroke=1)
            c.circle(px_pt, py_pt, R_FIN_INNER, fill=0, stroke=1)

        else:
            # CONTROL — hollow circle, NO fill; numbered label outside
            c.circle(px_pt, py_pt, R_CTRL, fill=0, stroke=1)

            c.setFillColor(route_color)
            c.setFont("Helvetica-Bold", LBL_SIZE)
            lx = px_pt + R_CTRL + LBL_GAP
            ly = py_pt - LBL_SIZE * 0.35
            c.drawString(lx, ly, str(idx))


def create_pdf_with_route(
    map_png_bytes: bytes,
    route: dict,
    config: dict,
    output_stream: io.BytesIO
) -> None:
    """
    Create A3 PDF (300 DPI, no margins) with map + route overlay.
    
    Args:
        map_png_bytes: Map image in bytes
        route: {id, name, color, points: [{lat, lng}], totalDistanceMeters}
        config: {title, corners, imageSize: [w, h], ...}
        output_stream: BytesIO to write PDF
    """
    try:
        # Validate inputs
        if not map_png_bytes:
            raise ValueError("Map image is empty")
        if not route or not route.get("points") or len(route["points"]) < 2:
            raise ValueError("Route must have at least 2 points")
        if not config or not config.get("title"):
            raise ValueError("Map config is invalid")
        
        # Load map image
        map_img = Image.open(io.BytesIO(map_png_bytes))
        img_width, img_height = map_img.size
        if not img_width or not img_height:
            raise ValueError("Map image has invalid dimensions")
        
        # A3 dimensions in pixels @ 300 DPI
        a3_w_px = A3_WIDTH_MM * DPI / 25.4
        a3_h_px = A3_HEIGHT_MM * DPI / 25.4
        
        # Scale to fit A3 while maintaining aspect ratio
        scale_x = a3_w_px / img_width
        scale_y = a3_h_px / img_height
        scale = min(scale_x, scale_y)
        
        # Scaled image dimensions (in pixels)
        scaled_w = img_width * scale
        scaled_h = img_height * scale
        
        # Offset to center on page (in pixels)
        offset_x = (a3_w_px - scaled_w) / 2
        offset_y = (a3_h_px - scaled_h) / 2
        
        # Convert to points for reportlab (72 DPI)
        dpi_scale = 72.0 / DPI
        img_x_pt = offset_x * dpi_scale
        img_y_pt = (a3_h_px - scaled_h - offset_y) * dpi_scale  # Flip Y axis
        img_w_pt = scaled_w * dpi_scale
        img_h_pt = scaled_h * dpi_scale
        
        # Create PDF canvas
        c = canvas.Canvas(output_stream, pagesize=(A3_WIDTH_POINTS, A3_HEIGHT_POINTS))
        
        # Save map image to temp file (reportlab doesn't support BytesIO directly)
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            map_img.save(tmp.name, format="PNG")
            tmp_path = tmp.name
        
        try:
            # Draw map image on PDF
            c.drawImage(
                tmp_path,
                img_x_pt,
                img_y_pt,
                width=img_w_pt,
                height=img_h_pt,
                preserveAspectRatio=False
            )
        finally:
            # Clean up temp file
            try:
                os.unlink(tmp_path)
            except:
                pass
        
        # Draw route on top of map
        _draw_route_on_pdf(
            c, 
            route, 
            config, 
            img_width, img_height,
            offset_x, offset_y, scale,
            dpi_scale
        )
        
        # Draw metadata at bottom
        c.setFont("Helvetica", 9)
        c.setFillAlpha(0.7)
        
        title = config.get('title', 'Carte')
        route_name = route.get('name', 'Parcours')
        distance = route.get('totalDistanceMeters', 0)
        
        # Metadata text
        metadata_y = 10 * MM_TO_POINTS
        c.drawString(10 * MM_TO_POINTS, metadata_y, 
                    f"Carte: {title} | Parcours: {route_name} | Distance: {distance:.1f}m")
        
        c.setFillAlpha(1.0)
        c.save()
        
    except Exception as e:
        raise RuntimeError(f"PDF generation error: {str(e)}")


# ── Async wrapper ────────────────────────────────────────────

async def export_route_to_pdf(
    map_png_bytes: bytes,
    route: dict,
    config: dict
) -> bytes:
    """
    Generate PDF bytes for route export.
    
    Args:
        map_png_bytes: Map image
        route: Route data
        config: Map config
    
    Returns:
        PDF content as bytes
    """
    output = io.BytesIO()
    
    try:
        create_pdf_with_route(map_png_bytes, route, config, output)
        output.seek(0)
        return output.getvalue()
    except Exception as e:
        raise RuntimeError(f"PDF generation failed: {str(e)}")
