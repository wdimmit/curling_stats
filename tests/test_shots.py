import pytest

from curling_score.detect.rest import RestState
from curling_score.detect.rocks import Detection
from curling_score.game import shots


def det(color, x, y):
    return Detection(color=color, x_m=x, y_m=y, x_px=0.0, y_px=0.0,
                     area_px=150.0, confidence=0.9)


def states(*configs, step=40.0):
    return [
        RestState(t_start=i * step, t_end=i * step + 20.0, stones=list(c), frames=30)
        for i, c in enumerate(configs)
    ]


class TestDeliveredColour:
    def test_a_new_stone_identifies_the_thrower(self):
        seq = states([], [det("yellow", 0.2, 0.3)])
        assert [s.color for s in shots.infer_shots(seq)] == ["yellow"]

    def test_reads_a_normal_alternating_end(self):
        y1, r1 = det("yellow", 0.2, 0.3), det("red", -0.3, 0.4)
        y2, r2 = det("yellow", 0.9, 0.1), det("red", -0.8, 0.2)
        seq = states([], [y1], [y1, r1], [y1, r1, y2], [y1, r1, y2, r2])
        assert [s.color for s in shots.infer_shots(seq)] == [
            "yellow", "red", "yellow", "red"
        ]

    def test_numbers_shots_from_one(self):
        y1, r1 = det("yellow", 0.2, 0.3), det("red", -0.3, 0.4)
        seq = states([], [y1], [y1, r1])
        assert [s.number for s in shots.infer_shots(seq)] == [1, 2]


class TestTakeouts:
    def test_a_takeout_that_removes_a_stone_is_still_a_shot(self):
        y1, r1 = det("yellow", 0.2, 0.3), det("red", -0.3, 0.4)
        # Red draws in, yellow hits it out: the count goes 1 -> 2 -> 1.
        seq = states([], [y1], [y1, r1], [y1])
        got = shots.infer_shots(seq)
        assert len(got) == 3
        assert [s.color for s in got] == ["yellow", "red", "yellow"]

    def test_the_thrower_of_a_takeout_comes_from_alternation(self):
        y1, r1 = det("yellow", 0.2, 0.3), det("red", -0.3, 0.4)
        seq = states([], [y1], [y1, r1], [y1])
        assert shots.infer_shots(seq)[2].color == "yellow"
        assert shots.infer_shots(seq)[2].color_inferred is True


class TestConstraintRepair:
    def test_inserts_a_missed_shot_when_a_colour_repeats(self):
        # Observed yellow twice running: a red delivery went unseen between them.
        y1 = det("yellow", 0.2, 0.3)
        y2 = det("yellow", 0.9, 0.1)
        seq = states([], [y1], [y1, y2])
        got = shots.infer_shots(seq)
        assert [s.color for s in got] == ["yellow", "red", "yellow"]
        assert got[1].missing is True
        assert got[0].missing is False

    def test_colours_always_end_up_alternating(self):
        y1 = det("yellow", 0.2, 0.3)
        y2 = det("yellow", 0.9, 0.1)
        y3 = det("yellow", -0.5, 0.6)
        seq = states([], [y1], [y1, y2], [y1, y2, y3])
        colors = [s.color for s in shots.infer_shots(seq)]
        assert all(a != b for a, b in zip(colors, colors[1:])), colors

    def test_never_reports_more_than_a_full_end(self):
        seq = states([], *[[det("yellow", 0.1 * i, 0.0) for i in range(1, n + 1)]
                           for n in range(1, 12)])
        assert len(shots.infer_shots(seq)) <= 16


class TestHammer:
    def test_the_team_throwing_first_does_not_hold_the_hammer(self):
        seq = states([], [det("yellow", 0.2, 0.3)])
        assert shots.hammer_from_shots(shots.infer_shots(seq)) == "red"

    def test_no_shots_means_no_hammer_can_be_read(self):
        assert shots.hammer_from_shots([]) is None


class TestClearing:
    """At the end of an end the players lift the stones off the sheet.

    That produces a long, stable, near-empty configuration. Scored naively it
    reads as a blank end, and the removals read as extra shots -- on the
    reference VOD this made 5 of 8 ends blank.
    """

    def _house(self, n):
        return [det("red" if i % 2 else "yellow", 0.1 * i, 0.2) for i in range(n)]

    def test_strips_the_trailing_clear_down(self):
        seq = states(self._house(6), self._house(7), self._house(4),
                     self._house(2), [])
        kept = shots.trim_clearing(seq)
        assert [len(s.stones) for s in kept] == [6, 7]

    def test_keeps_a_takeout_that_merely_thins_the_house(self):
        # Skip's last rock removing two stones is play, not clearing.
        seq = states(self._house(6), self._house(7), self._house(5))
        assert len(shots.trim_clearing(seq)) == 3

    def test_leaves_an_end_that_was_never_cleared_alone(self):
        seq = states(self._house(3), self._house(5), self._house(6))
        assert len(shots.trim_clearing(seq)) == 3

    def test_an_end_that_clears_completely_still_scores_its_final_house(self):
        seq = states([], self._house(3), self._house(6), self._house(3), [])
        kept = shots.trim_clearing(seq)
        assert len(kept[-1].stones) == 6

    def test_never_strips_everything(self):
        seq = states(self._house(4), self._house(2), [])
        assert len(shots.trim_clearing(seq)) >= 1


class TestShotsFromDeliveries:
    """Build the shot list from observed deliveries rather than house changes."""

    def _dv(self, color, t):
        from curling_score.detect.delivery import Delivery

        return Delivery(color=color, t_enter=t, t_rest=t + 8.0, entry_y_m=4.0,
                        rest_x_m=0.0, rest_y_m=0.0, travel_m=3.0)

    def _frames(self, at):
        """(t, detections) frames; `at` maps a time to a stone list."""
        out = []
        for t in [i * 0.5 for i in range(400)]:
            cur = []
            for start, stones in sorted(at.items()):
                if t >= start:
                    cur = stones
            out.append((t, cur))
        return out

    def test_one_shot_per_delivery(self):
        dvs = [self._dv("yellow", 10), self._dv("red", 60), self._dv("yellow", 110)]
        frames = self._frames({0: []})
        got = shots.from_deliveries(dvs, frames)
        assert [s.number for s in got] == [1, 2, 3]
        assert [s.color for s in got] == ["yellow", "red", "yellow"]

    def test_each_shot_carries_the_house_left_behind(self):
        a = [det("yellow", 0.2, 0.3)]
        b = [det("yellow", 0.2, 0.3), det("red", -0.4, 0.5)]
        dvs = [self._dv("yellow", 10), self._dv("red", 60)]
        got = shots.from_deliveries(dvs, self._frames({0: [], 19: a, 69: b}))
        assert len(got[0].stones) == 1
        assert len(got[1].stones) == 2

    def test_the_house_is_read_after_the_stone_settles(self):
        # Nothing from before the stone stopped may leak into the shot.
        a = [det("yellow", 0.2, 0.3)]
        dvs = [self._dv("yellow", 10)]  # rests at t=18
        got = shots.from_deliveries(dvs, self._frames({0: [], 19: a}))
        assert len(got[0].stones) == 1

    def test_the_colour_comes_from_the_delivery_not_from_alternation(self):
        # Two yellows running: a red was missed, but we do not invent one.
        dvs = [self._dv("yellow", 10), self._dv("yellow", 60)]
        got = shots.from_deliveries(dvs, self._frames({0: []}))
        assert [s.color for s in got] == ["yellow", "yellow"]
        assert all(s.color_inferred is False for s in got)

    def test_no_deliveries_gives_no_shots(self):
        assert shots.from_deliveries([], self._frames({0: []})) == []
