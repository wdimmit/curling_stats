"""Clips of the rocks a person says they threw: the clips worker's routes,
and the player's (service/api.py, service/gameclips.py)."""

from datetime import timedelta

from curling_score.service.records import ClipJob, play_id
from tests.test_plays import a_played_game, get, put  # noqa: F401
from tests.test_service_api import T0, VID, WORKER
from tests.test_service_auth import ALEX, SARAH, w  # noqa: F401  (fixtures)

W = "ryzen-clips"


def a_clip_job(w, src, rocks=("0.1.13", "0.1.15"), state="queued"):  # noqa: F811
    run = w["repo"].get_run(w["repo"].get_source(src).current_run_id)
    job = ClipJob(id="cj_1", play_id="p1", user_id="uid-sarah", source_id=src, run_id=run.id,
                  video_id=run.video_id, state=state, created_at=T0, run_after=T0,
                  spec={"calibration": {}, "rocks": [{"name": n, "house": "top"} for n in rocks]})
    w["repo"].put_clip_job(job)
    return job, run


def wpost(w, path, body):  # noqa: F811
    return w["client"].post(path, json={"worker_id": W, **body}, headers=WORKER)


def claim(w):  # noqa: F811
    return w["client"].post("/api/worker/clips/claim", json={"worker_id": W}, headers=WORKER)


class TestTheClipsWorkersRoutes:
    def test_needs_the_worker_token(self, w):  # noqa: F811
        assert w["client"].post("/api/worker/clips/claim", json={}).status_code in (401, 403)

    def test_nothing_queued(self, w):  # noqa: F811
        assert claim(w).status_code == 204

    def test_a_claim_hands_over_the_spec_and_leaves_the_run_alone(self, w):  # noqa: F811
        _, _, src = a_played_game(w)
        job, run = a_clip_job(w, src)
        before = (run.status, run.ready_at, run.error)
        r = claim(w)
        assert r.status_code == 200, r.text
        got = r.json()["job"]
        assert (got["id"], got["run_id"], got["video_id"], got["attempt"]) == \
            ("cj_1", run.id, VID, 1)
        assert [x["name"] for x in got["spec"]["rocks"]] == ["0.1.13", "0.1.15"]
        run = w["repo"].get_run(run.id)
        assert (run.status, run.ready_at, run.error) == before
        assert w["repo"].job_for_run(run.id).kind == "vod"

    def test_the_claim_leaves_a_live_run_alone(self, w):  # noqa: F811
        _, _, src = a_played_game(w)
        _, run = a_clip_job(w, src)
        w["repo"].update_run(run.id, status="live")
        assert claim(w).status_code == 204
        assert w["repo"].get_clip_job("cj_1").state == "queued"
        assert w["repo"].get_run(run.id).status == "live"

    def test_uploads_only_the_rocks_it_asked_for(self, w):  # noqa: F811
        _, _, src = a_played_game(w)
        _, run = a_clip_job(w, src)
        claim(w)
        r = wpost(w, "/api/worker/clips/cj_1/artifacts",
                  {"files": [{"name": "0.1.13.mp4", "bytes": 100}]})
        assert r.status_code == 200, r.text
        (up,) = r.json()["uploads"]
        assert up["key"] == f"runs/{VID}/{run.id}/clips/0.1.13.mp4"
        assert wpost(w, "/api/worker/clips/cj_1/artifacts",
                     {"files": [{"name": "0.1.14.mp4", "bytes": 100}]}).status_code == 400
        assert wpost(w, "/api/worker/clips/cj_1/artifacts",
                     {"files": [{"name": "0.1.13.mp4", "bytes": 30_000_000}]}).status_code == 400
        put_r = w["client"].put("/api/worker/clips/cj_1/upload", params={"key": up["key"]},
                                content=b"mp4", headers=WORKER)
        assert put_r.status_code == 200, put_r.text
        assert w["store"].get_bytes(up["key"]) == b"mp4"
        assert w["client"].put("/api/worker/clips/cj_1/upload",
                               params={"key": f"runs/{VID}/{run.id}/timeline.json"},
                               content=b"{}", headers=WORKER).status_code == 400

    def test_complete_keeps_the_results(self, w):  # noqa: F811
        _, _, src = a_played_game(w)
        _, run = a_clip_job(w, src)
        claim(w)
        r = wpost(w, "/api/worker/clips/cj_1/complete", {"rocks": {
            "0.1.13": {"clip": True, "overlay": [{"t0": 0.0, "t1": 3.0}]},
            "0.1.15": {"clip": None, "reason": "no_release"}}})
        assert r.status_code == 200, r.text
        job = w["repo"].get_clip_job("cj_1")
        assert job.state == "done" and job.finished_at is not None
        assert job.rocks == {"0.1.13": {"clip": True, "overlay": [{"t0": 0.0, "t1": 3.0}]},
                             "0.1.15": {"clip": None, "reason": "no_release"}}
        assert w["repo"].get_run(run.id).status == "ready"
        assert wpost(w, "/api/worker/clips/cj_1/complete", {"rocks": {}}).json()["already"]

    def test_progress_renews_the_lease(self, w):  # noqa: F811
        _, _, src = a_played_game(w)
        a_clip_job(w, src)
        claim(w)
        w["clock"].advance(500)
        assert wpost(w, "/api/worker/clips/cj_1/progress", {}).status_code == 200
        assert w["repo"].get_clip_job("cj_1").lease_expires_at == \
            w["clock"]() + timedelta(seconds=w["settings"].lease_s)

    def test_a_lapsed_lease_is_requeued_and_the_late_complete_refused(self, w):  # noqa: F811
        _, _, src = a_played_game(w)
        a_clip_job(w, src)
        claim(w)
        w["clock"].advance(w["settings"].lease_s + 1)
        r = w["client"].post("/api/worker/clips/claim", json={"worker_id": "other"},
                             headers=WORKER)
        assert r.json()["job"]["attempt"] == 2
        assert wpost(w, "/api/worker/clips/cj_1/complete", {"rocks": {}}).status_code == 409
        assert wpost(w, "/api/worker/clips/cj_1/progress", {}).status_code == 409

    def test_a_failure_goes_back_then_gives_up(self, w):  # noqa: F811
        _, _, src = a_played_game(w)
        _, run = a_clip_job(w, src)
        claim(w)
        r = wpost(w, "/api/worker/clips/cj_1/fail", {"error": "ffmpeg died"})
        assert r.json() == {"ok": True, "final": False}
        assert w["repo"].get_clip_job("cj_1").state == "queued"
        w["repo"].update_clip_job("cj_1", attempts=2)
        w["clock"].advance(61)
        claim(w)
        assert wpost(w, "/api/worker/clips/cj_1/fail", {"error": "again"}).json()["final"]
        assert w["repo"].get_clip_job("cj_1").state == "failed"
        assert w["repo"].get_run(run.id).status == "ready"
