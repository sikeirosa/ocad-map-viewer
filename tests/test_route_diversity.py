"""Regression tests for the homotopy-aware diverse-route selection.

These guard the fix for Parcours 1 where the route-choice analysis returned
near-identical alternatives (legs 0-1, 6-7, 7-8) and missed genuinely-distinct
ones (the south route on 7-8).  The selection now keeps a second/third route
only when it is a different *decision*:

  • the loop the two routes form must enclose a building-sized connected
    impassable component (different homotopy class), AND
  • their buffered corridors must overlap little.

The production cost-grid raster is not shipped with the repo, so these tests use
small synthetic grids that isolate each behaviour (no scipy-free fallback: the
selection logic only runs on the scipy path, which CI has).
"""

import numpy as np
import pytest

import pathfinding as pf

pytestmark = pytest.mark.skipif(
    not pf._HAS_SCIPY, reason="diverse selection requires scipy"
)


def _open_grid(n: int = 44) -> np.ndarray:
    return np.full((n, n), 1.0, dtype=np.float64)


def _block(grid: np.ndarray, r0: int, r1: int, c0: int, c1: int) -> None:
    """Mark a solid impassable rectangle [r0:r1, c0:c1] (inclusive)."""
    grid[r0 : r1 + 1, c0 : c1 + 1] = np.inf


# ── A big obstacle creates two genuine ways around ───────────────────────────

def test_big_block_yields_two_opposite_routes():
    """A building-sized block between start and end gives two distinct choices
    (above vs below), and they are not near-duplicates of each other."""
    grid = _open_grid(44)
    # ~25x7 = 175-cell block, comfortably above the 125-cell fallback threshold.
    _block(grid, 9, 33, 18, 24)
    routes = pf.find_diverse_routes(
        grid, start=(21, 3), end=(21, 40), k=3, timeout=20.0,
    )
    assert len(routes) >= 2, "a big block must offer at least two ways around"
    # The two cheapest must pass on opposite sides of the block (one above row 9,
    # one below row 33).
    tops = [min(r for r, _c in rt) for rt in routes]
    bots = [max(r for r, _c in rt) for rt in routes]
    assert min(tops) < 9 and max(bots) > 33, "routes do not straddle the block"
    # And they must not be near-duplicates.
    tol = pf._dup_tol_cells(grid.shape)
    d0 = pf._dense_cells_from_path(routes[0])
    d1 = pf._dense_cells_from_path(routes[1])
    assert pf._corridor_overlap(d0, d1, grid.shape, tol) <= pf._DIVERSE_SHARE_MAX


# ── An open field has no genuinely-distinct alternative ──────────────────────

def test_open_field_returns_single_route():
    """With no obstacle to disagree about, parallel detours are near-duplicates
    and must collapse to a single route (no fake choices)."""
    grid = _open_grid(40)
    routes = pf.find_diverse_routes(
        grid, start=(20, 3), end=(20, 36), k=3, timeout=20.0,
    )
    assert len(routes) == 1, f"open field must yield 1 route, got {len(routes)}"


# ── A sub-threshold obstacle does not justify a second route ─────────────────

def test_small_obstacle_below_area_threshold_does_not_split():
    """A tall-thin 20x5 = 100-cell block is corridor-distinct (the two ways
    around are ~20 cells apart) yet below the 125-cell fallback area threshold,
    so it is not a distinct-enough decision → a single route."""
    grid = _open_grid(44)
    _block(grid, 11, 30, 18, 22)  # 20 rows x 5 cols = 100 cells
    routes = pf.find_diverse_routes(
        grid, start=(20, 3), end=(20, 40), k=3, timeout=20.0,
    )
    assert len(routes) == 1


# ── The distinctness threshold is resolution-aware (m² → cells) ──────────────

def test_threshold_is_resolution_aware():
    """The SAME 100-cell block becomes a real decision at a coarse resolution,
    where 100 cells exceed the m²-derived minimum (200 m² ÷ 25 m²/cell = 8
    cells).  Only the area threshold changes between this test and the previous
    one — isolating its resolution-awareness."""
    grid = _open_grid(44)
    _block(grid, 11, 30, 18, 22)  # 100 cells
    routes = pf.find_diverse_routes(
        grid, start=(20, 3), end=(20, 40), k=3, timeout=20.0,
        meters_per_cell=5.0,  # min_cc = 200 / 25 = 8 cells < 100
    )
    assert len(routes) >= 2


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
