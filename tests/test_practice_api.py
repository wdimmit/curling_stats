"""Practice sessions through the API: start, join, read, stop, hide -- and the
worker's side of a practice job, as phase 2's worker drives it."""

import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from curling_score.service.api import Settings, create_app
from curling_score.service.auth import FakeVerifier
from curling_score.service.records import WatchedPlaylist
from curling_score.service.repo import MemoryRepo
from curling_score.service.store import MemoryStore, practice_key
from curling_score.service.youtube import FakeYouTube, VideoMeta
from tests.test_service_api import CLUB, T0, WORKER, Clock

SARAH = {"Authorization": "Bearer tok-sarah"}
ALEX = {"Authorization": "Bearer tok-alex"}
S3, S4, UP = "qJhDHMMtHsE", "dXYqdopieoU", "IqiFhMEOL4s"


def _world(practice=True):
    clock = Clock()
    repo, store = MemoryRepo(), MemoryStore()
    yt = FakeYouTube(playlists={"PLErN0WbLUyFI": [S3, S4, UP], "PLleague": [S4]})
    yt.add(VideoMeta(S3, "10/10 - Sheet 3 - Competitive Practice Ice 2026-2027", CLUB, None,
                     "live", T0))
    yt.add(VideoMeta(S4, "10/10 - Sheet 4 - Competitive Practice Ice 2026-2027", CLUB, None,
                     "live", T0))
    yt.add(VideoMeta(UP, "10/10 - Sheet 1 - Competitive Practice Ice 2026-2027", CLUB, None,
                     "upcoming", T0))
    repo.put_playlist(WatchedPlaylist(id="p_prac", playlist_id="PLErN0WbLUyFI",
                                      label="Practice", created_at=T0, practice=True))
    auth = FakeVerifier()
    auth.add("tok-sarah", "uid-sarah", "sarah@example.org", "Sarah")
    auth.add("tok-alex", "uid-alex", "alex@example.org", "Alex")
    settings = Settings(public_base_url="https://chart.example", worker_token="worker-secret",
                        admin_token="admin-secret", allowed_channels={CLUB}, model_id="m-abc",
                        firebase_project="proj", firebase_api_key="AIzaFake",
                        firebase_auth_domain="proj.firebaseapp.com",
                        practice_enabled=practice)
    app = create_app(repo, store, yt, settings, now=clock, auth=auth)
    return {"client": TestClient(app), "repo": repo, "store": store, "yt": yt,
            "clock": clock}


@pytest.fixture
def w():
    return _world()


def start(w, sheet=3, who=SARAH, hand="right"):
    return w["client"].post("/api/practice/sessions", json={"sheet": sheet, "hand": hand},
                            headers=who)


def claim(w):
    r = w["client"].post("/api/worker/claim", headers=WORKER, json={
        "worker_id": "ryzen", "model_id": "m-abc", "version": "2026.10.10", "gpu": "3080",
        "kinds": ["live", "practice"]})
    assert r.status_code == 200, r.text
    return r.json()["job"]


def doc(throws, wall_t0, status="watching"):
    return {"practice": 1, "status": status, "t0_s": 0.0, "wall_t0": wall_t0, "busy_s": 3.0,
            "since_s": 1200.0, "recorded_s": 1500.0, "throws": throws}


def throw(t, id=None, rest=True):
    return {"id": id or f"t_{t:.1f}", "t_release_s": t, "t_rest_s": t + 25.0 if rest else None,
            "house": "top", "color": "red", "arrived": rest, "release_source": "overhead",
            "split_s": 13.8, "release_speed": 2.4, "curl": "left", "broom": None,
            "line": None, "rest": {"x": 0.1, "y": -0.4, "to_tee_m": 0.41, "ring": "4"},
            "track": []}


def publish(w, job, d, worker="ryzen"):
    c = w["client"]
    plan = c.post(f"/api/worker/jobs/{job['id']}/artifacts", headers=WORKER,
                  json={"worker_id": worker, "files": [{"name": "practice.json", "bytes": 9}],
                        "detcache": []})
    assert plan.status_code == 200, plan.text
    (up,) = plan.json()["uploads"]
    w["store"].put_bytes(up["key"], json.dumps(d).encode())
    r = c.post(f"/api/worker/jobs/{job['id']}/publish", headers=WORKER, json={
        "worker_id": worker, "practice": {"status": d["status"], "recorded_s": 1500.0,
                                          "throws": len(d["throws"])}, "sheet": 3})
    assert r.status_code == 200, r.text
    return r.json()


def heartbeat(w, job):
    r = w["client"].post(f"/api/worker/jobs/{job['id']}/progress", headers=WORKER, json={
        "worker_id": "ryzen", "phase": "live", "fraction": None, "message": "recorded 25 min"})
    assert r.status_code == 200, r.text
    return r.json()


def view(w, sid, who=SARAH):
    return w["client"].get(f"/api/practice/sessions/{sid}", headers=who)


class TestStarting:
    def test_start_finds_the_sheets_live_stream_and_queues_a_watch(self, w):
        r = start(w)
        assert r.status_code == 201, r.text
        s = w["repo"].get_session(r.json()["id"])
        watch = w["repo"].get_watch(s.watch_id)
        run, job = w["repo"].get_run(watch.run_id), w["repo"].get_job(watch.job_id)
        assert (watch.sheet, watch.video_id, run.kind, job.kind) == (3, S3, "practice", "practice")
        assert (s.user_id, s.hand, s.active) == ("uid-sarah", "right", True)

    def test_a_second_start_on_a_sheet_joins_its_watch(self, w):
        a, b = start(w).json(), start(w, who=ALEX, hand="left").json()
        assert a["watch_id"] == b["watch_id"] and a["id"] != b["id"]
        assert len(w["repo"].jobs) == 1

    def test_a_sheet_with_no_live_stream_says_so(self, w):
        r = start(w, sheet=1)                      # upcoming, not live yet
        assert r.status_code == 404 and "sheet 1" in r.json()["detail"]

    def test_a_league_playlist_is_not_searched_for_practice(self, w):
        w["repo"].put_playlist(WatchedPlaylist(id="p_league", playlist_id="PLleague",
                                               label="League", created_at=T0))
        assert w["yt"].playlist_video_ids("PLleague") == [S4]
        assert start(w, sheet=4).status_code == 201   # found through the practice one

    def test_signed_out_is_refused(self, w):
        assert start(w, who={}).status_code == 401

    def test_off_unless_switched_on(self):
        w = _world(practice=False)
        assert start(w).status_code == 404
        assert w["client"].get("/api/features").json()["practice"] is False


class TestTheWorkersSide:
    def test_a_practice_job_is_claimed_with_its_lookback(self, w):
        start(w)
        job = claim(w)
        assert (job["kind"], job["video_id"], job["sheet"], job["lookback_s"]) == (
            "practice", S3, 3, 1200.0)

    def test_a_worker_that_asks_only_for_recordings_never_gets_practice(self, w):
        start(w)
        r = w["client"].post("/api/worker/claim", headers=WORKER, json={
            "worker_id": "home", "model_id": "m-abc", "version": "2026.10.10", "gpu": "A2000"})
        assert r.status_code == 204

    def test_published_throws_reach_the_session_in_its_window(self, w):
        sid = start(w).json()["id"]
        job = claim(w)
        start_epoch = T0.timestamp()
        # Recording t=0 is 20 minutes before Start; throws at Start-60 s
        # (before), Start+60 s and Start+120 s.
        wall_t0 = start_epoch - 1200.0
        got = publish(w, job, doc([throw(1140.0), throw(1260.0), throw(1320.0)], wall_t0))
        assert got["stop"] is False
        w["clock"].advance(300)                    # read five minutes in
        v = view(w, sid).json()
        assert [t["id"] for t in v["throws"]] == ["t_1260.0", "t_1320.0"]
        assert v["session"]["status"] == "active" and v["watch"]["status"] == "watching"
        assert v["throws"][0]["released_at"].startswith("2026-09-11T12:01:00")

    def test_a_session_is_its_owners_alone(self, w):
        sid = start(w).json()["id"]
        assert view(w, sid, who=ALEX).status_code == 404
        assert w["client"].post(f"/api/practice/sessions/{sid}/stop", headers=ALEX).status_code == 404

    def test_reading_again_unchanged_is_a_304(self, w):
        sid = start(w).json()["id"]
        first = view(w, sid)
        again = w["client"].get(f"/api/practice/sessions/{sid}",
                                headers={**SARAH, "If-None-Match": first.headers["etag"]})
        assert again.status_code == 304

    def test_not_mine_hides_a_throw_and_can_be_undone(self, w):
        sid = start(w).json()["id"]
        job = claim(w)
        publish(w, job, doc([throw(1260.0)], T0.timestamp() - 1200.0))
        w["clock"].advance(300)
        c = w["client"]
        r = c.patch(f"/api/practice/sessions/{sid}/throws/t_1260.0", json={"hidden": True},
                    headers=SARAH)
        assert r.status_code == 200
        assert view(w, sid).json()["throws"][0]["hidden"] is True
        c.patch(f"/api/practice/sessions/{sid}/throws/t_1260.0", json={"hidden": False},
                headers=SARAH)
        assert view(w, sid).json()["throws"][0]["hidden"] is False

    def test_stopping_the_last_session_tells_the_worker_to_stop(self, w):
        sid = start(w).json()["id"]
        job = claim(w)
        assert heartbeat(w, job).get("stop") is not True
        assert w["client"].post(f"/api/practice/sessions/{sid}/stop",
                                headers=SARAH).status_code == 200
        assert heartbeat(w, job)["stop"] is True
        v = view(w, sid).json()
        assert v["session"]["status"] == "ended" and v["session"]["end_reason"] == "stop"

    def test_twenty_minutes_without_a_throw_ends_a_session(self, w):
        sid = start(w).json()["id"]
        job = claim(w)
        publish(w, job, doc([throw(1260.0)], T0.timestamp() - 1200.0))
        w["clock"].t = T0 + timedelta(seconds=60 + 1199)       # the throw was at Start+60
        assert heartbeat(w, job).get("stop") is not True
        w["clock"].t = T0 + timedelta(seconds=60 + 1201)
        assert heartbeat(w, job)["stop"] is True
        assert w["repo"].get_session(sid).end_reason == "idle"

    def test_a_completed_watch_ends_its_sessions(self, w):
        sid = start(w).json()["id"]
        job = claim(w)
        publish(w, job, doc([throw(1260.0)], T0.timestamp() - 1200.0))
        w["clock"].advance(300)
        r = w["client"].post(f"/api/worker/jobs/{job['id']}/complete", headers=WORKER, json={
            "worker_id": "ryzen", "practice": {"status": "ended", "recorded_s": 1600.0,
                                               "throws": 1}, "sheet": 3})
        assert r.status_code == 200, r.text
        v = view(w, sid).json()
        assert v["session"]["end_reason"] == "stream_ended" and v["watch"]["status"] == "ended"
        assert [t["id"] for t in v["throws"]] == ["t_1260.0"]

    def test_a_session_on_a_failed_watch_reads_as_failed(self, w):
        sid = start(w).json()["id"]
        job = claim(w)
        w["client"].post(f"/api/worker/jobs/{job['id']}/fail", headers=WORKER, json={
            "worker_id": "ryzen", "error": "dropped out", "kind": "permanent"})
        assert view(w, sid).json()["session"]["status"] == "failed"
        assert start(w).json()["watch_id"] != w["repo"].get_session(sid).watch_id


class TestMine:
    def test_my_sessions_newest_first(self, w):
        a = start(w).json()["id"]
        w["clock"].t = T0 + timedelta(minutes=5)
        b = start(w, sheet=4).json()["id"]
        start(w, who=ALEX)
        got = w["client"].get("/api/me/practice", headers=SARAH).json()["sessions"]
        assert [s["id"] for s in got] == [b, a]
        assert got[0]["sheet"] == 4 and got[0]["active"] is True


class TestKeptApart:
    def test_a_practice_run_is_not_a_game(self, w):
        start(w)
        claim(w)
        games = w["client"].get("/api/games").json()
        assert all(g.get("video_id") != S3 for g in games.get("games", []))

    def test_the_league_poller_leaves_a_practice_playlist_alone(self, w):
        from curling_score.service import playlists

        got = playlists.poll(w["repo"], w["yt"], processing_version="v", now=T0, live=True,
                             force=True)
        assert got["live"] == [] and got["created"] == []

    def test_the_page_and_the_flag(self, w):
        c = w["client"]
        assert 'data-page="practice"' in c.get("/practice/sheet/3").text
        assert c.get("/api/features").json()["practice"] is True

    def test_an_admin_can_mark_a_playlist_as_practice(self, w):
        r = w["client"].post("/api/admin/playlists", headers={"Authorization": "Bearer admin-secret"},
                             json={"playlist_id": "PLnew", "label": "More practice", "practice": True})
        assert r.status_code == 201, r.text
        assert next(p for p in w["repo"].list_playlists() if p.playlist_id == "PLnew").practice


def test_the_practice_document_has_a_key_of_its_own():
    assert practice_key("vid", "r_1") == "runs/vid/r_1/practice.json"
