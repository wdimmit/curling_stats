import numpy as np
import pytest

from curling_score.geometry import calibrate, constants as C
from tests import synth


class TestFindRings:
    def test_locates_the_twelve_and_four_foot_rings(self):
        img = synth.house_panel(cx=148, cy=150, px_per_m=75.0)
        rings = calibrate.find_rings(img)
        assert rings.twelve_ft.center == pytest.approx((148, 150), abs=2.0)
        assert rings.twelve_ft.radius_px == pytest.approx(C.R_12FT_M * 75.0, rel=0.03)
        assert rings.four_ft.radius_px == pytest.approx(C.R_4FT_M * 75.0, rel=0.05)

    def test_rings_are_concentric(self):
        rings = calibrate.find_rings(synth.house_panel())
        dx = rings.twelve_ft.center[0] - rings.four_ft.center[0]
        dy = rings.twelve_ft.center[1] - rings.four_ft.center[1]
        assert (dx**2 + dy**2) ** 0.5 < 3.0

    def test_survives_sensor_noise(self):
        img = synth.house_panel(px_per_m=75.0, noise=8.0)
        rings = calibrate.find_rings(img)
        assert rings.twelve_ft.radius_px == pytest.approx(C.R_12FT_M * 75.0, rel=0.05)

    def test_raises_when_there_is_no_house_in_view(self):
        blank = np.full((514, 297, 3), 235, dtype=np.uint8)
        with pytest.raises(calibrate.CalibrationError):
            calibrate.find_rings(blank)

    def test_measures_the_outer_edge_even_when_the_ring_is_broken(self):
        # Regression: a swept-over wedge makes the green annulus a C-shape.
        # RETR_EXTERNAL then traces the inner boundary too, and a naive
        # fitEllipse averaged inner (97 px) and outer (145 px) radii into a
        # 9%-wrong 132.8 px on sheet 3.
        img = synth.break_ring(synth.house_panel(cx=148, cy=150, px_per_m=75.0))
        rings = calibrate.find_rings(img)
        assert rings.twelve_ft.radius_px == pytest.approx(C.R_12FT_M * 75.0, rel=0.03)

    def test_ring_ratio_is_recovered_from_a_broken_ring(self):
        img = synth.break_ring(synth.house_panel(px_per_m=75.0))
        rings = calibrate.find_rings(img)
        ratio = rings.twelve_ft.radius_px / rings.four_ft.radius_px
        assert ratio == pytest.approx(C.R_12FT_M / C.R_4FT_M, rel=0.05)


class TestRingsOnEverySheet:
    """The cross-sheet gate: ring geometry must hold on all five sheets."""

    @pytest.mark.parametrize("sheet", [1, 2, 3, 4, 5])
    @pytest.mark.parametrize("which", ["top", "bottom"])
    def test_ring_ratio_matches_the_painted_house(self, sheet, which, harvested_frames):
        import cv2
        from curling_score.geometry import layout, lighting
        from tests.conftest import VALIDATION_VIDS

        imgs = harvested_frames(VALIDATION_VIDS[sheet])
        rect = getattr(layout.detect_panels(imgs), which)
        x, y, w, h = rect
        lit = [
            im[y : y + h, x : x + w]
            for im in imgs
            if lighting.is_calibratable(im[y : y + h, x : x + w])
        ]
        assert lit, f"sheet {sheet} {which}: no well-lit frames"
        median = np.median(np.stack(lit), axis=0).astype(np.uint8)

        rings = calibrate.find_rings(median)
        ratio = rings.twelve_ft.radius_px / rings.four_ft.radius_px
        # 3% covers the residual barrel distortion that solve() then removes.
        assert ratio == pytest.approx(C.R_12FT_M / C.R_4FT_M, rel=0.03)
        # Near-nadir cameras: the imaged rings are close to circular.
        assert rings.twelve_ft.eccentricity < 0.08


class TestSolve:
    def test_maps_the_tee_to_the_origin(self):
        img = synth.house_panel(cx=148, cy=150, px_per_m=75.0)
        cal = calibrate.solve(img, delivery_side="bottom")
        assert cal.to_sheet(148, 150) == pytest.approx((0.0, 0.0), abs=0.03)

    def test_recovers_the_scale(self):
        cal = calibrate.solve(synth.house_panel(px_per_m=75.0), delivery_side="bottom")
        assert cal.px_per_m == pytest.approx(75.0, rel=0.02)

    def test_reports_a_small_residual_against_the_four_foot_ring(self):
        cal = calibrate.solve(synth.house_panel(px_per_m=75.0), delivery_side="bottom")
        assert cal.residual_m < 0.02

    def test_up_sheet_is_positive_y_when_stones_arrive_from_below(self):
        # Top panel: the house sits high and stones enter at the bottom of the
        # frame, so image-down is toward the delivery end.
        cal = calibrate.solve(
            synth.house_panel(cx=148, cy=150, px_per_m=75.0), delivery_side="bottom"
        )
        x, y = cal.to_sheet(148, 150 + 75)  # one metre further down the image
        assert y == pytest.approx(1.0, abs=0.05)
        assert x == pytest.approx(0.0, abs=0.05)

    def test_up_sheet_is_still_positive_y_when_stones_arrive_from_above(self):
        # Bottom panel: the house sits low and stones enter at the top, so the
        # frame is effectively rotated 180 degrees relative to the top panel.
        cal = calibrate.solve(
            synth.house_panel(cx=148, cy=360, px_per_m=75.0), delivery_side="top"
        )
        x, y = cal.to_sheet(148, 360 - 75)  # one metre further up the image
        assert y == pytest.approx(1.0, abs=0.05)
        assert x == pytest.approx(0.0, abs=0.05)

    def test_x_flips_with_y_so_both_houses_share_one_frame(self):
        top = calibrate.solve(
            synth.house_panel(cx=148, cy=150, px_per_m=75.0), delivery_side="bottom"
        )
        bottom = calibrate.solve(
            synth.house_panel(cx=148, cy=360, px_per_m=75.0), delivery_side="top"
        )
        # The same physical side of the sheet must get the same sign.
        assert top.to_sheet(148 + 75, 150)[0] == pytest.approx(1.0, abs=0.05)
        assert bottom.to_sheet(148 - 75, 360)[0] == pytest.approx(1.0, abs=0.05)

    def test_round_trips_through_pixels(self):
        cal = calibrate.solve(synth.house_panel(px_per_m=75.0), delivery_side="bottom")
        for pt in [(0.0, 0.0), (0.5, -1.2), (-1.4, 2.0)]:
            assert cal.to_sheet(*cal.to_pixels(*pt)) == pytest.approx(pt, abs=1e-6)

    def test_refuses_an_unlit_panel(self):
        dark = np.zeros((514, 297, 3), dtype=np.uint8)
        with pytest.raises(calibrate.CalibrationError):
            calibrate.solve(dark, delivery_side="bottom")


class TestSolveOnEverySheet:
    """The headline calibration gate: every panel of every sheet must solve."""

    @pytest.mark.parametrize("sheet", [1, 2, 3, 4, 5])
    @pytest.mark.parametrize("which", ["top", "bottom"])
    def test_calibrates_within_two_centimetres(self, sheet, which, harvested_frames):
        import cv2
        from curling_score.geometry import layout, lighting
        from tests.conftest import VALIDATION_VIDS

        imgs = harvested_frames(VALIDATION_VIDS[sheet])
        rect = getattr(layout.detect_panels(imgs), which)
        x, y, w, h = rect
        lit = [
            im[y : y + h, x : x + w]
            for im in imgs
            if lighting.is_calibratable(im[y : y + h, x : x + w])
        ]
        assert lit, f"sheet {sheet} {which}: no well-lit frames"
        median = np.median(np.stack(lit), axis=0).astype(np.uint8)

        cal = calibrate.solve(
            median, delivery_side="bottom" if which == "top" else "top"
        )
        # The plan's acceptance threshold, checked against the unused 4-ft ring.
        assert cal.residual_m < 0.02
        # Sanity: the club's cameras put the house at 65-85 px per metre.
        assert 60.0 < cal.px_per_m < 90.0
        # Erosion is a property of the paint and threshold, not the sheet.
        assert 0.5 < cal.edge_erosion_px < 5.0
