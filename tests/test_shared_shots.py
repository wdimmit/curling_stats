"""A player's shots, shown to their teammates once they say so.

Plays are private until their owner turns on sharing; then everyone on a team
with them can read the report -- the list, each game and its clips -- but not
change it. Both halves are asked on every request, so turning sharing off or
leaving the team ends it at once. Anyone else is answered 404, as if there
were nothing to see."""

from curling_score.service.records import play_id
from tests.test_game_clips_api import claimed, worker_finishes
from tests.test_plays import a_played_game, get, put
from tests.test_service_api import VID
from tests.test_service_auth import ALEX, SARAH, _joined_team, a_team, post, w  # noqa: F401

SHARING = "/api/me/shots-sharing"
SARAHS = "/api/players/uid-sarah/plays"


def share(w, on=True, headers=SARAH):  # noqa: F811
    return w["client"].put(SHARING, json={"share": on}, headers=headers)


def tagged(w):  # noqa: F811
    """Sarah's play on a charted game; (chart id, source id)."""
    slug, _, src = a_played_game(w)
    assert put(w, src, {"path": f"/c/{slug}/", "color": "red", "slot": 4}).status_code == 200
    return slug, src


class TestTheSwitch:
    def test_off_until_turned_on(self, w):  # noqa: F811
        r = get(w, SHARING)
        assert r.status_code == 200, r.text
        assert r.json() == {"share": False, "teams": [], "players": []}

    def test_turning_it_on_and_off(self, w):  # noqa: F811
        a_team(w, "Thistles")
        r = share(w)
        assert r.status_code == 200, r.text
        assert r.json() == {"share": True, "teams": ["Thistles"], "players": []}
        assert w["repo"].get_user("uid-sarah").share_shots is True
        assert share(w, False).json()["share"] is False
        assert get(w, SHARING).json()["share"] is False

    def test_only_true_or_false(self, w):  # noqa: F811
        for bad in ("yes", 1, None):
            assert share(w, bad).status_code == 422
        assert w["client"].put(SHARING, json={}, headers=SARAH).status_code == 422

    def test_signed_out_is_turned_away(self, w):  # noqa: F811
        assert get(w, SHARING, None).status_code == 401
        assert share(w, headers=None).status_code == 401

    def test_lists_only_the_teammates_who_share(self, w):  # noqa: F811
        _joined_team(w)
        assert get(w, SHARING, ALEX).json()["players"] == []
        share(w)
        assert get(w, SHARING, ALEX).json()["players"] == [
            {"id": "uid-sarah", "name": "Sarah", "teams": ["Thistles"]}]
        # Not yourself, however you are set.
        share(w, headers=ALEX)
        assert [p["id"] for p in get(w, SHARING, ALEX).json()["players"]] == ["uid-sarah"]

    def test_a_teammate_on_two_of_your_teams_is_listed_once(self, w):  # noqa: F811
        _joined_team(w)
        other = a_team(w, "Brooms")
        invite = post(w, f"/api/teams/{other}/invites", {}, SARAH).json()
        post(w, f"/api/invites/{invite['token']}/accept", {}, ALEX)
        share(w)
        assert get(w, SHARING, ALEX).json()["players"] == [
            {"id": "uid-sarah", "name": "Sarah", "teams": ["Thistles", "Brooms"]}]

    def test_there_is_a_limit(self, w):  # noqa: F811
        codes = [share(w, i % 2 == 0).status_code for i in range(61)]
        assert codes[:60] == [200] * 60 and codes[60] == 429


class TestATeammatesReport:
    def test_private_until_shared(self, w):  # noqa: F811
        _, src = tagged(w)
        _joined_team(w)
        assert get(w, SARAHS, ALEX).status_code == 404
        assert get(w, f"{SARAHS}/{src}/doc", ALEX).status_code == 404

    def test_once_shared_a_teammate_reads_what_the_owner_does(self, w):  # noqa: F811
        slug, src = tagged(w)
        _joined_team(w)
        share(w)
        r = get(w, SARAHS, ALEX)
        assert r.status_code == 200, r.text
        assert r.json() == get(w, "/api/me/plays").json()
        doc = get(w, f"{SARAHS}/{src}/doc", ALEX)
        assert doc.status_code == 200, doc.text
        assert doc.json() == get(w, f"/api/me/plays/{src}/doc").json()
        assert slug not in r.text and slug not in doc.text and "/c/" not in r.text

    def test_sharing_reaches_teammates_only(self, w):  # noqa: F811
        _, src = tagged(w)
        a_team(w)
        share(w)
        assert get(w, SARAHS, ALEX).status_code == 404
        assert get(w, f"{SARAHS}/{src}/doc", ALEX).status_code == 404

    def test_leaving_the_team_ends_it(self, w):  # noqa: F811
        _, src = tagged(w)
        team = _joined_team(w)
        share(w)
        assert get(w, SARAHS, ALEX).status_code == 200
        w["client"].delete(f"/api/teams/{team}/members/uid-alex", headers=ALEX)
        assert get(w, SARAHS, ALEX).status_code == 404

    def test_turning_it_off_ends_it(self, w):  # noqa: F811
        _, src = tagged(w)
        _joined_team(w)
        share(w)
        share(w, False)
        assert get(w, SARAHS, ALEX).status_code == 404
        assert get(w, f"{SARAHS}/{src}/doc", ALEX).status_code == 404

    def test_your_own_always_open_to_you(self, w):  # noqa: F811
        _, src = tagged(w)
        assert get(w, SARAHS).json() == get(w, "/api/me/plays").json()

    def test_nobody_by_that_id(self, w):  # noqa: F811
        assert get(w, "/api/players/uid-nobody/plays", ALEX).status_code == 404

    def test_signed_out_is_turned_away(self, w):  # noqa: F811
        assert get(w, SARAHS, None).status_code == 401

    def test_a_teammate_cannot_change_it(self, w):  # noqa: F811
        _, src = tagged(w)
        _joined_team(w)
        share(w)
        c = w["client"]
        assert c.put(f"{SARAHS}/{src}", json={"color": "yellow", "slot": 1},
                     headers=ALEX).status_code in (404, 405)
        assert c.delete(f"{SARAHS}/{src}", headers=ALEX).status_code in (404, 405)
        assert w["repo"].get_play("uid-sarah", src).color == "red"

    def test_a_game_she_did_not_tag_is_404(self, w):  # noqa: F811
        _, _, src = a_played_game(w)
        _joined_team(w)
        share(w)
        assert get(w, f"{SARAHS}/{src}/doc", ALEX).status_code == 404


class TestATeammatesClips:
    def test_shared_the_clips_come_too(self, w):  # noqa: F811
        src = claimed(w)
        job = worker_finishes(w, {"0.1.13": {"clip": True},
                                  "0.1.15": {"clip": None, "reason": "no_release"}})
        _joined_team(w)
        assert get(w, f"{SARAHS}/{src}/clips", ALEX).status_code == 404
        assert get(w, f"{SARAHS}/{src}/clips/0.1.13", ALEX).status_code == 404
        share(w)
        got = get(w, f"{SARAHS}/{src}/clips", ALEX)
        assert got.status_code == 200, got.text
        assert got.json() == get(w, f"/api/me/plays/{src}/clips").json()
        r = get(w, f"{SARAHS}/{src}/clips/0.1.13", ALEX)
        assert r.status_code == 200, r.text
        assert r.json()["url"] == f"memory://runs/{VID}/{job['run_id']}/clips/0.1.13.mp4"
        assert get(w, f"{SARAHS}/{src}/clips/0.1.15", ALEX).status_code == 404

    def test_reading_a_teammates_report_asks_for_nothing_more(self, w):  # noqa: F811
        src = claimed(w)
        _joined_team(w)
        share(w)
        assert get(w, f"{SARAHS}/{src}/doc", ALEX).status_code == 200
        assert len(w["repo"].clip_jobs_for_play(play_id("uid-sarah", src))) == 1


class TestStars:
    def test_a_teammate_sees_the_stars_but_cannot_change_them(self, w):  # noqa: F811
        _, src = tagged(w)
        _joined_team(w)
        share(w)
        c = w["client"]
        c.put(f"/api/me/plays/{src}/starred", json={"rock": "0.1.13", "starred": True}, headers=SARAH)
        [item] = get(w, SARAHS, ALEX).json()["plays"]
        assert item["starred"] == ["0.1.13"]
        assert c.put(f"{SARAHS}/{src}/starred", json={"rock": "0.1.15", "starred": True},
                     headers=ALEX).status_code in (404, 405)
        # Alex has no play of his own on it to star.
        assert c.put(f"/api/me/plays/{src}/starred", json={"rock": "0.1.15", "starred": True},
                     headers=ALEX).status_code == 404
        assert w["repo"].get_play("uid-sarah", src).starred == ["0.1.13"]
