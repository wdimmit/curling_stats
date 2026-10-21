"""The ends a doubles pair threw the other way round (Source.switched_ends):
marked on the game from "I played…" or a chart's End box, served into every
chart of it, both partners' rocks credited in those ends."""

import copy

import pytest

from curling_score.game import format as F
from tests.test_plays import a_played_game, get, played_doc, put
from tests.test_service_auth import ALEX, MAKER, SARAH, post, w  # noqa: F401  (fixtures)

PLAIN, SWITCHED = ["A", "B", "B", "B", "A"], ["B", "A", "A", "A", "B"]
END_S = 900.0


def doubles_game_doc(ends=3):
    """`played_doc(F.DOUBLES)`'s one end, played `ends` times over."""
    d = played_doc(F.DOUBLES)
    game = d["games"][0]
    first = game["ends"][0]
    game["ends"] = []
    for k in range(1, ends + 1):
        e = copy.deepcopy(first)
        shift = END_S * (k - 1)
        e.update(number=k, start_s=first["start_s"] + shift, end_s=first["start_s"] + shift + END_S)
        for s in e["shots"]:
            s["t_rest_s"] += shift
            s["label"] = F.DOUBLES.shot_label(k, s["number"])
        game["ends"].append(e)
    return d


def a_doubles_game(w, ends=3):  # noqa: F811  (w: the fixture, passed through)
    """A processed doubles game with one chart: (chart id, share slug, source id)."""
    return a_played_game(w, doubles_game_doc(ends))


def red_by_end(doc):
    """Red's rocks' positions, end by end."""
    return [[s["position"] for s in e["shots"] if s["color"] == "red"]
            for e in doc["games"][0]["ends"]]


def mark(w, src, color, ends):  # noqa: F811
    w["repo"].set_switched_ends(src, color, {"ends": ends, "by": "uid-sarah", "at": "t"})


class TestServed:
    def test_the_game_link_credits_a_switched_end(self, w):  # noqa: F811
        _, _, src = a_doubles_game(w)
        mark(w, src, "red", [2])
        doc = get(w, f"/g/{src}/timeline.json").json()
        assert red_by_end(doc) == [PLAIN, SWITCHED, PLAIN]
        assert doc["games"][0]["ends"][1]["roles_swapped"] == {"red": True}

    def test_a_chart_and_its_share_link_get_it(self, w):  # noqa: F811
        slug, share, src = a_doubles_game(w)
        mark(w, src, "red", [2])
        for path in (f"/c/{slug}/timeline.json", f"/s/{share}/timeline.json"):
            assert red_by_end(get(w, path).json())[1] == SWITCHED

    def test_a_fours_game_ignores_a_stray_mark(self, w):  # noqa: F811
        _, _, src = a_played_game(w)
        before = get(w, f"/g/{src}/timeline.json").json()
        w["repo"].update_source(src, switched_ends={"red": {"ends": [1]}})
        assert get(w, f"/g/{src}/timeline.json").json() == before

    def test_each_partners_report_follows_the_switch(self, w):  # noqa: F811
        _, _, src = a_doubles_game(w)
        mark(w, src, "red", [2])
        assert put(w, src, {"path": f"/g/{src}/", "color": "red", "slot": 1}).status_code == 200
        assert put(w, src, {"path": f"/g/{src}/", "color": "red", "slot": 2},
                   headers=ALEX).status_code == 200

        def mine(headers):
            doc = get(w, f"/api/me/plays/{src}/doc", headers).json()["doc"]
            return [[s["number"] for s in e["shots"] if s["color"] == "red" and "track" in s]
                    for e in doc["games"][0]["ends"]]
        assert mine(SARAH) == [[1, 9], [3, 5, 7], [1, 9]]
        assert mine(ALEX) == [[3, 5, 7], [1, 9], [3, 5, 7]]

    def test_the_marks_follow_the_end_not_its_number(self, w):  # noqa: F811
        _, _, src = a_doubles_game(w)
        mark(w, src, "red", [3])
        end2 = get(w, f"/g/{src}/timeline.json").json()["games"][0]["ends"][1]
        w["repo"].update_source(src, play_start_s=end2["start_s"])      # end 1 trimmed off
        ends = get(w, f"/g/{src}/timeline.json").json()["games"][0]["ends"]
        assert [e["number"] for e in ends] == [1, 2]
        assert red_by_end({"games": [{"ends": ends}]}) == [PLAIN, SWITCHED]


def play(w, src, extra, headers=SARAH):  # noqa: F811
    return put(w, src, {"path": f"/g/{src}/", "color": "red", "slot": 1, **extra}, headers)


def marks(w, src, color="red"):  # noqa: F811
    return ((w["repo"].get_source(src).switched_ends or {}).get(color) or {}).get("ends")


class TestSavingWithAPlay:
    def test_a_play_saves_its_teams_switched_ends(self, w):  # noqa: F811
        _, _, src = a_doubles_game(w)
        r = play(w, src, {"switched_ends": [3, 2]})
        assert r.status_code == 200, r.text
        assert r.json()["play"]["switched_ends"] == [2, 3]
        got = w["repo"].get_source(src).switched_ends["red"]
        assert (got["ends"], got["by"]) == ([2, 3], "uid-sarah")

    def test_leaving_the_key_out_leaves_them(self, w):  # noqa: F811
        _, _, src = a_doubles_game(w)
        play(w, src, {"switched_ends": [2]})
        r = play(w, src, {})
        assert r.status_code == 200 and r.json()["play"]["switched_ends"] == [2]
        assert marks(w, src) == [2]

    def test_an_empty_list_clears_them(self, w):  # noqa: F811
        _, _, src = a_doubles_game(w)
        play(w, src, {"switched_ends": [2]})
        assert play(w, src, {"switched_ends": []}).json()["play"]["switched_ends"] == []
        assert "red" not in (w["repo"].get_source(src).switched_ends or {})

    def test_the_partners_save_without_the_key_keeps_them(self, w):  # noqa: F811
        _, _, src = a_doubles_game(w)
        play(w, src, {"switched_ends": [2]})
        r = put(w, src, {"path": f"/g/{src}/", "color": "red", "slot": 2}, headers=ALEX)
        assert r.status_code == 200 and r.json()["play"]["switched_ends"] == [2]
        assert marks(w, src) == [2]

    def test_only_the_claimed_colour_changes(self, w):  # noqa: F811
        _, _, src = a_doubles_game(w)
        mark(w, src, "yellow", [3])
        play(w, src, {"switched_ends": [2]})
        assert (marks(w, src, "yellow"), marks(w, src)) == ([3], [2])

    def test_clearing_a_play_keeps_the_marks(self, w):  # noqa: F811
        _, _, src = a_doubles_game(w)
        play(w, src, {"switched_ends": [2]})
        r = w["client"].delete(f"/api/me/plays/{src}", headers=SARAH)
        assert r.status_code == 200 and r.json()["removed"] is True
        assert marks(w, src) == [2]

    def test_saved_from_a_chart_checks_the_charts_ends(self, w):  # noqa: F811
        slug, _, src = a_doubles_game(w)
        r = put(w, src, {"path": f"/c/{slug}/", "color": "red", "slot": 1, "switched_ends": [2]})
        assert r.status_code == 200, r.text
        assert marks(w, src) == [2]

    @pytest.mark.parametrize("bad,detail", [
        ("2", "switched ends are a list of distinct end numbers"),
        ([2, 2], "switched ends are a list of distinct end numbers"),
        ([True], "switched ends are a list of distinct end numbers"),
        ([2.0], "switched ends are a list of distinct end numbers"),
        ([1], "the first end sets who is who: it cannot be switched"),
        ([9], "the game has no end 9")])
    def test_bad_switched_ends_are_refused(self, w, bad, detail):  # noqa: F811
        _, _, src = a_doubles_game(w)
        r = play(w, src, {"switched_ends": bad})
        assert r.status_code == 422 and r.json()["detail"] == detail
        assert not w["repo"].get_source(src).switched_ends
        assert w["repo"].get_play("uid-sarah", src) is None

    def test_a_fours_game_has_none(self, w):  # noqa: F811
        _, _, src = a_played_game(w)
        r = put(w, src, {"path": f"/g/{src}/", "color": "red", "slot": 1, "switched_ends": [2]})
        assert r.status_code == 422 and r.json()["detail"] == "only a doubles game has switched ends"


def endbox(w, src, path, body, headers=MAKER):  # noqa: F811
    return post(w, f"/api/games/{src}/switched-ends", {"path": path, **body}, headers)


class TestTheEndBoxRoute:
    def test_the_charts_maker_marks_an_end(self, w):  # noqa: F811
        slug, _, src = a_doubles_game(w)
        r = endbox(w, src, f"/c/{slug}/", {"color": "red", "ends": [2]})
        assert r.status_code == 200, r.text
        assert r.json() == {"ok": True, "color": "red", "ends": [2]}
        assert red_by_end(get(w, f"/g/{src}/timeline.json").json())[1] == SWITCHED
        assert w["repo"].get_source(src).switched_ends["red"]["by"] == "uid-maker"

    def test_someone_elses_chart_is_refused(self, w):  # noqa: F811
        slug, _, src = a_doubles_game(w)
        r = endbox(w, src, f"/c/{slug}/", {"color": "red", "ends": [2]}, headers=SARAH)
        assert r.status_code == 403
        assert r.json()["detail"] == "only the person or team who made this chart can change it"
        assert not w["repo"].get_source(src).switched_ends

    def test_signed_out_is_refused(self, w):  # noqa: F811
        slug, _, src = a_doubles_game(w)
        assert endbox(w, src, f"/c/{slug}/", {"color": "red", "ends": [2]},
                      headers=None).status_code == 401

    def test_a_view_only_link_is_refused(self, w):  # noqa: F811
        _, share, src = a_doubles_game(w)
        r = endbox(w, src, f"/s/{share}/", {"color": "red", "ends": [2]})
        assert r.status_code == 403 and r.json()["detail"] == "this is a view-only link"

    def test_a_game_link_is_refused(self, w):  # noqa: F811
        _, _, src = a_doubles_game(w)
        r = endbox(w, src, f"/g/{src}/", {"color": "red", "ends": [2]})
        assert r.status_code == 422 and r.json()["detail"] == "switched ends are marked on a chart"

    def test_the_first_end_is_refused(self, w):  # noqa: F811
        slug, _, src = a_doubles_game(w)
        r = endbox(w, src, f"/c/{slug}/", {"color": "red", "ends": [1]})
        assert r.status_code == 422
        assert r.json()["detail"] == "the first end sets who is who: it cannot be switched"

    def test_a_colour_must_be_a_team(self, w):  # noqa: F811
        slug, _, src = a_doubles_game(w)
        r = endbox(w, src, f"/c/{slug}/", {"color": "blue", "ends": [2]})
        assert r.status_code == 422 and r.json()["detail"] == "color must be red or yellow"

    def test_an_empty_list_clears_them(self, w):  # noqa: F811
        slug, _, src = a_doubles_game(w)
        endbox(w, src, f"/c/{slug}/", {"color": "red", "ends": [2]})
        assert endbox(w, src, f"/c/{slug}/", {"color": "red", "ends": []}).json()["ends"] == []
        assert "red" not in (w["repo"].get_source(src).switched_ends or {})
