"""The thinking report through the API: every game summarised as its run
finishes, kept in step when its play start moves, backfilled for games from
before, and grouped by the club's own playlist."""

import pytest

from tests.test_service_api import (  # noqa: F401
    ADMIN, CLUB, DOUBLES_VID, VID, add_doubles_video, practice_doc, sample_doc, spans_doc,
    submit, work_through, world,
)


def with_thinking(doc, per_end=((90.0, 60.0),)):
    """`doc` with a clock on every end: (red, yellow) seconds per end, cycled,
    and the game totals summed from them the way the pipeline writes them."""
    for game in doc["games"]:
        red = yellow = 0.0
        for i, end in enumerate(game["ends"]):
            r, y = per_end[i % len(per_end)]
            end["thinking_time"] = {"red": r, "yellow": y, "measured_shots": 15,
                                    "unmeasured_shots": 1, "estimated_shots": 0,
                                    "anomalies": 0}
            red, yellow = red + r, yellow + y
        n = len(game["ends"])
        game["thinking_time"] = {"red": red, "yellow": yellow, "measured_shots": 15 * n,
                                 "unmeasured_shots": n, "estimated_shots": 0, "anomalies": 0}
    return doc


def report(w):
    r = w["client"].get("/api/reports/thinking")
    assert r.status_code == 200, r.text
    return r.json()


def game_row(w):
    (league,) = report(w)["leagues"]
    (row,) = league["by_pace"]
    return league, row


def the_source(w):
    (src,) = w["repo"].list_sources()
    return src


class TestSummarisedOnCompletion:
    def test_a_finished_game_is_in_the_report(self, world):
        submit(world)
        work_through(world, doc=with_thinking(sample_doc(1)), games=1)
        league, row = game_row(world)
        assert league["league"] == "Spring League" and league["from"] == "title"
        assert row["red_s"] == 90.0 and row["yellow_s"] == 60.0 and row["ends"] == 1
        assert row["share"] == pytest.approx(0.6) and row["longer"] == "red"
        assert report(world)["pending"] == 0

    def test_the_report_is_public_and_cacheable(self, world):
        r = world["client"].get("/api/reports/thinking")
        assert r.status_code == 200 and r.json()["leagues"] == []
        assert r.headers["cache-control"] == "public, max-age=300"

    def test_a_timeline_without_a_clock_is_left_out_rather_than_pending(self, world):
        submit(world)
        work_through(world, doc=sample_doc(1), games=1)
        got = report(world)
        assert got["leagues"] == [] and got["pending"] == 0

    def test_a_reprocess_replaces_the_summary(self, world):
        submit(world)
        work_through(world, doc=with_thinking(sample_doc(1)), games=1)
        r = world["client"].post("/api/admin/reprocess", json={"video_id": VID}, headers=ADMIN)
        assert r.status_code in (200, 201), r.text
        work_through(world, doc=with_thinking(sample_doc(1), [(30.0, 120.0)]), games=1)
        _, row = game_row(world)
        assert (row["red_s"], row["yellow_s"]) == (30.0, 120.0)
        assert the_source(world).thinking["run_id"] == the_source(world).current_run_id


class TestFollowsThePlayStart:
    def _game(self, world):
        submit(world)
        # Three warm-up ends ahead of two real ones, each end its own clock.
        work_through(world, doc=with_thinking(practice_doc(), [(10.0, 10.0), (11.0, 11.0),
                                                               (12.0, 12.0), (100.0, 50.0),
                                                               (200.0, 50.0)]), games=1)
        return the_source(world).id

    def test_trimming_the_warm_up_takes_its_thinking_out(self, world):
        sid = self._game(world)
        assert game_row(world)[1]["ends"] == 5
        r = world["client"].post(f"/api/admin/games/{sid}/play-start", json={"start_s": 1440},
                                 headers=ADMIN)
        assert r.status_code == 200, r.text
        _, row = game_row(world)
        assert row["ends"] == 2 and (row["red_s"], row["yellow_s"]) == (300.0, 100.0)
        assert row["per_end_s"] == pytest.approx(200.0)

    def test_clearing_it_puts_the_warm_up_back(self, world):
        sid = self._game(world)
        c = world["client"]
        c.post(f"/api/admin/games/{sid}/play-start", json={"start_s": 1440}, headers=ADMIN)
        c.post(f"/api/admin/games/{sid}/play-start", json={"start_s": None}, headers=ADMIN)
        assert game_row(world)[1]["ends"] == 5

    def test_a_chart_that_says_where_play_starts_trims_it_too(self, world):
        submit(world, url=f"https://youtu.be/{VID}?t=1440")
        work_through(world, doc=with_thinking(practice_doc(), [(10.0, 10.0), (11.0, 11.0),
                                                               (12.0, 12.0), (100.0, 50.0),
                                                               (200.0, 50.0)]), games=1)
        assert game_row(world)[1]["ends"] == 2


class TestBackfill:
    def _unsummarised(self, world):
        submit(world)
        work_through(world, doc=with_thinking(sample_doc(1)), games=1)
        src = the_source(world)
        world["repo"].update_source(src.id, thinking=None)
        return src.id

    def test_it_needs_the_admin_token(self, world):
        assert world["client"].post("/api/admin/backfill-thinking").status_code == 401

    def test_a_game_from_before_the_report_is_pending_until_backfilled(self, world):
        self._unsummarised(world)
        assert report(world)["pending"] == 1 and report(world)["leagues"] == []
        r = world["client"].post("/api/admin/backfill-thinking", headers=ADMIN)
        assert r.json() == {"ok": True, "refreshed": 1, "skipped": 0, "remaining": 0}
        assert report(world)["pending"] == 0
        assert game_row(world)[1]["red_s"] == 90.0

    def test_it_is_bounded_and_can_be_called_again(self, world):
        submit(world)
        work_through(world, doc=with_thinking(sample_doc(2)), games=2)
        for s in world["repo"].list_sources():
            world["repo"].update_source(s.id, thinking=None)
        c = world["client"]
        assert c.post("/api/admin/backfill-thinking?limit=1", headers=ADMIN).json()["remaining"] == 1
        assert c.post("/api/admin/backfill-thinking?limit=1", headers=ADMIN).json()["remaining"] == 0
        assert c.post("/api/admin/backfill-thinking", headers=ADMIN).json()["refreshed"] == 0


class TestTheLeagueIsThePlaylist:
    def _club_playlist(self, world, title="2026 Spring League", videos=(VID,)):
        world["yt"].add_playlist(CLUB, "PL_spring", title, list(videos))

    def test_the_index_names_the_league(self, world):
        submit(world)
        work_through(world, doc=with_thinking(sample_doc(1)), games=1)
        self._club_playlist(world)
        r = world["client"].post("/api/admin/playlist-index", headers=ADMIN)
        assert r.status_code == 200, r.text
        assert r.json()["scanned"] == 1 and r.json()["stamped"] == 1
        league, _ = game_row(world)
        assert (league["league"], league["from"]) == ("2026 Spring League", "playlist")

    def test_the_index_needs_the_admin_token(self, world):
        assert world["client"].post("/api/admin/playlist-index").status_code == 401

    def test_a_game_finishing_after_the_index_is_stamped_at_once(self, world):
        self._club_playlist(world)
        world["client"].post("/api/admin/playlist-index", headers=ADMIN)
        submit(world)
        work_through(world, doc=with_thinking(sample_doc(1)), games=1)
        assert the_source(world).playlist_title == "2026 Spring League"

    def test_the_hourly_poll_keeps_the_index(self, world):
        submit(world)
        work_through(world, doc=with_thinking(sample_doc(1)), games=1)
        self._club_playlist(world)
        r = world["client"].post("/api/admin/poll-playlists", headers=ADMIN)
        assert r.status_code == 200 and r.json()["index"]["stamped"] == 1
        assert game_row(world)[0]["league"] == "2026 Spring League"

    def test_a_youtube_failure_does_not_stop_the_poll(self, world, monkeypatch):
        def broken(channel_id):
            raise RuntimeError("quota exceeded")
        monkeypatch.setattr(world["yt"], "channel_playlists", broken)
        r = world["client"].post("/api/admin/poll-playlists", headers=ADMIN)
        assert r.status_code == 200 and "quota" in r.json()["index"]["error"]
        r = world["client"].post("/api/admin/playlist-index", headers=ADMIN)
        assert r.status_code == 502

    def test_the_hand_label_is_left_alone(self, world):
        submit(world)
        work_through(world, doc=with_thinking(sample_doc(1)), games=1)
        src = the_source(world)
        world["repo"].update_source(src.id, league="Spring Thursday")
        self._club_playlist(world)
        world["client"].post("/api/admin/playlist-index", headers=ADMIN)
        assert the_source(world).league == "Spring Thursday"
        games = world["client"].get("/api/games").json()["games"]
        assert games[0]["league"] == "Spring Thursday"


class TestDoubles:
    def test_a_doubles_league_is_marked_and_kept_apart(self, world):
        world["settings"].doubles_enabled = True
        add_doubles_video(world)
        submit(world, url=f"https://youtu.be/{DOUBLES_VID}")
        work_through(world, doc=with_thinking(sample_doc(1), [(40.0, 80.0)]), games=1,
                     fmt="doubles")
        league, row = game_row(world)
        assert league["format"] == "doubles" and row["format"] == "doubles"
        assert row["longer"] == "yellow"


def test_the_page_is_served(world):
    r = world["client"].get("/thinking")
    assert r.status_code == 200 and 'data-page="thinking"' in r.text


def test_a_quiet_poll_reads_no_sources(world, monkeypatch):
    """Nothing new on YouTube means nothing to re-stamp: the hourly poll
    must not read every game in the catalogue to find that out."""
    world["yt"].add_playlist(CLUB, "PL_spring", "2026 Spring League", [VID])
    c = world["client"]
    assert c.post("/api/admin/poll-playlists", headers=ADMIN).json()["index"]["scanned"] == 1
    calls = []
    real = world["repo"].list_sources
    monkeypatch.setattr(world["repo"], "list_sources", lambda *a, **k: calls.append(1) or real(*a, **k))
    got = c.post("/api/admin/poll-playlists", headers=ADMIN).json()["index"]
    assert got["scanned"] == 0 and got["stamped"] == 0 and calls == []


def test_a_game_with_no_timeline_to_read_is_left_out_not_pending(world):
    submit(world)
    work_through(world, doc=with_thinking(sample_doc(1)), games=1)
    src = the_source(world)
    world["repo"].update_run(src.current_run_id, timeline_key="runs/gone/timeline.json")
    world["repo"].update_source(src.id, thinking=None)
    r = world["client"].post("/api/admin/backfill-thinking", headers=ADMIN).json()
    assert r["refreshed"] == 1 and r["remaining"] == 0
    got = report(world)
    assert got["pending"] == 0 and got["leagues"] == []
    assert the_source(world).thinking["measured_shots"] == 0


def test_a_new_chart_repairs_a_summary_the_game_moved_out_from_under(world):
    submit(world)
    work_through(world, doc=with_thinking(sample_doc(1)), games=1)
    world["repo"].update_source(the_source(world).id, thinking=None)
    assert report(world)["pending"] == 1
    assert submit(world, ip="5.6.7.8").status_code in (200, 201)
    assert report(world)["pending"] == 0 and game_row(world)[1]["red_s"] == 90.0


def test_a_retry_drops_the_summary_until_the_run_is_read_again(world):
    submit(world)
    work_through(world, doc=with_thinking(sample_doc(1)), games=1)
    run_id = the_source(world).current_run_id
    assert world["client"].post(f"/api/admin/runs/{run_id}/retry", headers=ADMIN).status_code == 200
    assert report(world)["pending"] == 1 and report(world)["leagues"] == []
    world["clock"].advance(600)
    work_through(world, doc=with_thinking(sample_doc(1), [(30.0, 120.0)]), games=1)
    assert report(world)["pending"] == 0
    assert (game_row(world)[1]["red_s"], game_row(world)[1]["yellow_s"]) == (30.0, 120.0)


class TestAGameJoinedFromTwo:
    def test_its_left_over_page_is_not_ranked_as_a_game(self, world):
        """Monday sheet 5, 2026-09-28: the late game read as two, then as one."""
        submit(world)
        work_through(world, with_thinking(
            spans_doc((130.0, 6245.0), (7210.0, 8905.0), (9215.0, 13730.0))))
        world["client"].post("/api/admin/reprocess", headers=ADMIN, json={"video_id": VID})
        work_through(world, with_thinking(spans_doc((130.0, 6245.0), (7210.0, 13730.0))))
        (league,) = report(world)["leagues"]
        folded = {s.id for s in world["repo"].sources_for_video(VID) if s.merged_into}
        assert len(folded) == 1
        assert len(league["by_pace"]) == 2
        assert not folded & {row["source_id"] for row in league["by_pace"]}
