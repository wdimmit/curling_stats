"""Deliveries found while the recording is still growing, each reported once."""

from types import SimpleNamespace

from curling_score.practice.finder import ArrivalFinder, Buffer
from tests.test_delivery import det, thrown

SETUP = SimpleNamespace(view_x_limit_m=None, view_y_min_m=None)


def sheet(*traces, until_s, fps=5.0):
    """Every frame from 0 to ``until_s``, each holding what ``traces`` put there."""
    frames = {round(i / fps, 3): [] for i in range(int(until_s * fps) + 1)}
    for tr in traces:
        for t, d in tr:
            frames.setdefault(round(t, 3), []).append(d)
    return sorted(frames.items())


def departing(color, t0, fps=5.0, speed=2.0):
    """A stone leaving this house for the other end, as at a release."""
    out, t, y = [], t0, -3.6
    while y < 6.5:
        out.append((t, det(color, 0.0, y)))
        y += speed / fps
        t += 1.0 / fps
    return out


def feed(frames, step_s=1.0):
    """Hand ``frames`` over a second at a time, as the watch does, and collect
    everything the finder reports."""
    buf, finder, got = Buffer(), ArrivalFinder(SETUP), []
    t = frames[0][0]
    while t <= frames[-1][0] + step_s:
        buf.extend([f for f in frames if f[0] <= t])
        got.extend(finder.new(buf))
        t += step_s
    return got


class TestBuffer:
    def test_it_keeps_only_newer_frames_and_forgets_old_ones(self):
        buf = Buffer(keep_s=10.0)
        buf.extend([(0.0, []), (1.0, [])])
        buf.extend([(1.0, ["again"]), (2.0, [])])
        assert [t for t, _ in buf.frames] == [0.0, 1.0, 2.0]
        buf.extend([(t / 1.0, []) for t in range(3, 15)])
        assert buf.start_s == 4.0 and buf.head_s == 14.0


class TestArrivalFinder:
    def test_a_delivery_is_reported_once_as_the_window_slides(self):
        got = feed(sheet(thrown("red", t0=40.0), until_s=90.0))
        assert len(got) == 1
        assert got[0].color == "red" and abs(got[0].rest_y_m - (-0.9)) < 0.11

    def test_two_deliveries_are_reported_in_order(self):
        got = feed(sheet(thrown("red", t0=40.0, x=0.2),
                         thrown("yellow", t0=80.0, x=-0.5, y1=0.5), until_s=130.0))
        assert [d.color for d in got] == ["red", "yellow"]

    def test_one_with_too_little_window_behind_it_is_not_judged(self):
        # The buffer starts at 0, so a stone entering at 10 s has no look-back.
        assert feed(sheet(thrown("red", t0=10.0), until_s=60.0)) == []

    def test_a_stone_leaving_for_the_other_end_is_not_a_delivery(self):
        assert feed(sheet(departing("red", t0=40.0), until_s=60.0)) == []

    def test_the_same_colour_entering_within_seconds_is_the_same_delivery(self):
        finder = ArrivalFinder(SETUP)
        a = SimpleNamespace(color="red", t_enter=40.0, t_rest=49.4)
        b = SimpleNamespace(color="red", t_enter=41.5, t_rest=50.0)
        c = SimpleNamespace(color="yellow", t_enter=41.5, t_rest=50.0)
        finder.reported.append(a)
        assert finder._reported(b) and not finder._reported(c)

    def test_a_stone_seen_again_just_after_it_rested_is_not_a_new_throw(self):
        # VXU9 8638.5, 9310.1, 9789.3: a few frames of the same colour moving
        # 0.3-3 s after a reported stone came to rest -- a fragment, a sweeper
        # nudging it -- that the house read then put on that very stone.
        finder = ArrivalFinder(SETUP)
        finder.reported.append(SimpleNamespace(color="red", t_enter=40.0, t_rest=49.4,
                                               entry_y_m=4.5))
        after = SimpleNamespace(color="red", t_enter=49.7, t_rest=52.0, entry_y_m=2.9)
        later = SimpleNamespace(color="red", t_enter=75.0, t_rest=84.0, entry_y_m=2.9)
        assert finder._reported(after) and not finder._reported(later)

    def test_a_throw_coming_in_from_the_top_just_after_a_rest_is_a_new_throw(self):
        # Partners throwing one colour without waiting: the next stone comes
        # into view at the top of the panel, as every real arrival on VXU9 did
        # (4.44-4.57 m), seconds after the last one stopped.
        finder = ArrivalFinder(SETUP)
        finder.reported.append(SimpleNamespace(color="red", t_enter=40.0, t_rest=49.4,
                                               entry_y_m=4.5))
        rapid = SimpleNamespace(color="red", t_enter=52.0, t_rest=60.0, entry_y_m=4.5)
        assert not finder._reported(rapid)
