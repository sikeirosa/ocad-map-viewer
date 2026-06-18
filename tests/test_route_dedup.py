"""Tests for near-duplicate route-choice detection in pathfinding.py.

The cost-grid raster of real maps is not shipped with the repo, so these tests
validate the deduplication *algorithm* on synthetic grids and cell sets:

  • two parallel routes offset by a couple of cells  → near-duplicate (rejected),
  • two routes diverging into distinct corridors      → kept (both),
  • routes sharing a segment then splitting           → kept (not a duplicate).
"""

import numpy as np
import pytest

import pathfinding as pf


SHAPE = (60, 60)


def _hline(row, c0, c1):
    return {(row, c) for c in range(c0, c1)}


def test_parallel_offset_is_near_duplicate():
    """Two near-identical routes offset by 2 cells must read as duplicates."""
    a = _hline(30, 5, 55)
    b = _hline(32, 5, 55)
    assert pf._corridor_overlap(a, b, SHAPE, 4) >= pf._DUP_OVERLAP_THRESHOLD
    assert pf._is_near_duplicate(a, b, SHAPE) is True


def test_divergent_routes_are_distinct():
    """Routes in clearly separate corridors must NOT be deduplicated."""
    a = _hline(20, 5, 55)
    b = _hline(45, 5, 55)
    assert pf._corridor_overlap(a, b, SHAPE, 4) == 0.0
    assert pf._is_near_duplicate(a, b, SHAPE) is False


def test_partial_share_then_split_is_distinct():
    """Sharing a common leg then splitting is a genuine choice, keep both."""
    common = _hline(30, 5, 30)
    a = common | {(y, 30) for y in range(30, 55)}     # turns down
    b = common | _hline(30, 30, 55)                    # continues straight
    assert pf._is_near_duplicate(a, b, SHAPE) is False


def test_shorter_distinct_extent_not_duplicate():
    """A shorter route in a genuinely different corridor is kept even though it
    is shorter than the optimal — distinctness is geometric, not length-based."""
    a = _hline(20, 5, 55)            # northern corridor
    b = _hline(45, 5, 40)           # southern corridor, also shorter
    assert pf._is_near_duplicate(b, a, SHAPE) is False


def test_find_diverse_routes_dedups_when_only_clone_exists():
    """On a single dominant corridor, near-duplicate alternatives are filtered
    rather than returned as separate choices."""
    grid = np.ones(SHAPE, dtype=np.float32)
    grid[:28, :] = np.inf
    grid[32:, :] = np.inf
    start = (30, 3)
    end = (30, 56)
    routes = pf.find_diverse_routes(grid, start, end, k=3, timeout=10.0)
    assert len(routes) >= 1
    for i in range(len(routes)):
        for j in range(i + 1, len(routes)):
            assert not pf._is_near_duplicate(
                set(routes[i]), set(routes[j]), grid.shape
            )


def test_find_diverse_routes_keeps_genuine_alternatives():
    """Two wide separate corridors must yield two distinct choices."""
    grid = np.full(SHAPE, np.inf, dtype=np.float32)
    grid[10:14, :] = 1.0
    grid[46:50, :] = 1.0
    grid[10:50, 3:7] = 1.0      # left connector
    grid[10:50, 53:57] = 1.0    # right connector
    start = (30, 5)
    end = (30, 55)
    routes = pf.find_diverse_routes(grid, start, end, k=3, timeout=10.0)
    assert len(routes) >= 2
    assert not pf._is_near_duplicate(set(routes[0]), set(routes[1]), grid.shape)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
