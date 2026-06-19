"""Regression tests for the control-isolation fix.

Two layers guard against the false "balise isolée" verdict that occurred on
Parcours 1, leg 3-4 of the rzeszow-3000 map:

  • Layer 3 — grid resolution targeting (traversability._compute_factor):
    the downsample factor must hit a real-world resolution fine enough
    (≤ ~1.3 m/cell) to keep ~2 m sprint passages 8-connected, instead of the
    old fixed grid-size cap that DEGRADED resolution as the map grew.

  • Layer 1 — component-aware re-anchor (pathfinding.find_diverse_routes):
    when a control's snapped cell lands in a tiny quantisation pocket a few
    cells from the real network, the routing endpoint is moved onto the main
    component within a bounded radius — but never further, and never by
    drawing a connector across a genuine seal.

The real cost-grid raster is not shipped with the repo, so these tests use
synthetic grids that isolate each behaviour.
"""

import numpy as np
import pytest

import pathfinding as pf
import traversability as tv


# ── Layer 3: resolution targeting ────────────────────────────────────────────

def test_factor_targets_fine_resolution_for_sprint_map():
    """A 1:3000 sprint map must be quantised at ≤ ~1.3 m/cell (factor 5 here)."""
    # rzeszow-3000 source raster (300 DPI): 6174 x 10292 px, scale 1:3000.
    factor = tv._compute_factor(6174, 10292, map_scale=3000)
    mpp = tv._metres_per_pixel(3000)
    metres_per_cell = mpp * factor
    assert factor == 5
    assert metres_per_cell <= 1.3, metres_per_cell


def test_factor_never_finer_than_floor():
    """The factor never drops below the safety floor, even at large scales."""
    factor = tv._compute_factor(6174, 10292, map_scale=500)
    assert factor >= tv._DOWNSAMPLE_BASE


def test_factor_falls_back_when_scale_unknown():
    """Unknown scale → fall back to the base factor (still cell-cap bounded)."""
    # Use a small raster so the cell cap does not coarsen past the base factor.
    factor = tv._compute_factor(2000, 3000, map_scale=None)
    assert factor == tv._DOWNSAMPLE_BASE


def test_factor_coarsens_when_grid_would_be_huge():
    """A pathological huge map coarsens past target to respect the cell cap."""
    factor = tv._compute_factor(60000, 80000, map_scale=3000)
    assert (60000 // factor) * (80000 // factor) <= tv._MAX_GRID_CELLS


# ── Layer 1: component-aware re-anchor ───────────────────────────────────────

def _grid_with_pocket():
    """20x20 passable grid with a 1-cell INF wall sealing a 1-cell pocket.

    Column 2 is an impassable wall; the pocket is cell (1, 1).  The main
    network is everything at column >= 3.  The pocket's nearest main cell is
    (1, 3), Euclidean distance 2 cells (across the wall).
    """
    grid = np.full((20, 20), 1.0, dtype=np.float64)
    grid[:, 2] = tv.INF  # vertical wall
    return grid


def test_enclosed_control_without_reanchor_returns_no_route():
    """With re-anchor OFF, a sealed pocket yields no route (honest failure)."""
    grid = _grid_with_pocket()
    routes = pf.find_diverse_routes(
        grid, start=(1, 1), end=(10, 10), k=1, reanchor_radius_cells=0
    )
    assert routes == []


def test_reanchor_rescues_pocket_within_radius():
    """A sufficient radius moves the endpoint onto the network and routes."""
    grid = _grid_with_pocket()
    routes = pf.find_diverse_routes(
        grid, start=(1, 1), end=(10, 10), k=1, reanchor_radius_cells=3
    )
    assert len(routes) == 1
    # The route must live on the main component (column >= 3); it must NOT
    # contain the walled-off pocket cell, i.e. no connector across the seal.
    cells = routes[0]
    assert all(c >= 3 for (_r, c) in cells)


def test_reanchor_radius_too_small_stays_isolated():
    """If the network is beyond the radius, we still honestly report failure."""
    grid = _grid_with_pocket()
    routes = pf.find_diverse_routes(
        grid, start=(1, 1), end=(10, 10), k=1, reanchor_radius_cells=1
    )
    assert routes == []


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
