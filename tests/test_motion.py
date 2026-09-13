import pytest

from curling_score.detect.rocks import Detection
from curling_score.harvest import motion


def det(color, x, y):
    return Detection(color=color, x_m=x, y_m=y, x_px=0.0, y_px=0.0,
                     area_px=100.0, confidence=0.9)


def travelling(color="red", y0=6.0, y1=0.0, n=30, fps=5.0, t0=100.0, x=0.2):
    """A stone crossing the panel down-sheet at a steady rate."""
    return [(t0 + i / fps, [det(color, x, y0 + (y1 - y0) * i / (n - 1))])
            for i in range(n)]


def milling(color="red", n=30, fps=5.0, t0=100.0):
    """A person: the same path length, but ending where they started."""
    out = []
    for i in range(n):
        y = 1.0 + (1.0 if (i // 3) % 2 else -1.0) * (i % 3) * 0.35
        out.append((t0 + i / fps, [det(color, 0.2, y)]))
    return out


class TestFindFlights:
    def test_finds_a_stone_crossing_the_panel(self):
        (flight,) = motion.find_flights(travelling())
        assert flight.color == "red"
        assert flight.travel_m == pytest.approx(6.0, abs=0.1)

    def test_ignores_a_person_covering_the_same_ground(self):
        # Sweepers and players move constantly. What separates them from a
        # delivery is that they end up where they started.
        assert motion.find_flights(milling()) == []

    def test_ignores_a_flicker_too_brief_to_be_a_delivery(self):
        # At 5 fps a sample floor tuned for 2 fps is only a moment, so the
        # test has to be in seconds or noise clears it.
        assert motion.find_flights(travelling(n=9)) == []

    def test_ignores_a_stone_that_barely_moves(self):
        assert motion.find_flights(travelling(y0=1.0, y1=0.6)) == []

    def test_finds_both_colours_in_one_clip(self):
        seq = {}
        for t, dets in travelling("red", x=-0.5) + travelling("yellow", x=1.2):
            seq.setdefault(t, []).extend(dets)
        found = motion.find_flights(sorted(seq.items()))
        assert sorted(f.color for f in found) == ["red", "yellow"]

    def test_reports_absolute_times(self):
        # The clip's own timeline is useless for naming a frame or linking to
        # the video; only absolute source time is.
        (flight,) = motion.find_flights(travelling(t0=3204.0))
        assert flight.ts[0] == pytest.approx(3204.0)


class TestFlightTimes:
    def test_spans_the_flight_from_end_to_end(self):
        (flight,) = motion.find_flights(travelling(t0=100.0, n=30, fps=5.0))
        times = motion.flight_times(flight, n=3)
        assert times[0] == pytest.approx(flight.ts[0])
        assert times[-1] == pytest.approx(flight.ts[-1])

    def test_returns_the_requested_count(self):
        (flight,) = motion.find_flights(travelling())
        assert len(motion.flight_times(flight, n=4)) == 4

    def test_the_frames_are_far_enough_apart_to_differ(self):
        # A stone at ~2 m/s over ~62 px/m moves ~124 px/s, and it is ~18 px
        # across, so consecutive picks must be well over a tenth of a second
        # apart or they show the same thing twice.
        (flight,) = motion.find_flights(travelling())
        times = motion.flight_times(flight, n=3)
        assert min(b - a for a, b in zip(times, times[1:])) > 0.2

    def test_never_returns_more_times_than_it_has_samples(self):
        (flight,) = motion.find_flights(travelling(n=12, fps=2.0))
        assert len(motion.flight_times(flight, n=99)) <= len(flight.ts)


def leaving(color="red", y0=-2.2, y1=2.6, speed=2.0, fps=5.0, t0=300.0, x=0.1):
    """A stone climbing out of the thrower's own house: a delivery, seen early."""
    out, t, y = [], t0, y0
    while y <= y1 + 1e-9:
        out.append((round(t, 3), [det(color, x, y)]))
        y += speed / fps
        t += 1.0 / fps
    return out


VIEW_Y_MIN = -2.25


class TestFindThrows:
    """The throw is the one thing the harvest has never been able to select.

    ``is_flight`` requires net *down-sheet* travel, so a release -- which
    climbs -- fails it by construction, and every frame in the motion bin is
    an arrival. These frames are what the model has never been shown.
    """

    def test_a_release_is_not_a_flight(self):
        assert motion.find_flights(leaving()) == []

    def test_finds_a_stone_leaving_the_thrower_s_house(self):
        (throw,) = motion.find_throws(leaving(), VIEW_Y_MIN)
        assert throw.color == "red"
        assert throw.ts[0] == pytest.approx(300.0)
        assert throw.ys[-1] > throw.ys[0]

    def test_ignores_a_sweeper_starting_mid_panel(self):
        # The entry test is the whole defence against a red-jacketed sweeper
        # running up-sheet beside the stone.
        assert motion.find_throws(leaving(y0=0.5, y1=4.0), VIEW_Y_MIN) == []

    def test_ignores_an_arrival_running_the_other_way(self):
        assert motion.find_throws(travelling(), VIEW_Y_MIN) == []

    def test_throw_times_span_the_climb(self):
        (throw,) = motion.find_throws(leaving(), VIEW_Y_MIN)
        picks = motion.flight_times(throw, 3)
        assert len(picks) == 3
        assert picks[0] == throw.ts[0]
        assert picks[-1] == throw.ts[-1]

    def test_it_reuses_the_one_definition_of_a_release(self):
        """Two definitions of a throw would drift; there must only be one."""
        from curling_score.detect import release
        fs = leaving()
        assert len(motion.find_throws(fs, VIEW_Y_MIN)) == len(
            release.find_releases(fs, VIEW_Y_MIN))
