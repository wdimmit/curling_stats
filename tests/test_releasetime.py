"""The release read's model-free part: hand crops cut along the delivery
path, the frame stacks the model reads, and the on-to-off step fit."""
import numpy as np
import pytest

from curling_score.detect import lanekeep
from curling_score.game import linetime, releasetime
from curling_score.geometry.sideview import SideView

# 10/20 Wed sheet 2's left view.
VIEW = SideView(rect=(0, 0, 810, 1080), tee_row=360.8, hog_row=433.0,
                centre_col=399.21, lat_px_per_m_at_tee=117.425,
                centre_line=(394.038, 0.007471))
TEE_Y = linetime.TEE_Y
T_REL = 100.0

# A throw from half a metre behind the throwing tee to 8 m past it, 3 s,
# drifting a little to the thrower's right: `[t, y, x]`.
DELIVERY = [[3.0 * i / 20, TEE_Y - (-1.0 + 9.0 * i / 20), 0.05 + 0.1 * i / 20]
            for i in range(21)]


def lane(t0=T_REL - 0.2, t1=T_REL + 3.5):
    """Strips of the lane at 30 fps with a bright 3x3 dot at the rock's image
    point in each; the `LaneKeeper`'s origin, and the strips' times."""
    row0, row1, col0, col1 = lanekeep.strip_box(VIEW)
    times = np.arange(round(t0 * 30), round(t1 * 30)) / 30
    strips = np.zeros((len(times), row1 - row0, col1 - col0), np.uint8)
    for i, t in enumerate(times):
        at = releasetime.rock_at(DELIVERY, t - T_REL)
        if at is None:
            continue
        y, x = at
        col, row = VIEW.to_image(-x, TEE_Y - y)
        r, c = round(row) - row0, round(col) - col0
        strips[i, r - 1:r + 2, c - 1:c + 2] = 255
    return strips, times, (col0, row0)


def test_rock_at_interpolates_the_path_and_is_none_outside_it():
    y, x = releasetime.rock_at(DELIVERY, 0.075)
    assert y == pytest.approx((DELIVERY[0][1] + DELIVERY[1][1]) / 2)
    assert x == pytest.approx((DELIVERY[0][2] + DELIVERY[1][2]) / 2)
    assert releasetime.rock_at(DELIVERY, -0.01) is None
    assert releasetime.rock_at(DELIVERY, 3.01) is None
    assert releasetime.rock_at(DELIVERY, 3.0) == pytest.approx(DELIVERY[-1][1:])


def test_the_rock_sits_at_the_same_spot_in_every_crop():
    strips, times, origin = lane()
    crops, o, yp = releasetime.hand_crops(strips, times, origin, VIEW, DELIVERY, T_REL)
    assert crops.shape[1:] == (107, 160) and crops.dtype == np.uint8
    assert len(crops) > 40
    for c in crops:
        r, col = np.unravel_index(c.argmax(), c.shape)
        assert abs(col - 80) <= 2 and abs(r - 88) <= 2       # 0.5 m across, 0.55 m up at 160 px/m


def test_crops_span_half_a_metre_behind_the_tee_to_the_path_end():
    strips, times, origin = lane()
    crops, o, yp = releasetime.hand_crops(strips, times, origin, VIEW, DELIVERY, T_REL)
    assert len(crops) == len(o) == len(yp) < len(strips)
    assert yp.min() >= -0.5 - 0.1 and yp.max() <= TEE_Y - DELIVERY[-1][1] + 1e-6
    assert o.min() >= DELIVERY[0][0] and o.max() <= DELIVERY[-1][0]


def test_stacks_take_the_frames_two_and_four_back():
    o = np.arange(10) / 30
    crops = np.stack([np.full((107, 160), 10 * i, np.uint8) for i in range(10)])
    s = releasetime.stacks(crops, o)
    assert s.shape == (10, 3, 107, 160) and s.dtype == np.float32
    for i in (4, 7, 9):
        assert [s[i, k, 0, 0] * 255 for k in range(3)] == pytest.approx(
            [10 * (i - 4), 10 * (i - 2), 10 * i])
    # Clamped to the sequence at the start.
    assert [s[0, k, 0, 0] * 255 for k in range(3)] == pytest.approx([0, 0, 0])
    assert [s[1, k, 0, 0] * 255 for k in range(3)] == pytest.approx([0, 0, 10])
    assert s.max() <= 1.0


def test_stacks_pick_by_time_on_a_thinned_sequence():
    o = np.arange(0, 20, 2) / 30                       # every other frame
    crops = np.stack([np.full((107, 160), 10 * i, np.uint8) for i in range(10)])
    s = releasetime.stacks(crops, o)
    # Four frames back is two crops back, two frames back is one.
    assert [s[5, k, 0, 0] * 255 for k in range(3)] == pytest.approx([30, 40, 50])


def test_step_fit_finds_the_switch():
    assert releasetime.step_fit(np.array([0, .1, 0, .9, 1, 1.])) == 3


def test_a_switch_on_the_first_frames_or_never_is_no_release():
    o = np.linspace(0.2, 3.0, 43)
    assert releasetime.release_at(np.r_[[0.0], np.ones(42)], o, DELIVERY) is None
    assert releasetime.release_at(np.zeros(43), o, DELIVERY) is None


def test_release_is_the_rocks_point_on_the_path():
    o = np.linspace(0.2, 3.0, 43)
    t, y, x = releasetime.release_at(np.r_[np.zeros(20), np.ones(23)], o, DELIVERY)
    assert t == pytest.approx(o[20])
    assert (y, x) == pytest.approx(releasetime.rock_at(DELIVERY, o[20]))
