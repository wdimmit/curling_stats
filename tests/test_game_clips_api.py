"""Clips of the rocks a person says they threw: the clips worker's routes,
and the player's (service/api.py, service/gameclips.py)."""

from datetime import timedelta

from curling_score.service import gameclips
from curling_score.service.records import ClipJob, play_id
from curling_score.service.store import timeline_key
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


from tests.test_service_api import ADMIN, work_through  # noqa: E402
from tests.test_plays import played_doc  # noqa: E402

G = "/g/{}/"


def claimed(w, slot=4, color="red"):  # noqa: F811
    _, _, src = a_played_game(w)
    w["settings"].game_clips_enabled = True
    r = put(w, src, {"path": G.format(src), "color": color, "slot": slot})
    assert r.status_code == 200, r.text
    return src


def jobs(w, src):  # noqa: F811
    return w["repo"].clip_jobs_for_play(play_id("uid-sarah", src))


def worker_finishes(w, results):  # noqa: F811
    job = claim(w).json()["job"]
    assert wpost(w, f"/api/worker/clips/{job['id']}/complete",
                 {"rocks": results}).status_code == 200
    return job


class TestAskingForClips:
    def test_off_nothing_is_asked_and_nothing_shown(self, w):  # noqa: F811
        _, _, src = a_played_game(w)
        put(w, src, {"path": G.format(src), "color": "red", "slot": 4})
        assert jobs(w, src) == []
        body = get(w, f"/api/me/plays/{src}/doc").json()
        assert "clips" not in body
        assert all("rock" not in s for s in body["doc"]["games"][0]["ends"][0]["shots"])
        assert get(w, f"/api/me/plays/{src}/clips").status_code == 404
        assert get(w, f"/api/me/plays/{src}/clips/0.1.13").status_code == 404

    def test_a_save_stands_when_the_document_cannot_be_read(self, w):  # noqa: F811
        _, _, src = a_played_game(w)
        w["settings"].game_clips_enabled = True
        run_id = w["repo"].get_source(src).current_run_id
        # Another key, so the cached document is not the one asked for.
        w["repo"].update_run(run_id, timeline_key=timeline_key(VID, "missing"))
        r = put(w, src, {"path": G.format(src), "color": "red", "slot": 4})
        assert r.status_code == 200, r.text
        assert w["repo"].get_play("uid-sarah", src) is not None
        assert jobs(w, src) == []

    def test_saving_a_play_asks_for_its_rocks(self, w):  # noqa: F811
        src = claimed(w)
        (job,) = jobs(w, src)
        assert [r["name"] for r in job.spec["rocks"]] == ["0.1.13", "0.1.15"]
        assert job.run_id == w["repo"].get_source(src).current_run_id

    def test_saving_the_same_rocks_again_queues_nothing(self, w):  # noqa: F811
        src = claimed(w)
        worker_finishes(w, {"0.1.13": {"clip": True}, "0.1.15": {"clip": True}})
        put(w, src, {"path": G.format(src), "color": "red", "slot": 4})
        assert len(jobs(w, src)) == 1
        assert get(w, f"/api/me/plays/{src}/clips").json()["clips"]["pending"] is False

    def test_other_rocks_are_asked_for_and_the_old_clips_kept(self, w):  # noqa: F811
        src = claimed(w)
        worker_finishes(w, {"0.1.13": {"clip": True}, "0.1.15": {"clip": True}})
        put(w, src, {"color": "yellow", "slot": 4})
        assert [r["name"] for r in jobs(w, src)[-1].spec["rocks"]] == ["0.1.14", "0.1.16"]
        put(w, src, {"color": "red", "slot": 4})
        assert len(jobs(w, src)) == 2

    def test_the_report_marks_the_rocks_and_says_where_they_stand(self, w):  # noqa: F811
        src = claimed(w)
        body = get(w, f"/api/me/plays/{src}/doc").json()
        shots = body["doc"]["games"][0]["ends"][0]["shots"]
        assert [s.get("rock") for s in shots if s.get("rock")] == ["0.1.13", "0.1.15"]
        assert body["clips"] == {"rocks": {"0.1.13": {"clip": False}, "0.1.15": {"clip": False}},
                                 "pending": True}

    def test_reading_again_queues_nothing(self, w):  # noqa: F811
        src = claimed(w)
        get(w, f"/api/me/plays/{src}/doc")
        get(w, f"/api/me/plays/{src}/doc")
        assert len(jobs(w, src)) == 1

    def test_a_play_from_before_clips_is_asked_for_when_the_report_is_read(self, w):  # noqa: F811
        _, _, src = a_played_game(w)
        put(w, src, {"path": G.format(src), "color": "red", "slot": 4})
        w["settings"].game_clips_enabled = True
        assert get(w, f"/api/me/plays/{src}/doc").json()["clips"]["pending"] is True
        assert len(jobs(w, src)) == 1

    def test_a_reprocess_asks_again_on_the_new_run(self, w):  # noqa: F811
        src = claimed(w)
        worker_finishes(w, {"0.1.13": {"clip": True}, "0.1.15": {"clip": True}})
        first = w["repo"].get_source(src).current_run_id
        w["client"].post("/api/admin/reprocess", headers=ADMIN, json={"video_id": VID})
        work_through(w, doc=played_doc(), games=1)
        assert w["repo"].get_source(src).current_run_id != first
        clips = get(w, f"/api/me/plays/{src}/doc").json()["clips"]
        assert clips["pending"] is True
        assert jobs(w, src)[-1].run_id == w["repo"].get_source(src).current_run_id


class TestReadingClips:
    def test_the_state_as_the_worker_left_it(self, w):  # noqa: F811
        src = claimed(w)
        worker_finishes(w, {"0.1.13": {"clip": True, "overlay": [{"t0": 0.0, "t1": 2.0}]},
                            "0.1.15": {"clip": None, "reason": "video_gone"}})
        got = get(w, f"/api/me/plays/{src}/clips").json()["clips"]
        assert got == {"rocks": {"0.1.13": {"clip": True, "overlay": [{"t0": 0.0, "t1": 2.0}]},
                                 "0.1.15": {"clip": None, "reason": "video_gone"}},
                       "pending": False}

    def test_the_link_is_the_owners_alone(self, w):  # noqa: F811
        src = claimed(w)
        job = worker_finishes(w, {"0.1.13": {"clip": True},
                                  "0.1.15": {"clip": None, "reason": "no_release"}})
        r = get(w, f"/api/me/plays/{src}/clips/0.1.13")
        assert r.status_code == 200, r.text
        assert r.json()["url"] == f"memory://runs/{VID}/{job['run_id']}/clips/0.1.13.mp4"
        assert get(w, f"/api/me/plays/{src}/clips/0.1.15").status_code == 404
        assert get(w, f"/api/me/plays/{src}/clips/0.1.13", headers=ALEX).status_code == 404
        assert get(w, f"/api/me/plays/{src}/clips/..%2Fx").status_code == 404
        assert get(w, f"/api/me/plays/{src}/clips/0.1.13", headers=None).status_code == 401

    def test_a_run_being_analysed_again_is_not_answered_for(self, w):  # noqa: F811
        slug, _, src = a_played_game(w)
        w["settings"].game_clips_enabled = True
        r = put(w, src, {"path": f"/c/{slug}/", "color": "red", "slot": 4})
        assert r.status_code == 200, r.text
        run_id = w["repo"].get_chart(w["repo"].get_play("uid-sarah", src).chart_id).run_id
        w["repo"].update_run(run_id, status="processing")
        assert get(w, f"/api/me/plays/{src}/clips").status_code == 404
        assert get(w, f"/api/me/plays/{src}/doc").status_code == 409


class TestBackfill:
    """Plays saved before clips existed get theirs asked for in one go."""

    def backfill(self, w, headers=ADMIN, **params):  # noqa: F811
        return w["client"].post("/api/admin/clips/backfill", headers=headers, params=params)

    def test_for_the_admin_alone_and_only_with_clips_on(self, w):  # noqa: F811
        assert self.backfill(w, headers={}).status_code == 401
        assert self.backfill(w, headers=SARAH).status_code == 401
        assert self.backfill(w).status_code == 404

    def test_every_persons_plays_are_asked_for_once(self, w):  # noqa: F811
        _, _, src = a_played_game(w)
        put(w, src, {"path": G.format(src), "color": "red", "slot": 4})
        put(w, src, {"path": G.format(src), "color": "yellow", "slot": 1}, headers=ALEX)
        w["settings"].game_clips_enabled = True
        r = self.backfill(w)
        assert r.status_code == 200, r.text
        assert r.json()["plays"] == 2 and r.json()["skipped"] == []
        assert [x["name"] for x in jobs(w, src)[0].spec["rocks"]] == ["0.1.13", "0.1.15"]
        alex = lambda: w["repo"].clip_jobs_for_play(play_id("uid-alex", src))  # noqa: E731
        assert [x["name"] for x in alex()[0].spec["rocks"]] == ["0.1.2", "0.1.4"]
        self.backfill(w)
        assert len(jobs(w, src)) == 1 and len(alex()) == 1

    def test_recut_asks_again_for_clips_without_their_marks_burned_in(self, w):  # noqa: F811
        _, _, src = a_played_game(w)
        put(w, src, {"path": G.format(src), "color": "red", "slot": 4})
        w["settings"].game_clips_enabled = True
        self.backfill(w)
        worker_finishes(w, {"0.1.13": {"clip": True, "overlay": [{"t0": 0.0, "t1": 2.0}]},
                            "0.1.15": {"clip": None, "reason": "video_gone"}})
        self.backfill(w)
        assert len(jobs(w, src)) == 1
        assert self.backfill(w, recut="true").status_code == 200
        assert [x["name"] for x in jobs(w, src)[-1].spec["rocks"]] == ["0.1.13"]
        worker_finishes(w, {"0.1.13": {"clip": True, "burned": True,
                                       "style": gameclips.CLIP_STYLE}})
        got = get(w, f"/api/me/plays/{src}/clips").json()["clips"]
        assert got["rocks"]["0.1.13"] == {"clip": True, "burned": True,
                                          "style": gameclips.CLIP_STYLE}
        self.backfill(w, recut="true")
        assert len(jobs(w, src)) == 2

    def test_a_game_that_cannot_be_read_is_skipped_and_said(self, w):  # noqa: F811
        slug, _, src = a_played_game(w)
        put(w, src, {"path": f"/c/{slug}/", "color": "red", "slot": 4})
        w["settings"].game_clips_enabled = True
        w["repo"].update_run(w["repo"].get_chart(slug).run_id, status="processing")
        got = self.backfill(w).json()
        assert got["plays"] == 1 and len(got["skipped"]) == 1
        assert got["skipped"][0]["source_id"] == src
        assert jobs(w, src) == []


# ------------------------------------------------------------------ shot clips
from curling_score.service import api as api_mod  # noqa: E402


def released_game(w):  # noqa: F811
    """A processed game whose rocks were seen released; (chart id, source id)."""
    doc = played_doc()
    for s in doc["games"][0]["ends"][0]["shots"]:
        s["t_release_s"] = s["t_rest_s"] - 20.0
    w["settings"].game_clips_enabled = True
    slug, _, src = a_played_game(w, doc)
    return slug, src


def shot(w, path, rock="0.1.13"):  # noqa: F811
    return w["client"].get("/api/shot-clips", params={"path": path, "rock": rock})


def ask(w, path, rock="0.1.13", headers=SARAH):  # noqa: F811
    return w["client"].post("/api/shot-clips", json={"path": path, "rock": rock},
                            headers=headers or {})


class TestShotClips:
    """A clip of any shot, asked for from its page."""

    def test_off_there_are_none(self, w):  # noqa: F811
        _, src = released_game(w)
        w["settings"].game_clips_enabled = False
        assert shot(w, G.format(src)).status_code == 404
        assert ask(w, G.format(src)).status_code == 404

    def test_anyone_may_look_only_someone_signed_in_may_ask(self, w):  # noqa: F811
        _, src = released_game(w)
        assert shot(w, G.format(src)).json() == {"state": "none"}
        assert ask(w, G.format(src), headers=None).status_code == 401
        r = ask(w, G.format(src))
        assert r.status_code == 200, r.text
        assert r.json() == {"state": "queued", "ahead": 0}
        (job,) = w["repo"].clip_jobs_in_state("queued")
        run = w["repo"].get_run(w["repo"].get_source(src).current_run_id)
        assert job.id == gameclips.shot_job_id(run.id, "0.1.13")
        assert (job.play_id, job.user_id, job.source_id) == ("", "uid-sarah", src)
        assert [r["name"] for r in job.spec["rocks"]] == ["0.1.13"]
        assert job.spec["rocks"][0]["line"]["delivery"] == [[0.0, 38.0, 0.1]]
        assert ask(w, G.format(src), headers=ALEX).json()["state"] == "queued"
        assert len(w["repo"].clip_jobs_in_state("queued")) == 1

    def test_cut_then_played_and_saved_by_anyone(self, w):  # noqa: F811
        slug, src = released_game(w)
        ask(w, G.format(src))
        job = claim(w).json()["job"]
        assert shot(w, G.format(src)).json() == {"state": "cutting"}
        wpost(w, f"/api/worker/clips/{job['id']}/complete",
              {"rocks": {"0.1.13": {"clip": True, "burned": True}}})
        got = shot(w, f"/c/{slug}/").json()
        run = w["repo"].get_run(w["repo"].get_source(src).current_run_id)
        key = f"runs/{VID}/{run.id}/clips/0.1.13.mp4"
        assert got == {"state": "ready", "url": f"memory://{key}",
                       "download": f"memory://{key}?download=curling-{src}-e1-r13.mp4"}

    def test_a_shot_somebody_waits_on_comes_before_a_plays_batch(self, w):  # noqa: F811
        _, src = released_game(w)
        put(w, src, {"path": G.format(src), "color": "red", "slot": 4})
        assert len(jobs(w, src)) == 1
        w["clock"].advance(5)
        ask(w, G.format(src), rock="0.1.14", headers=ALEX)
        assert claim(w).json()["job"]["id"].startswith(gameclips.SHOT_JOB)
        assert claim(w).json()["job"]["id"] == jobs(w, src)[0].id

    def test_none_once_the_video_is_past_keeping_but_a_cut_one_stays(self, w):  # noqa: F811
        _, src = released_game(w)
        ask(w, G.format(src))
        job = claim(w).json()["job"]
        wpost(w, f"/api/worker/clips/{job['id']}/complete",
              {"rocks": {"0.1.13": {"clip": True, "burned": True}}})
        w["clock"].advance(gameclips.SHOT_CLIP_DAYS * 86400 + 60)
        assert shot(w, G.format(src)).json()["state"] == "ready"
        assert shot(w, G.format(src), rock="0.1.15").json() == {"state": "expired"}
        assert ask(w, G.format(src), rock="0.1.15").status_code == 410

    def test_gone_failed_and_asked_again(self, w):  # noqa: F811
        _, src = released_game(w)
        ask(w, G.format(src))
        ask(w, G.format(src), rock="0.1.15")
        for _ in range(2):
            job = claim(w).json()["job"]
            name = job["spec"]["rocks"][0]["name"]
            wpost(w, f"/api/worker/clips/{job['id']}/complete", {"rocks": {
                name: {"clip": None, "reason": "video_gone" if name == "0.1.13" else "cut_failed"}}})
        assert shot(w, G.format(src)).json() == {"state": "gone"}
        assert ask(w, G.format(src)).json() == {"state": "gone"}
        assert shot(w, G.format(src), rock="0.1.15").json() == {"state": "failed",
                                                                 "reason": "cut_failed"}
        assert ask(w, G.format(src), rock="0.1.15").json()["state"] == "queued"

    def test_a_rock_never_released_has_nothing_to_cut(self, w):  # noqa: F811
        w["settings"].game_clips_enabled = True
        _, _, src = a_played_game(w)
        assert ask(w, G.format(src)).json() == {"state": "failed", "reason": "no_release"}
        assert w["repo"].clip_jobs_in_state("queued") == []

    def test_no_such_shot_or_page(self, w):  # noqa: F811
        _, src = released_game(w)
        assert ask(w, G.format(src), rock="0.1.99").status_code == 404
        assert ask(w, G.format(src), rock="13").status_code == 404
        assert ask(w, "/x/y/").status_code == 404
        assert shot(w, "/g/nope/").status_code == 404

    def test_not_while_being_analysed_again(self, w):  # noqa: F811
        slug, src = released_game(w)
        run_id = w["repo"].get_chart(slug).run_id
        w["repo"].update_run(run_id, status="processing")
        assert shot(w, f"/c/{slug}/").json() == {"state": "busy"}
        assert ask(w, f"/c/{slug}/").status_code == 409

    def test_while_live_cut_from_the_recording_being_made(self, w):  # noqa: F811
        slug, src = released_game(w)
        run_id = w["repo"].get_chart(slug).run_id
        w["repo"].update_run(run_id, status="live", kind="live")
        assert shot(w, f"/c/{slug}/").json() == {"state": "none"}
        assert ask(w, f"/c/{slug}/").json() == {"state": "queued", "ahead": 0}
        job = claim(w).json()["job"]
        assert job["spec"]["live"] is True

    def test_asking_is_rate_limited(self, w, monkeypatch):  # noqa: F811
        monkeypatch.setattr(api_mod, "SHOT_CLIP_HOUR_LIMIT", 2)
        _, src = released_game(w)
        assert ask(w, G.format(src), rock="0.1.13").status_code == 200
        assert ask(w, G.format(src), rock="0.1.14").status_code == 200
        assert ask(w, G.format(src), rock="0.1.13").status_code == 200    # on its way: free
        assert ask(w, G.format(src), rock="0.1.15").status_code == 429


class TestTheClaimsQuietWindow:
    """An idle clips worker asks every few seconds; Firestore is asked less."""

    def test_an_empty_queue_is_remembered_until_this_instance_queues_a_job(self, w):  # noqa: F811
        _, src = released_game(w)
        assert claim(w).status_code == 204
        a_clip_job(w, src)                                  # queued on the other instance
        assert claim(w).status_code == 204
        w["clock"].advance(api_mod.CLIP_QUIET_S + 1)
        assert claim(w).json()["job"]["id"] == "cj_1"
        assert claim(w).status_code == 204
        ask(w, G.format(src))                               # queued here: asked at once
        assert claim(w).json()["job"]["id"].startswith(gameclips.SHOT_JOB)


class TestPlaysClipsSave:
    def test_a_claimed_rocks_clip_has_a_link_that_saves_it(self, w):  # noqa: F811
        _, src = released_game(w)
        put(w, src, {"path": G.format(src), "color": "red", "slot": 4})
        worker_finishes(w, {"0.1.13": {"clip": True, "burned": True},
                            "0.1.15": {"clip": True, "burned": True}})
        got = get(w, f"/api/me/plays/{src}/clips/0.1.13").json()
        assert got["download"].endswith(f"?download=curling-{src}-e1-r13.mp4")
        assert got["url"].endswith("/clips/0.1.13.mp4")
