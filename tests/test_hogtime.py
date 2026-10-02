"""Giving each shot the moment it crossed the throwing end's hog line.

Runs after the rules have settled the shot list, and like
``thinking.time_shots`` it can only attach a time to a rock already in it --
nothing here may add, drop or renumber a shot.
"""

import types

import pytest

from curling_score.detect.delivery import Delivery
from curling_score.detect import longview
from curling_score.detect.longview import Crossing
from curling_score.detect.release import Release
from curling_score.game import hogtime, shots as S
from curling_score.detect.rocks import Detection


def det(color, x, y):
    return Detection(color=color, x_m=x, y_m=y, x_px=0.0, y_px=0.0,
                     area_px=140.0, confidence=0.9)


def a_shot(n, color, t_rest, t_rel=None, t_enter=None):
    dv = Delivery(color=color, t_enter=t_enter if t_enter is not None else t_rest - 8,
                  t_rest=t_rest, entry_y_m=4.55, rest_x_m=0.0, rest_y_m=1.0,
                  travel_m=3.5, track=((t_rest - 8, 0.0, 4.55), (t_rest, 0.0, 1.0)))
    rel = None if t_rel is None else Release(
        color=color, t=t_rel, y_exit_m=3.0, speed_m_s=2.0,
        track=((t_rel, 0.05, -2.0), (t_rel + 2, 0.05, 2.0)))
    return S.Shot(number=n, color=color, stones=[det(color, 0.1, 0.2)],
                  t_rest_s=t_rest, delivery=dv, release=rel)


class Recorder:
    """Stands in for the detector, and remembers what it was asked."""

    def __init__(self, answer=None):
        self.calls = []
        self.answer = answer or (lambda c, lo, hi: Crossing(lo + 3.0, "ok"))

    def __call__(self, video, view, color, t0, t1, fps=30.0):
        self.calls.append((color, t0, t1))
        return self.answer(color, t0, t1)


class TestTiming:
    def test_a_shot_with_a_release_is_searched_around_it(self):
        shots = [a_shot(1, "red", 100.0, t_rel=80.0)]
        find = Recorder()
        hogtime.time_hog_crossings(shots, "v.mp4", object(), find=find)
        assert find.calls == [("red", 81.0, 86.5)]
        assert hogtime.crossing(shots[0]) == pytest.approx(84.0)

    def test_a_shot_with_no_release_is_searched_back_from_its_arrival(self):
        shots = [a_shot(1, "red", 100.0, t_enter=92.0)]
        find = Recorder()
        hogtime.time_hog_crossings(shots, "v.mp4", object(), find=find)
        (color, t0, t1), = find.calls
        assert color == "red"
        assert (t0, t1) == (92.0 - 20.0, 92.0 - 8.0)

    def test_a_refusal_leaves_the_shot_untimed_rather_than_guessing(self):
        shots = [a_shot(1, "red", 100.0, t_rel=80.0)]
        hogtime.time_hog_crossings(
            shots, "v.mp4", object(),
            find=Recorder(lambda c, lo, hi: Crossing(None, "two candidates")))
        assert hogtime.crossing(shots[0]) is None

    def test_a_missing_shot_is_not_searched_for_at_all(self):
        shots = [a_shot(1, "red", 100.0, t_rel=80.0)]
        shots[0].missing = True
        find = Recorder()
        hogtime.time_hog_crossings(shots, "v.mp4", object(), find=find)
        assert find.calls == []

    def test_it_never_changes_the_shot_list(self):
        shots = [a_shot(1, "red", 100.0, t_rel=80.0),
                 a_shot(2, "yellow", 160.0, t_rel=140.0)]
        before = [(s.number, s.color) for s in shots]
        hogtime.time_hog_crossings(shots, "v.mp4", object(), find=Recorder())
        assert [(s.number, s.color) for s in shots] == before
        assert len(shots) == 2


class TestWhichCamera:
    def test_the_camera_at_the_far_end_watches_the_throwing_house(self):
        assert hogtime.CAMERA_FOR["top"] == "left"
        assert hogtime.CAMERA_FOR["bottom"] == "right"


class TestTheDefaultFinder:
    """`find` resolves per call, not as a default argument value, so importing
    this module does not need a GPU, weights or ultralytics -- and a caller can
    still pass its own."""

    def test_an_explicit_find_is_used_unchanged(self):
        seen = []

        def find(video, view, color, t0, t1):
            seen.append(color)
            return longview.Crossing(t0 + 1.0, "ok", longview.KEY_OK)

        shot = types.SimpleNamespace(color="red", missing=False,
                                     release=types.SimpleNamespace(t=10.0))
        hogtime.time_hog_crossings([shot], "v.mp4", object(), find=find)
        assert seen == ["red"]
        # t0 is release.t + WINDOW_S[0] = 11.0, and the stub returns t0 + 1.
        assert shot.t_hog_s == pytest.approx(12.0)

    def test_it_falls_back_to_the_colour_scan_with_no_side_model(self, monkeypatch):
        monkeypatch.setattr(hogtime.sidemodel, "default_finder", lambda: None)
        calls = []
        monkeypatch.setattr(hogtime.longview, "find_crossing",
                            lambda *a, **k: calls.append(a) or
                            longview.Crossing(None, "no", longview.KEY_NO_CANDIDATE))
        shot = types.SimpleNamespace(color="red", missing=False,
                                     release=types.SimpleNamespace(t=10.0))
        hogtime.time_hog_crossings([shot], "v.mp4", object())
        assert calls, "the colour scan was not used as the fallback"

    def test_a_side_model_is_preferred_when_configured(self, monkeypatch):
        used = []
        monkeypatch.setattr(hogtime.sidemodel, "default_finder",
                            lambda: (lambda *a, **k: used.append(1) or
                                     longview.Crossing(11.0, "ok", longview.KEY_OK)))
        shot = types.SimpleNamespace(color="red", missing=False,
                                     release=types.SimpleNamespace(t=10.0))
        hogtime.time_hog_crossings([shot], "v.mp4", object())
        assert used and shot.t_hog_s == pytest.approx(11.0)


def test_the_crossing_is_kept_on_the_shot_even_when_refused():
    from types import SimpleNamespace
    from curling_score.detect import longview
    from curling_score.game import hogtime
    from curling_score.geometry.sideview import SideView
    view = SideView(rect=(0, 0, 810, 1080), tee_row=430.0, hog_row=520.0)
    refused = longview.Crossing(None, "speed 3.44 m/s is not a delivery", longview.KEY_BAD_SPEED,
                                track_key=3, samples=((1.0, 300.0, 510.0, 52.0),))
    shot = SimpleNamespace(missing=False, release=SimpleNamespace(t=0.0), delivery=None,
                           color="red", t_hog_s=None, v_hog_m_s=None, hog_crossing=None)
    hogtime.time_hog_crossings([shot], "v.mp4", view, find=lambda *a: refused)
    assert shot.hog_crossing is refused and shot.t_hog_s is None
