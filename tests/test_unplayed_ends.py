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


def built(number, delivered=16, released=16, start_s=None, kept=None,
          fmt=format_mod.FOURS):
    """An end that offered ``delivered``, saw ``released`` and kept ``kept``
    rocks (as many as were offered, up to an end's worth, unless told)."""
    start_s = number * 900.0 if start_s is None else start_s
    out = timeline.build_end(number, ("bottom", "top")[number % 2], start_s,
                             start_s + 850.0, [], fmt=fmt)
    out["deliveries_seen"], out["releases_seen"] = delivered, released
    kept = min(delivered, fmt.delivered_per_end) if kept is None else kept
    out["shots"] = [{"number": i + 1, "missing": False} for i in range(kept)]
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

    def test_no_end_is_one_or_two_rocks_released_or_not(self):
        """The user, flagging 10/01 doubles sheet 5's eighth end: "no end
        should ever have only one or two shots". 09/29 Supper sheet 4's last
        end kept two -- the 7 pm game's first rocks -- one released."""
        assert timeline.nothing_thrown(built(1, delivered=1, released=1))
        assert timeline.nothing_thrown(built(7, delivered=5, released=1, kept=2))

    def test_three_released_rocks_kept_are_an_end(self):
        """09/30 Womens sheet 5's last end, as the stream ran out."""
        assert not timeline.nothing_thrown(built(2, delivered=5, released=4, kept=3))

    def test_a_clean_up_that_pushed_many_stones_but_released_none(self):
        """10/01 doubles sheet 5 end 8, flagged twice: stones pushed about
        after the game offered eleven candidates, the rules kept two, and no
        release was seen. Of 569 hosted ends, 8 saw no release: 7 were a
        game's last end, keeping 0-3 rocks, and no real end went unreleased."""
        doubles = format_mod.DOUBLES
        assert timeline.nothing_thrown(built(8, delivered=11, released=0, kept=2, fmt=doubles))
        # 09/29 Supper sheet 5's last end: the same, three kept.
        assert timeline.nothing_thrown(built(6, delivered=11, released=0, kept=3))

    def test_with_no_release_seen_more_than_half_an_end_is_still_an_end(self):
        assert not timeline.nothing_thrown(built(1, delivered=12, released=0, kept=9))

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
        delivered, released, *kept = counts.get((game.index, end.number), (16, 16))
        out = built(end.number, delivered, released, start_s=end.start_s,
                    kept=kept[0] if kept else None)
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

    def test_a_clean_up_after_the_last_end_is_left_off(self, ends_as):
        """10/01 doubles sheet 5: "game ended after 7 ends but system says 2
        rocks in 8th end were played"."""
        ends_as[0, 8] = (11, 0, 2)
        game, = analyze.build_games(context(), [segment(8)])
        assert [e["number"] for e in game["ends"]] == list(range(1, 8))

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


@pytest.fixture
def ends_at(monkeypatch):
    """build_one_end faked by where an end starts, as the real one is -- a
    renumbered end is the same end -- recording each (game, number) it built."""
    counts, seen = {}, []

    def build_one_end(ctx, game, end, prev_end_s, board_score):
        delivered, released, *kept = counts.get(end.start_s, (16, 16))
        out = built(end.number, delivered, released, start_s=end.start_s,
                    kept=kept[0] if kept else None)
        out["score"] = None if board_score is None else dict(board_score)
        out["score_source"] = None if board_score is None else "board"
        seen.append((game.index, end.number, end.start_s))
        return out, end.end_s

    monkeypatch.setattr(analyze, "build_one_end", build_one_end)
    return counts, seen


class TestALaterGameStartingWithParkedStones:
    """Doubles sheet 4, 2026-10-04: the board split two games where stones
    sat parked in a house between them, and those stones were the second
    game's first "end" -- one rock kept, none released. Left on, every end
    after it is numbered one too high and the board's scores miss by an end."""

    def games(self):
        first = segment(6)
        second = replace(segment(7, start_s=6000.0), index=1)
        return first, second

    def test_it_is_left_off_and_the_rest_numbered_from_one(self, ends_at):
        counts, seen = ends_at
        first, second = self.games()
        counts[second.ends[0].start_s] = (2, 0, 1)
        g1, g2 = analyze.build_games(context(), [first, second])
        assert [e["number"] for e in g2["ends"]] == [1, 2, 3, 4, 5, 6]
        assert g2["ends"][0]["start_s"] == second.ends[1].start_s
        assert g2["start_s"] == pytest.approx(second.ends[1].start_s)
        # Numbered before it was built, not after: an end's number is in its labels.
        assert (1, 1, second.ends[1].start_s) in seen

    def test_the_board_is_read_for_the_game_without_it(self, ends_at):
        counts, _seen = ends_at
        first, second = self.games()
        counts[second.ends[0].start_s] = (2, 0, 1)
        reads = []

        def read_board(g):
            reads.append((g.index, g.start_s, len(g.ends)))
            return scored(("red", 1), ("yellow", 2), n_ends=len(g.ends))

        _g1, g2 = analyze.build_games(context(), [first, second], read_board=read_board)
        assert reads[-1] == (1, second.ends[1].start_s, 6)
        assert [e["score"] for e in g2["ends"][:2]] == [{"red": 1, "yellow": 0},
                                                       {"red": 0, "yellow": 2}]

    def test_the_first_game_keeps_its_first_end(self, ends_at):
        """A stream joined late opens on an end with rocks missing; the first
        game's first end is never dropped from the front."""
        counts, _seen = ends_at
        first, _second = self.games()
        counts[first.ends[0].start_s] = (2, 0, 1)
        g1, = analyze.build_games(context(), [first])
        assert [e["number"] for e in g1["ends"]] == [1, 2, 3, 4, 5, 6]

    def test_a_later_game_of_nothing_but_parked_stones_is_no_game(self, ends_at):
        counts, _seen = ends_at
        first, _second = self.games()
        parked = replace(segment(1, start_s=6000.0), index=1)
        counts[parked.ends[0].start_s] = (2, 0, 1)
        got = analyze.build_games(context(), [first, parked])
        assert [len(g["ends"]) for g in got] == [6]
