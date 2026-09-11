import pytest

from curling_score.detect.delivery import Delivery
from curling_score.game import endcheck


def dv(color, t, rest=(0.0, 0.0)):
    return Delivery(color=color, t_enter=t, t_rest=t + 8.0, entry_y_m=4.0,
                    rest_x_m=rest[0], rest_y_m=rest[1], travel_m=3.0)


def alternating(n, first="yellow", step=50.0):
    out = []
    for i in range(n):
        c = first if i % 2 == 0 else ("red" if first == "yellow" else "yellow")
        out.append(dv(c, 100.0 + i * step))
    return out


class TestStonesPerTeam:
    def test_a_complete_end_uses_eight_each(self):
        r = endcheck.check(alternating(16))
        assert r.thrown == {"yellow": 8, "red": 8}
        assert r.complete is True
        assert r.problems == []

    def test_a_short_end_is_flagged_incomplete(self):
        r = endcheck.check(alternating(11))
        assert r.complete is False
        assert any("11" in p for p in r.problems)

    def test_more_than_eight_from_one_team_is_impossible(self):
        seq = alternating(16) + [dv("yellow", 1000.0)]
        r = endcheck.check(seq)
        assert r.complete is False
        assert any("yellow" in p for p in r.problems)


class TestAlternation:
    def test_a_repeated_colour_means_a_delivery_was_missed(self):
        seq = [dv("yellow", 100), dv("red", 150), dv("red", 200)]
        r = endcheck.check(seq)
        assert r.missed_after == [1]

    def test_a_clean_sequence_reports_no_gaps(self):
        assert endcheck.check(alternating(16)).missed_after == []

    def test_several_repeats_are_all_reported(self):
        seq = [dv("yellow", 100), dv("yellow", 150),
               dv("red", 200), dv("red", 250)]
        assert endcheck.check(seq).missed_after == [0, 2]


class TestTimingGaps:
    def test_an_unusually_long_gap_suggests_a_missed_delivery(self):
        seq = alternating(4)
        seq.append(dv("yellow", seq[-1].t_enter + 300.0))
        r = endcheck.check(seq)
        assert r.long_gaps, "a 300 s gap should be flagged"

    def test_normal_spacing_is_not_flagged(self):
        assert endcheck.check(alternating(16)).long_gaps == []


class TestNoDeliveries:
    def test_an_empty_end_is_incomplete_but_not_contradictory(self):
        r = endcheck.check([])
        assert r.complete is False
        assert r.thrown == {"yellow": 0, "red": 0}
