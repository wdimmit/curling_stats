"""The ends a doubles pair threw the other way round (Source.switched_ends):
marked on the game from "I played…" or a chart's End box, served into every
chart of it, both partners' rocks credited in those ends."""

import copy

from curling_score.game import format as F
from tests.test_plays import a_played_game, get, played_doc, put
from tests.test_service_auth import ALEX, SARAH, post, w  # noqa: F401  (fixtures)

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
