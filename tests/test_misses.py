import pytest

from curling_score.detect.delivery import Delivery
from curling_score.game import misses


def dv(color, t, dur=8.0, reason="rest"):
    return Delivery(color=color, t_enter=t, t_rest=t + dur, entry_y_m=4.0,
                    rest_x_m=0.0, rest_y_m=0.0, travel_m=3.0, reason=reason)


def alternating(n, first="yellow"):
    other = "red" if first == "yellow" else "yellow"
    return [dv(first if i % 2 == 0 else other, 50.0 * i) for i in range(n)]


class TestColourRepeat:
    def test_a_repeat_pins_a_missing_delivery_between_two_known_times(self):
        got = misses.find_candidates(
            [dv("yellow", 100), dv("red", 200), dv("red", 300)],
            start_s=0.0, end_s=900.0,
        )
        repeat = [c for c in got if c.reason == "colour repeat"]
        assert len(repeat) == 1
        # It must lie between the first red settling and the second being thrown.
        assert repeat[0].start_s == pytest.approx(208.0)
        assert repeat[0].end_s == pytest.approx(300.0)

    def test_the_missing_colour_is_known_from_alternation(self):
        got = misses.find_candidates(
            [dv("yellow", 100), dv("red", 200), dv("red", 300)],
            start_s=0.0, end_s=900.0,
        )
        repeat = [c for c in got if c.reason == "colour repeat"][0]
        assert repeat.expected_color == "yellow"

    def test_a_repeat_is_the_most_confident_kind_of_candidate(self):
        got = misses.find_candidates(
            [dv("yellow", 100), dv("red", 200), dv("red", 300)],
            start_s=0.0, end_s=900.0,
        )
        repeat = [c for c in got if c.reason == "colour repeat"][0]
        assert repeat.confidence >= 0.9


class TestLongGaps:
    def test_an_unusually_long_gap_is_flagged(self):
        seq = [dv("yellow", 0), dv("red", 50), dv("yellow", 100), dv("red", 400)]
        got = misses.find_candidates(seq, start_s=0.0, end_s=900.0)
        gaps = [c for c in got if c.reason == "long gap"]
        assert len(gaps) == 1
        assert gaps[0].start_s == pytest.approx(108.0)
        assert gaps[0].end_s == pytest.approx(400.0)

    def test_regular_spacing_produces_no_gap_candidates(self):
        seq = [dv(c, 50 * i) for i, c in enumerate("yr" * 8)]
        seq = [dv("yellow" if i % 2 == 0 else "red", 50 * i) for i in range(16)]
        got = misses.find_candidates(seq, start_s=0.0, end_s=900.0)
        assert [c for c in got if c.reason == "long gap"] == []

    def test_a_bigger_gap_scores_higher(self):
        seq = [dv("yellow", 0), dv("red", 50), dv("yellow", 100),
               dv("red", 300), dv("yellow", 700)]
        got = [c for c in misses.find_candidates(seq, 0.0, 900.0)
               if c.reason == "long gap"]
        assert len(got) == 2
        assert got[0].confidence > got[1].confidence or \
               got[0].end_s - got[0].start_s > got[1].end_s - got[1].start_s


class TestEdgesOfTheEnd:
    def test_a_late_first_delivery_leaves_a_window_at_the_start(self):
        got = misses.find_candidates([dv("yellow", 400), dv("red", 450)],
                                     start_s=0.0, end_s=900.0)
        assert any(c.reason == "before first delivery" for c in got)

    def test_an_early_last_delivery_leaves_a_window_at_the_end(self):
        got = misses.find_candidates([dv("yellow", 10), dv("red", 60)],
                                     start_s=0.0, end_s=900.0)
        assert any(c.reason == "after last delivery" for c in got)

    def test_a_full_end_needs_no_edge_windows(self):
        seq = [dv("yellow" if i % 2 == 0 else "red", 20 + 50 * i) for i in range(16)]
        got = misses.find_candidates(seq, start_s=0.0, end_s=850.0)
        assert [c for c in got if "delivery" in c.reason] == []


class TestOrdering:
    def test_candidates_come_back_most_likely_first(self):
        seq = [dv("yellow", 100), dv("red", 200), dv("red", 300),
               dv("yellow", 800)]
        got = misses.find_candidates(seq, 0.0, 900.0)
        assert got == sorted(got, key=lambda c: -c.confidence)

    def test_an_end_with_nothing_detected_flags_the_whole_span(self):
        got = misses.find_candidates([], start_s=100.0, end_s=900.0)
        assert len(got) == 1
        assert (got[0].start_s, got[0].end_s) == (100.0, 900.0)


class TestSuspects:
    """The other direction: deliveries that probably are not real.

    Relaxing the travel filter so guards survive also let noise in -- game 1
    end 4 reported 17 deliveries with yellow throwing 11, both impossible. The
    rules bound an end to 8 per team, so an over-count is evidence, and the
    likeliest culprits are worth eyeballing alongside the misses.
    """

    def test_nothing_is_suspect_in_a_clean_end(self):
        seq = [dv("yellow" if i % 2 == 0 else "red", 50 * i) for i in range(16)]
        assert misses.find_suspects(seq) == []

    def test_flags_extras_when_a_team_throws_too_many(self):
        seq = [dv("yellow" if i % 2 == 0 else "red", 50 * i) for i in range(16)]
        seq.append(dv("yellow", 810))
        got = misses.find_suspects(seq)
        assert got, "an over-count should produce suspects"
        assert all(s.color == "yellow" for s in got)

    def test_two_deliveries_almost_on_top_of_each_other_are_suspect(self):
        # A takeout scattering stones can look like several deliveries at once.
        # Both of the pair are surfaced: one is spurious, but timing alone
        # cannot say which, and guessing would hide the real one.
        seq = [dv("yellow", 100), dv("red", 150), dv("red", 153),
               dv("yellow", 200)]
        got = misses.find_suspects(seq)
        assert {round(s.t_enter) for s in got} == {150, 153}

    def test_suspects_carry_a_reason(self):
        seq = [dv("yellow", 100), dv("red", 150), dv("red", 153)]
        assert all(s.reason for s in misses.find_suspects(seq))

    def test_an_empty_end_has_no_suspects(self):
        assert misses.find_suspects([]) == []


class TestScoreWindows:
    """The window the score is actually read from.

    The score depends on the house right after the *last* delivery, so that one
    window decides the end. Reviewing it answers two things at once: whether the
    house looks as detected, and whether another stone was thrown after it.
    """

    def test_gives_the_window_just_after_the_last_delivery(self):
        seq = [dv("yellow", 100), dv("red", 200)]
        got = misses.find_score_windows(seq, start_s=0.0, end_s=900.0)
        assert len(got) == 1
        # Just after the last stone settles (200 + 8), not before.
        assert got[0].start_s >= 208.0
        assert got[0].end_s <= 240.0

    def test_flags_the_tail_when_the_end_runs_on_afterwards(self):
        seq = [dv("yellow", 100), dv("red", 200)]
        got = misses.find_score_windows(seq, start_s=0.0, end_s=900.0)
        assert got[0].later_unchecked_s > 600.0

    def test_no_tail_when_the_last_delivery_is_near_the_end(self):
        seq = [dv("yellow", 100), dv("red", 820)]
        got = misses.find_score_windows(seq, start_s=0.0, end_s=850.0)
        assert got[0].later_unchecked_s < 40.0

    def test_an_end_with_no_deliveries_has_no_score_window(self):
        assert misses.find_score_windows([], 0.0, 900.0) == []

    def test_carries_the_delivery_count_so_the_reviewer_knows_the_risk(self):
        seq = [dv("yellow" if i % 2 == 0 else "red", 50 * i) for i in range(16)]
        got = misses.find_score_windows(seq, 0.0, 900.0)
        assert got[0].deliveries_seen == 16


class TestScoringMoment:
    """The review must point at the frames that decided the end.

    The pipeline does not score from the last delivery -- `shots.scoring_shot`
    walks back to the last one that left stones, because a trailing candidate
    is routinely a phantom from the players clearing the house. A review that
    showed the last delivery would show the clearing instead.
    """

    def test_the_window_follows_the_moment_scored_from(self):
        found = [dv("yellow", 100), dv("red", 200), dv("yellow", 800)]
        got = misses.find_score_windows(found, 0.0, 900.0, scored_at=208.0)
        assert got[0].start_s == pytest.approx(208.5)

    def test_without_one_it_falls_back_to_the_last_delivery(self):
        found = [dv("yellow", 100), dv("red", 200)]
        got = misses.find_score_windows(found, 0.0, 900.0)
        assert got[0].start_s == pytest.approx(208.5)

    def test_it_names_the_colour_that_decided_the_end(self):
        found = [dv("yellow", 100), dv("red", 200), dv("yellow", 800)]
        got = misses.find_score_windows(found, 0.0, 900.0, scored_at=208.0)
        assert got[0].color == "red"

    def test_the_run_on_is_measured_from_that_moment_not_the_last(self):
        found = [dv("yellow", 100), dv("red", 200), dv("yellow", 800)]
        got = misses.find_score_windows(found, 0.0, 900.0, scored_at=208.0)
        assert got[0].later_unchecked_s == pytest.approx(692.0)

    def test_a_bare_house_is_called_out_and_downgraded(self):
        found = alternating(16)
        bare = misses.find_score_windows(found, 0.0, 900.0, stones_in_house=0)[0]
        full = misses.find_score_windows(found, 0.0, 900.0, stones_in_house=3)[0]
        assert "blank end" in bare.reason
        assert bare.confidence < full.confidence

    def test_a_weakly_confirmed_last_shot_is_called_out(self):
        found = [dv("yellow", 100), dv("red", 200, reason="left-view")]
        got = misses.find_score_windows(found, 0.0, 900.0)[0]
        assert "left-view" in got.reason
        assert got.evidence == "left-view"


class TestRuleConflicts:
    """Every candidate the rules drop lost a contest with a neighbour, and
    which of the two was the stone is what a human eye settles at a glance."""

    def test_a_dropped_candidate_becomes_a_window(self):
        offered = [dv("yellow", 100), dv("yellow", 110), dv("red", 200)]
        kept = [offered[0], offered[2]]
        got = misses.find_conflicts(offered, kept)
        assert len(got) == 1
        assert got[0].color == "yellow"
        assert got[0].at_s == 110

    def test_the_window_spans_the_rival_too(self):
        offered = [dv("yellow", 100), dv("yellow", 110), dv("red", 200)]
        kept = [offered[0], offered[2]]
        got = misses.find_conflicts(offered, kept)
        assert got[0].start_s <= 100
        assert got[0].end_s >= 118

    def test_a_candidate_too_close_to_a_kept_one_is_called_a_phantom(self):
        offered = [dv("yellow", 100), dv("yellow", 110), dv("red", 200)]
        kept = [offered[0], offered[2]]
        got = misses.find_conflicts(offered, kept)
        assert "not a stone" in got[0].reason
        assert got[0].nearest_kept_s == pytest.approx(10.0)

    def test_closeness_is_judged_against_either_colour(self):
        """The sweeper case, from game 1 end 4.

        A red takeout entered at 3152.9 and the yellow-jacketed sweeper running
        alongside it was detected as a yellow delivery one second later. Its
        nearest same-coloured neighbour is 89 s away, so judging closeness by
        colour alone calls it an isolated drop and ranks it near the top of the
        page -- which is where it was.
        """
        offered = [dv("yellow", 3105.8), dv("red", 3152.9),
                   dv("yellow", 3153.9), dv("yellow", 3242.8)]
        kept = [offered[0], offered[1], offered[3]]
        got = misses.find_conflicts(offered, kept)
        assert len(got) == 1
        assert got[0].nearest_kept_color == "red"
        assert got[0].nearest_kept_s == pytest.approx(1.0)
        assert "not a stone" in got[0].reason

    def test_an_isolated_drop_states_both_accounts(self):
        # The rules cannot choose between "this is a phantom" and "a delivery
        # of the other colour between them was missed", so both are stated.
        offered = [dv("yellow", 100), dv("red", 200), dv("yellow", 400),
                   dv("yellow", 600)]
        kept = [offered[0], offered[1], offered[3]]
        got = misses.find_conflicts(offered, kept)
        assert len(got) == 1
        assert "either this is not a stone" in got[0].reason
        assert "a red between them was missed" in got[0].reason

    def test_a_phantom_ranks_below_an_isolated_drop(self):
        phantom = [dv("yellow", 100), dv("red", 200), dv("yellow", 201),
                   dv("yellow", 400)]
        p_got = misses.find_conflicts(
            phantom, [phantom[0], phantom[1], phantom[3]])
        lone = [dv("yellow", 100), dv("red", 200), dv("yellow", 400),
                dv("yellow", 600)]
        l_got = misses.find_conflicts(lone, [lone[0], lone[1], lone[3]])
        assert p_got[0].confidence < l_got[0].confidence

    def test_a_same_colour_delivery_minutes_away_is_not_a_rival(self):
        # The fitter drops candidates for reasons that span the whole end, so
        # the nearest same-coloured delivery is often unrelated. Calling it a
        # rival reports a conflict that is not there.
        offered = [dv("yellow", 100), dv("red", 200), dv("yellow", 300),
                   dv("red", 340), dv("yellow", 400)]
        kept = [offered[0], offered[1], offered[2], offered[4]]
        got = misses.find_conflicts(offered, kept)
        assert len(got) == 1
        assert got[0].rival_s is None
        assert "turn order" in got[0].reason

    def test_a_near_duplicate_of_the_same_colour_also_ranks_low(self):
        # Two candidates seconds apart are one stone counted twice, which the
        # rules almost certainly resolved correctly. A strong candidate with
        # nothing nearby to clash with is the one worth a look.
        dupe = [dv("yellow", 100), dv("yellow", 108), dv("red", 200)]
        dupe_c = misses.find_conflicts(dupe, [dupe[0], dupe[2]])
        lone = [dv("yellow", 100), dv("red", 200), dv("yellow", 300),
                dv("red", 340), dv("yellow", 400)]
        lone_c = misses.find_conflicts(
            lone, [lone[0], lone[1], lone[2], lone[4]])
        assert dupe_c[0].confidence < lone_c[0].confidence

    def test_a_team_at_its_eight_says_so(self):
        kept = [dv("yellow" if i % 2 == 0 else "red", 50.0 * i)
                for i in range(16)]
        extra = dv("yellow", 810)
        got = misses.find_conflicts(kept + [extra], kept)
        assert len(got) == 1
        assert "already has eight" in got[0].reason

    def test_nothing_dropped_means_nothing_to_review(self):
        found = alternating(16)
        assert misses.find_conflicts(found, found) == []

    def test_a_strongly_attested_drop_ranks_higher(self):
        offered = [dv("yellow", 100), dv("yellow", 110, reason="rest"),
                   dv("red", 200)]
        weak = [dv("yellow", 100), dv("yellow", 110, reason="left-view"),
                dv("red", 200)]
        strong_c = misses.find_conflicts(offered, [offered[0], offered[2]])
        weak_c = misses.find_conflicts(weak, [weak[0], weak[2]])
        assert strong_c[0].confidence > weak_c[0].confidence


class TestCrowdedScoringHouse:
    """Half the stones in the twelve-foot when the end is decided usually means
    the house was read during staging, not that nothing was ever taken out."""

    def test_a_crowded_house_is_called_out_and_downgraded(self):
        found = alternating(16)
        crowded = misses.find_score_windows(found, 0.0, 900.0,
                                            stones_in_house=10)[0]
        normal = misses.find_score_windows(found, 0.0, 900.0,
                                           stones_in_house=3)[0]
        assert "10 stones in the house" in crowded.reason
        assert crowded.confidence < normal.confidence

    def test_an_ordinary_house_is_not_mentioned(self):
        found = alternating(16)
        got = misses.find_score_windows(found, 0.0, 900.0, stones_in_house=4)[0]
        assert "in the house" not in got.reason

    def test_an_unknown_count_is_not_treated_as_crowded(self):
        found = alternating(16)
        got = misses.find_score_windows(found, 0.0, 900.0)[0]
        assert "stones in the house" not in got.reason
        assert got.confidence > 0.5


class TestNoFlightSeen:
    """Deliveries believed without their flight ever having been observed.

    The house-appear route is the only way to catch a guard thrown short, and
    the only one a stone parked at the delivery end can also satisfy -- it did,
    three times in game 1 end 6, on a stone sitting in 94% of the end's frames.
    It is the weakest thing the pipeline believes, so it belongs on the page.
    """

    def appear(self, y, t=100.0, color="yellow"):
        return Delivery(color=color, t_enter=t, t_rest=t, entry_y_m=y + 0.1,
                        rest_x_m=0.3, rest_y_m=y, travel_m=0.0,
                        reason="house-appear")

    def test_a_no_flight_delivery_is_flagged(self):
        found = [dv("yellow", 100), self.appear(3.68, t=200, color="red")]
        got = misses.find_weak_evidence(found)
        assert len(got) == 1
        assert got[0].color == "red"
        assert "no flight seen" in got[0].reason

    def test_ordinary_deliveries_are_not_flagged(self):
        assert misses.find_weak_evidence(alternating(16)) == []

    def test_the_window_covers_the_delivery(self):
        got = misses.find_weak_evidence([self.appear(3.68, t=200)])
        assert got[0].start_s <= 200
        assert got[0].end_s >= 200

    def test_one_parked_up_sheet_ranks_above_one_near_the_house(self):
        far = misses.find_weak_evidence([self.appear(4.43)])[0]
        near = misses.find_weak_evidence([self.appear(2.10)])[0]
        assert far.confidence > near.confidence

    def test_the_resting_height_is_reported(self):
        got = misses.find_weak_evidence([self.appear(3.68, color="red")])
        assert "y=+3.68" in got[0].reason
