"""Route A is the shortest route by DISTANCE (evolution n°31).

The traversability grid carries a clearance penalty (+2.0 / +0.8 / +0.2 next to
impassable cells) so that Dijkstra prefers wide corridors.  That penalty is
right for shaping the ALTERNATIVES, but it must not decide which route is "A":
on Kasumi the 136 m alley across the service bridge was shown as B behind a
284 m detour (A), and on the WMOC 2026 final (Rzeszów) leg 4 showed A = 559 m,
B = 520 m.  Route A must be the shortest-distance route (IOF rule 16.3: the
announced leg length is the shortest route), and the choices must be returned
in increasing distance.
"""

import numpy as np
import pytest

import pathfinding as pf
import traversability as tv

pytestmark = pytest.mark.skipif(
    not pf._HAS_SCIPY, reason="route selection requires scipy"
)


def _alley_vs_detour_grid():
    """Two solid blocks leave a 1-cell alley on row 25 between cols 10 and 50;
    open ground above (rows 0–14) and below (rows 36–49).  Start (22, 3) and
    end (28, 56) are offset so that the straight line crosses a block (the
    obstacle-aware string-pull cannot collapse a detour onto the alley).  With
    the clearance penalty the alley costs 1.8 per cell, so the penalised
    optimum is the detour (~70 cells), although the alley is ~55 cells."""
    grid = np.full((50, 60), 1.0, dtype=np.float32)
    grid[15:25, 10:51] = np.inf
    grid[26:36, 10:51] = np.inf
    return tv._apply_clearance_penalty(grid)


def test_route_a_is_the_shortest_route_not_the_cheapest():
    grid = _alley_vs_detour_grid()
    routes = pf.find_diverse_routes(grid, start=(22, 3), end=(28, 56), k=3, timeout=20.0)
    assert routes, "a route must exist"
    len_a = pf._path_geom_length(routes[0])
    assert len_a < 60, f"route A must take the alley (~55 cells), got {len_a:.1f}"
    assert all(r == 25 for r, _c in routes[0][1:-1] if 10 <= _c <= 50), "route A must stay in the alley"


def test_choices_are_returned_in_increasing_distance():
    grid = _alley_vs_detour_grid()
    routes = pf.find_diverse_routes(grid, start=(22, 3), end=(28, 56), k=3, timeout=20.0)
    assert len(routes) >= 2, "the detour above/below must remain as an alternative"
    lengths = [pf._path_geom_length(r) for r in routes]
    assert lengths == sorted(lengths), lengths
    assert lengths[1] > 65, f"the alternative must be the detour, got {lengths[1]:.1f}"


def test_single_route_request_returns_the_shortest():
    grid = _alley_vs_detour_grid()
    routes = pf.find_diverse_routes(grid, start=(22, 3), end=(28, 56), k=1, timeout=20.0)
    assert len(routes) == 1
    assert pf._path_geom_length(routes[0]) < 60


def _octile_trap_grid(s: int = 25):
    """Two ways around: an UPPER route at oblique angles (corner (0.8 s, 2 s):
    Euclidean ≈ 4.66 s, octile ≈ 5.0 s) and a LOWER route made of 45° and
    horizontal segments around a block (Euclidean = octile ≈ 4.83 s).  The
    8-neighbour Dijkstra prefers the lower one, but once string-pulled the
    upper one is shorter — it must be route A."""
    n = 4 * s + 1
    grid = np.full((n, n), 1.0, dtype=np.float32)
    grid[int(0.84 * s):2 * s + 1, 2 * s] = np.inf      # wall: upper route turns at its top
    grid[2 * s + 1:3 * s, s:3 * s + 1] = np.inf        # block: lower route runs below it
    return grid, (2 * s, 0), (2 * s, 4 * s)


def test_route_a_is_the_shortest_after_string_pull():
    grid, a, b = _octile_trap_grid(25)
    routes = pf.find_diverse_routes(grid, start=a, end=b, k=3, timeout=20.0)
    assert len(routes) >= 2, "both ways around must be offered"
    lengths = [pf._path_geom_length(r) for r in routes]
    assert lengths == sorted(lengths), lengths
    assert min(r for r, _c in routes[0]) <= 21, f"route A must be the upper route, got {lengths}"


def test_single_route_is_the_shortest_after_string_pull():
    """k = 1: the 8-neighbour optimum (lower, 120.7) must not hide the shorter
    simplified route (upper, ≈ 117) already computed."""
    grid, a, b = _octile_trap_grid(25)
    routes = pf.find_diverse_routes(grid, start=a, end=b, k=1, timeout=20.0)
    assert pf._path_geom_length(routes[0]) < 118.5


def test_shorter_route_not_lost_when_obstacle_below_distinctness_threshold():
    """Small version (obstacle 73 cells < 125-cell distinctness threshold): the
    two ways around are not offered as separate choices, but the one shown must
    be the shorter (upper, ≈ 28.9) rather than the 8-neighbour optimum (lower,
    29.0)."""
    grid, a, b = _octile_trap_grid(6)
    routes = pf.find_diverse_routes(grid, start=a, end=b, k=3, timeout=20.0)
    assert min(r for r, _c in routes[0]) < 12, "route A must be the upper route"
    assert pf._path_geom_length(routes[0]) < 28.97, [pf._path_geom_length(r) for r in routes]


def test_route_a_never_jumps_from_the_control_circle():
    """Multi-anchor candidates leave from a cell on the control circle and are
    normalised by a JUMP to the control cell; they are alternatives, never the
    base.  Start sits in a walled box whose only door faces away from the end:
    a ring anchor just outside the right wall gives a short candidate that
    jumps through the wall.  Route A must be a real path (no segment crosses an
    impassable cell) and must not be shorter than the true geodesic."""
    grid = np.full((41, 70), 1.0, dtype=np.float32)
    grid[10, 5:14] = np.inf; grid[30, 5:14] = np.inf     # box top / bottom
    grid[10:31, 13] = np.inf                              # right wall (faces the end)
    grid[10:31, 5] = np.inf; grid[20, 5] = 1.0            # left wall with a door
    start, end = (20, 10), (20, 60)
    routes = pf.find_diverse_routes(grid, start=start, end=end, k=3, timeout=20.0,
                                    reanchor_radius_cells=6, meters_per_cell=1.0)
    a = routes[0]
    assert a[0] == start and a[-1] == end
    for (r0, c0), (r1, c1) in zip(a, a[1:]):
        for (r, c) in pf._bresenham(r0, c0, r1, c1):
            assert np.isfinite(grid[r, c]), f"route A crosses an obstacle at {(r, c)}"
    assert pf._path_geom_length(a) > 60, pf._path_geom_length(a)


# 40 × 40 cells of ≈ 1.79 m × 1.79 m (0.000643° lat ≈ 0.001° lng at 50° N)
_SQUARE_CORNERS = {"nw": {"lat": 50.000643, "lng": 22.0}, "ne": {"lat": 50.000643, "lng": 22.001},
                   "se": {"lat": 50.0, "lng": 22.001}, "sw": {"lat": 50.0, "lng": 22.0}}


def test_reported_length_includes_the_trimmed_connector():
    """A multi-anchor route is displayed without its connector (control cell →
    circle anchor, across a wall), but its length must still be counted from
    the control: the ordering and the distance shown must not reward the jump."""
    grid = np.full((40, 40), 1.0, dtype=np.float32)
    grid[0:30, 10] = np.inf                       # wall between control and anchor
    corners = _SQUARE_CORNERS
    path = [(5, 5), (5, 15)] + [(5 + i, 15) for i in range(1, 20)]   # jump (5,5)->(5,15) then real steps
    gps = pf.path_to_gps(path, corners, 40, 40, grid=grid)
    assert gps[0] != pf.grid_to_gps(5, 5, corners, 40, 40), "display trims the connector"
    ordered = pf.order_choices([path], corners, 40, 40, grid=grid)
    shown, dist = ordered[0]
    poly = sum(pf.haversine_m(shown[j], shown[j + 1]) for j in range(len(shown) - 1))
    connector = pf.haversine_m(pf.grid_to_gps(5, 5, corners, 40, 40), pf.grid_to_gps(5, 15, corners, 40, 40))
    assert dist == pytest.approx(poly + connector, rel=1e-6)


def test_order_choices_ranks_by_length_from_the_control():
    """A genuine 20-cell route must rank ahead of a 12-cell displayed route that
    needed a 10-cell connector to reach the control."""
    grid = np.full((40, 40), 1.0, dtype=np.float32)
    grid[0:30, 10] = np.inf
    corners = _SQUARE_CORNERS
    jumpy = [(5, 5), (5, 15), (17, 15)]
    genuine = [(5, 5)] + [(5 + i, 5) for i in range(1, 21)]
    ordered = pf.order_choices([jumpy, genuine], corners, 40, 40, grid=grid)
    assert ordered[0][0][0] == pf.grid_to_gps(5, 5, corners, 40, 40)   # genuine first


def _thick_box_grid():
    """Start inside a large thick-walled box (obstacle well above the 200 m²
    distinctness threshold) whose only door faces away from the end.  A ring
    anchor 11 cells away lies just outside the right wall, so a multi-anchor
    candidate (≈ 76 cells with its connector) passes the diversity gates while
    the genuine route through the door is ≈ 141 cells."""
    g = np.full((81, 100), 1.0, dtype=np.float32)
    g[5:9, 3:24] = np.inf; g[71:75, 3:24] = np.inf       # top / bottom
    g[5:75, 20:24] = np.inf                               # right wall, facing the end
    g[5:75, 3:7] = np.inf; g[38:42, 3:7] = 1.0            # left wall with a door
    return g, (40, 14), (40, 90)


def test_wall_jumping_alternative_never_outranks_the_genuine_route():
    grid, start, end = _thick_box_grid()
    routes = pf.find_diverse_routes(grid, start=start, end=end, k=3, timeout=20.0,
                                    reanchor_radius_cells=11, meters_per_cell=1.0)
    a = routes[0]
    for (r0, c0), (r1, c1) in zip(a, a[1:]):
        for (r, c) in pf._bresenham(r0, c0, r1, c1):
            assert np.isfinite(grid[r, c]), f"route A crosses an obstacle at {(r, c)}"
    shown, _d = pf.order_choices(routes, _SQUARE_CORNERS, 81, 100, grid=grid)[0]
    assert shown[0] == pf.grid_to_gps(*start, _SQUARE_CORNERS, 81, 100), "A must start at the control"
