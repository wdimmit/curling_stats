"""The names a league's teams have gone by, offered back when somebody names
the teams of another of its games."""

from datetime import datetime, timedelta, timezone

import pytest

from curling_score.service import team_names as tn
from curling_score.service.records import Source
from tests.test_service_api import world  # noqa: F401

T0 = datetime(2026, 10, 5, 2, 0, tzinfo=timezone.utc)
MONDAY = "2026-2027 Monday Open League"


def src(sid, red=None, yellow=None, *, days=0, playlist=MONDAY,
        title="10/5 - Sheet 3 - Monday Open League 2026-2027", **kw):
    return Source(id=sid, video_id=f"v_{sid}", game_start_s=0.0, game_end_s=7000.0,
                  current_run_id="r_1", game_index=0, created_at=T0,
                  played_at=T0 + timedelta(days=days), title=title, sheet=3,
                  playlist_title=playlist, team_red=red, team_yellow=yellow, **kw)


class TestSuggest:
    def test_both_colours_of_every_game_in_the_league(self):
        games = [src("a", "Dimmit", "Grant"), src("b", "Casey", "Rice")]
        got = tn.suggest(games, src("new"))
        assert got == {"league": MONDAY, "names": ["Casey", "Dimmit", "Grant", "Rice"]}

    def test_another_league_or_season_is_left_out(self):
        games = [src("a", "Dimmit", "Grant"),
                 src("b", "Casey", "Rice", playlist="2026-2027 Tuesday Super League"),
                 src("c", "Hart", "Kim", playlist="2025-2026 Monday Open League")]
        assert tn.suggest(games, src("new"))["names"] == ["Dimmit", "Grant"]

    def test_the_most_used_come_first(self):
        games = [src("a", "Grant", "Dimmit"), src("b", "Dimmit", "Casey"),
                 src("c", "Rice", "Dimmit"), src("d", "Grant", "Hart")]
        assert tn.suggest(games, src("new"))["names"] == ["Dimmit", "Grant", "Casey",
                                                          "Hart", "Rice"]

    def test_a_tie_goes_to_the_one_used_last(self):
        games = [src("a", "Casey", None, days=0), src("b", "Rice", None, days=7)]
        assert tn.suggest(games, src("new"))["names"] == ["Rice", "Casey"]

    def test_case_and_spacing_are_one_name_said_the_usual_way(self):
        games = [src("a", "Dimmit", None), src("b", "dimmit ", None),
                 src("c", "Dimmit", None), src("d", "Team Grant", "team  grant"),
                 src("e", "Team Grant", None)]
        assert tn.suggest(games, src("new"))["names"] == ["Dimmit", "Team Grant"]

    def test_an_even_split_goes_to_the_spelling_used_last(self):
        games = [src("a", "Dimmit", None, days=0), src("b", "dimmit", None, days=7)]
        assert tn.suggest(games, src("new"))["names"] == ["dimmit"]
        games = [src("a", "dimmit", None, days=0), src("b", "Dimmit", None, days=7)]
        assert tn.suggest(games, src("new"))["names"] == ["Dimmit"]

    def test_unnamed_teams_are_not_names(self):
        games = [src("a", None, ""), src("b", "  ", "Grant")]
        assert tn.suggest(games, src("new"))["names"] == ["Grant"]

    def test_the_league_is_the_one_the_thinking_report_uses(self):
        """No playlist: the stream title's league, as /thinking groups it."""
        title = "10/7 - Sheet 2 - Wednesday Supper League 2026-2027"
        games = [src("a", "Dimmit", None, playlist=None, title=title),
                 src("b", "Grant", None, playlist=None,
                     title="10/8 - Sheet 2 - Thursday Morning League 2026-2027")]
        got = tn.suggest(games, src("new", playlist=None, title=title))
        assert got == {"league": "Wednesday Supper League 2026-2027", "names": ["Dimmit"]}

    def test_a_game_with_no_league_gets_nothing(self):
        """Every unlabelled game lands in "Other" -- not a league whose teams
        mean anything to each other."""
        games = [src("a", "Dimmit", "Grant", playlist=None, title="practice")]
        got = tn.suggest(games, src("new", playlist=None, title="practice"))
        assert got == {"league": None, "names": []}


class TestTheEndpoint:
    """GET /api/games/{id}/team-names: public, like the names themselves, and
    asked about on every open so a name typed a minute ago is offered."""

    @pytest.fixture
    def w(self, world):  # noqa: F811
        for s in (src("a", "Dimmit", "Grant"), src("b", "Casey", "Dimmit", days=7),
                  src("c", "Hart", "Kim", playlist="2026-2027 Tuesday Super League"),
                  src("new")):
            world["repo"].put_source(s)
        return world

    def get(self, w, sid="new", **headers):
        return w["client"].get(f"/api/games/{sid}/team-names", headers=headers)

    def test_it_offers_the_leagues_names(self, w):
        r = self.get(w)
        assert r.status_code == 200
        assert r.json() == {"league": MONDAY, "names": ["Dimmit", "Casey", "Grant"]}

    def test_it_is_public_and_kept_but_asked_about(self, w):
        r = self.get(w)
        assert r.headers["cache-control"] == "public, no-cache" and r.headers["etag"]
        assert self.get(w, **{"If-None-Match": r.headers["etag"]}).status_code == 304

    def test_a_name_saved_since_is_offered_on_the_next_open(self, w):
        tag = self.get(w).headers["etag"]
        w["repo"].update_source("a", team_yellow="Rice")
        again = self.get(w, **{"If-None-Match": tag})
        assert again.status_code == 200 and "Rice" in again.json()["names"]

    def test_merged_and_practice_games_are_not_the_leagues(self, w):
        w["repo"].put_source(src("m", "Merged", None, merged_into="a"))
        w["repo"].put_source(src("p", "Practising", None, practice=True))
        assert self.get(w).json()["names"] == ["Dimmit", "Casey", "Grant"]

    def test_no_such_game(self, w):
        assert self.get(w, "nope").status_code == 404
