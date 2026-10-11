"""Following a rock down the camera facing its thrower, for its spin."""
from types import SimpleNamespace

import numpy as np
import pytest

from curling_score.game import spintime as ST
from curling_score.geometry.sideview import SideView

VIEW = SideView(rect=(0, 0, 810, 1080), tee_row=430.0, hog_row=520.0,
                centre_col=390.0, lat_px_per_m_at_tee=150.0)


def box_at(y_m, x_px=380.0, conf=0.9):
    """The rock's box where its bottom sits ``y_m`` past the throwing tee."""
    row = VIEW.row_for(y_m)
    w = ST.STONE_M * VIEW.lateral_px_per_m(row)
    return (x_px - w / 2, row - 0.7 * w, x_px + w / 2, row, conf)


def crossing(t0=100.0, n=60, fps=30.0, speed=2.2, y0=5.0):
    """hogtime's samples: (t, cx, row, w) on the rock's track, 30 fps, from y0 metres."""
    samples = []
    for i in range(n):
        x0, y0b, x1, y1, _c = box_at(y0 + speed * i / fps)
        samples.append((t0 + i / fps, (x0 + x1) / 2, y1, x1 - x0))
    return SimpleNamespace(t=t0 + 0.6, track_key=int(samples[0][1] // 120), samples=tuple(samples))


class TestSeed:
    def test_it_takes_the_track_where_the_window_starts_with_its_velocity(self):
        c = crossing()
        t, cx, row, vx, vy = ST.seed_of(c, VIEW, 100.8)
        assert t == pytest.approx(100.8, abs=0.02)
        assert vy > 0 and abs(vx) < 1.0           # down the frame, straight

    def test_no_track_no_seed(self):
        assert ST.seed_of(None, VIEW, 100.0) is None
        assert ST.seed_of(SimpleNamespace(t=None, track_key=None, samples=()), VIEW, 100.0) is None


class TestFollow:
    def frames(self, n=100, fps=10.0, t0=100.6, speed=2.2, y_start=None, extra=()):
        times = [t0 + i / fps for i in range(n)]
        y_start = 5.0 + speed * (t0 - 100.0) if y_start is None else y_start
        boxes = [[box_at(y_start + speed * i / fps)] + [e(i) for e in extra] for i in range(n)]
        return times, boxes

    def test_it_follows_the_rock_to_the_bottom_of_the_frame_and_stops(self):
        times, boxes = self.frames()
        got = ST.follow(times, boxes, ST.seed_of(crossing(), VIEW, times[0]), VIEW, 1080)
        rows = [b[3] for _i, _t, b in got]
        assert len(got) > 20 and rows == sorted(rows)
        assert rows[-1] < 1080 - ST.BOTTOM_PAD_PX

    def test_a_parked_stone_of_its_colour_beside_it_is_not_the_rock(self):
        parked = lambda i: box_at(14.0, x_px=650.0)          # 1.8 m across, still
        times, boxes = self.frames(extra=(parked,))
        got = ST.follow(times, boxes, ST.seed_of(crossing(), VIEW, times[0]), VIEW, 1080)
        assert all(abs((b[0] + b[2]) / 2 - 380.0) < 20 for _i, _t, b in got)

    def test_a_rock_hidden_for_a_moment_is_found_again(self):
        times, boxes = self.frames()
        for i in range(20, 25):
            boxes[i] = []                                   # a sweeper in front of it
        got = ST.follow(times, boxes, ST.seed_of(crossing(), VIEW, times[0]), VIEW, 1080)
        assert any(i > 25 for i, _t, _b in got)

    def test_a_rock_lost_for_long_is_let_go(self):
        times, boxes = self.frames()
        for i in range(20, 40):
            boxes[i] = []
        got = ST.follow(times, boxes, ST.seed_of(crossing(), VIEW, times[0]), VIEW, 1080)
        assert max(i for i, _t, _b in got) < 20

    def test_a_box_far_off_the_stones_size_is_refused(self):
        times, boxes = self.frames()
        x0, y0, x1, y1, c = boxes[10][0]
        boxes[10] = [(x0 - 60, y0 - 60, x1 + 60, y1, c)]    # a box round a sweeper's legs
        got = ST.follow(times, boxes, ST.seed_of(crossing(), VIEW, times[0]), VIEW, 1080)
        assert 10 not in [i for i, _t, _b in got]


class TestCrop:
    def test_it_is_the_training_crop(self):
        assert ST.crop_box((100.0, 200.0, 160.0, 240.0, 0.9), (1080, 810, 3)) == (79, 180, 182, 251)

    def test_it_stays_inside_the_frame(self):
        x0, y0, x1, y1 = ST.crop_box((5.0, 1050.0, 65.0, 1079.0, 0.9), (1080, 810, 3))
        assert (x0, y1) == (0, 1080)
