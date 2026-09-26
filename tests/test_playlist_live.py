"""Watched playlists on a schedule, and a stream that goes live in one of them
becoming a live job."""

from datetime import datetime, timedelta, timezone

import pytest

from curling_score.service import playlists
from curling_score.service.records import Run, WatchedPlaylist
from curling_score.service.repo import MemoryRepo
from curling_score.service.youtube import FakeYouTube, VideoMeta

TZ = "America/Los_Angeles"
# Thursday 1 October 2026, 19:00 in Seattle.
THU_7PM = datetime(2026, 10, 2, 2, 0, tzinfo=timezone.utc)
NIGHT = [{"days": ["thu"], "start": "18:00", "end": "23:30"}]


def later(t, minutes):
    return t + timedelta(minutes=minutes)


def watched(**kw):
    base = dict(id="p", playlist_id="PL1", label="Thursday", created_at=THU_7PM)
    base.update(kw)
    return WatchedPlaylist(**base)


class TestWhenAPlaylistIsDue:
    def test_one_never_polled_is_due(self):
        assert playlists.due(watched(schedule=NIGHT), THU_7PM, TZ)

    def test_inside_its_window_every_few_minutes(self):
        pl = watched(schedule=NIGHT, last_polled_at=later(THU_7PM, -2))
        assert not playlists.due(pl, THU_7PM, TZ)
        pl = watched(schedule=NIGHT, last_polled_at=later(THU_7PM, -3))
        assert playlists.due(pl, THU_7PM, TZ)

    def test_outside_its_window_hourly(self):
        noon = later(THU_7PM, -7 * 60)
        pl = watched(schedule=NIGHT, last_polled_at=later(noon, -30))
        assert not playlists.due(pl, noon, TZ)
        pl = watched(schedule=NIGHT, last_polled_at=later(noon, -60))
        assert playlists.due(pl, noon, TZ)

    def test_a_window_past_midnight_runs_into_the_next_day(self):
        late = [{"days": ["thu"], "start": "22:00", "end": "01:00"}]
        half_past_midnight = later(THU_7PM, 5 * 60 + 30)     # Friday 00:30
        pl = watched(schedule=late, last_polled_at=later(half_past_midnight, -3))
        assert playlists.due(pl, half_past_midnight, TZ)
        pl = watched(schedule=late, last_polled_at=later(half_past_midnight + timedelta(hours=1), -3))
        assert not playlists.due(pl, half_past_midnight + timedelta(hours=1), TZ)

    def test_with_no_idle_interval_it_is_left_alone_outside_its_windows(self):
        noon = later(THU_7PM, -7 * 60)
        pl = watched(schedule=NIGHT, idle_poll_s=None, last_polled_at=later(noon, -600))
        assert not playlists.due(pl, noon, TZ)

    def test_a_schedule_is_checked_for_sense(self):
        assert playlists.check_schedule(NIGHT) == NIGHT
        for bad in ([{"days": ["thursday"], "start": "18:00", "end": "23:00"}],
                    [{"days": ["thu"], "start": "6pm", "end": "23:00"}],
                    [{"days": [], "start": "18:00", "end": "23:00"}],
                    "thursday"):
            with pytest.raises(ValueError):
                playlists.check_schedule(bad)


class TestPollingOnASchedule:
    def test_only_the_playlists_that_are_due_are_asked(self):
        repo = MemoryRepo()
        repo.put_playlist(watched(id="a", playlist_id="PLA", schedule=NIGHT,
                                  last_polled_at=later(THU_7PM, -1)))
        repo.put_playlist(watched(id="b", playlist_id="PLB", schedule=NIGHT,
                                  last_polled_at=later(THU_7PM, -5)))
        yt = FakeYouTube(playlists={"PLA": [], "PLB": []})
        playlists.poll(repo, yt, processing_version="p+m", now=THU_7PM, tz=TZ)
        assert repo.get_playlist("a").last_polled_at == later(THU_7PM, -1)
        assert repo.get_playlist("b").last_polled_at == THU_7PM

    def test_forced_it_asks_them_all(self):
        repo = MemoryRepo()
        repo.put_playlist(watched(schedule=NIGHT, last_polled_at=later(THU_7PM, -1)))
        yt = FakeYouTube(playlists={"PL1": []})
        playlists.poll(repo, yt, processing_version="p+m", now=THU_7PM, tz=TZ, force=True)
        assert repo.get_playlist("p").last_polled_at == THU_7PM


def live_setup(live_status="live"):
    repo = MemoryRepo()
    repo.put_playlist(watched(schedule=NIGHT))
    yt = FakeYouTube(playlists={"PL1": ["vidL"]})
    yt.add(VideoMeta("vidL", "10/1 - Sheet 3 - Thursday League", "UCclub",
                     0.0, live_status, THU_7PM))
    return repo, yt


def poll(repo, yt, t=THU_7PM, **kw):
    kw.setdefault("live", True)
    return playlists.poll(repo, yt, processing_version="p+m", now=t, tz=TZ,
                          allowed_channels={"UCclub"}, force=True, **kw)


class TestAStreamGoingLive:
    def test_it_becomes_a_live_job_and_stays_unseen(self):
        repo, yt = live_setup()
        result = poll(repo, yt)
        (run,) = repo.list_runs()
        assert run.kind == "live" and run.status == "queued"
        assert run.league == "Thursday" and run.format == "fours"
        job = repo.job_for_run(run.id)
        assert job.kind == "live" and job.state == "queued"
        assert result["live"] == [run.id]
        assert "vidL" not in repo.get_playlist("p").last_seen_video_ids

    def test_it_is_not_queued_twice_while_it_plays(self):
        repo, yt = live_setup()
        poll(repo, yt)
        poll(repo, yt, later(THU_7PM, 3))
        assert len(repo.list_runs()) == 1

    def test_it_does_not_wait_for_approval(self):
        repo, yt = live_setup()
        poll(repo, yt, initial_status="pending_approval")
        (run,) = repo.list_runs()
        assert run.status == "queued"

    def test_an_upcoming_stream_waits(self):
        repo, yt = live_setup("upcoming")
        poll(repo, yt)
        assert repo.list_runs() == []
        assert "vidL" not in repo.get_playlist("p").last_seen_video_ids

    def test_one_from_another_channel_is_skipped_and_remembered(self):
        repo, yt = live_setup()
        yt.add(VideoMeta("vidL", "x", "UCstranger", 0.0, "live", THU_7PM))
        result = poll(repo, yt)
        assert repo.list_runs() == []
        assert ("vidL", "channel_not_allowed") in result["skipped"]
        assert "vidL" in repo.get_playlist("p").last_seen_video_ids

    def test_without_live_on_it_waits_for_the_recording_as_before(self):
        repo, yt = live_setup()
        poll(repo, yt, live=False)
        assert repo.list_runs() == []


class TestOnceTheRecordingIsArchived:
    def archive(self, yt):
        yt.add(VideoMeta("vidL", "10/1 - Sheet 3 - Thursday League", "UCclub",
                         9000.0, "none", THU_7PM))

    def test_a_live_run_that_played_stands_and_the_video_is_seen(self):
        repo, yt = live_setup()
        poll(repo, yt)
        (run,) = repo.list_runs()
        repo.update_run(run.id, status="ready")
        self.archive(yt)
        result = poll(repo, yt, later(THU_7PM, 180))
        assert result["created"] == [] and len(repo.list_runs()) == 1
        assert "vidL" in repo.get_playlist("p").last_seen_video_ids

    def test_a_failed_live_run_leaves_it_to_the_recording(self):
        repo, yt = live_setup()
        poll(repo, yt)
        (run,) = repo.list_runs()
        repo.update_run(run.id, status="failed")
        self.archive(yt)
        result = poll(repo, yt, later(THU_7PM, 180))
        assert len(result["created"]) == 1
        vod = repo.get_run(result["created"][0])
        assert vod.kind == "vod" and repo.job_for_run(vod.id).kind == "vod"

    def test_a_live_run_nobody_picked_up_gives_way_to_the_recording(self):
        repo, yt = live_setup()
        poll(repo, yt)
        (live_run,) = repo.list_runs()
        self.archive(yt)
        result = poll(repo, yt, later(THU_7PM, 180))
        assert repo.get_run(live_run.id).status == "failed"
        assert repo.job_for_run(live_run.id).state == "failed"
        assert len(result["created"]) == 1
