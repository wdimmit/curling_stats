"""Any signed-in person can fill in an end the wall board didn't read.

The scores live on the game, so every chart and link of it shows them; they
fill gaps and never beat the board."""

import copy
import logging

import pytest

from tests.test_service_api import VID, sample_doc, work_through
from tests.test_service_auth import ALEX, SARAH, post, w  # noqa: F401  (w is a fixture)


def scored_doc(unread=(3,), schema=8):
    """Three ends; the board read all but ``unread``."""
    d = sample_doc(games=1)
    d["schema_version"] = schema
    g = d["games"][0]
    base = g["ends"][0]
    board = {1: {"red": 1, "yellow": 0}, 2: {"red": 0, "yellow": 2}, 3: {"red": 0, "yellow": 1}}
    g["ends"] = []
    for n in (1, 2, 3):
        e = copy.deepcopy(base)
        sc = None if n in unread else board[n]
        e.update(number=n, start_s=base["start_s"] + 900.0 * (n - 1),
                 end_s=base["start_s"] + 900.0 * n - 60, hammer="yellow",
                 score=sc, score_source="board" if sc else None)
        g["ends"].append(e)
    g["final"] = None
    g["scoreboard"] = {"per_end": {str(n): board[n] for n in (1, 2, 3) if n not in unread},
                       "unread_ends": list(unread), "final": None, "scores_withheld": None}
    return d


def a_scored_game(w, doc=None):  # noqa: F811  (w: the fixture, passed through)
    slug = post(w, "/api/submissions", {"url": f"https://youtu.be/{VID}"}).json()["slug"]
    work_through(w, doc=doc or scored_doc(), games=1)
    chart = w["repo"].get_chart(slug)
    return slug, chart.share_slug, chart.source_id


def end_of(w, path, n):  # noqa: F811
    return w["client"].get(path).json()["games"][0]["ends"][n - 1]


class TestEnteringAScore:
    def test_signed_out_is_turned_away(self, w):  # noqa: F811
        _, _, src = a_scored_game(w)
        assert post(w, f"/api/games/{src}/scores", {"end": 3, "red": 0, "yellow": 3}).status_code == 401

    def test_an_unread_end_takes_a_score(self, w):  # noqa: F811
        slug, _, src = a_scored_game(w)
        r = post(w, f"/api/games/{src}/scores", {"end": 3, "red": 0, "yellow": 3}, SARAH)
        assert r.status_code == 200, r.text
        assert r.json()["entered_scores"]["3"]["yellow"] == 3
        assert "by" not in r.json()["entered_scores"]["3"]
        end = end_of(w, f"/c/{slug}/timeline.json", 3)
        assert end["score"] == {"red": 0, "yellow": 3} and end["score_source"] == "entered"
        game = w["client"].get(f"/c/{slug}/timeline.json").json()["games"][0]
        assert game["final"] == {"red": 1, "yellow": 5}

    def test_every_link_to_the_game_shows_it(self, w):  # noqa: F811
        _, share, src = a_scored_game(w)
        post(w, f"/api/games/{src}/scores", {"end": 3, "red": 2, "yellow": 0}, SARAH)
        for path in (f"/s/{share}/timeline.json", f"/g/{src}/timeline.json"):
            assert end_of(w, path, 3)["score"] == {"red": 2, "yellow": 0}, path

    def test_a_blank_end(self, w):  # noqa: F811
        slug, _, src = a_scored_game(w)
        assert post(w, f"/api/games/{src}/scores", {"end": 3, "red": 0, "yellow": 0},
                    SARAH).status_code == 200
        assert end_of(w, f"/c/{slug}/timeline.json", 3)["score"] == {"red": 0, "yellow": 0}

    def test_an_end_the_board_read_is_refused_with_the_boards_score(self, w):  # noqa: F811
        _, _, src = a_scored_game(w)
        r = post(w, f"/api/games/{src}/scores", {"end": 1, "red": 0, "yellow": 4}, SARAH)
        assert r.status_code == 409
        assert r.json()["detail"]["score"] == {"red": 1, "yellow": 0}

    @pytest.mark.parametrize("body", [
        {"end": 3, "red": 1, "yellow": 1},      # both teams scored
        {"end": 3, "red": 9, "yellow": 0},      # more than a team has stones
        {"end": 3, "red": -1, "yellow": 0},
        {"end": 3, "red": "2", "yellow": 0},
        {"end": 3, "red": True, "yellow": 0},
        {"end": 0, "red": 1, "yellow": 0},
        {"end": 4, "red": 1, "yellow": 0},      # the game has three ends
        {"red": 1, "yellow": 0},
    ])
    def test_a_bad_score_is_refused(self, w, body):  # noqa: F811
        _, _, src = a_scored_game(w)
        assert post(w, f"/api/games/{src}/scores", body, SARAH).status_code == 422

    def test_eight_is_the_most_in_fours(self, w):  # noqa: F811
        _, _, src = a_scored_game(w)
        assert post(w, f"/api/games/{src}/scores", {"end": 3, "red": 8, "yellow": 0},
                    SARAH).status_code == 200

    def test_clearing_puts_the_end_back_to_unread(self, w):  # noqa: F811
        slug, _, src = a_scored_game(w)
        post(w, f"/api/games/{src}/scores", {"end": 3, "red": 1, "yellow": 0}, SARAH)
        r = post(w, f"/api/games/{src}/scores", {"end": 3, "clear": True}, ALEX)
        assert r.status_code == 200 and r.json()["entered_scores"] == {}
        assert end_of(w, f"/c/{slug}/timeline.json", 3)["score"] is None

    def test_two_ends_are_kept_apart(self, w):  # noqa: F811
        slug, _, src = a_scored_game(w, scored_doc(unread=(2, 3)))
        post(w, f"/api/games/{src}/scores", {"end": 2, "red": 1, "yellow": 0}, SARAH)
        post(w, f"/api/games/{src}/scores", {"end": 3, "red": 0, "yellow": 2}, ALEX)
        game = w["client"].get(f"/c/{slug}/timeline.json").json()["games"][0]
        assert [e["score_source"] for e in game["ends"]] == ["board", "entered", "entered"]
        assert game["final"] == {"red": 2, "yellow": 2}

    def test_an_unknown_game_is_404(self, w):  # noqa: F811
        assert post(w, "/api/games/s_nope/scores", {"end": 1, "red": 1, "yellow": 0},
                    SARAH).status_code == 404

    def test_a_game_analysed_before_board_reading_is_refused(self, w):  # noqa: F811
        _, _, src = a_scored_game(w, scored_doc(schema=3))
        assert post(w, f"/api/games/{src}/scores", {"end": 3, "red": 1, "yellow": 0},
                    SARAH).status_code == 409

    def test_the_change_is_logged_with_who(self, w, caplog):  # noqa: F811
        _, _, src = a_scored_game(w)
        with caplog.at_level(logging.INFO):
            post(w, f"/api/games/{src}/scores", {"end": 3, "red": 0, "yellow": 3}, SARAH)
        assert any("score for end 3" in r.getMessage() and "uid-sarah" in r.getMessage()
                   for r in caplog.records)
