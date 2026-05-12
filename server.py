"""
OCAD Map Viewer — FastAPI server.
Upload geo-referenced OCAD PDF exports, browse and navigate maps with Street View.
Maps are stored in Google Cloud Storage for persistence across deployments.
"""

import json
import os
import re
import tempfile
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from google.cloud import storage

from processing import process_pdf

load_dotenv()

STATIC_DIR = Path(__file__).parent / "static"
GCS_BUCKET = os.environ.get("GCS_BUCKET", "")

# 50 MB server-side upload limit
MAX_UPLOAD_BYTES = 50 * 1024 * 1024

# Slug validation — matches output of processing.slugify()
_MAP_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")

_CONTENT_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}

app = FastAPI(title="OCAD Map Viewer")


# ── Security headers middleware ────────────────────────────

@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response: Response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), camera=(), microphone=()"
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


# ── GCS helpers ────────────────────────────────────────────

def _bucket() -> storage.Bucket:
    if not GCS_BUCKET:
        raise HTTPException(500, "GCS_BUCKET is not configured")
    return storage.Client().bucket(GCS_BUCKET)


def _validate_map_id(map_id: str) -> str:
    if not _MAP_ID_RE.match(map_id):
        raise HTTPException(400, "Invalid map id")
    return map_id


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
    """List all available maps from GCS."""
    bucket = _bucket()
    maps = []
    for blob in bucket.list_blobs():
        if blob.name.endswith("/config.json"):
            maps.append(json.loads(blob.download_as_text()))
    return sorted(maps, key=lambda m: m.get("id", ""))


@app.get("/api/maps/{map_id}")
def get_map(map_id: str):
    """Get config for a specific map."""
    _validate_map_id(map_id)
    blob = _bucket().blob(f"{map_id}/config.json")
    if not blob.exists():
        raise HTTPException(404, "Map not found")
    return json.loads(blob.download_as_text())


@app.post("/api/upload")
async def upload_map(
    file: UploadFile = File(...),
    title: str = Form(None),
):
    """Upload a geo-referenced OCAD PDF, process it, and store in GCS."""
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF files are accepted")

    # Server-side size limit
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "File exceeds the 50 MB limit")

    with tempfile.TemporaryDirectory() as tmp_dir:
        pdf_path = Path(tmp_dir) / "upload.pdf"
        pdf_path.write_bytes(content)

        try:
            config = process_pdf(
                str(pdf_path), tmp_dir,
                title=title or None,
                original_filename=file.filename,
            )
        except ValueError as e:
            raise HTTPException(400, str(e))
        except Exception:
            raise HTTPException(
                500,
                "Processing failed. Please check that the PDF is a valid geo-referenced OCAD export.",
            )

        # Upload generated files to GCS
        bucket = _bucket()
        map_id = config["id"]
        for f in (Path(tmp_dir) / map_id).iterdir():
            bucket.blob(f"{map_id}/{f.name}").upload_from_filename(str(f))

    return JSONResponse(config, status_code=201)


@app.delete("/api/maps/{map_id}")
def delete_map(map_id: str):
    """Delete a map and all its files from GCS."""
    _validate_map_id(map_id)
    bucket = _bucket()
    blobs = list(bucket.list_blobs(prefix=f"{map_id}/"))
    if not blobs:
        raise HTTPException(404, "Map not found")
    bucket.delete_blobs(blobs)
    return {"deleted": map_id}


# ── Serve map files from GCS ───────────────────────────────

@app.get("/maps/{map_id}/{filename}")
def serve_map_file(map_id: str, filename: str):
    """Stream map image/thumbnail from GCS."""
    _validate_map_id(map_id)
    suffix = Path(filename).suffix.lower()
    if suffix not in _CONTENT_TYPES:
        raise HTTPException(400, "Unsupported file type")
    blob = _bucket().blob(f"{map_id}/{filename}")
    if not blob.exists():
        raise HTTPException(404, "File not found")
    return StreamingResponse(
        blob.open("rb"),
        media_type=_CONTENT_TYPES[suffix],
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


# ── Static files (HTML, CSS, JS) ──────────────────────────

app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")


# ── Static files (HTML, CSS, JS) ──────────────────────────

app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
