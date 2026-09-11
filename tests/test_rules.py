import pytest

from curling_score.game import rules


class TestThrowOrder:
    def test_first_stone_of_end_is_thrown_by_the_lead_without_hammer(self):
        t = rules.throw_info(1)
        assert t.has_hammer is False
        assert t.position_slot == 1
        assert t.rock_of_player == 1

    def test_last_stone_of_end_is_the_hammer_skips_second_rock(self):
        t = rules.throw_info(16)
        assert t.has_hammer is True
        assert t.position_slot == 4
        assert t.rock_of_player == 2

    @pytest.mark.parametrize(
        "shot, hammer, slot, rock",
        [
            (1, False, 1, 1), (2, True, 1, 1),
            (3, False, 1, 2), (4, True, 1, 2),
            (5, False, 2, 1), (6, True, 2, 1),
            (7, False, 2, 2), (8, True, 2, 2),
            (9, False, 3, 1), (10, True, 3, 1),
            (11, False, 3, 2), (12, True, 3, 2),
            (13, False, 4, 1), (14, True, 4, 1),
            (15, False, 4, 2), (16, True, 4, 2),
        ],
    )
    def test_full_delivery_order(self, shot, hammer, slot, rock):
        t = rules.throw_info(shot)
        assert (t.has_hammer, t.position_slot, t.rock_of_player) == (hammer, slot, rock)

    @pytest.mark.parametrize("bad", [0, 17, -1])
    def test_rejects_shot_numbers_outside_an_end(self, bad):
        with pytest.raises(ValueError):
            rules.throw_info(bad)


class TestShotLabel:
    def test_labels_a_shot_the_way_a_curler_would_say_it(self):
        assert rules.shot_label(end=3, shot_number=5) == "3rd end, second's first rock"

    def test_uses_correct_ordinal_suffix_for_the_end(self):
        assert rules.shot_label(end=1, shot_number=1) == "1st end, lead's first rock"
        assert rules.shot_label(end=2, shot_number=16) == "2nd end, skip's second rock"
        assert rules.shot_label(end=11, shot_number=9) == "11th end, third's first rock"


def stone(color, x, y):
    return rules.Stone(color=color, x=x, y=y)


class TestScoreEnd:
    def test_empty_house_is_a_blank_end(self):
        assert rules.score_end([]) == {"red": 0, "yellow": 0}

    def test_stones_outside_the_house_do_not_score(self):
        # 2.5 m from the tee is well outside the 12-foot ring.
        assert rules.score_end([stone("red", 0.0, 2.5)]) == {"red": 0, "yellow": 0}

    def test_counts_every_own_stone_closer_than_the_nearest_opponent(self):
        stones = [
            stone("red", 0.0, 0.10),
            stone("red", 0.3, 0.00),
            stone("yellow", 0.0, 1.00),
            stone("red", 1.50, 0.0),  # in the house but behind the yellow
        ]
        assert rules.score_end(stones) == {"red": 2, "yellow": 0}

    def test_a_biter_counts_because_any_overlap_is_in_the_house(self):
        # Centre just inside 12-ft radius + stone radius.
        assert rules.score_end([stone("yellow", 0.0, 1.97)]) == {"red": 0, "yellow": 1}

    def test_a_stone_fully_outside_the_twelve_foot_does_not_count(self):
        assert rules.score_end([stone("yellow", 0.0, 1.98)]) == {"red": 0, "yellow": 0}

    def test_opponent_shot_rock_blanks_the_other_team(self):
        stones = [stone("yellow", 0.0, 0.05), stone("red", 0.0, 0.20)]
        assert rules.score_end(stones) == {"red": 0, "yellow": 1}

    def test_an_eight_ender_is_possible(self):
        stones = [stone("red", 0.0, 0.1 * i) for i in range(8)]
        stones.append(stone("yellow", 0.0, 1.9))
        assert rules.score_end(stones) == {"red": 8, "yellow": 0}


class TestHammer:
    def test_team_that_scores_loses_the_hammer(self):
        # Red held the hammer and scored 2; yellow gets the hammer next end.
        assert rules.next_hammer("red", {"red": 2, "yellow": 0}) == "yellow"

    def test_stealing_takes_the_hammer_away_from_the_thief(self):
        # Yellow stole while red had the hammer, so red gets it back.
        assert rules.next_hammer("red", {"red": 0, "yellow": 1}) == "red"

    def test_blank_end_retains_the_hammer(self):
        assert rules.next_hammer("red", {"red": 0, "yellow": 0}) == "red"

    def test_rejects_a_score_where_both_teams_scored(self):
        with pytest.raises(ValueError):
            rules.next_hammer("red", {"red": 1, "yellow": 1})


class TestRunningScore:
    def test_accumulates_totals_across_ends(self):
        ends = [
            {"red": 0, "yellow": 1},
            {"red": 2, "yellow": 0},
            {"red": 0, "yellow": 2},
        ]
        assert rules.running_total(ends) == {"red": 2, "yellow": 3}

    def test_no_ends_is_nil_all(self):
        assert rules.running_total([]) == {"red": 0, "yellow": 0}


class TestHammerChain:
    """The hammer follows from the scores, independently of what was detected.

    It is currently read from whoever threw the first stone of an end, which is
    wrong whenever that delivery is missed -- game 2 came out with red holding
    the hammer in all six ends, which cannot happen. The rules give the whole
    sequence from the scores alone, so the two can be cross-checked.
    """

    def test_derives_the_chain_from_the_first_hammer_and_the_scores(self):
        ends = [
            {"red": 0, "yellow": 1},   # yellow scores, so red gets the hammer
            {"red": 1, "yellow": 0},
            {"red": 0, "yellow": 0},   # blank: hammer stays put
            {"red": 0, "yellow": 2},
        ]
        # Yellow scores end 1, so yellow throws first in end 2 and red keeps
        # the hammer. Red scores end 2, handing it to yellow. End 3 is blank, so
        # yellow keeps it.
        assert rules.hammer_chain("red", ends) == ["red", "red", "yellow", "yellow"]

    def test_a_blank_end_keeps_the_hammer(self):
        ends = [{"red": 0, "yellow": 0}, {"red": 0, "yellow": 0}]
        assert rules.hammer_chain("yellow", ends) == ["yellow", "yellow"]

    def test_no_ends_gives_no_chain(self):
        assert rules.hammer_chain("red", []) == []

    def test_agrees_with_next_hammer_applied_step_by_step(self):
        ends = [{"red": 0, "yellow": 1}, {"red": 2, "yellow": 0},
                {"red": 0, "yellow": 0}, {"red": 1, "yellow": 0}]
        chain = rules.hammer_chain("yellow", ends)
        cur = "yellow"
        for i, end in enumerate(ends):
            assert chain[i] == cur
            cur = rules.next_hammer(cur, end)


class TestFirstHammerFromChain:
    def test_recovers_the_opening_hammer_from_a_later_observation(self):
        # We saw who had the hammer in end 3; who had it in end 1?
        ends = [{"red": 0, "yellow": 1}, {"red": 1, "yellow": 0}]
        assert rules.first_hammer_given("red", 3, ends) == "red"

    def test_an_out_of_range_end_is_rejected(self):
        with pytest.raises(ValueError):
            rules.first_hammer_given("red", 0, [])
