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


from curling_score.game import spinfit


class Stubs:
    """A decode, a detector and a handle-point model for one rock turning at
    ``f`` turns a second: the grip swings 0.2 stone widths either side."""

    def __init__(self, f=0.2, speed=2.2, fail=0):
        self.f, self.speed, self.fail, self.calls = f, speed, fail, 0     # fail: the first n calls

    def decode(self, video, rect, t0, t1, fps):
        times = list(np.arange(t0, t1, 1 / fps))
        return np.zeros((len(times), 1080, 810, 3), np.uint8), times

    def detect(self, model, frames, times, lo, hi, color, **kw):
        self.calls += 1
        if self.calls <= self.fail:
            raise RuntimeError("decoder fell over")
        return [[box_at(5.0 + self.speed * (t - 100.0))] for t in times]

    def points(self, pose, patches):
        out = []
        for k, p in enumerate(patches):
            t = self.t[k]
            w = self.w[k]
            s = 0.2 * np.sin(2 * np.pi * self.f * t)
            out.append(((30.0, 20.0), (30.0 + s * w, 12.0), 0.9))
        return out


def shot(n=1, cross=True, missing=False, speed=2.2):
    return SimpleNamespace(number=n, color="red", missing=missing, t_hog_s=100.6 if cross else None,
                           hog_crossing=crossing(speed=speed) if cross else None)


def run(shots, stubs, **kw):
    # `points` needs each crop's time and box width: read_spin passes crops in
    # the order of `follow`, so the stub records them from a wrapped follow.
    real = ST.follow

    def spy(times, boxes, seed, view, frame_h):
        got = real(times, boxes, seed, view, frame_h)
        stubs.t = [t for _i, t, _b in got]
        stubs.w = [b[2] - b[0] for _i, _t, b in got]
        return got

    ST.follow = spy
    try:
        return ST.time_spins(shots, "v.mp4", VIEW, model=object(), pose=object(),
                             decode=stubs.decode, detect=stubs.detect, points=stubs.points, **kw)
    finally:
        ST.follow = real


class TestTimeSpins:
    def test_a_turning_rock_gets_its_spin_and_nothing_else(self):
        s = shot()
        before = dict(vars(s))
        assert run([s], Stubs(f=0.2)) == 1
        assert isinstance(s.spin, spinfit.Spin)
        assert s.spin.rpm == pytest.approx(12.0, rel=0.05)
        assert {k: v for k, v in vars(s).items() if k != "spin"} == before

    def test_a_rock_hogtime_never_followed_gets_none_and_the_rest_still_read(self):
        a, b, c = shot(1, cross=False), shot(2, missing=True), shot(3)
        assert run([a, b, c], Stubs()) == 1
        assert not hasattr(a, "spin") and not hasattr(b, "spin") and hasattr(c, "spin")

    def test_a_failure_on_one_rock_costs_that_rock_only(self):
        a, b = shot(1), shot(2)
        assert run([a, b], Stubs(fail=1)) == 1
        assert not hasattr(a, "spin") and hasattr(b, "spin")

    def test_no_model_or_no_lateral_calibration_is_a_no_op(self):
        s = shot()
        assert ST.time_spins([s], "v.mp4", VIEW, model=None, pose=object()) == 0
        flat = SideView(rect=(0, 0, 810, 1080), tee_row=430.0, hog_row=520.0)
        assert ST.time_spins([s], "v.mp4", flat, model=object(), pose=object()) == 0
        assert not hasattr(s, "spin")

    def test_no_pose_model_configured_is_no_spin(self, monkeypatch):
        from curling_score.detect import spinmodel
        monkeypatch.setattr(spinmodel, "default_model", lambda: None)
        s = shot()
        assert ST.time_spins([s], "v.mp4", VIEW, model=object()) == 0

    def test_frames_before_8_m_are_left_out(self):
        s = shot(speed=0.9)
        stubs = Stubs(f=0.2, speed=0.9)          # slow: the first frames sit short of 8 m
        run([s], stubs)
        assert s.spin.samples < len(stubs.t)
