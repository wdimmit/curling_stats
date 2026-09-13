"""Thinking time: the clock each team runs between shots.

The clock starts once the previous stone has come to rest and the players have
had a moment to clear the ice, and stops when the next thrower's stone crosses
the tee line at the delivering end -- which is where World Curling stops it,
and also the one point of the delivery this footage sees without fail. The
existing release gate demands a 3.0 m climb from at most 0.6 m above a back
edge near -2.0 m, so a detected release has always crossed y = 0.

Everything it cannot see, it declines to guess: a shot with no release, an end's
first stone, a previous stone that never rested. Those are counted, because a
total assembled from an unknown fraction of an end is not a total.
"""

import pytest

from curling_score.detect.release import Release
from curling_score.game import thinking


def climbing(t0, y0=-2.0, y1=2.0, speed=2.0, fps=5.0):
    out, t, y = [], t0, y0
    while y <= y1 + 1e-9:
        out.append((round(t, 3), 0.05, round(y, 4)))
        y += speed / fps
        t += 1.0 / fps
    return tuple(out)


def rel(t0, color="red", speed=2.0):
    tr = climbing(t0, speed=speed)
    return Release(color=color, t=tr[0][0], y_exit_m=tr[-1][2],
                   speed_m_s=speed, track=tr)


class Shot:
    """Just enough of a shot for the clock."""

    def __init__(self, number, color, t_rest_s=None, release=None, missing=False):
        self.number, self.color = number, color
        self.t_rest_s = t_rest_s
        self.release = release
        self.missing = missing


def shot(n, color, rest, t_rel=None, speed=2.0):
    return Shot(n, color, t_rest_s=rest,
                release=None if t_rel is None else rel(t_rel, color, speed))


class TestOneInterval:
    def test_runs_from_the_grace_period_to_the_tee_line(self):
        # red rests at 100; yellow's stone starts at -2.0 m at t=140 doing
        # 2 m/s, so it crosses the tee line at 141.0
        end = thinking.for_end([shot(1, "red", 100.0),
                                shot(2, "yellow", 160.0, t_rel=140.0)])
        assert end.by_color["yellow"] == pytest.approx(
            141.0 - (100.0 + thinking.GRACE_S), abs=0.02)
        assert end.by_color["red"] == 0.0

    def test_the_grace_period_is_five_seconds(self):
        assert thinking.GRACE_S == 5.0

    def test_charges_the_team_about_to_throw(self):
        end = thinking.for_end([shot(1, "red", 100.0),
                                shot(2, "yellow", 160.0, t_rel=140.0)])
        assert end.by_color["yellow"] > 0
        assert end.by_color["red"] == 0.0

    def test_the_clock_stops_at_the_tee_line_not_at_first_sighting(self):
        """First sighting is already mid-slide, a second back down the sheet."""
        end = thinking.for_end([shot(1, "red", 100.0),
                                shot(2, "yellow", 160.0, t_rel=140.0)])
        # 141.0 (tee) rather than 140.0 (first seen at -2.0 m)
        assert end.by_color["yellow"] == pytest.approx(36.0, abs=0.02)


class TestWhatItRefusesToGuess:
    def test_the_first_shot_of_an_end_is_never_charged(self):
        end = thinking.for_end([shot(1, "red", 100.0, t_rel=80.0),
                                shot(2, "yellow", 160.0, t_rel=140.0)])
        assert end.measured_shots == 1
        assert end.unmeasured_shots == 1

    def test_a_shot_with_no_release_is_unmeasured(self):
        end = thinking.for_end([shot(1, "red", 100.0),
                                shot(2, "yellow", 160.0)])
        assert end.by_color["yellow"] == 0.0
        assert end.measured_shots == 0
        assert end.unmeasured_shots == 2

    def test_a_previous_shot_that_never_rested_is_unmeasured(self):
        end = thinking.for_end([shot(1, "red", None),
                                shot(2, "yellow", 160.0, t_rel=140.0)])
        assert end.by_color["yellow"] == 0.0
        assert end.unmeasured_shots == 2

    def test_a_missing_shot_breaks_the_chain_rather_than_spanning_it(self):
        shots = [shot(1, "red", 100.0),
                 Shot(2, "yellow", missing=True),
                 shot(3, "red", 220.0, t_rel=200.0)]
        end = thinking.for_end(shots)
        assert end.by_color["red"] == 0.0
        assert end.measured_shots == 0

    def test_a_nan_rest_time_is_treated_as_no_rest(self):
        end = thinking.for_end([shot(1, "red", float("nan")),
                                shot(2, "yellow", 160.0, t_rel=140.0)])
        assert end.by_color["yellow"] == 0.0


class TestAnomalies:
    def test_a_release_before_the_grace_period_clamps_to_zero(self):
        # red rests at 100, yellow crosses the tee at 101 -- inside the grace
        end = thinking.for_end([shot(1, "red", 100.0),
                                shot(2, "yellow", 160.0, t_rel=100.0)])
        assert end.by_color["yellow"] == 0.0
        assert end.anomalies == 1

    def test_a_clean_end_reports_no_anomalies(self):
        end = thinking.for_end([shot(1, "red", 100.0),
                                shot(2, "yellow", 160.0, t_rel=140.0)])
        assert end.anomalies == 0


class TestTotals:
    def test_an_empty_end_totals_zero(self):
        end = thinking.for_end([])
        assert end.by_color == {"red": 0.0, "yellow": 0.0}
        assert end.measured_shots == 0

    def test_per_shot_seconds_line_up_with_the_totals(self):
        shots = [shot(1, "red", 100.0),
                 shot(2, "yellow", 160.0, t_rel=140.0),
                 shot(3, "red", 220.0, t_rel=200.0)]
        end = thinking.for_end(shots)
        assert end.per_shot[0] is None
        assert sum(s for s in end.per_shot if s) == pytest.approx(
            end.by_color["red"] + end.by_color["yellow"], abs=1e-9)

    def test_games_add_their_ends_up(self):
        shots = [shot(1, "red", 100.0), shot(2, "yellow", 160.0, t_rel=140.0)]
        one = thinking.for_end(shots)
        both = thinking.for_game([shots, shots])
        assert both.by_color["yellow"] == pytest.approx(2 * one.by_color["yellow"])
        assert both.measured_shots == 2 * one.measured_shots
