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

    def test_the_colour_of_an_observed_shot_is_never_inferred(self):
        # Two yellows running: a red went unseen between them. The red is
        # marked as a blank for someone to chart, but neither yellow has its
        # own colour second-guessed by the alternation rule.
        dvs = [self._dv("yellow", 10), self._dv("yellow", 60)]
        got = shots.from_deliveries(dvs, self._frames({0: []}))
        observed = [s for s in got if not s.missing]
        assert [s.color for s in observed] == ["yellow", "yellow"]
        assert all(s.color_inferred is False for s in observed)

    def test_no_deliveries_gives_no_shots(self):
        assert shots.from_deliveries([], self._frames({0: []})) == []


class TestBlanksForUnseenDeliveries:
    """A shot we could not read must be shown as unknown, never as empty."""

    def _dv(self, color, t):
        return TestShotsFromDeliveries()._dv(color, t)

    def _frames(self, at):
        return TestShotsFromDeliveries()._frames(at)

    def test_a_repeated_colour_inserts_a_blank_between(self):
        dvs = [self._dv("yellow", 10), self._dv("yellow", 60)]
        got = shots.from_deliveries(dvs, self._frames({0: []}))
        assert [s.color for s in got] == ["yellow", "red", "yellow"]
        assert [s.missing for s in got] == [False, True, False]
        assert [s.number for s in got] == [1, 2, 3]

    def test_a_blank_is_unknown_rather_than_an_empty_house(self):
        dvs = [self._dv("yellow", 10), self._dv("yellow", 60)]
        blank = shots.from_deliveries(dvs, self._frames({0: []}))[1]
        assert blank.state_known is False
        assert blank.stones == []
        assert blank.confidence == 0.0
        assert blank.delivery is None

    def test_an_observed_shot_with_no_stones_is_also_unknown(self):
        # An empty reading is a failure to see the house, not a bare sheet:
        # the stone that was just delivered has to be somewhere.
        dvs = [self._dv("yellow", 10)]
        got = shots.from_deliveries(dvs, self._frames({0: []}))
        assert got[0].state_known is False

    def test_a_stone_thrown_out_of_play_may_leave_an_empty_house(self):
        # It never came to rest, so nothing was added: an empty house is the
        # correct reading here, not a failure to see one.
        from curling_score.detect.delivery import Delivery

        dv = Delivery(color="yellow", t_enter=10, t_rest=18, entry_y_m=4.0,
                      rest_x_m=0.0, rest_y_m=-2.4, travel_m=6.4,
                      came_to_rest=False, reason="left-view")
        got = shots.from_deliveries([dv], self._frames({0: []}))
        assert got[0].state_known is True

    def test_a_house_we_could_read_is_known(self):
        a = [det("yellow", 0.2, 0.3)]
        dvs = [self._dv("yellow", 10)]
        got = shots.from_deliveries(dvs, self._frames({0: [], 19: a}))
        assert got[0].state_known is True

    def test_alternating_deliveries_need_no_blanks(self):
        dvs = [self._dv("yellow", 10), self._dv("red", 60),
               self._dv("yellow", 110)]
        got = shots.from_deliveries(dvs, self._frames({0: []}))
        assert not any(s.missing for s in got)

    def test_an_end_never_exceeds_sixteen_shots(self):
        dvs = [self._dv("yellow", 10 + 20 * i) for i in range(16)]
        got = shots.from_deliveries(dvs, self._frames({0: []}))
        assert len(got) == 16


class TestHouseDelta:
    """What a delivery did to the stones that were already there."""

    def test_a_draw_adds_its_own_stone(self):
        before = [det("yellow", 0.2, 0.3)]
        after = [det("yellow", 0.2, 0.3), det("red", -0.4, 0.5)]
        got = shots.house_delta(before, after)
        assert [s["color"] for s in got["added"]] == ["red"]
        assert got["removed"] == [] and got["moved"] == []

    def test_a_takeout_removes_one(self):
        before = [det("yellow", 0.2, 0.3), det("red", -0.4, 0.5)]
        after = [det("red", -0.4, 0.5)]
        got = shots.house_delta(before, after)
        assert [s["color"] for s in got["removed"]] == ["yellow"]
        assert got["added"] == []

    def test_a_stone_nudged_across_the_house_counts_as_moved(self):
        before = [det("yellow", 0.2, 0.3)]
        after = [det("yellow", 0.2, 1.0)]
        got = shots.house_delta(before, after)
        assert len(got["moved"]) == 1
        assert got["moved"][0]["distance_m"] == pytest.approx(0.7)
        assert got["added"] == [] and got["removed"] == []

    def test_detector_jitter_is_not_a_move(self):
        before = [det("yellow", 0.20, 0.30)]
        after = [det("yellow", 0.23, 0.33)]
        got = shots.house_delta(before, after)
        assert got == {"added": [], "removed": [], "moved": []}

    def test_the_closest_pairing_wins_in_a_crowded_house(self):
        # Matching in list order would pair the first "before" with the first
        # "after" and invent two long moves where nothing moved at all.
        before = [det("red", 1.0, 0.0), det("red", -1.0, 0.0)]
        after = [det("red", -1.02, 0.0), det("red", 1.02, 0.0)]
        got = shots.house_delta(before, after)
        assert got == {"added": [], "removed": [], "moved": []}

    def test_a_removal_and_a_distant_arrival_are_not_one_move(self):
        # Same colour, but far too far apart to be the same stone: calling it
        # a move would invent a collision and hide a real removal.
        before = [det("red", 2.0, 3.0)]
        after = [det("red", -2.0, -1.5)]
        got = shots.house_delta(before, after)
        assert len(got["removed"]) == 1 and len(got["added"]) == 1
        assert got["moved"] == []

    def test_colours_are_never_matched_across(self):
        before = [det("red", 0.2, 0.3)]
        after = [det("yellow", 0.2, 0.3)]
        got = shots.house_delta(before, after)
        assert len(got["added"]) == 1 and len(got["removed"]) == 1

    def test_an_empty_house_before_is_all_additions(self):
        after = [det("red", 0.2, 0.3), det("yellow", 1.0, 1.0)]
        got = shots.house_delta([], after)
        assert len(got["added"]) == 2


class TestDeliveredStone:
    """Which rock in the house is the one that was just thrown."""

    def _dv(self, color, t, x=0.0, y=0.0):
        from curling_score.detect.delivery import Delivery

        return Delivery(color=color, t_enter=t, t_rest=t + 8.0, entry_y_m=4.0,
                        rest_x_m=x, rest_y_m=y, travel_m=3.0)

    def test_it_points_at_the_stone_where_the_delivery_stopped(self):
        a = [det("yellow", 1.5, 1.5), det("red", 0.1, 0.05)]
        dvs = [self._dv("red", 10, x=0.1, y=0.0)]
        got = shots.from_deliveries(dvs, TestShotsFromDeliveries()._frames(
            {0: [], 19: a}))
        assert got[0].delivered_stone_index == 1

    def test_it_never_points_at_the_other_team(self):
        a = [det("yellow", 0.1, 0.0)]
        dvs = [self._dv("red", 10, x=0.1, y=0.0)]
        got = shots.from_deliveries(dvs, TestShotsFromDeliveries()._frames(
            {0: [], 19: a}))
        assert got[0].delivered_stone_index is None

    def test_a_shooter_that_is_not_in_the_house_leaves_it_unset(self):
        a = [det("red", -2.0, 3.0)]
        dvs = [self._dv("red", 10, x=0.1, y=0.0)]
        got = shots.from_deliveries(dvs, TestShotsFromDeliveries()._frames(
            {0: [], 19: a}))
        assert got[0].delivered_stone_index is None


class TestShortEndsAreFilledWithBlanks:
    """fit_end enforces alternation, so a missed rock shows up as a short end.

    It never shows up as a repeated colour -- the fit has already dropped
    whatever broke the pattern -- so the count is the only signal left.
    """

    def _dv(self, color, t):
        return TestShotsFromDeliveries()._dv(color, t)

    def _frames(self, at):
        return TestShotsFromDeliveries()._frames(at)

    def _alternating(self, n, first="yellow", step=50, start=10):
        colors = [first, "red"] * n
        return [self._dv(colors[i], start + i * step) for i in range(n)]

    def test_an_end_missing_more_than_a_handful_is_not_filled(self):
        # With half the end gone, every blank's position would be invented.
        # The end-level "complete: false" is the honest signal there.
        got = shots.from_deliveries(self._alternating(3), self._frames({0: []}))
        assert len(got) == 3
        assert not any(s.missing for s in got)

    def test_a_full_end_is_left_alone(self):
        got = shots.from_deliveries(self._alternating(16), self._frames({0: []}))
        assert len(got) == 16
        assert not any(s.missing for s in got)

    def test_an_end_one_rock_short_gains_one_blank_at_the_end(self):
        # A single blank cannot go in the middle without breaking alternation,
        # so its only possible home is the end.
        got = shots.from_deliveries(self._alternating(15), self._frames({0: []}))
        assert len(got) == 16
        assert [s.missing for s in got] == [False] * 15 + [True]

    def test_the_appended_blank_keeps_the_colours_alternating(self):
        got = shots.from_deliveries(self._alternating(15), self._frames({0: []}))
        colors = [s.color for s in got]
        assert all(a != b for a, b in zip(colors, colors[1:]))
        assert colors.count("red") == 8 and colors.count("yellow") == 8

    def test_a_pair_is_inserted_where_the_long_gap_is(self):
        # Thirteen rocks with one gap three times the usual: two went unseen
        # there, and the third is the one after the last we saw.
        dvs = self._alternating(13)
        dvs = [d if i < 7 else
               type(d)(**{**d.__dict__, "t_enter": d.t_enter + 110,
                          "t_rest": d.t_rest + 110})
               for i, d in enumerate(dvs)]
        got = shots.from_deliveries(dvs, self._frames({0: []}))
        assert len(got) == 16
        blanks = [i for i, s in enumerate(got) if s.missing]
        assert len(blanks) == 3
        assert blanks[0] == 7 and blanks[1] == 8   # the pair, at the gap
        assert blanks[2] == 15                     # the odd one, at the end

    def test_filling_never_breaks_alternation(self):
        for n in (12, 13, 14, 15):
            got = shots.from_deliveries(self._alternating(n),
                                        self._frames({0: []}))
            colors = [s.color for s in got]
            assert len(got) == 16, n
            assert all(a != b for a, b in zip(colors, colors[1:])), (n, colors)
            assert colors.count("red") == 8, (n, colors)

    def test_every_inserted_blank_is_unknown_not_empty(self):
        got = shots.from_deliveries(self._alternating(13), self._frames({0: []}))
        for s in got:
            if s.missing:
                assert s.state_known is False
                assert s.stones == [] and s.delivery is None

    def test_an_end_with_no_deliveries_stays_empty(self):
        # Nothing observed is not the same as sixteen unknown rocks; an end we
        # never saw at all should not be presented as one to chart.
        assert shots.from_deliveries([], self._frames({0: []})) == []


class TestStonesOnTheSheetPlaceTheBlanks:
    """The sheet cannot hold more stones than rocks have been thrown."""

    def _alt(self, n, first="red"):
        other = "yellow" if first == "red" else "red"
        dv = TestShortEndsAreFilledWithBlanks()._dv
        return [dv(first if i % 2 == 0 else other, 10 + i * 50) for i in range(n)]

    def _plan(self, n, sizes, first="red"):
        return shots._fill_short_end(
            shots._with_placeholders(self._alt(n, first)), 16, house_sizes=sizes)

    def test_three_stones_after_the_first_seen_rock_means_two_went_before(self):
        # Game 3 end 4 of the 5U championship: the two lead guards were never
        # seen, so the first house read held three stones. Timing could not
        # place them -- the end's clock had not started -- but the count can.
        plan = self._plan(14, [3] + [4] * 13)
        blanks = [i for i, (_c, dv) in enumerate(plan) if dv is None]
        assert blanks == [0, 1]
        # The rock we saw first was the third thrown and keeps its colour.
        assert [c for c, _ in plan[:3]] == ["red", "yellow", "red"]

    def test_the_end_stays_legal(self):
        plan = self._plan(14, [3] + [4] * 13)
        colors = [c for c, _ in plan]
        assert len(plan) == 16
        assert all(a != b for a, b in zip(colors, colors[1:]))
        assert colors.count("red") == 8

    def test_a_house_that_never_outgrows_the_count_changes_nothing(self):
        # Fourteen rocks, houses of at most one stone each: blanks go where
        # they always did, at the end.
        plan = self._plan(14, [1] * 14)
        assert [i for i, (_c, dv) in enumerate(plan) if dv is None] == [14, 15]

    def test_one_stone_too_many_still_costs_a_pair(self):
        # A single blank between alternating neighbours cannot exist, so the
        # evidence for one rock buys a pair -- and the rest of the deficit
        # goes to the tail as before.
        plan = self._plan(13, [2] + [3] * 12)
        blanks = [i for i, (_c, dv) in enumerate(plan) if dv is None]
        assert blanks == [0, 1, 15]

    def test_it_never_invents_more_rocks_than_the_end_is_short(self):
        plan = self._plan(15, [4] + [5] * 14)   # one short; the count says three
        assert len(plan) == 16
        assert [i for i, (_c, dv) in enumerate(plan) if dv is None] == [15]

    def test_evidence_mid_end_puts_the_pair_there(self):
        # Ten rocks alternate cleanly, then the eleventh house holds two more
        # stones than have been thrown: the pair goes before it, not at the end.
        sizes = list(range(1, 11)) + [13, 14, 15, 16]
        plan = self._plan(14, sizes)
        assert [i for i, (_c, dv) in enumerate(plan) if dv is None] == [10, 11]

    def test_from_deliveries_feeds_the_house_sizes_through(self):
        # Fourteen alternating deliveries; the first settles with three stones
        # already on the sheet. The blanks lead the end.
        from curling_score.detect.rocks import Detection

        def det(color, x, y):
            return Detection(color=color, x_m=x, y_m=y, x_px=0.0, y_px=0.0,
                             area_px=150.0, confidence=0.9)

        dvs = self._alt(14)
        frames = []
        for i, dv in enumerate(dvs):
            stones = [det("red", 0.3, 4.2), det("yellow", 0.3, 3.5),
                      det(dv.color, 0.0, -1.7)] + [
                det("red" if k % 2 else "yellow", 0.4 * k - 1.0, 0.5) for k in range(i)]
            for k in range(20):
                frames.append((dv.t_rest + 0.5 * k, stones))
        got = shots.from_deliveries(dvs, frames)
        assert [s.missing for s in got[:3]] == [True, True, False]
        assert [s.color for s in got[:3]] == ["red", "yellow", "red"]
        assert got[2].number == 3 and got[2].delivery is dvs[0]
        assert not any(s.missing for s in got[3:])


class TestFillingAgainstRealEnds:
    """The four short ends of the reference VOD, by their real delivery times.

    Synthetic fixtures cannot show that the parity rule beats timing alone --
    these can. g2e1 has three gaps at twice its own median and is only one rock
    short, so a gap-only filler would have invented three blanks there; parity
    says a lone blank can only go at the end, and it does.
    """

    # (label, first colour, inter-delivery gaps in seconds, expected blank
    #  positions) -- gaps read from out_chart/timeline.json, t_enter deltas.
    ENDS = [
        ("g1e1", "yellow",
         [48, 47, 60, 53, 35, 50, 159, 82, 68, 50, 58, 52], [7, 8, 15]),
        ("g2e1", "yellow",
         [56, 39, 41, 49, 56, 46, 40, 55, 69, 46, 120, 118, 119, 56], [15]),
        ("g2e3", "yellow",
         [48, 31, 37, 59, 50, 163, 47, 66, 69, 68, 62, 84], [6, 7, 15]),
        ("g2e4", "red",
         [39, 54, 58, 33, 40, 47, 58, 54, 78, 49, 74, 114, 118, 78], [15]),
    ]

    def _deliveries(self, first, gaps):
        from curling_score.detect.delivery import Delivery

        t, color, out = 100.0, first, []
        for i in range(len(gaps) + 1):
            out.append(Delivery(color=color, t_enter=t, t_rest=t + 8.0,
                                entry_y_m=4.0, rest_x_m=0.0, rest_y_m=0.0,
                                travel_m=3.0))
            if i < len(gaps):
                t += gaps[i]
            color = "red" if color == "yellow" else "yellow"
        return out

    @pytest.mark.parametrize("label,first,gaps,expected", ENDS,
                             ids=[e[0] for e in ENDS])
    def test_blanks_land_where_the_evidence_puts_them(self, label, first,
                                                      gaps, expected):
        plan = shots._fill_short_end(
            shots._with_placeholders(self._deliveries(first, gaps)), 16)
        assert [i for i, (_c, dv) in enumerate(plan) if dv is None] == expected

    @pytest.mark.parametrize("label,first,gaps,expected", ENDS,
                             ids=[e[0] for e in ENDS])
    def test_every_filled_end_is_a_legal_end(self, label, first, gaps,
                                             expected):
        plan = shots._fill_short_end(
            shots._with_placeholders(self._deliveries(first, gaps)), 16)
        colors = [c for c, _dv in plan]
        assert len(plan) == 16
        assert all(a != b for a, b in zip(colors, colors[1:])), colors
        assert colors.count("red") == 8 and colors.count("yellow") == 8

    def test_timing_alone_would_have_got_g2e1_wrong(self):
        # Three gaps at ~2.1x the median, but the end is one rock short. A
        # filler that trusted the gaps would have placed three blanks.
        _label, first, gaps, _expected = self.ENDS[1]
        median = sorted(gaps)[len(gaps) // 2]
        long_gaps = [g for g in gaps if g >= shots.GAP_FACTOR * median]
        assert len(long_gaps) == 3, long_gaps
        plan = shots._fill_short_end(
            shots._with_placeholders(self._deliveries(first, gaps)), 16)
        assert sum(1 for _c, dv in plan if dv is None) == 1
