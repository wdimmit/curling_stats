"""A practice session is not a game, and is left out of the catalogue.

Four were published as games in the week to 2026-10-06, each confirmed on
video or by its sheet's schedule: two people throwing both colours on 10/04
Sunday Open Doubles sheet 1, one person alone on 10/02 Friday sheet 5 after
the league game, and both 10/04 Sunday Afternoon sheet 5 "games". Each read
no board card all session and kept well under an end's rocks; no real game of
127 does both.
"""

from dataclasses import replace

from curling_score import analyze, timeline
from tests.test_unplayed_ends import built, context, segment


def end(kept, expected=16):
    return {"shots_expected": expected,
            "shots": [{"number": i + 1, "missing": i >= kept} for i in range(expected)]}


def game(kept_per_end, expected=16, cards=False, in_progress=False):
    return {"ends": [end(k, expected) for k in kept_per_end],
            "scoreboard": ({"per_end": {"1": {"red": 1, "yellow": 0}}} if cards else None),
            "in_progress": in_progress}


class TestIsPractice:
    def test_the_four_practice_sessions_of_the_week(self):
        # 10/04 doubles sheet 1: 10, 3 of 4 listed, 7, 5 of 10 kept
        assert timeline.is_practice(game([10, 3, 7, 5], expected=10))
        # 10/02 Friday sheet 5, game 2: 81% kept
        assert timeline.is_practice(game([13, 13]))
        # 10/04 Sunday Afternoon sheet 5: 58% and 38%
        assert timeline.is_practice(game([9, 8, 10, 10]))
        assert timeline.is_practice(game([5, 7]))

    def test_a_real_game_whose_board_was_never_read_is_a_game(self):
        """17 of 122 real games have no board card -- a board the reader
        could not read -- and every one kept 85% or more of its rocks."""
        assert not timeline.is_practice(game([16, 15, 16, 14, 16, 16]))

    def test_a_game_the_board_scored_is_a_game_however_few_rocks_it_kept(self):
        """sGYkR-Zw4MU kept 83%, the most short of any real game, and its
        board posted every end."""
        assert not timeline.is_practice(game([13, 14, 13, 13, 14, 13], cards=True))

    def test_a_board_read_with_no_cards_on_it_is_no_card(self):
        g = game([4, 5])
        g["scoreboard"] = {"per_end": {}}
        assert timeline.is_practice(g)

    def test_a_game_with_no_ends_says_nothing(self):
        assert not timeline.is_practice({"ends": [], "scoreboard": None})


class TestTheGameSummary:
    """What the worker and the live lane tell the service about each game."""

    def test_a_practice_session_says_so(self):
        g = {"index": 1, "start_s": 7630.0, "end_s": 9605.0, "ends": [{}, {}], "practice": True}
        assert timeline.game_summary(g) == {"index": 1, "start_s": 7630.0, "end_s": 9605.0,
                                            "ends": 2, "practice": True}

    def test_a_game_is_summarised_as_it_always_was(self):
        g = {"index": 0, "start_s": 10.0, "end_s": 6000.0, "ends": [{}, {}], "practice": False}
        assert timeline.game_summary(g) == {"index": 0, "start_s": 10.0, "end_s": 6000.0, "ends": 2}


class TestARecordingsGames:
    def test_a_practice_session_is_marked_and_a_game_is_not(self, monkeypatch):
        def build_one_end(ctx, game, end, prev_end_s, board_score):
            kept = 6 if game.index == 1 else 16
            return built(end.number, kept, kept, start_s=end.start_s, kept=kept), end.end_s

        monkeypatch.setattr(analyze, "build_one_end", build_one_end)
        real, practice = segment(4), replace(segment(2, start_s=9000.0), index=1)
        got = analyze.build_games(context(), [real, practice])
        assert [g["practice"] for g in got] == [False, True]
