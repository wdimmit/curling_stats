"""Accounts, teams, and the one chart a team shares for a game.

The invariant every test here defends is that identity is *additive*. A chart
link is still the whole of the permission it carries: signing in adds a way
back to your links and a team to share them with, and takes nothing away from
anyone who never signs in.

Nothing here talks to Google. `FakeVerifier` hands out claims the same way
`FakeYouTube` hands out metadata.
"""

import pytest
from fastapi.testclient import TestClient

from curling_score.service.api import Settings, create_app
from curling_score.service.auth import FakeVerifier
from curling_score.service.repo import MemoryRepo
from curling_score.service.store import MemoryStore
from curling_score.service.youtube import FakeYouTube, VideoMeta
from tests.test_service_api import ADMIN, CLUB, T0, VID, Clock, work_through

SARAH = {"Authorization": "Bearer tok-sarah"}
ALEX = {"Authorization": "Bearer tok-alex"}


def _world(auth):
    clock = Clock()
    repo, store = MemoryRepo(), MemoryStore()
    yt = FakeYouTube()
    yt.add(VideoMeta(VID, "4/30 - Sheet 2 - Spring League", CLUB, 14392.0, "none", T0))
    settings = Settings(public_base_url="https://chart.example", worker_token="worker-secret",
                        admin_token="admin-secret", allowed_channels={CLUB},
                        model_id="m-abc", max_queued=3, rate_hour=5, rate_day=20,
                        firebase_project="proj", firebase_api_key="AIzaFake",
                        firebase_auth_domain="proj.firebaseapp.com")
    app = create_app(repo, store, yt, settings, now=clock, auth=auth)
    return {"client": TestClient(app), "repo": repo, "store": store, "yt": yt,
            "clock": clock, "settings": settings, "auth": auth}


@pytest.fixture
def w():
    auth = FakeVerifier()
    auth.add("tok-sarah", "uid-sarah", "Sarah@Example.org", "Sarah")
    auth.add("tok-alex", "uid-alex", "alex@example.org", "Alex")
    return _world(auth)


@pytest.fixture
def no_accounts():
    """The service exactly as it was before any of this existed."""
    from curling_score.service.auth import NoAuth
    return _world(NoAuth())


def post(w, path, body=None, headers=None):
    return w["client"].post(path, json=body if body is not None else {},
                            headers={"X-Forwarded-For": "1.2.3.4", **(headers or {})})


def a_ready_game(w):
    """Submit and process a video; return the source id the catalogue lists."""
    post(w, "/api/submissions", {"url": f"https://youtu.be/{VID}"})
    work_through(w)
    games = w["client"].get("/api/games").json()["games"]
    return next(g["source_id"] for g in games if g["source_id"])


def a_team(w, name="Thistles"):
    return post(w, "/api/teams", {"name": name}, SARAH).json()["id"]


class TestAccountsCanBeOff:
    def test_the_config_says_so(self, no_accounts):
        assert no_accounts["client"].get("/api/auth/config").json() == {"enabled": False}

    def test_routes_that_need_a_person_say_they_are_unconfigured(self, no_accounts):
        assert no_accounts["client"].get("/api/me", headers=SARAH).status_code == 503

    def test_everything_else_still_works(self, no_accounts):
        r = post(no_accounts, "/api/submissions", {"url": f"https://youtu.be/{VID}"})
        assert r.status_code == 201
        assert no_accounts["client"].get(r.json()["chart_url"]).status_code == 200


class TestSigningIn:
    def test_the_config_carries_the_public_firebase_settings(self, w):
        cfg = w["client"].get("/api/auth/config").json()
        assert cfg["enabled"] and cfg["apiKey"] == "AIzaFake"
        assert cfg["projectId"] == "proj"

    def test_first_sight_of_a_token_creates_the_person(self, w):
        me = w["client"].get("/api/me", headers=SARAH).json()
        assert me["user"]["id"] == "uid-sarah" and me["user"]["name"] == "Sarah"
        assert me["teams"] == []
        assert w["repo"].get_user("uid-sarah").email == "sarah@example.org"  # lowered

    def test_no_token_is_401_not_a_crash(self, w):
        assert w["client"].get("/api/me").status_code == 401

    def test_a_token_we_do_not_know_is_401(self, w):
        assert w["client"].get("/api/me", headers={"Authorization": "Bearer forged"}
                               ).status_code == 401

    def test_a_forged_token_is_simply_anonymous_everywhere_else(self, w):
        """Failing closed here would break submitting for everyone."""
        r = post(w, "/api/submissions", {"url": f"https://youtu.be/{VID}"},
                 {"Authorization": "Bearer forged"})
        assert r.status_code == 201
        assert w["repo"].get_chart(r.json()["slug"]).owner_user_id is None


class TestMyGames:
    def test_a_submission_while_signed_in_is_mine(self, w):
        r = post(w, "/api/submissions", {"url": f"https://youtu.be/{VID}"}, SARAH)
        mine = w["client"].get("/api/me/charts", headers=SARAH).json()["charts"]
        assert [c["slug"] for c in mine] == [r.json()["slug"]]

    def test_someone_elses_games_are_not_mine(self, w):
        post(w, "/api/submissions", {"url": f"https://youtu.be/{VID}"}, SARAH)
        assert w["client"].get("/api/me/charts", headers=ALEX).json()["charts"] == []

    def test_a_link_made_anonymously_can_be_claimed_later(self, w):
        """The recovery path for every chart that exists today."""
        slug = post(w, "/api/submissions", {"url": f"https://youtu.be/{VID}"}).json()["slug"]
        assert w["client"].get("/api/me/charts", headers=SARAH).json()["charts"] == []
        assert post(w, f"/api/me/charts/{slug}/claim", {}, SARAH).status_code == 200
        mine = w["client"].get("/api/me/charts", headers=SARAH).json()["charts"]
        assert [c["slug"] for c in mine] == [slug]

    def test_a_teams_chart_cannot_be_claimed_by_an_outsider(self, w):
        """Otherwise sharing a link would be handing over ownership."""
        team = a_team(w)
        slug = post(w, "/api/submissions",
                    {"url": f"https://youtu.be/{VID}", "team_id": team}, SARAH).json()["slug"]
        assert post(w, f"/api/me/charts/{slug}/claim", {}, ALEX).status_code == 403


class TestTeams:
    def test_create_invite_and_join(self, w):
        team = a_team(w)
        invite = post(w, f"/api/teams/{team}/invites", {}, SARAH).json()
        assert invite["team"] == "Thistles"
        preview = w["client"].get(f"/api/invites/{invite['token']}").json()
        assert preview["team"] == "Thistles" and preview["usable"]
        got = post(w, f"/api/invites/{invite['token']}/accept", {}, ALEX).json()
        assert sorted(m["id"] for m in got["members"]) == ["uid-alex", "uid-sarah"]
        assert [t["id"] for t in
                w["client"].get("/api/me", headers=ALEX).json()["teams"]] == [team]

    def test_accepting_twice_is_harmless(self, w):
        invite = post(w, f"/api/teams/{a_team(w)}/invites", {}, SARAH).json()
        post(w, f"/api/invites/{invite['token']}/accept", {}, ALEX)
        r = post(w, f"/api/invites/{invite['token']}/accept", {}, ALEX)
        assert r.status_code == 200 and r.json()["member_count"] == 2

    def test_an_expired_invitation_is_410(self, w):
        invite = post(w, f"/api/teams/{a_team(w)}/invites", {"days": 1}, SARAH).json()
        w["clock"].advance(2 * 24 * 3600)
        assert post(w, f"/api/invites/{invite['token']}/accept", {}, ALEX).status_code == 410

    def test_a_team_you_are_not_on_is_404_not_403(self, w):
        """403 would confirm the team exists to anyone guessing ids."""
        assert w["client"].get(f"/api/teams/{a_team(w)}", headers=ALEX).status_code == 404

    def test_signing_in_is_required_to_make_a_team(self, w):
        assert post(w, "/api/teams", {"name": "Nope"}).status_code == 401

    def test_a_member_can_leave_but_the_owner_cannot(self, w):
        team = a_team(w)
        invite = post(w, f"/api/teams/{team}/invites", {}, SARAH).json()
        post(w, f"/api/invites/{invite['token']}/accept", {}, ALEX)
        c = w["client"]
        assert c.delete(f"/api/teams/{team}/members/uid-alex", headers=ALEX).status_code == 200
        assert c.delete(f"/api/teams/{team}/members/uid-sarah", headers=SARAH).status_code == 409

    def test_only_the_owner_shows_someone_else_out(self, w):
        team = a_team(w)
        invite = post(w, f"/api/teams/{team}/invites", {}, SARAH).json()
        post(w, f"/api/invites/{invite['token']}/accept", {}, ALEX)
        c = w["client"]
        assert c.delete(f"/api/teams/{team}/members/uid-sarah", headers=ALEX).status_code == 403
        assert c.delete(f"/api/teams/{team}/members/uid-alex", headers=SARAH).status_code == 200


def _joined_team(w):
    team = a_team(w)
    invite = post(w, f"/api/teams/{team}/invites", {}, SARAH).json()
    post(w, f"/api/invites/{invite['token']}/accept", {}, ALEX)
    return team


class TestOneChartPerGame:
    def test_two_teammates_land_on_the_same_chart(self, w):
        team = _joined_team(w)
        sid = a_ready_game(w)
        first = post(w, "/api/charts", {"source_id": sid, "team_id": team}, SARAH).json()
        second = post(w, "/api/charts", {"source_id": sid, "team_id": team}, ALEX).json()
        assert second["slug"] == first["slug"] and second["reused_chart"] is True

    def test_one_persons_second_visit_finds_their_own_chart(self, w):
        """The bug this whole thing exists to kill: coming back used to mint a blank."""
        sid = a_ready_game(w)
        first = post(w, "/api/charts", {"source_id": sid}, SARAH).json()
        again = post(w, "/api/charts", {"source_id": sid}, SARAH).json()
        assert again["slug"] == first["slug"]

    def test_grading_survives_coming_back(self, w):
        sid = a_ready_game(w)
        slug = post(w, "/api/charts", {"source_id": sid}, SARAH).json()["slug"]
        w["client"].post(f"/c/{slug}/overrides.json?merge=1&v=0",
                         json={"0.1.1": {"user_score": 3}})
        again = post(w, "/api/charts", {"source_id": sid}, SARAH).json()["slug"]
        assert w["client"].get(f"/c/{again}/overrides.json").json() == {
            "0.1.1": {"user_score": 3}}

    def test_two_teams_charting_one_game_stay_apart(self, w):
        sid = a_ready_game(w)
        t1, t2 = a_team(w, "Thistles"), a_team(w, "Rocks")
        a = post(w, "/api/charts", {"source_id": sid, "team_id": t1}, SARAH).json()
        b = post(w, "/api/charts", {"source_id": sid, "team_id": t2}, SARAH).json()
        assert a["slug"] != b["slug"]

    def test_anonymous_visitors_each_get_their_own(self, w):
        """Unchanged from before accounts: no owner, no dedupe."""
        sid = a_ready_game(w)
        a = post(w, "/api/charts", {"source_id": sid}).json()
        b = post(w, "/api/charts", {"source_id": sid}).json()
        assert a["slug"] != b["slug"]

    def test_starting_a_chart_does_not_spend_a_submission(self, w):
        """It queues no work, so it must not count against five an hour."""
        sid = a_ready_game(w)
        for _ in range(8):
            assert post(w, "/api/charts", {"source_id": sid}, SARAH).status_code == 201

    def test_making_new_charts_is_still_bounded(self, w):
        """Free of the submission budget is not the same as free of any budget:
        an anonymous caller never dedupes, so every press makes a document."""
        sid = a_ready_game(w)
        codes = {post(w, "/api/charts", {"source_id": sid}).status_code for _ in range(40)}
        assert codes == {201, 429}

    def test_but_reopening_your_own_chart_never_runs_out(self, w):
        """It creates nothing, so there is nothing to meter."""
        sid = a_ready_game(w)
        for _ in range(40):
            assert post(w, "/api/charts", {"source_id": sid}, SARAH).status_code == 201

    def test_a_game_still_processing_cannot_be_charted_this_way(self, w):
        post(w, "/api/submissions", {"url": f"https://youtu.be/{VID}"})
        assert post(w, "/api/charts", {"source_id": "s_nope"}, SARAH).status_code == 404


class TestRacingOnANewVideo:
    def _both_submit(self, w, team, gap_s=1):
        """Two teammates paste the same not-yet-processed link.

        Both get a link back and both sit on the status page, because until the
        run finishes nobody knows which game either of them asked for.
        """
        a = post(w, "/api/submissions",
                 {"url": f"https://youtu.be/{VID}", "team_id": team}, SARAH).json()
        w["clock"].advance(gap_s)
        b = post(w, "/api/submissions",
                 {"url": f"https://youtu.be/{VID}", "team_id": team}, ALEX).json()
        return a["slug"], b["slug"]

    def test_the_earlier_link_is_the_one_the_team_keeps(self, w):
        team = _joined_team(w)
        first, second = self._both_submit(w, team)
        assert first != second                     # before the run resolved
        work_through(w)
        assert w["repo"].get_chart(second).superseded_by == first
        assert w["repo"].get_chart(first).superseded_by is None

    def test_the_loser_link_keeps_working_and_shows_the_teams_chart(self, w):
        """Neither had anything to lose -- an unresolved chart has no viewer --
        and the link the second person wrote down must never break."""
        team = _joined_team(w)
        first, second = self._both_submit(w, team)
        work_through(w)
        w["client"].post(f"/c/{first}/overrides.json?merge=1&v=0",
                         json={"0.1.1": {"user_score": 3}})
        assert w["client"].get(f"/c/{second}/overrides.json").json() == {
            "0.1.1": {"user_score": 3}}
        # And editing through the old link reaches the same chart, not a fork.
        w["client"].post(f"/c/{second}/overrides.json?merge=1&v=1",
                         json={"0.1.2": {"user_score": 4}})
        assert set(w["client"].get(f"/c/{first}/overrides.json").json()) == {"0.1.1", "0.1.2"}

    def test_exactly_one_of_them_survives_even_in_a_dead_heat(self, w):
        """Two submissions inside one clock tick still must not diverge."""
        team = _joined_team(w)
        first, second = self._both_submit(w, team, gap_s=0)
        work_through(w)
        charts = [w["repo"].get_chart(first), w["repo"].get_chart(second)]
        survivors = [c for c in charts if not c.superseded_by]
        assert len(survivors) == 1
        assert {c.superseded_by for c in charts if c.superseded_by} == {survivors[0].id}

    def test_claiming_a_superseded_link_claims_the_live_chart(self, w):
        """Somebody bookmarked the losing link. Adding it must add the real one."""
        team = _joined_team(w)
        first, second = self._both_submit(w, team)
        work_through(w)
        got = post(w, f"/api/me/charts/{second}/claim", {}, SARAH).json()
        assert got["slug"] == first
        # The dead chart is left exactly as it was -- Alex made it, and it
        # stays theirs; the claim went to the chart the team actually uses.
        assert w["repo"].get_chart(second).owner_user_id == "uid-alex"
        assert [c["slug"] for c in
                w["client"].get("/api/me/charts", headers=SARAH).json()["charts"]] == [first]

    def test_only_one_of_them_shows_up_in_the_list(self, w):
        team = _joined_team(w)
        first, second = self._both_submit(w, team)
        work_through(w)
        mine = w["client"].get("/api/me/charts", headers=ALEX).json()["charts"]
        assert [c["slug"] for c in mine] == [first]
        assert second not in [c["slug"] for c in mine]

    def test_a_duplicate_with_grading_in_it_is_flagged_not_folded(self, w):
        """Two people's charting is never merged behind their backs."""
        team = _joined_team(w)
        first, second = self._both_submit(w, team)
        # Somebody got grading into the second chart before the run resolved.
        w["repo"].get_chart(second).overrides = {"0.1.1": {"user_score": 2}}
        work_through(w)
        got = w["repo"].get_chart(second)
        assert got.superseded_by is None and got.duplicate_of == first
        assert got.overrides == {"0.1.1": {"user_score": 2}}


class TestCapabilityUrlsAreUntouched:
    def test_a_teams_chart_is_still_editable_by_a_stranger_with_the_link(self, w):
        """Deliberate. The link is the permission; an account only adds a list."""
        team = _joined_team(w)
        sid = a_ready_game(w)
        slug = post(w, "/api/charts", {"source_id": sid, "team_id": team}, SARAH).json()["slug"]
        r = w["client"].post(f"/c/{slug}/overrides.json?merge=1&v=0",
                             json={"0.1.1": {"user_score": 3}})
        assert r.status_code == 200

    def test_a_share_link_is_still_read_only_for_a_member(self, w):
        """Membership changes what you can find, never what a URL lets you do."""
        team = _joined_team(w)
        sid = a_ready_game(w)
        got = post(w, "/api/charts", {"source_id": sid, "team_id": team}, SARAH).json()
        share = got["share_url"].rstrip("/").split("/")[-1]
        r = w["client"].post(f"/s/{share}/overrides.json?merge=1&v=0",
                             json={"0.1.1": {"user_score": 3}}, headers=SARAH)
        assert r.status_code == 403

    def test_a_shared_chart_tells_the_page_to_poll(self, w):
        team = _joined_team(w)
        sid = a_ready_game(w)
        slug = post(w, "/api/charts", {"source_id": sid, "team_id": team}, SARAH).json()["slug"]
        assert '"shared": true' in w["client"].get(f"/c/{slug}/").text
        solo = post(w, "/api/charts", {"source_id": sid}, ALEX).json()["slug"]
        assert '"shared": false' in w["client"].get(f"/c/{solo}/").text


class TestNamingALeague:
    """The watcher labels what it queues; a pasted link arrives with nothing,
    and an unlabelled game is findable only by scrolling to its date."""

    def test_a_pasted_link_takes_the_league_from_its_title(self, w):
        """The fixture is titled "4/30 - Sheet 2 - Spring League", and the club
        writes the league after the sheet, so there is nothing to type in."""
        a_ready_game(w)
        assert w["client"].get("/api/games").json()["leagues"] == ["Spring League"]

    def test_relabelling_fills_the_blanks_left_by_older_games(self, w):
        """Games processed before titles were read have nothing; one admin
        call names them, from the same titles they were streamed under."""
        src = a_ready_game(w)
        vid = w["repo"].get_source(src).video_id
        for s in w["repo"].sources_for_video(vid):
            w["repo"].update_source(s.id, league=None)
        for r in w["repo"].runs_for_video(vid):
            w["repo"].update_run(r.id, league=None)
        assert w["client"].get("/api/games").json()["leagues"] == []
        got = post(w, "/api/admin/relabel", {}, ADMIN).json()
        assert got["runs"] == 1 and got["games"] == 2
        assert w["client"].get("/api/games").json()["leagues"] == ["Spring League"]

    def test_relabelling_never_overwrites_a_name_somebody_chose(self, w):
        src = a_ready_game(w)
        post(w, f"/api/games/{src}/league", {"league": "Tuesday"}, SARAH)
        assert post(w, "/api/admin/relabel", {}, ADMIN).json()["games"] == 0
        assert w["client"].get("/api/games").json()["leagues"] == ["Tuesday"]

    def test_a_signed_in_person_can_name_it(self, w):
        src = a_ready_game(w)
        r = post(w, f"/api/games/{src}/league", {"league": "Tuesday Super League"}, SARAH)
        assert r.status_code == 200 and r.json()["league"] == "Tuesday Super League"
        listed = w["client"].get("/api/games").json()
        assert listed["leagues"] == ["Tuesday Super League"]

    def test_it_names_every_game_in_the_recording(self, w):
        """One recording is one sheet for one night, so both its games are it."""
        src = a_ready_game(w)
        assert post(w, f"/api/games/{src}/league", {"league": "Tuesday"}, SARAH).json()["games"] == 2
        games = [g for g in w["client"].get("/api/games").json()["games"] if g["source_id"]]
        assert len(games) == 2 and {g["league"] for g in games} == {"Tuesday"}

    def test_it_names_the_runs_too_so_a_reprocess_keeps_it(self, w):
        """A reprocess builds its run from the last one; a label that lived
        only on the source would be one a reprocess quietly dropped."""
        src = a_ready_game(w)
        post(w, f"/api/games/{src}/league", {"league": "Tuesday"}, SARAH)
        vid = w["repo"].get_source(src).video_id
        assert [r.league for r in w["repo"].runs_for_video(vid)] == ["Tuesday"]

    def test_the_filter_then_finds_it(self, w):
        src = a_ready_game(w)
        post(w, f"/api/games/{src}/league", {"league": "Tuesday"}, SARAH)
        found = w["client"].get("/api/games?league=Tuesday").json()["games"]
        assert [g["source_id"] for g in found if g["source_id"]]
        assert not [g for g in w["client"].get("/api/games?league=Friday").json()["games"]
                    if g["source_id"]]

    def test_an_empty_name_clears_it(self, w):
        src = a_ready_game(w)
        post(w, f"/api/games/{src}/league", {"league": "Tuesday"}, SARAH)
        assert post(w, f"/api/games/{src}/league", {"league": "  "}, SARAH).json()["league"] is None
        assert w["client"].get("/api/games").json()["leagues"] == []

    def test_signed_out_it_is_read_only(self, w):
        src = a_ready_game(w)
        assert post(w, f"/api/games/{src}/league", {"league": "Tuesday"}).status_code == 401
        assert w["client"].get("/api/games").json()["leagues"] == ["Spring League"]

    def test_a_name_nobody_could_read_is_refused(self, w):
        src = a_ready_game(w)
        assert post(w, f"/api/games/{src}/league", {"league": "x" * 61}, SARAH).status_code == 422

    def test_a_game_we_do_not_have_is_404(self, w):
        assert post(w, "/api/games/s_nope/league", {"league": "Tuesday"}, SARAH).status_code == 404

    def test_without_accounts_there_is_nothing_to_sign_in_to(self, no_accounts):
        src = a_ready_game(no_accounts)
        assert post(no_accounts, f"/api/games/{src}/league",
                    {"league": "Tuesday"}, SARAH).status_code == 503


class TestNamingTheTeams:
    """Per game, where the league is per recording. Nothing detects it: the
    cameras read stones, not scoreboards."""

    def test_a_game_starts_with_nobody_named(self, w):
        a_ready_game(w)
        g = next(x for x in w["client"].get("/api/games").json()["games"] if x["source_id"])
        assert g["team_red"] is None and g["team_yellow"] is None

    def test_a_signed_in_person_names_them(self, w):
        src = a_ready_game(w)
        r = post(w, f"/api/games/{src}/teams", {"red": "Rice", "yellow": "Casey"}, SARAH)
        assert r.status_code == 200 and r.json()["red"] == "Rice"
        g = next(x for x in w["client"].get("/api/games").json()["games"]
                 if x["source_id"] == src)
        assert (g["team_red"], g["team_yellow"]) == ("Rice", "Casey")

    def test_only_the_game_named_not_the_whole_recording(self, w):
        """Unlike the league: one night on one sheet is two different pairs."""
        src = a_ready_game(w)
        post(w, f"/api/games/{src}/teams", {"red": "Rice", "yellow": "Casey"}, SARAH)
        others = [g for g in w["client"].get("/api/games").json()["games"]
                  if g["source_id"] and g["source_id"] != src]
        assert others and all(g["team_red"] is None for g in others)

    def test_one_colour_can_be_named_without_the_other(self, w):
        src = a_ready_game(w)
        assert post(w, f"/api/games/{src}/teams", {"red": "Rice"}, SARAH).json()["red"] == "Rice"
        assert w["repo"].get_source(src).team_yellow is None

    def test_an_empty_name_clears_that_colour(self, w):
        src = a_ready_game(w)
        post(w, f"/api/games/{src}/teams", {"red": "Rice", "yellow": "Casey"}, SARAH)
        post(w, f"/api/games/{src}/teams", {"red": ""}, SARAH)
        assert w["repo"].get_source(src).team_red is None
        assert w["repo"].get_source(src).team_yellow == "Casey"

    def test_naming_nothing_is_a_mistake_worth_saying(self, w):
        src = a_ready_game(w)
        assert post(w, f"/api/games/{src}/teams", {}, SARAH).status_code == 400

    def test_signed_out_it_is_read_only(self, w):
        src = a_ready_game(w)
        assert post(w, f"/api/games/{src}/teams", {"red": "Rice"}).status_code == 401

    def test_a_name_nobody_could_read_is_refused(self, w):
        src = a_ready_game(w)
        assert post(w, f"/api/games/{src}/teams",
                    {"red": "x" * 61}, SARAH).status_code == 422

    def test_the_names_reach_the_public_view_of_the_game(self, w):
        src = a_ready_game(w)
        post(w, f"/api/games/{src}/teams", {"red": "Rice", "yellow": "Casey"}, SARAH)
        doc = w["client"].get(f"/g/{src}/timeline.json").json()
        assert doc["games"][0]["teams"]["red"]["name"] == "Rice"
        assert doc["games"][0]["teams"]["yellow"]["name"] == "Casey"

    def test_they_reach_a_chart_made_before_anyone_knew(self, w):
        """Which is why they live on the source and not in the run's document:
        the chart is pinned to a run that was built before the names existed."""
        src = a_ready_game(w)
        chart = post(w, "/api/charts", {"source_id": src}, SARAH).json()
        before = w["client"].get(f"/c/{chart['slug']}/timeline.json").json()
        assert before["games"][0]["teams"]["red"]["name"] is None
        post(w, f"/api/games/{src}/teams", {"red": "Rice", "yellow": "Casey"}, SARAH)
        after = w["client"].get(f"/c/{chart['slug']}/timeline.json").json()
        assert after["games"][0]["teams"]["red"]["name"] == "Rice"

    def test_a_game_we_do_not_have_is_404(self, w):
        assert post(w, "/api/games/s_nope/teams", {"red": "Rice"}, SARAH).status_code == 404
