"""The sidelines, found where their paint is in each overhead panel."""

import json
import math
from pathlib import Path

import cv2
import numpy as np
import pytest

from curling_score.geometry import sidepaint
from curling_score.geometry.calibrate import PanelCalib
from tests.synth_hogline import BOTTOM, TOP

NAVY_BGR = (65, 22, 26)
FIXTURES = Path(__file__).parent / "fixtures" / "sidelines"


def left_col(row, h=534, flipped=False):
    """A left sideline the way the club's lenses bow it: in from the frame's
    edge at the far end of the panel, out of the frame toward the house."""
    u = row if flipped else h - 1 - row      # rows from the far edge
    return 50.0 - 0.32 * u + 0.0004 * u * u


def plate(h=534, w=300, flipped=False, sides=("left", "right"), seen=None,
          col=left_col, colour=NAVY_BGR):
    """Ice with navy sidelines, painted on the rows of ``seen`` (by default
    the 170 rows nearest the far edge)."""
    img = np.full((h, w, 3), 225, np.uint8)
    if seen is None:
        seen = range(0, 170) if flipped else range(h - 170, h)
    for row in seen:
        c = col(row, h, flipped)
        for side in sides:
            x = c if side == "left" else w - 1 - c
            for dx in (-1, 0, 1):
                xi = int(round(x)) + dx
                if 0 <= xi < w:
                    img[row, xi] = colour
    return img


class TestFindingThePaint:
    def test_finds_both_lines_on_a_top_panel(self):
        s = sidepaint.find_sidelines(plate(), TOP)
        for row in (380, 450, 533):
            assert s.left.col_at(row) == pytest.approx(left_col(row), abs=0.5)
            assert s.right.col_at(row) == pytest.approx(299 - left_col(row), abs=0.5)
        assert s.left.rows == (364.0, math.inf)
        assert s.missing == ()

    def test_finds_them_on_a_flipped_bottom_panel(self):
        s = sidepaint.find_sidelines(plate(flipped=True), BOTTOM)
        for row in (0, 80, 160):
            assert s.left.col_at(row) == pytest.approx(left_col(row, flipped=True), abs=0.5)
        assert s.left.rows == (-math.inf, 169.0)

    def test_the_inner_of_two_lines_is_this_sheet_s(self):
        """The next sheet's line can show outside this one's (10/19 sheet 1)."""
        img = plate()
        img2 = plate(col=lambda r, h, f: left_col(r, h, f) - 14)
        img[(img2 == NAVY_BGR).all(axis=2)] = NAVY_BGR
        s = sidepaint.find_sidelines(img, TOP)
        assert s.left.col_at(500) == pytest.approx(left_col(500), abs=0.5)

    def test_a_sponsor_s_lettering_in_the_band_does_not_pull_the_line(self):
        img = plate()
        img[430:470, 30:60] = NAVY_BGR
        s = sidepaint.find_sidelines(img, TOP)
        assert s.left.col_at(450) == pytest.approx(left_col(450), abs=0.5)

    def test_a_stray_pixel_on_the_curve_near_the_house_does_not_extend_it(self):
        """10/19 sheet 2's bottom panel: one pixel of navy, far from the paint,
        lay where the line's curve came back into the frame."""
        def col(row, h=534, flipped=False):
            u = h - 1 - row      # out of the frame from u = 200 to 333, then back
            return 50.0 - 0.40 * u + 0.00075 * u * u
        img = plate(col=col)
        row = 83
        assert 0 <= col(row) < 70
        img[row, int(round(col(row)))] = NAVY_BGR
        s = sidepaint.find_sidelines(img, TOP)
        assert s.left.col_at(row) == pytest.approx(col(row), abs=1.0)
        assert s.left.rows[0] == 364.0
        assert not s.left.beyond(col(row) - 5, row)


class TestWhatIsBeyond:
    def test_beyond_is_outward_of_the_line_on_the_rows_it_was_seen(self):
        s = sidepaint.find_sidelines(plate(), TOP)
        c = left_col(500)
        assert s.beyond(c - 3, 500)
        assert not s.beyond(c + 3, 500)
        assert s.beyond(299 - c + 3, 500)
        assert not s.beyond(150.0, 500)

    def test_nothing_is_beyond_a_line_nearer_the_house_than_its_paint(self):
        """Where no paint was seen the line is out of the frame."""
        s = sidepaint.find_sidelines(plate(), TOP)
        assert not s.beyond(0.0, 300)

    def test_the_far_edge_is_covered_past_the_last_painted_row(self):
        s = sidepaint.find_sidelines(plate(flipped=True, seen=range(8, 170)), BOTTOM)
        assert s.beyond(left_col(0, flipped=True) - 5, 0)


class TestRefusingPaintItCannotTrust:
    def test_no_paint_no_lines(self):
        s = sidepaint.find_sidelines(np.full((534, 300, 3), 225, np.uint8), TOP)
        assert s.left is None and s.right is None
        assert len(s.missing) == 2
        assert not s.beyond(0.0, 533)

    def test_too_few_rows(self):
        s = sidepaint.find_sidelines(plate(seen=range(500, 534)), TOP)
        assert s.left is None and s.right is None

    def test_paint_that_stops_short_of_the_far_edge_is_not_a_sideline(self):
        s = sidepaint.find_sidelines(plate(seen=range(300, 420)), TOP)
        assert s.left is None and s.right is None

    def test_one_side_without_paint_leaves_the_other(self):
        s = sidepaint.find_sidelines(plate(sides=("right",)), TOP)
        assert s.left is None and s.right is not None
        assert s.missing == ("left: no line of navy paint runs to the far edge",)


class TestItReadsBgr:
    def test_the_same_colour_in_rgb_order_is_not_paint(self):
        s = sidepaint.find_sidelines(plate(colour=NAVY_BGR[::-1]), TOP)
        assert s.left is None and s.right is None


def test_to_json_round_numbers_and_open_ends():
    j = sidepaint.find_sidelines(plate(), TOP).to_json()
    assert j["left"]["side"] == "left" and j["right"]["side"] == "right"
    assert j["left"]["rows"] == [364.0, None]
    assert json.dumps(j)


# --- the club's own panels -----------------------------------------------------

def _real(name):
    cal = json.loads((FIXTURES / "calibrations.json").read_text())[name]
    calib = PanelCalib(center_px=tuple(cal["center_px"]), px_per_m=cal["px_per_m"],
                       edge_erosion_px=0.0, residual_m=0.0, flipped=cal["flipped"])
    return sidepaint.find_sidelines(cv2.imread(str(FIXTURES / name)), calib)


@pytest.mark.parametrize("name", ["plate-XbBwOnjkhpY-top.png", "plate-XbBwOnjkhpY-bottom.png",
                                  "plate-XnC-moPkja8-bottom.png", "plate-yequ4JYjslE-top.png"])
def test_every_real_panel_has_both_lines(name):
    s = _real(name)
    for line in (s.left, s.right):
        assert line is not None
        assert line.scatter_px < 1.0
        assert line.support >= 90


@pytest.mark.parametrize("x_px,y_px", [(26, 511), (10, 521), (21, 523)])
def test_the_rock_on_sheet_3_is_beyond_sheet_4_s_line(x_px, y_px):
    """The rock that held the 10/19 changeover open, at 6830, 6835 and 6860 s."""
    assert _real("plate-XbBwOnjkhpY-top.png").beyond(x_px, y_px)


@pytest.mark.parametrize("x_px,y_px", [
    (158, 366), (102, 316), (110, 154), (93, 192), (39, 378),
    (133, 210), (159, 171), (93, 52), (134, 171)])
def test_the_stones_in_play_at_the_end_of_that_game_are_all_on_the_sheet(x_px, y_px):
    """End 7, bottom house, 6560 s: nine stones, the nearest the edge at x = 1.43."""
    assert not _real("plate-XbBwOnjkhpY-bottom.png").beyond(x_px, y_px)


def test_a_stone_at_the_edge_level_with_the_house_is_on_the_sheet():
    """Red at x = -1.68, level with the top house, through end 6 (5500 s)."""
    assert not _real("plate-XbBwOnjkhpY-top.png").beyond(20, 300)


def test_the_curve_that_comes_back_in_near_the_house_excludes_nothing_there():
    s = _real("plate-XnC-moPkja8-bottom.png")
    assert s.right.rows[1] < 200
    assert not s.beyond(240, 480)
