"""
Route-choice pathfinding — via-vertex Dijkstra (Phase 2) + Theta* (Phase 1 fallback).

Primary algorithm: via-vertex Dijkstra (Abraham et al. 2013 style)
- Run full Dijkstra from start AND end on the passable-cell graph.
- Find the best "via-vertex" v on each side (left/right of the direct line)
  such that d_fwd[v] + d_bwd[v] is minimised and v is NOT on route A.
- Reconstruct route B = Dijkstra(start→v) + Dijkstra(v→end), smoothed with RDP.

Fallback: penalty-based Theta* multi-path (original Phase 1 approach) if
scipy is unavailable or the via-vertex approach does not find enough routes.
"""

from __future__ import annotations

import heapq
import math
import time
from collections import deque
from typing import Iterator

import numpy as np

# ── Optional scipy (required for via-vertex) ────────────────────────────────
try:
    from scipy.sparse import csr_matrix as _csr_matrix
    from scipy.sparse.csgraph import dijkstra as _sp_dijkstra
    from scipy.ndimage import binary_dilation as _binary_dilation
    from scipy.ndimage import label as _sp_label
    _HAS_SCIPY = True
except ImportError:  # pragma: no cover
    _HAS_SCIPY = False

# ── Constants ────────────────────────────────────────────────────────────────

# Jaccard threshold for *penalty* fallback (strict).
_JACCARD_THRESHOLD = 0.30

# Jaccard threshold for accepting via-vertex routes (more permissive — the
# structural diversity is already guaranteed by the perpendicular sector split).
_VIA_JACCARD_THRESHOLD = 0.60

# Maximum stretch factor for a via-vertex candidate: route ≤ this × optimal.
_VIA_MAX_STRETCH = 1.50

# Exclusion radius around route A when searching for via-vertex candidates.
_VIA_EXCLUSION_CELLS = 5

# Cost multiplier applied to grid cells near an already-found path (penalty fallback).
_PENALTY_FACTOR = 5.0

# Half-width of the penalty corridor around a found path (in grid cells).
_PENALTY_CORRIDOR = 3

# Max retries per alternative when Jaccard check fails (penalty fallback).
_MAX_JACCARD_RETRIES = 3

# ── Near-duplicate detection (buffered corridor overlap) ─────────────────────
# Exact-cell Jaccard under-estimates the similarity of two routes that follow
# the *same* corridor but are offset by a couple of cells (parallel street, or a
# one-cell split at a junction): few exact cells overlap, yet the two routes are
# visually identical with near-equal lengths.  To catch these "practically
# identical" choices we additionally compare *buffered* (dilated) corridors.
#
# A candidate is rejected as a near-duplicate of an existing route when EITHER:
#   • the symmetric corridor overlap is extreme (one route is almost entirely
#     contained in the other's tolerance band, both directions), OR
#   • the corridor overlap is high AND the two lengths are within _DUP_LENGTH_FRAC.
#
# This is purely ADDITIVE to the existing exact Jaccard test — it can only reject
# more duplicates, never accept new ones.

# Tolerance band (half-width) around a route, in real-world metres.  Converted
# to grid cells via the map scale + downsample factor (falls back to
# _DUP_TOL_CELLS_FALLBACK when geometry is unknown).
_DUP_TOL_METRES = 6.0
_DUP_TOL_CELLS_FALLBACK = 8  # ≈ 10 m at scale 1:3000 (1.27 m/cell)

# Corridor-overlap fraction (mean of both directions) above which two routes are
# "high overlap".  Using the mean rather than the min makes the test more
# sensitive to mutual similarity: if A covers 80% of B's band and B covers 77%
# of A's band, mean = 78.5% which catches the case whereas min = 77% would not.
_DUP_OVERLAP_THRESHOLD = 0.72

# Mean corridor-overlap above which two routes are duplicates regardless of
# length (near-total mutual inclusion).
_DUP_OVERLAP_EXTREME = 0.88

# Max relative length difference for the "high overlap + similar length" rule.
_DUP_LENGTH_FRAC = 0.08

# ── Homotopy-aware diverse-alternative selection ─────────────────────────────
# The via-vertex + penalty generators above produce a *pool* of candidate routes;
# the historical "cheapest-per-sector + lenient dedup" selection both (a) clipped
# genuinely-different long alternatives (stretch caps 1.5/1.6×) and (b) still let
# near-parallel hugs through (overlap thresholds 0.72/0.88).  We replace the
# SELECTION with a literature-grounded distinctness test (Abraham et al. 2013,
# "Alternative Routes in Road Networks" — bounded stretch + limited sharing —
# combined with a homotopy-class test w.r.t. the impassable obstacles).
#
# Two routes are a genuinely DIFFERENT choice iff BOTH hold on the string-pulled
# DISPLAY geometry (what the runner actually sees):
#   • the closed loop they form encloses a CONNECTED impassable component of at
#     least _DIVERSE_MIN_OBSTACLE_M2 — i.e. they pass opposite sides of a
#     building-sized obstacle (a real route-choice decision), AND
#   • their buffered corridor overlap is ≤ _DIVERSE_SHARE_MAX (visually distinct,
#     not a parallel hug).
# Each gate catches what the other misses: the enclosed-obstacle test alone
# admits trivial splits around tiny buildings (high overlap); the overlap test
# alone fails on convoluted legs (a 15 m parallel offset can score "distinct").
#
# Generation uses a generous stretch cap because the two gates — not the cap —
# guarantee quality: long junk routes are rejected by the gates, while a genuine
# 2.2× south alternative around a fenced block is surfaced.
_DIVERSE_MAX_STRETCH = 2.5

# Minimum enclosed connected-obstacle area (m²) for two routes to count as
# different homotopy classes.  Resolution-independent: converted to grid cells
# via the map's metres-per-cell.  ~200 m² ≈ "they disagree about a building".
_DIVERSE_MIN_OBSTACLE_M2 = 200.0

# Fallback in cells when metres-per-cell is unknown (≈ 200 m² at 1.27 m/cell).
_DIVERSE_MIN_OBSTACLE_CELLS_FALLBACK = 125

# Maximum buffered corridor overlap for two routes to count as distinct.
_DIVERSE_SHARE_MAX = 0.55

# Maximum length ratio (in real distance) for an alternative to count as a
# genuine route *choice* relative to the shortest route.  The generation cap
# (_DIVERSE_MAX_STRETCH) is measured on grid COST, so a long detour over cheap
# "open ground" (cost 0.8) can slip through even at ~2.7× the distance.  An
# orienteer would never pick a route nearly triple the optimum, so we drop any
# kept alternative whose string-pulled length exceeds this × the shortest route.
# Resolution-independent (cells cancel in the ratio).  1.7× keeps every approved
# alternative in the golden baseline (max kept ratio is leg 3-4 at 1.62×) while
# dropping absurd detours such as leg 7-8's 2.76× river loop.
_DIVERSE_MAX_DETOUR = 1.7

# ── Multi-anchor (control-circle) exploration ────────────────────────────────
# The routing endpoint is the single nearest-passable cell to the control's GPS
# point.  When that cell lands in a thin-wall quantisation pocket (one side of a
# building outline), the search is trapped on that side and a genuine route on
# the OTHER side of the control (e.g. an alley along a fence) is never surfaced.
# Treating the whole control circle as the valid endpoint region, we additionally
# sample passable anchors around its rim and add their shortest paths as extra
# candidates — route A (from the primary cell) is never changed, so this is
# additive and regression-safe.  Anchor variants are accepted only when their
# string-pulled length is within this factor of route A, so genuine close choices
# (a parallel alley) are kept while long detours that merely approach the control
# from the far side are dropped (the normal via-vertex pool keeps its 1.7× gate).
_MULTIANCHOR_MAX_STRETCH = 1.35

# Number of rim samples around the control circle when generating anchors.
_MULTIANCHOR_RING_SAMPLES = 8

# Perpendicular bands sampled per side when harvesting via-vertex candidates.
_DIVERSE_VIA_BANDS = 12

# Penalty-detour iterations added to the candidate pool.
_DIVERSE_PENALTY_TRIES = 4

# Maximum grid cells searched before giving up.
_MAX_CELLS_VISITED = 4_000_000

# Default per-route timeout in seconds.
DEFAULT_TIMEOUT = 15.0


class BarrierCtx:
    """
    Thin-wall barrier model: forbids movement *between* adjacent cells when an
    impassable barrier (wall / fence / crossing-free segment) lies on the
    pixel segment joining their centres.

    Holds four bool grids (E, S, SE, SW) of shape (H_grid, W_grid):
      E[i,j]  : wall between (i,j) and (i,j+1)
      S[i,j]  : wall between (i,j) and (i+1,j)
      SE[i,j] : wall between (i,j) and (i+1,j+1)
      SW[i,j] : wall between (i,j) and (i+1,j-1)
    """

    __slots__ = ("E", "S", "SE", "SW")

    def __init__(self, edges: dict):
        self.E = edges["E"]
        self.S = edges["S"]
        self.SE = edges["SE"]
        self.SW = edges["SW"]

    def step_blocked(self, r0: int, c0: int, r1: int, c1: int) -> bool:
        """True if the unit move (r0,c0)->(r1,c1) crosses a barrier edge."""
        dr = r1 - r0
        dc = c1 - c0
        if dr == 0:
            if dc == 1:
                return bool(self.E[r0, c0])
            if dc == -1:
                return bool(self.E[r0, c1])
            return False
        if dc == 0:
            if dr == 1:
                return bool(self.S[r0, c0])
            if dr == -1:
                return bool(self.S[r1, c0])
            return False
        if dr == 1 and dc == 1:
            return bool(self.SE[r0, c0])
        if dr == -1 and dc == -1:
            return bool(self.SE[r1, c1])
        if dr == 1 and dc == -1:
            return bool(self.SW[r0, c0])
        if dr == -1 and dc == 1:
            return bool(self.SW[r1, c1])
        return False


# ── Bilinear 4-corner coordinate helpers ─────────────────────────────────────
#
# OCAD maps are rarely perfectly north-aligned.  Using a simple lat/lng
# bounding-box introduces positional errors proportional to the map rotation
# (up to ~35 m for a 0.78° rotation on a 2.6 km-wide map).
#
# The correct approach is a 4-corner bilinear transform:
#
#   lat(u,v) = a00 + a10·u + a01·v + a11·u·v
#   lng(u,v) = b00 + b10·u + b01·v + b11·u·v
#
# where (u=0,v=0)=NW, (u=1,v=0)=NE, (u=0,v=1)=SW, (u=1,v=1)=SE.
# The inverse (lat,lng)→(u,v) is solved with 4 Newton-Raphson iterations
# (converges in 1 step for maps with negligible a11/b11 twist terms).
# ─────────────────────────────────────────────────────────────────────────────

def _bilinear_coeffs(corners: dict) -> tuple[float, ...]:
    """
    Compute the 8 bilinear coefficients for the 4-corner GPS↔(u,v) mapping.

    Returns (a00, a10, a01, a11, b00, b10, b01, b11) where
      lat(u,v) = a00 + a10·u + a01·v + a11·u·v
      lng(u,v) = b00 + b10·u + b01·v + b11·u·v
    """
    nw = corners["nw"]; ne = corners["ne"]
    se = corners["se"]; sw = corners["sw"]
    nw_lat, nw_lng = nw["lat"], nw["lng"]
    ne_lat, ne_lng = ne["lat"], ne["lng"]
    se_lat, se_lng = se["lat"], se["lng"]
    sw_lat, sw_lng = sw["lat"], sw["lng"]

    a00, a10 = nw_lat, ne_lat - nw_lat
    a01, a11 = sw_lat - nw_lat, nw_lat - ne_lat - sw_lat + se_lat
    b00, b10 = nw_lng, ne_lng - nw_lng
    b01, b11 = sw_lng - nw_lng, nw_lng - ne_lng - sw_lng + se_lng
    return (a00, a10, a01, a11, b00, b10, b01, b11)


def _uv_to_latlon(u: float, v: float, coeffs: tuple) -> tuple[float, float]:
    """Evaluate bilinear map at (u, v) → (lat, lng). O(1)."""
    a00, a10, a01, a11, b00, b10, b01, b11 = coeffs
    return (a00 + a10*u + a01*v + a11*u*v,
            b00 + b10*u + b01*v + b11*u*v)


def _latlon_to_uv(
    lat: float,
    lng: float,
    coeffs: tuple,
    n_iter: int = 4,
) -> tuple[float, float]:
    """
    Inverse bilinear map (lat, lng) → (u, v) via Newton-Raphson.

    Starting from the bounding-box estimate (accurate for axis-aligned maps),
    4 iterations is more than sufficient for any realistic map rotation.
    Returns (u, v) clamped to [0, 1].
    """
    a00, a10, a01, a11, b00, b10, b01, b11 = coeffs

    # Initial estimate: bounding box (works well as a warm start)
    lng_min = min(b00, b00 + b01)
    lng_max = max(b00 + b10, b00 + b10 + b01)
    lat_max = max(a00, a00 + a10)
    lat_min = min(a00 + a01, a00 + a01 + a10)
    u = (lng - lng_min) / (lng_max - lng_min) if lng_max > lng_min else 0.5
    v = (lat_max - lat) / (lat_max - lat_min) if lat_max > lat_min else 0.5
    u = max(0.0, min(1.0, u))
    v = max(0.0, min(1.0, v))

    for _ in range(n_iter):
        pred_lat = a00 + a10*u + a01*v + a11*u*v
        pred_lng = b00 + b10*u + b01*v + b11*u*v
        dlat = lat - pred_lat
        dlng = lng - pred_lng
        if abs(dlat) < 1e-10 and abs(dlng) < 1e-10:
            break
        J00 = a10 + a11*v   # ∂lat/∂u
        J01 = a01 + a11*u   # ∂lat/∂v
        J10 = b10 + b11*v   # ∂lng/∂u
        J11 = b01 + b11*u   # ∂lng/∂v
        det = J00*J11 - J01*J10
        if abs(det) < 1e-20:
            break
        u = max(0.0, min(1.0, u + ( J11*dlat - J01*dlng) / det))
        v = max(0.0, min(1.0, v + (-J10*dlat + J00*dlng) / det))

    return u, v


# ── Public API ───────────────────────────────────────────────────────────────

def gps_to_grid(
    lat: float,
    lng: float,
    corners: dict,
    grid_h: int,
    grid_w: int,
) -> tuple[int, int] | None:
    """
    Convert a GPS coordinate to (row, col) in the traversability grid.

    Uses the exact 4-corner bilinear transform so rotated OCAD maps are
    handled correctly (eliminates the up-to-35 m bounding-box error).
    """
    try:
        coeffs = _bilinear_coeffs(corners)
        u, v = _latlon_to_uv(lat, lng, coeffs)
        col = int(u * grid_w)
        row = int(v * grid_h)
        return (min(row, grid_h - 1), min(col, grid_w - 1))
    except (KeyError, TypeError, ZeroDivisionError):
        return None


def grid_to_gps(
    row: int,
    col: int,
    corners: dict,
    grid_h: int,
    grid_w: int,
) -> dict:
    """Convert (row, col) grid cell centre to {lat, lng} using bilinear 4-corner mapping."""
    coeffs = _bilinear_coeffs(corners)
    u = (col + 0.5) / grid_w
    v = (row + 0.5) / grid_h
    lat, lng = _uv_to_latlon(u, v, coeffs)
    return {"lat": lat, "lng": lng}


def nearest_passable(
    grid: np.ndarray,
    row: int,
    col: int,
    max_radius: int = 20,
) -> tuple[int, int] | None:
    """
    BFS to find the nearest passable (finite-cost) cell within max_radius steps.

    Returns (row, col) or None if none found within the radius.
    """
    h, w = grid.shape
    if not np.isinf(grid[row, col]):
        return (row, col)

    q: deque[tuple[int, int, int]] = deque()
    q.append((row, col, 0))
    visited: set[tuple[int, int]] = {(row, col)}
    _DIRS = [(-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1)]

    while q:
        r, c, d = q.popleft()
        if d > max_radius:
            return None
        if not np.isinf(grid[r, c]):
            return (r, c)
        for dr, dc in _DIRS:
            nr, nc = r + dr, c + dc
            if 0 <= nr < h and 0 <= nc < w and (nr, nc) not in visited:
                visited.add((nr, nc))
                q.append((nr, nc, d + 1))
    return None


def find_diverse_routes(
    grid: np.ndarray,
    start: tuple[int, int],
    end: tuple[int, int],
    k: int = 3,
    timeout: float = DEFAULT_TIMEOUT,
    barrier: "BarrierCtx | None" = None,
    reanchor_radius_cells: int = 0,
    meters_per_cell: float = 0.0,
) -> list[list[tuple[int, int]]]:
    """
    Find up to k geographically distinct routes between start and end.

    *reanchor_radius_cells* (>0) enables a component-aware rescue: if start and
    end land in different graph components, each endpoint is moved to the nearest
    cell of the largest component within that radius before declaring failure.
    The routing endpoint moves at most this far (intended ≈ the control-circle
    radius); no connector is ever drawn across the seal.

    Strategy (scipy available — the normal path):
      1. Build the passable-cell graph ONCE (barrier walls cut inter-cell edges).
      2. Route A = shortest path via Dijkstra (fast & robust — no any-angle
         search to time out on fine grids).
      3. Harvest a bounded candidate pool of alternatives (via-vertex band
         sampling across the full perpendicular width + penalty detours), then
         select up to k that are genuinely distinct from one another via a
         homotopy-aware test: two routes count as different choices only when the
         loop they form encloses a building-sized impassable obstacle AND their
         buffered corridors overlap little.  This both surfaces real long-way-
         round alternatives and rejects near-parallel hugs.

    *meters_per_cell* (>0) makes the distinctness threshold resolution-aware
    (the minimum enclosed-obstacle area is specified in m²); when 0 a cell-count
    fallback is used.

    If *start* and *end* lie in different connected components — i.e. a control
    is genuinely walled off (e.g. a fenced courtyard with no opening) — NO route
    exists and an empty list is returned.  The caller surfaces this to the user
    rather than fabricating a path across an impassable barrier.

    Fallback (scipy missing): penalty-based Theta* multi-path.

    Returns a list of paths (each path = list of (row, col) cells).
    May return fewer than k paths if the terrain doesn't allow diversity.
    """
    deadline = time.monotonic() + timeout

    if _HAS_SCIPY:
        return _find_routes_dijkstra(
            grid, start, end, k, deadline, barrier, reanchor_radius_cells,
            meters_per_cell,
        )

    # ── No scipy: Theta* route A + penalty fallback ─────────────────────────
    routes: list[list[tuple[int, int]]] = []
    path_a = theta_star(grid, start, end, deadline=deadline, barrier=barrier)
    if path_a is None:
        return []
    routes.append(path_a)
    if k <= 1:
        return routes
    if len(routes) < k and time.monotonic() < deadline:
        penalty_routes = _penalty_diverse_routes(
            grid, start, end, routes, k - len(routes), deadline, barrier=barrier
        )
        routes.extend(penalty_routes)
    routes = _final_dedup_routes(routes, grid.shape)
    return routes[:k]


def _nearest_in_component(
    node_idx: np.ndarray,
    labels: np.ndarray,
    target_label: int,
    origin_rc: tuple[int, int],
    radius_cells: int,
) -> tuple[tuple[int, int], int] | None:
    """
    Nearest passable cell whose connected-component label == *target_label*,
    within *radius_cells* of *origin_rc*.  Returns ((row, col), node_index) or
    None.  Searches a bounded window so the cost is negligible.
    """
    r0, c0 = origin_rc
    h, w = node_idx.shape
    r_lo, r_hi = max(0, r0 - radius_cells), min(h, r0 + radius_cells + 1)
    c_lo, c_hi = max(0, c0 - radius_cells), min(w, c0 + radius_cells + 1)
    win = node_idx[r_lo:r_hi, c_lo:c_hi]
    mask = win >= 0
    if not mask.any():
        return None
    rr, cc = np.where(mask)
    idxs = win[rr, cc]
    lab_ok = labels[idxs] == target_label
    if not lab_ok.any():
        return None
    rr = rr[lab_ok] + r_lo
    cc = cc[lab_ok] + c_lo
    d2 = (rr - r0) ** 2 + (cc - c0) ** 2
    j = int(d2.argmin())
    rc = (int(rr[j]), int(cc[j]))
    return rc, int(node_idx[rc[0], rc[1]])


def _ring_anchors(
    grid: np.ndarray,
    node_idx: np.ndarray,
    cell: tuple[int, int],
    radius_cells: int,
    ring_samples: int = _MULTIANCHOR_RING_SAMPLES,
) -> list[tuple[int, int]]:
    """Passable, in-graph anchor cells on the control circle around *cell*.

    Samples *ring_samples* directions at *radius_cells* (≈ the control-circle
    radius) and snaps each to the nearest passable cell.  The returned list is
    de-duplicated and always begins with *cell* itself.  Used to explore exits
    on every side of a control whose snapped cell sits in a quantisation pocket.
    """
    out: list[tuple[int, int]] = [cell]
    if radius_cells and radius_cells > 0:
        h, w = grid.shape
        for s in range(ring_samples):
            ang = 2.0 * math.pi * s / ring_samples
            rr = int(round(cell[0] + radius_cells * math.sin(ang)))
            cc = int(round(cell[1] + radius_cells * math.cos(ang)))
            if 0 <= rr < h and 0 <= cc < w:
                got = nearest_passable(grid, rr, cc, max_radius=3)
                if got is not None and node_idx[got[0], got[1]] >= 0:
                    out.append(got)
    seen: set[tuple[int, int]] = set()
    uniq: list[tuple[int, int]] = []
    for c in out:
        if c not in seen:
            seen.add(c)
            uniq.append(c)
    return uniq


def _obstacle_mask(grid: np.ndarray, barrier: "BarrierCtx | None") -> np.ndarray:
    """Boolean mask of cells a route must not pass *through*: impassable cost
    cells (np.isinf) plus any cell carrying a barrier edge (wall / fence).

    The barrier edge arrays mark thin walls *between* cells; treating the owning
    cell as an obstacle is the right granularity for the enclosed-area homotopy
    test (a fence between two cells separates the regions either side of it).
    """
    obst = np.isinf(grid)
    if barrier is not None:
        for arr in (barrier.E, barrier.S, barrier.SE, barrier.SW):
            if arr is not None and arr.shape == obst.shape:
                obst = obst | arr.astype(bool)
    return obst


def _max_enclosed_obstacle(
    boundary_p: list[tuple[int, int]],
    boundary_r: list[tuple[int, int]],
    obst: np.ndarray,
) -> int:
    """Largest connected impassable component enclosed by the closed loop formed
    by the two dense boundaries *boundary_p* and *boundary_r* (which share their
    endpoints).  Returns the component size in cells, or 0 if the loop encloses
    no obstacle.

    This is the homotopy-class test: two routes belong to different classes
    (pass opposite sides of an obstacle) exactly when the region between them
    contains a substantial impassable block.  Computed on a cropped bounding box
    around both boundaries so the cost is small.
    """
    if not boundary_p or not boundary_r:
        return 0
    H, W = obst.shape
    rs = [c[0] for c in boundary_p] + [c[0] for c in boundary_r]
    cs = [c[1] for c in boundary_p] + [c[1] for c in boundary_r]
    r0 = max(0, min(rs) - 2)
    c0 = max(0, min(cs) - 2)
    r1 = min(H - 1, max(rs) + 2)
    c1 = min(W - 1, max(cs) + 2)
    bm = np.zeros((r1 - r0 + 1, c1 - c0 + 1), dtype=bool)
    for (r, c) in boundary_p:
        bm[r - r0, c - c0] = True
    for (r, c) in boundary_r:
        bm[r - r0, c - c0] = True
    free = ~bm
    lbl, _ = _sp_label(free)
    # Any free region touching the crop border is "outside" the loop.
    border = set(lbl[0, :]) | set(lbl[-1, :]) | set(lbl[:, 0]) | set(lbl[:, -1])
    border.discard(0)
    inside = free & ~np.isin(lbl, list(border))
    enc = inside & obst[r0 : r1 + 1, c0 : c1 + 1]
    if not enc.any():
        return 0
    olbl, _ = _sp_label(enc)
    sizes = np.bincount(olbl.ravel())
    sizes[0] = 0
    return int(sizes.max())


def _diverse_candidate_pool(
    grid, barrier, graph, node_idx, node_cells,
    start_idx, end_idx, raw_a, dist_fwd, pred_fwd, d_opt, deadline,
) -> list[list[tuple[int, int]]]:
    """Build a bounded pool of raw candidate routes (route A already included).

    Combines two generators on the shared graph, each capped at
    _DIVERSE_MAX_STRETCH × optimal:
      • via-vertex band sampling — partition the perpendicular offset from the
        direct line into _DIVERSE_VIA_BANDS bands per side; take the cheapest
        admissible via-vertex in each band (samples the full width of detours,
        not just the single cheapest sector like the legacy selector did);
      • penalty detours — iteratively penalise already-used corridors and re-run
        Dijkstra to surface structurally different alternatives.

    The pool is deliberately generous; distinctness is enforced later by the two
    gates in _select_diverse_routes, so junk here is harmless.
    """
    h, w = grid.shape
    raw_pool: list[list[tuple[int, int]]] = [raw_a]
    max_cost = _DIVERSE_MAX_STRETCH * d_opt if math.isfinite(d_opt) else math.inf

    # ── Via-vertex band sampling ────────────────────────────────────────────
    try:
        dist_bwd, pred_bwd = _dijkstra_from(graph, end_idx)
        via = dist_fwd + dist_bwd
        s = node_cells[start_idx]
        e = node_cells[end_idx]
        dr, dc = e[0] - s[0], e[1] - s[1]
        seg = math.hypot(dr, dc) or 1.0
        nr, nc = node_cells[:, 0], node_cells[:, 1]
        par = (nr - s[0]) * (dr / seg) + (nc - s[1]) * (dc / seg)
        perp = (nr - s[0]) * (-dc / seg) + (nc - s[1]) * (dr / seg)
        in_band = (par >= 0.06 * seg) & (par <= 0.94 * seg)
        ok = np.isfinite(via) & (via <= max_cost) & in_band
        if ok.any():
            pmax = float(np.nanmax(np.abs(perp[ok])))
            if pmax > 0:
                bnds = np.linspace(0, pmax, _DIVERSE_VIA_BANDS + 1)
                for sign in (+1, -1):
                    for bi in range(_DIVERSE_VIA_BANDS):
                        if time.monotonic() > deadline:
                            break
                        sel = ok & (sign * perp >= bnds[bi]) & (sign * perp < bnds[bi + 1])
                        idx = np.where(sel)[0]
                        if idx.size == 0:
                            continue
                        vi = int(idx[np.argmin(via[idx])])
                        sf = _trace_path_dijkstra(pred_fwd, start_idx, vi, node_cells)
                        sb = _trace_path_dijkstra(pred_bwd, end_idx, vi, node_cells)
                        if sf and sb:
                            raw_pool.append(sf + list(reversed(sb[:-1])))
    except Exception:
        pass

    # ── Penalty detours ─────────────────────────────────────────────────────
    try:
        coo = graph.tocoo()
        bdata, row, col = coo.data, coo.row, coo.col
        n = graph.shape[0]
        kmask = np.zeros((h, w), dtype=bool)
        for (r, c) in raw_a:
            kmask[r, c] = True
        for t in range(1, _DIVERSE_PENALTY_TRIES + 1):
            if time.monotonic() > deadline:
                break
            used = _binary_dilation(kmask, iterations=_VIA_EXCLUSION_CELLS)
            pen = np.zeros(n, dtype=np.float32)
            ur, uc = np.where(used)
            ui = node_idx[ur, uc]
            ui = ui[ui >= 0]
            pen[ui] = _PENALTY_FACTOR * t
            pg = _csr_matrix((bdata + pen[row] + pen[col], (row, col)), shape=graph.shape)
            dist, pred = _dijkstra_from(pg, start_idx)
            if not math.isfinite(dist[end_idx]):
                break
            raw = _trace_path_dijkstra(pred, start_idx, end_idx, node_cells)
            if raw is None or _path_grid_cost(grid, raw) > max_cost:
                continue
            raw_pool.append(raw)
            for (r, c) in raw:
                kmask[r, c] = True
    except Exception:
        pass

    return raw_pool


def _select_diverse_routes(
    grid, barrier, obst, raw_pool, k, min_cc_cells, tol_cells, deadline,
) -> list[list[tuple[int, int]]]:
    """Greedy cheapest-first selection of genuinely distinct routes from the
    candidate pool.

    Route A (raw_pool[0], the optimum) is always kept.  Each further candidate
    is added only if it is distinct — passes BOTH gates — from EVERY already-kept
    route, measured on the string-pulled display geometry:
      • _max_enclosed_obstacle ≥ min_cc_cells  (different homotopy class), AND
      • _corridor_overlap ≤ _DIVERSE_SHARE_MAX  (visually substantial divergence).
    Stops once k routes are kept.  Returns the string-pulled display paths.
    """
    cand = []
    for raw in raw_pool:
        disp = _string_pull(grid, raw, barrier)
        boundary = list(_iter_dense_cells(disp))
        dense = set(boundary)
        cand.append((_path_grid_cost(grid, raw), disp, boundary, dense))

    if not cand:
        return []
    kept = [cand[0]]
    base_len = _path_geom_length(cand[0][1])
    max_len = base_len * _DIVERSE_MAX_DETOUR if base_len > 0 else math.inf
    rest = sorted(cand[1:], key=lambda x: x[0])
    for cost, disp, boundary, dense in rest:
        if len(kept) >= k or time.monotonic() > deadline:
            break
        # Distance gate: reject alternatives far longer than the optimum — they
        # are not real route choices, only "technically possible" detours.
        if _path_geom_length(disp) > max_len:
            continue
        distinct = True
        for (_, _, kb, kd) in kept:
            if _max_enclosed_obstacle(boundary, kb, obst) < min_cc_cells:
                distinct = False
                break
            if _corridor_overlap(dense, kd, grid.shape, tol_cells) > _DIVERSE_SHARE_MAX:
                distinct = False
                break
        if distinct:
            kept.append((cost, disp, boundary, dense))
    return [c[1] for c in kept]


def _iter_dense_cells(path: list[tuple[int, int]]) -> Iterator[tuple[int, int]]:
    """Yield the dense Bresenham cells covering every segment of *path* (with the
    shared endpoints, so two paths sharing endpoints form a closed loop)."""
    for i in range(len(path) - 1):
        r0, c0 = path[i]
        r1, c1 = path[i + 1]
        yield from _bresenham(r0, c0, r1, c1)


def _find_routes_dijkstra(
    grid: np.ndarray,
    start: tuple[int, int],
    end: tuple[int, int],
    k: int,
    deadline: float,
    barrier: "BarrierCtx | None" = None,
    reanchor_radius_cells: int = 0,
    meters_per_cell: float = 0.0,
) -> list[list[tuple[int, int]]]:
    """
    Diverse-route search built entirely on a single shared Dijkstra graph.

    Route A is the optimal shortest path.  Alternatives are harvested into a
    bounded candidate pool (via-vertex band sampling + penalty detours) and then
    filtered by a homotopy-aware distinctness test so that only genuinely
    different choices are returned (see _select_diverse_routes).  Returns [] if
    end is unreachable from start (different connected components → a genuinely
    enclosed control), unless a component-aware re-anchor within
    *reanchor_radius_cells* can move an endpoint onto the main component (see
    find_diverse_routes).
    """
    graph, node_idx, node_cells = _build_graph(grid, barrier)

    start_idx = int(node_idx[start[0], start[1]])
    end_idx = int(node_idx[end[0], end[1]])
    if start_idx < 0 or end_idx < 0:
        return []

    # ── Route A: shortest path (forward Dijkstra) ───────────────────────────
    dist_fwd, pred_fwd = _dijkstra_from(graph, start_idx)
    if not math.isfinite(dist_fwd[end_idx]):
        # Endpoints are in different components.  Before giving up, try a small
        # component-aware re-anchor: the control's snapped cell may sit in a tiny
        # quantisation pocket while the real network is a few cells away.  We
        # only MOVE the routing endpoint onto the main component (≤ the control-
        # circle radius); we never fabricate a connector across the seal.
        rescued = False
        if reanchor_radius_cells and reanchor_radius_cells > 0:
            from scipy.sparse.csgraph import connected_components
            _, labels = connected_components(graph, directed=False)
            main = int(np.bincount(labels).argmax())
            si, ei = start_idx, end_idx
            ns, ne = start, end
            if labels[start_idx] != main:
                got = _nearest_in_component(
                    node_idx, labels, main, start, reanchor_radius_cells
                )
                if got:
                    ns, si = got
            if labels[end_idx] != main:
                got = _nearest_in_component(
                    node_idx, labels, main, end, reanchor_radius_cells
                )
                if got:
                    ne, ei = got
            if labels[si] == main and labels[ei] == main and (
                si != start_idx or ei != end_idx
            ):
                start, end, start_idx, end_idx = ns, ne, si, ei
                dist_fwd, pred_fwd = _dijkstra_from(graph, start_idx)
                rescued = math.isfinite(dist_fwd[end_idx])
        if not rescued:
            return []  # end walled off from start — no valid route

    raw_a = _trace_path_dijkstra(pred_fwd, start_idx, end_idx, node_cells)
    if raw_a is None:
        return []
    route_a = _string_pull(grid, raw_a, barrier)
    if k <= 1:
        return [route_a]

    d_opt = float(dist_fwd[end_idx])

    # ── Diverse alternatives: bounded pool + homotopy-gated selection ────────
    if time.monotonic() > deadline:
        return [route_a]

    try:
        raw_pool = _diverse_candidate_pool(
            grid, barrier, graph, node_idx, node_cells,
            start_idx, end_idx, raw_a, dist_fwd, pred_fwd, d_opt, deadline,
        )
        raw_pool.extend(
            _multi_anchor_candidates(
                grid, barrier, graph, node_idx, node_cells,
                start, end, start_idx, end_idx, route_a,
                dist_fwd, pred_fwd, reanchor_radius_cells, deadline,
            )
        )
        obst = _obstacle_mask(grid, barrier)
        tol_cells = _dup_tol_cells(grid.shape)
        if meters_per_cell and meters_per_cell > 0:
            min_cc_cells = max(20, round(_DIVERSE_MIN_OBSTACLE_M2 / (meters_per_cell ** 2)))
        else:
            min_cc_cells = _DIVERSE_MIN_OBSTACLE_CELLS_FALLBACK
        selected = _select_diverse_routes(
            grid, barrier, obst, raw_pool, k, min_cc_cells, tol_cells, deadline,
        )
        if selected:
            return selected[:k]
    except Exception:
        pass  # diversity is best-effort; route A is always a valid answer

    return [route_a]


def _multi_anchor_candidates(
    grid, barrier, graph, node_idx, node_cells,
    start, end, start_idx, end_idx, route_a,
    dist_fwd, pred_fwd, reanchor_radius_cells, deadline,
) -> list[list[tuple[int, int]]]:
    """Additive candidate routes that leave/enter a control from anywhere on its
    circle, not just the single snapped cell.

    For each rim anchor around the *start* control we route to the primary *end*
    cell; for each rim anchor around the *end* control we route from the primary
    *start* cell (reusing the forward Dijkstra field).  Every candidate is
    normalised to share the primary start/end (so the homotopy distinctness test
    forms proper loops) and accepted only when its string-pulled length is within
    _MULTIANCHOR_MAX_STRETCH × route A.  Route A itself is never touched, so this
    can only ADD genuine close alternatives, never alter or drop existing routes.
    """
    if not reanchor_radius_cells or reanchor_radius_cells <= 0:
        return []

    len_a = _path_geom_length(route_a)
    max_var = len_a * _MULTIANCHOR_MAX_STRETCH if len_a > 0 else math.inf
    out: list[list[tuple[int, int]]] = []

    def _norm(raw):
        p = raw
        if p[0] != start:
            p = [start] + p
        if p[-1] != end:
            p = p + [end]
        return p

    def _accept(raw):
        if raw is None:
            return None
        p = _norm(raw)
        if _path_geom_length(_string_pull(grid, p, barrier)) <= max_var:
            out.append(p)

    # Start-side anchors → primary end (covers a start-control pocket).
    for s_cell in _ring_anchors(grid, node_idx, start, reanchor_radius_cells)[1:]:
        if time.monotonic() > deadline:
            return out
        si = int(node_idx[s_cell[0], s_cell[1]])
        d_s, p_s = _dijkstra_from(graph, si)
        if math.isfinite(d_s[end_idx]):
            _accept(_trace_path_dijkstra(p_s, si, end_idx, node_cells))

    # Primary start → end-side anchors (covers an end-control pocket); reuse the
    # forward field already computed from start_idx.
    for e_cell in _ring_anchors(grid, node_idx, end, reanchor_radius_cells)[1:]:
        if time.monotonic() > deadline:
            return out
        ei = int(node_idx[e_cell[0], e_cell[1]])
        if math.isfinite(dist_fwd[ei]):
            _accept(_trace_path_dijkstra(pred_fwd, start_idx, ei, node_cells))

    return out


def _path_grid_cost(grid: np.ndarray, path: list[tuple[int, int]]) -> float:
    """Sum the 8-connected move cost of *path* using the same weighting as
    _build_graph (move_dist × mean(cost_src, cost_dst))."""
    total = 0.0
    for (r0, c0), (r1, c1) in zip(path, path[1:]):
        move = math.sqrt(2.0) if (r0 != r1 and c0 != c1) else 1.0
        total += move * (float(grid[r0, c0]) + float(grid[r1, c1])) / 2.0
    return total


def _path_geom_length(path: list[tuple[int, int]]) -> float:
    """Euclidean length of *path* in grid cells (resolution proxy for real
    distance — the metres-per-cell factor cancels in any ratio)."""
    total = 0.0
    for (r0, c0), (r1, c1) in zip(path, path[1:]):
        total += math.hypot(r1 - r0, c1 - c0)
    return total


def path_to_gps(
    path: list[tuple[int, int]],
    corners: dict,
    grid_h: int,
    grid_w: int,
    epsilon: float = 1.5,
    grid: "np.ndarray | None" = None,
    barrier: "BarrierCtx | None" = None,
) -> list[dict]:
    """
    Convert a grid path to a GPS point list.

    Simplification strategy:
    - When *grid* is provided: obstacle-aware string-pulling is used.
      Every consecutive pair of waypoints is guaranteed to have a clear
      line-of-sight, so the displayed polyline never crosses buildings.
    - When *grid* is None: RDP with *epsilon* tolerance (legacy behaviour,
      safe for Theta* paths which already have LOS between waypoints).

    Args:
        epsilon: RDP tolerance in grid cells (used only when grid is None).
        grid:    Traversability grid for obstacle-aware simplification.

    Returns:
        List of {lat, lng} dicts.
    """
    if len(path) < 2:
        return [grid_to_gps(r, c, corners, grid_h, grid_w) for r, c in path]

    if grid is not None:
        simplified = _string_pull(grid, path, barrier)
    else:
        simplified = _rdp(path, epsilon)

    return [grid_to_gps(r, c, corners, grid_h, grid_w) for r, c in simplified]


def haversine_m(a: dict, b: dict) -> float:
    """Haversine great-circle distance in metres."""
    R = 6_371_000.0
    lat1 = math.radians(a["lat"])
    lat2 = math.radians(b["lat"])
    dlat = math.radians(b["lat"] - a["lat"])
    dlng = math.radians(b["lng"] - a["lng"])
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    return 2 * R * math.asin(min(1.0, math.sqrt(h)))


# ── Theta* implementation ────────────────────────────────────────────────────

def theta_star(
    grid: np.ndarray,
    start: tuple[int, int],
    end: tuple[int, int],
    deadline: float | None = None,
    barrier: "BarrierCtx | None" = None,
) -> list[tuple[int, int]] | None:
    """
    Theta* any-angle pathfinding on a weighted cost grid.

    Args:
        grid:     float32 ndarray; finite = passable (cost), np.inf = blocked.
        start:    (row, col) start cell.
        end:      (row, col) goal cell.
        deadline: monotonic time deadline; returns None if exceeded.

    Returns:
        List of (row, col) cells from start to end (inclusive), or None.
    """
    h, w = grid.shape

    if deadline is None:
        deadline = time.monotonic() + DEFAULT_TIMEOUT

    INF_F = float("inf")
    g: dict[tuple[int, int], float] = {start: 0.0}
    parent: dict[tuple[int, int], tuple[int, int]] = {start: start}
    closed: set[tuple[int, int]] = set()
    open_heap: list[tuple[float, tuple[int, int]]] = []
    heapq.heappush(open_heap, (0.0, start))

    visited_count = 0
    _8DIR = [
        (-1, -1), (-1, 0), (-1, 1),
        ( 0, -1),           ( 0, 1),
        ( 1, -1), ( 1, 0), ( 1, 1),
    ]

    while open_heap:
        if time.monotonic() > deadline:
            return None
        visited_count += 1
        if visited_count > _MAX_CELLS_VISITED:
            return None

        _, s = heapq.heappop(open_heap)
        if s in closed:
            continue
        closed.add(s)

        if s == end:
            return _reconstruct(parent, end)

        r, c = s
        ps = parent[s]  # parent of s (for line-of-sight shortcut)

        for dr, dc in _8DIR:
            nr, nc = r + dr, c + dc
            if not (0 <= nr < h and 0 <= nc < w):
                continue
            if np.isinf(grid[nr, nc]):
                continue
            neighbor = (nr, nc)
            if neighbor in closed:
                continue

            # Barrier edge: forbid the direct step across a wall/fence.
            if barrier is not None and barrier.step_blocked(r, c, nr, nc):
                continue

            move_dist = math.sqrt(dr * dr + dc * dc)

            # Theta* relaxation: try to use grandparent for any-angle movement.
            pr, pc = ps
            if ps != s:
                los_cost = _line_cost(grid, ps, neighbor, barrier)
                if los_cost < INF_F:
                    new_g_via_parent = g[ps] + los_cost
                    cur_g = g.get(neighbor, INF_F)
                    if new_g_via_parent < cur_g:
                        g[neighbor] = new_g_via_parent
                        parent[neighbor] = ps
                        f = new_g_via_parent + _heuristic(neighbor, end)
                        heapq.heappush(open_heap, (f, neighbor))
                    continue

            # Standard A* relaxation.
            step_cost = (grid[r, c] + grid[nr, nc]) / 2.0 * move_dist
            new_g = g[s] + step_cost
            cur_g = g.get(neighbor, INF_F)
            if new_g < cur_g:
                g[neighbor] = new_g
                parent[neighbor] = s
                f = new_g + _heuristic(neighbor, end)
                heapq.heappush(open_heap, (f, neighbor))

    return None  # no path found


# ── Via-vertex Dijkstra (Phase 2) ────────────────────────────────────────────

def _build_graph(
    grid: np.ndarray,
    barrier: "BarrierCtx | None" = None,
) -> tuple["_csr_matrix", np.ndarray, np.ndarray]:
    """
    Build a sparse undirected graph from the passable cells of *grid*.

    When *barrier* is provided, inter-cell moves that cross a thin-wall barrier
    edge are removed from the graph, so Dijkstra never steps across a wall.

    Returns
    -------
    graph      : scipy CSR sparse matrix of shape (n, n), edge weights are
                 move_distance × mean(cost_src, cost_dst) for 8-connected moves.
    node_idx   : int32 ndarray of shape (h, w); -1 for impassable cells,
                 0..n-1 for passable cells.
    node_cells : int32 ndarray of shape (n, 2); node_cells[i] = (row, col).
    """
    h, w = grid.shape
    passable = ~np.isinf(grid)

    node_idx = np.full((h, w), -1, dtype=np.int32)
    rows, cols = np.where(passable)
    n = len(rows)
    node_idx[rows, cols] = np.arange(n, dtype=np.int32)
    node_cells = np.stack([rows, cols], axis=1).astype(np.int32)

    # 8-directional moves: (dr, dc, euclidean_dist)
    _DIRS8 = [
        (-1, -1, math.sqrt(2)), (-1, 0, 1.0), (-1, 1, math.sqrt(2)),
        ( 0, -1, 1.0),                          ( 0, 1, 1.0),
        ( 1, -1, math.sqrt(2)), ( 1, 0, 1.0),  ( 1, 1, math.sqrt(2)),
    ]

    def _edge_blocked(dr, dc, r_s, c_s, r_d, c_d):
        """Vectorised barrier test for move (r_s,c_s)->(r_d,c_d) in direction (dr,dc)."""
        if barrier is None:
            return np.zeros(len(r_s), dtype=bool)
        if dr == 0 and dc == 1:
            return barrier.E[r_s, c_s]
        if dr == 0 and dc == -1:
            return barrier.E[r_d, c_d]
        if dr == 1 and dc == 0:
            return barrier.S[r_s, c_s]
        if dr == -1 and dc == 0:
            return barrier.S[r_d, c_d]
        if dr == 1 and dc == 1:
            return barrier.SE[r_s, c_s]
        if dr == -1 and dc == -1:
            return barrier.SE[r_d, c_d]
        if dr == 1 and dc == -1:
            return barrier.SW[r_s, c_s]
        if dr == -1 and dc == 1:
            return barrier.SW[r_d, c_d]
        return np.zeros(len(r_s), dtype=bool)

    all_from: list[np.ndarray] = []
    all_to:   list[np.ndarray] = []
    all_w:    list[np.ndarray] = []

    for dr, dc, move_dist in _DIRS8:
        nr = rows + dr
        nc = cols + dc

        valid = (nr >= 0) & (nr < h) & (nc >= 0) & (nc < w)
        r_v  = rows[valid]
        c_v  = cols[valid]
        nr_v = nr[valid]
        nc_v = nc[valid]

        nbr_node = node_idx[nr_v, nc_v]
        passable_nb = nbr_node >= 0

        r_s  = r_v[passable_nb]
        c_s  = c_v[passable_nb]
        r_d  = nr_v[passable_nb]
        c_d  = nc_v[passable_nb]

        # Drop edges crossing a barrier wall/fence.
        keep = ~_edge_blocked(dr, dc, r_s, c_s, r_d, c_d)
        r_s, c_s, r_d, c_d = r_s[keep], c_s[keep], r_d[keep], c_d[keep]

        src  = node_idx[r_s, c_s]
        dst  = node_idx[r_d, c_d]
        c_src = grid[r_s, c_s]
        c_dst = grid[r_d, c_d]
        w_e  = (move_dist * (c_src + c_dst) / 2.0).astype(np.float32)

        all_from.append(src)
        all_to.append(dst)
        all_w.append(w_e)

    ef = np.concatenate(all_from)
    et = np.concatenate(all_to)
    ew = np.concatenate(all_w)

    graph = _csr_matrix((ew, (ef, et)), shape=(n, n), dtype=np.float32)
    return graph, node_idx, node_cells


def _dijkstra_from(
    graph: "_csr_matrix",
    source_idx: int,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Full single-source Dijkstra via scipy.

    Returns
    -------
    dist : float64 ndarray of shape (n,) — shortest distance from source.
    pred : int32  ndarray of shape (n,) — predecessor index (-9999 if none).
    """
    dist, pred = _sp_dijkstra(
        graph,
        directed=False,
        indices=source_idx,
        return_predecessors=True,
    )
    return dist, pred


def _trace_path_dijkstra(
    pred: np.ndarray,
    source_idx: int,
    target_idx: int,
    node_cells: np.ndarray,
) -> list[tuple[int, int]] | None:
    """
    Reconstruct the shortest path from *source_idx* to *target_idx* by
    following the predecessor chain backwards.

    Returns a list of (row, col) tuples, or None if the path is broken.
    """
    if target_idx == source_idx:
        r, c = node_cells[source_idx]
        return [(int(r), int(c))]

    if pred[target_idx] < 0:
        return None  # unreachable

    path_indices: list[int] = []
    cur = target_idx
    max_steps = len(pred) + 1

    for _ in range(max_steps):
        path_indices.append(cur)
        if cur == source_idx:
            break
        nxt = int(pred[cur])
        if nxt < 0:
            return None  # broken chain
        cur = nxt
    else:
        return None  # cycle guard

    path_indices.reverse()
    return [(int(node_cells[i, 0]), int(node_cells[i, 1])) for i in path_indices]


def _select_via_vertices(
    grid: np.ndarray,
    start: tuple[int, int],
    end: tuple[int, int],
    path_a: list[tuple[int, int]],
    dist_fwd: np.ndarray,
    dist_bwd: np.ndarray,
    d_opt: float,
    node_idx: np.ndarray,
    node_cells: np.ndarray,
    n: int = 2,
    max_stretch: float = _VIA_MAX_STRETCH,
) -> list[tuple[int, int]]:
    """
    Select *n* via-vertex candidates for diverse route reconstruction.

    Candidates must satisfy:
    - via_cost = d_fwd[v] + d_bwd[v] ≤ max_stretch × d_opt  (not too long)
    - v is NOT within *_VIA_EXCLUSION_CELLS* of any route-A cell
    - v lies between 15% and 85% along the start→end segment (parallel projection)

    The candidates are split into left/right sectors perpendicular to the
    start→end direction so that routes B and C genuinely diverge.

    Returns list of (row, col) tuples (at most *n* elements).
    """
    h, w = grid.shape
    n_nodes = len(node_cells)

    # ── Via-cost and reachability filter ────────────────────────────────────
    via_cost = dist_fwd + dist_bwd
    reachable = np.isfinite(dist_fwd) & np.isfinite(dist_bwd)
    stretch_ok = via_cost <= max_stretch * d_opt
    candidate_mask = reachable & stretch_ok

    if not candidate_mask.any():
        return []

    # ── Exclude cells near route A ───────────────────────────────────────────
    route_a_mask = np.zeros((h, w), dtype=bool)
    for r, c in path_a:
        route_a_mask[r, c] = True
    dilated_a = _binary_dilation(route_a_mask, iterations=_VIA_EXCLUSION_CELLS)
    node_r = node_cells[:, 0]
    node_c = node_cells[:, 1]
    not_near_a = ~dilated_a[node_r, node_c]
    candidate_mask &= not_near_a

    if not candidate_mask.any():
        return []

    # ── Geometry: direction vectors ──────────────────────────────────────────
    dr = end[0] - start[0]
    dc = end[1] - start[1]
    seg_len = math.sqrt(dr * dr + dc * dc)

    if seg_len < 1e-6:
        return []  # start == end

    # Parallel unit vector (along direct route)
    par_r, par_c = dr / seg_len, dc / seg_len
    # Perpendicular unit vector (90° CCW from parallel)
    perp_r, perp_c = -dc / seg_len, dr / seg_len

    # ── Parallel projection: keep candidates between 15% and 85% of route ───
    cand_idxs = np.where(candidate_mask)[0]
    cand_r = node_cells[cand_idxs, 0]
    cand_c = node_cells[cand_idxs, 1]

    par_proj = (
        (cand_r - start[0]) * par_r +
        (cand_c - start[1]) * par_c
    )
    in_middle = (par_proj >= 0.15 * seg_len) & (par_proj <= 0.85 * seg_len)

    # Perpendicular projection (left < 0 < right of direct line)
    mid_r = (start[0] + end[0]) / 2.0
    mid_c = (start[1] + end[1]) / 2.0
    perp_proj = (
        (cand_r - mid_r) * perp_r +
        (cand_c - mid_c) * perp_c
    )

    # Minimum perpendicular offset: ensure the via-vertex is genuinely off-route.
    # A cell too close to the direct line would produce a path nearly identical to A.
    min_perp = max(6.0, 0.08 * seg_len)
    has_perp_offset = np.abs(perp_proj) >= min_perp

    cand_costs = via_cost[cand_idxs]

    # ── Sector-based selection ───────────────────────────────────────────────
    # Sort all candidates by via_cost ascending.
    sort_order = np.argsort(cand_costs)

    selected: list[int] = []   # node indices

    # Try to fill left then right sectors first (diverse directions).
    # Require minimum perpendicular offset to ensure the path differs from A.
    sectors = [
        (perp_proj < -min_perp),   # "left"  sector (significantly left of route)
        (perp_proj >= min_perp),   # "right" sector (significantly right of route)
    ]
    for sector_mask in sectors:
        if len(selected) >= n:
            break
        for i in sort_order:
            if not sector_mask[i]:
                continue
            if not in_middle[i]:
                continue
            node_i = int(cand_idxs[i])
            # Mutual distance check: via-vertex must be ≥ 8 cells from existing selections.
            r_i, c_i = int(node_cells[node_i, 0]), int(node_cells[node_i, 1])
            too_close = False
            for s_idx in selected:
                rs, cs = int(node_cells[s_idx, 0]), int(node_cells[s_idx, 1])
                if abs(r_i - rs) + abs(c_i - cs) < 8:
                    too_close = True
                    break
            if not too_close:
                selected.append(node_i)
                break

    # If still short, relax constraints progressively.
    # First: relax in_middle, keep perp_offset requirement.
    if len(selected) < n:
        selected_set = set(selected)
        for sector_mask in sectors:
            if len(selected) >= n:
                break
            for i in sort_order:
                if not sector_mask[i]:
                    continue
                node_i = int(cand_idxs[i])
                if node_i in selected_set:
                    continue
                r_i, c_i = int(node_cells[node_i, 0]), int(node_cells[node_i, 1])
                too_close = False
                for s_idx in selected:
                    rs, cs = int(node_cells[s_idx, 0]), int(node_cells[s_idx, 1])
                    if abs(r_i - rs) + abs(c_i - cs) < 8:
                        too_close = True
                        break
                if not too_close:
                    selected.append(node_i)
                    selected_set.add(node_i)
                    break

    # Last resort: relax ALL constraints, just pick cheapest remaining.
    if len(selected) < n:
        selected_set = set(selected)
        for i in sort_order:
            if len(selected) >= n:
                break
            node_i = int(cand_idxs[i])
            if node_i in selected_set:
                continue
            r_i, c_i = int(node_cells[node_i, 0]), int(node_cells[node_i, 1])
            too_close = False
            for s_idx in selected:
                rs, cs = int(node_cells[s_idx, 0]), int(node_cells[s_idx, 1])
                if abs(r_i - rs) + abs(c_i - cs) < 8:
                    too_close = True
                    break
            if not too_close:
                selected.append(node_i)

    return [(int(node_cells[i, 0]), int(node_cells[i, 1])) for i in selected]


def _find_via_vertex_routes(
    grid: np.ndarray,
    start: tuple[int, int],
    end: tuple[int, int],
    path_a: list[tuple[int, int]],
    n_routes: int,
    deadline: float,
    barrier: "BarrierCtx | None" = None,
) -> list[list[tuple[int, int]]]:
    """
    Core via-vertex orchestrator.

    Builds the passable-cell graph, runs full Dijkstra from start and end,
    selects the best via-vertices in left/right sectors, and reconstructs
    the optimal paths through those vertices.

    Each returned path is verified for Jaccard diversity against route A and
    against previously found alternatives.

    Raises any exception so the caller can catch and fall back to penalty method.
    """
    # ── Build graph ──────────────────────────────────────────────────────────
    graph, node_idx, node_cells = _build_graph(grid, barrier)
    if time.monotonic() > deadline:
        return []

    start_idx = int(node_idx[start[0], start[1]])
    end_idx   = int(node_idx[end[0],   end[1]])
    if start_idx < 0 or end_idx < 0:
        return []  # caller must have snapped start/end to passable cells

    # ── Full Dijkstra from start and from end ────────────────────────────────
    dist_fwd, pred_fwd = _dijkstra_from(graph, start_idx)
    if time.monotonic() > deadline:
        return []

    dist_bwd, pred_bwd = _dijkstra_from(graph, end_idx)
    if time.monotonic() > deadline:
        return []

    d_opt = float(dist_fwd[end_idx])
    if not math.isfinite(d_opt):
        return []  # no path at all (shouldn't happen if Theta* succeeded)

    # ── Select via-vertices ──────────────────────────────────────────────────
    via_cells = _select_via_vertices(
        grid, start, end, path_a,
        dist_fwd, dist_bwd, d_opt,
        node_idx, node_cells, n=n_routes,
    )

    routes: list[list[tuple[int, int]]] = []
    cells_a = set(path_a)

    for v_rc in via_cells:
        if time.monotonic() > deadline:
            break

        v_idx = int(node_idx[v_rc[0], v_rc[1]])
        if v_idx < 0:
            continue

        # Reconstruct path: start → v (following pred_fwd)
        seg_fwd = _trace_path_dijkstra(pred_fwd, start_idx, v_idx, node_cells)
        # Reconstruct path: end → v (following pred_bwd), then reverse
        seg_bwd = _trace_path_dijkstra(pred_bwd, end_idx, v_idx, node_cells)

        if seg_fwd is None or seg_bwd is None:
            continue

        # seg_bwd goes end → v; reversed it goes v → end.
        # Remove the duplicate via-vertex at the junction.
        raw_path = seg_fwd + list(reversed(seg_bwd[:-1]))

        # String-pull: compress the grid-aligned Dijkstra path so that every
        # consecutive pair of waypoints has a clear line-of-sight.  This prevents
        # the visual artefact of polyline segments appearing to cross buildings.
        full_path = _string_pull(grid, raw_path, barrier)

        # ── Diversity checks ─────────────────────────────────────────────────
        # Use the raw (unpulled) cell set for Jaccard to preserve accuracy.
        cells_v = set(raw_path)
        j_a = _jaccard(cells_v, cells_a)
        if j_a > _VIA_JACCARD_THRESHOLD:
            continue

        ok = True
        for prev in routes:
            if _jaccard(cells_v, set(prev)) > _VIA_JACCARD_THRESHOLD:
                ok = False
                break
        if not ok:
            continue

        routes.append(full_path)

    return routes


# ── Penalty-based fallback (original Phase 1 approach) ──────────────────────

def _penalty_diverse_routes(
    grid: np.ndarray,
    start: tuple[int, int],
    end: tuple[int, int],
    existing_routes: list[list[tuple[int, int]]],
    n_more: int,
    deadline: float,
    barrier: "BarrierCtx | None" = None,
) -> list[list[tuple[int, int]]]:
    """
    Find *n_more* additional diverse routes using the penalty-based approach.

    Corridors around all *existing_routes* are pre-penalised so that Theta*
    is forced to explore different terrain.  Each new route is also checked
    with the Jaccard threshold.
    """
    routes: list[list[tuple[int, int]]] = []
    penalty_grid = grid.copy()

    # Pre-penalise all already-found routes.
    for pr in existing_routes:
        penalty_grid = _apply_penalty(penalty_grid, pr)

    for _ in range(n_more):
        if time.monotonic() > deadline:
            break

        path = theta_star(penalty_grid, start, end, deadline=deadline, barrier=barrier)
        if path is None:
            break

        cells = set(path)
        accepted = True

        # Check against all existing + already-appended routes.
        all_prev = existing_routes + routes
        for prev_path in all_prev:
            j = _jaccard(cells, set(prev_path))
            if j >= _JACCARD_THRESHOLD:
                accepted = False
                for retry in range(_MAX_JACCARD_RETRIES):
                    penalty_grid = _apply_penalty(
                        penalty_grid, prev_path,
                        penalty=_PENALTY_FACTOR * (2 ** retry),
                    )
                    path = theta_star(penalty_grid, start, end, deadline=deadline, barrier=barrier)
                    if path is None:
                        accepted = False
                        break
                    j = _jaccard(set(path), set(prev_path))
                    if j < _JACCARD_THRESHOLD:
                        cells = set(path)
                        accepted = True
                        break
                break

        if accepted and path is not None:
            routes.append(path)
            penalty_grid = _apply_penalty(penalty_grid, path)

    return routes


# ── Theta* helpers ──────────────────────────────────────────────────────────

def _heuristic(a: tuple[int, int], b: tuple[int, int]) -> float:
    """Admissible heuristic: euclidean distance × minimum possible cost."""
    return 0.8 * math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2)


def _line_cost(
    grid: np.ndarray,
    a: tuple[int, int],
    b: tuple[int, int],
    barrier: "BarrierCtx | None" = None,
) -> float:
    """
    Cost of moving directly from cell a to cell b along the Bresenham line.

    Returns the euclidean distance × mean cell cost, or inf if any cell is blocked.

    Corner-crossing check: when the Bresenham path makes a diagonal step (both
    row and column change simultaneously), the two "skipped" orthogonal cells
    (the corners the line clips through) are also checked.  This prevents
    string_pull from producing GPS segments that visually cut through building
    corners even though the Bresenham centre-line stays in passable cells.

    Barrier check: when *barrier* is provided, every unit step along the line is
    tested against the thin-wall edge masks; a line crossing any barrier edge
    (wall / fence) is rejected with inf cost.
    """
    cells = list(_bresenham(a[0], a[1], b[0], b[1]))
    h, w = grid.shape
    cost_sum = 0.0
    n = 0
    prev: tuple[int, int] | None = None
    for r, c in cells:
        if not (0 <= r < h and 0 <= c < w):
            return float("inf")
        val = float(grid[r, c])
        if math.isinf(val):
            return float("inf")
        # Corner-crossing check for diagonal Bresenham steps.
        # When the step moves diagonally (Δr=1, Δc=1), the two orthogonal
        # "corner" cells (prev_r, c) and (r, prev_c) are implicitly clipped.
        # If either is impassable, the visual GPS line crosses an obstacle.
        if prev is not None:
            pr, pc = prev
            if abs(r - pr) == 1 and abs(c - pc) == 1:
                if 0 <= pr < h and 0 <= c < w and math.isinf(grid[pr, c]):
                    return float("inf")
                if 0 <= r < h and 0 <= pc < w and math.isinf(grid[r, pc]):
                    return float("inf")
            # Barrier edge crossing on this unit step.
            if barrier is not None and barrier.step_blocked(pr, pc, r, c):
                return float("inf")
        cost_sum += val
        n += 1
        prev = (r, c)
    if n == 0:
        return 0.0
    dist = math.sqrt((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2)
    return dist * (cost_sum / n)


def _string_pull(
    grid: np.ndarray,
    path: list[tuple[int, int]],
    barrier: "BarrierCtx | None" = None,
) -> list[tuple[int, int]]:
    """
    Obstacle-aware path compression (greedy line-of-sight string-pulling).

    For each anchor waypoint, find the *farthest* later waypoint reachable via
    a Bresenham line that crosses no impassable cell.  Advance to that waypoint
    and repeat.

    Unlike RDP, this NEVER produces a segment that crosses an impassable cell
    or a barrier edge, so the resulting polyline can be displayed directly
    without visual artefacts.

    Time complexity: O(n²) worst case, O(n · k) average where k is the number
    of output waypoints (typically k ≪ n).
    """
    if len(path) <= 2:
        return list(path)

    result: list[tuple[int, int]] = [path[0]]
    anchor = 0
    n = len(path)

    while anchor < n - 1:
        # Scan all remaining points to find the farthest one with clear LOS.
        reach = anchor + 1
        for j in range(anchor + 2, n):
            if _line_cost(grid, path[anchor], path[j], barrier) < float("inf"):
                reach = j
        result.append(path[reach])
        anchor = reach

    return result


def _bresenham(r0: int, c0: int, r1: int, c1: int) -> Iterator[tuple[int, int]]:
    """Yield (row, col) cells along the Bresenham line from (r0,c0) to (r1,c1)."""
    dr = abs(r1 - r0)
    dc = abs(c1 - c0)
    r, c = r0, c0
    sr = 1 if r1 > r0 else -1
    sc = 1 if c1 > c0 else -1

    if dc > dr:
        err = dc // 2
        while c != c1:
            yield (r, c)
            err -= dr
            if err < 0:
                r += sr
                err += dc
            c += sc
    else:
        err = dr // 2
        while r != r1:
            yield (r, c)
            err -= dc
            if err < 0:
                c += sc
                err += dr
            r += sr
    yield (r1, c1)


def _reconstruct(
    parent: dict[tuple[int, int], tuple[int, int]],
    end: tuple[int, int],
) -> list[tuple[int, int]]:
    """Trace the parent chain back from end to start."""
    path = []
    cur = end
    while True:
        path.append(cur)
        p = parent[cur]
        if p == cur:
            break
        cur = p
    return list(reversed(path))


def _jaccard(set_a: set, set_b: set) -> float:
    """Jaccard similarity: |A ∩ B| / |A ∪ B|."""
    inter = len(set_a & set_b)
    union = len(set_a | set_b)
    return inter / union if union > 0 else 0.0


def _dup_tol_cells(grid_shape: tuple[int, int]) -> int:
    """Tolerance band half-width (cells) for near-duplicate corridor overlap.

    Derived from the grid resolution when known.  The cost grid is a downsample
    of the 300-DPI raster; without the explicit scale/factor here we approximate
    the cell size from the grid dimensions, falling back to a fixed cell count.
    """
    return _DUP_TOL_CELLS_FALLBACK


def _dilate_cells(cells: set, grid_shape: tuple[int, int], tol_cells: int) -> np.ndarray:
    """Return a boolean mask of *cells* dilated (buffered) by tol_cells."""
    h, w = grid_shape
    mask = np.zeros((h, w), dtype=bool)
    if not cells:
        return mask
    rr = np.fromiter((rc[0] for rc in cells), dtype=np.intp, count=len(cells))
    cc = np.fromiter((rc[1] for rc in cells), dtype=np.intp, count=len(cells))
    mask[rr, cc] = True
    if tol_cells > 0 and _HAS_SCIPY:
        mask = _binary_dilation(mask, iterations=tol_cells)
    return mask


def _corridor_overlap(
    cells_a: set,
    cells_b: set,
    grid_shape: tuple[int, int],
    tol_cells: int,
) -> float:
    """Symmetric buffered-corridor overlap of two routes in [0, 1].

    Each route's cells are dilated by *tol_cells* to form a tolerance band.
    We compute, for each route, the fraction of its cells lying inside the OTHER
    route's band, then return the *mean* of the two fractions.

    Using the mean (rather than the min) makes the measure more sensitive to
    mutual similarity: two routes that both cover ~78% of each other's band score
    0.78, whereas min would give only 0.77 and might slip under the threshold.
    A route that merely shares one short segment with a much longer one still
    scores low because the longer route's fraction is small.
    """
    if not cells_a or not cells_b:
        return 0.0
    band_a = _dilate_cells(cells_a, grid_shape, tol_cells)
    band_b = _dilate_cells(cells_b, grid_shape, tol_cells)

    a_in_b = sum(1 for (r, c) in cells_a if band_b[r, c]) / len(cells_a)
    b_in_a = sum(1 for (r, c) in cells_b if band_a[r, c]) / len(cells_b)
    return (a_in_b + b_in_a) / 2


def _is_near_duplicate(
    cand_cells: set,
    existing_cells: set,
    grid_shape: tuple[int, int],
    tol_cells: int | None = None,
    jaccard_threshold: float = _VIA_JACCARD_THRESHOLD,
) -> bool:
    """True when *cand_cells* is practically identical to *existing_cells*.

    Combines the original exact-cell Jaccard test with a buffered-corridor
    overlap test (multi-signal, additive — only rejects more duplicates):

      • exact Jaccard > jaccard_threshold                      → duplicate, OR
      • corridor overlap ≥ _DUP_OVERLAP_EXTREME                → duplicate, OR
      • corridor overlap ≥ _DUP_OVERLAP_THRESHOLD AND the two routes' lengths
        (cell counts) are within _DUP_LENGTH_FRAC of each other → duplicate.
    """
    if _jaccard(cand_cells, existing_cells) > jaccard_threshold:
        return True
    if tol_cells is None:
        tol_cells = _dup_tol_cells(grid_shape)
    overlap = _corridor_overlap(cand_cells, existing_cells, grid_shape, tol_cells)
    if overlap >= _DUP_OVERLAP_EXTREME:
        return True
    if overlap >= _DUP_OVERLAP_THRESHOLD:
        la, lb = len(cand_cells), len(existing_cells)
        if max(la, lb) > 0 and abs(la - lb) / max(la, lb) <= _DUP_LENGTH_FRAC:
            return True
    return False


def _dense_cells_from_path(path: list[tuple[int, int]]) -> set[tuple[int, int]]:
    """Return a dense set of cells covering all segments of *path*.

    Unlike the sparse string-pulled waypoint list, this uses Bresenham line
    tracing between consecutive waypoints to reconstruct the full visual
    corridor.  Two string-pulled paths that follow the same street (but were
    computed from different raw Dijkstra zigzags) will produce nearly identical
    dense cell sets — enabling reliable deduplication on the final output.
    """
    cells: set[tuple[int, int]] = set()
    for i in range(len(path) - 1):
        r0, c0 = path[i]
        r1, c1 = path[i + 1]
        cells.update(_bresenham(r0, c0, r1, c1))
    return cells


def _final_dedup_routes(
    routes: list[list[tuple[int, int]]],
    grid_shape: tuple[int, int],
    tol_cells: int | None = None,
) -> list[list[tuple[int, int]]]:
    """Remove visually near-duplicate routes from the final (string-pulled) list.

    The upstream Jaccard / corridor-overlap checks work on raw Dijkstra cell
    sets, but string-pull can collapse different raw zigzags to the same visual
    corridor.  This pass compares dense Bresenham representations of the final
    paths — capturing what the user actually sees — and drops any route that is
    near-identical to one already kept.  Route 0 (the optimal) is always kept.
    """
    if tol_cells is None:
        tol_cells = _dup_tol_cells(grid_shape)
    kept: list[list[tuple[int, int]]] = []
    kept_dense: list[set[tuple[int, int]]] = []
    for route in routes:
        dense = _dense_cells_from_path(route)
        if not any(
            _is_near_duplicate(dense, prev, grid_shape, tol_cells)
            for prev in kept_dense
        ):
            kept.append(route)
            kept_dense.append(dense)
    return kept


def _apply_penalty(
    grid: np.ndarray,
    path: list[tuple[int, int]],
    penalty: float = _PENALTY_FACTOR,
    corridor: int = _PENALTY_CORRIDOR,
) -> np.ndarray:
    """Multiply costs of cells in a corridor around path by penalty."""
    penalised = grid.copy()
    h, w = grid.shape
    path_set = set(path)

    for r, c in path:
        for dr in range(-corridor, corridor + 1):
            for dc in range(-corridor, corridor + 1):
                nr, nc = r + dr, c + dc
                if 0 <= nr < h and 0 <= nc < w and not math.isinf(penalised[nr, nc]):
                    penalised[nr, nc] = min(penalised[nr, nc] * penalty, 1e9)

    return penalised


def _rdp(
    path: list[tuple[int, int]],
    epsilon: float,
) -> list[tuple[int, int]]:
    """Ramer-Douglas-Peucker simplification of a grid path."""
    if len(path) <= 2:
        return path

    max_dist = 0.0
    idx = 0
    r0, c0 = path[0]
    r1, c1 = path[-1]

    for i in range(1, len(path) - 1):
        r, c = path[i]
        d = _perp_dist(r, c, r0, c0, r1, c1)
        if d > max_dist:
            max_dist = d
            idx = i

    if max_dist > epsilon:
        left = _rdp(path[: idx + 1], epsilon)
        right = _rdp(path[idx:], epsilon)
        return left[:-1] + right

    return [path[0], path[-1]]


def _perp_dist(
    r: int, c: int,
    r0: int, c0: int,
    r1: int, c1: int,
) -> float:
    """Perpendicular distance from point (r,c) to segment (r0,c0)-(r1,c1)."""
    dr = r1 - r0
    dc = c1 - c0
    seg_len_sq = dr * dr + dc * dc
    if seg_len_sq == 0:
        return math.sqrt((r - r0) ** 2 + (c - c0) ** 2)
    num = abs(dr * (c0 - c) - (r0 - r) * dc)
    return num / math.sqrt(seg_len_sq)
