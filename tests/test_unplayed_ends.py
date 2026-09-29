"""An end in which nobody threw a rock is not an end.

Monday sheet 3, 2026-09-28: after the late game's eighth end the rocks sat in
the bottom house for 280 s while they were pushed back, which clears
``MIN_END_S``, and the game got a ninth end that saw one delivery, no release
and no stone at rest. Across the 56 hosted games every end like that -- none
of 0-2 deliveries released -- was rocks left in a house: after a game, before
one, or in a practice session posted as a game. The real ends that read short
were still released six or more times.
"""

from dataclasses import replace

import pytest

from curling_score import analyze, timeline
from curling_score.game import format as format_mod, scoreboard as sb
from curling_score.game.segment import EndSegment, GameSegment


def built(number, delivered=16, released=16, start_s=None):
    start_s = number * 900.0 if start_s is None else start_s
    out = timeline.build_end(number, ("bottom", "top")[number % 2], start_s,
                             start_s + 850.0, [], fmt=format_mod.FOURS)
    out["deliveries_seen"], out["releases_seen"] = delivered, released
    return out


def segment(n, start_s=0.0):
    ends = [EndSegment(number=i, house=("bottom", "top")[i % 2],
                       start_s=start_s + i * 900.0, end_s=start_s + i * 900.0 + 850.0)
            for i in range(1, n + 1)]
    return GameSegment(index=0, start_s=ends[0].start_s, end_s=ends[-1].end_s, ends=ends)


class TestNothingThrown:
    def test_rocks_pushed_back_after_the_game(self):
        """Sheet 3 end 9: one delivery, no release."""
        assert timeline.nothing_thrown(built(9, delivered=1, released=0))

    def test_rocks_left_in_a_house_with_nothing_moving(self):
        assert timeline.nothing_thrown(built(1, delivered=0, released=0))
        assert timeline.nothing_thrown(built(1, delivered=2, released=0))

    def test_a_real_end_that_read_short_was_still_released(self):
        """4/2 sheet 5 end 7: six deliveries, six releases."""
        assert not timeline.nothing_thrown(built(7, delivered=6, released=6))

    def test_one_release_is_a_rock_thrown(self):
        assert not timeline.nothing_thrown(built(1, delivered=1, released=1))

    def test_a_house_full_of_deliveries_is_an_end_even_with_no_release_seen(self):
        """The thrower's panel can miss every release; the deliveries decide."""
        assert not timeline.nothing_thrown(built(1, delivered=16, released=0))

    def test_an_end_that_never_counted_either_is_kept(self):
        end = timeline.build_end(1, "top", 0.0, 850.0, [], fmt=format_mod.FOURS)
        assert not timeline.nothing_thrown(end)


def scored(*per_end, n_ends):
    """A board read giving each end in ``per_end`` its score, in order."""
    cards, total = [], {"red": 0, "yellow": 0}
    for number, (color, points) in enumerate(per_end, start=1):
        total[color] += points
        cards.append(sb.Card(slot=total[color], end=number, confidence=1.0))
    colors = [c for c, _ in per_end]
    board = sb.CardBoard(
        red=tuple(c for c, col in zip(cards, colors) if col == "red"),
        yellow=tuple(c for c, col in zip(cards, colors) if col == "yellow"))
    got = sb.GameBoard(scores=sb.per_end_from_cards(board, n_ends), read_at_s=0.0,
                       reads=1, board=board)
    return analyze.board_block(got)


@pytest.fixture
def ends_as(monkeypatch):
    """build_one_end faked: each end gets the delivery and release counts the
    test gives for its (game, end), and its score from the board it was handed."""
    counts = {}

    def build_one_end(ctx, game, end, prev_end_s, board_score):
        delivered, released = counts.get((game.index, end.number), (16, 16))
        out = built(end.number, delivered, released, start_s=end.start_s)
        out["score"] = None if board_score is None else dict(board_score)
        out["score_source"] = None if board_score is None else "board"
        return out, end.end_s

    monkeypatch.setattr(analyze, "build_one_end", build_one_end)
    return counts


def context():
    return analyze.EndContext(path="v.mp4", read_path="v.mp4", read_setups={},
                              sideviews=None, detector=None, broom_model=None,
                              line_model=None, fmt=format_mod.FOURS)


class TestBuildGames:
    def test_a_last_end_with_nothing_thrown_is_left_off(self, ends_as):
        ends_as[0, 9] = (1, 0)
        game, = analyze.build_games(context(), [segment(9)])
        assert [e["number"] for e in game["ends"]] == list(range(1, 9))
        assert game["end_s"] == pytest.approx(8 * 900.0 + 850.0)

    def test_the_board_is_read_again_for_the_game_without_it(self, ends_as):
        """Read at the end of the rocks pushed back, the board had already been
        cleared; the game's own last end is where its score is."""
        ends_as[0, 3] = (1, 0)
        reads = []

        def read_board(g):
            reads.append((g.end_s, len(g.ends)))
            if len(g.ends) == 3:
                return None
            return scored(("red", 1), ("yellow", 2), n_ends=len(g.ends))

        game, = analyze.build_games(context(), [segment(3)], read_board=read_board)
        assert reads == [(3 * 900.0 + 850.0, 3), (2 * 900.0 + 850.0, 2)]
        assert [e["score"] for e in game["ends"]] == [{"red": 1, "yellow": 0},
                                                     {"red": 0, "yellow": 2}]
        assert game["scoreboard"]["unread_ends"] == []

    def test_an_end_with_nothing_thrown_in_the_middle_is_kept(self, ends_as):
        ends_as[0, 2] = (1, 0)
        game, = analyze.build_games(context(), [segment(3)])
        assert [e["number"] for e in game["ends"]] == [1, 2, 3]

    def test_a_game_of_nothing_thrown_is_no_game_and_the_rest_are_renumbered(self, ends_as):
        """Sunday evening sheet 5, 2026-09-27: two one-end "games" of rocks
        left in the house, neither released."""
        ends_as[0, 1] = (1, 0)
        practice = segment(1)
        real = replace(segment(2, start_s=5000.0), index=1)
        got = analyze.build_games(context(), [practice, real])
        assert [(g["index"], len(g["ends"])) for g in got] == [(0, 2)]
        assert got[0]["start_s"] == pytest.approx(5900.0)

    def test_without_a_board_every_game_is_built_unscored(self, ends_as):
        g1, g2 = analyze.build_games(context(), [segment(2), replace(segment(2, 9000.0), index=1)])
        assert [g1["index"], g2["index"]] == [0, 1]
        assert all(e["score"] is None for e in g1["ends"] + g2["ends"])
