"""
OCAD Map Viewer — FastAPI server.
Upload geo-referenced OCAD PDF exports, browse and navigate maps with Street View.
Maps are stored in Google Cloud Storage for persistence across deployments.
"""

import asyncio
import base64
import io
import json
import math
import os
import re
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from google.cloud import storage
from pydantic import BaseModel, Field, field_validator

from processing import process_pdf
from pdf_export import export_route_to_pdf
import numpy as np

load_dotenv()

STATIC_DIR = Path(__file__).parent / "static"
GCS_BUCKET = os.environ.get("GCS_BUCKET", "")

# Local filesystem storage root (used when GCS_BUCKET is not configured).
LOCAL_STORAGE_DIR = Path(os.environ.get("LOCAL_STORAGE_DIR", Path(__file__).parent / "maps"))

# 50 MB server-side upload limit
MAX_UPLOAD_BYTES = 50 * 1024 * 1024

# Slug validation — matches output of processing.slugify()
_MAP_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")

# Route id validation — server-generated UUID hex
_ROUTE_ID_RE = re.compile(r"^[a-f0-9]{32}$")

_CONTENT_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}

# Per-map limits for user-created routes
_MAX_ROUTES_PER_MAP = 50
_MAX_POINTS_PER_ROUTE = 2000

_ERR_MAP_NOT_FOUND = "Map not found"
_ERR_ROUTE_NOT_FOUND = "Route not found"

# PDF export job tracking: {jobId: {status, progress, pdf, error}}
_PDF_JOBS = {}

# Export timeout in seconds
_PDF_EXPORT_TIMEOUT = 60

# Route-choice job tracking: {jobId: {status, progress, choices, error}}
_CHOICE_JOBS: dict = {}

# Timeout for a single route-choice job (pathfinding per alternative is ~15s each)
_CHOICE_TIMEOUT = 60

# Per-map asyncio locks to prevent concurrent traversability generation
_traversability_locks: dict[str, asyncio.Lock] = {}

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

def _bucket():
    """Return the active storage bucket.

    Uses Google Cloud Storage when GCS_BUCKET is configured, otherwise falls
    back to a local filesystem backend (handy for local development without
    GCS credentials). The local backend mimics the subset of the GCS bucket /
    blob API used by this module.
    """
    if GCS_BUCKET:
        return storage.Client().bucket(GCS_BUCKET)
    return _LocalBucket(LOCAL_STORAGE_DIR)


# ── Local filesystem backend (GCS-compatible shim) ─────────

class _LocalBlob:
    """Filesystem-backed stand-in for google.cloud.storage.Blob."""

    def __init__(self, root: Path, name: str):
        self._root = root
        self.name = name
        self._path = root / name

    def exists(self) -> bool:
        return self._path.is_file()

    def download_as_text(self) -> str:
        return self._path.read_text(encoding="utf-8")
    
    def download_as_bytes(self) -> bytes:
        return self._path.read_bytes()

    def upload_from_string(self, data, content_type: str = "application/octet-stream"):
        del content_type  # accepted for GCS API compatibility; not used locally
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, str):
            self._path.write_text(data, encoding="utf-8")
        else:
            self._path.write_bytes(data)

    def upload_from_filename(self, filename: str):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_bytes(Path(filename).read_bytes())

    def open(self, mode: str = "rb"):
        return self._path.open(mode)

    def delete(self):
        if self._path.is_file():
            self._path.unlink()


class _LocalBucket:
    """Filesystem-backed stand-in for google.cloud.storage.Bucket."""

    def __init__(self, root: Path):
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    def blob(self, name: str) -> _LocalBlob:
        return _LocalBlob(self._root, name)

    def list_blobs(self, prefix: str = ""):
        base = self._root
        if not base.exists():
            return
        for path in sorted(base.rglob("*")):
            if not path.is_file():
                continue
            name = path.relative_to(base).as_posix()
            if name.startswith(prefix):
                yield _LocalBlob(self._root, name)

    def delete_blobs(self, blobs):
        for blob in blobs:
            blob.delete()


def _validate_map_id(map_id: str) -> str:
    if not _MAP_ID_RE.match(map_id):
        raise HTTPException(400, "Invalid map id")
    return map_id


def _validate_route_id(route_id: str) -> str:
    if not _ROUTE_ID_RE.match(route_id):
        raise HTTPException(400, "Invalid route id")
    return route_id


def _map_exists(bucket, map_id: str) -> bool:
    return bucket.blob(f"{map_id}/config.json").exists()


def _get_map_config(map_id: str) -> dict:
    """Load and parse map config.json from GCS."""
    blob = _bucket().blob(f"{map_id}/config.json")
    if not blob.exists():
        raise HTTPException(404, _ERR_MAP_NOT_FOUND)
    return json.loads(blob.download_as_text())


# ── Route (course) models ──────────────────────────────────

class RoutePoint(BaseModel):
    lat: float = Field(..., ge=-90.0, le=90.0)
    lng: float = Field(..., ge=-180.0, le=180.0)


class RoutePayload(BaseModel):
    name: str = Field("", max_length=120)
    color: str = Field("#7c3aed", max_length=9)
    points: list[RoutePoint] = Field(default_factory=list)

    @field_validator("points")
    @classmethod
    def _limit_points(cls, v: list[RoutePoint]) -> list[RoutePoint]:
        if len(v) > _MAX_POINTS_PER_ROUTE:
            raise ValueError(f"A route cannot exceed {_MAX_POINTS_PER_ROUTE} points")
        return v

    @field_validator("color")
    @classmethod
    def _valid_color(cls, v: str) -> str:
        if not re.match(r"^#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?$", v):
            raise ValueError("Color must be a hex value like #7c3aed")
        return v


class EmbargoZone(BaseModel):
    """Polygone de zone embargo — minimum 3 points"""
    points: list[dict] = Field(default_factory=list)
    
    @field_validator('points')
    @classmethod
    def validate_polygon(cls, v: list[dict]) -> list[dict]:
        if len(v) < 3:
            raise ValueError("Minimum 3 points required for embargo zone")
        if len(v) > 100:
            raise ValueError("Maximum 100 points per embargo zone")
        for i, p in enumerate(v):
            lat = p.get('lat')
            lng = p.get('lng')
            if lat is None or lng is None:
                raise ValueError(f"Point {i} missing 'lat' or 'lng'")
            if not (-90 <= lat <= 90):
                raise ValueError(f"Point {i} latitude out of range [-90, 90]")
            if not (-180 <= lng <= 180):
                raise ValueError(f"Point {i} longitude out of range [-180, 180]")
        return v


def _haversine_m(a: RoutePoint, b: RoutePoint) -> float:
    """Great-circle distance between two points in metres."""
    r = 6371000.0
    p1 = math.radians(a.lat)
    p2 = math.radians(b.lat)
    dphi = math.radians(b.lat - a.lat)
    dlmb = math.radians(b.lng - a.lng)
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(h)))


def _total_distance_m(points: list[RoutePoint]) -> float:
    return round(sum(_haversine_m(points[i], points[i + 1]) for i in range(len(points) - 1)), 1)


def is_point_in_polygon(point: dict, polygon: list[dict]) -> bool:
    """
    Ray-casting algorithm — checks if point {lat, lng} is inside polygon.
    Used to validate routes against embargo zones.
    """
    lat = point.get('lat')
    lng = point.get('lng')
    if lat is None or lng is None:
        return False
    
    n = len(polygon)
    if n < 3:
        return False
    
    inside = False
    p1_lat = polygon[0]['lat']
    p1_lng = polygon[0]['lng']
    
    for i in range(1, n + 1):
        p2_lat = polygon[i % n]['lat']
        p2_lng = polygon[i % n]['lng']
        
        if lng > min(p1_lng, p2_lng):
            if lng <= max(p1_lng, p2_lng):
                if lat <= max(p1_lat, p2_lat):
                    if p1_lng != p2_lng:
                        xinters = (lng - p1_lng) * (p2_lat - p1_lat) / \
                                  (p2_lng - p1_lng) + p1_lat
                    if p1_lat == p2_lat or lat <= xinters:
                        inside = not inside
        p1_lat = p2_lat
        p1_lng = p2_lng
    
    return inside


def _route_blob_path(map_id: str, route_id: str) -> str:
    return f"{map_id}/routes/{route_id}.json"


def _serialize_route(route_id: str, map_id: str, payload: RoutePayload,
                     created_at: str, updated_at: str) -> dict:
    return {
        "id": route_id,
        "mapId": map_id,
        "name": payload.name,
        "color": payload.color,
        "points": [{"lat": p.lat, "lng": p.lng} for p in payload.points],
        "totalDistanceMeters": _total_distance_m(payload.points),
        "createdAt": created_at,
        "updatedAt": updated_at,
    }



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
        raise HTTPException(404, _ERR_MAP_NOT_FOUND)
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
        raise HTTPException(404, _ERR_MAP_NOT_FOUND)
    bucket.delete_blobs(blobs)
    return {"deleted": map_id}


# ── Serve map files from GCS ───────────────────────────────

@app.get("/maps/{map_id}/{filename}")
def serve_map_file(map_id: str, filename: str):
    """Stream map image/thumbnail from GCS.
    
    Fallback: if map-mobile.png doesn't exist, serve map.png instead.
    """
    _validate_map_id(map_id)
    suffix = Path(filename).suffix.lower()
    if suffix not in _CONTENT_TYPES:
        raise HTTPException(400, "Unsupported file type")
    blob = _bucket().blob(f"{map_id}/{filename}")
    if not blob.exists():
        # Fallback: if map-mobile.png is missing, try map.png
        if filename == "map-mobile.png":
            blob = _bucket().blob(f"{map_id}/map.png")
            if blob.exists():
                return StreamingResponse(
                    blob.open("rb"),
                    media_type=_CONTENT_TYPES[".png"],
                    headers={"Cache-Control": "public, max-age=31536000, immutable"},
                )
        raise HTTPException(404, "File not found")
    return StreamingResponse(
        blob.open("rb"),
        media_type=_CONTENT_TYPES[suffix],
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


# ── Routes (courses) CRUD ──────────────────────────────────

@app.get("/api/maps/{map_id}/routes")
def list_routes(map_id: str):
    """List all routes (courses) attached to a map."""
    _validate_map_id(map_id)
    bucket = _bucket()
    if not _map_exists(bucket, map_id):
        raise HTTPException(404, _ERR_MAP_NOT_FOUND)
    routes = []
    for blob in bucket.list_blobs(prefix=f"{map_id}/routes/"):
        if blob.name.endswith(".json"):
            routes.append(json.loads(blob.download_as_text()))
    return sorted(routes, key=lambda r: r.get("createdAt", ""))


@app.post("/api/maps/{map_id}/routes")
def create_route(map_id: str, payload: RoutePayload):
    """Create a new route for a map."""
    _validate_map_id(map_id)
    bucket = _bucket()
    if not _map_exists(bucket, map_id):
        raise HTTPException(404, _ERR_MAP_NOT_FOUND)
    
    config = _get_map_config(map_id)
    
    # Validate route points against embargo zone if it exists
    if 'embargoPoly' in config and config['embargoPoly']:
        embargo_points = config['embargoPoly']['points']
        for idx, point in enumerate(payload.points):
            if not is_point_in_polygon({'lat': point.lat, 'lng': point.lng}, embargo_points):
                raise HTTPException(
                    400,
                    f"Route point #{idx + 1} ({point.lat:.4f}, {point.lng:.4f}) is outside embargo zone"
                )

    existing = sum(
        1 for b in bucket.list_blobs(prefix=f"{map_id}/routes/") if b.name.endswith(".json")
    )
    if existing >= _MAX_ROUTES_PER_MAP:
        raise HTTPException(409, f"This map already has the maximum of {_MAX_ROUTES_PER_MAP} routes")

    route_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc).isoformat()
    route = _serialize_route(route_id, map_id, payload, now, now)
    bucket.blob(_route_blob_path(map_id, route_id)).upload_from_string(
        json.dumps(route), content_type="application/json"
    )
    return JSONResponse(route, status_code=201)


@app.get("/api/maps/{map_id}/routes/{route_id}")
def get_route(map_id: str, route_id: str):
    """Get a single route."""
    _validate_map_id(map_id)
    _validate_route_id(route_id)
    blob = _bucket().blob(_route_blob_path(map_id, route_id))
    if not blob.exists():
        raise HTTPException(404, _ERR_ROUTE_NOT_FOUND)
    return json.loads(blob.download_as_text())


@app.put("/api/maps/{map_id}/routes/{route_id}")
def update_route(map_id: str, route_id: str, payload: RoutePayload):
    """Replace an existing route."""
    _validate_map_id(map_id)
    _validate_route_id(route_id)
    blob = _bucket().blob(_route_blob_path(map_id, route_id))
    if not blob.exists():
        raise HTTPException(404, _ERR_ROUTE_NOT_FOUND)
    
    config = _get_map_config(map_id)
    
    # Validate route points against embargo zone if it exists
    if 'embargoPoly' in config and config['embargoPoly']:
        embargo_points = config['embargoPoly']['points']
        for idx, point in enumerate(payload.points):
            if not is_point_in_polygon({'lat': point.lat, 'lng': point.lng}, embargo_points):
                raise HTTPException(
                    400,
                    f"Route point #{idx + 1} ({point.lat:.4f}, {point.lng:.4f}) is outside embargo zone"
                )
    
    existing = json.loads(blob.download_as_text())
    route = _serialize_route(
        route_id, map_id, payload,
        existing.get("createdAt", datetime.now(timezone.utc).isoformat()),
        datetime.now(timezone.utc).isoformat(),
    )
    blob.upload_from_string(json.dumps(route), content_type="application/json")
    return route


@app.delete("/api/maps/{map_id}/routes/{route_id}")
def delete_route(map_id: str, route_id: str):
    """Delete a route."""
    _validate_map_id(map_id)
    _validate_route_id(route_id)
    blob = _bucket().blob(_route_blob_path(map_id, route_id))
    if not blob.exists():
        raise HTTPException(404, _ERR_ROUTE_NOT_FOUND)
    blob.delete()
    return {"deleted": route_id}


# ── Route PDF Export ──────────────────────────────────────────

def _generate_pdf_sync_wrapper(job_id: str, map_id: str, route_id: str) -> None:
    """
    Synchronous wrapper to launch async PDF generation in background thread.
    BackgroundTasks calls this synchronously, but we need async context.
    """
    def run_in_thread():
        try:
            asyncio.run(_generate_pdf_async(job_id, map_id, route_id))
        except Exception as e:
            print(f"Error in PDF generation background task: {e}")
            import traceback
            traceback.print_exc()
            if job_id in _PDF_JOBS:
                _PDF_JOBS[job_id]["status"] = "error"
                _PDF_JOBS[job_id]["error"] = str(e)
    
    # Launch in separate thread so it doesn't block the main event loop
    thread = threading.Thread(target=run_in_thread, daemon=True)
    thread.start()


async def _generate_pdf_async(job_id: str, map_id: str, route_id: str) -> None:
    """
    Generate PDF in background, update job status.
    Called via BackgroundTasks.add_task().
    """
    try:
        _validate_map_id(map_id)
        _validate_route_id(route_id)
        
        _PDF_JOBS[job_id]["status"] = "generating"
        _PDF_JOBS[job_id]["progress"] = 10
        
        # Load route
        route_blob = _bucket().blob(_route_blob_path(map_id, route_id))
        if not route_blob.exists():
            raise ValueError(_ERR_ROUTE_NOT_FOUND)
        route = json.loads(route_blob.download_as_text())
        
        # Validate route has points
        if not route.get("points") or len(route["points"]) < 2:
            raise ValueError("Route must have at least 2 points")
        
        _PDF_JOBS[job_id]["progress"] = 20
        
        # Load config
        config = _get_map_config(map_id)
        
        _PDF_JOBS[job_id]["progress"] = 30
        
        # Load map image (use map.png for high resolution)
        map_blob = _bucket().blob(f"{map_id}/map.png")
        if not map_blob.exists():
            raise ValueError("Map image not found")
        map_png_bytes = map_blob.download_as_bytes()
        
        _PDF_JOBS[job_id]["progress"] = 50
        
        # Generate PDF
        pdf_bytes = await export_route_to_pdf(map_png_bytes, route, config)
        
        _PDF_JOBS[job_id]["progress"] = 90
        _PDF_JOBS[job_id]["pdf"] = pdf_bytes
        _PDF_JOBS[job_id]["progress"] = 100
        _PDF_JOBS[job_id]["status"] = "done"
        
    except Exception as e:
        _PDF_JOBS[job_id]["status"] = "error"
        _PDF_JOBS[job_id]["error"] = str(e)
        _PDF_JOBS[job_id]["progress"] = 0


@app.post("/api/maps/{map_id}/routes/{route_id}/export-pdf")
async def start_export_pdf(map_id: str, route_id: str, background_tasks: BackgroundTasks):
    """
    Start PDF export job.
    Returns jobId to track progress via SSE.
    """
    try:
        _validate_map_id(map_id)
        _validate_route_id(route_id)
        
        # Verify route exists
        if not _bucket().blob(_route_blob_path(map_id, route_id)).exists():
            raise HTTPException(404, _ERR_ROUTE_NOT_FOUND)
        
        job_id = uuid.uuid4().hex
        _PDF_JOBS[job_id] = {
            "status": "queued",
            "progress": 0,
            "pdf": None,
            "error": None
        }
        
        # Launch background task
        # Use a wrapper function to run async code in background
        background_tasks.add_task(_generate_pdf_sync_wrapper, job_id, map_id, route_id)
        
        return {"jobId": job_id, "status": "queued"}
    
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error in start_export_pdf: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(500, f"Failed to start PDF export: {str(e)}")


@app.get("/api/maps/{map_id}/routes/{route_id}/export-pdf/{job_id}/stream")
async def export_pdf_stream(map_id: str, route_id: str, job_id: str):
    """
    SSE stream for PDF generation progress.
    Sends events: "data: {\"progress\": X}"
    Final: "data: {\"progress\": 100, \"done\": true}"
    """
    _validate_map_id(map_id)
    _validate_route_id(route_id)
    
    async def event_generator():
        start_time = time.time()
        
        while time.time() - start_time < _PDF_EXPORT_TIMEOUT:
            if job_id not in _PDF_JOBS:
                yield 'data: {"error": "Job not found"}\n\n'
                break
            
            job = _PDF_JOBS[job_id]
            
            # Error occurred
            if job["status"] == "error":
                yield f'data: {{"error": "{job["error"]}"}}\n\n'
                break
            
            # Job completed
            if job["status"] == "done":
                pdf_bytes = job["pdf"]
                if pdf_bytes:
                    # Encode PDF as base64 for SSE transmission
                    pdf_b64 = base64.b64encode(pdf_bytes).decode("utf-8")
                    yield f'data: {{"progress": 100, "done": true, "pdf": "{pdf_b64}"}}\n\n'
                else:
                    yield 'data: {"error": "PDF generation failed"}\n\n'
                break
            
            # Send progress update
            progress = job.get("progress", 0)
            yield f'data: {{"progress": {progress}}}\n\n'
            
            await asyncio.sleep(0.3)  # Send updates every 300ms
        
        else:
            # Timeout
            yield 'data: {"error": "PDF generation timeout"}\n\n'
        
        # Cleanup
        if job_id in _PDF_JOBS:
            del _PDF_JOBS[job_id]
    
    return StreamingResponse(event_generator(), media_type="text/event-stream")


# ── Embargo Zone CRUD ──────────────────────────────────────

@app.post("/api/maps/{map_id}/embargo")
def create_embargo(map_id: str, embargo: EmbargoZone):
    """Create or replace embargo zone for a map."""
    _validate_map_id(map_id)
    config = _get_map_config(map_id)
    
    # Add/replace embargoPoly in config
    config['embargoPoly'] = {
        'points': embargo.points,
        'createdAt': datetime.now(timezone.utc).isoformat() + 'Z',
        'updatedAt': datetime.now(timezone.utc).isoformat() + 'Z'
    }
    
    # Persist config.json
    try:
        _bucket().blob(f"{map_id}/config.json").upload_from_string(
            json.dumps(config, indent=2),
            content_type="application/json"
        )
    except Exception as e:
        raise HTTPException(500, "Failed to save embargo zone")
    
    return config


@app.delete("/api/maps/{map_id}/embargo")
def delete_embargo(map_id: str):
    """Delete embargo zone from a map."""
    _validate_map_id(map_id)
    config = _get_map_config(map_id)
    
    # Delete embargoPoly if exists
    if 'embargoPoly' in config:
        del config['embargoPoly']
    
    # Persist config.json
    try:
        _bucket().blob(f"{map_id}/config.json").upload_from_string(
            json.dumps(config, indent=2),
            content_type="application/json"
        )
    except Exception as e:
        raise HTTPException(500, "Failed to delete embargo zone")
    
    return {"status": "deleted"}



# ── Route choices ─────────────────────────────────────────

class _LatLng(BaseModel):
    lat: float
    lng: float


class RouteChoicePayload(BaseModel):
    from_point: _LatLng
    to_point: _LatLng
    count: int = Field(default=3, ge=1, le=3)


@app.post("/api/maps/{map_id}/route-choices")
async def start_route_choices(
    map_id: str,
    payload: RouteChoicePayload,
    background_tasks: BackgroundTasks,
):
    """
    Launch a route-choice analysis job.
    Returns {jobId} immediately; monitor via SSE stream endpoint.
    """
    _validate_map_id(map_id)
    config = _get_map_config(map_id)

    job_id = uuid.uuid4().hex
    _CHOICE_JOBS[job_id] = {
        "status": "queued",
        "progress": 0,
        "choices": None,
        "error": None,
        "routesFound": 0,
    }

    background_tasks.add_task(
        _run_choice_wrapper,
        job_id,
        map_id,
        config,
        payload.from_point.model_dump(),
        payload.to_point.model_dump(),
        payload.count,
    )
    return {"jobId": job_id}


@app.get("/api/maps/{map_id}/route-choices/{job_id}/stream")
async def stream_route_choices(map_id: str, job_id: str):
    """SSE stream for route-choice progress.  Pattern mirrors export-pdf stream."""
    _validate_map_id(map_id)

    async def _gen():
        start = time.time()
        while time.time() - start < _CHOICE_TIMEOUT:
            if job_id not in _CHOICE_JOBS:
                yield 'data: {"error":"Job not found"}\n\n'
                return
            job = _CHOICE_JOBS[job_id]

            if job["status"] == "error":
                payload = json.dumps({"error": job["error"]})
                yield f"data: {payload}\n\n"
                break

            if job["status"] == "done":
                payload = json.dumps({
                    "done": True,
                    "choices": job["choices"],
                    "routesFound": job["routesFound"],
                })
                yield f"data: {payload}\n\n"
                break

            yield f'data: {{"progress":{job["progress"]}}}\n\n'
            await asyncio.sleep(0.4)
        else:
            yield 'data: {"error":"Timeout"}\n\n'

        if job_id in _CHOICE_JOBS:
            del _CHOICE_JOBS[job_id]

    return StreamingResponse(_gen(), media_type="text/event-stream")


@app.get("/api/maps/{map_id}/traversability")
async def debug_traversability(map_id: str):
    """Return the traversability cost grid as a grayscale PNG (debug)."""
    _validate_map_id(map_id)
    from traversability import TRAVERSABILITY_VERSION, mask_to_debug_png

    cache_key = f"{map_id}/traversability_{TRAVERSABILITY_VERSION}.npy"
    blob = _bucket().blob(cache_key)

    if not blob.exists():
        raise HTTPException(404, "Traversability not yet computed for this map")

    grid = np.load(io.BytesIO(blob.download_as_bytes()))
    png_bytes = mask_to_debug_png(grid)
    return Response(content=png_bytes, media_type="image/png")


# ── Route-choice background helpers ──────────────────────

def _run_choice_wrapper(
    job_id: str,
    map_id: str,
    config: dict,
    from_pt: dict,
    to_pt: dict,
    count: int,
) -> None:
    """Thread entry point: wraps async logic so BackgroundTasks can call it."""
    def _run():
        try:
            asyncio.run(_run_choice_async(job_id, map_id, config, from_pt, to_pt, count))
        except Exception as exc:
            import traceback
            traceback.print_exc()
            if job_id in _CHOICE_JOBS:
                _CHOICE_JOBS[job_id]["status"] = "error"
                _CHOICE_JOBS[job_id]["error"] = str(exc)

    t = threading.Thread(target=_run, daemon=True)
    t.start()


async def _run_choice_async(
    job_id: str,
    map_id: str,
    config: dict,
    from_pt: dict,
    to_pt: dict,
    count: int,
) -> None:
    """
    Full route-choice pipeline:
    1. Load or compute traversability grid.
    2. Snap GPS control points to grid cells.
    3. Find diverse routes using via-vertex Dijkstra (Phase 2) with Theta* fallback.
    4. Convert grid paths back to GPS.
    5. Store result in _CHOICE_JOBS.
    """
    from traversability import TRAVERSABILITY_VERSION, build_traversability_mask
    from pathfinding import (
        find_diverse_routes,
        gps_to_grid,
        grid_to_gps,
        haversine_m,
        nearest_passable,
        path_to_gps,
    )

    def _update(progress: int):
        if job_id in _CHOICE_JOBS:
            _CHOICE_JOBS[job_id]["progress"] = progress

    corners = config.get("corners", {})
    scale = config.get("scale")

    # ── Step 1: load traversability grid ──
    lock = _traversability_locks.setdefault(map_id, asyncio.Lock())
    async with lock:
        _update(10)
        cache_key = f"{map_id}/traversability_{TRAVERSABILITY_VERSION}.npy"
        blob = _bucket().blob(cache_key)

        if blob.exists():
            _update(30)
            grid = np.load(io.BytesIO(blob.download_as_bytes()))
        else:
            _update(20)
            # Not pre-computed — generate now (longer, user sees progress message)
            map_blob = _bucket().blob(f"{map_id}/map.png")
            if not map_blob.exists():
                raise ValueError("Map image not found")
            png_bytes = map_blob.download_as_bytes()
            _update(30)
            grid = build_traversability_mask(png_bytes, scale)
            try:
                buf = io.BytesIO()
                np.save(buf, grid)
                blob.upload_from_string(buf.getvalue(), content_type="application/octet-stream")
            except Exception:
                pass  # cache write failure is non-fatal

    _update(40)
    grid_h, grid_w = grid.shape

    # ── Step 2: snap GPS to grid ──
    start_rc = gps_to_grid(from_pt["lat"], from_pt["lng"], corners, grid_h, grid_w)
    end_rc = gps_to_grid(to_pt["lat"], to_pt["lng"], corners, grid_h, grid_w)

    if start_rc is None or end_rc is None:
        raise ValueError("Points outside map bounds")

    start_rc = nearest_passable(grid, start_rc[0], start_rc[1])
    if start_rc is None:
        raise ValueError("start_blocked: Le point de départ est sur une zone infranchissable")

    end_rc = nearest_passable(grid, end_rc[0], end_rc[1])
    if end_rc is None:
        raise ValueError("end_blocked: Le point d'arrivée est sur une zone infranchissable")

    _update(50)

    # ── Step 3: find diverse routes ──
    deadline = time.time() + 45  # generous timeout for up to 3 routes
    paths = find_diverse_routes(grid, start_rc, end_rc, k=count, timeout=15.0)

    _update(80)

    if not paths:
        raise ValueError("Aucun chemin trouvé entre ces deux balises")

    # ── Step 4: direct distance for % display ──
    direct_m = haversine_m(from_pt, to_pt)

    _LABELS = ["A", "B", "C"]
    _COLORS = ["#1565C0", "#C62828", "#2E7D32"]

    choices = []
    for i, path in enumerate(paths):
        gps_pts = path_to_gps(path, corners, grid_h, grid_w, epsilon=1.5)
        dist_m = sum(
            haversine_m(gps_pts[j], gps_pts[j + 1]) for j in range(len(gps_pts) - 1)
        )
        pct = ((dist_m / direct_m) - 1) * 100 if direct_m > 0 else 0
        choices.append({
            "label": _LABELS[i],
            "color": _COLORS[i],
            "points": gps_pts,
            "distanceMeters": round(dist_m, 1),
            "directDistanceMeters": round(direct_m, 1),
            "detourPercent": round(pct, 1),
        })

    _update(100)
    if job_id in _CHOICE_JOBS:
        _CHOICE_JOBS[job_id]["status"] = "done"
        _CHOICE_JOBS[job_id]["choices"] = choices
        _CHOICE_JOBS[job_id]["routesFound"] = len(choices)


# ── Static files (HTML, CSS, JS) ──────────────────────────

app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
