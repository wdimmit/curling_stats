import math

import pytest

from curling_score.geometry.sideview import SideView
from curling_score.train import boxedit, segserve

VIEW = SideView(rect=(0, 0, 810, 1080), tee_row=435.0, hog_row=520.0)
GEOM = boxedit.frame_geometry(VIEW, 52.0)


def box_of(width, aspect, bottom_row):
    """A box whose inclusive extent is exactly `width` px wide.

    `score` measures `x1 - x0 + 1`, the pixel-inclusive convention a mask's
    bbox has, so a helper that spans `width` between its edges would be one
    pixel wider than it claims and no assertion about exact ratios would hold.
    """
    h = width * aspect
    return (400.0, bottom_row - h + 1, 400.0 + width - 1, bottom_row)


class TestScore:
    """Geometry's job here is to CHOOSE among SAM's readings of one click, not
    to filter colour blobs. SAM returns the stone, the stone plus its shadow,
    the stone plus the hand on it; `stone_width_at` knows which is stone-sized.
    """

    def test_a_stone_sized_mask_scores_best(self):
        at_hog = VIEW.stone_width_at(520.0, 52.0)
        exact = segserve.score(box_of(at_hog, 0.42, 520.0), GEOM)[0]
        wide = segserve.score(box_of(at_hog * 1.8, 0.42, 520.0), GEOM)[0]
        assert exact < wide

    def test_the_whole_sheet_is_rejected(self):
        assert segserve.score((0, 300, 809, 1079), GEOM)[0] == math.inf

    def test_a_tall_thin_mask_is_rejected(self):
        at_hog = VIEW.stone_width_at(520.0, 52.0)
        assert segserve.score(box_of(at_hog, 3.0, 520.0), GEOM)[0] == math.inf

    def test_half_size_and_double_size_are_equally_wrong(self):
        """Scored on the log of the ratio. On a plain ratio everything too
        small crowds into (0, 1) while everything too large has the rest of
        the line, which quietly makes the chooser prefer masks too small."""
        at_hog = VIEW.stone_width_at(520.0, 52.0)
        half = segserve.score(box_of(at_hog * 0.5, 0.42, 520.0), GEOM)[0]
        double = segserve.score(box_of(at_hog * 2.0, 0.42, 520.0), GEOM)[0]
        assert half == pytest.approx(double, rel=1e-6)

    def test_the_same_stone_scores_well_at_any_row(self):
        """A stone further down the sheet is wider in pixels and must not be
        penalised for it -- that is the whole point of using the solve."""
        for row in (450.0, 500.0, 560.0, 620.0):
            w = VIEW.stone_width_at(row, 52.0)
            s, ratio, _a = segserve.score(box_of(w, 0.42, row), GEOM)
            assert ratio == pytest.approx(1.0, abs=0.05), row
            assert s < 0.06, row

    def test_a_row_off_the_top_of_the_map_is_refused_not_divided_by_zero(self):
        assert segserve.score((0, 0, 40, 2), GEOM)[0] == math.inf


class TestHandler:
    def test_a_bad_request_does_not_kill_the_server(self):
        """A click that raises must come back as JSON, not a dead socket --
        the person is mid-session with unsaved work in the browser."""
        class Boom:
            def box_at(self, *a, **k):
                raise RuntimeError("gpu fell over")

        handler = segserve.make_handler(".", Boom(), {})
        assert handler is not None       # constructed without touching CUDA
