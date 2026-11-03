import cv2
import numpy as np
import pytest

from curling_score.geometry import constants as C, logotie
from curling_score.geometry.sideview import SideView, SideViewError

# Swift Current's two long cameras as tied on the Pepsi logo, WYoHsNHFOk4.
RIGHT = SideView(rect=(1190, 0, 730, 990), tee_row=525.8, hog_row=621.5, d_m=43.76,
                 centre_col=376.8 - 0.0034 * 525.8, lat_px_per_m_at_tee=128.6, centre_line=(376.8, -0.0034))
LEFT = SideView(rect=(0, 0, 730, 990), tee_row=532.7, hog_row=620.1, d_m=41.7,
                centre_col=353.8 - 0.007 * 532.7, lat_px_per_m_at_tee=132.7, centre_line=(353.8, -0.007))
# The logo in the left camera's frame: 17.1 m from its far tee.
LOGO = [(1.26 * np.cos(t), 17.1 + 1.1 * np.sin(t)) for t in np.linspace(0, 2 * np.pi, 400, endpoint=False)]


def camera(true, start, logo):
    th = np.linspace(0, 2 * np.pi, 360, endpoint=False)
    pts, radii = [], []
    for r in (C.R_12FT_M, C.R_8FT_M):
        pts += [true.to_image(r * np.cos(t), r * np.sin(t)) for t in th]
        radii += [r] * len(th)
    outline = np.array([true.to_image(x, y) for x, y in logo])
    return logotie.Camera(view=start, ring_pts=np.round(np.array(pts)), ring_radii=np.array(radii),
                          outline=np.round(outline))


def test_a_camera_fitted_short_is_pulled_to_the_one_it_faces():
    # The left camera as it fitted alone: too close, and too wide across.
    short = SideView(**{**LEFT.__dict__, "d_m": 33.8, "tee_row": 533.0, "lat_px_per_m_at_tee": 137.5,
                        "centre_col": 353.8 - 0.007 * 533.0})
    facing = [(-x, C.TEE_TO_TEE_M - y) for x, y in LOGO]
    got = logotie.tie(camera(LEFT, short, LOGO), camera(RIGHT, RIGHT, facing))
    left, right = got.views
    assert got.logo_before_m > 0.3
    assert got.logo_after_m < 0.03
    assert left.d_m == pytest.approx(LEFT.d_m, abs=1.5)
    assert right.d_m == pytest.approx(RIGHT.d_m, abs=1.5)
    for row in (620, 750, 900):
        assert left.lateral_px_per_m(row) == pytest.approx(LEFT.lateral_px_per_m(row), rel=0.02)
        assert right.lateral_px_per_m(row) == pytest.approx(RIGHT.lateral_px_per_m(row), rel=0.02)
    # The paint the fit holds to stays where it is.
    assert left.hog_row == LEFT.hog_row and left.centre_line == LEFT.centre_line


def test_the_logo_outline_is_its_outer_edge():
    img = np.full((220, 730, 3), 222, np.uint8)
    cv2.ellipse(img, (350, 100), (285, 40), 0, 0, 360, (90, 90, 90), 3, cv2.LINE_AA)
    cv2.ellipse(img, (350, 100), (280, 36), 0, 180, 360, (60, 60, 200), -1, cv2.LINE_AA)
    cv2.ellipse(img, (350, 100), (280, 36), 0, 0, 180, (180, 80, 60), -1, cv2.LINE_AA)
    cv2.putText(img, "PEPSI", (200, 115), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (60, 60, 60), 6)
    p = logotie.logo_outline(img, (0, 220))
    r = np.hypot((p[:, 0] - 350) / 286.5, (p[:, 1] - 100) / 41.5)
    assert np.median(np.abs(r - 1)) < 0.02


def test_no_logo_is_an_error():
    with pytest.raises(SideViewError):
        logotie.logo_outline(np.full((220, 730, 3), 222, np.uint8), (0, 220))
