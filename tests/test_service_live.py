"""The service's side of live games: live work first, a recording that steps
aside for it, and a game published one end at a time while its stream plays."""

import json

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
