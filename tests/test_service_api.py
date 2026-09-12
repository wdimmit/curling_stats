"""The hosted service, end to end, with nothing outside this process.

A person submits a link and gets a chart URL; a worker claims the job, reports
progress, uploads, completes; the chart page fills in; two tabs cannot clobber
each other's grading; the catalogue lists the game; the watcher queues a league
night. Every external system is its in-memory twin.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from curling_score.service import slug
from curling_score.service.api import Settings, create_app
from curling_score.service.records import WatchedPlaylist
from curling_score.service.repo import MemoryRepo
from curling_score.service.store import MemoryStore, timeline_key
from curling_score.service.youtube import FakeYouTube, VideoMeta

T0 = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)
VID = "VXU9xwmugRg"
CLUB = "UC81pcWoRJbdnK77B3WS0zjg"
WORKER = {"Authorization": "Bearer worker-secret"}
ADMIN = {"Authorization": "Bearer admin-secret"}


class Clock:
    def __init__(self):
        self.t = T0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += timedelta(seconds=seconds)


def sample_doc(games=2):
    """A schema-v2 document with the shape the viewer needs, tiny."""
    def game(i, start):
        return {"index": i, "start_s": start, "end_s": start + 6000.0,
                "teams": {"red": {"name": None}, "yellow": {"name": None}},
                "final": {"red": 0, "yellow": 0}, "hammer_consistent": True,
                "ends": [{"number": 1, "house": "top", "start_s": start, "end_s": start + 900.0,
                          "score": {"red": 0, "yellow": 0}, "running": {"red": 0, "yellow": 0},
                          "unplaced_shots": 0, "shots": [
                              {"number": 1, "color": "red", "position": "lead", "rock_of_player": 1,
                               "label": "1st end, lead's first rock", "t_rest_s": start + 60.0,
                               "t_enter_s": start + 52.0, "t_video_s": start + 42.0,
                               "state_known": True, "missing": False, "shot_type": "guard",
                               "stones": [], "track": []}]}]}
    return {"schema_version": 2, "processing_version": "2026.09.1+m-abc",
            "source": {"url": "u", "video_id": VID, "sheet": 2, "duration_s": 14392.0,
                       "window": {"start_s": None, "end_s": None}},
            "calibration": {},
            "games": [game(i, 100.0 + i * 7400.0) for i in range(games)]}


@pytest.fixture
def world():
    clock = Clock()
    repo, store = MemoryRepo(), MemoryStore()
    yt = FakeYouTube()
    yt.add(VideoMeta(VID, "4/30 - Sheet 2 - Spring League", CLUB, 14392.0, "none", T0))
    yt.add(VideoMeta("otherVid001", "Someone else", "UCstranger", 3600.0, "none", T0))
    yt.add(VideoMeta("liveVid0001", "Tonight", CLUB, 0.0, "live", T0))
    settings = Settings(public_base_url="https://chart.example", worker_token="worker-secret",
                        admin_token="admin-secret", allowed_channels={CLUB},
                        model_id="m-abc", max_queued=3, rate_hour=5, rate_day=20)
    # max_hours is left at its default: a hard sanity cap well above the 5 h
    # threshold at which a stream needs a start time (dedupe.MAX_WHOLE_S).
    app = create_app(repo, store, yt, settings, now=clock)
    client = TestClient(app)
    return {"client": client, "repo": repo, "store": store, "yt": yt,
            "clock": clock, "settings": settings}


def submit(w, url=f"https://youtu.be/{VID}", **body):
    return w["client"].post("/api/submissions", json={"url": url, **body},
                            headers={"X-Forwarded-For": body.pop("ip", "1.2.3.4")})


def work_through(w, doc=None, games=2):
    """Play the worker: claim, progress, upload, complete. Returns the job."""
    c = w["client"]
    r = c.post("/api/worker/claim", json={"worker_id": "home", "model_id": "m-abc",
                                          "version": "2026.09.1", "gpu": "A2000"}, headers=WORKER)
    assert r.status_code == 200, r.text
    job = r.json()["job"]
    assert c.post(f"/api/worker/jobs/{job['id']}/progress", headers=WORKER,
                  json={"worker_id": "home", "phase": "detect", "fraction": 0.5,
                        "message": "game 1 end 4"}).status_code == 200
    doc = doc or sample_doc(games)
    plan = c.post(f"/api/worker/jobs/{job['id']}/artifacts", headers=WORKER,
                  json={"worker_id": "home",
                        "files": [{"name": "timeline.json", "bytes": 10},
                                  {"name": "meta.json", "bytes": 5}],
                        "detcache": [{"digest": "abc123", "bytes": 99}]}).json()
    for up in plan["uploads"]:
        w["store"].put_bytes(up["key"], json.dumps(doc if up["name"] == "timeline.json"
                                                   else {"meta": 1}).encode())
    for up in plan["detcache_uploads"]:
        w["store"].put_bytes(up["key"], b"npz")
    games_list = [{"index": g["index"], "start_s": g["start_s"], "end_s": g["end_s"],
                   "ends": len(g["ends"])} for g in doc["games"]]
    r = c.post(f"/api/worker/jobs/{job['id']}/complete", headers=WORKER,
               json={"worker_id": "home", "title": "4/30 - Sheet 2 - Spring League",
                     "channel_id": CLUB, "duration_s": 14392.0, "sheet": 2,
                     "games": games_list, "detcache_digests": ["abc123"], "timings": {}})
    assert r.status_code == 200, r.text
    return job


class TestSubmitting:
    def test_a_club_video_gets_a_link_and_a_queued_job(self, world):
        r = submit(world)
        assert r.status_code == 201, r.text
        body = r.json()
        assert slug.is_slug(body["slug"])
        assert body["chart_url"] == f"https://chart.example/c/{body['slug']}/"
        assert body["share_url"].startswith("https://chart.example/s/")
        assert body["status"] == "queued" and body["position"] == 0
        assert world["repo"].count_queued() == 1

    def test_the_start_time_in_the_link_is_kept(self, world):
        r = submit(world, url=f"https://youtu.be/{VID}?t=7600")
        chart = world["repo"].get_chart(r.json()["slug"])
        assert chart.requested_start_s == 7600.0

    def test_an_explicit_start_wins_over_the_link(self, world):
        r = submit(world, url=f"https://youtu.be/{VID}?t=10", start_s=7600)
        assert world["repo"].get_chart(r.json()["slug"]).requested_start_s == 7600.0

    def test_the_same_video_again_is_a_new_link_but_not_a_new_job(self, world):
        a = submit(world).json()
        b = submit(world, ip="5.6.7.8").json()
        assert a["slug"] != b["slug"]
        assert b["reused"] is True
        assert world["repo"].count_queued() == 1
        assert world["repo"].get_chart(a["slug"]).run_id == world["repo"].get_chart(b["slug"]).run_id

    def test_a_video_from_another_channel_is_refused(self, world):
        r = submit(world, url="otherVid001")
        assert r.status_code == 403 and "club" in r.json()["detail"]

    def test_a_live_stream_is_refused(self, world):
        r = submit(world, url="liveVid0001")
        assert r.status_code == 422

    def test_a_non_youtube_link_is_a_400(self, world):
        assert submit(world, url="https://example.com/x").status_code == 400

    def test_an_unknown_video_is_a_404(self, world):
        assert submit(world, url="unknownVid1").status_code == 404

    def test_rate_limit_per_address(self, world):
        for _ in range(5):
            assert submit(world, ip="9.9.9.9").status_code == 201
        assert submit(world, ip="9.9.9.9").status_code == 429
        assert submit(world, ip="9.9.9.10").status_code == 201

    def test_a_full_queue_refuses_new_videos_but_not_new_links(self, world):
        yt = world["yt"]
        for i in range(3):
            yt.add(VideoMeta(f"clubVid000{i}", f"Sheet {i}", CLUB, 10000.0, "none", T0))
            assert submit(world, url=f"clubVid000{i}", ip=f"10.0.0.{i}").status_code == 201
        yt.add(VideoMeta("clubVid0009", "Sheet 9", CLUB, 10000.0, "none", T0))
        assert submit(world, url="clubVid0009", ip="10.0.0.9").status_code == 503
        # A link to an already-queued video needs no new job, so it is fine.
        assert submit(world, url="clubVid0000", ip="10.0.0.11").status_code == 201

    def test_a_very_long_stream_needs_a_start_time(self, world):
        world["yt"].add(VideoMeta("longVid0001", "Marathon", CLUB, 8 * 3600.0, "none", T0))
        assert submit(world, url="longVid0001").status_code == 422
        r = submit(world, url="longVid0001", start_s=20000, ip="2.2.2.2")
        assert r.status_code == 201
        run = world["repo"].get_run(world["repo"].get_chart(r.json()["slug"]).run_id)
        assert run.window_start_s == 19400.0


class TestStatusPage:
    def test_a_queued_chart_serves_the_status_page(self, world):
        s = submit(world).json()["slug"]
        r = world["client"].get(f"/c/{s}/")
        assert r.status_code == 200 and "status.js" in r.text
        st = world["client"].get(f"/c/{s}/status.json").json()
        assert st["status"] == "queued" and st["position"] == 0
        assert st["worker_online"] is False

    def test_the_worker_heartbeat_shows_as_online(self, world):
        s = submit(world).json()["slug"]
        world["client"].post("/api/worker/claim", headers=WORKER,
                             json={"worker_id": "home", "model_id": "m-abc"})
        st = world["client"].get(f"/c/{s}/status.json").json()
        assert st["status"] == "processing" and st["worker_online"] is True
        world["clock"].advance(120)
        assert world["client"].get(f"/c/{s}/status.json").json()["worker_online"] is False

    def test_progress_and_eta_appear_while_processing(self, world):
        s = submit(world).json()["slug"]
        job = world["client"].post("/api/worker/claim", headers=WORKER,
                                   json={"worker_id": "home", "model_id": "m-abc"}).json()["job"]
        world["client"].post(f"/api/worker/jobs/{job['id']}/progress", headers=WORKER,
                             json={"worker_id": "home", "phase": "detect", "fraction": 0.5,
                                   "message": "game 1 end 4"})
        st = world["client"].get(f"/c/{s}/status.json").json()
        assert st["phase"] == "detect" and st["fraction"] == 0.5
        assert st["message"] == "game 1 end 4"
        assert 0 < st["eta_s"] < 30 * 60

    def test_the_timeline_is_404_until_ready(self, world):
        s = submit(world).json()["slug"]
        assert world["client"].get(f"/c/{s}/timeline.json").status_code == 404

    def test_a_bad_slug_is_404(self, world):
        assert world["client"].get("/c/nope/").status_code == 404
        assert world["client"].get("/c/nope/status.json").status_code == 404


class TestWorkerProtocol:
    def test_claim_requires_the_token(self, world):
        submit(world)
        assert world["client"].post("/api/worker/claim", json={}).status_code == 401
        assert world["client"].post("/api/worker/claim", json={},
                                    headers={"Authorization": "Bearer wrong"}).status_code == 401

    def test_nothing_to_do_is_204(self, world):
        r = world["client"].post("/api/worker/claim", headers=WORKER,
                                 json={"worker_id": "home", "model_id": "m-abc"})
        assert r.status_code == 204

    def test_a_worker_with_the_wrong_model_is_refused(self, world):
        submit(world)
        r = world["client"].post("/api/worker/claim", headers=WORKER,
                                 json={"worker_id": "home", "model_id": "m-OLD"})
        assert r.status_code == 409
        assert world["repo"].count_queued() == 1   # nothing was handed out

    def test_the_full_happy_path_makes_the_chart_ready(self, world):
        a = submit(world, url=f"https://youtu.be/{VID}?t=7600").json()
        b = submit(world, ip="2.2.2.2").json()
        work_through(world)
        c = world["client"]
        run = world["repo"].get_run(world["repo"].get_chart(a["slug"]).run_id)
        assert run.status == "ready" and len(run.games) == 2
        # The chart that asked for t=7600 got game 2; the one with no time, game 1.
        ta = c.get(f"/c/{a['slug']}/timeline.json").json()
        tb = c.get(f"/c/{b['slug']}/timeline.json").json()
        assert [g["index"] for g in ta["games"]] == [1]
        assert [g["index"] for g in tb["games"]] == [0]
        assert ta["chart"]["slug"] == a["slug"] and ta["chart"]["read_only"] is False
        assert ta["chart"]["share_url"] == b_share(b) or ta["chart"]["share_url"].startswith("https://chart.example/s/")
        assert ta["source"]["start_s"] == 7500.0
        # The page now serves the viewer, told its mode.
        page = c.get(f"/c/{a['slug']}/").text
        assert "app.js" in page and '"mode": "edit"' in page
        st = c.get(f"/c/{a['slug']}/status.json").json()
        assert st["status"] == "ready" and st["game"]["index"] == 1
        assert [g["index"] for g in st["other_games"]] == [0]
        # Two games, two sources, in the catalogue.
        games = c.get("/api/games").json()["games"]
        assert len(games) == 2 and {g["game_index"] for g in games} == {0, 1}
        assert all(g["status"] == "ready" for g in games)

    def test_complete_without_an_upload_is_refused(self, world):
        submit(world)
        job = world["client"].post("/api/worker/claim", headers=WORKER,
                                   json={"worker_id": "home", "model_id": "m-abc"}).json()["job"]
        r = world["client"].post(f"/api/worker/jobs/{job['id']}/complete", headers=WORKER,
                                 json={"worker_id": "home", "games": []})
        assert r.status_code == 409

    def test_complete_is_idempotent(self, world):
        submit(world)
        job = work_through(world)
        r = world["client"].post(f"/api/worker/jobs/{job['id']}/complete", headers=WORKER,
                                 json={"worker_id": "home", "games": []})
        assert r.status_code == 200 and r.json().get("already") is True

    def test_an_expired_lease_is_requeued_on_the_next_claim(self, world):
        submit(world)
        job = world["client"].post("/api/worker/claim", headers=WORKER,
                                   json={"worker_id": "home", "model_id": "m-abc"}).json()["job"]
        world["clock"].advance(601)
        r = world["client"].post("/api/worker/claim", headers=WORKER,
                                 json={"worker_id": "home2", "model_id": "m-abc"})
        assert r.status_code == 200
        again = r.json()["job"]
        assert again["id"] == job["id"] and again["attempt"] == 2
        # The first worker is now told to stop.
        r = world["client"].post(f"/api/worker/jobs/{job['id']}/progress", headers=WORKER,
                                 json={"worker_id": "home", "phase": "detect", "fraction": 0.9})
        assert r.status_code == 409

    def test_a_blocked_download_is_retried_on_the_ladder(self, world):
        s = submit(world).json()["slug"]
        job = world["client"].post("/api/worker/claim", headers=WORKER,
                                   json={"worker_id": "home", "model_id": "m-abc"}).json()["job"]
        r = world["client"].post(f"/api/worker/jobs/{job['id']}/fail", headers=WORKER,
                                 json={"worker_id": "home", "error": "not a bot", "kind": "blocked"})
        assert r.json() == {"ok": True, "final": False, "retry_after_s": 300}
        assert world["client"].get(f"/c/{s}/status.json").json()["status"] == "queued"
        # Not claimable until the ladder step has passed.
        assert world["client"].post("/api/worker/claim", headers=WORKER,
                                    json={"worker_id": "home", "model_id": "m-abc"}).status_code == 204
        world["clock"].advance(301)
        assert world["client"].post("/api/worker/claim", headers=WORKER,
                                    json={"worker_id": "home", "model_id": "m-abc"}).status_code == 200

    def test_a_permanent_failure_fails_the_run(self, world):
        s = submit(world).json()["slug"]
        job = world["client"].post("/api/worker/claim", headers=WORKER,
                                   json={"worker_id": "home", "model_id": "m-abc"}).json()["job"]
        world["client"].post(f"/api/worker/jobs/{job['id']}/fail", headers=WORKER,
                             json={"worker_id": "home", "error": "Video unavailable: private",
                                   "kind": "permanent"})
        st = world["client"].get(f"/c/{s}/status.json").json()
        assert st["status"] == "failed" and "private" in st["error"]

    def test_detcache_already_stored_is_not_asked_for_again(self, world):
        submit(world)
        work_through(world)
        # A second run of the same video (reprocess) offers the same digest.
        world["client"].post("/api/admin/reprocess", headers=ADMIN, json={"video_id": VID})
        job = world["client"].post("/api/worker/claim", headers=WORKER,
                                   json={"worker_id": "home", "model_id": "m-abc"}).json()["job"]
        plan = world["client"].post(f"/api/worker/jobs/{job['id']}/artifacts", headers=WORKER,
                                    json={"worker_id": "home", "files": [],
                                          "detcache": [{"digest": "abc123", "bytes": 1},
                                                       {"digest": "new456", "bytes": 1}]}).json()
        assert [d["digest"] for d in plan["detcache_uploads"]] == ["new456"]


def b_share(b):
    return b["share_url"]


class TestCharting:
    def _ready(self, world):
        a = submit(world).json()
        work_through(world)
        return a["slug"]

    def test_overrides_round_trip_with_versions(self, world):
        s = self._ready(world)
        c = world["client"]
        r = c.get(f"/c/{s}/overrides.json")
        assert r.json() == {} and r.headers["ETag"] == '"0"'
        r = c.post(f"/c/{s}/overrides.json?v=0", json={"0.1.1": {"user_score": 3}})
        assert r.status_code == 200 and r.json() == {"ok": True, "shots": 1, "version": 1}
        r = c.get(f"/c/{s}/overrides.json")
        assert r.json() == {"0.1.1": {"user_score": 3}} and r.headers["ETag"] == '"1"'

    def test_a_stale_tab_gets_409_and_the_truth(self, world):
        s = self._ready(world)
        c = world["client"]
        c.post(f"/c/{s}/overrides.json?v=0", json={"0.1.1": {"user_score": 3}})
        r = c.post(f"/c/{s}/overrides.json?v=0", json={"0.1.1": {"user_score": 1}})
        assert r.status_code == 409
        body = r.json()
        assert body["error"] == "stale" and body["version"] == 1
        assert body["overrides"] == {"0.1.1": {"user_score": 3}}

    def test_a_beacon_without_a_version_still_saves(self, world):
        s = self._ready(world)
        c = world["client"]
        c.post(f"/c/{s}/overrides.json?v=0", json={"a": {}})
        r = c.post(f"/c/{s}/overrides.json", content=b'{"b": {}}',
                   headers={"Content-Type": "application/json"})
        assert r.status_code == 200 and r.json()["version"] == 2

    @pytest.mark.parametrize("body,code", [
        (b"{not json", 400), (b"[1,2]", 400), (b'{"k": "not a patch"}', 400), (b"", 400),
    ])
    def test_bad_bodies_are_400_and_change_nothing(self, world, body, code):
        s = self._ready(world)
        c = world["client"]
        c.post(f"/c/{s}/overrides.json?v=0", json={"keep": {"x": 1}})
        r = c.post(f"/c/{s}/overrides.json?v=1", content=body,
                   headers={"Content-Type": "application/json"})
        assert r.status_code == code and r.json()["ok"] is False
        assert c.get(f"/c/{s}/overrides.json").json() == {"keep": {"x": 1}}

    def test_export_merges_overrides_into_the_document(self, world):
        s = self._ready(world)
        c = world["client"]
        c.post(f"/c/{s}/overrides.json?v=0", json={"0.1.1": {"user_score": 4, "shot_type": "peel"}})
        shot = c.get(f"/c/{s}/export.json").json()["games"][0]["ends"][0]["shots"][0]
        assert shot["user_score"] == 4 and shot["shot_type"] == "peel" and shot["corrected"] is True

    def test_the_share_link_views_but_cannot_edit(self, world):
        s = self._ready(world)
        c = world["client"]
        share = world["repo"].get_chart(s).share_slug
        c.post(f"/c/{s}/overrides.json?v=0", json={"0.1.1": {"user_score": 2}})
        assert c.get(f"/s/{share}/overrides.json").json() == {"0.1.1": {"user_score": 2}}
        doc = c.get(f"/s/{share}/timeline.json").json()
        assert doc["chart"]["read_only"] is True and doc["chart"]["share_url"] is None
        assert '"mode": "view"' in c.get(f"/s/{share}/").text
        r = c.post(f"/s/{share}/overrides.json", json={"x": {}})
        assert r.status_code == 403

    def test_assets_are_served_under_the_chart(self, world):
        s = self._ready(world)
        c = world["client"]
        assert c.get(f"/c/{s}/app.js").status_code == 200
        assert c.get(f"/c/{s}/style.css").status_code == 200
        assert c.get(f"/c/{s}/status.js").status_code == 200
        assert c.get(f"/c/{s}/nope.js").status_code == 404
        assert c.get(f"/c/{s}", follow_redirects=False).status_code == 302

    def test_a_link_made_after_processing_resolves_at_once(self, world):
        self._ready(world)
        r = submit(world, url=f"https://youtu.be/{VID}?t=7600", ip="3.3.3.3").json()
        assert r["status"] == "ready" and r["reused"] is True
        doc = world["client"].get(f"/c/{r['slug']}/timeline.json").json()
        assert [g["index"] for g in doc["games"]] == [1]


class TestCatalogueAndAdmin:
    def test_the_catalogue_lists_queued_runs_before_they_have_sources(self, world):
        submit(world)
        games = world["client"].get("/api/games").json()["games"]
        assert len(games) == 1 and games[0]["status"] == "queued"
        assert games[0]["source_id"] is None

    def test_chart_this_game_from_the_catalogue(self, world):
        submit(world)
        work_through(world)
        game2 = [g for g in world["client"].get("/api/games").json()["games"]
                 if g["game_index"] == 1][0]
        r = submit(world, url=game2["video_id"], start_s=game2["start_s"], ip="4.4.4.4")
        doc = world["client"].get(f"/c/{r.json()['slug']}/timeline.json").json()
        assert [g["index"] for g in doc["games"]] == [1]

    def test_admin_needs_its_token(self, world):
        assert world["client"].get("/api/admin/runs").status_code == 401
        assert world["client"].get("/api/admin/runs", headers=WORKER).status_code == 401
        assert world["client"].get("/api/admin/runs", headers=ADMIN).status_code == 200

    def test_admin_sees_runs_workers_and_the_queue(self, world):
        submit(world)
        world["client"].post("/api/worker/claim", headers=WORKER,
                             json={"worker_id": "home", "model_id": "m-abc", "gpu": "A2000"})
        data = world["client"].get("/api/admin/runs", headers=ADMIN).json()
        assert data["runs"][0]["status"] == "processing"
        assert data["workers"][0]["gpu"] == "A2000"

    def test_retry_requeues_a_failed_run(self, world):
        s = submit(world).json()["slug"]
        job = world["client"].post("/api/worker/claim", headers=WORKER,
                                   json={"worker_id": "home", "model_id": "m-abc"}).json()["job"]
        world["client"].post(f"/api/worker/jobs/{job['id']}/fail", headers=WORKER,
                             json={"worker_id": "home", "error": "x", "kind": "permanent"})
        run_id = world["repo"].get_chart(s).run_id
        assert world["client"].post(f"/api/admin/runs/{run_id}/retry", headers=ADMIN).status_code == 200
        assert world["client"].get(f"/c/{s}/status.json").json()["status"] == "queued"

    def test_approval_mode(self, world):
        world["settings"].require_approval = True
        s = submit(world).json()["slug"]
        assert world["client"].get(f"/c/{s}/status.json").json()["status"] == "pending_approval"
        assert world["repo"].count_queued() == 0
        run_id = world["repo"].get_chart(s).run_id
        world["client"].post(f"/api/admin/runs/{run_id}/approve", headers=ADMIN)
        assert world["repo"].count_queued() == 1

    def test_export_is_a_backup_that_restores(self, world):
        s = submit(world).json()["slug"]
        work_through(world)
        world["client"].post(f"/c/{s}/overrides.json?v=0", json={"0.1.1": {"user_score": 4}})
        data = world["client"].get("/api/admin/export", headers=ADMIN).json()
        assert {c["id"] for c in data["charts"]} == {s}
        fresh = MemoryRepo()
        # Timestamps come back as strings; the restore path parses them.
        from curling_score.service.restore import restore
        restore(fresh, data)
        assert fresh.get_chart(s).overrides == {"0.1.1": {"user_score": 4}}

    def test_playlists_are_watched_and_polled(self, world):
        c = world["client"]
        r = c.post("/api/admin/playlists", headers=ADMIN,
                   json={"playlist_id": "PLtuesday", "label": "Tuesday Open"})
        assert r.status_code == 201
        world["yt"]._playlists["PLtuesday"] = [VID, "liveVid0001", "otherVid001"]
        result = c.post("/api/admin/poll-playlists", headers=ADMIN).json()
        assert len(result["created"]) == 1
        assert ["otherVid001", "channel_not_allowed"] in result["skipped"]
        games = c.get("/api/games").json()["games"]
        assert games[0]["league"] == "Tuesday Open" and games[0]["status"] == "queued"
        pls = c.get("/api/admin/playlists", headers=ADMIN).json()["playlists"]
        assert pls[0]["label"] == "Tuesday Open" and "liveVid0001" not in pls[0]["last_seen_video_ids"]

    def test_health(self, world):
        assert world["client"].get("/api/healthz").json() == {"ok": True}
        assert world["client"].get("/api/healthz/worker").status_code == 503
        world["client"].post("/api/worker/claim", headers=WORKER,
                             json={"worker_id": "home", "model_id": "m-abc"})
        assert world["client"].get("/api/healthz/worker").status_code == 200

    def test_the_pages_load(self, world):
        c = world["client"]
        assert "Get my link" in c.get("/").text
        assert "All games" in c.get("/games").text or "games.js" in c.get("/games").text
        assert c.get("/static/site.css").status_code == 200


class TestTokensWithStraySurroundingWhitespace:
    """Secrets pick up trailing newlines; the comparison must not care.

    Every token in the deployment was created by piping `print(...)` into
    `gcloud secrets create`, which stores the newline. The worker reads its own
    copy through `$(...)`, which strips it. That mismatch is a 401 with nothing
    in the logs to explain it, and it cost a real deployment.
    """

    @pytest.mark.parametrize("stored,sent", [
        ("worker-secret\n", "worker-secret"),
        ("worker-secret", "worker-secret\n"),
        (" worker-secret ", "worker-secret"),
        ("worker-secret\n", "worker-secret\n"),
    ])
    def test_a_stray_newline_either_side_still_authenticates(self, world, stored, sent):
        world["settings"].worker_token = stored
        submit(world)
        r = world["client"].post("/api/worker/claim",
                                 headers={"Authorization": f"Bearer {sent}"},
                                 json={"worker_id": "home", "model_id": "m-abc"})
        assert r.status_code == 200, r.text

    def test_a_genuinely_wrong_token_is_still_refused(self, world):
        world["settings"].worker_token = "worker-secret\n"
        r = world["client"].post("/api/worker/claim",
                                 headers={"Authorization": "Bearer nope"},
                                 json={"worker_id": "home"})
        assert r.status_code == 401

    def test_an_empty_token_never_authenticates(self, world):
        world["settings"].worker_token = ""
        r = world["client"].post("/api/worker/claim",
                                 headers={"Authorization": "Bearer "},
                                 json={"worker_id": "home"})
        assert r.status_code in (401, 503)


class TestRetryDelay:
    def test_an_explicit_zero_means_retry_now(self, world):
        # `float(x or 60)` would read 0 as "unset" and wait a minute.
        submit(world)
        job = world["client"].post("/api/worker/claim", headers=WORKER,
                                   json={"worker_id": "home", "model_id": "m-abc"}).json()["job"]
        r = world["client"].post(f"/api/worker/jobs/{job['id']}/fail", headers=WORKER,
                                 json={"worker_id": "home", "error": "released",
                                       "kind": "transient", "retry_after_s": 0})
        assert r.json()["retry_after_s"] == 0
        assert world["client"].post("/api/worker/claim", headers=WORKER,
                                    json={"worker_id": "home2", "model_id": "m-abc"}
                                    ).status_code == 200

    def test_an_unset_delay_still_defaults_to_a_minute(self, world):
        submit(world)
        job = world["client"].post("/api/worker/claim", headers=WORKER,
                                   json={"worker_id": "home", "model_id": "m-abc"}).json()["job"]
        r = world["client"].post(f"/api/worker/jobs/{job['id']}/fail", headers=WORKER,
                                 json={"worker_id": "home", "error": "x", "kind": "transient"})
        assert r.json()["retry_after_s"] == 60


class TestStatusIsInformative:
    """What the waiting page can say about a job in flight."""

    def _running(self, world):
        submit(world)
        return world["client"].post("/api/worker/claim", headers=WORKER,
                                    json={"worker_id": "home", "model_id": "m-abc"}
                                    ).json()["job"]

    def _slug(self, world):
        return world["repo"].list_runs()[0] and [
            c.id for c in world["repo"].charts_for_run(world["repo"].list_runs()[0].id)][0]

    def test_a_working_worker_reads_as_online_all_job_long(self, world):
        """The claim used to be the only heartbeat, so a worker went 'offline'
        90 seconds into every job while plainly working."""
        job = self._running(world)
        slug = self._slug(world)
        world["clock"].advance(600)
        assert world["client"].get(f"/c/{slug}/status.json").json()["worker_online"] is False
        world["client"].post(f"/api/worker/jobs/{job['id']}/progress", headers=WORKER,
                             json={"worker_id": "home", "phase": "detect", "fraction": 0.3})
        assert world["client"].get(f"/c/{slug}/status.json").json()["worker_online"] is True

    def test_each_finished_phase_reports_what_it_took(self, world):
        job = self._running(world)
        slug = self._slug(world)
        c = world["client"]
        c.post(f"/api/worker/jobs/{job['id']}/progress", headers=WORKER,
               json={"worker_id": "home", "phase": "download", "fraction": 0.5})
        world["clock"].advance(180)
        c.post(f"/api/worker/jobs/{job['id']}/progress", headers=WORKER,
               json={"worker_id": "home", "phase": "proxy", "fraction": 0.1})
        st = c.get(f"/c/{slug}/status.json").json()
        by = {p["name"]: p for p in st["phases"]}
        assert by["download"]["state"] == "done" and by["download"]["took_s"] == 180.0
        assert by["proxy"]["state"] == "running"
        assert by["detect"]["state"] == "todo"
        assert st["elapsed_s"] == 180 and st["phase_elapsed_s"] == 0

    def test_a_retried_job_does_not_show_the_old_error(self, world):
        job = self._running(world)
        slug = self._slug(world)
        c = world["client"]
        c.post(f"/api/worker/jobs/{job['id']}/fail", headers=WORKER,
               json={"worker_id": "home", "error": "a hiccup", "kind": "transient",
                     "retry_after_s": 0})
        c.post("/api/worker/claim", headers=WORKER,
               json={"worker_id": "home", "model_id": "m-abc"})
        st = c.get(f"/c/{slug}/status.json").json()
        assert st["status"] == "processing" and st["error"] is None

    def test_a_real_failure_is_still_shown(self, world):
        job = self._running(world)
        slug = self._slug(world)
        world["client"].post(f"/api/worker/jobs/{job['id']}/fail", headers=WORKER,
                             json={"worker_id": "home", "error": "Video unavailable",
                                   "kind": "permanent"})
        st = world["client"].get(f"/c/{slug}/status.json").json()
        assert st["status"] == "failed" and "unavailable" in st["error"]

    def test_the_attempt_number_is_visible(self, world):
        job = self._running(world)
        slug = self._slug(world)
        assert world["client"].get(f"/c/{slug}/status.json").json()["attempt"] == 1
