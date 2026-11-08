"""ISSprOM 2019 print colours (v10).

A map exported with the official ISSprOM colour table (CMYK → RGB values of the
OpenOrienteering / OCAD symbol sets) must be classified like a map drawn with the
app's historical "OCAD export" palette:

- olive 520, water 301, impassable vegetation 410 → impassable
- ISSprOM 2019-2 411 uncrossable vegetation (dark green) → impassable (v12)
- walk / slow-run vegetation, crossable water → slow
- open land 401/403, canopy, brown contour lines → passable (cost 1.0)
- paved 501 (light and heavy traffic) → fast (0.8)

Black line work never blocks a cell (only impassable AREAS do); it is handled
by the barrier-edge model, where only lines ≥ 3 px ≈ 0.25 mm are barriers:
impassable wall 515 / fence 518 / cliff 201 (0.40–0.50 mm) and the 1 m (3 px)
obstacles engraved on the WMOC working maps.  Edges of paved areas (501.1,
0.10 mm) and building outlines (521.4, 0.14 mm) are ≤ 2 px and never cut a
route; dashed symbols (506) are ignored; the impassable hedge 410.1 (green
100 %, 0.40 mm) is a barrier.  Line work enclosed in impassable areas stays
blocked so that no free islands appear.
"""

import io

import numpy as np
import pytest
from PIL import Image

import traversability as tv

# ── ISSprOM 2019 colours (OpenOrienteering "ISSprOM 2019_4000.omap") ──────────
OLIVE_520 = (158, 186, 29)
BLUE_100 = (0, 255, 255)
BLUE_70 = (76, 255, 255)
BLUE_30 = (178, 255, 255)
GREEN_100 = (61, 255, 23)
DARK_GREEN_411 = (0, 178, 36)        # IOF 2022: CMYK 100/0/80/30
DARK_GREEN_SATSU = (0, 176, 32)      # as read on a 2026 Livelox map image
DARK_GREEN_NOICC = (0, 178, 0)       # CMYK PDF rasterised without ICC
DARK_GREEN_ICC = (0, 127, 83)        # CMYK PDF rasterised with ICC
DARK_GREEN_RZESZOW = (0, 145, 25)    # WMOC 2026 Rzeszów final, Livelox image
GREEN_60 = (139, 255, 116)
GREEN_30 = (197, 255, 185)
YELLOW_100 = (255, 186, 54)
YELLOW_50 = (255, 221, 154)
PAVED_LIGHT = (241, 206, 178)
PAVED_HEAVY = (232, 173, 128)
BUILDING_60 = (102, 102, 102)
CANOPY_20 = (204, 204, 204)
BROWN_100 = (209, 92, 0)
BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
# historical app palette (must keep working)
APP_OLIVE = (168, 160, 48)
APP_WATER = (90, 160, 225)

SCALE = 4000          # 300 DPI → factor 4 (1.35 m / cell)
FACTOR = 4


def _png(arr: np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(arr.astype(np.uint8), "RGB").save(buf, format="PNG")
    return buf.getvalue()


def _blank(h: int = 400, w: int = 400, colour=WHITE) -> np.ndarray:
    arr = np.empty((h, w, 3), dtype=np.uint8)
    arr[:] = colour
    return arr


def _classify(colour) -> float:
    arr = np.array([[colour]], dtype=np.float32)
    return float(tv._classify_pixels(arr)[0, 0])


# ── pixel classification ──────────────────────────────────────────────────────

@pytest.mark.parametrize("colour", [OLIVE_520, BLUE_100, GREEN_100, BUILDING_60, APP_OLIVE, APP_WATER,
                                    DARK_GREEN_411, DARK_GREEN_SATSU, DARK_GREEN_NOICC, DARK_GREEN_ICC, DARK_GREEN_RZESZOW,
                                    (128, 128, 128), (120, 120, 120)])
def test_issprom_impassable_colours(colour):
    assert np.isinf(_classify(colour)), colour


@pytest.mark.parametrize("colour", [GREEN_60, BLUE_70])
def test_issprom_walk_colours_cost_2_5(colour):
    assert _classify(colour) == pytest.approx(2.5), colour


@pytest.mark.parametrize("colour", [GREEN_30, BLUE_30])
def test_issprom_slow_colours_cost_1_5(colour):
    assert _classify(colour) == pytest.approx(1.5), colour


@pytest.mark.parametrize("colour", [YELLOW_100, YELLOW_50, BROWN_100, CANOPY_20, WHITE])
def test_issprom_passable_colours_cost_1(colour):
    assert _classify(colour) == pytest.approx(1.0), colour


@pytest.mark.parametrize("colour", [PAVED_LIGHT, PAVED_HEAVY])
def test_issprom_paved_colours_cost_0_8(colour):
    assert _classify(colour) == pytest.approx(0.8), colour


# ── cell rules ────────────────────────────────────────────────────────────────

def test_issprom_olive_border_cells_block_at_8_percent():
    """A cell with ~25 % ISSprOM olive (below the 40 % primary rule) is blocked
    by the olive rule, exactly like app-palette olive."""
    arr = _blank()
    arr[:, 201:] = OLIVE_520          # olive starts 1 px into the cell column 50
    grid = tv.build_traversability_mask(_png(arr), SCALE)
    assert np.isinf(grid[50, 50])      # border cell: 3 of 4 px columns olive → blocked
    assert np.isinf(grid[50, 60])      # interior
    assert np.isfinite(grid[50, 40])   # far side stays open


def test_brown_contour_lines_do_not_block_cells():
    arr = _blank()
    for y in range(10, 400, 20):
        arr[y:y + 3, :] = BROWN_100   # 0.21–0.25 mm contour lines
    grid = tv.build_traversability_mask(_png(arr), SCALE)
    assert np.isfinite(grid).all()
    assert float(grid[5:95, 5:95].max()) <= 1.05


# ── barrier edges ─────────────────────────────────────────────────────────────

def _vertical_line_edges(colour, width_px: int):
    """Vertical line of `width_px` centred on the cell boundary x = 200."""
    arr = _blank()
    x0 = 200 - width_px // 2
    arr[:, x0:x0 + width_px] = colour
    gh, gw = 400 // FACTOR, 400 // FACTOR
    edges = tv.build_barrier_edges(arr, (gh, gw), FACTOR)
    return edges["E"][:, 49]           # wall between cell column 49 and 50


@pytest.mark.parametrize("width_px", [1, 2])
def test_thin_black_line_is_not_a_barrier(width_px):
    """≤ 2 px: 0.10–0.14 mm ink (paved-area edge, building outline) must not
    cut edges."""
    assert not _vertical_line_edges(BLACK, width_px).any()


@pytest.mark.parametrize("width_px", [3, 5, 6])
def test_thick_black_line_is_a_barrier(width_px):
    """≥ 3 px: impassable wall 515 / fence 518 (0.40 mm), cliff 201 (0.50 mm),
    and the 1 m obstacles engraved on the WMOC working maps."""
    assert _vertical_line_edges(BLACK, width_px).all()


def test_dashed_footpath_is_not_a_barrier():
    """506 small footpath: 0.27 mm black DASHED (1.5 mm dash, 0.37 mm gap).
    Dashes are short ink components (< 2 mm): never a barrier."""
    arr = _blank()
    for y0 in range(0, 400, 22):       # 18 px dash, 4 px gap
        arr[y0:y0 + 18, 199:202] = BLACK
    gh, gw = 400 // FACTOR, 400 // FACTOR
    edges = tv.build_barrier_edges(arr, (gh, gw), FACTOR)
    assert not edges["E"][:, 49].any()


def test_green_hedge_line_is_a_barrier():
    """410.1 impassable vegetation drawn as a 0.40 mm green 100 % line."""
    assert _vertical_line_edges(GREEN_100, 5).all()


@pytest.mark.parametrize("colour", [DARK_GREEN_411, DARK_GREEN_SATSU, DARK_GREEN_NOICC, DARK_GREEN_ICC, DARK_GREEN_RZESZOW])
def test_dark_green_hedge_line_is_a_barrier(colour):
    """ISSprOM 2019-2 411 uncrossable vegetation drawn as a 0.40 mm dark green line."""
    assert _vertical_line_edges(colour, 5).all()


@pytest.mark.parametrize("colour", [(26, 170, 59), (40, 170, 15), (0, 112, 112), GREEN_60, BLUE_70])
def test_other_greens_are_not_dark_green(colour):
    """Event-logo green, green/black half-tone, water/bank half-tone, 408, 302."""
    r, g, b = (np.array([c]) for c in colour)
    assert not tv._dark_green_mask(r, g, b).any()


def test_water_bank_halftone_is_not_dark_green():
    """Cyan water 301 anti-aliased with its black bank line (≈ 0/112/112) is not 411."""
    assert _classify((0, 112, 112)) == pytest.approx(1.0)   # same as v11
    arr = _blank()
    arr[:, 200:205] = (0, 112, 112)
    gh, gw = 400 // FACTOR, 400 // FACTOR
    assert not tv.build_barrier_edges(arr, (gh, gw), FACTOR)["E"].any()


@pytest.mark.parametrize("ground", [WHITE, YELLOW_100])
def test_dark_green_hedge_with_antialiased_fringe_stays_closed(ground):
    """5 px dark green hedge with a 1 px half-tone fringe on both sides."""
    arr = _blank(colour=ground)
    fringe = tuple(int(0.5 * a + 0.5 * b) for a, b in zip(DARK_GREEN_411, ground))
    arr[:, 199] = fringe
    arr[:, 200:205] = DARK_GREEN_411
    arr[:, 205] = fringe
    gh, gw = 400 // FACTOR, 400 // FACTOR
    grid = tv.build_traversability_mask(_png(arr), SCALE)
    edges = tv.build_barrier_edges(arr, (gh, gw), FACTOR)
    closed = np.isinf(grid[:, 49:52]).any(axis=1) | edges["E"][:, 49:52].any(axis=1)
    assert closed[2:-2].all()


def test_green_black_antialiasing_next_to_thin_black_line_is_not_a_dark_green_hedge():
    """A 2 px black kerb with a 1 px green-100 % / black half-tone (r ≈ 40) next
    to it must stay a non-barrier: the 411 mask requires r ≤ 30."""
    arr = _blank()
    arr[:, 200:202] = BLACK
    arr[:, 202] = (40, 170, 15)
    gh, gw = 400 // FACTOR, 400 // FACTOR
    edges = tv.build_barrier_edges(arr, (gh, gw), FACTOR)
    assert not edges["E"].any()


# ── thin impassable structures block edges, not cells ─────────────────────────

def test_thin_black_line_does_not_block_cells():
    """A 3 px (≈ 0.25 mm, or 1 m engraved) black line is a barrier EDGE, never
    a blocked cell: the cells it crosses stay passable so that a street or an
    alley along a wall keeps its width."""
    arr = _blank()
    arr[:, 200:203] = BLACK            # 3 px inside cell column 50
    grid = tv.build_traversability_mask(_png(arr), SCALE)
    assert np.isfinite(grid[10:90, 48:53]).all()


def test_building_outline_with_antialiasing_does_not_block_outside_cells():
    """ISSprOM building: black 60 % fill, 0.14 mm outline (2 px) plus a 1 px
    grey anti-aliasing fringe.  The fill blocks; the outline + fringe (3 px of
    dark ink) must not push the blocked area one cell outwards."""
    arr = _blank()
    arr[100:300, 100:200] = BUILDING_60
    arr[98:100, 98:202] = BLACK; arr[300:302, 98:202] = BLACK      # outline
    arr[98:302, 98:100] = BLACK; arr[98:302, 200:202] = BLACK
    arr[97, 97:203] = (101, 101, 101); arr[302, 97:203] = (101, 101, 101)   # fringe
    arr[97:303, 97] = (101, 101, 101); arr[97:303, 202] = (101, 101, 101)
    grid = tv.build_traversability_mask(_png(arr), SCALE)
    assert np.isinf(grid[30:70, 30:45]).all()      # fill
    assert np.isfinite(grid[30:70, 24]).all()      # cell x 96–99: fringe + outline only
    assert np.isfinite(grid[30:70, 50]).all()      # cell x 200–203: outline + fringe only


def test_outlined_building_inside_olive_leaves_no_free_island():
    """Inside an olive block, the anti-aliased outline ring of a building (up to
    5 px of dark ink and grey fringe) must not produce passable island cells:
    the ring is the edge of the olive, not a passage."""
    arr = _blank(colour=OLIVE_520)
    arr[100:300, 100:200] = BUILDING_60
    arr[97:100, 97:203] = BLACK; arr[300:303, 97:203] = BLACK      # 3 px outline
    arr[97:303, 97:100] = BLACK; arr[97:303, 200:203] = BLACK
    arr[95:97, 95:205] = (101, 101, 101); arr[303:305, 95:205] = (101, 101, 101)   # 2 px fringe
    arr[95:305, 95:97] = (101, 101, 101); arr[95:305, 203:205] = (101, 101, 101)
    grid = tv.build_traversability_mask(_png(arr), SCALE)
    assert np.isinf(grid).all()


def test_index_contour_through_olive_leaves_no_free_island():
    """A 0.30 mm (4 px) brown index contour crossing an olive block must not
    open a line of passable cells through the olive."""
    arr = _blank(colour=OLIVE_520)
    arr[200:204, :] = BROWN_100
    grid = tv.build_traversability_mask(_png(arr), SCALE)
    assert np.isinf(grid).all()


def test_kerb_along_olive_does_not_block_the_street_cell():
    """A 1 px kerb (501.1) on the street side of an olive edge: the street
    cell holding the kerb stays passable (olive < 8 % there)."""
    arr = _blank(colour=PAVED_LIGHT)
    arr[:, 220:] = OLIVE_520           # olive from cell column 55
    arr[:, 218] = BLACK                # kerb inside cell column 54 (x 216–219), 2 px from the olive
    grid = tv.build_traversability_mask(_png(arr), SCALE)
    assert np.isfinite(grid[10:90, 54]).all()
    assert np.isinf(grid[10:90, 55]).all()


def test_outline_ring_between_two_buildings_is_not_a_passage():
    """Two outlined buildings sharing a wall: the 5 px of outline + fringe
    between the fills is enclosed line work, never a corridor of free cells."""
    arr = _blank()
    arr[100:300, 100:200] = BUILDING_60
    arr[100:300, 205:300] = BUILDING_60
    arr[100:300, 198:202] = BLACK                 # shared outline, 4 px
    arr[100:300, 197] = (101, 101, 101); arr[100:300, 202:205] = (101, 101, 101)
    grid = tv.build_traversability_mask(_png(arr), SCALE)
    assert np.isinf(grid[30:70, 25:75]).all()
    # same with 6 px of pure ink between the fills (two overlapping outlines)
    arr = _blank()
    arr[100:300, 100:200] = BUILDING_60
    arr[100:300, 206:300] = BUILDING_60
    arr[100:300, 200:206] = BLACK
    grid = tv.build_traversability_mask(_png(arr), SCALE)
    assert np.isinf(grid[30:70, 25:75]).all()


def test_thick_impassable_area_still_blocks_cells():
    arr = _blank()
    arr[:, 196:204] = BLACK            # 8 px wall (2 cells)
    grid = tv.build_traversability_mask(_png(arr), SCALE)
    assert np.isinf(grid[10:90, 49:51]).all()


def test_cache_version_bumped_past_v10():
    """v10 introduced the ISSprOM print palette; v11 changed the olive cell rule
    (tests/test_olive_border.py); v12 reads ISSprOM 2019-2 dark green (411).
    Any later change to a threshold must bump it."""
    assert tv.TRAVERSABILITY_VERSION == "v12"
    assert tv._CACHE_FILENAME == "traversability_v12.npz"
