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


def test_order_by_gps_distance_sorts_choices_and_reports_lengths():
    """The server labels A/B/C after the GPS string-pull; the order must follow
    the GPS distances, not the grid geometry (which can differ by a few m)."""
    pts_short = [{"lat": 50.0, "lng": 22.0}, {"lat": 50.0, "lng": 22.001}]
    pts_long = [{"lat": 50.0, "lng": 22.0}, {"lat": 50.0005, "lng": 22.001}, {"lat": 50.0, "lng": 22.002}]
    ordered = pf.order_by_gps_distance([pts_long, pts_short])
    assert [p for p, _d in ordered] == [pts_short, pts_long]
    assert ordered[0][1] == pytest.approx(pf.haversine_m(pts_short[0], pts_short[1]))
    assert ordered[1][1] > ordered[0][1]
