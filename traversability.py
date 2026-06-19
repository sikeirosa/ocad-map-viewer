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

TRAVERSABILITY_VERSION = "v9"
_CACHE_FILENAME = f"traversability_{TRAVERSABILITY_VERSION}.npz"

# Infinite cost = physically impassable.
INF = np.inf

# ── Grid resolution ──────────────────────────────────────────────────────────
# The downsample factor is driven by a target REAL-WORLD resolution (metres per
# grid cell), NOT by a fixed grid-size cap.  Rationale: sprint maps have decisive
# ~1–2 m passages (alleys, gaps between buildings, fenced-garden paths).  When a
# ~2 m path flanked by olive / buildings is quantised at ≥ 2 m/cell, its cells
# become olive/building-dominated and impassable, severing the path; the
# surviving fragments are offset by 1–2 cells and no longer 8-connect, which
# FALSELY isolates the control on it (observed at factor 8 ≈ 2.03 m/cell).
# Empirically, ≤ ~1.3 m/cell keeps those passages connected.
#
# A fixed grid-size cap (the previous approach) made resolution DEGRADE as the
# map grew — the opposite of what narrow-passage connectivity needs.  We instead
# pick the factor to hit _TARGET_METRES_PER_CELL and only coarsen if the grid
# would exceed _MAX_GRID_CELLS (a memory/CPU safety bound for very large maps).
_TARGET_METRES_PER_CELL = 1.2

# Finest factor we ever use (floor), and the fallback when the scale is unknown.
_DOWNSAMPLE_BASE = 4

# Safety cap on total grid cells (coarsen the factor if exceeded).  ~3.0 M keeps
# the scipy Dijkstra / connected-components well under a second on the route
# endpoint while comfortably allowing ~1.27 m/cell on a 6200×10300 sprint map.
_MAX_GRID_CELLS = 3_000_000

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

def _metres_per_pixel(map_scale: int | None) -> float | None:
    """Real-world metres per source pixel at 300 DPI, or None if scale unknown."""
    if map_scale and map_scale > 0:
        return map_scale / (300.0 * 39.3701)
    return None


def _compute_factor(full_h: int, full_w: int, map_scale: int | None = None) -> int:
    """
    Downsample factor targeting ~_TARGET_METRES_PER_CELL real-world resolution.

    Picks the finest factor that (a) is ≥ _DOWNSAMPLE_BASE and (b) hits the
    target metres-per-cell, then coarsens only if the resulting grid would
    exceed _MAX_GRID_CELLS.  When the map scale is unknown, falls back to
    _DOWNSAMPLE_BASE (still bounded by the cell cap).
    """
    mpp = _metres_per_pixel(map_scale)
    if mpp:
        factor = max(_DOWNSAMPLE_BASE, round(_TARGET_METRES_PER_CELL / mpp))
    else:
        factor = _DOWNSAMPLE_BASE
    # Coarsen if the grid would be too large for the cell-count safety cap.
    while (full_h // factor) * (full_w // factor) > _MAX_GRID_CELLS:
        factor += 1
    return factor


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

    # Adapt downsample factor to hit the target real-world resolution.
    factor = _compute_factor(full_h, full_w, map_scale)

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

    # Primary rule: a cell is impassable only if ≥40% of its pixels are blocked.
    # Filled buildings / OOB / water cover ~100% of a cell, so they block cleanly;
    # a thin building outline or curb crossing an 8×8 cell touches ~12% of its
    # pixels and stays passable, preserving the narrow alleys and courtyard
    # accesses that link every control to the street network.  (At factor 8 a
    # 0.25 threshold severs the enclosed-control passages; 0.40 keeps them open
    # while the finer cells keep edge-grazing down to ~1.5 m.)
    any_blocked = blocked_ratio >= 0.40

    # Secondary rule: olive / out-of-bounds zones (ISSprOM 520/521).
    # Interior cells of olive zones are 100% olive (already caught by ≥25%).
    # Border cells can have only 8-15% olive pixels yet should still block routes,
    # since olive terrain is strictly impassable regardless of the fraction.
    # Threshold 8%: catches ≥1 full row of olive pixels in a 11×11 block.
    arr_trim = arr[:h_trim, :w_trim]
    r_t, g_t, b_t = arr_trim[:, :, 0], arr_trim[:, :, 1], arr_trim[:, :, 2]
    olive_full = ((r_t >= 110) & (r_t <= 215) &
                  (g_t >= 110) & (g_t <= 215) &
                  (b_t <  110) &
                  (g_t > b_t + 50) &
                  (np.abs(r_t - g_t) <= 25)).astype(np.float32)
    olive_ratio = olive_full.reshape(h_grid, factor, w_grid, factor).mean(axis=(1, 3))
    any_blocked = any_blocked | (olive_ratio >= 0.08)

    # Tertiary rule REMOVED in v8: thin/medium walls are now handled precisely by
    # the inter-cell barrier-edge model (see build_barrier_edges), which forbids
    # *crossing* a wall while still allowing travel ALONG it and through genuine
    # openings.  A whole-cell "≥20% near-black" rule double-blocked those walls
    # and, at the finer factor-8 grid, wrongly sealed legitimate narrow passages
    # (severing controls reachable only through a 1-cell alley).  Solid filled
    # walls that cover ≥40% of a cell are still caught by the primary rule above.

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


# ── Barrier edge-cut layer (thin walls / fences / crossing points) ───────────
#
# The cost grid is coarse (~2.8 m / cell at 1:3000).  A passable cell can still
# contain a thin black barrier (ISSprOM 515 impassable wall, 518.1 impassable
# fence, building outlines).  Blocking the whole cell would sever narrow
# corridors and crossing points; instead we model barriers as *blocked edges*
# between adjacent cells.  A route may travel ALONG a wall but never ACROSS it,
# and a gap in the wall (a crossing point, ISSprOM 519) leaves the edge open.
#
# We detect impassable barrier pixels at full 300-DPI resolution, then for each
# of the 8 inter-cell connections sample the pixel segment between the two cell
# centres: if any barrier pixel lies on it, that move is forbidden.
#
# Output: four bool grids (E, S, SE, SW) of shape (H_grid, W_grid).
#   E[i,j]  : wall between (i,j) and (i,j+1)
#   S[i,j]  : wall between (i,j) and (i+1,j)
#   SE[i,j] : wall between (i,j) and (i+1,j+1)
#   SW[i,j] : wall between (i,j) and (i+1,j-1)
# ──────────────────────────────────────────────────────────────────────────────

def _barrier_pixels(arr: np.ndarray) -> np.ndarray:
    """
    Boolean mask of impassable *linear* barrier pixels (near-black ink).

    Covers ISSprOM black line features that must never be crossed: impassable
    walls (515), impassable fences/railings (518.1), and building outlines.
    Passable walls/fences (516 / 518.2) are drawn in the same black ink, so —
    lacking a colour discriminator — they are treated conservatively as
    impassable too; their legitimate openings (crossing points 519) remain
    passable because a gap in the ink leaves the inter-cell edge open.
    """
    r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
    return (r <= 70) & (g <= 70) & (b <= 70)


def build_barrier_edges(
    png_bytes_or_arr,
    grid_shape: tuple[int, int],
    factor: int,
) -> dict[str, np.ndarray]:
    """
    Build the four inter-cell barrier edge masks from the full-resolution map.

    Args:
        png_bytes_or_arr: raw map.png bytes, or a pre-loaded HxWx3 uint8/float array.
        grid_shape:       (H_grid, W_grid) of the cost grid.
        factor:           downsample factor used to build the cost grid.

    Returns:
        dict with bool ndarrays "E", "S", "SE", "SW", each shaped grid_shape.
    """
    if isinstance(png_bytes_or_arr, (bytes, bytearray)):
        img = Image.open(io.BytesIO(png_bytes_or_arr)).convert("RGB")
        arr = np.array(img)
    else:
        arr = png_bytes_or_arr
    full_h, full_w = arr.shape[0], arr.shape[1]

    barrier = _barrier_pixels(arr.astype(np.int16))

    gh, gw = grid_shape
    h_trim = gh * factor
    w_trim = gw * factor
    bar = barrier[:h_trim, :w_trim]

    # ── Continuous-wall detection ────────────────────────────────────────────
    # A barrier only SEPARATES two cells if it forms a near-continuous line
    # along their shared boundary, spanning most of the cell's width.  A single
    # stray black pixel (tick mark, label, outline anti-alias) must NOT cut the
    # edge.  We therefore require the wall to cover ≥ WALL_FRAC of the boundary.
    #
    #   E[i,j] (vertical wall between cols j|j+1): for each pixel row of cell i,
    #     is there black in the boundary column band?  Block if the fraction of
    #     such rows ≥ WALL_FRAC.
    #   S[i,j] (horizontal wall between rows i|i+1): symmetric over columns.
    WALL_FRAC = 0.55
    band = max(1, factor // 6)   # half-width of the boundary pixel band

    # Boundary "presence" per full-res row/col, reduced over the band.
    # Vertical boundaries sit at pixel column b*factor for b in 1..gw-1.
    E = np.zeros((gh, gw), dtype=bool)
    S = np.zeros((gh, gw), dtype=bool)

    # Vertical walls (affect E edges).
    bcols = np.arange(1, gw) * factor            # (gw-1,) boundary x positions
    col_idx = np.clip(
        bcols[None, :] + np.arange(-band, band + 1)[:, None], 0, w_trim - 1
    )                                            # (2band+1, gw-1)
    # presence[r, b] = any black at row r within band around boundary b
    presence_v = bar[:, col_idx].any(axis=1)     # (h_trim, gw-1)
    # fraction of each cell-row's pixel rows that are "walled"
    frac_v = presence_v.reshape(gh, factor, gw - 1).mean(axis=1)  # (gh, gw-1)
    E[:, : gw - 1] = frac_v >= WALL_FRAC

    # Horizontal walls (affect S edges).
    brows = np.arange(1, gh) * factor
    row_idx = np.clip(
        brows[None, :] + np.arange(-band, band + 1)[:, None], 0, h_trim - 1
    )                                            # (2band+1, gh-1)
    presence_h = bar[row_idx, :].any(axis=0)     # (gh-1, w_trim)
    frac_h = presence_h.reshape(gh - 1, gw, factor).mean(axis=2)  # (gh-1, gw)
    S[: gh - 1, :] = frac_h >= WALL_FRAC

    # ── Diagonals derived from orthogonal edges (no corner cutting) ───────────
    # A diagonal move is allowed only if at least one of its two orthogonal
    # "L-shaped" detours is fully open; otherwise it would clip a wall corner.
    SE = np.zeros((gh, gw), dtype=bool)
    SW = np.zeros((gh, gw), dtype=bool)

    # SE (i,j)->(i+1,j+1): L-paths are [E(i,j) then S(i,j+1)] and [S(i,j) then E(i+1,j)]
    e_ij = E[: gh - 1, : gw - 1]
    s_ij1 = S[: gh - 1, 1:gw]
    s_ij = S[: gh - 1, : gw - 1]
    e_i1j = E[1:gh, : gw - 1]
    path1_open = ~e_ij & ~s_ij1
    path2_open = ~s_ij & ~e_i1j
    SE[: gh - 1, : gw - 1] = ~(path1_open | path2_open)

    # SW (i,j)->(i+1,j-1): L-paths are [Wedge(i,j)=E(i,j-1) then S(i,j-1)] and
    # [S(i,j) then Wedge(i+1,j)=E(i+1,j-1)]
    e_ijm1 = E[: gh - 1, : gw - 1]   # E at col j-1  (aligns to dest col band)
    s_ijm1 = S[: gh - 1, : gw - 1]   # S at col j-1
    s_ij_b = S[: gh - 1, 1:gw]       # S at col j
    e_i1jm1 = E[1:gh, : gw - 1]      # E at (i+1, j-1)
    pw1_open = ~e_ijm1 & ~s_ijm1
    pw2_open = ~s_ij_b & ~e_i1jm1
    SW[: gh - 1, 1:gw] = ~(pw1_open | pw2_open)

    return {"E": E, "S": S, "SE": SE, "SW": SW}


def build_cost_and_edges(
    png_bytes: bytes,
    map_scale: int | None,
) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """
    Build both the cost grid and the barrier edge masks in one pass.

    Returns (grid, edges) where edges has keys "E", "S", "SE", "SW".
    """
    img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    arr = np.array(img)
    full_h, full_w = arr.shape[0], arr.shape[1]
    factor = _compute_factor(full_h, full_w, map_scale)

    grid = build_traversability_mask(png_bytes, map_scale)
    edges = build_barrier_edges(arr, grid.shape, factor)
    return grid, edges


def pack_cache(grid: np.ndarray, edges: dict[str, np.ndarray]) -> bytes:
    """
    Serialize the cost grid + barrier edges into a compressed .npz blob.

    Keys: grid, E, S, SE, SW.  Used by both the upload precompute (processing.py)
    and the route-choice endpoint (server.py) so the cache format stays in sync.
    """
    buf = io.BytesIO()
    np.savez_compressed(
        buf,
        grid=grid,
        E=edges["E"],
        S=edges["S"],
        SE=edges["SE"],
        SW=edges["SW"],
    )
    return buf.getvalue()


def unpack_cache(data: bytes) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """Inverse of pack_cache: returns (grid, edges) from a .npz blob."""
    npz = np.load(io.BytesIO(data))
    grid = npz["grid"]
    edges = {k: npz[k] for k in ("E", "S", "SE", "SW")}
    return grid, edges


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

    # Olive-green private land / out-of-bounds (ISSprOM 520/521 "Area which
    # shall not be entered").  On OCAD sprint exports this renders as a *muted*
    # yellow-green, e.g. RGB(168,160,48): R and G are close (|R-G| small) and
    # both are mid-range, with B much lower.
    #
    # The critical discriminator vs. bright paving-yellow RGB(248,184,72):
    #   - olive  : R≈G (|R-G| ≤ 25), muted (R,G ≤ ~215)
    #   - paving : R≫G (|R-G| ≈ 64), bright  (R ≈ 248)
    # And vs. sandy/beige RGB(232,192,168): sandy has high blue (B ≥ 150),
    # olive has low blue (B < 110, with G > B + 50).
    olive = (
        (r >= 110) & (r <= 215) &
        (g >= 110) & (g <= 215) &
        (b < 110) &
        (g > b + 50) &
        (np.abs(r - g) <= 25)
    )

    # Very dark pixels: safety net for pure-black walls/fences (R,G,B all ≤ 15).
    # The "black_features" palette entry (tol=55) should already cover these, but
    # an explicit rule provides a guaranteed second layer for edge cases.
    very_dark = (r <= 15) & (g <= 15) & (b <= 15)

    cost = np.where(water | olive | very_dark, INF, cost)
    return cost


def _predilate_barriers(arr: np.ndarray, cost: np.ndarray) -> np.ndarray:
    """
    Pre-dilate very-dark barrier pixels before the threshold-pool downsampling.

    Barriers (ISSprOM 515 impassable wall, 516 impassable fence) are drawn as
    thin lines (0.25–0.5 mm = 1–3 px at 300 DPI).  In an 11×11 downsample
    block, 1 px = 9% and 3 px diagonal = 16%: both below the 25% threshold,
    so the barrier is silently dropped.

    This function dilates the very-dark mask (R,G,B ≤ 50) by 2 pixels so
    that a 1 px line becomes 5 px (≈41% of block, safely above 25%).

    Building fills are already 100% INF, so dilation has no extra effect on
    passable street cells adjacent to buildings.
    """
    try:
        from scipy.ndimage import binary_dilation
    except ImportError:
        return cost  # scipy not available — skip silently

    r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
    very_dark = (r <= 50) & (g <= 50) & (b <= 50)
    if not very_dark.any():
        return cost

    dilated = binary_dilation(very_dark, iterations=2)
    # Only expand INTO currently passable cells (don't overwrite existing INF).
    expanded = dilated & ~np.isinf(cost)
    return np.where(expanded, INF, cost).astype(np.float32)


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
