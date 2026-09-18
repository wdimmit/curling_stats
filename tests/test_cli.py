"""The `curling-score analyze` summary line: what it says about a score."""

from curling_score import cli


class TestTheEndScoreLabel:
    """Minor 5: a withheld end is not an unposted one. The board *was* read;
    settle_board_scores (timeline.py) just could not place its scores against
    these ends -- a leading practice block, and a board short of the game --
    so every end's score came back off together, with the reason recorded on
    ``game.scoreboard.scores_withheld``. The viewer already tells the two
    apart (Watch.jsx, ChartPanel.jsx); the CLI printing "not posted" for both
    is the same confusion in a different surface.
    """

    def test_a_real_score_is_printed_as_is(self):
        assert cli._score_label({"red": 1, "yellow": 0}, None) == \
            {"red": 1, "yellow": 0}

    def test_an_end_the_board_never_reached_is_not_posted(self):
        assert cli._score_label(None, None) == "not posted"

    def test_an_end_on_a_game_with_no_scoreboard_block_is_not_posted(self):
        assert cli._score_label(None, {"scores_withheld": None}) == "not posted"

    def test_a_withheld_end_says_so_rather_than_not_posted(self):
        board = {"scores_withheld": "leading practice, board short of the ends"}
        got = cli._score_label(None, board)
        assert got != "not posted"
        assert "withheld" in got

    def test_a_withheld_end_is_distinct_from_an_unposted_one_in_wording(self):
        withheld = cli._score_label(None, {"scores_withheld": "no start time"})
        unposted = cli._score_label(None, None)
        assert withheld != unposted
