"""The composite's two wide side views: where they are, and where the far
end's paint sits in them.

Each side camera watches the *other* end's house and hog line -- its own hog
line is about half a metre beneath it, out of frame. So the throwing end's
crossing is seen by the camera at the target end.
"""

import pytest

from curling_score.geometry import constants as C
from curling_score.geometry import layout, sideview
from tests import synth


def a_layout(x=810, w=297):
    """A panel layout the shape the club's composite actually produces."""
    return layout.PanelLayout(top=(x, 10, w, 514), bottom=(x, 554, w, 516))


class TestLocate:
    def test_the_views_are_what_the_overhead_strip_leaves_behind(self):
        got = sideview.locate(a_layout(), width=1920, height=1080)
        assert got["left"] == (0, 0, 810, 1080)
        assert got["right"] == (1107, 0, 813, 1080)

    def test_it_follows_the_strip_rather_than_assuming_where_it_sits(self):
        got = sideview.locate(a_layout(x=700, w=300), width=1920, height=1080)
        assert got["left"] == (0, 0, 700, 1080)
        assert got["right"] == (1000, 0, 920, 1080)

    def test_a_strip_touching_an_edge_leaves_no_view_that_side(self):
        with pytest.raises(sideview.SideViewError):
            sideview.locate(a_layout(x=0, w=297), width=1920, height=1080)


class TestSolve:
    def test_it_recovers_the_rows_it_was_drawn_with(self):
        plate = synth.side_view(tee_row=430.0, hog_row=520.0)
        got = sideview.solve(plate, (0, 0, 810, 1080))
        assert got.tee_row == pytest.approx(430.0, abs=1.5)
        assert got.hog_row == pytest.approx(520.0, abs=1.5)
        assert got.rows_tee_to_hog == pytest.approx(90.0, abs=2.0)

    def test_it_works_at_the_other_framings_the_club_actually_uses(self):
        for tee, hog in ((452.0, 533.0), (466.0, 552.0), (468.0, 557.0)):
            got = sideview.solve(synth.side_view(tee_row=tee, hog_row=hog),
                                 (0, 0, 810, 1080))
            assert got.tee_row == pytest.approx(tee, abs=1.5), (tee, hog)
            assert got.hog_row == pytest.approx(hog, abs=1.5), (tee, hog)

    def test_the_tee_is_fitted_not_taken_as_the_ring_s_centroid(self):
        """Perspective magnifies the annulus's near half, so its centroid sits
        about 2 px toward the camera. Fitting the outer painted edges does not."""
        plate = synth.side_view(tee_row=430.0, hog_row=520.0)
        got = sideview.solve(plate, (0, 0, 810, 1080))
        green = (plate[:, :, 1].astype(float)
                 - (plate[:, :, 0].astype(float) + plate[:, :, 2]) / 2)
        prof = green[:, 300:560].mean(axis=1)
        weights = prof.clip(min=0)
        centroid = (weights * range(len(weights))).sum() / weights.sum()
        assert abs(got.tee_row - 430.0) < abs(centroid - 430.0)

    def test_the_map_puts_the_hog_line_where_the_rules_say(self):
        got = sideview.solve(synth.side_view(), (0, 0, 810, 1080))
        assert got.row_for(0.0) == pytest.approx(got.tee_row, abs=0.01)
        assert got.row_for(C.TEE_TO_HOGLINE_M) == pytest.approx(got.hog_row, abs=0.01)

    def test_extra_edges_from_paint_bleed_do_not_mispair(self):
        """A gap cut into the near annulus band -- paint bleed, or a sweeper's
        shadow, as seen on one of the club's ten real views -- throws six green
        crossings instead of four, with the split *inside* the band itself. That
        is the case the old positional selection (edges[0], edges[1], edges[-2],
        edges[-1]) gets wrong: the split's inner edge lands at edges[1], not the
        true 8-ft boundary. Reconstructing that old selection on this exact
        plate gives tee 428.25 (1.75 off, outside this test's tolerance);
        fitting from the outer pair alone gives 429.65 (0.35 off)."""
        plate = synth.side_view(tee_row=430.0, hog_row=520.0)
        plate[411:415, :] = synth.SIDE_ICE
        got = sideview.solve(plate, (0, 0, 810, 1080))
        assert got.tee_row == pytest.approx(430.0, abs=1.0)
        assert got.hog_row == pytest.approx(520.0, abs=1.5)

    def test_ice_with_no_house_on_it_is_refused(self):
        import numpy as np
        blank = np.full((1080, 810, 3), 238, dtype=np.uint8)
        with pytest.raises(sideview.SideViewError):
            sideview.solve(blank, (0, 0, 810, 1080))
