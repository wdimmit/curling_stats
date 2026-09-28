"""The composite's two wide side views: where they are, and where the far
end's paint sits in them.

Each side camera watches the *other* end's house and hog line -- its own hog
line is about half a metre beneath it, out of frame. So the throwing end's
crossing is seen by the camera at the target end.
"""

import numpy as np
import pytest

from curling_score.geometry import constants as C
from curling_score.geometry import layout, sideview
from curling_score.geometry.sideview import SideView
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

    def test_the_higher_framings_of_the_2026_27_season(self):
        """Sheets 2-4 were re-aimed over the 2026 summer: on 2026-09-27 the far
        tee sat at rows 306-338, where the spring's sat at 405-445."""
        for tee, hog in ((337.7, 405.0), (305.8, 373.0), (336.3, 409.0),
                         (367.8, 440.0)):
            got = sideview.solve(synth.side_view(tee_row=tee, hog_row=hog),
                                 (0, 0, 810, 1080))
            assert got.tee_row == pytest.approx(tee, abs=1.5), (tee, hog)
            assert got.hog_row == pytest.approx(hog, abs=1.5), (tee, hog)

    def test_a_ring_the_search_cuts_through_is_refused_not_mispaired(self):
        """With the ring's top edge above the rows searched, the outermost
        crossings are the far band's inner edge and the near band's outer edge.
        Fitting those as the 12-ft pair put sheet 3's tee 8 px low on
        2026-09-27 -- a confident, wrong calibration."""
        top = int(1080 * sideview._HOUSE_SEARCH_WIDE[0])
        # At sheet 3's own spacing, 72.2 rows, the far band spans tee-16.6 to
        # tee-11.2, so a tee 14 rows in puts the first row searched inside it.
        # The mispaired fit shortens tee-to-hog to ~64, which the plausibility
        # band lets through.
        tee = top + 14.0
        plate = synth.side_view(tee_row=tee, hog_row=tee + 72.2)
        with pytest.raises(sideview.SideViewError, match="runs past"):
            sideview.solve(plate, (0, 0, 810, 1080))

    def test_faint_green_well_above_the_ring_is_not_the_ring(self):
        """Sheet 3's right view, 2026-09-27 at 19:31: a faint green patch at
        rows 200-223 (peak 3.4), 120 rows above the ring. It crosses the top of
        the wide search, which is not the ring running past it."""
        plate = synth.side_view(tee_row=362.4, hog_row=435.0)
        plate[200:224, :] = (236, 242, 236)
        got = sideview.solve(plate, (0, 0, 810, 1080))
        assert got.tee_row == pytest.approx(362.4, abs=1.5)
        assert got.hog_row == pytest.approx(435.0, abs=1.5)

    def test_green_inside_the_search_but_apart_from_the_ring_is_left_out(self):
        plate = synth.side_view(tee_row=337.7, hog_row=405.0)
        plate[240:250, :] = (236, 242, 236)
        got = sideview.solve(plate, (0, 0, 810, 1080))
        assert got.tee_row == pytest.approx(337.7, abs=1.5)

    def test_a_view_the_spring_band_fits_is_never_searched_wider(self):
        """Faint green above the house on some right views sits just under the
        threshold. A view the spring band fits must not be staked on it."""
        plate = synth.side_view(tee_row=430.0, hog_row=520.0)
        plate[270:280, 300:500] = synth.GREEN_PAINT    # well above the ring
        got = sideview.solve(plate, (0, 0, 810, 1080))
        assert got.tee_row == pytest.approx(430.0, abs=1.5)


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


class TestLateralMap:
    """Across the sheet. A row is a line of constant depth -- the hog line is
    flat, which the depth fit already relies on -- so a metre across spans a
    number of pixels that scales with the same 1/(d - x) as the rows do."""

    V = sideview.SideView(rect=(0, 0, 810, 1080), tee_row=430.0, hog_row=520.0,
                          centre_col=390.0, lat_px_per_m_at_tee=148.0)

    def test_the_tee_is_the_origin(self):
        assert self.V.to_house(390.0, 430.0) == pytest.approx((0.0, 0.0), abs=1e-9)

    def test_the_12ft_ring_s_sides_on_the_tee_row_are_its_radius(self):
        for sign in (-1, 1):
            x, y = self.V.to_house(390.0 + sign * C.R_12FT_M * 148.0, 430.0)
            assert x == pytest.approx(sign * C.R_12FT_M, abs=1e-9)
            assert y == pytest.approx(0.0, abs=1e-9)

    def test_image_right_is_the_thrower_s_right(self):
        assert self.V.to_house(500.0, 430.0)[0] > 0

    def test_nearer_the_camera_is_up_sheet(self):
        assert self.V.to_house(390.0, 450.0)[1] > 0

    def test_it_round_trips(self):
        for x, y in ((-1.5, -1.2), (0.7, 0.4), (1.9, 2.5), (0.0, -1.829)):
            col, row = self.V.to_image(x, y)
            assert self.V.to_house(col, row) == pytest.approx((x, y), abs=1e-9)

    def test_a_metre_across_shrinks_with_distance_as_a_stone_does(self):
        for y in (-1.829, 0.0, 1.829, 3.0):
            row = self.V.row_for(y)
            assert (self.V.lateral_px_per_m(row) / 148.0 == pytest.approx(
                self.V.stone_width_at(row, 52.0)
                / self.V.stone_width_at(430.0, 52.0)))

    def test_a_view_without_lateral_calibration_says_so(self):
        v = sideview.SideView(rect=(0, 0, 810, 1080), tee_row=430.0, hog_row=520.0)
        assert not v.has_lateral
        with pytest.raises(sideview.SideViewError):
            v.to_house(390.0, 430.0)
        with pytest.raises(sideview.SideViewError):
            v.to_image(0.0, 0.0)


def _depth_only(tee=430.0, hog=520.0, w=810):
    return sideview.SideView(rect=(0, 0, w, 1080), tee_row=tee, hog_row=hog)


class TestSolveLateral:
    def test_it_recovers_the_centre_and_scale_it_was_drawn_with(self):
        plate = synth.side_view_house(centre_col=390.0, lat_px_per_m=148.0)
        got = sideview.solve_lateral(plate, _depth_only())
        assert got.centre_col == pytest.approx(390.0, abs=1.0)
        assert got.lat_px_per_m_at_tee == pytest.approx(148.0, rel=0.015)

    def test_it_works_off_centre_and_at_the_other_scales_measured(self):
        # VXU9's right view (Phase 0), and a nearer, wider framing.
        for tee, hog, centre, lat in ((436.45, 514.0, 414.7, 128.4),
                                      (452.0, 533.0, 420.0, 180.0)):
            plate = synth.side_view_house(tee_row=tee, hog_row=hog,
                                          centre_col=centre, lat_px_per_m=lat)
            got = sideview.solve_lateral(plate, _depth_only(tee, hog))
            assert got.centre_col == pytest.approx(centre, abs=1.0), (centre, lat)
            assert got.lat_px_per_m_at_tee == pytest.approx(lat, rel=0.015)

    def test_the_depth_calibration_is_left_exactly_as_it_was(self):
        view = _depth_only()
        got = sideview.solve_lateral(synth.side_view_house(), view)
        assert (got.rect, got.tee_row, got.hog_row, got.d_m) == \
            (view.rect, view.tee_row, view.hog_row, view.d_m)

    def test_a_far_band_half_as_green_still_fits(self):
        """A fixed threshold fails here: each band's edges sit at half its own peak."""
        plate = synth.side_view_house(far_green=(120, 138, 120), noise=4.0)
        got = sideview.solve_lateral(plate, _depth_only())
        assert got.centre_col == pytest.approx(390.0, abs=1.5)
        assert got.lat_px_per_m_at_tee == pytest.approx(148.0, rel=0.02)

    def test_the_neighbouring_sheet_s_ring_at_the_edge_is_not_this_house(self):
        plate = synth.side_view_house()
        plate[:, 785:810] = synth.GREEN_PAINT      # VXU9 left: crossings at 790-797
        got = sideview.solve_lateral(plate, _depth_only())
        assert got.centre_col == pytest.approx(390.0, abs=1.0)

    def test_neighbouring_rings_longer_than_this_one_s_bands_are_passed_over(self):
        """Sheet 2's wider 2026-27 framing: the far house spans ~400 px, and the
        neighbouring sheets' rings show ~100 px of green at each edge of the
        view -- longer than this ring's own ~67 px bands along the tee rows."""
        tee, hog, centre, lat = 337.7, 405.0, 405.0, 110.0
        plate = synth.side_view_house(tee_row=tee, hog_row=hog,
                                      centre_col=centre, lat_px_per_m=lat)
        rows = slice(int(tee) - 12, int(tee) + 13)
        plate[rows, 0:100] = synth.GREEN_PAINT
        plate[rows, 700:790] = synth.GREEN_PAINT
        got = sideview.solve_lateral(plate, _depth_only(tee, hog))
        assert got.centre_col == pytest.approx(centre, abs=1.0)
        assert got.lat_px_per_m_at_tee == pytest.approx(lat, rel=0.015)

    def test_a_player_across_a_few_rows_costs_those_rows_only(self):
        plate = synth.side_view_house()
        plate[426:429, 100:220] = (40, 40, 40)     # dark trousers over the near band
        got = sideview.solve_lateral(plate, _depth_only())
        assert got.lat_px_per_m_at_tee == pytest.approx(148.0, rel=0.015)

    def test_ice_with_no_house_is_refused(self):
        plate = np.full((1080, 810, 3), synth.SIDE_ICE, np.uint8)
        with pytest.raises(sideview.SideViewError):
            sideview.solve_lateral(plate, _depth_only())

    def test_a_far_band_too_faint_to_see_is_refused_not_guessed(self):
        plate = synth.side_view_house(far_green=(236, 239, 236))
        with pytest.raises(sideview.SideViewError):
            sideview.solve_lateral(plate, _depth_only())

    def test_an_implausible_scale_is_refused(self):
        plate = synth.side_view_house(lat_px_per_m=60.0)
        with pytest.raises(sideview.SideViewError):
            sideview.solve_lateral(plate, _depth_only())

    def test_a_view_offset_in_the_composite_reads_its_own_columns(self):
        big = np.full((1080, 1920, 3), synth.SIDE_ICE, np.uint8)
        big[:, 1107:1107 + 810] = synth.side_view_house()
        view = sideview.SideView(rect=(1107, 0, 810, 1080), tee_row=430.0,
                                 hog_row=520.0)
        got = sideview.solve_lateral(big, view)
        assert got.centre_col == pytest.approx(390.0, abs=1.0)


@pytest.mark.slow
class TestEveryRealViewLateral:
    """Both side views of all five sheets, across the sheet this time."""

    @pytest.mark.parametrize("sheet,vid", sorted(VALIDATION_VIDS.items()))
    def test_both_views_calibrate_across(self, side_plate, sheet, vid):
        plate = side_plate(vid)
        h, w = plate.shape[:2]
        rects = sideview.locate(a_layout(), width=w, height=h)
        for name, rect in rects.items():
            label = f"sheet{sheet}-{name}"
            view = sideview.solve_lateral(
                plate, sideview.solve(plate, rect, name=label), name=label)
            lo, hi = sideview.PLAUSIBLE_LAT_PX_PER_M
            assert lo <= view.lat_px_per_m_at_tee <= hi, label
            assert 0.2 * rect[2] <= view.centre_col <= 0.8 * rect[2], label


def _plate_with_centre_line(a=380.0, b=0.01, side_px=200.0, logo=True, line=True, seed=0):
    """A grey plate with the painted centre line col = a + b*row below the house,
    a parallel line side_px to its right, and optionally a noisy centre-ice logo."""
    rng = np.random.default_rng(seed)
    plate = np.full((1080, 1920, 3), 150.0) + rng.normal(0, 2.0, (1080, 1920, 3))
    for row in range(470, 1080):
        for col in ((a + b * row, a + b * row + side_px) if line else ()):
            c = int(round(col))
            plate[row, c - 1:c + 2, :] -= 14.0
    if logo:
        plate[700:800, 280:520, :] = 150.0 + rng.normal(0, 18.0, (100, 240, 3))
    return plate


class TestSolveCentreLine:
    VIEW = SideView(rect=(0, 0, 810, 1080), tee_row=430.0, hog_row=520.0,
                    centre_col=384.0, lat_px_per_m_at_tee=150.0)

    def test_it_recovers_the_painted_line_through_a_logo(self):
        got = sideview.solve_centre_line(_plate_with_centre_line(), self.VIEW)
        a, b = got.centre_line
        for row in (500.0, 1000.0):
            assert a + b * row == pytest.approx(380.0 + 0.01 * row, abs=0.6)

    def test_ice_with_no_line_is_refused(self):
        with pytest.raises(sideview.SideViewError):
            sideview.solve_centre_line(_plate_with_centre_line(line=False), self.VIEW)

    def test_a_view_without_lateral_calibration_is_refused(self):
        flat = SideView(rect=(0, 0, 810, 1080), tee_row=430.0, hog_row=520.0)
        with pytest.raises(sideview.SideViewError):
            sideview.solve_centre_line(_plate_with_centre_line(), flat)

    def test_the_rest_of_the_calibration_is_left_as_it_was(self):
        got = sideview.solve_centre_line(_plate_with_centre_line(), self.VIEW)
        assert (got.tee_row, got.hog_row, got.centre_col, got.lat_px_per_m_at_tee) == (
            430.0, 520.0, 384.0, 150.0)


class TestLateralFromThePaint:
    V = SideView(rect=(0, 0, 810, 1080), tee_row=430.0, hog_row=520.0,
                 centre_col=384.0, lat_px_per_m_at_tee=150.0, centre_line=(380.0, 0.01))

    def test_the_centre_line_is_x_zero_on_every_row(self):
        for row in (450.0, 700.0, 1050.0):
            assert self.V.lateral_x(380.0 + 0.01 * row, row) == pytest.approx(0.0, abs=1e-9)

    def test_without_a_centre_line_the_ring_centre_is_used(self):
        v = SideView(rect=(0, 0, 810, 1080), tee_row=430.0, hog_row=520.0,
                     centre_col=384.0, lat_px_per_m_at_tee=150.0)
        assert v.centre_col_at(900.0) == 384.0

    def test_to_house_and_to_image_round_trip_with_the_line(self):
        col, row = self.V.to_image(0.7, 5.0)
        x, y = self.V.to_house(col, row)
        assert (x, y) == pytest.approx((0.7, 5.0), abs=1e-6)
