"""A team that played with three (Source.short_handed): marked on the game by
anyone signed in, served into every chart of it, its rocks credited 3/3/2."""

import pytest

from curling_score.game import format as F
from tests.test_plays import a_played_game, get, played_doc, put
from tests.test_service_auth import ALEX, SARAH, give_role, post, w  # noqa: F401  (fixtures)

THREE = [(1, "lead", 1), (3, "lead", 2), (5, "lead", 3), (7, "second", 1),
         (9, "second", 2), (11, "second", 3), (13, "skip", 1), (15, "skip", 2)]
FOUR = [(1, "lead", 1), (3, "lead", 2), (5, "second", 1), (7, "second", 2),
        (9, "third", 1), (11, "third", 2), (13, "skip", 1), (15, "skip", 2)]


def teams(w, src, body, headers=SARAH):  # noqa: F811
    return post(w, f"/api/games/{src}/teams", body, headers)


def red(doc):
    return [(s["number"], s["position"], s["rock_of_player"])
            for s in doc["games"][0]["ends"][0]["shots"] if s["color"] == "red"]


@pytest.fixture(autouse=True)
def validated(w):  # noqa: F811
    """Marking a team goes through the teams endpoint, which only validated
    accounts may use (User.role) -- the same as naming one."""
    give_role(w, SARAH, "validated")


class TestMarkingIt:
    def test_an_account_nobody_validated_may_not_mark_one(self, w):  # noqa: F811
        _, _, src = a_played_game(w)
        assert teams(w, src, {"short_handed": ["red"]}, headers=ALEX).status_code == 403
        assert w["repo"].get_source(src).short_handed is None

    def test_a_signed_in_person_marks_a_team(self, w):  # noqa: F811
        _, _, src = a_played_game(w)
        r = teams(w, src, {"short_handed": ["red"]})
        assert r.status_code == 200, r.text
        assert r.json()["short_handed"] == ["red"]
        assert w["repo"].get_source(src).short_handed == ["red"]

    def test_names_alone_leave_it_as_it_was(self, w):  # noqa: F811
        _, _, src = a_played_game(w)
        teams(w, src, {"short_handed": ["red"]})
        assert teams(w, src, {"red": "Dimmit"}).json()["short_handed"] == ["red"]
        assert w["repo"].get_source(src).short_handed == ["red"]

    def test_an_empty_list_clears_it(self, w):  # noqa: F811
        _, _, src = a_played_game(w)
        teams(w, src, {"short_handed": ["red"]})
        assert teams(w, src, {"short_handed": []}).json()["short_handed"] == []
        assert w["repo"].get_source(src).short_handed is None

    def test_both_teams_can_be_three(self, w):  # noqa: F811
        _, _, src = a_played_game(w)
        r = teams(w, src, {"short_handed": ["yellow", "red"]})
        assert r.json()["short_handed"] == ["red", "yellow"]

    @pytest.mark.parametrize("bad", ["red", ["blue"], ["red", "red"], [1], [["red"]],
                                     None, {"red": True}])
    def test_anything_but_a_list_of_colours_is_refused(self, w, bad):  # noqa: F811
        _, _, src = a_played_game(w)
        r = teams(w, src, {"short_handed": bad})
        assert r.status_code == 422 and r.json()["detail"] == \
            "short_handed is a list of colours: red, yellow"
        assert w["repo"].get_source(src).short_handed is None

    def test_signed_out_it_is_read_only(self, w):  # noqa: F811
        _, _, src = a_played_game(w)
        assert teams(w, src, {"short_handed": ["red"]}, headers=None).status_code == 401

    def test_a_doubles_team_is_two_players(self, w):  # noqa: F811
        _, _, src = a_played_game(w, played_doc(F.DOUBLES))
        r = teams(w, src, {"short_handed": ["red"]})
        assert r.status_code == 422 and r.json()["detail"] == "a doubles team is two players"
        assert teams(w, src, {"short_handed": []}).status_code == 200


class TestServed:
    def test_the_review_link_credits_the_short_team_three_three_two(self, w):  # noqa: F811
        _, _, src = a_played_game(w)
        assert red(get(w, f"/g/{src}/timeline.json").json()) == FOUR
        teams(w, src, {"short_handed": ["red"]})
        doc = get(w, f"/g/{src}/timeline.json").json()
        assert red(doc) == THREE
        assert doc["format"]["short_handed"] == ["red"]
        assert doc["format"]["short_throw_table"] == [1, 1, 1, 2, 2, 2, 4, 4]
        assert doc["format"]["name"] == "fours"
        assert doc["games"][0]["ends"][0]["shots"][4]["label"] == "1st end, lead's third rock"

    def test_a_chart_made_before_gets_it_too(self, w):  # noqa: F811
        slug, share, src = a_played_game(w)
        teams(w, src, {"short_handed": ["red"]})
        assert red(get(w, f"/c/{slug}/timeline.json").json()) == THREE
        assert red(get(w, f"/s/{share}/timeline.json").json()) == THREE

    def test_unmarked_it_is_fours_again(self, w):  # noqa: F811
        _, _, src = a_played_game(w)
        teams(w, src, {"short_handed": ["red"]})
        teams(w, src, {"short_handed": []})
        doc = get(w, f"/g/{src}/timeline.json").json()
        assert red(doc) == FOUR and "short_handed" not in (doc.get("format") or {})

    def test_a_doubles_game_ignores_a_stray_mark(self, w):  # noqa: F811
        _, _, src = a_played_game(w, played_doc(F.DOUBLES))
        before = get(w, f"/g/{src}/timeline.json").json()
        w["repo"].update_source(src, short_handed=["red"])
        assert get(w, f"/g/{src}/timeline.json").json() == before


class TestPlays:
    def test_the_leads_report_gets_all_three_rocks(self, w):  # noqa: F811
        _, _, src = a_played_game(w)
        teams(w, src, {"short_handed": ["red"]})
        assert put(w, src, {"path": f"/g/{src}/", "color": "red", "slot": 1}).status_code == 200
        doc = get(w, f"/api/me/plays/{src}/doc").json()["doc"]
        mine = [s["number"] for s in doc["games"][0]["ends"][0]["shots"]
                if s["color"] == "red" and "track" in s]
        assert mine == [1, 3, 5]

    def test_third_is_refused_on_a_team_of_three(self, w):  # noqa: F811
        _, _, src = a_played_game(w)
        teams(w, src, {"short_handed": ["red"]})
        r = put(w, src, {"path": f"/g/{src}/", "color": "red", "slot": 3})
        assert r.status_code == 422
        assert r.json()["detail"] == "that team played with three: lead, second or skip"
        assert put(w, src, {"path": f"/g/{src}/", "color": "yellow", "slot": 3}).status_code == 200

    def test_a_third_saved_before_is_kept_and_credits_nothing(self, w):  # noqa: F811
        _, _, src = a_played_game(w)
        put(w, src, {"path": f"/g/{src}/", "color": "red", "slot": 3})
        teams(w, src, {"short_handed": ["red"]})
        [item] = get(w, "/api/me/plays").json()["plays"]
        assert (item["slot"], item["short_handed"]) == (3, ["red"])
        doc = get(w, f"/api/me/plays/{src}/doc").json()["doc"]
        assert not any("track" in s for s in doc["games"][0]["ends"][0]["shots"]
                       if s["color"] == "red")

    def test_the_list_says_nobody_when_nobody_was_short(self, w):  # noqa: F811
        _, _, src = a_played_game(w)
        put(w, src, {"path": f"/g/{src}/", "color": "red", "slot": 4})
        assert get(w, "/api/me/plays").json()["plays"][0]["short_handed"] == []
