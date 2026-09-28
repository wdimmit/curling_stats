"""Two games the empty-sheet rule ran together, told apart by the wall
scoreboard: cleared after a game, it reads blank across the changeover."""

import pytest

from curling_score.game import boardsplit as B
from curling_score.game.segment import EndSegment, GameSegment

MIN = 60.0


def game(spans, index=0):
    """A game from (start_min, end_min) end spans, houses alternating."""
    ends = [EndSegment(number=i + 1, house=("bottom", "top")[i % 2],
                       start_s=a * MIN, end_s=b * MIN) for i, (a, b) in enumerate(spans)]
    return GameSegment(index=index, start_s=ends[0].start_s, end_s=ends[-1].end_s, ends=ends)


# Doubles sheet 4, 2026-09-27: two six-end games, 400 s apart, merged because
# stones stayed in view through the changeover.
SHEET4 = [(4.3, 16.7), (16.8, 28.2), (28.3, 41.0), (41.1, 56.1), (56.2, 68.3), (68.4, 83.2),
          (89.8, 102.2), (102.2, 114.3), (114.4, 125.5), (125.6, 138.3), (138.4, 150.1),
          (150.2, 161.2)]


class Board:
    """A board read from a list of (minute, state) -- state "cards", "blank",
    or None for unreadable -- answering any window, and counting the reads."""

    def __init__(self, states):
        self.states = [(m * MIN, s) for m, s in states]
        self.calls = []

    def __call__(self, t0, t1):
        self.calls.append((t0, t1))
        return [(t, s) for t, s in self.states if t0 <= t <= t1]


class TestSplitGames:
    def test_two_games_run_together_are_split_where_the_board_went_blank(self):
        board = Board([(79.5, "cards"), (81.0, "cards"), (82.0, None), (84.0, "blank"),
                       (90.0, None), (93.5, "blank"), (96.5, "blank"), (116.5, "cards")])
        got = B.split_games([game(SHEET4)], board)
        assert [len(g.ends) for g in got] == [6, 6]
        assert [g.index for g in got] == [0, 1]
        assert [e.number for e in got[1].ends] == [1, 2, 3, 4, 5, 6]
        assert got[1].start_s == pytest.approx(89.8 * MIN)
        assert got[0].end_s == pytest.approx(83.2 * MIN)

    def test_a_long_gap_with_the_score_still_up_is_one_game(self):
        """kS3r-6GtdKA: the board still showed the finished game's score."""
        board = Board([(80.0, "cards"), (84.0, "cards"), (93.0, "cards"), (97.0, "cards")])
        assert [len(g.ends) for g in B.split_games([game(SHEET4)], board)] == [12]

    def test_one_blank_read_is_not_a_new_game(self):
        """7pm sheet 3 read blank once mid-game, between two readings of cards."""
        board = Board([(81.0, "cards"), (84.0, "blank"), (86.0, "cards"), (95.0, "cards")])
        assert [len(g.ends) for g in B.split_games([game(SHEET4)], board)] == [12]

    def test_blank_before_any_card_is_the_game_s_own_start_not_a_change(self):
        board = Board([(80.0, "blank"), (84.0, "blank"), (93.5, "blank"), (116.5, "cards")])
        assert [len(g.ends) for g in B.split_games([game(SHEET4)], board)] == [12]

    def test_a_board_that_cannot_be_read_splits_nothing(self):
        board = Board([(t, None) for t in range(60, 120)])
        assert [len(g.ends) for g in B.split_games([game(SHEET4)], board)] == [12]

    def test_without_a_long_gap_the_board_is_never_read(self):
        """Ends run into each other within a game -- 5-10 s apart on every game
        of 2026-09-27 -- so a gap that short is no changeover, whatever the board."""
        spans = [(a, b) for a, b in SHEET4[:6]]
        board = Board([(10.0, "cards"), (20.0, "blank"), (21.0, "blank")])
        assert [len(g.ends) for g in B.split_games([game(spans)], board)] == [6]
        assert board.calls == []

    def test_the_board_is_read_only_around_the_long_gap(self):
        board = Board([(81.0, "cards"), (84.0, "blank"), (85.0, "blank")])
        B.split_games([game(SHEET4)], board)
        (t0, t1), = board.calls
        assert t0 <= 83.2 * MIN and t1 >= 89.8 * MIN
        assert t1 - t0 <= 30 * MIN

    def test_games_already_apart_keep_their_order_and_are_renumbered(self):
        first = game([(0, 12), (12.1, 25)], index=0)
        board = Board([(80.0, "cards"), (84.0, "blank"), (85.0, "blank")])
        got = B.split_games([first, game(SHEET4, index=1)], board)
        assert [g.index for g in got] == [0, 1, 2]
        assert [len(g.ends) for g in got] == [2, 6, 6]

    def test_the_games_given_are_left_as_they_were(self):
        g = game(SHEET4)
        board = Board([(81.0, "cards"), (84.0, "blank"), (85.0, "blank")])
        B.split_games([g], board)
        assert len(g.ends) == 12 and [e.number for e in g.ends] == list(range(1, 13))

    def test_an_open_game_s_last_piece_stays_open(self):
        g = game(SHEET4)
        g.closed = False
        board = Board([(81.0, "cards"), (84.0, "blank"), (85.0, "blank")])
        got = B.split_games([g], board)
        assert [x.closed for x in got] == [True, False]


class TestBoardStates:
    def test_each_step_reads_the_median_of_its_keyframes(self, monkeypatch):
        from curling_score.game import scoreboard as SB
        from curling_score.ingest import frames as F

        kf = [(float(t), t) for t in range(0, 200, 5)]
        monkeypatch.setattr(F, "keyframe_sweep", lambda path, start_s=None, end_s=None:
                            iter([(t, i) for t, i in kf if start_s <= t <= end_s]))
        monkeypatch.setattr(SB, "median_frame", lambda imgs: sum(imgs) / len(imgs))
        monkeypatch.setattr(SB, "find_board", lambda img: "geom")
        monkeypatch.setattr(SB, "is_readable", lambda img, geom: img < 150)
        monkeypatch.setattr(SB, "read_slots", lambda img, geom:
                            SB.BoardReading(yellow=set(), red=set()) if img >= 60
                            else SB.BoardReading(yellow={3}, red=set()))
        got = B.board_states("v.mp4", 0.0, 180.0)
        assert [s for _t, s in got] == ["cards", "cards", "blank", "blank", "blank", None, None]
        assert [t for t, _s in got] == [0.0, 30.0, 60.0, 90.0, 120.0, 150.0, 180.0]


class TestAnalyzeCallsIt:
    def test_it_splits_the_segmented_games_unless_the_board_is_skipped(self):
        from pathlib import Path
        src = (Path(__file__).resolve().parents[1] / "src/curling_score/analyze.py").read_text()
        i_seg = src.index("games = segment.segment_games(")
        i_split = src.index("boardsplit.split_games(")
        assert i_seg < i_split
        assert "skip_scoreboard" in src[i_seg:i_split + 300]
