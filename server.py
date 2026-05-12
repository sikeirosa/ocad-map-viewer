"""
OCAD Map Viewer — FastAPI server.
Upload geo-referenced OCAD PDF exports, browse and navigate maps with Street View.
"""

import json
import os
import shutil
import tempfile
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from processing import process_pdf

load_dotenv()

BASE_DIR = Path(__file__).parent
MAPS_DIR = BASE_DIR / "maps"
STATIC_DIR = BASE_DIR / "static"

# 50 MB server-side upload limit
MAX_UPLOAD_BYTES = 50 * 1024 * 1024

app = FastAPI(title="OCAD Map Viewer")


# ── Security headers middleware ────────────────────────────

@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response: Response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), camera=(), microphone=()"
    # Allow Google Maps scripts/styles/fonts but block inline scripts not explicitly nonce'd
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' https://maps.googleapis.com https://maps.gstatic.com; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data: https://*.googleapis.com https://*.gstatic.com; "
        "connect-src 'self' https://maps.googleapis.com; "
        "frame-ancestors 'none';"
    )
    return response


# ── API routes ──────────────────────────────────────────────

@app.get("/api/config")
def get_config():
    """Return public runtime configuration (Google Maps API key)."""
    api_key = os.environ.get("GOOGLE_MAPS_API_KEY", "")
    if not api_key:
        raise HTTPException(500, "GOOGLE_MAPS_API_KEY is not configured")
    map_id = os.environ.get("GOOGLE_MAPS_MAP_ID", "")
    return {"googleMapsApiKey": api_key, "googleMapsMapId": map_id}


@app.get("/api/maps")
def list_maps():
    """List all available maps."""
    maps = []
    if MAPS_DIR.exists():
        for config_path in sorted(MAPS_DIR.glob("*/config.json")):
            with open(config_path) as f:
                maps.append(json.load(f))
    return maps


@app.get("/api/maps/{map_id}")
def get_map(map_id: str):
    """Get config for a specific map."""
    config_path = _safe_map_path(map_id, "config.json")
    if not config_path.exists():
        raise HTTPException(404, "Map not found")
    with open(config_path) as f:
        return json.load(f)


@app.post("/api/upload")
async def upload_map(
    file: UploadFile = File(...),
    title: str = Form(None),
):
    """Upload a geo-referenced OCAD PDF and process it."""
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF files are accepted")

    # Server-side size limit — read up to MAX_UPLOAD_BYTES+1 to detect oversized files
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "File exceeds the 50 MB limit")

    # Save uploaded file to temp location
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        config = process_pdf(tmp_path, str(MAPS_DIR), title=title or None, original_filename=file.filename)
    except ValueError as e:
        Path(tmp_path).unlink(missing_ok=True)
        raise HTTPException(400, str(e))
    except Exception:
        Path(tmp_path).unlink(missing_ok=True)
        raise HTTPException(500, "Processing failed. Please check that the PDF is a valid geo-referenced OCAD export.")
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    return JSONResponse(config, status_code=201)


@app.delete("/api/maps/{map_id}")
def delete_map(map_id: str):
    """Delete a map and its files."""
    map_dir = _safe_map_dir(map_id)
    if not map_dir.exists():
        raise HTTPException(404, "Map not found")
    shutil.rmtree(map_dir)
    return {"deleted": map_id}


# ── Safe path helpers ──────────────────────────────────────

def _safe_map_dir(map_id: str) -> Path:
    """Resolve map directory and assert it stays within MAPS_DIR."""
    resolved = (MAPS_DIR / map_id).resolve()
    if not resolved.is_relative_to(MAPS_DIR.resolve()):
        raise HTTPException(400, "Invalid map id")
    return resolved


def _safe_map_path(map_id: str, filename: str) -> Path:
    """Resolve a file path inside a map directory; reject traversal attempts."""
    resolved = (MAPS_DIR / map_id / filename).resolve()
    if not resolved.is_relative_to(MAPS_DIR.resolve()):
        raise HTTPException(400, "Invalid path")
    return resolved


# ── Serve map files (PNG, thumbnails) ──────────────────────

@app.get("/maps/{map_id}/{filename}")
def serve_map_file(map_id: str, filename: str):
    """Serve map image/thumbnail files."""
    file_path = _safe_map_path(map_id, filename)
    if not file_path.exists():
        raise HTTPException(404, "File not found")
    return FileResponse(file_path)


# ── Static files (HTML, CSS, JS) ──────────────────────────

app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
