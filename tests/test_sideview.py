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
from tests.conftest import VALIDATION_VIDS


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


@pytest.mark.slow
class TestEveryRealView:
    """Both side views of all five sheets, on plates thinner than production's."""

    @pytest.mark.parametrize("sheet,vid", sorted(VALIDATION_VIDS.items()))
    def test_both_views_calibrate(self, side_plate, sheet, vid):
        plate = side_plate(vid)
        h, w = plate.shape[:2]
        rects = sideview.locate(a_layout(), width=w, height=h)
        for name, rect in rects.items():
            got = sideview.solve(plate, rect, name=f"sheet{sheet}-{name}")
            assert sideview.PLAUSIBLE_ROWS[0] <= got.rows_tee_to_hog <= sideview.PLAUSIBLE_ROWS[1], (
                f"sheet {sheet} {name}: tee->hog {got.rows_tee_to_hog:.1f} px")

    def test_a_fit_outside_the_plausible_band_raises_rather_than_returns(self):
        """A calibration that is merely wrong is the dangerous outcome: every
        crossing afterwards is confidently mistimed."""
        plate = synth.side_view(tee_row=430.0, hog_row=470.0)   # only 40 rows
        with pytest.raises(sideview.SideViewError):
            sideview.solve(plate, (0, 0, 810, 1080))


class TestStoneWidthAt:
    """One measured width pins the rest: apparent width scales with the same
    1/(d - x) the row spacing does."""

    def test_at_the_hog_line_it_returns_the_measured_width(self):
        v = sideview.SideView(rect=(0, 0, 810, 1080), tee_row=435.0, hog_row=520.0)
        assert v.stone_width_at(520.0, 52.0) == pytest.approx(52.0, abs=0.01)

    def test_a_stone_further_away_is_narrower(self):
        v = sideview.SideView(rect=(0, 0, 810, 1080), tee_row=435.0, hog_row=520.0)
        assert v.stone_width_at(435.0, 52.0) < 52.0

    def test_a_stone_nearer_the_camera_is_wider(self):
        v = sideview.SideView(rect=(0, 0, 810, 1080), tee_row=435.0, hog_row=520.0)
        assert v.stone_width_at(600.0, 52.0) > 52.0

    def test_it_matches_the_depth_map_it_is_derived_from(self):
        """Width must track 1/(d - x) exactly, since that is where it comes
        from -- a drift here would be a second, disagreeing scale."""
        from curling_score.geometry import constants as C
        v = sideview.SideView(rect=(0, 0, 810, 1080), tee_row=435.0, hog_row=520.0)
        for row in (450.0, 500.0, 560.0, 620.0):
            ratio = ((v.d_m - C.TEE_TO_HOGLINE_M) / (v.d_m - v.metres_at(row)))
            assert v.stone_width_at(row, 52.0) == pytest.approx(52.0 * ratio, rel=1e-9)


class TestHogRowEdge:
    """The near edge of the paint, not the deepest row of it.

    Measured on VXU9xwmugRg: the two cameras' paint edges differ in sharpness,
    and `_hog_row`'s steepest-dip row lands 0.14 px out on the sharp one and
    2.77 px low on the gradual one. At ~33 image rows a second near the line
    that is 0.084 s, and every crossing through the left view ran that late.
    """

    def _profile(self, ice, dark, width, sharp):
        """A luminance column: flat ice, a dip `width` rows wide, ice again."""
        import numpy as np
        lum = np.full(400, float(ice))
        top = 200
        if sharp:
            lum[top:top + width] = dark
        else:                       # a three-row ramp into the paint
            for k, v in enumerate((ice - (ice - dark) * f
                                   for f in (0.2, 0.6, 0.9))):
                lum[top + k] = v
            lum[top + 3:top + width] = dark
        return lum

    def test_it_finds_the_near_edge_of_a_sharp_line(self):
        lum = self._profile(160, 140, 6, sharp=True)
        got = sideview._hog_row_edge(lum, 150, "t")
        assert got == pytest.approx(199.5, abs=0.6)

    def test_a_gradual_edge_does_not_drag_it_into_the_paint(self):
        """The failure this exists for: `_hog_row` returns the darkest row, so
        a three-row ramp puts its answer three rows deep."""
        lum = self._profile(160, 140, 8, sharp=False)
        edge = sideview._hog_row_edge(lum, 150, "t")
        argmin = sideview._hog_row(lum, 150, "t")
        assert edge < argmin, (edge, argmin)
        assert argmin - edge >= 1.5

    def test_it_anchors_on_the_deepest_dip_not_the_first(self):
        """Taking the first downward threshold crossing put the left view's
        line at row 476 instead of 517, catching the annulus's far edge."""
        lum = self._profile(160, 140, 6, sharp=True)
        lum[170:176] = 152.0              # a shallower dip above the paint
        got = sideview._hog_row_edge(lum, 150, "t")
        assert got > 190, got

    def test_a_flat_profile_is_refused_rather_than_guessed(self):
        import numpy as np
        with pytest.raises(sideview.SideViewError):
            sideview._hog_row_edge(np.full(400, 160.0), 150, "t")

    def test_it_returns_sub_pixel_positions(self):
        lum = self._profile(160, 140, 6, sharp=False)
        got = sideview._hog_row_edge(lum, 150, "t")
        assert got != int(got)
