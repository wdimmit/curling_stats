"""A `cameras` playlist records each sheet from its schedule's start (a
standby), and the sheet's stream, once the poll finds it, takes that recording
over: nothing between the stream going up and the poll finding it is lost."""

from datetime import timedelta

from curling_score.service import playlists, practice
from curling_score.service.records import Job, Run
from curling_score.service.repo import MemoryRepo
from curling_score.service.youtube import FakeYouTube, VideoMeta
from tests.test_playlist_live import ALL_SHEETS, NIGHT, THU_7PM, TZ, later, watched

# Thursday 1 October 2026, 18:00 in Seattle: the window opens.
THU_6PM = later(THU_7PM, -60)


def setup(input="cameras", schedule=NIGHT):
    repo = MemoryRepo()
    repo.put_playlist(watched(schedule=schedule, input=input))
    return repo


def standbys(repo, t=THU_6PM, sheets=ALL_SHEETS, cap=5):
    return playlists.standbys(repo, processing_version="p+m", now=t, tz=TZ,
                              camera_sheets=sheets, camera_max_sheets=cap)


def waiting(repo):
    return sorted((r for r in repo.list_runs() if r.kind == "standby"
                   and r.status in ("queued", "processing")), key=lambda r: r.sheet)


def stream(yt, vid, sheet, t=THU_7PM):
    yt._playlists.setdefault("PL1", []).append(vid)
    yt.add(VideoMeta(vid, f"10/1 - Sheet {sheet} - Thursday", "UCclub", 0.0, "live", t))


def poll(repo, yt, t=THU_7PM, sheets=ALL_SHEETS, cap=5):
    return playlists.poll(repo, yt, processing_version="p+m", now=t, tz=TZ,
                          allowed_channels={"UCclub"}, camera_sheets=sheets,
                          camera_max_sheets=cap)


class TestOpening:
    def test_the_window_opening_queues_a_standby_for_each_camera_sheet(self):
        repo = setup()
        got = standbys(repo)
        runs = waiting(repo)
        assert [r.sheet for r in runs] == [1, 2, 3, 4, 5]
        assert sorted(got["opened"]) == sorted(r.id for r in runs)
        for r in runs:
            assert (r.source, r.playlist_id, r.league) == ("cameras", "PL1", "Thursday")
            assert repo.job_for_run(r.id).kind == "camera_standby"
            assert repo.job_for_run(r.id).state == "queued"

    def test_only_once_a_window(self):
        repo = setup()
        standbys(repo)
        assert standbys(repo, later(THU_6PM, 3))["opened"] == []
        assert len(waiting(repo)) == 5

    def test_and_again_the_next_week(self):
        repo = setup()
        standbys(repo)
        for r in waiting(repo):
            repo.update_run(r.id, status="failed")
        assert len(standbys(repo, THU_6PM + timedelta(days=7))["opened"]) == 5

    def test_only_on_camera_sheets_up_to_the_cap(self):
        repo = setup()
        standbys(repo, sheets={2, 3, 4}, cap=2)
        assert [r.sheet for r in waiting(repo)] == [2, 3]

    def test_a_sheet_left_out_by_the_cap_opens_when_there_is_room(self):
        repo = setup()
        standbys(repo, sheets={2, 3}, cap=1)
        (first,) = waiting(repo)
        repo.update_run(first.id, status="failed")
        standbys(repo, later(THU_6PM, 3), sheets={2, 3}, cap=1)
        assert [r.sheet for r in waiting(repo)] == [3]

    def test_none_for_a_youtube_playlist(self):
        repo = setup(input="youtube")
        assert standbys(repo)["opened"] == [] and waiting(repo) == []

    def test_none_outside_the_window(self):
        repo = setup()
        assert standbys(repo, later(THU_6PM, -5))["opened"] == []

    def test_none_once_the_window_is_well_under_way(self):
        """By then the poll has found the night's streams, if there are any."""
        repo = setup()
        assert standbys(repo, later(THU_6PM, 95))["opened"] == []

    def test_none_for_a_disabled_playlist(self):
        repo = setup()
        repo.update_playlist("p", enabled=False)
        assert standbys(repo)["opened"] == []

    def test_a_sheet_still_reading_its_cameras_waits(self):
        repo = setup()
        repo.put_run(Run(id="r_pr", video_id="cam-s2", processing_version="p", status="processing",
                         created_at=later(THU_6PM, -60), kind="practice", source="cameras",
                         sheet=2))
        standbys(repo)
        assert [r.sheet for r in waiting(repo)] == [1, 3, 4, 5]
        repo.update_run("r_pr", status="ready")
        standbys(repo, later(THU_6PM, 3))
        assert [r.sheet for r in waiting(repo)] == [1, 2, 3, 4, 5]

    def test_a_sheet_whose_stream_was_already_found_gets_none(self):
        repo = setup()
        yt = FakeYouTube(playlists={"PL1": []})
        stream(yt, "vid3", 3, THU_6PM)
        poll(repo, yt, THU_6PM)
        standbys(repo, later(THU_6PM, 1))
        assert [r.sheet for r in waiting(repo)] == [1, 2, 4, 5]

    def test_a_league_waiting_on_its_streams_is_a_league_on(self):
        repo = setup()
        standbys(repo)
        assert practice.league_on(repo, later(THU_6PM, 1))


class TestTheStreamTakesOver:
    def test_its_sheets_standby_becomes_the_game(self):
        repo = setup()
        standbys(repo)
        three = next(r for r in waiting(repo) if r.sheet == 3)
        job = repo.claim_job("w1", later(THU_6PM, 1), kinds=("camera_standby",))
        while job.run_id != three.id:
            job = repo.claim_job("w1", later(THU_6PM, 1), kinds=("camera_standby",))
        yt = FakeYouTube(playlists={"PL1": []})
        stream(yt, "vid3", 3)
        got = poll(repo, yt)
        assert got["live"] == [three.id]
        run = repo.get_run(three.id)
        assert (run.kind, run.source, run.video_id) == ("live", "cameras", "vid3")
        assert run.title == "10/1 - Sheet 3 - Thursday" and run.published_at == THU_7PM
        assert run.status == "processing"
        job = repo.job_for_run(three.id)
        assert (job.kind, job.state, job.worker_id) == ("camera_live", "running", "w1")
        assert [r.id for r in repo.runs_for_video("vid3")] == [three.id]

    def test_an_unclaimed_standby_is_handed_over_queued(self):
        repo = setup()
        standbys(repo)
        three = next(r for r in waiting(repo) if r.sheet == 3)
        yt = FakeYouTube(playlists={"PL1": []})
        stream(yt, "vid3", 3)
        poll(repo, yt)
        job = repo.job_for_run(three.id)
        assert (job.kind, job.state) == ("camera_live", "queued")

    def test_without_a_standby_the_stream_is_queued_as_before(self):
        repo = setup()
        yt = FakeYouTube(playlists={"PL1": []})
        stream(yt, "vid3", 3)
        poll(repo, yt)
        (run,) = repo.list_runs()
        assert repo.job_for_run(run.id).kind == "camera_live"

    def test_a_standby_holds_its_place_under_the_cap(self):
        """Five standbys under a cap of five: the stream still takes its own."""
        repo = setup()
        standbys(repo, cap=5)
        yt = FakeYouTube(playlists={"PL1": []})
        stream(yt, "vid5", 5)
        poll(repo, yt, cap=5)
        run = next(r for r in repo.list_runs() if r.video_id == "vid5")
        assert run.sheet == 5 and run.source == "cameras" and run.kind == "live"


class TestGivingUp:
    def claimed(self, repo):
        standbys(repo)
        for _ in range(5):
            repo.claim_job("w1", later(THU_6PM, 1), kinds=("camera_standby",))

    def test_a_standby_without_a_stream_ends_after_90_minutes(self):
        repo = setup()
        self.claimed(repo)
        assert standbys(repo, later(THU_6PM, 89))["closed"] == []
        got = standbys(repo, later(THU_6PM, 90))
        assert len(got["closed"]) == 5 and waiting(repo) == []
        for rid in got["closed"]:
            run, job = repo.get_run(rid), repo.job_for_run(rid)
            assert run.status == "failed" and "no stream" in run.error
            assert job.state == "failed" and job.error_kind == "permanent"

    def test_a_handed_over_one_is_left_alone(self):
        repo = setup()
        self.claimed(repo)
        yt = FakeYouTube(playlists={"PL1": []})
        stream(yt, "vid3", 3)
        poll(repo, yt)
        got = standbys(repo, later(THU_6PM, 120))
        game = repo.runs_for_video("vid3")[0]
        assert game.id not in got["closed"] and repo.job_for_run(game.id).state == "running"

    def test_one_whose_playlist_stops_reading_the_cameras_ends(self):
        repo = setup()
        self.claimed(repo)
        repo.update_playlist("p", input="youtube")
        assert len(standbys(repo, later(THU_6PM, 5))["closed"]) == 5


class TestBackToBackLeagues:
    """Two leagues on the same sheets, the second's streams going up as the
    first's come down (Tuesday: Supper until 19:00, Super from 19:00)."""

    def first_league(self, repo, ended_at=None, sheets=(1, 2, 3, 4, 5)):
        for s in sheets:
            repo.put_run(Run(id=f"r_sup{s}", video_id=f"sup{s}", processing_version="p",
                             status="processing", created_at=later(THU_7PM, -120), kind="live",
                             source="cameras", sheet=s, playlist_id="PL0", league="Supper",
                             stream_ended_at=ended_at))

    def game(self, repo, vid):
        runs = repo.runs_for_video(vid)
        return runs[0] if runs else None

    def test_a_game_whose_stream_ended_does_not_hold_a_place_under_the_cap(self):
        repo = setup()
        self.first_league(repo, ended_at=later(THU_7PM, -5))
        yt = FakeYouTube(playlists={"PL1": []})
        stream(yt, "vid3", 3)
        poll(repo, yt, cap=5)
        assert self.game(repo, "vid3").source == "cameras"

    def test_a_stream_waits_while_its_sheet_still_records_the_last_game(self):
        repo = setup()
        self.first_league(repo)
        yt = FakeYouTube(playlists={"PL1": []})
        stream(yt, "vid3", 3)
        poll(repo, yt, cap=5)
        assert self.game(repo, "vid3") is None
        assert "vid3" not in repo.get_playlist("p").last_seen_video_ids

    def test_and_waits_a_moment_more_while_the_worker_lets_the_cameras_go(self):
        repo = setup()
        self.first_league(repo, ended_at=later(THU_7PM, 2))
        yt = FakeYouTube(playlists={"PL1": []})
        stream(yt, "vid3", 3)
        poll(repo, yt, later(THU_7PM, 2.5), cap=5)
        assert self.game(repo, "vid3") is None
        poll(repo, yt, later(THU_7PM, 5.5), cap=5)
        assert self.game(repo, "vid3").source == "cameras"

    def test_a_sheet_that_never_comes_free_goes_to_youtube_after_15_minutes(self):
        repo = setup()
        self.first_league(repo)
        yt = FakeYouTube(playlists={"PL1": []})
        stream(yt, "vid3", 3)
        poll(repo, yt, later(THU_7PM, 13), cap=5)
        assert self.game(repo, "vid3") is None
        poll(repo, yt, later(THU_7PM, 16), cap=5)
        run = self.game(repo, "vid3")
        assert run.source is None and repo.job_for_run(run.id).kind == "live"
