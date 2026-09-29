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

    def test_a_board_blank_early_in_a_game_is_not_a_changeover(self):
        """jgZ9wlxGYHM, PNWCA draw 7: with one or two cards up, the board
        flickered between cards and blank around a 2-minute gap after end 2
        of a real game. No game is over after two ends."""
        spans = [(0.0, 15.0), (15.1, 30.3), (32.8, 45.0), (45.1, 60.0), (60.1, 75.0)]
        board = Board([(26.0, "cards"), (28.0, "cards"), (30.0, "blank"), (30.5, "blank"),
                       (31.0, "blank"), (33.0, "cards")])
        assert [len(g.ends) for g in B.split_games([game(spans)], board)] == [5]

    def test_one_end_after_a_cleared_board_is_split_off(self):
        """a2U6XeQYcbA: six ends, the board cleared, then one more end 535 s
        later -- not the first game's seventh."""
        spans = SHEET4[:6] + [(92.1, 104.0)]
        board = Board([(80.0, "cards"), (85.0, "blank"), (86.0, "blank"), (95.0, "cards")])
        assert [len(g.ends) for g in B.split_games([game(spans)], board)] == [6, 1]

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



def seconds(spans, index=0):
    """A game from (start_s, end_s) end spans, houses alternating."""
    return game([(a / MIN, b / MIN) for a, b in spans], index=index)


# Monday sheet 5, 2026-09-28, the late game: both houses sat empty for 310 s
# after its second end, and the empty-sheet rule started a new game there.
# The board kept the first two ends' cards up through it.
LATE5_A = [(7210, 8065), (8255, 8905)]
LATE5_B = [(9215, 10150), (10155, 11020), (11100, 11895), (11915, 12805), (12830, 13730)]


class TestJoinGames:
    def test_a_pause_with_the_board_still_up_is_the_same_game(self):
        board = Board([(140.0, "cards"), (147.0, "cards"), (150.0, "cards"),
                       (154.0, "cards"), (158.0, "cards")])
        got = B.join_games([seconds(LATE5_A, 0), seconds(LATE5_B, 1)], board)
        assert [len(g.ends) for g in got] == [7]
        assert [e.number for e in got[0].ends] == [1, 2, 3, 4, 5, 6, 7]
        assert got[0].start_s == pytest.approx(7210) and got[0].end_s == pytest.approx(13730)
        assert [e.house for e in got[0].ends] == ["bottom", "top", "bottom", "top",
                                                  "bottom", "top", "bottom"]

    def test_a_changeover_where_the_board_was_cleared_stays_two_games(self):
        """Doubles sheet 4, 2026-09-27: 400 s apart, the board cleared."""
        board = Board([(81.0, "cards"), (84.0, "blank"), (84.5, "blank"),
                       (93.0, "blank"), (100.0, "cards")])
        got = B.join_games([game(SHEET4[:6], 0), game(SHEET4[6:], 1)], board)
        assert [len(g.ends) for g in got] == [6, 6]

    def test_a_board_that_cannot_be_read_joins_nothing(self):
        board = Board([(t, None) for t in range(100, 240)])
        got = B.join_games([seconds(LATE5_A, 0), seconds(LATE5_B, 1)], board)
        assert [len(g.ends) for g in got] == [2, 5]

    def test_cards_before_the_gap_but_nothing_read_after_it_join_nothing(self):
        board = Board([(140.0, "cards"), (147.0, "cards"), (154.0, None), (158.0, None)])
        got = B.join_games([seconds(LATE5_A, 0), seconds(LATE5_B, 1)], board)
        assert [len(g.ends) for g in got] == [2, 5]

    def test_a_board_with_no_cards_yet_says_nothing_either_way(self):
        board = Board([(140.0, "blank"), (147.0, "blank"), (154.0, "blank"),
                       (158.0, "blank")])
        got = B.join_games([seconds(LATE5_A, 0), seconds(LATE5_B, 1)], board)
        assert [len(g.ends) for g in got] == [2, 5]

    def test_a_gap_longer_than_any_pause_is_never_read(self):
        """Monday sheet 1, 2026-09-28: the draws were 1250 s apart."""
        board = Board([(t, "cards") for t in range(0, 260)])
        early = seconds([(80, 885), (980, 1745), (1815, 2760), (2765, 3835),
                         (3840, 4860), (4915, 5890)], 0)
        late = seconds([(7140, 7975), (8020, 9115)], 1)
        got = B.join_games([early, late], board)
        assert [len(g.ends) for g in got] == [6, 2]
        assert board.calls == []

    def test_the_board_is_read_only_around_the_gap(self):
        board = Board([(140.0, "cards"), (147.0, "cards"), (154.0, "cards"),
                       (158.0, "cards")])
        B.join_games([seconds(LATE5_A, 0), seconds(LATE5_B, 1)], board)
        (t0, t1), = board.calls
        assert t0 <= 8905 and t1 >= 9215
        assert t1 - t0 <= 30 * MIN

    def test_only_the_paused_pair_is_joined_and_the_rest_renumbered(self):
        early = seconds([(130, 1130), (1135, 2010), (2015, 3145), (3225, 4155),
                         (4160, 5300), (5390, 6245)], 0)
        board = Board([(95.0, "cards"), (104.0, "blank"), (105.0, "blank"),
                       (140.0, "cards"), (147.0, "cards"), (154.0, "cards"),
                       (158.0, "cards")])
        got = B.join_games([early, seconds(LATE5_A, 1), seconds(LATE5_B, 2)], board)
        assert [len(g.ends) for g in got] == [6, 7]
        assert [g.index for g in got] == [0, 1]

    def test_the_games_given_are_left_as_they_were(self):
        a, b = seconds(LATE5_A, 0), seconds(LATE5_B, 1)
        board = Board([(140.0, "cards"), (147.0, "cards"), (154.0, "cards"),
                       (158.0, "cards")])
        B.join_games([a, b], board)
        assert [e.number for e in b.ends] == [1, 2, 3, 4, 5]
        assert len(a.ends) == 2 and b.index == 1

    def test_an_open_game_joined_on_stays_open(self):
        a, b = seconds(LATE5_A, 0), seconds(LATE5_B, 1)
        b.closed = False
        board = Board([(140.0, "cards"), (147.0, "cards"), (154.0, "cards"),
                       (158.0, "cards")])
        got, = B.join_games([a, b], board)
        assert got.closed is False

    def test_a_split_across_the_joined_gap_would_not_undo_it(self):
        """join, then split, as analyze runs them: the board that kept the game
        together cannot also have cleared across the same gap."""
        board = Board([(140.0, "cards"), (147.0, "cards"), (154.0, "cards"),
                       (158.0, "cards")])
        joined = B.join_games([seconds(LATE5_A, 0), seconds(LATE5_B, 1)], board)
        assert [len(g.ends) for g in B.split_games(joined, board)] == [7]


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
    def test_it_joins_then_splits_the_segmented_games_unless_the_board_is_skipped(self):
        from pathlib import Path
        src = (Path(__file__).resolve().parents[1] / "src/curling_score/analyze.py").read_text()
        i_seg = src.index("games = segment.segment_games(")
        i_join = src.index("boardsplit.join_games(")
        i_split = src.index("boardsplit.split_games(")
        assert i_seg < i_join < i_split
        assert "skip_scoreboard" in src[i_seg:i_join]
