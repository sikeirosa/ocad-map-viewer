"""
Route-choice pathfinding using Theta* (any-angle A*).

Generates up to k distinct alternative routes between two grid cells on a
weighted cost raster.  Uses a penalty-based multi-path approach with Jaccard
similarity to enforce geographic diversity between alternatives.
"""

from __future__ import annotations

import heapq
import math
import time
from collections import deque
from typing import Iterator

import numpy as np

# Diversity threshold: two routes with Jaccard ≥ this are considered duplicates.
_JACCARD_THRESHOLD = 0.30

# Cost multiplier applied to grid cells near an already-found path.
_PENALTY_FACTOR = 5.0

# Half-width of the penalty corridor around a found path (in grid cells).
_PENALTY_CORRIDOR = 3

# Max retries per alternative when Jaccard check fails.
_MAX_JACCARD_RETRIES = 3

# Maximum grid cells searched before giving up.
_MAX_CELLS_VISITED = 4_000_000

# Default per-route timeout in seconds.
DEFAULT_TIMEOUT = 15.0


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

    Uses the same simplified bilinear projection as pdf_export.gps_to_pixels().
    """
    try:
        lat_max = max(corners["nw"]["lat"], corners["ne"]["lat"])
        lat_min = min(corners["sw"]["lat"], corners["se"]["lat"])
        lng_min = min(corners["nw"]["lng"], corners["sw"]["lng"])
        lng_max = max(corners["ne"]["lng"], corners["se"]["lng"])

        if lat_max <= lat_min or lng_max <= lng_min:
            return None

        u = (lng - lng_min) / (lng_max - lng_min)   # 0 = west, 1 = east
        v = (lat_max - lat) / (lat_max - lat_min)   # 0 = north, 1 = south

        u = max(0.0, min(1.0, u))
        v = max(0.0, min(1.0, v))

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
    """Convert (row, col) grid cell centre to {lat, lng}."""
    lat_max = max(corners["nw"]["lat"], corners["ne"]["lat"])
    lat_min = min(corners["sw"]["lat"], corners["se"]["lat"])
    lng_min = min(corners["nw"]["lng"], corners["sw"]["lng"])
    lng_max = max(corners["ne"]["lng"], corners["se"]["lng"])

    u = (col + 0.5) / grid_w
    v = (row + 0.5) / grid_h
    lat = lat_max - v * (lat_max - lat_min)
    lng = lng_min + u * (lng_max - lng_min)
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
    Find up to k geographically distinct routes using penalty-based multi-path.

    Returns a list of paths (each path = list of (row, col) cells).
    May return fewer than k paths if the terrain doesn't allow diverse alternatives.
    """
    routes: list[list[tuple[int, int]]] = []
    penalty_grid = grid.copy()

    for _ in range(k):
        deadline = time.monotonic() + timeout
        path = theta_star(penalty_grid, start, end, deadline=deadline)

        if path is None:
            break  # no more routes found

        # Check diversity against already-found routes.
        cells = set(path)
        accepted = True
        for prev_path in routes:
            j = _jaccard(cells, set(prev_path))
            if j >= _JACCARD_THRESHOLD:
                accepted = False
                # Retry with higher penalty applied to the corridor.
                for retry in range(_MAX_JACCARD_RETRIES):
                    penalty_grid = _apply_penalty(penalty_grid, prev_path,
                                                  penalty=_PENALTY_FACTOR * (2 ** retry))
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
            # Penalise this corridor for subsequent searches.
            penalty_grid = _apply_penalty(penalty_grid, path)

    return routes


def path_to_gps(
    path: list[tuple[int, int]],
    corners: dict,
    grid_h: int,
    grid_w: int,
    epsilon: float = 1.5,
) -> list[dict]:
    """
    Convert a grid path to a GPS point list, with RDP simplification.

    Args:
        epsilon: RDP tolerance in grid cells (1.5 ≈ 1 cell width).

    Returns:
        List of {lat, lng} dicts.
    """
    if len(path) < 2:
        return [grid_to_gps(r, c, corners, grid_h, grid_w) for r, c in path]

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


# ── Private helpers ──────────────────────────────────────────────────────────

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
