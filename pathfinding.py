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

# Maximum grid cells searched before giving up.
_MAX_CELLS_VISITED = 4_000_000

# Default per-route timeout in seconds.
DEFAULT_TIMEOUT = 15.0


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
) -> list[list[tuple[int, int]]]:
    """
    Find up to k geographically distinct routes between start and end.

    Strategy:
      1. Route A = Theta* on original grid (optimal, smooth).
      2. Routes B/C = via-vertex Dijkstra (truly optimal alternatives in
         perpendicular sectors — left / right of the start→end line).
      3. Fallback: penalty-based Theta* for any routes still missing.

    Returns a list of paths (each path = list of (row, col) cells).
    May return fewer than k paths if the terrain doesn't allow diversity.
    """
    deadline = time.monotonic() + timeout
    routes: list[list[tuple[int, int]]] = []

    # ── Route A: Theta* on the original (unmodified) grid ───────────────────
    path_a = theta_star(grid, start, end, deadline=deadline)
    if path_a is None:
        return []
    routes.append(path_a)

    if k <= 1:
        return routes

    # ── Routes B/C: via-vertex Dijkstra ─────────────────────────────────────
    if _HAS_SCIPY and time.monotonic() < deadline:
        try:
            via_routes = _find_via_vertex_routes(
                grid, start, end, path_a,
                n_routes=k - 1,
                deadline=deadline,
            )
            routes.extend(via_routes)
        except Exception:
            pass  # any error → fall through to penalty fallback

    # ── Fallback: penalty-based Theta* for missing routes ───────────────────
    if len(routes) < k and time.monotonic() < deadline:
        missing = k - len(routes)
        penalty_routes = _penalty_diverse_routes(
            grid, start, end, routes, missing, deadline
        )
        routes.extend(penalty_routes)

    return routes[:k]


def path_to_gps(
    path: list[tuple[int, int]],
    corners: dict,
    grid_h: int,
    grid_w: int,
    epsilon: float = 1.5,
    grid: "np.ndarray | None" = None,
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
        simplified = _string_pull(grid, path)
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

            move_dist = math.sqrt(dr * dr + dc * dc)

            # Theta* relaxation: try to use grandparent for any-angle movement.
            pr, pc = ps
            if ps != s:
                los_cost = _line_cost(grid, ps, neighbor)
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
) -> tuple["_csr_matrix", np.ndarray, np.ndarray]:
    """
    Build a sparse undirected graph from the passable cells of *grid*.

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

        src  = node_idx[r_v[passable_nb],  c_v[passable_nb]]
        dst  = nbr_node[passable_nb]
        c_s  = grid[r_v[passable_nb],  c_v[passable_nb]]
        c_n  = grid[nr_v[passable_nb], nc_v[passable_nb]]
        w_e  = (move_dist * (c_s + c_n) / 2.0).astype(np.float32)

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
    graph, node_idx, node_cells = _build_graph(grid)
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
        full_path = _string_pull(grid, raw_path)

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

        path = theta_star(penalty_grid, start, end, deadline=deadline)
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
                    path = theta_star(penalty_grid, start, end, deadline=deadline)
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
) -> float:
    """
    Cost of moving directly from cell a to cell b along the Bresenham line.

    Returns the euclidean distance × mean cell cost, or inf if any cell is blocked.
    """
    cells = list(_bresenham(a[0], a[1], b[0], b[1]))
    h, w = grid.shape
    cost_sum = 0.0
    n = 0
    for r, c in cells:
        if not (0 <= r < h and 0 <= c < w):
            return float("inf")
        val = float(grid[r, c])
        if math.isinf(val):
            return float("inf")
        cost_sum += val
        n += 1
    if n == 0:
        return 0.0
    dist = math.sqrt((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2)
    return dist * (cost_sum / n)


def _string_pull(
    grid: np.ndarray,
    path: list[tuple[int, int]],
) -> list[tuple[int, int]]:
    """
    Obstacle-aware path compression (greedy line-of-sight string-pulling).

    For each anchor waypoint, find the *farthest* later waypoint reachable via
    a Bresenham line that crosses no impassable cell.  Advance to that waypoint
    and repeat.

    Unlike RDP, this NEVER produces a segment that crosses an impassable cell,
    so the resulting polyline can be displayed directly without visual artefacts.

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
            if _line_cost(grid, path[anchor], path[j]) < float("inf"):
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
