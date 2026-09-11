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
