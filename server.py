"""
OCAD Map Viewer — FastAPI server.
Upload geo-referenced OCAD PDF exports, browse and navigate maps with Street View.
Maps are stored in Google Cloud Storage for persistence across deployments.
"""

import json
import math
import os
import re
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from google.cloud import storage
from pydantic import BaseModel, Field, field_validator

from processing import process_pdf

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


# ── Static files (HTML, CSS, JS) ──────────────────────────

app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
