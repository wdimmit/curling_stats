"""Saying which rocks of a game you threw, and reading them back.

A play is private to the person who saved it, and a chart's id -- its edit
link -- never comes back out of any of these routes, however the play was
saved."""

import dataclasses

import pytest

from curling_score.game import format as F
from curling_score.service.records import Source
from tests.test_service_api import T0, VID, sample_doc, work_through
from tests.test_service_auth import ALEX, SARAH, no_accounts, post, w  # noqa: F401  (fixtures)


def played_doc(fmt=F.FOURS):
    """One end, every rock with the heavy fields only the thrower's report keeps."""
    d = sample_doc(games=1)
    d["schema_version"] = 8
    if fmt is not F.FOURS:
        d["format"] = fmt.to_json()
    end = d["games"][0]["ends"][0]
    shots = []
    for n in range(1, fmt.delivered_per_end + 1):
        t = fmt.throw_info(n)
        shots.append({"number": n, "color": "red" if n % 2 else "yellow",
                      "color_inferred": False, "missing": False, "state_known": True,
                      "thrower_slot": t.position_slot,
                      "position": fmt.positions[t.position_slot - 1],
                      "rock_of_player": t.rock_of_player, "has_hammer": t.has_hammer,
                      "label": fmt.shot_label(1, n), "shot_type": "draw",
                      "t_rest_s": end["start_s"] + 40.0 * n,
                      "stones": [{"color": "red", "x": 0.0, "y": 0.1 * n}],
                      "track": [[1.0, 0.0, 1.0]],
                      "line": {"start": {"x": 0.1, "y": 38.0}, "delivery": [[0.0, 38.0, 0.1]]}})
    end["shots"] = shots
    return d


def a_played_game(w, doc=None):  # noqa: F811  (w: the fixture, passed through)
    """A processed game with one chart; (chart id, share slug, source id)."""
    slug = post(w, "/api/submissions", {"url": f"https://youtu.be/{VID}"}).json()["slug"]
    work_through(w, doc=doc or played_doc(), games=1)
    chart = w["repo"].get_chart(slug)
    return slug, chart.share_slug, chart.source_id


def put(w, sid, body, headers=SARAH):  # noqa: F811
    return w["client"].put(f"/api/me/plays/{sid}", json=body,
                           headers={"X-Forwarded-For": "1.2.3.4", **(headers or {})})


def get(w, path, headers=SARAH):  # noqa: F811
    return w["client"].get(path, headers=headers or {})


def mine(doc, color="red", slot=4):
    return [s for s in doc["games"][0]["ends"][0]["shots"]
            if s["color"] == color and s.get("thrower_slot") == slot]


class TestSavingAPlay:
    def test_signed_out_is_turned_away(self, w):  # noqa: F811
        _, _, src = a_played_game(w)
        assert put(w, src, {"path": f"/g/{src}/", "color": "red", "slot": 4},
                   headers=None).status_code == 401

    def test_without_accounts_it_says_so(self, no_accounts):  # noqa: F811
        assert put(no_accounts, "anything", {"color": "red", "slot": 1}).status_code == 503

    def test_from_the_review_link(self, w):  # noqa: F811
        _, _, src = a_played_game(w)
        r = put(w, src, {"path": f"/g/{src}/", "color": "red", "slot": 4})
        assert r.status_code == 200, r.text
        assert r.json()["play"] == {"source_id": src, "color": "red", "slot": 4,
                                    "format": F.FOURS.to_json(), "graded": False,
                                    "updated_at": r.json()["play"]["updated_at"]}
        got = w["repo"].get_play("uid-sarah", src)
        assert (got.color, got.slot, got.chart_id, got.link) == ("red", 4, None, "g")

    @pytest.mark.parametrize("kind", ["c", "s"])
    def test_from_a_chart_link_the_chart_is_remembered_but_never_told(self, w, kind):  # noqa: F811
        slug, share, src = a_played_game(w)
        key = slug if kind == "c" else share
        r = put(w, src, {"path": f"/{kind}/{key}/", "color": "yellow", "slot": 1})
        assert r.status_code == 200, r.text
        assert r.json()["play"]["graded"] is True
        assert slug not in r.text
        got = w["repo"].get_play("uid-sarah", src)
        assert (got.chart_id, got.link) == (slug, kind)

    def test_saving_again_replaces_it_and_keeps_when_it_was_first_saved(self, w):  # noqa: F811
        _, _, src = a_played_game(w)
        put(w, src, {"path": f"/g/{src}/", "color": "red", "slot": 4})
        first = w["repo"].get_play("uid-sarah", src)
        w["clock"].advance(60)
        put(w, src, {"path": f"/g/{src}/", "color": "yellow", "slot": 2})
        got = w["repo"].get_play("uid-sarah", src)
        assert (got.color, got.slot) == ("yellow", 2)
        assert got.created_at == first.created_at and got.updated_at > first.updated_at
        assert len(w["repo"].plays_for_user("uid-sarah")) == 1

    def test_from_the_report_page_only_the_colour_and_slot_change(self, w):  # noqa: F811
        slug, _, src = a_played_game(w)
        assert put(w, src, {"color": "red", "slot": 1}).status_code == 422
        put(w, src, {"path": f"/c/{slug}/", "color": "red", "slot": 4})
        r = put(w, src, {"color": "yellow", "slot": 3})
        assert r.status_code == 200, r.text
        got = w["repo"].get_play("uid-sarah", src)
        assert (got.color, got.slot, got.chart_id) == ("yellow", 3, slug)

    def test_a_link_to_another_game_is_refused(self, w):  # noqa: F811
        _, _, src = a_played_game(w)
        w["repo"].put_source(Source(id="other", video_id=VID, game_start_s=0.0, game_end_s=1.0,
                                    current_run_id="r_x", game_index=0, created_at=T0))
        r = put(w, "other", {"path": f"/g/{src}/", "color": "red", "slot": 1})
        assert r.status_code == 409

    def test_a_game_folded_into_another_is_saved_on_that_one(self, w):  # noqa: F811
        _, _, src = a_played_game(w)
        w["repo"].put_source(Source(id="folded", video_id=VID, game_start_s=0.0, game_end_s=1.0,
                                    current_run_id="r_x", game_index=0, created_at=T0,
                                    merged_into=src))
        assert put(w, "folded", {"path": f"/g/{src}/", "color": "red", "slot": 2}).status_code == 200
        assert w["repo"].get_play("uid-sarah", src).slot == 2
        assert get(w, "/api/me/plays/folded").json()["play"]["slot"] == 2

    @pytest.mark.parametrize("color,slot", [("blue", 1), ("red", 0), ("red", 5), ("red", True),
                                            ("red", "2"), ("red", None), (None, 1), ("red", 2.0)])
    def test_a_colour_or_slot_the_game_does_not_have_is_refused(self, w, color, slot):  # noqa: F811
        _, _, src = a_played_game(w)
        r = put(w, src, {"path": f"/g/{src}/", "color": color, "slot": slot})
        assert r.status_code == 422, (color, slot)
        assert w["repo"].get_play("uid-sarah", src) is None

    def test_doubles_has_two_players(self, w):  # noqa: F811
        _, _, src = a_played_game(w, played_doc(F.DOUBLES))
        assert put(w, src, {"path": f"/g/{src}/", "color": "red", "slot": 3}).status_code == 422
        r = put(w, src, {"path": f"/g/{src}/", "color": "red", "slot": 2})
        assert r.status_code == 200 and r.json()["play"]["format"]["name"] == "doubles"

    @pytest.mark.parametrize("path", ["/x/abc/", "https://elsewhere/g/abc/", "/g/../c/x/", 7])
    def test_a_path_that_is_not_a_game_link_is_refused(self, w, path):  # noqa: F811
        _, _, src = a_played_game(w)
        assert put(w, src, {"path": path, "color": "red", "slot": 1}).status_code == 400

    def test_an_unknown_chart_is_404(self, w):  # noqa: F811
        _, _, src = a_played_game(w)
        assert put(w, src, {"path": "/c/nochartlikethis/", "color": "red", "slot": 1}).status_code == 404

    def test_an_unknown_game_is_404(self, w):  # noqa: F811
        assert put(w, "nosuchgame", {"color": "red", "slot": 1}).status_code == 404

    def test_a_body_that_is_not_an_object_is_refused(self, w):  # noqa: F811
        _, _, src = a_played_game(w)
        r = w["client"].put(f"/api/me/plays/{src}", json=["red", 1], headers=SARAH)
        assert r.status_code in (400, 422)

    def test_there_is_a_limit(self, w):  # noqa: F811
        _, _, src = a_played_game(w)
        codes = [put(w, src, {"path": f"/g/{src}/", "color": "red", "slot": 1 + i % 4}).status_code
                 for i in range(61)]
        assert codes[:60] == [200] * 60 and codes[60] == 429


class TestReadingAndClearingAPlay:
    def test_reading_it_back(self, w):  # noqa: F811
        _, _, src = a_played_game(w)
        assert get(w, f"/api/me/plays/{src}").json() == {"play": None}
        put(w, src, {"path": f"/g/{src}/", "color": "red", "slot": 3})
        assert get(w, f"/api/me/plays/{src}").json()["play"]["slot"] == 3

    def test_nobody_else_can_see_it(self, w):  # noqa: F811
        _, _, src = a_played_game(w)
        put(w, src, {"path": f"/g/{src}/", "color": "red", "slot": 3})
        assert get(w, f"/api/me/plays/{src}", ALEX).json() == {"play": None}
        assert get(w, "/api/me/plays", ALEX).json() == {"plays": []}
        assert get(w, f"/api/me/plays/{src}/doc", ALEX).status_code == 404

    def test_clearing_it(self, w):  # noqa: F811
        _, _, src = a_played_game(w)
        put(w, src, {"path": f"/g/{src}/", "color": "red", "slot": 3})
        r = w["client"].delete(f"/api/me/plays/{src}", headers=SARAH)
        assert r.json() == {"ok": True, "removed": True}
        assert get(w, f"/api/me/plays/{src}").json() == {"play": None}
        r = w["client"].delete(f"/api/me/plays/{src}", headers=SARAH)
        assert r.json() == {"ok": True, "removed": False}
        assert w["client"].delete(f"/api/me/plays/{src}").status_code == 401


class TestTheList:
    def test_each_game_with_what_the_report_page_shows(self, w):  # noqa: F811
        slug, share, src = a_played_game(w)
        post(w, f"/api/games/{src}/teams", {"red": "Dimmit", "yellow": "Grant"}, SARAH)
        put(w, src, {"path": f"/c/{slug}/", "color": "red", "slot": 4})
        r = get(w, "/api/me/plays")
        assert r.status_code == 200, r.text
        [item] = r.json()["plays"]
        assert item["source_id"] == src and item["slot"] == 4 and item["graded"] is True
        assert item["teams"] == {"red": "Dimmit", "yellow": "Grant"}
        assert item["title"] == "4/30 - Sheet 2 - Spring League"
        assert item["format"]["positions"] == ["lead", "second", "third", "skip"]
        assert item["view_path"] == f"/s/{share}/"
        assert item["status"] == "ok"
        assert slug not in r.text

    def test_a_review_play_watches_on_the_review_link(self, w):  # noqa: F811
        _, _, src = a_played_game(w)
        put(w, src, {"path": f"/g/{src}/", "color": "red", "slot": 4})
        assert get(w, "/api/me/plays").json()["plays"][0]["view_path"] == f"/g/{src}/"

    def test_a_superseded_chart_watches_on_the_one_that_replaced_it(self, w):  # noqa: F811
        slug, _, src = a_played_game(w)
        put(w, src, {"path": f"/c/{slug}/", "color": "red", "slot": 4})
        old = w["repo"].get_chart(slug)
        newer = dataclasses.replace(old, id="Cnewerchartid000000000",
                                    share_slug="Snewershare00000000000")
        w["repo"].put_chart(newer)
        w["repo"].update_chart(old.id, superseded_by=newer.id)
        r = get(w, "/api/me/plays")
        assert r.json()["plays"][0]["view_path"] == "/s/Snewershare00000000000/"
        assert newer.id not in r.text and old.id not in r.text

    def test_signed_out_is_turned_away(self, w):  # noqa: F811
        assert get(w, "/api/me/plays", None).status_code == 401


class TestThePlayersGame:
    def test_a_chart_play_reads_the_charts_grading(self, w):  # noqa: F811
        slug, _, src = a_played_game(w)
        w["repo"].update_chart(slug, overrides={"0.1.13": {"shot_type": "hit"}})
        put(w, src, {"path": f"/c/{slug}/", "color": "red", "slot": 4})
        r = get(w, f"/api/me/plays/{src}/doc")
        assert r.status_code == 200, r.text
        assert [s["shot_type"] for s in mine(r.json()["doc"])] == ["hit", "draw"]
        assert r.json()["fallback"] is None
        assert slug not in r.text

    def test_a_moved_rock_arrives_renumbered_and_with_no_move_left_on_it(self, w):  # noqa: F811
        slug, _, src = a_played_game(w)
        w["repo"].update_chart(slug, overrides={"0.1.15": {"before": 14}})
        put(w, src, {"path": f"/s/{w['repo'].get_chart(slug).share_slug}/",
                     "color": "red", "slot": 4})
        doc = get(w, f"/api/me/plays/{src}/doc").json()["doc"]
        shots = doc["games"][0]["ends"][0]["shots"]
        assert [s.get("id") for s in shots][-3:] == [15, 14, 16]
        assert not any("before" in s for s in shots)

    def test_a_review_play_reads_the_game_as_detected(self, w):  # noqa: F811
        slug, _, src = a_played_game(w)
        w["repo"].update_chart(slug, overrides={"0.1.13": {"shot_type": "hit"}})
        put(w, src, {"path": f"/g/{src}/", "color": "red", "slot": 4})
        doc = get(w, f"/api/me/plays/{src}/doc").json()["doc"]
        assert [s["shot_type"] for s in mine(doc)] == ["draw", "draw"]

    def test_only_the_players_rocks_come_whole(self, w):  # noqa: F811
        _, _, src = a_played_game(w)
        put(w, src, {"path": f"/g/{src}/", "color": "red", "slot": 4})
        doc = get(w, f"/api/me/plays/{src}/doc").json()["doc"]
        shots = doc["games"][0]["ends"][0]["shots"]
        assert [s["number"] for s in shots if "track" in s] == [13, 15]
        assert [s["number"] for s in shots if "stones" in s] == [12, 13, 14, 15]
        assert "calibration" not in doc

    def test_a_chart_that_is_gone_falls_back_to_the_game_as_detected(self, w):  # noqa: F811
        slug, _, src = a_played_game(w)
        put(w, src, {"path": f"/c/{slug}/", "color": "red", "slot": 4})
        w["repo"].charts.pop(slug)
        r = get(w, f"/api/me/plays/{src}/doc")
        assert r.status_code == 200, r.text
        assert r.json()["fallback"] and len(mine(r.json()["doc"])) == 2

    def test_unchanged_it_is_not_sent_again(self, w):  # noqa: F811
        _, _, src = a_played_game(w)
        put(w, src, {"path": f"/g/{src}/", "color": "red", "slot": 4})
        r = get(w, f"/api/me/plays/{src}/doc")
        assert "private" in r.headers["cache-control"]
        again = w["client"].get(f"/api/me/plays/{src}/doc",
                                headers={**SARAH, "If-None-Match": r.headers["etag"]})
        assert again.status_code == 304

    def test_no_play_is_404(self, w):  # noqa: F811
        _, _, src = a_played_game(w)
        assert get(w, f"/api/me/plays/{src}/doc").status_code == 404
