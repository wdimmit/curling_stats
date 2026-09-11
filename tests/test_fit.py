import pytest

from curling_score.detect.delivery import Delivery
from curling_score.game import fit


def dv(color, t, reason="rest"):
    return Delivery(color=color, t_enter=t, t_rest=t + 8.0, entry_y_m=3.8,
                    rest_x_m=0.0, rest_y_m=0.0, travel_m=3.0, reason=reason)


def colors(got):
    return [d.color for d in got]


def alternating(n, first="yellow"):
    other = "red" if first == "yellow" else "yellow"
    return [dv(first if i % 2 == 0 else other, 50.0 * i) for i in range(n)]


class TestAComplyingEndIsLeftAlone:
    def test_sixteen_alternating_deliveries_all_survive(self):
        found = alternating(16)
        assert fit.fit_end(found) == found

    def test_a_short_end_is_not_padded(self):
        found = alternating(9)
        assert fit.fit_end(found) == found

    def test_nothing_in_nothing_out(self):
        assert fit.fit_end([]) == []

    def test_order_is_by_time_not_input_order(self):
        found = [dv("red", 100.0), dv("yellow", 50.0)]
        assert [d.t_enter for d in fit.fit_end(found)] == [50.0, 100.0]


class TestAlternation:
    """A team cannot throw twice in a row, so a doubled colour is one stone."""

    def test_a_doubled_colour_loses_one(self):
        found = [dv("yellow", 10), dv("red", 60), dv("yellow", 110),
                 dv("yellow", 120), dv("red", 170)]
        got = fit.fit_end(found)
        assert colors(got) == ["yellow", "red", "yellow", "red"]

    def test_end_one_drops_the_candidate_that_crowds_a_red(self):
        # The observed sequence from game 1 end 1. The doubled yellow at 799.5
        # sits 2.2 s before the red at 801.7, which no two deliveries are.
        ts = [(27.3, "yellow"), (75.8, "red"), (122.4, "yellow"),
              (182.5, "red"), (234.6, "yellow"), (269.8, "red"),
              (321.0, "yellow"), (478.9, "red"), (559.9, "yellow"),
              (627.7, "red"), (678.4, "yellow"), (736.4, "red"),
              (788.7, "yellow"), (799.5, "yellow"), (801.7, "red"),
              (852.4, "yellow")]
        # Alternation in isolation, so this stays a test of the turn order.
        # With the spacing rule as well the answer is different and stricter --
        # 801.7 is only 13 s after 788.7, so it cannot follow it either.
        found = [dv(c, t) for t, c in ts]
        got = fit.fit_end(found, min_separation_s=0.0)
        assert len(got) == 15
        assert 799.5 not in [d.t_enter for d in got]
        assert 801.7 in [d.t_enter for d in got]

    def test_the_result_always_alternates(self):
        found = [dv("yellow", 10 * i) for i in range(6)] + \
                [dv("red", 10 * i + 5) for i in range(6)]
        got = fit.fit_end(found)
        assert all(a.color != b.color for a, b in zip(got, got[1:]))


class TestTeamAllowance:
    def test_no_team_throws_more_than_eight(self):
        found = alternating(24)
        got = fit.fit_end(found)
        assert colors(got).count("red") <= 8
        assert colors(got).count("yellow") <= 8

    def test_no_end_holds_more_than_sixteen(self):
        assert len(fit.fit_end(alternating(24))) == 16

    def test_it_keeps_the_earliest_sixteen_when_all_are_equal(self):
        # Phantoms cluster at the end of an end, where the house is being
        # cleared, so the earlier candidates are the better bet.
        found = alternating(20)
        got = fit.fit_end(found)
        assert [d.t_enter for d in got] == [50.0 * i for i in range(16)]


class TestEvidenceBreaksTies:
    def test_the_better_attested_of_two_rivals_survives(self):
        found = [dv("red", 60), dv("yellow", 110, reason="left-view"),
                 dv("yellow", 115, reason="rest"), dv("red", 170)]
        # Only one of the two yellows can be kept, so three shots is the
        # most the rules allow here.
        got = fit.fit_end(found)
        assert len(got) == 3
        assert [d.reason for d in got if d.color == "yellow"] == ["rest"]

    def test_length_beats_evidence(self):
        # Two strong candidates that cannot both be kept, against three weak
        # ones that can. The rules say sixteen stones are thrown, so a longer
        # run that obeys them is the better account even if each step is
        # weaker. Spaced like real deliveries, so only the turn order is in
        # play here.
        found = [dv("yellow", 100, reason="rest"),
                 dv("yellow", 160, reason="rest"),
                 dv("red", 220, reason="left-view"),
                 dv("yellow", 280, reason="left-view"),
                 dv("red", 340, reason="left-view")]
        got = fit.fit_end(found)
        assert len(got) == 4


class TestMinimumSeparation:
    """Two deliveries are never seconds apart.

    The players have to clear the house and set up between stones, so fifteen
    seconds is a floor. Alternation alone does not catch a phantom of the
    *opposite* colour: game 2 end 1 ends with a yellow at 8508.9 and a red at
    8515.3, which alternate perfectly and keep both teams inside their eight.
    The red is the players starting to clear, and because it is last the end is
    scored from it -- eight stones in the house, read during the clear-up.
    """

    def test_two_deliveries_seconds_apart_cannot_both_be_kept(self):
        found = [dv("yellow", 100), dv("red", 200), dv("yellow", 300),
                 dv("red", 306)]  # 6.4 s is the measured game 2 end 1 case
        got = fit.fit_end(found)
        assert len(got) == 3
        assert 306 not in [d.t_enter for d in got]

    def test_the_better_attested_of_the_two_survives(self):
        found = [dv("yellow", 100), dv("red", 200),
                 dv("yellow", 300, reason="rest"),
                 dv("red", 306, reason="left-view")]
        got = fit.fit_end(found)
        assert [d.t_enter for d in got] == [100, 200, 300]

    def test_it_prefers_the_pair_that_lets_the_end_run_longer(self):
        # Dropping the earlier of a close pair opens the way to two more
        # shots, which beats keeping the better-attested one on its own.
        found = [dv("yellow", 100, reason="rest"),
                 dv("yellow", 104, reason="left-view"),
                 dv("red", 200), dv("yellow", 300)]
        got = fit.fit_end(found)
        assert len(got) == 3
        assert [d.color for d in got] == ["yellow", "red", "yellow"]

    def test_deliveries_properly_spaced_are_all_kept(self):
        found = alternating(16)
        assert fit.fit_end(found) == found

    def test_the_floor_is_tighter_than_the_one_used_for_end_length(self):
        # Deliberately not the same number. Segmentation uses its floor to
        # reject a run far too short to be an end, where erring generous costs
        # nothing; this one throws away individual stones, where it does. Swept
        # against the board, fifteen seconds cut real deliveries.
        from curling_score.game import segment

        assert fit.MIN_SEPARATION_S < segment.MIN_DELIVERY_GAP_S
