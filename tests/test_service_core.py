"""The service's building blocks, none of which need a network or a cloud."""

from datetime import datetime, timedelta, timezone

import pytest

from curling_score.service import dedupe, playlists, slug
from curling_score.service.records import Chart, Job, Run, Source, WatchedPlaylist, Worker
from curling_score.service.repo import MemoryRepo, worker_online
from curling_score.service.store import MemoryStore, detcache_key, timeline_key
from curling_score.service.youtube import (
    FakeYouTube, SubmissionError, VideoMeta, parse_duration, validate_submission,
)

T0 = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)


def at(seconds):
    return T0 + timedelta(seconds=seconds)


def run(**kw):
    base = dict(id=slug.new_run_id(), video_id="VXU9xwmugRg",
                processing_version="p+m", status="ready", created_at=T0,
                games=[{"index": 0, "start_s": 0.0, "end_s": 6400.0, "ends": 7},
                       {"index": 1, "start_s": 7500.0, "end_s": 14000.0, "ends": 6}])
    base.update(kw)
    return Run(**base)


def chart(run_, **kw):
    base = dict(id=slug.new_slug(), share_slug=slug.new_slug(), video_id=run_.video_id,
                run_id=run_.id, created_at=T0, updated_at=T0)
    base.update(kw)
    return Chart(**base)


class TestSlugs:
    def test_chart_slugs_are_22_chars_of_base62(self):
        s = slug.new_slug()
        assert len(s) == 22 and slug.is_slug(s)

    def test_run_and_job_ids_are_prefixed(self):
        assert slug.new_run_id().startswith("r_")
        assert slug.new_job_id().startswith("j_")

    def test_ids_do_not_repeat(self):
        assert len({slug.new_slug() for _ in range(2000)}) == 2000

    def test_encoding_is_fixed_width(self):
        assert len(slug.encode(b"\x00" * 16)) == 22
        assert len(slug.encode(b"\xff" * 16)) == 22

    def test_is_slug_rejects_the_wrong_shape(self):
        assert not slug.is_slug("short")
        assert not slug.is_slug("x" * 22 + "!")
        assert not slug.is_slug(None)


class TestQueue:
    def test_claim_hands_out_the_oldest_ready_job_once(self):
        repo = MemoryRepo()
        r1, r2 = run(id="r_a", status="queued"), run(id="r_b", status="queued")
        repo.put_run(r1); repo.put_run(r2)
        repo.put_job(Job(id="j_b", run_id="r_b", state="queued", created_at=at(5), run_after=at(5)))
        repo.put_job(Job(id="j_a", run_id="r_a", state="queued", created_at=at(0), run_after=at(0)))
        got = repo.claim_job("w1", now=at(10))
        assert got.id == "j_a" and got.state == "running" and got.worker_id == "w1"
        assert got.attempts == 1
        assert repo.get_run("r_a").status == "processing"
        assert repo.claim_job("w2", now=at(10)).id == "j_b"
        assert repo.claim_job("w3", now=at(10)) is None

    def test_a_job_not_yet_due_is_not_claimed(self):
        repo = MemoryRepo()
        repo.put_job(Job(id="j", run_id="r", state="queued", created_at=at(0), run_after=at(600)))
        assert repo.claim_job("w", now=at(10)) is None
        assert repo.claim_job("w", now=at(601)) is not None

    def test_an_expired_lease_goes_back_to_the_queue(self):
        repo = MemoryRepo()
        repo.put_run(run(id="r", status="queued"))
        repo.put_job(Job(id="j", run_id="r", state="queued", created_at=at(0), run_after=at(0)))
        repo.claim_job("w", now=at(0), lease_s=600)
        assert repo.requeue_expired(now=at(599)) == 0
        assert repo.requeue_expired(now=at(601)) == 1
        job = repo.get_job("j")
        assert job.state == "queued" and job.worker_id is None
        assert repo.get_run("r").status == "queued"
        # It can be claimed again, and that counts as a second attempt.
        assert repo.claim_job("w2", now=at(602)).attempts == 2

    def test_queue_position_counts_only_those_ahead(self):
        repo = MemoryRepo()
        for i in range(3):
            repo.put_job(Job(id=f"j{i}", run_id=f"r{i}", state="queued",
                             created_at=at(i), run_after=at(i)))
        assert repo.queue_position("j0") == 0
        assert repo.queue_position("j2") == 2
        assert repo.count_queued() == 3
        repo.claim_job("w", now=at(10))
        assert repo.queue_position("j2") == 1


class TestOverridesVersioning:
    def test_a_save_with_the_current_version_wins_and_bumps(self):
        repo = MemoryRepo()
        c = chart(run()); repo.put_chart(c)
        ok, version, _ = repo.save_overrides(c.id, {"0.1.1": {"user_score": 3}}, 0, at(1))
        assert ok and version == 1
        assert repo.get_chart(c.id).overrides == {"0.1.1": {"user_score": 3}}

    def test_a_stale_save_is_refused_and_returns_the_truth(self):
        repo = MemoryRepo()
        c = chart(run()); repo.put_chart(c)
        repo.save_overrides(c.id, {"a": {"x": 1}}, 0, at(1))
        ok, version, current = repo.save_overrides(c.id, {"b": {"y": 2}}, 0, at(2))
        assert not ok and version == 1 and current == {"a": {"x": 1}}

    def test_an_unconditional_save_always_lands(self):
        # sendBeacon cannot carry a version; the page's last gasp must not 409.
        repo = MemoryRepo()
        c = chart(run()); repo.put_chart(c)
        repo.save_overrides(c.id, {"a": {}}, 0, at(1))
        ok, version, _ = repo.save_overrides(c.id, {"b": {}}, None, at(2))
        assert ok and version == 2

    def test_share_slug_finds_the_chart(self):
        repo = MemoryRepo()
        c = chart(run()); repo.put_chart(c)
        assert repo.chart_by_share(c.share_slug).id == c.id
        assert repo.chart_by_share("nope") is None


class TestRateLimit:
    def test_limits_per_hour_and_per_day(self):
        repo = MemoryRepo()
        allowed = [repo.bump_rate_limit("ip", at(i), hour_limit=3, day_limit=5) for i in range(4)]
        assert allowed == [True, True, True, False]
        # A new hour resets the hourly count, but the day keeps counting.
        later = at(3700)
        assert repo.bump_rate_limit("ip", later, 3, 5)
        assert repo.bump_rate_limit("ip", later, 3, 5)
        assert not repo.bump_rate_limit("ip", later, 3, 5)   # day limit of 5 hit

    def test_addresses_are_independent(self):
        repo = MemoryRepo()
        assert repo.bump_rate_limit("a", T0, 1, 1)
        assert repo.bump_rate_limit("b", T0, 1, 1)
        assert not repo.bump_rate_limit("a", T0, 1, 1)


class TestWorkerPresence:
    def test_a_recent_heartbeat_means_online(self):
        w = Worker(id="home", last_seen_at=at(0))
        assert worker_online([w], now=at(60))
        assert not worker_online([w], now=at(120))
        assert not worker_online([], now=at(0))


class TestBackup:
    def test_export_round_trips_through_import(self):
        repo = MemoryRepo()
        r = run(); repo.put_run(r)
        c = chart(r, overrides={"0.1.1": {"user_score": 4}}, overrides_version=1)
        repo.put_chart(c)
        repo.put_playlist(WatchedPlaylist(id="p", playlist_id="PL1", label="Tuesday",
                                          created_at=T0))
        data = repo.export_all()
        fresh = MemoryRepo()
        fresh.import_all(data)
        assert fresh.get_chart(c.id).overrides == c.overrides
        assert fresh.chart_by_share(c.share_slug).id == c.id
        assert fresh.get_run(r.id).games == r.games
        assert fresh.get_playlist("p").label == "Tuesday"


class TestStore:
    def test_memory_store_round_trip_and_keys(self):
        s = MemoryStore()
        s.put_bytes(timeline_key("v", "r_1"), b"{}", "application/json")
        assert s.exists("runs/v/r_1/timeline.json")
        assert s.get_bytes("runs/v/r_1/timeline.json") == b"{}"
        assert s.list_keys("runs/v/") == ["runs/v/r_1/timeline.json"]
        assert s.get_bytes("missing") is None
        assert detcache_key("v", "abc") == "detcache/v/abc.npz"
        assert s.presign_put("k", "application/json")["headers"]["Content-Type"] == "application/json"


class TestWindows:
    def test_a_normal_stream_is_analysed_whole(self):
        assert dedupe.window_for(4 * 3600, None) == (None, None)
        assert dedupe.window_for(4 * 3600, 5000.0) == (None, None)

    def test_a_long_stream_is_windowed_around_the_start(self):
        assert dedupe.window_for(10 * 3600, 20000.0) == (19400.0, 20000.0 + 4 * 3600)

    def test_the_window_stays_inside_the_video(self):
        assert dedupe.window_for(8 * 3600, 300.0) == (0.0, 300.0 + 4 * 3600)
        assert dedupe.window_for(8 * 3600, 8 * 3600 - 100.0)[1] == 8 * 3600

    def test_a_long_stream_without_a_start_is_refused(self):
        with pytest.raises(dedupe.NeedsStartTime):
            dedupe.window_for(8 * 3600, None)


class TestAskingForALength:
    """A four-hour stream holding several games should not cost four hours.

    Under ``MAX_WHOLE_S`` the window is normally the whole video, so a start
    time alone narrows nothing. An explicit length has to beat that.
    """

    def test_a_length_narrows_a_stream_that_would_be_analysed_whole(self):
        assert dedupe.window_for(4 * 3600, None, 2 * 3600) == (0.0, 2 * 3600)

    def test_no_length_still_means_the_whole_video(self):
        assert dedupe.window_for(4 * 3600, None) == (None, None)

    def test_a_length_runs_from_the_start_time_and_keeps_the_lead_in(self):
        # 1:52:30 for two hours, with the ten minutes before that a game often
        # needs -- so the span is the length plus the lead-in, not the length.
        start = 1.875 * 3600
        got = dedupe.window_for(4 * 3600, start, 2 * 3600)
        assert got == (start - dedupe.WINDOW_BEFORE_S, start + 2 * 3600)

    def test_a_length_is_clamped_to_the_video(self):
        assert dedupe.window_for(3 * 3600, None, 9 * 3600) == (0.0, 3 * 3600)

    def test_the_lead_in_never_runs_before_the_video(self):
        assert dedupe.window_for(4 * 3600, 60.0, 1800.0)[0] == 0.0

    def test_a_length_satisfies_a_long_stream_that_would_otherwise_be_refused(self):
        # Saying how long is at least as good as saying where it starts.
        assert dedupe.window_for(8 * 3600, None, 2 * 3600) == (0.0, 2 * 3600)

    def test_a_length_that_is_not_a_length_is_refused(self):
        for bad in (0, -1, -3600):
            with pytest.raises(ValueError):
                dedupe.window_for(4 * 3600, None, bad)


class TestReusableRun:
    def test_the_same_video_and_version_is_reused(self):
        r = run(status="ready")
        assert dedupe.find_reusable_run([r], "p+m", 3000.0) is r

    def test_an_older_model_is_not_reused(self):
        r = run(processing_version="p+old")
        assert dedupe.find_reusable_run([r], "p+m", None) is None

    def test_a_failed_run_is_not_reused(self):
        assert dedupe.find_reusable_run([run(status="failed")], "p+m", None) is None

    def test_a_windowed_run_only_covers_its_window(self):
        r = run(window_start_s=19400.0, window_end_s=34400.0)
        assert dedupe.find_reusable_run([r], "p+m", 20000.0) is r
        assert dedupe.find_reusable_run([r], "p+m", 1000.0) is None
        assert dedupe.find_reusable_run([r], "p+m", None) is None

    def test_a_ready_run_beats_a_queued_one(self):
        queued = run(id="r_q", status="queued", created_at=at(100))
        ready = run(id="r_r", status="ready", created_at=at(0))
        assert dedupe.find_reusable_run([queued, ready], "p+m", None) is ready


class TestSnapping:
    GAMES = [{"index": 0, "start_s": 100.0, "end_s": 6400.0},
             {"index": 1, "start_s": 7500.0, "end_s": 14000.0}]

    def test_no_start_means_the_first_game(self):
        assert dedupe.snap_to_game(self.GAMES, None) == (0, 0.0)

    def test_a_time_inside_a_game_is_that_game(self):
        assert dedupe.snap_to_game(self.GAMES, 8000.0) == (1, 0.0)

    def test_a_little_early_still_counts(self):
        assert dedupe.snap_to_game(self.GAMES, 7450.0) == (1, 0.0)

    def test_between_games_snaps_to_the_nearest_and_says_how_far(self):
        idx, dist = dedupe.snap_to_game(self.GAMES, 7000.0)
        assert idx == 1 and dist == pytest.approx(500.0)
        idx, dist = dedupe.snap_to_game(self.GAMES, 6700.0)
        assert idx == 0 and dist == pytest.approx(300.0)

    def test_no_games_means_nothing_to_snap_to(self):
        assert dedupe.snap_to_game([], 100.0) == (None, None)


class TestSources:
    def test_one_source_per_game_however_many_charts(self):
        repo = MemoryRepo()
        r = run(); repo.put_run(r)
        c1 = chart(r, requested_start_s=8000.0); repo.put_chart(c1)
        c2 = chart(r, requested_start_s=9000.0); repo.put_chart(c2)
        c3 = chart(r, requested_start_s=None); repo.put_chart(c3)
        assert dedupe.resolve_charts_for_run(repo, r, at(1)) == 3
        sources = repo.sources_for_video(r.video_id)
        assert len(sources) == 2                      # two games discovered
        a, b, c = (repo.get_chart(x.id) for x in (c1, c2, c3))
        assert a.source_id == b.source_id             # same game -> same source
        assert a.game_index == b.game_index == 1
        assert c.game_index == 0 and c.source_id != a.source_id

    def test_two_runs_of_the_same_game_share_a_source(self):
        repo = MemoryRepo()
        r1 = run(id="r_1"); repo.put_run(r1)
        dedupe.resolve_charts_for_run(repo, r1, at(0))
        r2 = run(id="r_2", processing_version="p+new",
                 games=[{"index": 0, "start_s": 10.0, "end_s": 6390.0, "ends": 7}])
        repo.put_run(r2)
        dedupe.resolve_charts_for_run(repo, r2, at(1))
        assert len(repo.sources_for_video("VXU9xwmugRg")) == 2   # still two games

    def test_same_game_is_by_overlap(self):
        assert dedupe.same_game(0, 6400, 10, 6390)
        assert not dedupe.same_game(0, 6400, 7500, 14000)


class TestValidation:
    def meta(self, **kw):
        base = dict(video_id="VXU9xwmugRg", title="4/30 - Sheet 2 - League",
                    channel_id="UCclub", duration_s=14392.0, live_status="none")
        base.update(kw)
        return VideoMeta(**base)

    def test_a_good_archived_club_video_passes(self):
        assert validate_submission(self.meta(), {"UCclub"}, 5.0).video_id == "VXU9xwmugRg"

    def test_no_allowlist_means_any_channel(self):
        assert validate_submission(self.meta(channel_id="UCother"), None, 5.0)

    @pytest.mark.parametrize("kw,code,status", [
        (dict(channel_id="UCother"), "channel_not_allowed", 403),
        (dict(live_status="live"), "still_live", 422),
        (dict(live_status="upcoming"), "still_live", 422),
        (dict(duration_s=0.0), "no_duration", 422),
        (dict(duration_s=7 * 3600.0), "too_long", 422),
    ])
    def test_refusals_carry_a_code_and_status(self, kw, code, status):
        with pytest.raises(SubmissionError) as ei:
            validate_submission(self.meta(**kw), {"UCclub"}, 5.0)
        assert ei.value.code == code and ei.value.status == status

    def test_a_missing_video_is_a_404(self):
        with pytest.raises(SubmissionError) as ei:
            validate_submission(None, None, 5.0)
        assert ei.value.status == 404

    def test_iso_durations(self):
        assert parse_duration("PT3H59M52S") == 14392.0
        assert parse_duration("PT45S") == 45.0
        assert parse_duration("P1DT1H") == 90000.0
        with pytest.raises(ValueError):
            parse_duration("3:59:52")


class TestPlaylistWatcher:
    def _setup(self):
        repo = MemoryRepo()
        repo.put_playlist(WatchedPlaylist(id="p", playlist_id="PL1", label="Tuesday",
                                          created_at=T0))
        yt = FakeYouTube(playlists={"PL1": ["vidA", "vidB", "vidC"]})
        yt.add(VideoMeta("vidA", "Sheet 1", "UCclub", 14000.0, "none", at(-3600)))
        yt.add(VideoMeta("vidB", "Sheet 2", "UCclub", 14000.0, "none", at(-3600)))
        yt.add(VideoMeta("vidC", "Sheet 3", "UCclub", 0.0, "live", at(-100)))
        return repo, yt

    def test_new_archived_videos_become_queued_runs(self):
        repo, yt = self._setup()
        result = playlists.poll(repo, yt, processing_version="p+m", now=T0,
                                allowed_channels={"UCclub"})
        assert len(result["created"]) == 2
        runs = repo.list_runs()
        assert {r.video_id for r in runs} == {"vidA", "vidB"}
        assert all(r.status == "queued" and r.league == "Tuesday" for r in runs)
        assert repo.count_queued() == 2

    def test_a_live_stream_is_left_for_the_next_poll(self):
        repo, yt = self._setup()
        playlists.poll(repo, yt, processing_version="p+m", now=T0)
        pl = repo.get_playlist("p")
        assert "vidC" not in pl.last_seen_video_ids
        assert set(pl.last_seen_video_ids) == {"vidA", "vidB"}
        # Once archived, the next poll picks it up.
        yt.add(VideoMeta("vidC", "Sheet 3", "UCclub", 14000.0, "none", at(0)))
        result = playlists.poll(repo, yt, processing_version="p+m", now=at(3600))
        assert len(result["created"]) == 1
        assert repo.list_runs()[0].video_id == "vidC"

    def test_already_seen_videos_are_not_requeued(self):
        repo, yt = self._setup()
        playlists.poll(repo, yt, processing_version="p+m", now=T0)
        again = playlists.poll(repo, yt, processing_version="p+m", now=at(3600))
        assert again["created"] == []
        assert repo.count_queued() == 2

    def test_a_video_from_another_channel_is_skipped_and_remembered(self):
        repo, yt = self._setup()
        yt.add(VideoMeta("vidA", "x", "UCstranger", 14000.0, "none"))
        result = playlists.poll(repo, yt, processing_version="p+m", now=T0,
                                allowed_channels={"UCclub"})
        assert ("vidA", "channel_not_allowed") in result["skipped"]
        assert "vidA" in repo.get_playlist("p").last_seen_video_ids

    def test_a_backfill_trickles(self):
        repo = MemoryRepo()
        repo.put_playlist(WatchedPlaylist(id="p", playlist_id="PL", label="L", created_at=T0))
        ids = [f"vid{i:02d}" for i in range(30)]
        yt = FakeYouTube(playlists={"PL": ids})
        for v in ids:
            yt.add(VideoMeta(v, v, "UC", 10000.0, "none"))
        first = playlists.poll(repo, yt, processing_version="p+m", now=T0, max_new=10)
        assert len(first["created"]) == 10
        second = playlists.poll(repo, yt, processing_version="p+m", now=at(3600), max_new=10)
        assert len(second["created"]) == 10
        assert repo.count_queued() == 20

    def test_a_vanished_playlist_is_disabled_after_three_failures(self):
        repo = MemoryRepo()
        repo.put_playlist(WatchedPlaylist(id="p", playlist_id="GONE", label="L", created_at=T0))
        yt = FakeYouTube(playlists={})
        for i in range(3):
            playlists.poll(repo, yt, processing_version="p+m", now=at(i))
        pl = repo.get_playlist("p")
        assert pl.failures == 3 and pl.enabled is False
        # And it is no longer polled.
        calls = yt.calls
        playlists.poll(repo, yt, processing_version="p+m", now=at(10))
        assert yt.calls == calls

    def test_a_video_already_processed_is_not_reprocessed(self):
        repo, yt = self._setup()
        repo.put_run(run(id="r_x", video_id="vidA", processing_version="p+m", status="ready"))
        result = playlists.poll(repo, yt, processing_version="p+m", now=T0)
        assert len(result["created"]) == 1
        assert repo.list_runs(status="queued")[0].video_id == "vidB"


class TestThePagesAreShipped:
    """Present in the source tree is not the same as present in the wheel.

    Every page the API serves 500'd in production because `package-data`
    declared only the viewer's files, and the service's own were left out of
    the installed package. The tests could not see it: they run from a checkout
    where the files are on disk either way.
    """

    def test_every_page_the_api_serves_is_declared_as_package_data(self):
        import tomllib
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        data = tomllib.loads((root / "pyproject.toml").read_text())
        globs = data["tool"]["setuptools"]["package-data"]
        assert "curling_score.service" in globs, (
            "the service's static pages are not shipped; every page 500s once "
            "installed")
        suffixes = {Path(g).suffix for g in globs["curling_score.service"]}
        served = {p.suffix for p in (root / "src/curling_score/service/static").iterdir()}
        assert served <= suffixes, f"not declared: {served - suffixes}"

    def test_the_viewer_assets_are_declared_too(self):
        import tomllib
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        globs = tomllib.loads((root / "pyproject.toml").read_text())[
            "tool"]["setuptools"]["package-data"]["curling_score.viewer"]
        assert {".html", ".js", ".css"} <= {Path(g).suffix for g in globs}
