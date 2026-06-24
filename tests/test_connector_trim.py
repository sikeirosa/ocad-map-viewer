"""Regression tests for P1 — multi-anchor connector trim (Défaut 1).

A multi-anchor route choice is normalised to share the control's snapped pocket
cell (pathfinding._multi_anchor_candidates._norm), which prepends/appends a
single NON-adjacent connector from that pocket cell to the rim anchor.  String-
pulling keeps that connector unchecked, so the displayed polyline can start/end
with a straight line across a sealed wall.  _trim_teleport_connectors removes
exactly that connector — and nothing else.

Critical no-regression guard: a legitimate routing move between consecutive
display waypoints is always an adjacent graph step (<= sqrt(2) cells).  Even when
such an adjacent diagonal step clips an impassable corner (so _line_cost == inf),
it must be PRESERVED.  Only non-adjacent blocked connectors are trimmed.
"""

import math

import numpy as np
import pytest

import pathfinding as pf
import traversability as tv


def _open_grid(n: int = 10) -> np.ndarray:
    return np.full((n, n), 1.0, dtype=np.float64)


def test_trim_drops_leading_blocked_connector():
    """A leading non-adjacent segment crossing an INF cell is removed."""
    grid = _open_grid()
    grid[1, 3] = tv.INF
    # (1,1)->(1,5) crosses the wall at (1,3): non-adjacent (4 cells) AND blocked.
    path = [(1, 1), (1, 5), (2, 6), (3, 6)]
    out = pf._trim_teleport_connectors(path, grid, None)
    assert out[0] == (1, 5)
    assert out == [(1, 5), (2, 6), (3, 6)]


def test_trim_drops_trailing_blocked_connector():
    """A trailing non-adjacent segment crossing an INF cell is removed."""
    grid = _open_grid()
    grid[1, 6] = tv.INF
    path = [(3, 3), (2, 4), (1, 4), (1, 8)]  # (1,4)->(1,8) crosses (1,6)
    out = pf._trim_teleport_connectors(path, grid, None)
    assert out[-1] == (1, 4)
    assert out == [(3, 3), (2, 4), (1, 4)]


def test_trim_keeps_adjacent_corner_clip():
    """NO-REGRESSION: an adjacent diagonal step that clips an INF corner (so
    _line_cost == inf) must be preserved — these are legitimate graph moves that
    appear all over route A."""
    grid = _open_grid()
    grid[1, 1] = tv.INF  # corner clipped by the (2,1)->(1,2) diagonal
    path = [(2, 1), (1, 2), (0, 3)]
    assert math.isinf(pf._line_cost(grid, (2, 1), (1, 2), None))  # blocked...
    out = pf._trim_teleport_connectors(path, grid, None)
    assert out == path  # ...but adjacent, so kept unchanged


def test_trim_noop_on_clean_path():
    """A fully clear path is returned unchanged."""
    grid = _open_grid()
    path = [(1, 1), (1, 5), (5, 5)]
    out = pf._trim_teleport_connectors(path, grid, None)
    assert out == path


def test_trim_never_eats_whole_path():
    """The trim is bounded and always leaves at least two points."""
    grid = _open_grid()
    grid[1, 3] = tv.INF
    grid[1, 6] = tv.INF
    path = [(1, 1), (1, 5), (1, 8)]  # both ends look blocked
    out = pf._trim_teleport_connectors(path, grid, None)
    assert len(out) >= 2


def test_path_to_gps_trims_connector():
    """End-to-end: path_to_gps drops the leading connector so the first display
    segment no longer crosses the wall."""
    grid = _open_grid()
    grid[1, 3] = tv.INF
    corners = {
        "nw": {"lat": 1.0, "lng": 0.0},
        "ne": {"lat": 1.0, "lng": 1.0},
        "se": {"lat": 0.0, "lng": 1.0},
        "sw": {"lat": 0.0, "lng": 0.0},
    }
    path = [(1, 1), (1, 5), (2, 6), (3, 6)]
    pts = pf.path_to_gps(path, corners, 10, 10, grid=grid, barrier=None)
    expected_first = pf.grid_to_gps(1, 5, corners, 10, 10)
    assert pts[0]["lat"] == pytest.approx(expected_first["lat"])
    assert pts[0]["lng"] == pytest.approx(expected_first["lng"])


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
