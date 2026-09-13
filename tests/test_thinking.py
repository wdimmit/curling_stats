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


# --------------------------------------------------------------------- timing
# What the clock can establish without a paired release. ``find_releases``
# answers "was a rock thrown", and an unpaired release of its can add a shot,
# so it is strict. These shots already exist; all that is wanted is the instant
# their rock crossed the tee line, and that can be had on much less evidence.

from curling_score.detect.rocks import Detection  # noqa: E402

VIEW_Y_MIN = -2.0


def _det(color, x, y):
    return Detection(color=color, x_m=x, y_m=y, x_px=0.0, y_px=0.0,
                     area_px=140.0, confidence=0.9)


def leaving(color, t0, y0=-1.95, y1=2.4, speed=2.0, fps=5.0, x=0.1):
    """A stone climbing out of the thrower's house, as that panel sees it."""
    out, t, y = [], t0, y0
    while y < y1:
        out.append((round(t, 3), _det(color, x, y)))
        y += speed / fps
        t += 1.0 / fps
    return out


def frames(*traces):
    by_t = {}
    for tr in traces:
        for t, d in tr:
            by_t.setdefault(t, []).append(d)
    return [(t, by_t[t]) for t in sorted(by_t)]


class Arrival:
    def __init__(self, t_enter):
        self.t_enter = t_enter


def arriving(n, color, rest, t_enter, release=None):
    s = Shot(n, color, t_rest_s=rest, release=release)
    s.delivery = Arrival(t_enter)
    s.tee_s, s.tee_estimated = None, False
    return s


class TestTimingAShotWithNoRelease:
    def test_it_reads_the_tee_crossing_off_the_track(self):
        # climbs from -1.95 at 2 m/s, so it crosses y = 0 at t = 100.975
        shots = [arriving(1, "red", 80.0, 70.0),
                 arriving(2, "yellow", 160.0, 117.0)]
        thinking.time_shots(shots, frames(leaving("yellow", 100.0)), VIEW_Y_MIN)
        assert shots[1].tee_s == pytest.approx(100.975, abs=0.02)
        assert shots[1].tee_estimated is False

    def test_a_climb_too_short_to_be_called_a_throw_still_times_it(self):
        """2.0 m: under ``release.MIN_TRAVEL_M``, over the clock's own floor."""
        shots = [arriving(1, "red", 80.0, 70.0),
                 arriving(2, "yellow", 160.0, 117.0)]
        thinking.time_shots(shots, frames(leaving("yellow", 100.0, y1=0.05)),
                            VIEW_Y_MIN)
        assert shots[1].tee_s == pytest.approx(100.975, abs=0.02)
        assert shots[1].tee_estimated is False

    def test_a_track_that_dies_just_below_the_line_is_carried_to_it(self):
        # ends at -0.35 m still doing 2 m/s: 0.175 s short of the tee
        shots = [arriving(1, "red", 80.0, 70.0),
                 arriving(2, "yellow", 160.0, 117.0)]
        thinking.time_shots(shots, frames(leaving("yellow", 100.0, y1=-0.3)),
                            VIEW_Y_MIN)
        assert shots[1].tee_s == pytest.approx(100.975, abs=0.06)
        assert shots[1].tee_estimated is False

    def test_a_track_that_dies_well_below_the_line_is_not_carried(self):
        """Beyond ``TRACK_TEE_GAP_M`` the throw was lost too early to place."""
        shots = [arriving(1, "red", 80.0, 70.0),
                 arriving(2, "yellow", 160.0, 117.0)]
        thinking.time_shots(shots, frames(leaving("yellow", 100.0, y1=-1.4)),
                            VIEW_Y_MIN)
        assert shots[1].tee_estimated is True

    def test_something_starting_up_the_panel_is_not_this_delivery(self):
        """A sweeper walking up-sheet never came out of the hack."""
        shots = [arriving(1, "red", 80.0, 70.0),
                 arriving(2, "yellow", 160.0, 117.0)]
        thinking.time_shots(
            shots, frames(leaving("yellow", 100.0, y0=0.7, y1=3.0)), VIEW_Y_MIN)
        assert shots[1].tee_estimated is True

    def test_the_other_team_s_rock_is_not_taken(self):
        shots = [arriving(1, "red", 80.0, 70.0),
                 arriving(2, "yellow", 160.0, 117.0)]
        thinking.time_shots(shots, frames(leaving("red", 100.0)), VIEW_Y_MIN)
        assert shots[1].tee_estimated is True

    def test_a_throw_outside_the_lag_window_is_not_taken(self):
        """80 s before the arrival is the previous end's business, not this."""
        shots = [arriving(1, "red", 80.0, 70.0),
                 arriving(2, "yellow", 200.0, 117.0)]
        thinking.time_shots(shots, frames(leaving("yellow", 37.0)), VIEW_Y_MIN)
        assert shots[1].tee_estimated is True

    def test_one_delivery_seen_twice_is_taken_from_the_hack(self):
        """The rock and the slider beside it, or a track broken and remade."""
        shots = [arriving(1, "red", 80.0, 70.0),
                 arriving(2, "yellow", 160.0, 117.0)]
        thinking.time_shots(
            shots,
            frames(leaving("yellow", 100.0, x=0.1),
                   leaving("yellow", 101.4, y0=-0.9, y1=3.0, x=0.9)),
            VIEW_Y_MIN)
        assert shots[1].tee_s == pytest.approx(100.975, abs=0.02)


class TestWhenNothingWasSeenLeavingTheHouse:
    def test_the_clock_stops_the_usual_lag_before_the_rock_arrived(self):
        shots = [arriving(1, "red", 80.0, 70.0),
                 arriving(2, "yellow", 160.0, 117.0)]
        thinking.time_shots(shots, [], VIEW_Y_MIN)
        assert shots[1].tee_s == pytest.approx(
            117.0 - thinking.ASSUMED_TEE_TO_ARRIVAL_S)
        assert shots[1].tee_estimated is True

    def test_the_lag_is_the_one_measured_across_the_charted_games(self):
        assert thinking.ASSUMED_TEE_TO_ARRIVAL_S == 16.0

    def test_an_estimate_is_counted_apart_from_what_was_seen(self):
        shots = [arriving(1, "red", 80.0, 70.0),
                 arriving(2, "yellow", 160.0, 117.0)]
        thinking.time_shots(shots, [], VIEW_Y_MIN)
        end = thinking.for_end(shots)
        assert end.measured_shots == 1
        assert end.estimated_shots == 1
        assert end.by_color["yellow"] == pytest.approx(
            (117.0 - 16.0) - (80.0 + thinking.GRACE_S))

    def test_a_game_adds_its_estimates_up(self):
        shots = [arriving(1, "red", 80.0, 70.0),
                 arriving(2, "yellow", 160.0, 117.0)]
        thinking.time_shots(shots, [], VIEW_Y_MIN)
        assert thinking.for_game([shots, shots]).estimated_shots == 2


class TestWhatTheTimingPassLeavesAlone:
    def test_a_shot_with_a_paired_release_keeps_it(self):
        """The pairing is the better evidence, and the long split needs it."""
        shots = [arriving(1, "red", 80.0, 70.0),
                 arriving(2, "yellow", 160.0, 117.0, release=rel(140.0, "yellow"))]
        thinking.time_shots(shots, frames(leaving("yellow", 100.0)), VIEW_Y_MIN)
        assert shots[1].tee_s is None
        assert thinking.tee_crossing(shots[1]) == pytest.approx(141.0, abs=0.02)

    def test_a_blank_is_never_given_a_time(self):
        blank = Shot(2, "yellow", missing=True)
        blank.delivery, blank.tee_s, blank.tee_estimated = None, None, False
        shots = [arriving(1, "red", 80.0, 70.0), blank]
        thinking.time_shots(shots, [], VIEW_Y_MIN)
        assert blank.tee_s is None

    def test_it_never_adds_or_drops_a_shot(self):
        shots = [arriving(1, "red", 80.0, 70.0),
                 arriving(2, "yellow", 160.0, 117.0)]
        before = [s.number for s in shots]
        thinking.time_shots(shots, frames(leaving("yellow", 100.0)), VIEW_Y_MIN)
        assert [s.number for s in shots] == before
