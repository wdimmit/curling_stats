import cv2
import numpy as np
import pytest

from curling_score.geometry import calibrate as cal, constants as C, endcam
from curling_score.geometry.sideview import SideView, SideViewError

RECT = (0, 0, 513, 720)
# Cincinnati's blue camera, as fitted on rKiMmtVod0I.
TRUE = SideView(rect=RECT, tee_row=287.5, hog_row=702.5, d_m=13.8, centre_col=271.3 - 0.072 * 287.5,
                lat_px_per_m_at_tee=108.5, centre_line=(271.3, -0.072))
BLUE_BGR = (200, 90, 40)


def disc(view, r_m):
    th = np.linspace(0, 2 * np.pi, 720, endpoint=False)
    return np.array([view.to_image(r_m * np.cos(t), r_m * np.sin(t)) for t in th]).astype(np.int32)


def end_view(view=TRUE, letters=True):
    """An end camera's ice: a blue 12-to-8 band broken by white lettering, the
    painted centre line from the house to the hog line, and the red hog line."""
    img = np.full((720, 513, 3), 225, np.uint8)
    cv2.fillPoly(img, [disc(view, C.R_12FT_M)], BLUE_BGR, cv2.LINE_AA)
    cv2.fillPoly(img, [disc(view, C.R_8FT_M)], (225, 225, 225), cv2.LINE_AA)
    if letters:
        mid = (C.R_12FT_M + C.R_8FT_M) / 2
        for a in np.radians(np.arange(200, 340, 12)):  # "CINCINNATI" across the front
            col, row = view.to_image(mid * np.cos(a), -mid * np.sin(a))
            cv2.rectangle(img, (int(col) - 6, int(row) - 9), (int(col) + 6, int(row) + 9), (240, 240, 240), -1)
    top = view.row_for(2.2)
    for row in np.arange(top, view.hog_row - 4, 0.5):
        cv2.circle(img, (int(round(view.centre_col_at(row))), int(round(row))), 1, (180, 180, 180), -1)
    r = int(round(view.hog_row))
    img[r - 1:r + 2, :] = (90, 90, 200)
    return img


PAINT = endcam.EndPaint(ring=(cal.BLUE_HSV,), inner_radius_m=C.R_8FT_M)


class TestSolve:
    def test_recovers_the_camera_it_was_drawn_with(self):
        fit = endcam.solve(end_view(), RECT, PAINT, "blue")
        v = fit.view
        assert v.hog_row == pytest.approx(TRUE.hog_row, abs=1.0)
        assert v.tee_row == pytest.approx(TRUE.tee_row, abs=2.0)
        assert v.lat_px_per_m_at_tee == pytest.approx(TRUE.lat_px_per_m_at_tee, rel=0.03)
        assert v.centre_line[1] == pytest.approx(TRUE.centre_line[1], abs=0.005)
        assert fit.ring_residual_m < 0.03

    def test_places_points_between_the_house_and_the_hog_line(self):
        v = endcam.solve(end_view(), RECT, PAINT, "blue").view
        for x, y in ((0.0, 4.0), (1.0, 5.5), (-1.5, 3.0), (0.5, -3.0)):
            col, row = TRUE.to_image(x, y)
            got = v.to_house(col, row)
            assert got == pytest.approx((x, y), abs=0.08)

    def test_needs_a_house(self):
        with pytest.raises(SideViewError):
            endcam.solve(np.full((720, 513, 3), 225, np.uint8), RECT, PAINT, "blue")


class TestEndCameraCrossing:
    def test_a_later_sighting_in_the_column_does_not_unmake_the_crossing(self):
        # rKiMmtVod0I end 2 rock 2: the stone crosses at 2686.9 s and leaves the
        # view; 1.4 s later a yellow broom in the same column ends short of the line.
        from curling_score.detect import longview, sidemodel

        v = TRUE.__class__(**{**TRUE.__dict__, "end_camera": True})
        stone = [(2686.0 + i / 30, v.row_for(4.4 + 2.18 * i / 30), 52.0, 300.0) for i in range(30)]
        stone = [p for p in stone if p[1] < 715]
        broom = [(2688.3 + i / 30, 583.0 + 2.5 * i, 40.0, 303.0) for i in range(30)]
        tracks = {2: stone + broom}
        refused = longview.crossing_from_tracks(tracks, v, offset_s=0.0, max_gap_s=sidemodel.STRADDLE_GAP_MAX_S)
        assert refused.t is None
        got = longview.crossing_from_tracks(sidemodel.split_at_gaps(tracks, sidemodel.STRADDLE_GAP_MAX_S), v,
                                            offset_s=0.0, max_gap_s=sidemodel.STRADDLE_GAP_MAX_S)
        assert got.t == pytest.approx(2686.0 + (C.TEE_TO_HOGLINE_M - 4.4) / 2.18, abs=0.04)
        assert got.track_key % 1000 == 2
