"""Olive (ISSprOM 520/521) border cells — stability rule (v11, evolution n°33).

Before v11 a cell was blocked as soon as 8 % of its pixels were olive, and the
black line work "enclosed" within 3 px of olive was counted as olive.  At
1:3000 (factor 5, 25 px per cell) a kerb or an outline standing against the
olive could make a cell with a single olive pixel cross the threshold, and
isolated 2-px specks blocked cells of their own (Kasumi v15: ~1 000 such cells).

v11 rule, on PURE olive pixels only (ink never counts):
  - olive ≥ 12 % of the cell                       → blocked
  - olive ≥ 8 % AND an 8-neighbour cell is olive interior (≥ 50 %) → blocked
  - otherwise the cell stays passable (isolated specks, anti-aliasing, ink).
Enclosed line work is still blocked through the primary ≥ 40 % rule, so a
building outline standing inside olive never opens a free island.
"""

import io

import numpy as np
from PIL import Image

import traversability as tv

OLIVE_520 = (158, 186, 29)
BUILDING_60 = (102, 102, 102)
BLACK = (0, 0, 0)
WHITE = (255, 255, 255)

SCALE = 3000          # 300 DPI → factor 5 (1.27 m / cell, 25 px per cell)
FACTOR = 5


def _png(arr: np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(arr.astype(np.uint8), "RGB").save(buf, format="PNG")
    return buf.getvalue()


def _blank(h: int = 400, w: int = 400, colour=WHITE) -> np.ndarray:
    arr = np.empty((h, w, 3), dtype=np.uint8)
    arr[:] = colour
    return arr


def _grid(arr):
    return tv.build_traversability_mask(_png(arr), SCALE)


def test_enclosed_ink_next_to_olive_does_not_count_as_olive():
    """Street | 4 px wall (ink) | olive.  The street-side cell holding the wall
    (cell column 39 = px 195–199) has NO olive pixel; its last ink column is
    4 px from the street so it is 'enclosed'.  It must stay passable: the wall
    is a barrier edge, not a blocked cell."""
    arr = _blank()
    arr[:, 196:200] = BLACK
    arr[:, 200:] = OLIVE_520
    grid = _grid(arr)
    assert np.isfinite(grid[50, 39]), "ink-only border cell must not be blocked"
    assert np.isinf(grid[50, 40]), "olive interior stays blocked"


def test_two_olive_pixels_next_to_olive_interior_block_the_border_cell():
    """Olive from px 200 with a 2-px bump into cell column 39 at row 250:
    8 % olive (below the 12 % stand-alone threshold), but the neighbour cell is
    olive interior → blocked (hysteresis, same border width as v10)."""
    arr = _blank()
    arr[:, 200:] = OLIVE_520
    arr[250, 198:200] = OLIVE_520
    grid = _grid(arr)
    assert np.isinf(grid[50, 39]), "2 olive px attached to the olive block must block"
    assert np.isfinite(grid[48, 39]), "the same column without the bump stays open"


def test_one_olive_pixel_next_to_olive_interior_does_not_block():
    """A single olive pixel (4 %) is anti-aliasing of the edge, not terrain: the
    street cell keeps its width (v10 behaviour preserved)."""
    arr = _blank()
    arr[:, 200:] = OLIVE_520
    arr[250, 199] = OLIVE_520
    grid = _grid(arr)
    assert np.isfinite(grid[50, 39])


def test_isolated_olive_speck_does_not_block():
    """2 olive px (8 % of a 25-px cell) with no olive neighbour cell: an
    anti-aliasing or rendering speck, not an olive area."""
    arr = _blank()
    arr[250, 198:200] = OLIVE_520
    grid = _grid(arr)
    assert np.isfinite(grid[50, 39])


def test_three_olive_pixels_block_even_without_interior_neighbour():
    """≥ 12 % (3 px of 25) blocks on its own: a thin olive strip (fence line of
    a private garden) is olive terrain whatever its surroundings."""
    arr = _blank()
    arr[250, 197:200] = OLIVE_520
    grid = _grid(arr)
    assert np.isinf(grid[50, 39])


def test_building_outline_inside_olive_stays_sealed():
    """Regression guard: a building (grey fill + 2 px ink outline) standing in an
    olive block must not leave a free island of outline cells."""
    arr = _blank(colour=OLIVE_520)
    arr[100:300, 100:300] = BLACK          # outline
    arr[102:298, 102:298] = BUILDING_60    # fill
    grid = _grid(arr)
    assert np.isinf(grid).all()
