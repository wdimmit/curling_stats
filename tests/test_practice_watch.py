"""One sheet's practice, watched step by step, with no video."""

from types import SimpleNamespace

import pytest

from curling_score.detect.release import Release
from curling_score.geometry.calibrate import CalibrationError
from curling_score.live.session import Calibration
from curling_score.practice import enrich as E
from curling_score.practice import releases as R
from curling_score.practice import watch as W
from tests.test_delivery import det, thrown
from tests.test_practice_finder import sheet


def cal(complete=True):
    setups = {h: SimpleNamespace(hog_line="paint", view_x_limit_m=2.2, view_y_min_m=-4.0)
              for h in W.HOUSES}
    if not complete:
        setups["bottom"].hog_line = None
    return Calibration(panels=None, setups=setups, sideviews=None, until_s=0.0,
                       side_expected=False)


class Rec:
    path = "rec.ts"

    def __init__(self, head=0.0):
        self.head, self.over = head, False

    def head_s(self):
        return self.head

    def ended(self):
        return self.over


class Pipeline:
    def __init__(self, top=(), bottom=(), cals=None):
        self.frames = {"top": list(top), "bottom": list(bottom)}
        self.cals = list(cals) if cals else [cal()]
        self.calibrated, self.spans = [], []

    def calibrate(self, path, until_s):
        self.calibrated.append(until_s)
        got = self.cals.pop(0) if len(self.cals) > 1 else self.cals[0]
        if isinstance(got, Exception):
            raise got
        return got

    def detect(self, path, setups, from_s, until_s, detector):
        self.spans.append((from_s, until_s))
        return {h: [f for f in self.frames[h] if from_s <= f[0] <= until_s] for h in W.HOUSES}

    def enrich(self, path, cal, models, *, house, arrival, release, house_frames,
               throw_frames):
        return E.build_shot(arrival, release, house_frames)


@pytest.fixture(autouse=True)
def releases_behind_the_hack(monkeypatch):
    """A stand-in for the release finder: a release wherever a frame holds a
    stone behind the hack line, which only a throwing panel shows here."""
    monkeypatch.setattr(R.release, "find_releases", lambda frames, y, x=None: [
        Release(d.color, t, 1.0, 2.0) for t, ds in frames for d in ds if d.y_m < -3.5])


def hack(color, t):
    return [(t, det(color, 0.0, -3.6))]


def watched(pipeline, since_s=20.0, until_s=100.0, t0_s=0.0, clock=None):
    rec, docs = Rec(), []
    w = W.PracticeWatch(recording=rec, pipeline=pipeline, models=SimpleNamespace(detector=None),
                        since_s=since_s, t0_s=t0_s, publish=docs.append,
                        clock=clock or (lambda: 0.0))
    while rec.head < until_s:
        rec.head = min(until_s, rec.head + 1.0)
        while w.step():
            pass
    rec.over = True
    while w.step():
        pass
    return w, docs


def test_a_throw_is_published_once_with_its_release():
    w, docs = watched(Pipeline(top=sheet(thrown("red", t0=40.0), until_s=100.0),
                               bottom=sheet(hack("red", 22.0), until_s=100.0)),
                      t0_s=1000.0)
    assert len(w.throws) == 1
    th = w.throws[0]
    assert (th["house"], th["color"], th["arrived"]) == ("top", "red", True)
    assert th["t_release_s"] == 1022.0 and th["release_source"] == "overhead"
    assert docs[-1]["status"] == "ended" and w.done
    assert docs[-1]["since_s"] == 1020.0 and docs[-1]["t0_s"] == 1000.0


def test_a_release_that_never_arrives_becomes_a_throw_a_minute_later():
    w, _ = watched(Pipeline(top=sheet(until_s=100.0),
                            bottom=sheet(hack("yellow", 30.0), until_s=100.0)))
    assert [(t["house"], t["color"], t["arrived"]) for t in w.throws] == [
        ("top", "yellow", False)]


def test_an_arrival_nothing_released_is_reported_without_a_release():
    w, _ = watched(Pipeline(top=sheet(thrown("red", t0=40.0), until_s=100.0),
                            bottom=sheet(until_s=100.0)))
    assert len(w.throws) == 1
    assert w.throws[0]["release_source"] is None and w.throws[0]["t_release_s"] is None


def test_a_throw_released_before_start_is_left_out():
    top = sheet(thrown("red", t0=66.0, x=0.2), thrown("red", t0=96.0, x=-0.6), until_s=140.0)
    bottom = sheet(hack("red", 50.0), hack("red", 80.0), until_s=140.0)
    w, _ = watched(Pipeline(top=top, bottom=bottom), since_s=60.0, until_s=140.0)
    assert [t["t_release_s"] for t in w.throws] == [80.0]


def test_it_calibrates_only_once_the_lookback_is_there():
    p, rec = Pipeline(), Rec(head=10.0)
    w = W.PracticeWatch(recording=rec, pipeline=p, models=None, since_s=20.0,
                        clock=lambda: 0.0)
    assert w.step() is False and p.calibrated == []
    rec.head = 20.0
    assert w.step() is True
    assert p.calibrated == [20.0] and w.status == "watching"


def test_an_incomplete_calibration_is_tried_again_a_minute_later():
    now = [0.0]
    p, rec = Pipeline(cals=[cal(complete=False), cal()]), Rec(head=20.0)
    w = W.PracticeWatch(recording=rec, pipeline=p, models=None, since_s=20.0,
                        clock=lambda: now[0])
    assert w.step() is True and w.status == "calibrating"
    assert w.step() is False                       # not yet a minute
    now[0] = 60.0
    assert w.step() is True
    assert len(p.calibrated) == 2 and w.status == "watching" and w.cal.complete


def test_it_gives_up_after_its_calibration_tries():
    now, docs = [0.0], []
    p, rec = Pipeline(cals=[CalibrationError("no panels")]), Rec(head=20.0)
    w = W.PracticeWatch(recording=rec, pipeline=p, models=None, since_s=20.0,
                        publish=docs.append, clock=lambda: now[0])
    for _ in range(W.CALIB_TRIES):
        assert w.step() is True
        now[0] += W.CALIB_RETRY_S
    assert w.done and w.status == "failed" and docs[-1]["status"] == "failed"
    assert w.step() is False


def test_told_to_stop_it_reads_what_is_recorded_then_ends():
    p, rec, docs = Pipeline(), Rec(head=20.0), []
    w = W.PracticeWatch(recording=rec, pipeline=p, models=SimpleNamespace(detector=None),
                        since_s=20.0, publish=docs.append, clock=lambda: 0.0)
    w.step()                                       # calibrated
    rec.head = 31.0
    w.request_stop()
    while w.step():
        pass
    assert w.done and w.status == "ended" and docs[-1]["status"] == "ended"
    assert p.spans[-1][1] == 29.0                  # read to the head, less its margin


def test_a_stop_while_calibrating_ends_it():
    p, rec = Pipeline(), Rec(head=5.0)             # the lookback is still arriving
    w = W.PracticeWatch(recording=rec, pipeline=p, models=None, since_s=20.0,
                        clock=lambda: 0.0)
    w.request_stop()
    assert w.step() is True
    assert w.done and w.status == "ended" and p.calibrated == []


def test_the_document_says_when_the_recording_began_by_the_wall_clock():
    w = W.PracticeWatch(recording=Rec(), pipeline=Pipeline(), models=None, since_s=20.0,
                        wall_t0=1760000000.0)
    assert w.document()["wall_t0"] == 1760000000.0


def test_a_long_catch_up_is_read_in_pieces_from_the_look_back():
    p = Pipeline()
    rec = Rec(head=500.0)
    w = W.PracticeWatch(recording=rec, pipeline=p, models=SimpleNamespace(detector=None),
                        since_s=100.0, clock=lambda: 0.0)
    while w.step():
        pass
    assert p.spans[0][0] == 64.0                   # Start less a delivery's look-back
    assert all(b - a <= W.STEP_MAX_S for a, b in p.spans)
    assert all(p.spans[i][1] == p.spans[i + 1][0] for i in range(len(p.spans) - 1))
    assert p.spans[-1][1] == 498.0                 # just behind the head
