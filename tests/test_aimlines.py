"""The slide and release lines, read at the broom, and the set between them."""
from types import SimpleNamespace

import numpy as np
import pytest

from curling_score.game import aimlines as A
from curling_score.game import linetime as L
from curling_score.game.broomtime import TargetBroom

TEE_Y = L.TEE_Y
IN = 0.0254


def delivery_between(lo, hi, step=0.05, x=lambda d: 0.0):
    """Delivery samples (t, y, x) every ``step`` metres from ``lo`` to ``hi`` past the tee."""
    ds = np.arange(lo, hi + 1e-9, step)
    return tuple((i / 30.0, TEE_Y - d, x(d)) for i, d in enumerate(ds))


def track_between(lo, hi, step=0.05, x=lambda d: 0.0):
    """hog_track samples (t, x, y, yp)."""
    ds = np.arange(lo, hi + 1e-9, step)
    return tuple((10.0 + i / 30.0, x(d), TEE_Y - d, d) for i, d in enumerate(ds))


def line_with(delivery=(), release_d=None, track=(), curl="right"):
    release = None if release_d is None else (1.0, TEE_Y - release_d, 0.0)
    return L.Line(start=None, at_hog_x=0.0, at_hog_offset=None, at_broom_x=0.0, miss=0.0,
                  curl=curl, side=None, hog_path=(), path=(), fit_n=30, fit_rms=0.001,
                  delivery=delivery, release=release, track=track)


def d_of(points):
    return [round(TEE_Y - y, 6) for y, _x in points]


class TestWindows:
    def test_slide_window_runs_from_halfway_to_the_back_line_to_the_release(self):
        line = line_with(delivery_between(-3.2, 7.9), release_d=5.5)
        want = [round(TEE_Y - y, 6) for _t, y, _x in line.delivery
                if A.SLIDE_FROM_M <= TEE_Y - y <= 5.5]
        assert A.SLIDE_FROM_M == pytest.approx(-2.7435)
        assert d_of(A.slide_points(line)) == want
        assert min(want) >= -2.7435 and max(want) <= 5.5

    def test_release_window_is_nine_feet_past_the_release(self):
        line = line_with(delivery_between(-3.2, 7.9), release_d=4.0)
        got = d_of(A.release_points(line))
        assert min(got) >= 4.0 and max(got) <= 4.0 + 2.7432
        assert max(got) > 6.6                           # the whole 9 ft, from the delivery

    def test_release_window_continues_on_the_track_past_the_delivery(self):
        line = line_with(delivery_between(-3.2, 7.9, x=lambda d: 0.0), release_d=6.5,
                         track=track_between(6.4, 10.0, x=lambda d: 1.0))
        pts = A.release_points(line)
        from_delivery = [TEE_Y - y for y, x in pts if x == 0.0]
        from_track = [TEE_Y - y for y, x in pts if x == 1.0]
        assert min(from_delivery) >= 6.5 and max(from_delivery) <= 7.9 + 1e-9
        assert min(from_track) > 7.9 and max(from_track) <= 6.5 + 2.7432
        assert len(set(round(d, 6) for d in from_delivery + from_track)) == len(pts)

    def test_no_release_point_has_no_windows(self):
        line = line_with(delivery_between(-3.2, 7.9))
        assert A.slide_points(line) == [] and A.release_points(line) == []


def on_line(a, b, ys, noise=()):
    noise = list(noise) or [0.0] * len(ys)
    return [(y, a + b * y + e) for y, e in zip(ys, noise)]


class TestFitAim:
    def test_a_straight_slide_lands_where_it_points(self):
        pts = on_line(0.1 + 0.2, -0.01, np.linspace(23.0, 20.0, 40))   # x = 0.1 - 0.01 (y - 20)
        aim = A.fit_aim(pts, A.SLIDE_MIN_N, A.SLIDE_MIN_SPAN_M, (-0.5, 0.0), "right")
        assert aim.at_broom_x == pytest.approx(0.3)
        assert aim.miss == pytest.approx(0.8)
        assert aim.side == L.side_of(0.8, "right")
        assert aim.at_tee_x is None and aim.shown is True

    def test_correlated_residuals_shrink_the_effective_count(self):
        ys = np.linspace(23.0, 20.0, 60)
        wobble = 0.01 * np.sin(np.linspace(0, 2 * np.pi, 60))       # slow: neighbours agree
        aim = A.fit_aim(on_line(0.0, 0.0, ys, wobble), 15, 2.0, (0.0, 0.0), "right")
        assert aim.n == 60 and aim.n_eff < 10
        r = wobble - np.polyval(np.polyfit(ys, wobble, 1), ys)
        rms = np.sqrt(np.sum(r ** 2) / (60 - 2))
        independent = rms * np.sqrt(1 / 60 + (0.0 - ys.mean()) ** 2 / np.sum((ys - ys.mean()) ** 2))
        assert aim.se_m > 2 * independent

    def test_too_few_or_too_short_is_no_line(self):
        assert A.fit_aim(on_line(0.0, 0.0, np.linspace(23, 20, 7)), 8, 2.0, (0.0, 0.0), None) is None
        assert A.fit_aim(on_line(0.0, 0.0, np.linspace(23, 21.5, 20)), 8, 2.0, (0.0, 0.0), None) is None

    def test_too_uncertain_is_not_shown(self):
        ys = np.linspace(32.0, 30.0, 10)
        jitter = [0.05 * (-1) ** i for i in range(10)]
        aim = A.fit_aim(on_line(0.0, 0.0, ys, jitter), 8, 2.0, (0.0, 0.0), "right")
        assert aim.se_m > A.SE_MAX_M and aim.shown is False

    def test_broomless_lines_are_pinned_at_the_tee(self):
        aim = A.fit_aim(on_line(-0.2, 0.01, np.linspace(23, 20, 40)), 8, 2.0, None, "left")
        assert (aim.at_broom_x, aim.miss, aim.side) == (None, None, None)
        assert aim.at_tee_x == pytest.approx(-0.2)


def aim(miss, se=0.01, shown=True):
    return A.AimLine(a=0.0, b=0.0, n=30, n_eff=30.0, rms=0.001, se_m=se, at_broom_x=miss,
                     miss=miss, side=None, at_tee_x=None, shown=shown)


class TestAimSet:
    def test_set_is_signed_toward_wide(self):
        # Curling right: slide 3" narrow (right of the broom), release 4" wide (left).
        s = A.aim_set(aim(3 * IN), aim(-4 * IN), "right")
        assert s.m == pytest.approx(7 * IN) and s.shown is True
        assert s.se_m == pytest.approx(np.hypot(0.01, 0.01))

    def test_set_inside_two_sigma_is_not_shown(self):
        assert A.aim_set(aim(3 * IN, se=0.1), aim(-4 * IN, se=0.1), "right").shown is False

    def test_a_line_not_shown_shows_no_set(self):
        assert A.aim_set(aim(3 * IN, shown=False), aim(-4 * IN), "right").shown is False

    def test_without_a_curl_the_set_is_signed_right(self):
        assert A.aim_set(aim(3 * IN), aim(-4 * IN), None).m == pytest.approx(-7 * IN)

    def test_no_set_without_both_lines_at_a_broom(self):
        assert A.aim_set(None, aim(0.0), "right") is None
        assert A.aim_set(aim(None), aim(0.0), "right") is None


def shot_with(line, broom=TargetBroom(-0.5, 0.0, 1.0, 0.9)):
    return SimpleNamespace(number=1, line=line, target_broom=broom)


class TestAttach:
    def straight_line(self, release_d=5.5):
        x = lambda d: 0.001 * d                          # noqa: E731
        return line_with(delivery_between(-3.2, 7.9, x=x), release_d=release_d,
                         track=track_between(6.4, 10.0, x=x))

    def test_a_rock_with_a_release_gets_both_lines_and_a_set(self):
        shot = shot_with(self.straight_line())
        assert A.attach_aim_lines([shot]) == 1
        assert shot.line.slide is not None and shot.line.release_line is not None
        assert shot.line.set is not None

    def test_no_release_point_gives_no_slide_or_release(self):
        shot = shot_with(self.straight_line(release_d=None))
        assert A.attach_aim_lines([shot]) == 0
        assert (shot.line.slide, shot.line.release_line, shot.line.set) == (None, None, None)

    def test_attach_is_attach_only(self, monkeypatch):
        bare = SimpleNamespace(number=2, line=None, target_broom=None)
        shot = shot_with(self.straight_line())
        before = shot.line

        def boom(*a, **k):
            raise RuntimeError("boom")
        monkeypatch.setattr(A, "fit_aim", boom)
        assert A.attach_aim_lines([bare, shot]) == 0
        assert shot.line is before and bare.line is None


class TestJson:
    def test_json_shapes(self):
        a = aim(0.1)
        assert A.aim_json(a) == {"at_broom": {"x": 0.1, "miss_m": 0.1}, "side": None, "shown": True,
                                 "fit": {"n": 30, "n_eff": 30.0, "rms_m": 0.001, "se_m": 0.01}}
        tee = A.AimLine(a=-0.2, b=0.0, n=9, n_eff=4.5, rms=0.002, se_m=0.03, at_broom_x=None,
                        miss=None, side=None, at_tee_x=-0.2, shown=True)
        assert A.aim_json(tee)["at_broom"] is None and A.aim_json(tee)["at_tee"] == {"x": -0.2}
        assert A.set_json(A.AimSet(m=0.17781, se_m=0.0141, shown=True)) == \
            {"m": 0.1778, "se_m": 0.0141, "shown": True}
        assert A.aim_json(None) is None and A.set_json(None) is None
