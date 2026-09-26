"""The pieces ``analyze`` is built from, each usable on its own.

A live stream is processed one end at a time as the end finishes, so building
an end, finishing a game and reading a game's board have to be callable
without running the whole video through ``analyze``.
"""

import pytest

from curling_score import analyze
from curling_score.game import format as format_mod, scoreboard as sb
from curling_score.game.profile import PanelSetup
from curling_score.game.segment import EndSegment, GameSegment
from curling_score.geometry.calibrate import PanelCalib


def panel(flipped):
    return PanelSetup(rect=(0, 0, 297, 514),
                      calib=PanelCalib(center_px=(150.0, 160.0), px_per_m=76.0,
                                       edge_erosion_px=2.0, residual_m=0.001,
                                       flipped=flipped))


@pytest.fixture
def seen(monkeypatch):
    """Detection that finds nothing, recording what it was asked for."""
    calls = []

    def detect_end(path, setup, end, fps, detector=None, use_cache=True, from_s=None):
        calls.append({"what": "end", "path": path, "use_cache": use_cache,
                      "from_s": from_s})
        return []

    def detect_span(path, setup, start_s, end_s, fps, detector=None, use_cache=True):
        calls.append({"what": "span", "path": path, "use_cache": use_cache,
                      "from_s": start_s})
        return []

    monkeypatch.setattr(analyze.sequence, "detect_end", detect_end)
    monkeypatch.setattr(analyze.sequence, "detect_span", detect_span)
    return calls


def context(use_cache=True):
    return analyze.EndContext(
        path="original.mp4", read_path="proxy.mp4",
        read_setups={"top": panel(False), "bottom": panel(True)},
        sideviews=None, detector=None, broom_model=None, line_model=None,
        fmt=format_mod.FOURS, use_cache=use_cache,
    )


GAME = GameSegment(index=0, start_s=1000.0, end_s=3000.0, ends=[
    EndSegment(number=1, house="top", start_s=1000.0, end_s=1900.0),
    EndSegment(number=2, house="bottom", start_s=2000.0, end_s=3000.0),
])


class TestOneEndIsBuiltOnItsOwn:
    def test_an_end_with_nothing_seen_is_empty_and_closes_at_its_boundary(self, seen):
        built, closed = analyze.build_one_end(context(), GAME, GAME.ends[0],
                                              prev_end_s=None, board_score=None)
        assert (built["number"], built["house"]) == (1, "top")
        assert built["shots"] == []
        assert closed == GAME.ends[0].end_s

    def test_the_board_score_it_is_handed_is_the_ends_score(self, seen):
        built, _ = analyze.build_one_end(context(), GAME, GAME.ends[0],
                                         prev_end_s=None,
                                         board_score={"red": 1, "yellow": 0})
        assert built["score"] == {"red": 1, "yellow": 0}

    def test_detection_reads_the_read_path_and_can_skip_the_cache(self, seen):
        analyze.build_one_end(context(use_cache=False), GAME, GAME.ends[0],
                              prev_end_s=None, board_score=None)
        assert {c["what"] for c in seen} == {"end", "span"}
        assert all(c["path"] == "proxy.mp4" for c in seen)
        assert all(c["use_cache"] is False for c in seen)

    def test_a_later_end_runs_up_from_where_the_previous_one_closed(self, seen):
        analyze.build_one_end(context(), GAME, GAME.ends[1],
                              prev_end_s=1850.0, board_score=None)
        expected = analyze.run_up_from(1850.0, GAME.ends[1].start_s,
                                       crossed_games=False)
        assert {c["from_s"] for c in seen} == {expected}

    def test_the_first_end_of_a_game_runs_up_as_a_crossed_game(self, seen):
        analyze.build_one_end(context(), GAME, GAME.ends[0],
                              prev_end_s=500.0, board_score=None)
        expected = analyze.run_up_from(500.0, GAME.ends[0].start_s,
                                       crossed_games=True)
        assert seen[0]["from_s"] == expected


def two_empty_ends(seen_fixture):
    return [analyze.build_one_end(context(), GAME, e, prev_end_s=None,
                                  board_score=None)[0] for e in GAME.ends]


def game_board(cards_red=(), n_ends=2):
    board = sb.CardBoard(yellow=(), red=tuple(cards_red))
    scores = sb.per_end_from_cards(board, n_ends)
    return analyze.board_block(sb.GameBoard(scores=scores, read_at_s=2900.0,
                                            reads=1, board=board))


class TestAGameIsFinishedFromItsEnds:
    def test_no_board_means_no_scoreboard_block(self, seen):
        game = analyze.finish_game(GAME, two_empty_ends(seen), None,
                                   format_mod.FOURS)
        assert game["scoreboard"] is None
        assert len(game["ends"]) == 2

    def test_a_board_with_unread_ends_neither_agrees_nor_disagrees(self, seen):
        game = analyze.finish_game(GAME, two_empty_ends(seen), game_board(),
                                   format_mod.FOURS)
        assert game["scoreboard"]["agrees_with_detection"] is None
        assert all(e["scoreboard_agrees"] is None for e in game["ends"])

    def test_leading_practice_the_board_cannot_cover_withholds_its_scores(self, seen):
        # One card for end 1 against two detected ends, the first of them short
        # of sixteen rocks: the practice signature.
        board = game_board(cards_red=[sb.Card(slot=1, end=1, confidence=1.0)])
        game = analyze.finish_game(GAME, two_empty_ends(seen), board,
                                   format_mod.FOURS)
        assert game["scoreboard"]["scores_withheld"]
        assert all(e["score"] is None for e in game["ends"])


class TestTheBoardBlock:
    def test_it_carries_the_read_and_every_card(self):
        board = sb.CardBoard(yellow=(), red=(sb.Card(slot=2, end=1, confidence=0.99991),))
        got = sb.GameBoard(scores=sb.per_end_from_cards(board, 2), read_at_s=2900.123,
                           reads=3, board=board)
        block = analyze.board_block(got)
        assert block.block["read_at_s"] == 2900.12
        assert block.block["reads"] == 3
        assert block.block["cards"]["red"] == [{"slot": 2, "end": 1, "confidence": 0.9999}]
        assert block.block["per_end"] == {"1": {"red": 2, "yellow": 0}}
        assert block.scores is got.scores


class TestCalibratingFromFrames:
    @pytest.fixture
    def fake_geometry(self, monkeypatch):
        import numpy as np

        layout = analyze.layout.PanelLayout(top=(810, 10, 297, 514),
                                            bottom=(810, 554, 297, 516))
        monkeypatch.setattr(analyze.layout, "detect_panels", lambda frames: layout)
        monkeypatch.setattr(analyze.profile, "calibrate_panels",
                            lambda frames, panels: {"top": panel(False),
                                                    "bottom": panel(True)})
        return [np.zeros((1080, 1920, 3), np.uint8) for _ in range(3)]

    def test_without_the_side_views_it_gives_none(self, fake_geometry):
        panels, setups, sideviews = analyze.calibrate_from(
            fake_geometry, skip_longview=True, progress=lambda m: None)
        assert panels.top == (810, 10, 297, 514)
        assert set(setups) == {"top", "bottom"}
        assert sideviews is None

    def test_side_views_that_cannot_be_read_cost_the_splits_not_the_run(
            self, fake_geometry, monkeypatch):
        def unreadable(*a, **k):
            raise analyze.sideview.SideViewError("no paint")

        monkeypatch.setattr(analyze.sideview, "locate", unreadable)
        said = []
        _, setups, sideviews = analyze.calibrate_from(
            fake_geometry, skip_longview=False, progress=said.append)
        assert sideviews is None and set(setups) == {"top", "bottom"}
        assert any("no splits" in m for m in said)
