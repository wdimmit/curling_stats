"""Releases seen leaving the thrower's house, each accounted for once."""

from types import SimpleNamespace

from curling_score.detect.release import Release
from curling_score.practice import releases as R

SETUP = SimpleNamespace(view_y_min_m=-4.0, view_x_limit_m=2.2)


def book(monkeypatch, *found):
    """A book whose panel showed ``found``; each update hands over the next batch."""
    batches = [list(b) for b in found]
    monkeypatch.setattr(R.release, "find_releases",
                        lambda frames, y_min, x_limit=None: batches.pop(0) if batches else [])
    b = R.ReleaseBook(SETUP)
    for _ in found:
        b.update([(0.0, [])])
    return b


def rel(color, t):
    return Release(color, t, 1.0, 2.0)


def arrival(color, t_enter):
    return SimpleNamespace(color=color, t_enter=t_enter)


def test_frames_are_thinned_to_the_rate_the_finder_was_tuned_at():
    frames = [(i / 10, []) for i in range(20)]
    assert [t for t, _ in R.on_grid(frames, 5.0)] == [i / 5 for i in range(10)]


def test_a_release_found_again_is_kept_once(monkeypatch):
    b = book(monkeypatch, [rel("red", 10.0)], [rel("red", 10.4), rel("yellow", 10.2)])
    assert [(r.color, r.t) for r in b.releases] == [("red", 10.0), ("yellow", 10.2)]


def test_an_arrival_claims_its_release_once(monkeypatch):
    b = book(monkeypatch, [rel("red", 22.0)])
    assert b.claim(arrival("red", 40.0)).t == 22.0
    assert b.claim(arrival("red", 41.0)) is None


def test_an_arrival_takes_the_release_nearest_a_typical_flight(monkeypatch):
    b = book(monkeypatch, [rel("red", 15.0), rel("red", 22.0), rel("yellow", 23.0)])
    assert b.claim(arrival("red", 40.0)).t == 22.0       # lags 25 and 18: 18 is nearer 17


def test_no_claim_outside_the_lag_window(monkeypatch):
    b = book(monkeypatch, [rel("red", 22.0)])
    assert b.claim(arrival("red", 25.0)) is None          # 3 s: too soon to have flown
    assert b.claim(arrival("red", 60.0)) is None          # 38 s: too long


def test_a_release_waits_a_minute_for_its_arrival(monkeypatch):
    b = book(monkeypatch, [rel("yellow", 30.0)])
    assert b.unarrived(89.0) == []
    assert [r.t for r in b.unarrived(90.0)] == [30.0]
    assert b.unarrived(200.0) == []                       # given up on once


def test_a_claimed_release_is_never_given_up(monkeypatch):
    b = book(monkeypatch, [rel("red", 22.0)])
    b.claim(arrival("red", 40.0))
    assert b.unarrived(1000.0) == []
