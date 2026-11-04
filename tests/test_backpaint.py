"""The back line is found in a panel's paint, and a stone is beyond it only
when its box is wholly past the paint's outer edge."""

import numpy as np

from curling_score.geometry import backpaint
from curling_score.geometry.calibrate import PanelCalib

H, W = 534, 300
# A top panel's tee at row 161 and 80 px/m puts paint at row 14 1.84 m behind
# the tee on the centre line; a flipped bottom panel's at row 380 puts paint
# at row 519 1.8 m behind it.
TOP = PanelCalib(center_px=(150.0, 161.0), px_per_m=80.0, edge_erosion_px=0.0,
                 residual_m=0.0, flipped=False)
BOTTOM = PanelCalib(center_px=(150.0, 380.0), px_per_m=75.0, edge_erosion_px=0.0,
                    residual_m=0.0, flipped=True)
PAINT = (45, 55, 40)
GREEN = (30, 110, 25)
BOW = 0.00028


def outer(col, flipped=False, at=14.0):
    """The paint's outer edge the way these lenses bow it: nearest the back
    edge on the centre line, further in toward the corners."""
    d = at + BOW * (col - W / 2) ** 2
    return H - 1 - d if flipped else d


def plate(flipped=False, at=14.0, thick=2, cols=range(W), ring=True):
    """Ice with a back line painted ``thick`` rows from its outer edge toward
    the house, the 12-foot ring's green touching it from the house side."""
    img = np.full((H, W, 3), 225, np.uint8)
    for c in cols:
        o = int(round(outer(c, flipped, at)))
        rows = range(o - thick + 1, o + 1) if flipped else range(o, o + thick)
        for r in rows:
            img[r, c] = PAINT
        if ring and abs(c - W / 2) < 60:
            green = range(o - thick - 20, o - thick + 1) if flipped else range(o + thick, o + thick + 20)
            for r in green:
                img[r, c] = GREEN
    return img


def test_finds_the_outer_edge_of_a_top_panel_s_paint():
    line, why = backpaint.find_back_line(plate(), TOP)
    assert why is None
    for c in (0, 75, 150, 225, 299):
        assert abs(line.outer_row(c) - outer(c)) < 0.6
    assert not line.flipped and line.height == H


def test_finds_it_on_a_flipped_panel_near_the_last_row():
    line, why = backpaint.find_back_line(plate(flipped=True), BOTTOM)
    assert why is None
    for c in (0, 150, 299):
        assert abs(line.outer_row(c) - outer(c, flipped=True)) < 0.6


def test_the_centre_line_and_a_parked_stone_do_not_pull_it():
    img = plate()
    img[:60, 149:151] = (20, 20, 20)            # the centre line, from the back edge in
    img[0:12, 40:70] = (60, 60, 170)            # a red stone parked behind the line
    line, why = backpaint.find_back_line(img, TOP)
    assert why is None
    assert abs(line.outer_row(55) - outer(55)) < 0.6


def test_bare_ice_has_no_line_and_says_why():
    line, why = backpaint.find_back_line(np.full((H, W, 3), 225, np.uint8), TOP)
    assert line is None and "columns" in why


def test_paint_too_far_into_the_house_is_not_the_back_line():
    """Dark paint 1.4 m from the tee on the centre line is not the back line."""
    line, why = backpaint.find_back_line(plate(at=50.0), TOP)
    assert line is None and "outside" in why


def _line(flipped=False):
    line, _ = backpaint.find_back_line(plate(flipped=flipped),
                                       BOTTOM if flipped else TOP)
    return line


def test_a_box_wholly_past_the_paint_is_beyond():
    # Paint at row 14 on the centre line; a 16 px box centred at row 4 is
    # clipped by the edge to rows 0-8.
    assert _line().beyond(150.0, 4.0, 16.0 * 16.0)


def test_a_box_touching_the_paint_is_not():
    # Rows 0-16 reach past the paint's outer edge at 14: the stone touches it.
    assert not _line().beyond(150.0, 8.0, 16.0 * 16.0)


def test_a_stone_on_the_house_side_is_not():
    assert not _line().beyond(150.0, 60.0, 400.0)


def test_a_box_clipped_by_the_edge_reaches_twice_its_centre():
    """11/03 sheet 4 at 7920 s: a broom head at the corner of the top panel,
    centred 6.8 px from the edge with a box of 315 px. Its near edge is at
    13.6, not 6.8 + 8.9: the box runs from the edge."""
    line = _line()
    assert line.beyond(24.0, 6.8, 315.0)
    assert not line.beyond(24.0, 6.8 + 6.0, 315.0)


def test_on_a_flipped_panel_beyond_is_toward_the_last_row():
    line = _line(flipped=True)
    assert line.beyond(150.0, H - 1 - 4.0, 256.0)
    assert not line.beyond(150.0, H - 1 - 8.0, 256.0)
    assert not line.beyond(150.0, 300.0, 400.0)


def test_the_timeline_record():
    rec = _line().to_json()
    assert set(rec) == {"outer_edge_row_coef", "columns", "scatter_px"}
    assert rec["columns"] >= backpaint.MIN_COLUMNS
