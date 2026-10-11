"""Following a rock down the camera facing its thrower, for its spin."""
import math
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


def crossing(t0=100.0, n=60, fps=30.0, speed=2.2, y0=5.0, decel=0.0):
    """hogtime's samples: (t, cx, row, w) on the rock's track, 30 fps, from y0 metres."""
    samples = []
    for i in range(n):
        tau = i / fps
        x0, y0b, x1, y1, _c = box_at(y0 + speed * tau - 0.5 * decel * tau ** 2)
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

    def __init__(self, f=0.2, speed=2.2, fail=0, decel=0.0, amp=0.2, centre=0.0, noise=0.0):
        self.f, self.speed, self.fail, self.calls = f, speed, fail, 0     # fail: the first n calls
        self.amp, self.centre = amp, centre                               # the handle's swing
        self.noise = np.random.default_rng(7) if noise else None
        self.noise_sd = noise
        # decel: the rock slows from `speed` at t=100 and comes to rest in
        # view, and its handle stops with it.
        self.decel, self.decoded = decel, []
        self.t_stop = 100.0 + speed / decel if decel else math.inf

    def y_at(self, t):
        tau = min(t, self.t_stop) - 100.0
        return 5.0 + self.speed * tau - 0.5 * self.decel * tau ** 2

    def decode(self, video, rect, t0, t1, fps):
        self.decoded.append((t0, t1))
        times = list(np.arange(t0, t1, 1 / fps))
        return np.zeros((len(times), 1080, 810, 3), np.uint8), times

    def detect(self, model, frames, times, lo, hi, color, **kw):
        self.calls += 1
        if self.calls <= self.fail:
            raise RuntimeError("decoder fell over")
        return [[box_at(self.y_at(t))] for t in times]

    def points(self, pose, patches):
        out = []
        for k, p in enumerate(patches):
            t = min(self.t[k], self.t_stop)
            w = self.w[k]
            s = self.centre + self.amp * np.sin(2 * np.pi * self.f * t)
            if self.noise is not None:
                s += self.noise.normal(0, self.noise_sd)
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

    @pytest.mark.parametrize("speed, rest_s, f", [
        (2.8, 6.0, 0.2), (2.2, 6.0, 0.2), (2.2, 5.0, 0.15), (2.8, 5.0, 0.15), (2.8, 6.0, 0.15)])
    def test_a_rock_that_comes_to_rest_in_view_is_read_right_or_not_at_all(self, speed, rest_s, f):
        # A hogged rock stops short of the frame's bottom; at rest its box and
        # its handle keep still, and those frames read as a swing that stopped
        # -- 3-7 rpm for these 9-12 rpm rocks, given as fact, before the cut.
        decel = speed / rest_s
        s = shot(speed=speed)
        s.hog_crossing = crossing(speed=speed, decel=decel)
        # The handle swings as a red one usually does (spinfit.SWING): a rock
        # under a turn is timed with that swing held.
        run([s], Stubs(f=f, speed=speed, decel=decel, amp=0.226, centre=-0.024))
        assert getattr(s, "spin", None) is None or s.spin.rpm == pytest.approx(60 * f, rel=0.05)

    def test_a_rock_that_comes_to_rest_in_view_reads_its_spin_while_moving(self):
        s = shot(speed=2.8)
        s.hog_crossing = crossing(speed=2.8, decel=2.8 / 6.0)
        run([s], Stubs(f=0.2, speed=2.8, decel=2.8 / 6.0))      # at rest 6 s on, 13.4 m out
        assert s.spin.rpm == pytest.approx(12.0, rel=0.05)

    def test_a_refused_crossing_starts_where_its_own_track_met_the_hog_row(self):
        # Refused for speed: no time, but the samples are kept -- every one of
        # the rock's colour in the window, a stone on the next sheet's among
        # them, here already below the hog row three seconds before this rock
        # got there.
        s = shot()
        c = s.hog_crossing
        stray = tuple((97.0 + i / 30, 650.0, VIEW.hog_row + 60.0 + i / 10, 40.0) for i in range(120))
        s.t_hog_s, s.hog_crossing = None, SimpleNamespace(t=None, track_key=c.track_key,
                                                          samples=stray + c.samples)
        stubs = Stubs(f=0.2)
        assert run([s], stubs) == 1
        assert stubs.decoded[0][0] == pytest.approx(100.6 + ST.WINDOW_S[0], abs=0.1)
        assert s.spin.rpm == pytest.approx(12.0, rel=0.05)

    def test_a_handle_point_model_that_will_not_load_costs_the_spin_not_the_end(self, monkeypatch):
        from curling_score.detect import spinmodel

        def broken():
            raise RuntimeError("this ultralytics cannot read that file")
        monkeypatch.setattr(spinmodel, "default_model", broken)
        s = shot()
        assert ST.time_spins([s], "v.mp4", VIEW, model=object()) == 0
        assert not hasattr(s, "spin")


class TestStopping:
    def test_a_rock_running_on_has_not_stopped(self):
        ts = np.arange(0, 6, 0.1)
        assert ST.stopping_at(ts, 8.0 + 1.0 * ts) is None

    def test_a_rock_at_rest_stopped_where_it_slowed_below_the_bound(self):
        ts = np.arange(0, 8, 0.1)
        ms = 8.0 + 2.0 * np.minimum(ts, 4.0) - 0.25 * np.minimum(ts, 4.0) ** 2   # rests at 4 s
        assert 2.5 < ST.stopping_at(ts, ms) < 4.0

    def test_one_wild_box_does_not_stop_it(self):
        ts = np.arange(0, 6, 0.1)
        ms = 8.0 + 1.5 * ts
        ms[30] -= 3.0
        assert ST.stopping_at(ts, ms) is None


class TestModelsAreLoadedAtTheStart:
    """The handle-point model is loaded with the others, before any detection
    runs: a pinned file that is missing or will not load stops the job at its
    start, not on every end after the expensive part."""

    def load(self, monkeypatch, spin, **kw):
        from curling_score import analyze
        from curling_score.detect import broommodel, sidemodel, spinmodel
        monkeypatch.setattr(broommodel, "default_model", lambda: object())
        monkeypatch.setattr(sidemodel, "default_model", lambda: object())
        monkeypatch.setattr(spinmodel, "default_model", spin)
        args = dict(skip_longview=False, skip_line=False)
        args.update(kw)
        return analyze.load_models(None, 448, None, progress=lambda *_: None, **args)

    def test_it_is_loaded_with_the_line_model(self, monkeypatch):
        calls = []
        self.load(monkeypatch, lambda: calls.append(1) or object())
        assert calls == [1]

    def test_no_line_no_spin_model(self, monkeypatch):
        calls = []
        self.load(monkeypatch, lambda: calls.append(1) or object(), skip_line=True)
        assert calls == []

    def test_one_that_will_not_load_stops_the_job_there(self, monkeypatch):
        def broken():
            raise FileNotFoundError("CURLING_SCORE_SPIN_WEIGHTS=/x/spin1.pt does not exist")
        with pytest.raises(FileNotFoundError):
            self.load(monkeypatch, broken)


class TestSlowRocks:
    """A rock showing under a turn in view is timed with its handle's swing
    held (spinfit.read_with_swing); one still in view and moving when the
    window ends is followed on for more of its turn."""

    RED = (0.226, -0.024)                      # spinfit.SWING["red"]

    def rock(self, speed):
        s = shot(speed=speed)
        s.hog_crossing = crossing(speed=speed)
        return s

    def test_a_slow_rock_is_read_with_the_swing_held(self):
        s = self.rock(1.5)
        run([s], Stubs(f=0.07, speed=1.5, amp=self.RED[0], centre=self.RED[1]))      # 4.2 rpm
        assert s.spin.rpm == pytest.approx(4.2, rel=0.12)

    def test_a_slow_rock_still_in_view_when_the_window_ends_is_followed_on(self):
        s = self.rock(1.2)                                   # at 18 m of the frame's 23 when it ends
        stubs = Stubs(f=0.07, speed=1.2, amp=self.RED[0], centre=self.RED[1])
        run([s], stubs)
        assert len(stubs.decoded) == 2
        assert stubs.decoded[1][0] == pytest.approx(stubs.decoded[0][1], abs=0.15)
        assert s.spin.samples > len([t for t in np.arange(*stubs.decoded[0], 0.1)
                                     if 5.0 + 1.2 * (t - 100.0) >= ST.FROM_M])

    def test_a_rock_gone_out_of_the_frame_is_not_followed_on(self):
        stubs = Stubs(f=0.07, speed=2.2, amp=self.RED[0], centre=self.RED[1])
        run([self.rock(2.2)], stubs)                         # past the bottom at 8 s
        assert len(stubs.decoded) == 1

    def test_a_rock_gone_out_of_the_frame_just_before_the_window_ends_is_not_followed_on(self):
        stubs = Stubs(f=0.07, speed=1.68, amp=self.RED[0], centre=self.RED[1])
        run([self.rock(1.68)], stubs)                        # at the frame's bottom 0.4 s before
        assert len(stubs.decoded) == 1

    def test_a_rock_refused_with_a_turn_or_more_in_view_is_not_followed_on(self):
        # Turning fast but too noisy to read: noise, not slowness -- more of
        # its track would not change that.
        stubs = Stubs(f=0.3, speed=1.2, amp=self.RED[0], centre=self.RED[1], noise=0.25)
        s = self.rock(1.2)
        run([s], stubs)
        assert not hasattr(s, "spin") and len(stubs.decoded) == 1

    def test_a_rock_read_in_full_is_not_followed_on(self):
        stubs = Stubs(f=0.2, speed=1.2)
        s = self.rock(1.2)
        run([s], stubs)
        assert len(stubs.decoded) == 1 and s.spin.rpm == pytest.approx(12.0, rel=0.05)


class TestSwingFor:
    """The swing a slow rock is timed with: its end's own, from the clean reads
    of its colour when there are enough, else the colour's usual one."""

    def read(self, swing):
        return spinfit.Spin(rpm=12.0, rpm_avg=12.0, turns_in_view=1.8, samples=80, swing=swing)

    def test_three_clean_reads_of_its_colour_give_the_end_s_own(self):
        firsts = [self.read((0.24, -0.01)), self.read((0.25, -0.03)), self.read((0.26, -0.02)), None]
        assert ST.swing_for("red", firsts) == pytest.approx((0.25, -0.02))

    def test_fewer_give_the_colour_s_usual_swing(self):
        firsts = [self.read((0.30, 0.0)), self.read(None), None]
        assert ST.swing_for("yellow", firsts) == spinfit.SWING["yellow"]
