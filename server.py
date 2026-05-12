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
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from processing import process_pdf

load_dotenv()

BASE_DIR = Path(__file__).parent
MAPS_DIR = BASE_DIR / "maps"
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="OCAD Map Viewer")


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
    config_path = MAPS_DIR / map_id / "config.json"
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

    # Save uploaded file to temp location
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

    try:
        config = process_pdf(tmp_path, str(MAPS_DIR), title=title or None, original_filename=file.filename)
    except ValueError as e:
        Path(tmp_path).unlink(missing_ok=True)
        raise HTTPException(400, str(e))
    except Exception as e:
        Path(tmp_path).unlink(missing_ok=True)
        raise HTTPException(500, f"Processing failed: {e}")
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    return JSONResponse(config, status_code=201)


@app.delete("/api/maps/{map_id}")
def delete_map(map_id: str):
    """Delete a map and its files."""
    map_dir = MAPS_DIR / map_id
    if not map_dir.exists():
        raise HTTPException(404, "Map not found")
    shutil.rmtree(map_dir)
    return {"deleted": map_id}


# ── Serve map files (PNG, thumbnails) ──────────────────────

@app.get("/maps/{map_id}/{filename}")
def serve_map_file(map_id: str, filename: str):
    """Serve map image/thumbnail files."""
    # Prevent path traversal
    if ".." in map_id or ".." in filename:
        raise HTTPException(400, "Invalid path")
    file_path = MAPS_DIR / map_id / filename
    if not file_path.exists():
        raise HTTPException(404, "File not found")
    return FileResponse(file_path)


# ── Static files (HTML, CSS, JS) ──────────────────────────

app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
