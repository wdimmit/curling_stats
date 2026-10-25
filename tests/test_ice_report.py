"""The ice maker's report's list of draws: a league's games grouped by when
they started, worked out from the sources alone -- and served to people
signed in."""

from datetime import datetime, timedelta, timezone

from curling_score.service import ice_report
from curling_score.service.records import Source
from tests.test_service_api import submit, work_through, world, sample_doc  # noqa: F401
from tests.test_service_auth import SARAH, w  # noqa: F401  (w: accounts on)

# 7 pm Pacific, a Tuesday.
NIGHT = datetime(2026, 10, 14, 2, 0, 21, tzinfo=timezone.utc)


def src(sid, *, sheet=1, index=0, start_s=10.0, at=NIGHT, video=None, fmt=None,
        playlist="Tuesday Super League 2026-2027", **kw):
    return Source(id=sid, video_id=video or f"v{sheet}_{at:%m%d%H}", game_start_s=start_s,
                  game_end_s=start_s + 7000.0, current_run_id="r_1", game_index=index,
                  created_at=at, played_at=at, title=f"10/13 - Sheet {sheet} - x",
                  sheet=sheet, playlist_title=playlist, format=fmt, **kw)


def ids(draw):
    return [g["source_id"] for g in draw["games"]]


class TestDraws:
    def test_a_recordings_two_games_are_two_draws(self):
        """The 7 pm and 9:12 pm games of each sheet share a stream."""
        report = ice_report.draws([
            src("a1", sheet=1), src("a2", sheet=1, index=1, start_s=7923.0),
            src("b1", sheet=2), src("b2", sheet=2, index=1, start_s=7709.0),
        ])
        (league,) = report["leagues"]
        late, early = league["draws"]
        assert ids(early) == ["a1", "b1"] and ids(late) == ["a2", "b2"]
        assert early["start"] == (NIGHT + timedelta(seconds=10)).isoformat()
        assert late["start"] == (NIGHT + timedelta(seconds=7709)).isoformat()

    def test_a_game_a_few_minutes_late_is_still_in_its_draw(self):
        report = ice_report.draws([src("a", sheet=1), src("b", sheet=3, start_s=1500.0)])
        assert [ids(d) for d in report["leagues"][0]["draws"]] == [["a", "b"]]

    def test_games_are_in_sheet_order(self):
        report = ice_report.draws([src("c", sheet=3, start_s=5.0), src("a", sheet=1, start_s=60.0)])
        assert ids(report["leagues"][0]["draws"][0]) == ["a", "c"]

    def test_the_warm_up_trimmed_off_moves_the_start(self):
        report = ice_report.draws([src("a", play_start_s=600.0)])
        assert report["leagues"][0]["draws"][0]["start"] == (NIGHT + timedelta(seconds=600)).isoformat()

    def test_weeks_and_leagues_are_newest_first(self):
        week = timedelta(days=7)
        report = ice_report.draws([
            src("old", at=NIGHT - week), src("new", at=NIGHT),
            src("sup", at=NIGHT - timedelta(hours=2), playlist="Tuesday Supper League"),
            src("mon", at=NIGHT - timedelta(days=1), playlist="Monday Open League"),
        ])
        assert [lg["league"] for lg in report["leagues"]] == [
            "Tuesday Super League 2026-2027", "Tuesday Supper League", "Monday Open League"]
        assert [ids(d) for d in report["leagues"][0]["draws"]] == [["new"], ["old"]]

    def test_doubles_is_its_own_league(self):
        report = ice_report.draws([src("f"), src("d", sheet=2, fmt="doubles")])
        assert sorted((lg["format"], ids(lg["draws"][0])[0]) for lg in report["leagues"]) == [
            ("doubles", "d"), ("fours", "f")]

    def test_a_game_with_no_date_is_left_out(self):
        s = src("x")
        s.played_at = None
        assert ice_report.draws([s]) == {"leagues": []}

    def test_a_game_carries_its_teams(self):
        report = ice_report.draws([src("a", team_red="Bonin", team_yellow="Dimmit")])
        assert report["leagues"][0]["draws"][0]["games"] == [
            {"source_id": "a", "sheet": 1, "team_red": "Bonin", "team_yellow": "Dimmit"}]


class TestThroughTheApi:
    def test_a_finished_game_is_listed(self, world):
        submit(world)
        work_through(world, doc=sample_doc(1), games=1)
        r = world["client"].get("/api/reports/ice")
        assert r.status_code == 200, r.text
        (league,) = r.json()["leagues"]
        (draw,) = league["draws"]
        (src_,) = world["repo"].list_sources()
        assert ids(draw) == [src_.id]
        assert r.headers["cache-control"] == "private, no-cache" and r.headers["etag"]

    def test_signed_out_is_told_to_sign_in(self, w):
        r = w["client"].get("/api/reports/ice")
        assert r.status_code == 401 and r.json()["detail"] == "sign in to see the ice report"

    def test_anyone_signed_in_sees_it(self, w):
        r = w["client"].get("/api/reports/ice", headers=SARAH)
        assert r.status_code == 200 and r.json() == {"leagues": []}

    def test_the_page_is_served(self, world):
        r = world["client"].get("/ice")
        assert r.status_code == 200 and 'data-page="ice"' in r.text
