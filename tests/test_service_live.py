"""The service's side of live games: live work first, a recording that steps
aside for it, and a game published one end at a time while its stream plays."""

import json
from datetime import timedelta

import pytest

from curling_score.service.records import Job, Run
from tests.test_service_api import (  # noqa: F401 - world is a fixture
    CLUB, T0, VID, WORKER, sample_doc, submit, world,
)

LIVE = "liveVid0001"


def add_live_run(w):
    w["repo"].put_run(Run(id="r_live", video_id=LIVE, status="queued", created_at=T0,
                          processing_version=w["settings"].processing_version,
                          kind="live", title="Tonight - Sheet 3", format="fours"))
    w["repo"].put_job(Job(id="j_live", run_id="r_live", state="queued", created_at=T0,
                          run_after=T0, kind="live"))


def claim(w, kinds=None):
    body = {"worker_id": "home", "model_id": "m-abc"}
    if kinds is not None:
        body["kinds"] = kinds
    return w["client"].post("/api/worker/claim", json=body, headers=WORKER)


def one_game(ends, start=600.0):
    doc = sample_doc(games=1)
    game = doc["games"][0]
    game["start_s"] = start
    first = game["ends"][0]
    game["ends"] = [dict(first, number=n, start_s=start + 900.0 * (n - 1),
                         end_s=start + 900.0 * n) for n in range(1, ends + 1)]
    game["end_s"] = game["ends"][-1]["end_s"]
    doc["source"]["video_id"] = LIVE
    doc["live"] = {"in_progress": True, "recorded_s": game["end_s"] + 300.0}
    return doc


def upload(w, job_id, doc):
    plan = w["client"].post(f"/api/worker/jobs/{job_id}/artifacts", headers=WORKER,
                            json={"worker_id": "home",
                                  "files": [{"name": "timeline.json", "bytes": 1}]}).json()
    (up,) = plan["uploads"]
    w["store"].put_bytes(up["key"], json.dumps(doc).encode())


def publish(w, job_id, doc):
    games = [{"index": g["index"], "start_s": g["start_s"], "end_s": g["end_s"],
              "ends": len(g["ends"])} for g in doc["games"]]
    return w["client"].post(f"/api/worker/jobs/{job_id}/publish", headers=WORKER,
                            json={"worker_id": "home", "games": games,
                                  "title": "Tonight - Sheet 3", "sheet": 3,
                                  "duration_s": doc["live"]["recorded_s"]})


class TestLiveWorkComesFirst:
    def test_a_worker_that_asks_for_live_work_gets_it_ahead_of_recordings(self, world):
        assert submit(world).status_code == 201
        add_live_run(world)
        job = claim(world, ["live", "vod"]).json()["job"]
        assert (job["video_id"], job["kind"]) == (LIVE, "live")
        assert claim(world, ["live", "vod"]).json()["job"]["video_id"] == VID

    def test_a_worker_that_does_not_say_gets_no_live_work(self, world):
        add_live_run(world)
        assert claim(world).status_code == 204


class TestAYieldedRecordingIsNotAFailure:
    def test_it_goes_back_to_the_queue_without_spending_an_attempt(self, world):
        submit(world)
        job = claim(world).json()["job"]
        r = world["client"].post(f"/api/worker/jobs/{job['id']}/yield", headers=WORKER,
                                 json={"worker_id": "home"})
        assert r.status_code == 200
        back = world["repo"].get_job(job["id"])
        assert back.state == "queued" and back.attempts == 0 and back.worker_id is None
        assert "live" in back.message
        assert world["repo"].get_run(back.run_id).status == "queued"
        assert claim(world).json()["job"]["attempt"] == 1

    def test_a_job_that_is_not_yours_cannot_be_yielded(self, world):
        submit(world)
        job = claim(world).json()["job"]
        r = world["client"].post(f"/api/worker/jobs/{job['id']}/yield", headers=WORKER,
                                 json={"worker_id": "someone-else"})
        assert r.status_code == 409


class TestPublishingOneEndAtATime:
    def test_each_publish_is_what_the_game_page_serves(self, world):
        add_live_run(world)
        job = claim(world, ["live"]).json()["job"]
        doc = one_game(1)
        upload(world, job["id"], doc)
        assert publish(world, job["id"], doc).status_code == 200
        run = world["repo"].get_run("r_live")
        assert run.status == "live" and run.revised_at is not None
        (src,) = world["repo"].sources_for_video(LIVE)
        c = world["client"]
        assert c.get(f"/g/{src.id}/").status_code == 200
        served = c.get(f"/g/{src.id}/timeline.json").json()
        assert len(served["games"][0]["ends"]) == 1

        world["clock"].advance(900)
        doc = one_game(2)
        upload(world, job["id"], doc)
        assert publish(world, job["id"], doc).status_code == 200
        assert len(c.get(f"/g/{src.id}/timeline.json").json()["games"][0]["ends"]) == 2
        (again,) = world["repo"].sources_for_video(LIVE)
        assert again.id == src.id and again.game_end_s == doc["games"][0]["end_s"]

    def test_the_api_keeps_one_copy_of_a_live_game_s_timeline(self, world):
        # Every end is a new version of the same key; keeping each one is
        # what ran the instance out of memory on league nights (doccache).
        add_live_run(world)
        job = claim(world, ["live"]).json()["job"]
        c = world["client"]
        for ends in (1, 2, 3):
            world["clock"].advance(900)
            doc = one_game(ends)
            upload(world, job["id"], doc)
            assert publish(world, job["id"], doc).status_code == 200
            (src,) = world["repo"].sources_for_video(LIVE)
            assert len(c.get(f"/g/{src.id}/timeline.json").json()["games"][0]["ends"]) == ends
        assert len(c.app.state.docs) == 1

    def test_publishing_keeps_the_job_leased(self, world):
        add_live_run(world)
        job = claim(world, ["live"]).json()["job"]
        world["clock"].advance(500)
        doc = one_game(1)
        upload(world, job["id"], doc)
        publish(world, job["id"], doc)
        leased = world["repo"].get_job(job["id"]).lease_expires_at
        assert leased > world["clock"]()

    def test_nothing_can_be_published_before_it_is_uploaded(self, world):
        add_live_run(world)
        job = claim(world, ["live"]).json()["job"]
        assert publish(world, job["id"], one_game(1)).status_code == 409

    def test_a_recording_cannot_publish_partial_results(self, world):
        submit(world)
        job = claim(world).json()["job"]
        doc = sample_doc(games=1)
        doc["live"] = {"in_progress": True, "recorded_s": 1.0}
        upload(world, job["id"], doc)
        assert publish(world, job["id"], doc).status_code == 400

    def test_the_catalogue_lists_a_live_game_as_live(self, world):
        add_live_run(world)
        job = claim(world, ["live"]).json()["job"]
        doc = one_game(3)
        upload(world, job["id"], doc)
        publish(world, job["id"], doc)
        (game,) = [g for g in world["client"].get("/api/games").json()["games"]
                   if g["video_id"] == LIVE]
        assert game["status"] == "live" and game["ends"] == 3
        assert game["source_id"] is not None

    def test_completing_it_makes_the_same_game_final(self, world):
        add_live_run(world)
        job = claim(world, ["live"]).json()["job"]
        doc = one_game(2)
        upload(world, job["id"], doc)
        publish(world, job["id"], doc)
        (src,) = world["repo"].sources_for_video(LIVE)
        doc["live"]["in_progress"] = False
        upload(world, job["id"], doc)
        games = [{"index": 0, "start_s": 600.0, "end_s": doc["games"][0]["end_s"], "ends": 2}]
        r = world["client"].post(f"/api/worker/jobs/{job['id']}/complete", headers=WORKER,
                                 json={"worker_id": "home", "games": games, "format": "fours",
                                       "title": "Tonight - Sheet 3", "channel_id": CLUB})
        assert r.status_code == 200, r.text
        assert world["repo"].get_run("r_live").status == "ready"
        (after,) = world["repo"].sources_for_video(LIVE)
        assert after.id == src.id
        served = world["client"].get(f"/g/{src.id}/timeline.json").json()
        assert served["live"]["in_progress"] is False


class TestAGameWhoseLiveCoverageStopped:
    def test_its_last_published_ends_stay_up_and_say_it_stopped(self, world):
        add_live_run(world)
        job = claim(world, ["live"]).json()["job"]
        doc = one_game(3)
        upload(world, job["id"], doc)
        publish(world, job["id"], doc)
        (src,) = world["repo"].sources_for_video(LIVE)
        world["client"].post(f"/api/worker/jobs/{job['id']}/fail", headers=WORKER,
                             json={"worker_id": "home", "error": "gone", "kind": "permanent"})
        r = world["client"].get(f"/g/{src.id}/timeline.json")
        assert r.status_code == 200
        served = r.json()
        assert len(served["games"][0]["ends"]) == 3
        assert served["live"]["in_progress"] is False and served["live"]["stopped"] is True


class TestAPausedRecordingSaysSo:
    def test_the_status_page_knows_it_stepped_aside_for_live_games(self, world):
        slug = submit(world).json()["slug"]
        job = claim(world).json()["job"]
        world["client"].post(f"/api/worker/jobs/{job['id']}/yield", headers=WORKER,
                             json={"worker_id": "home"})
        status = world["client"].get(f"/c/{slug}/status.json").json()
        assert status["status"] == "queued" and status["paused"] is True
        claim(world)
        assert world["client"].get(f"/c/{slug}/status.json").json().get("paused") is False


# --- camera games ------------------------------------------------------------------

def add_camera_run(w, kind="camera_live", run_id="r_cam", job_id="j_cam", **run_kw):
    w["repo"].put_run(Run(id=run_id, video_id=LIVE, status="queued", created_at=T0,
                          processing_version=w["settings"].processing_version,
                          kind="live" if kind == "camera_live" else "vod", source="cameras",
                          title="Tonight - Sheet 3", format="fours", sheet=3,
                          published_at=T0, **run_kw))
    w["repo"].put_job(Job(id=job_id, run_id=run_id, state="queued", created_at=T0,
                          run_after=T0, kind=kind))


def camera_claim(w, kinds, cameras=True):
    body = {"worker_id": "home", "model_id": "m-abc", "kinds": kinds}
    if cameras:
        body["cameras"] = True
    return w["client"].post("/api/worker/claim", json=body, headers=WORKER)


class TestCameraGames:
    def test_a_worker_without_cameras_never_gets_camera_work(self, world):
        add_camera_run(world)
        add_camera_run(world, kind="camera_vod", run_id="r_cv", job_id="j_cv")
        add_live_run(world)
        kinds = ["live", "camera_live", "camera_vod", "vod"]
        assert camera_claim(world, kinds, cameras=False).json()["job"]["id"] == "j_live"
        assert camera_claim(world, kinds, cameras=False).status_code == 204

    def test_a_camera_game_is_claimed_before_a_recording(self, world):
        assert submit(world).status_code == 201
        add_camera_run(world)
        job = camera_claim(world, ["live", "camera_live", "vod"]).json()["job"]
        assert job["id"] == "j_cam"

    def test_its_claim_says_cameras_and_when_the_stream_began(self, world):
        add_camera_run(world)
        job = camera_claim(world, ["live", "camera_live"]).json()["job"]
        assert (job["kind"], job["source"], job["sheet"]) == ("camera_live", "cameras", 3)
        assert job["published_at"] == T0.isoformat()

    def test_its_heartbeat_says_stop_once_the_stream_has_ended(self, world):
        add_camera_run(world)
        camera_claim(world, ["live", "camera_live"])
        beat = lambda: world["client"].post(  # noqa: E731
            "/api/worker/jobs/j_cam/progress", headers=WORKER,
            json={"worker_id": "home", "phase": "live", "fraction": None,
                  "message": "recorded 10 min"}).json()
        assert "stop" not in beat()
        world["repo"].update_run("r_cam", stream_ended_at=T0)
        assert beat()["stop"] is True


class TestAStandby:
    def test_its_claim_says_cameras_and_its_sheet(self, world):
        add_camera_run(world, kind="camera_standby", run_id="r_sb", job_id="j_sb")
        world["repo"].update_run("r_sb", kind="standby", video_id="standby-3-x",
                                 published_at=None)
        job = camera_claim(world, ["live", "camera_live", "camera_standby"]).json()["job"]
        assert (job["id"], job["kind"], job["source"], job["sheet"]) == \
            ("j_sb", "camera_standby", "cameras", 3)

    def test_a_worker_without_cameras_never_gets_one(self, world):
        add_camera_run(world, kind="camera_standby", run_id="r_sb", job_id="j_sb")
        assert camera_claim(world, ["live", "camera_standby"], cameras=False).status_code == 204

    def beat(self, world, job_id):
        return world["client"].post(
            f"/api/worker/jobs/{job_id}/progress", headers=WORKER,
            json={"worker_id": "home", "phase": "live", "fraction": None,
                  "message": "recorded 10 min"})

    def test_handed_to_its_stream_the_heartbeat_says_which_game(self, world):
        add_camera_run(world, kind="camera_standby", run_id="r_sb", job_id="j_sb")
        world["repo"].update_run("r_sb", kind="standby", video_id="standby-3-x",
                                 published_at=None)
        camera_claim(world, ["camera_standby"])
        assert "game" not in self.beat(world, "j_sb").json()
        world["repo"].update_run("r_sb", kind="live", video_id=LIVE, published_at=T0,
                                 title="Tonight - Sheet 3")
        world["repo"].update_job("j_sb", kind="camera_live")
        game = self.beat(world, "j_sb").json()["game"]
        assert game["video_id"] == LIVE and game["published_at"] == T0.isoformat()
        assert (game["kind"], game["sheet"], game["title"], game["format"]) == \
            ("camera_live", 3, "Tonight - Sheet 3", "fours")

    def test_a_stream_found_and_over_between_two_heartbeats_still_says_which_game(self, world):
        add_camera_run(world, kind="camera_standby", run_id="r_sb", job_id="j_sb")
        world["repo"].update_run("r_sb", kind="standby", video_id="standby-3-x",
                                 published_at=None)
        camera_claim(world, ["camera_standby"])
        world["repo"].update_run("r_sb", kind="live", video_id=LIVE, published_at=T0,
                                 stream_ended_at=T0)
        world["repo"].update_job("j_sb", kind="camera_live")
        got = self.beat(world, "j_sb").json()
        assert got["stop"] is True and got["game"]["video_id"] == LIVE

    def test_given_up_its_heartbeat_is_refused(self, world):
        add_camera_run(world, kind="camera_standby", run_id="r_sb", job_id="j_sb")
        camera_claim(world, ["camera_standby"])
        world["repo"].update_job("j_sb", state="failed")
        assert self.beat(world, "j_sb").status_code == 409


class TestACameraGameEndToEnd:
    def test_it_publishes_and_completes_like_a_live_game(self, world):
        """Review 1: the API refused every publish that was not kind "live"."""
        add_camera_run(world)
        camera_claim(world, ["live", "camera_live"])
        doc = one_game(2)
        upload(world, "j_cam", doc)
        r = publish(world, "j_cam", doc)
        assert r.status_code == 200, r.text
        assert world["repo"].get_run("r_cam").status == "live"

    def test_a_camera_game_claimed_again_goes_to_youtube(self, world):
        """Review 3: a worker restarting mid-game would record its cameras from
        then on, and lose the game's start; the stream has it from segment 0."""
        add_camera_run(world)
        camera_claim(world, ["live", "camera_live"])
        world["repo"].release_jobs("home", T0)
        job = camera_claim(world, ["live", "camera_live"]).json()["job"]
        assert job["kind"] == "live" and "source" not in job
        assert world["repo"].get_run("r_cam").source is None


class TestASheetsGamesInARow:
    """The earlier game on a sheet says where it ended; the next reads from there."""

    def beat(self, world, job_id="j_cam", **extra):
        return world["client"].post(
            f"/api/worker/jobs/{job_id}/progress", headers=WORKER,
            json={"worker_id": "home", "phase": "live", "fraction": None,
                  "message": "recorded 10 min", **extra}).json()

    def test_a_lookahead_game_is_read_from_its_opening(self, world):
        opened = T0 - timedelta(minutes=10)
        add_camera_run(world, opened_at=opened)
        job = camera_claim(world, ["camera_live"]).json()["job"]
        assert job["analysis_from"] == opened.isoformat()
        assert self.beat(world)["game"]["analysis_from"] == opened.isoformat()

    def test_one_with_no_opening_is_read_from_ten_minutes_before_its_stream(self, world):
        add_camera_run(world)
        job = camera_claim(world, ["camera_live"]).json()["job"]
        assert job["analysis_from"] == (T0 - timedelta(minutes=10)).isoformat()

    def test_the_earlier_game_hears_when_the_next_one_opened(self, world):
        add_camera_run(world)
        camera_claim(world, ["camera_live"])
        assert self.beat(world)["game"]["handoff_at"] is None
        world["repo"].update_run("r_cam", handoff_at=T0 + timedelta(hours=2))
        assert self.beat(world)["game"]["handoff_at"] == (T0 + timedelta(hours=2)).isoformat()

    def test_the_later_game_waits_for_the_earlier_ones_word(self, world):
        add_camera_run(world, run_id="r_A", job_id="j_A")
        world["repo"].update_run("r_A", status="live")
        add_camera_run(world, opened_at=T0, prev_run_id="r_A")
        camera_claim(world, ["camera_live"])
        camera_claim(world, ["camera_live"])
        game = self.beat(world)["game"]
        assert game["floor_pending"] is True and game["floor_at"] is None
        until = T0 + timedelta(minutes=7)
        self.beat(world, "j_A", own_until=until.isoformat())
        assert world["repo"].get_run("r_A").own_until == until
        game = self.beat(world)["game"]
        assert game["floor_pending"] is False and game["floor_at"] == until.isoformat()

    def test_a_first_game_on_its_sheet_reads_from_its_opening(self, world):
        add_camera_run(world, opened_at=T0)
        camera_claim(world, ["camera_live"])
        game = self.beat(world)["game"]
        assert (game["floor_pending"], game["floor_at"]) == (False, T0.isoformat())

    @pytest.mark.parametrize("how", ["failed", "youtube", "too long"])
    def test_a_dead_earlier_game_lets_the_later_one_go_in_the_end(self, world, how):
        add_camera_run(world, run_id="r_A", job_id="j_A")
        world["repo"].update_run("r_A", status="live")
        add_camera_run(world, opened_at=T0, prev_run_id="r_A")
        camera_claim(world, ["camera_live"])
        camera_claim(world, ["camera_live"])
        if how == "failed":
            world["repo"].update_run("r_A", status="failed")
        elif how == "youtube":
            world["repo"].update_run("r_A", source=None)
        else:
            world["clock"].advance(45 * 60 + 1)
        game = self.beat(world)["game"]
        assert (game["floor_pending"], game["floor_at"]) == (False, T0.isoformat())
