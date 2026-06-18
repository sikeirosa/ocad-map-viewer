"""
Route-choice traversability analysis.

Classifies map.png pixels using the ISSprOM sprint color palette with
Chebyshev L∞ distance matching, builds a weighted cost raster for
Theta* pathfinding, and caches the result in GCS / local storage.

Algorithm version: v1 — increment TRAVERSABILITY_VERSION when thresholds change.
"""

from __future__ import annotations

import io
from pathlib import Path

import numpy as np
from PIL import Image

TRAVERSABILITY_VERSION = "v4"
_CACHE_FILENAME = f"traversability_{TRAVERSABILITY_VERSION}.npy"

# Infinite cost = physically impassable.
INF = np.inf

# Max grid size for pathfinding (cap to keep A* fast).
_MAX_GRID_H = 800
_MAX_GRID_W = 1000

# Base downsample factor (300 DPI → ~60 DPI equivalent).
_DOWNSAMPLE_BASE = 5

# Safety buffer around impassable areas in real-world metres.
# The max-pool already provides a 0.5-cell buffer, so we keep explicit dilation
# small.  Sprint maps (≤ 1:5000) use 0 extra cells to preserve narrow passages.
_SAFETY_BUFFER_METRES = 1.5

# ── ISSprOM sprint color palette ────────────────────────────────────────────
#
# Each entry: (label, (R, G, B) center, tolerance, cost)
# Matching uses Chebyshev distance (L∞): max(|ΔR|, |ΔG|, |ΔB|) < tolerance.
# Nearest-match wins (smallest Chebyshev distance takes priority).
#
# Cost meanings:
#   0.8 = paved/road/courtyard (fastest)
#   1.0 = open terrain (reference)
#   1.5 = light/medium vegetation (slow)
#   INF = impassable
#
# Color sources: ISSprOM 2019 spec + empirical calibration against OCAD exports.
# v3 changes vs v2:
#   - black_features tol 40→55 (covers pure-black walls/fences RGB(0,0,0))
#   - green_dense tol 35→45 (covers darker impassable greens RGB(40,100,40))
#   - added paving_stones (ISSprOM 505) at cost 0.8
#   - added sandy_ground (ISSprOM 418) at cost 0.8
#   - green_light center adjusted + tol widened for broader slow-veg coverage
#   - added green_medium for forest slow-run shades
# ──────────────────────────────────────────────────────────────────────────────
_PALETTE: list[tuple[str, tuple[int, int, int], int, float]] = [
    # ── Impassable ─────────────────────────────────────────────────────────
    ("magenta_forbidden",  (200,  50, 200),  60,  INF),    # forbidden / OOB zone
    ("blue_water",         ( 80, 150, 220),  40,  INF),    # water features (OCAD dark blue)
    # tol=45: covers R/B in [35,125], G in [95,185] — catches RGB(40,100,40) dark green
    ("green_dense",        ( 80, 140,  80),  45,  INF),    # impassable vegetation (ISSprOM 408)
    ("dark_gray_building", (110, 110, 110),  35,  INF),    # dark building fill
    ("gray_building",      (160, 160, 160),  40,  INF),    # standard building fill
    # tol=55: covers channels [0,104] — catches pure-black (0,0,0) walls/fences
    ("black_features",     ( 50,  50,  50),  55,  INF),    # walls, fences, thick lines (ISSprOM 518/524)

    # ── Passable – fast (paved / firm ground) ──────────────────────────────
    # ISSprOM 505 paving stones: OCAD typically renders as warm yellow RGB~(250,190,75)
    ("paving_stones",      (250, 190,  75),  40,  0.8),    # paving stones (ISSprOM 505)
    # ISSprOM 418 sandy/gravel ground, courtyards: warm beige RGB~(235,195,165)
    ("sandy_ground",       (235, 195, 165),  35,  0.8),    # sandy / courtyard ground (ISSprOM 418)
    ("light_gray_paved",   (215, 215, 215),  25,  0.8),    # paved area / asphalt (ISSprOM 501)

    # ── Passable – normal (open terrain) ───────────────────────────────────
    ("white_open",         (240, 240, 240),  20,  1.0),    # open terrain (ISSprOM 401)

    # ── Passable – slow (vegetation) ───────────────────────────────────────
    # Medium green: forest slow-run RGB~(115-160, 165-210, 115-160)
    ("green_medium",       (115, 170, 115),  45,  1.5),    # medium-green slow vegetation (ISSprOM 407)
    # Light green: sparse vegetation, park edges RGB~(135-195, 185-225, 135-195)
    ("green_light",        (155, 205, 155),  40,  1.5),    # light-green slow vegetation (ISSprOM 406)
]


# ── Public API ───────────────────────────────────────────────────────────────

def build_traversability_mask(png_bytes: bytes, map_scale: int | None) -> np.ndarray:
    """
    Build a traversability cost grid from the map PNG (clean OCAD rasterisation,
    no course overprint).

    Args:
        png_bytes:  Raw bytes of map.png (300 DPI PNG, lossless).
        map_scale:  Map scale denominator (e.g. 4000 for 1:4000).  Used to
                    compute the real-world safety buffer in grid cells.

    Returns:
        float32 ndarray of shape (H_grid, W_grid):
            finite cost (0.8 / 1.0 / 1.5 / 2.5) = passable area
            np.inf = impassable area
    """
    img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    full_w, full_h = img.size

    # Adapt downsample factor so the grid stays ≤ MAX_GRID_SIZE.
    factor = _DOWNSAMPLE_BASE
    while (full_h // factor > _MAX_GRID_H) or (full_w // factor > _MAX_GRID_W):
        factor += 1

    arr = np.array(img, dtype=np.float32)  # (H, W, 3)

    # Step 1: classify every pixel at full 300-DPI resolution.
    cost_full = _classify_pixels(arr)       # (H, W) float32

    # Step 2: downsample using a threshold-pool.
    #
    # With max-pool (any blocked pixel → blocked cell), thin 1-2px building
    # outlines (~9% of an 11×11 block) would block entire street cells.
    # Instead we use a 25% threshold: a cell is blocked only if ≥25% of its
    # pixels are impassable.  This preserves narrow passages while still
    # blocking filled buildings and OOB zones (which are 100% impassable).
    h_trim = (full_h // factor) * factor
    w_trim = (full_w // factor) * factor
    cost_trim = cost_full[:h_trim, :w_trim]

    h_grid = h_trim // factor
    w_grid = w_trim // factor

    blocked_full = np.isinf(cost_trim).astype(np.float32)
    blocked_blocks = blocked_full.reshape(h_grid, factor, w_grid, factor)
    blocked_ratio = blocked_blocks.mean(axis=(1, 3))   # fraction of blocked px per cell
    any_blocked = blocked_ratio >= 0.25                # ≥25% → cell is impassable

    finite_cost = np.where(np.isinf(cost_trim), 0.0, cost_trim)
    finite_blocks = finite_cost.reshape(h_grid, factor, w_grid, factor)
    passable_count = (blocked_full.reshape(h_grid, factor, w_grid, factor) < 1).sum(axis=(1, 3))
    passable_sum = finite_blocks.sum(axis=(1, 3))
    mean_cost = np.where(
        passable_count > 0,
        passable_sum / np.maximum(passable_count, 1),
        1.0,
    )

    grid = np.where(any_blocked, INF, mean_cost).astype(np.float32)

    # Step 3: adaptive dilation so routes don't hug walls.
    buffer_cells = _compute_buffer_cells(map_scale, factor)
    grid = _apply_dilation(grid, buffer_cells)

    # Step 4: clearance penalty — bias pathfinding away from narrow passages.
    # Cells within 1-2 grid cells of an impassable area get a cost premium.
    # Routes then prefer wide, unambiguous corridors over tight 1-cell passages
    # that visually look like they're inside buildings.
    grid = _apply_clearance_penalty(grid)

    return grid


def mask_to_debug_png(grid: np.ndarray) -> bytes:
    """
    Convert cost grid to a grayscale debug PNG:
      white  = passable (cost 0.8–1.0)
      grey   = slow (cost 1.5–2.5)
      black  = impassable (inf)
    """
    h, w = grid.shape
    img_arr = np.zeros((h, w), dtype=np.uint8)

    passable = ~np.isinf(grid)
    # Map cost [0.8 .. 2.5] → brightness [255 .. 80]
    normalized = np.clip((grid[passable] - 0.8) / (2.5 - 0.8), 0.0, 1.0)
    img_arr[passable] = (255 - normalized * 175).astype(np.uint8)
    # impassable stays 0 (black)

    buf = io.BytesIO()
    Image.fromarray(img_arr, mode="L").save(buf, format="PNG")
    return buf.getvalue()


# ── Internal helpers ─────────────────────────────────────────────────────────

def _classify_pixels(arr: np.ndarray) -> np.ndarray:
    """
    Classify each pixel to a traversal cost using:
      1. Nearest-neighbor Chebyshev matching against _PALETTE.
      2. Explicit pixel rules for water and olive-green (higher priority).
    """
    r = arr[:, :, 0]
    g = arr[:, :, 1]
    b = arr[:, :, 2]

    cost = np.full(r.shape, 1.0, dtype=np.float32)   # default: open terrain
    best_dist = np.full(r.shape, np.inf, dtype=np.float32)

    for _name, (cr, cg, cb), tol, entry_cost in _PALETTE:
        dist = np.maximum(np.maximum(np.abs(r - cr), np.abs(g - cg)), np.abs(b - cb))
        closer = (dist < tol) & (dist < best_dist)
        cost = np.where(closer, entry_cost, cost)
        best_dist = np.where(closer, dist, best_dist)

    # ── Explicit override rules (higher priority than palette) ──────────────
    # Cyan/light-blue water: OCAD exports often produce RGB(0-130, 150-220, 200-255).
    # The palette "blue_water" catches dark blue; this catches bright cyan pools/rivers.
    water = (b > 170) & (r < 140) & (b > g) & (b > r + 80)

    # Olive-green private land / out-of-bounds (g > r means greenish, b < 100 means not blue).
    olive = (r >= 100) & (r <= 220) & (g >= 130) & (g <= 230) & (b < 100) & (g > r + 20)

    # Very dark pixels: safety net for pure-black walls/fences (R,G,B all ≤ 15).
    # The "black_features" palette entry (tol=55) should already cover these, but
    # an explicit rule provides a guaranteed second layer for edge cases.
    very_dark = (r <= 15) & (g <= 15) & (b <= 15)

    cost = np.where(water | olive | very_dark, INF, cost)
    return cost


def _apply_clearance_penalty(grid: np.ndarray) -> np.ndarray:
    """
    Add a traversal-cost premium to passable cells that are close to impassable
    areas (buildings, walls, water, etc.).

    This biases Dijkstra / Theta* away from narrow 1-cell passages that hug
    building walls and visually look like they cross buildings, in favour of
    wider, unambiguous corridors.

    Penalty profile (in grid cells from nearest blocked cell):
      dist < 1.0  →  +2.0  (cell touching a building corner/edge)
      dist < 2.0  →  +0.8  (one cell away)
      dist < 3.0  →  +0.2  (two cells away – mild preference for space)
      dist ≥ 3.0  →   0.0  (well clear, no penalty)

    A wide open street cell costs 1.0.  A wall-hugging cell costs 3.0.
    The Dijkstra will detour up to ~3 × longer to avoid these cells.
    """
    try:
        from scipy.ndimage import distance_transform_edt
    except ImportError:
        return grid  # scipy not available – skip silently

    blocked = np.isinf(grid)
    if not blocked.any():
        return grid

    # EDT on the passable mask: each passable cell gets its Euclidean distance
    # (in grid cells) to the nearest blocked cell.  Blocked cells get 0.
    dist = distance_transform_edt(~blocked)

    penalty = np.where(dist < 1.0, 2.0,
              np.where(dist < 2.0, 0.8,
              np.where(dist < 3.0, 0.2, 0.0))).astype(np.float32)

    return np.where(blocked, grid, grid + penalty).astype(np.float32)


def _compute_buffer_cells(map_scale: int | None, downsample_factor: int) -> int:
    """Compute safety-buffer size in grid cells from real-world metres.

    Sprint maps (scale ≤ 5000) skip explicit dilation: the max-pool step already
    provides a half-block (~0.6m) natural buffer, and sprint passages are too
    narrow to survive further expansion.
    """
    if map_scale and map_scale > 0:
        # Sprint maps: no extra dilation
        if map_scale <= 5000:
            return 0
        # At 300 DPI: 1 pixel = map_scale / (300 * 39.3701) metres in the real world.
        metres_per_px_full = map_scale / (300.0 * 39.3701)
        metres_per_cell = metres_per_px_full * downsample_factor
        return max(1, round(_SAFETY_BUFFER_METRES / metres_per_cell))
    return 0  # fallback: no dilation


def _apply_dilation(grid: np.ndarray, buffer_cells: int) -> np.ndarray:
    """Dilate (expand) impassable cells outward by buffer_cells steps."""
    if buffer_cells <= 0:
        return grid

    blocked = np.isinf(grid)
    expanded = blocked.copy()

    for _ in range(buffer_cells):
        pad = np.pad(expanded, 1, mode="constant", constant_values=False)
        expanded = (
            pad[:-2, :-2] | pad[:-2, 1:-1] | pad[:-2, 2:]
            | pad[1:-1, :-2] | pad[1:-1, 1:-1] | pad[1:-1, 2:]
            | pad[2:, :-2]   | pad[2:, 1:-1]   | pad[2:, 2:]
        )

    return np.where(expanded, INF, grid).astype(np.float32)
