import pytest

from curling_score.game import format as F
from curling_score.game import rules


class TestFours:
    @pytest.mark.parametrize("n", range(1, 17))
    def test_matches_the_arithmetic_it_replaces(self, n):
        k = (n + 1) // 2
        t = F.FOURS.throw_info(n)
        assert (t.has_hammer, t.team_stone_number, t.position_slot, t.rock_of_player) \
            == (n % 2 == 0, k, (k + 1) // 2, (k - 1) % 2 + 1)

    def test_counts(self):
        assert (F.FOURS.delivered_per_team, F.FOURS.delivered_per_end,
                F.FOURS.max_score_per_end, F.FOURS.placed_per_team) == (8, 16, 8, 0)

    def test_rules_still_answers_for_fours(self):
        assert rules.throw_info(5) == F.FOURS.throw_info(5)
        assert rules.shot_label(3, 5) == "3rd end, second's first rock"

    def test_a_swap_means_nothing_in_fours(self):
        assert F.FOURS.throw_info(1, swapped=True).position_slot == 1


class TestDoubles:
    @pytest.mark.parametrize("n, hammer, slot, rock", [
        (1, False, 1, 1), (2, True, 1, 1),
        (3, False, 2, 1), (4, True, 2, 1),
        (5, False, 2, 2), (6, True, 2, 2),
        (7, False, 2, 3), (8, True, 2, 3),
        (9, False, 1, 2), (10, True, 1, 2),
    ])
    def test_one_player_throws_first_and_last_the_other_the_three_between(
            self, n, hammer, slot, rock):
        t = F.DOUBLES.throw_info(n)
        assert (t.has_hammer, t.position_slot, t.rock_of_player) == (hammer, slot, rock)

    def test_counts(self):
        assert (F.DOUBLES.delivered_per_team, F.DOUBLES.delivered_per_end,
                F.DOUBLES.max_score_per_end, F.DOUBLES.placed_per_team) == (5, 10, 6, 1)

    @pytest.mark.parametrize("bad", [0, 11])
    def test_rejects_shot_numbers_outside_an_end(self, bad):
        with pytest.raises(ValueError, match="1..10"):
            F.DOUBLES.throw_info(bad)

    def test_a_swap_hands_the_first_and_last_to_the_other_player(self):
        first = F.DOUBLES.throw_info(1, swapped=True)
        assert (first.position_slot, first.rock_of_player) == (2, 1)
        assert F.DOUBLES.throw_info(3, swapped=True).position_slot == 1

    def test_labels(self):
        assert F.DOUBLES.shot_label(3, 7) == "3rd end, B's third rock"
        assert F.DOUBLES.shot_label(1, 9) == "1st end, A's second rock"
        assert F.DOUBLES.shot_label(1, 1, swapped=True) == "1st end, B's first rock"
        assert rules.shot_label(2, 4, fmt=F.DOUBLES) == "2nd end, B's first rock"


class TestLookup:
    def test_by_name(self):
        assert F.by_name("doubles") is F.DOUBLES
        assert F.by_name("fours") is F.FOURS
        with pytest.raises(ValueError, match="quads"):
            F.by_name("quads")

    @pytest.mark.parametrize("doc", [{}, {"format": None}, {"format": {}},
                                     {"format": {"name": "quads"}}, None,
                                     {"format": {"name": ["x"]}},
                                     {"format": {"name": {"n": 1}}}])
    def test_a_document_that_does_not_say_is_fours(self, doc):
        assert F.of_document(doc) is F.FOURS

    def test_a_document_names_its_format(self):
        assert F.of_document({"format": F.DOUBLES.to_json()}) is F.DOUBLES

    def test_the_block_carries_what_a_reader_needs(self):
        assert F.DOUBLES.to_json() == {
            "name": "doubles", "stones_per_team": 6, "placed_per_team": 1,
            "delivered_per_team": 5, "delivered_per_end": 10,
            "positions": ["A", "B"], "throw_table": [1, 2, 2, 2, 1],
            "blank_passes_hammer": True, "swappable": True,
        }


class TestLineWithoutBroom:
    def test_only_doubles_draws_a_line_without_a_broom(self):
        assert F.DOUBLES.line_without_broom is True
        assert F.FOURS.line_without_broom is False

    def test_the_document_block_does_not_carry_it(self):
        assert "line_without_broom" not in F.DOUBLES.to_json()
        assert "line_without_broom" not in F.FOURS.to_json()


class TestShortHanded:
    """A team of three throws its eight rocks 3/3/2: lead, second, skip. Slots
    keep their four-player meaning, so the skip is still slot 4 and slot 3
    throws nothing."""

    @pytest.mark.parametrize("k, slot, rock", [
        (1, 1, 1), (2, 1, 2), (3, 1, 3),
        (4, 2, 1), (5, 2, 2), (6, 2, 3),
        (7, 4, 1), (8, 4, 2),
    ])
    def test_the_team_stones_go_three_three_two(self, k, slot, rock):
        for n in (2 * k - 1, 2 * k):          # either team's k-th rock
            t = F.FOURS.throw_info(n, short=True)
            assert (t.team_stone_number, t.position_slot, t.rock_of_player) == (k, slot, rock)
            assert t.has_hammer == (n % 2 == 0)

    def test_labels(self):
        assert F.FOURS.shot_label(3, 5, short=True) == "3rd end, lead's third rock"
        assert F.FOURS.shot_label(3, 7, short=True) == "3rd end, second's first rock"
        assert F.FOURS.shot_label(3, 13, short=True) == "3rd end, skip's first rock"

    def test_a_full_team_is_unchanged(self):
        for n in range(1, 17):
            assert F.FOURS.throw_info(n, short=False) == F.FOURS.throw_info(n)

    def test_doubles_has_no_team_of_three(self):
        assert F.DOUBLES.short_throw_table is None
        assert F.DOUBLES.throw_info(3, short=True) == F.DOUBLES.throw_info(3)
        assert "short_throw_table" not in F.DOUBLES.to_json()

    def test_the_fours_block_carries_the_table(self):
        assert F.FOURS.to_json()["short_throw_table"] == [1, 1, 1, 2, 2, 2, 4, 4]


class TestShortHandedOf:
    """Which colours played with three, as api.game_doc writes them into the
    document's format block."""

    def test_read_from_the_format_block(self):
        doc = {"format": {**F.FOURS.to_json(), "short_handed": ["red"]}}
        assert F.short_handed_of(doc) == frozenset({"red"})

    @pytest.mark.parametrize("doc", [
        {}, None, [], {"format": None}, {"format": {"name": "fours"}},
        {"format": {"name": "fours", "short_handed": "red"}},
        {"format": {"name": "fours", "short_handed": {"red": True}}},
    ])
    def test_anything_else_is_nobody(self, doc):
        assert F.short_handed_of(doc) == frozenset()

    def test_only_colours_count(self):
        doc = {"format": {"name": "fours", "short_handed": ["red", "blue", 3, None, ["yellow"]]}}
        assert F.short_handed_of(doc) == frozenset({"red"})

    def test_doubles_ignores_it(self):
        doc = {"format": {**F.DOUBLES.to_json(), "short_handed": ["red"]}}
        assert F.short_handed_of(doc) == frozenset()
