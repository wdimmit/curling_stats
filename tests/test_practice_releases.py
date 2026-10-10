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


def test_a_release_is_found_once_as_the_buffer_front_passes_through_its_climb():
    # The real finder, over a stone leaving the hack at 1.8 m/s. Once the
    # buffer's front edge cuts into the climb, what is left still crosses the
    # stage-1 line at a legal speed -- the same throw, first seen 1.2 s later.
    from curling_score.detect.rocks import Detection
    from curling_score.practice.finder import Buffer

    def stone(y):
        return Detection(color="red", x_m=0.0, y_m=y, x_px=0.0, y_px=0.0,
                         area_px=140.0, confidence=0.9)

    frames = []
    for i in range(0, 40 * 5):
        t = round(i / 5, 3)
        y = -3.6 + 1.8 * (t - 19.7)
        frames.append((t, [stone(y)] if t >= 19.7 and y < 6.5 else []))
    buf, b = Buffer(keep_s=10.0), R.ReleaseBook(SETUP)
    for head in range(1, 41):
        buf.extend([f for f in frames if f[0] <= head])
        b.update(buf.frames)
    assert [round(r.t, 1) for r in b.releases] == [19.8]


def test_a_claimed_release_is_never_given_up(monkeypatch):
    b = book(monkeypatch, [rel("red", 22.0)])
    b.claim(arrival("red", 40.0))
    assert b.unarrived(1000.0) == []
