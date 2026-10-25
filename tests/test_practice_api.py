"""Practice sessions through the API: start, join, read, stop, hide -- and the
worker's side of a practice job. Practice reads the club's cameras, through a
worker that can see them; a session from when it read YouTube still reads."""

import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from curling_score.service.api import Settings, create_app
from curling_score.service.auth import FakeVerifier
from curling_score.service.records import (
    ClipJob, Job, PracticeSession, PracticeWatch, Run, WatchedPlaylist,
)
from curling_score.service.repo import MemoryRepo
from curling_score.service.store import MemoryStore, clip_key, practice_key
from curling_score.service.youtube import FakeYouTube, VideoMeta
from tests.test_service_api import ADMIN, CLUB, T0, WORKER, Clock

SARAH = {"Authorization": "Bearer tok-sarah"}
ALEX = {"Authorization": "Bearer tok-alex"}
# A sheet's YouTube practice streams: never read for practice now, and never
# charted as games (the poller skips a practice playlist).
S3, S4, UP = "qJhDHMMtHsE", "dXYqdopieoU", "IqiFhMEOL4s"
CLUB_IP = "203.0.113.9"


def _world(practice=True, max_camera_watches=None):
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
                        practice_enabled=practice,
                        club_ips=frozenset({CLUB_IP}),
                        **({"practice_max_camera_watches": max_camera_watches}
                           if max_camera_watches is not None else {}))
    app = create_app(repo, store, yt, settings, now=clock, auth=auth)
    return {"client": TestClient(app), "repo": repo, "store": store, "yt": yt,
            "clock": clock}


def validate(w, uid, role="validated"):
    """Make ``uid`` a validated account. An account exists once it has been
    seen, so it is seen first."""
    who = {"uid-sarah": SARAH, "uid-alex": ALEX}[uid]
    w["client"].get("/api/me", headers=who)
    w["repo"].update_user(uid, role=role)


@pytest.fixture
def w():
    """Practice with Sarah's account validated, which Start needs (or the
    club's address: TestWhoMayStart). Alex's is not."""
    world = _world()
    validate(world, "uid-sarah")
    return world


def start(w, sheet=3, who=SARAH, hand="right", fwd=None):
    """``fwd`` is the X-Forwarded-For the request arrives with."""
    return w["client"].post("/api/practice/sessions", json={"sheet": sheet, "hand": hand},
                            headers={**who, **({"X-Forwarded-For": fwd} if fwd else {})})


def claim_body(cameras=True, **extra):
    """A Ryzen worker's claim: it can see the club's cameras."""
    return {"worker_id": "ryzen", "model_id": "m-abc", "version": "2026.10.10", "gpu": "3080",
            "kinds": ["live", "practice"], **({"cameras": True} if cameras else {}), **extra}


def claim(w, **extra):
    r = w["client"].post("/api/worker/claim", headers=WORKER, json=claim_body(**extra))
    assert r.status_code == 200, r.text
    return r.json()["job"]


def doc(throws, wall_t0, status="watching"):
    """A camera watch's document: its recording's t=0 is ``wall_t0``, the
    Start that made the watch."""
    return {"practice": 1, "status": status, "t0_s": 0.0, "wall_t0": wall_t0, "busy_s": 3.0,
            "since_s": 0.0, "recorded_s": 1500.0, "throws": throws}


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


NOT_ALLOWED = "Practice on the club's cameras needs a validated account, or the club's Wi-Fi."
LEAGUE_ON = "A league game is on; practice opens when it ends."
CAMERAS_FULL = ("Practice on the cameras is full: 2 sheets are already practising. "
                "Try again when one ends.")


def league_run(w, status="live", sheet=2, age_s=60.0, kind="live"):
    w["repo"].put_run(Run(id=f"r_league_{sheet}_{status}", video_id=f"league{sheet}",
                          processing_version="v", status=status, kind=kind, sheet=sheet,
                          created_at=w["clock"].t - timedelta(seconds=age_s)))


class TestStarting:
    def test_start_queues_a_watch_on_the_sheets_cameras(self, w):
        # Sheet 1's YouTube practice stream is "upcoming" and sheet 5 has none:
        # neither matters.
        for sheet in (1, 5):
            r = start(w, sheet=sheet)
            assert r.status_code == 201, r.text
            s = w["repo"].get_session(r.json()["id"])
            watch = w["repo"].get_watch(s.watch_id)
            run, job = w["repo"].get_run(watch.run_id), w["repo"].get_job(watch.job_id)
            assert (run.source, run.kind, job.kind, watch.sheet) == (
                "cameras", "practice", "practice", sheet)
            assert run.video_id.startswith(f"cams{sheet}-") and watch.video_id == run.video_id
            assert (s.user_id, s.hand, s.active) == ("uid-sarah", "right", True)

    def test_a_second_start_on_a_sheet_joins_its_watch(self, w):
        a = start(w).json()
        b = start(w, who=ALEX, hand="left", fwd=CLUB_IP).json()
        assert a["watch_id"] == b["watch_id"] and a["id"] != b["id"]
        assert len(w["repo"].jobs) == 1

    def test_a_sheet_the_club_has_no_cameras_on_is_a_404(self, w):
        r = start(w, sheet=6)
        assert r.status_code == 404 and r.json()["detail"] == "Sheet 6 has no cameras."
        assert not w["repo"].jobs

    def test_a_start_never_asks_youtube(self, w, monkeypatch):
        def boom(*a, **k):
            raise AssertionError("YouTube was asked")
        for name in ("playlist_video_ids", "videos"):
            monkeypatch.setattr(w["yt"], name, boom)
        assert start(w).status_code == 201

    def test_signed_out_is_refused(self, w):
        assert start(w, who={}).status_code == 401

    def test_off_unless_switched_on(self):
        w = _world(practice=False)
        assert start(w).status_code == 404


class TestWhoMayStart:
    """The club's cameras: a validated (or admin) account, or a signed-in
    request from the club's address. Nobody else, ever."""

    def test_a_validated_account_or_the_club_ip(self):
        w = _world()
        r = start(w)
        assert r.status_code == 403 and r.json()["detail"] == NOT_ALLOWED
        validate(w, "uid-sarah")
        assert start(w).status_code == 201
        # Alex is not validated: the club's own address stands in for that.
        assert start(w, who=ALEX, sheet=4).status_code == 403
        assert start(w, who=ALEX, sheet=4, fwd=CLUB_IP).status_code == 201

    def test_an_admin_may_start(self):
        w = _world()
        validate(w, "uid-sarah", role="admin")
        assert start(w).status_code == 201

    def test_the_club_ip_still_needs_a_signed_in_user(self):
        assert start(_world(), who={}, fwd=CLUB_IP).status_code == 401

    def test_forged_forwarded_for_is_not_the_club(self):
        # Our one proxy appended 198.51.100.7; the club's address in front of
        # it is the caller's own invention.
        r = start(_world(), fwd=f"{CLUB_IP}, 198.51.100.7")
        assert r.status_code == 403 and r.json()["detail"] == NOT_ALLOWED

    def test_joining_a_sheets_watch_is_a_start_under_the_same_rule(self, w):
        a = start(w).json()
        assert start(w, who=ALEX).status_code == 403
        r = start(w, who=ALEX, fwd=CLUB_IP)
        assert r.status_code == 201 and r.json()["watch_id"] == a["watch_id"]
        assert len(w["repo"].jobs) == 1

    def test_who_may_start_is_checked_before_the_league(self):
        w = _world()
        league_run(w)
        assert start(w).status_code == 403


class TestALeagueClosesPractice:
    def test_start_refused_while_a_league_is_live(self, w):
        league_run(w, status="live", sheet=2)
        r = start(w, sheet=4)
        assert r.status_code == 409 and r.json()["detail"] == LEAGUE_ON
        assert not w["repo"].jobs and not w["repo"].sessions_for_user("uid-sarah")

    def test_a_refusal_does_not_spend_the_rate_limit(self, w):
        league_run(w)
        for _ in range(30):
            assert start(w).status_code == 409
        w["repo"].update_run("r_league_2_live", status="ready")
        assert start(w).status_code == 201

    def test_the_league_closes_practice_to_the_club_ip_too(self):
        w = _world()
        league_run(w)
        assert start(w, fwd=CLUB_IP).status_code == 409


class TestClaiming:
    def test_a_practice_job_only_to_a_worker_with_cameras(self, w):
        start(w)
        r = w["client"].post("/api/worker/claim", headers=WORKER,
                             json=claim_body(cameras=False))
        assert r.status_code == 204
        job = claim(w)
        assert (job["kind"], job["source"], job["sheet"]) == ("practice", "cameras", 3)
        assert job["video_id"].startswith("cams3-")
        # Recorded from Start: there is no lookback to ask for.
        assert "lookback_s" not in job

    def test_a_worker_that_asks_only_for_recordings_never_gets_practice(self, w):
        start(w)
        r = w["client"].post("/api/worker/claim", headers=WORKER, json={
            "worker_id": "home", "model_id": "m-abc", "version": "2026.10.10", "gpu": "A2000"})
        assert r.status_code == 204

    def test_a_worker_without_cameras_still_takes_league_games(self, w):
        league_run(w, status="queued")
        w["repo"].put_job(Job(id="j_league", run_id="r_league_2_queued", state="queued",
                              created_at=T0, run_after=T0, kind="live"))
        assert claim(w, cameras=False)["id"] == "j_league"

    def test_a_youtube_practice_job_left_over_goes_only_to_a_camera_worker(self, w):
        # Queued before practice moved to the cameras, with a session still on
        # it. A camera worker gets it with no source, and refuses it
        # (live/manager.py); nothing else may claim it at all.
        w["repo"].put_run(Run(id="r_old", video_id=S3, processing_version="v",
                              status="queued", kind="practice", sheet=3, created_at=T0))
        w["repo"].put_job(Job(id="j_old", run_id="r_old", state="queued", created_at=T0,
                              run_after=T0, kind="practice"))
        w["repo"].put_watch(PracticeWatch(id="pw_old", sheet=3, video_id=S3, run_id="r_old",
                                          job_id="j_old", created_at=T0))
        w["repo"].put_session(PracticeSession(id="ps_old", user_id="uid-sarah", sheet=3,
                                              watch_id="pw_old", started_at=T0))
        r = w["client"].post("/api/worker/claim", headers=WORKER,
                             json=claim_body(cameras=False))
        assert r.status_code == 204
        job = claim(w)
        assert job["id"] == "j_old" and "source" not in job


class TestTheCap:
    def test_the_cameras_take_two_sheets_at_once_by_default(self, w):
        # The Ryzen's link carries about two sheets: a third would put every
        # camera into keyframe storms, the club's own receivers' too.
        for sheet in (1, 2):
            assert start(w, sheet=sheet).status_code == 201
        r = start(w, sheet=3)
        assert r.status_code == 409 and r.json()["detail"] == CAMERAS_FULL
        assert len(w["repo"].jobs) == 2
        # A sheet with no cameras is still a 404, full or not.
        assert start(w, sheet=6).status_code == 404

    def test_joining_a_sheet_already_practising_is_never_capped(self, w):
        a = start(w, sheet=1).json()
        start(w, sheet=2)
        r = start(w, who=ALEX, sheet=1, fwd=CLUB_IP)
        assert r.status_code == 201 and r.json()["watch_id"] == a["watch_id"]

    def test_a_sheet_frees_up_when_its_watch_ends(self, w):
        sid = start(w, sheet=1).json()["id"]
        start(w, sheet=2)
        job = claim(w)
        assert job["sheet"] == 1
        w["client"].post(f"/api/practice/sessions/{sid}/stop", headers=SARAH)
        # Stopped, the session still holds its cameras until the worker's
        # next heartbeat is told so and the watch completes.
        assert start(w, sheet=3).status_code == 409
        assert heartbeat(w, job)["stop"] is True
        w["client"].post(f"/api/worker/jobs/{job['id']}/complete", headers=WORKER, json={
            "worker_id": "ryzen", "practice": {"status": "ended"}})
        assert start(w, sheet=3).status_code == 201

    def test_the_cap_is_a_setting(self):
        w = _world(max_camera_watches=3)
        validate(w, "uid-sarah")
        for sheet in (1, 2, 3):
            assert start(w, sheet=sheet).status_code == 201
        r = start(w, sheet=4)
        assert r.status_code == 409 and r.json()["detail"] == CAMERAS_FULL.replace("2", "3")

    def test_a_full_refusal_does_not_spend_the_rate_limit(self, w):
        start(w, sheet=1)
        start(w, sheet=2)
        for _ in range(30):
            assert start(w, sheet=3).status_code == 409
        assert start(w, sheet=1).status_code == 201       # joining, the limit untouched


class TestAJobWhoseSessionsHaveEnded:
    """A practice job still queued when its last session ends -- Stop, idle,
    a league, or no worker to claim it -- is never handed out: a worker would
    read the cameras until its first heartbeat said stop, with nobody's
    session on."""

    def test_a_job_stopped_while_queued_is_not_claimed(self, w):
        sid = start(w).json()["id"]
        w["client"].post(f"/api/practice/sessions/{sid}/stop", headers=SARAH)
        r = w["client"].post("/api/worker/claim", headers=WORKER, json=claim_body())
        assert r.status_code == 204
        watch = w["repo"].get_watch(w["repo"].get_session(sid).watch_id)
        # Finished as a stopped watch completes, so the sheet starts afresh.
        assert w["repo"].get_job(watch.job_id).state == "done"
        assert watch.status == "ended"
        assert w["repo"].get_run(watch.run_id).status == "ready"
        assert w["repo"].get_session(sid).end_reason == "stop"
        assert start(w).json()["watch_id"] != watch.id

    def test_a_job_whose_session_went_idle_while_queued_is_not_claimed(self, w):
        # No worker claimed it for 15 minutes: the session has run out.
        sid = start(w).json()["id"]
        w["clock"].advance(901)
        r = w["client"].post("/api/worker/claim", headers=WORKER, json=claim_body())
        assert r.status_code == 204
        assert w["repo"].get_session(sid).end_reason == "idle"

    def test_the_next_queued_job_is_handed_out_instead(self, w):
        gone = start(w, sheet=1).json()["id"]
        start(w, sheet=2)
        w["client"].post(f"/api/practice/sessions/{gone}/stop", headers=SARAH)
        assert claim(w)["sheet"] == 2

    def test_a_job_with_a_session_still_on_is_handed_out(self, w):
        a = start(w).json()["id"]
        start(w, who=ALEX, fwd=CLUB_IP)              # joins: two sessions on one watch
        w["client"].post(f"/api/practice/sessions/{a}/stop", headers=SARAH)
        assert claim(w)["sheet"] == 3


def requeued(w, job):
    """The worker holding ``job`` is gone: its lease runs out, and the next
    claim requeues the job and hands it out again."""
    w["clock"].advance(job["lease_s"] + 1)
    again = claim(w)
    assert again["id"] == job["id"] and again["attempt"] == job["attempt"] + 1
    return again


class TestResumingAfterARestart:
    """A camera recording dies with its worker. The job comes back, and the
    watch that picks it up carries on from what was published."""

    def test_claim_resume_after_requeue(self, w):
        start(w)
        job = claim(w)
        assert "resume" not in job                     # a first claim starts afresh
        publish(w, job, doc([throw(60.0)], T0.timestamp()))
        again = requeued(w, job)
        assert again["resume"]["wall_t0"] == T0.timestamp()
        assert len(again["resume"]["throws"]) == 1
        assert again["resume"]["throws"][0]["id"] == "t_60.0"

    def test_a_watch_resumed_twice_stays_on_the_sessions_clock(self, w):
        # The last document is a resumed watch's: its recording began 600 s
        # into the session, so the session's clock began 600 s before its wall_t0.
        start(w)
        job = requeued(w, claim(w))
        publish(w, job, {**doc([throw(60.0), throw(612.0)], T0.timestamp() + 600.0),
                         "t0_s": 600.0})
        again = requeued(w, job)
        assert again["resume"]["wall_t0"] == T0.timestamp()
        assert [th["id"] for th in again["resume"]["throws"]] == ["t_60.0", "t_612.0"]

    def test_the_resumed_document_keeps_each_throw_in_the_session(self, w):
        # What a resumed watch publishes: this recording began at Start+600 s
        # (wall_t0), 600 s into the session's clock (t0_s). The earlier throw
        # keeps its moment and the new one is dated on the same clock.
        sid = start(w).json()["id"]
        job = requeued(w, claim(w))
        publish(w, job, {**doc([throw(60.0), throw(612.0)], T0.timestamp() + 600.0),
                         "t0_s": 600.0})
        w["clock"].advance(60)                      # read once both are thrown
        v = view(w, sid).json()
        assert [t["id"] for t in v["throws"]] == ["t_60.0", "t_612.0"]
        assert v["throws"][0]["released_at"].startswith("2026-09-11T12:01:00")
        assert v["throws"][1]["released_at"].startswith("2026-09-11T12:10:12")

    def test_a_requeued_job_with_nothing_published_starts_afresh(self, w):
        start(w)
        assert "resume" not in requeued(w, claim(w))


def release(w, worker="ryzen"):
    """What a worker says as it starts: hand back whatever my id held."""
    r = w["client"].post("/api/worker/release", headers=WORKER, json={"worker_id": worker})
    assert r.status_code == 200, r.text
    return r.json()


class TestARestartedWorkerHandsBackItsJobs:
    """A worker that restarts says so before its first claim, and what it held
    is queued again at once, as an expired lease would be: a camera watch
    resumes in seconds rather than after the lease's ten minutes."""

    def test_its_camera_watch_resumes_at_once(self, w):
        start(w)
        job = claim(w)
        publish(w, job, doc([throw(60.0)], T0.timestamp()))
        w["clock"].advance(30)                      # the lease has minutes to run
        assert release(w) == {"ok": True, "jobs": 1, "clip_jobs": 0}
        again = claim(w)
        assert again["id"] == job["id"] and again["attempt"] == 2
        assert again["resume"]["wall_t0"] == T0.timestamp()
        assert [th["id"] for th in again["resume"]["throws"]] == ["t_60.0"]

    def test_only_that_workers_jobs(self, w):
        start(w, sheet=1)
        start(w, sheet=2)
        mine = claim(w)
        theirs = claim(w, worker_id="ryzen-3080-2")
        assert release(w)["jobs"] == 1
        assert w["repo"].get_job(mine["id"]).state == "queued"
        held = w["repo"].get_job(theirs["id"])
        assert (held.state, held.worker_id) == ("running", "ryzen-3080-2")

    def test_a_worker_holding_nothing_hands_back_nothing(self, w):
        start(w)
        claim(w)
        assert release(w, "ryzen-3080-2") == {"ok": True, "jobs": 0, "clip_jobs": 0}

    def test_the_clips_workers_clip_jobs_too(self, w):
        w["repo"].put_run(Run(id="r_game", video_id="VID", processing_version="v",
                              status="ready", created_at=T0))
        w["repo"].put_clip_job(ClipJob(
            id="cj_1", play_id="p1", user_id="u1", source_id="s1", run_id="r_game",
            video_id="VID", state="running", created_at=T0, run_after=T0, attempts=1,
            worker_id="ryzen-clips", lease_expires_at=T0 + timedelta(seconds=600)))
        assert release(w, "ryzen-clips") == {"ok": True, "jobs": 0, "clip_jobs": 1}
        assert w["repo"].get_clip_job("cj_1").state == "queued"

    def test_only_a_worker_may_and_it_says_which(self, w):
        c = w["client"]
        assert c.post("/api/worker/release", json={"worker_id": "ryzen"}).status_code == 401
        assert c.post("/api/worker/release", headers=WORKER, json={}).status_code == 422


LEAGUE_LIVE = "10/24 - Sheet 2 - Thursday League"


def poll(w):
    """The scheduler's call: poll the watched playlists."""
    r = w["client"].post("/api/admin/poll-playlists", headers=ADMIN)
    assert r.status_code == 200, r.text
    return r.json()


def league_goes_live_on_sheet_2(w):
    w["yt"].add(VideoMeta("lg2sheet2001", LEAGUE_LIVE, CLUB, None, "live", T0))
    w["yt"]._playlists["PLthu"] = ["lg2sheet2001"]
    w["repo"].put_playlist(WatchedPlaylist(id="p_league", playlist_id="PLthu",
                                           label="Thursday League", created_at=T0))


class TestALeagueEndsPractice:
    """Leagues always use all five sheets: one going live on any sheet ends
    every practice session, within the standard playlist poll."""

    def sessions_on(self, w, sheets):
        validate(w, "uid-sarah")
        sids, jobs = [], []
        for sheet in sheets:
            sids.append(start(w, sheet=sheet).json()["id"])
            jobs.append(claim(w))
        return sids, jobs

    def test_league_live_ends_every_session(self):
        # Three sheets at once: the cameras' link as it will be at gigabit.
        w = _world(max_camera_watches=5)
        sids, jobs = self.sessions_on(w, (1, 3, 5))
        assert all(heartbeat(w, job).get("stop") is not True for job in jobs)
        league_goes_live_on_sheet_2(w)
        w["clock"].advance(120)
        assert len(poll(w)["live"]) == 1
        for sid in sids:
            s = w["repo"].get_session(sid)
            assert not s.active and s.end_reason == "league"
            assert s.ended_at == w["clock"].t
        # Each practice job's next heartbeat is told to stop.
        assert [heartbeat(w, job)["stop"] for job in jobs] == [True, True, True]

    def test_the_sessions_end_even_when_the_hourly_index_is_not_due(self):
        # The hourly index rides on a call in an hour's first three minutes;
        # at 12:10 the poll returns early, and the sessions still end.
        w = _world()
        sids, _ = self.sessions_on(w, (4,))
        league_goes_live_on_sheet_2(w)
        w["clock"].advance(60 * 10)
        assert poll(w)["index"] == "not due"
        assert w["repo"].get_session(sids[0]).end_reason == "league"

    def test_a_poll_that_finds_no_league_ends_nothing(self):
        w = _world()
        sids, jobs = self.sessions_on(w, (1, 3))
        w["repo"].put_playlist(WatchedPlaylist(id="p_league", playlist_id="PLthu",
                                               label="Thursday League", created_at=T0))
        w["yt"]._playlists["PLthu"] = []
        assert poll(w)["live"] == []
        assert all(w["repo"].get_session(sid).active for sid in sids)
        assert all(heartbeat(w, job).get("stop") is not True for job in jobs)

    def test_a_league_stream_already_followed_does_not_end_a_later_session(self):
        # The live run is made once per stream: the next poll finds nothing new,
        # so a session started afterwards (the league run is over) is left be.
        w = _world()
        league_goes_live_on_sheet_2(w)
        assert len(poll(w)["live"]) == 1
        run, = w["repo"].runs_for_video("lg2sheet2001")
        w["repo"].update_run(run.id, status="ready")
        sids, _ = self.sessions_on(w, (3,))
        w["clock"].advance(3600)                        # the playlist is due again
        assert poll(w)["live"] == []
        assert w["repo"].get_session(sids[0]).active

    def flaky_ending(self, monkeypatch, fails=1):
        """end_for_league failing its first ``fails`` calls; the calls made."""
        from curling_score.service import practice as practice_mod

        real, calls = practice_mod.end_for_league, []

        def ending(repo, now):
            calls.append(now)
            if len(calls) <= fails:
                raise RuntimeError("Firestore blinked")
            return real(repo, now)
        monkeypatch.setattr(practice_mod, "end_for_league", ending)
        return calls

    def test_a_failed_ending_is_tried_again_on_the_next_poll(self, monkeypatch, caplog):
        w = _world()
        sids, jobs = self.sessions_on(w, (1, 3))
        calls = self.flaky_ending(monkeypatch)
        league_goes_live_on_sheet_2(w)
        assert len(poll(w)["live"]) == 1                 # the poll goes on regardless
        assert "ending practice for a league failed: Firestore blinked" in caplog.text
        assert all(w["repo"].get_session(sid).active for sid in sids)
        assert claim(w)["kind"] == "live"               # a worker records the league
        w["clock"].advance(180)
        assert poll(w)["live"] == []                     # nothing new: the league is followed
        assert len(calls) == 2
        for sid in sids:
            assert w["repo"].get_session(sid).end_reason == "league"
        assert [heartbeat(w, job)["stop"] for job in jobs] == [True, True]

    def test_an_ending_that_keeps_failing_never_stops_the_poll(self, monkeypatch, caplog):
        w = _world()
        self.sessions_on(w, (1,))
        calls = self.flaky_ending(monkeypatch, fails=99)
        league_goes_live_on_sheet_2(w)
        poll(w)
        claim(w)
        for _ in range(3):
            w["clock"].advance(180)
            poll(w)
        assert len(calls) == 4
        assert caplog.text.count("ending practice for a league failed") == 4

    def test_the_retry_asks_nothing_while_no_practice_is_running(self, monkeypatch):
        w = _world()
        calls = self.flaky_ending(monkeypatch, fails=0)
        league_goes_live_on_sheet_2(w)
        poll(w)
        claim(w)                                         # the league is being recorded
        w["clock"].advance(180)
        poll(w)
        assert len(calls) == 1                           # only when the league went live

    def test_the_retry_asks_nothing_while_no_league_is_on(self, monkeypatch):
        w = _world()
        sids, _ = self.sessions_on(w, (1,))
        calls = self.flaky_ending(monkeypatch, fails=0)
        poll(w)
        assert calls == [] and w["repo"].get_session(sids[0]).active

    def test_a_league_left_unclosed_for_hours_ends_nothing(self, monkeypatch):
        # A live run never closed out is not a league on (LEAGUE_FRESH_S).
        w = _world()
        league_run(w, status="live", age_s=7 * 3600)
        sids, _ = self.sessions_on(w, (1,))
        poll(w)
        assert w["repo"].get_session(sids[0]).active


class TestThePrivacyPage:
    def test_privacy_page_names_the_cameras(self, w):
        text = " ".join(w["client"].get("/privacy").text.split())
        assert "reads the club's cameras directly" in text
        # Records, not reads: outside sessions the cameras are read briefly.
        assert "It records them only from when a player starts a session" in text
        for end in ("when the player stops it", "after 15 minutes without a throw",
                    "after three hours", "when a league game goes live",
                    "if the session fails"):
            assert end in text, end
        assert "kept as for games" in text
        assert "only measurements are kept" in text


class TestSettings:
    def test_practice_source_is_no_longer_read(self, monkeypatch):
        # Practice reads the cameras, whatever an old deploy still sets.
        for value in ("youtube", "camera"):
            monkeypatch.setenv("PRACTICE_SOURCE", value)
            got = Settings.from_env()
            assert not hasattr(got, "practice_source")

    def test_the_clubs_addresses_come_from_the_environment(self, monkeypatch):
        monkeypatch.delenv("PRACTICE_CLUB_IPS", raising=False)
        assert Settings.from_env().club_ips == frozenset()
        monkeypatch.setenv("PRACTICE_CLUB_IPS", "203.0.113.9, 198.51.100.7,")
        assert Settings.from_env().club_ips == frozenset({"203.0.113.9", "198.51.100.7"})

    def test_two_sheets_on_the_cameras_at_once_unless_told_otherwise(self, monkeypatch):
        monkeypatch.delenv("PRACTICE_MAX_CAMERA_WATCHES", raising=False)
        assert Settings.from_env().practice_max_camera_watches == 2
        monkeypatch.setenv("PRACTICE_MAX_CAMERA_WATCHES", "5")
        assert Settings.from_env().practice_max_camera_watches == 5


class TestTheWorkersSide:
    def test_published_throws_reach_the_session_in_its_window(self, w):
        # Alex's Start made the watch; Sarah joins a minute later, and sees
        # only the throws from her own Start on.
        start(w, who=ALEX, fwd=CLUB_IP)
        job = claim(w)
        w["clock"].advance(60)
        sid = start(w).json()["id"]
        got = publish(w, job, doc([throw(30.0), throw(120.0), throw(180.0)], T0.timestamp()))
        assert got["stop"] is False
        w["clock"].advance(240)                    # read five minutes in
        v = view(w, sid).json()
        assert [t["id"] for t in v["throws"]] == ["t_120.0", "t_180.0"]
        assert v["session"]["status"] == "active" and v["watch"]["status"] == "watching"
        assert v["throws"][0]["released_at"].startswith("2026-09-11T12:02:00")
        run = w["repo"].get_run(job["run_id"])
        assert w["store"].exists(practice_key(run.video_id, run.id))

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
        publish(w, job, doc([throw(60.0)], T0.timestamp()))
        w["clock"].advance(300)
        c = w["client"]
        r = c.patch(f"/api/practice/sessions/{sid}/throws/t_60.0", json={"hidden": True},
                    headers=SARAH)
        assert r.status_code == 200
        assert view(w, sid).json()["throws"][0]["hidden"] is True
        c.patch(f"/api/practice/sessions/{sid}/throws/t_60.0", json={"hidden": False},
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

    def test_fifteen_minutes_without_a_throw_ends_a_session(self, w):
        sid = start(w).json()["id"]
        job = claim(w)
        publish(w, job, doc([throw(60.0)], T0.timestamp()))
        w["clock"].t = T0 + timedelta(seconds=60 + 899)        # the throw was at Start+60
        assert heartbeat(w, job).get("stop") is not True
        w["clock"].t = T0 + timedelta(seconds=60 + 901)
        assert heartbeat(w, job)["stop"] is True
        assert w["repo"].get_session(sid).end_reason == "idle"

    def test_a_completed_watch_ends_its_sessions(self, w):
        sid = start(w).json()["id"]
        job = claim(w)
        publish(w, job, doc([throw(60.0)], T0.timestamp()))
        w["clock"].advance(300)
        r = w["client"].post(f"/api/worker/jobs/{job['id']}/complete", headers=WORKER, json={
            "worker_id": "ryzen", "practice": {"status": "ended", "recorded_s": 400.0,
                                               "throws": 1}, "sheet": 3})
        assert r.status_code == 200, r.text
        v = view(w, sid).json()
        assert v["session"]["end_reason"] == "stream_ended" and v["watch"]["status"] == "ended"
        assert [t["id"] for t in v["throws"]] == ["t_60.0"]

    def test_a_session_on_a_failed_watch_reads_as_failed(self, w):
        sid = start(w).json()["id"]
        job = claim(w)
        w["client"].post(f"/api/worker/jobs/{job['id']}/fail", headers=WORKER, json={
            "worker_id": "ryzen", "error": "dropped out", "kind": "permanent"})
        assert view(w, sid).json()["session"]["status"] == "failed"
        assert start(w).json()["watch_id"] != w["repo"].get_session(sid).watch_id


class TestWhenTheWorkerGivesUp:
    def test_a_failed_job_ends_its_sessions_and_frees_the_sheet(self, w):
        # The recorder dropped out (a LiveError) or the watch raised three
        # times: the worker fails the job and stops sending heartbeats.
        sid = start(w).json()["id"]
        job = claim(w)
        w["client"].post(f"/api/worker/jobs/{job['id']}/fail", headers=WORKER, json={
            "worker_id": "ryzen", "error": "the practice recording dropped out",
            "kind": "permanent"})
        got = w["client"].get("/api/me/practice", headers=SARAH).json()["sessions"]
        assert got[0]["active"] is False and got[0]["end_reason"] == "failed"
        assert w["repo"].get_watch(w["repo"].get_session(sid).watch_id).status == "failed"

    def test_a_session_whose_watch_died_unannounced_is_ended_when_listed(self, w):
        sid = start(w).json()["id"]
        watch = w["repo"].get_watch(w["repo"].get_session(sid).watch_id)
        w["repo"].update_job(watch.job_id, state="failed")      # no fail call reached us
        got = w["client"].get("/api/me/practice", headers=SARAH).json()["sessions"]
        assert got[0]["active"] is False


class TestHiding:
    def test_only_a_throw_id_can_be_hidden_and_the_list_is_bounded(self, w):
        sid = start(w).json()["id"]
        c = w["client"]
        bad = c.patch(f"/api/practice/sessions/{sid}/throws/{'x' * 200}",
                      json={"hidden": True}, headers=SARAH)
        assert bad.status_code == 422
        for i in range(520):
            c.patch(f"/api/practice/sessions/{sid}/throws/t_{i}.0", json={"hidden": True},
                    headers=SARAH)
        assert len(w["repo"].get_session(sid).hidden) == 500


class TestMine:
    def test_my_sessions_newest_first(self, w):
        a = start(w).json()["id"]
        w["clock"].t = T0 + timedelta(minutes=5)
        b = start(w, sheet=4).json()["id"]
        start(w, who=ALEX, fwd=CLUB_IP)
        got = w["client"].get("/api/me/practice", headers=SARAH).json()["sessions"]
        assert [s["id"] for s in got] == [b, a]
        assert got[0]["sheet"] == 4 and got[0]["active"] is True

    def test_each_session_counts_its_own_throws(self, w):
        # Two sessions on one watch, one after the other: each counts the
        # throws in its window that the page shows -- not a hidden one, and
        # not a stone placed by hand (arrived, nothing seen releasing it).
        a = start(w).json()["id"]
        job = claim(w)
        w["clock"].advance(200)
        w["client"].post(f"/api/practice/sessions/{a}/stop", headers=SARAH)
        b = start(w).json()["id"]
        placed = {**throw(200.0), "id": "t_200.0", "t_release_s": None,
                  "release_source": None}
        publish(w, job, doc([throw(60.0), throw(100.0), throw(220.0), throw(240.0), placed],
                            T0.timestamp()))
        w["clock"].advance(300)                    # read once they have all been thrown
        w["client"].patch(f"/api/practice/sessions/{b}/throws/t_240.0", json={"hidden": True},
                          headers=SARAH)
        got = {s["id"]: s for s in
               w["client"].get("/api/me/practice", headers=SARAH).json()["sessions"]}
        assert got[a]["throws"] == 2 and got[a]["active"] is False
        assert got[b]["throws"] == 1

    def test_a_session_with_nothing_published_yet_has_no_throws(self, w):
        start(w)
        (s,) = w["client"].get("/api/me/practice", headers=SARAH).json()["sessions"]
        assert s["throws"] == 0


class TestASessionFromTheYouTubeDays:
    """Practice once read a sheet's YouTube practice stream, recording from
    20 minutes before Start. Those sessions, their throws and their clips
    still read as they did."""

    def stored(self, w):
        repo = w["repo"]
        repo.put_run(Run(id="r_yt", video_id=S3, processing_version="v", status="ready",
                         kind="practice", sheet=3, created_at=T0))
        repo.put_job(Job(id="j_yt", run_id="r_yt", state="done", created_at=T0,
                         run_after=T0, kind="practice"))
        repo.put_watch(PracticeWatch(id="pw_yt", sheet=3, video_id=S3, run_id="r_yt",
                                     job_id="j_yt", created_at=T0, status="ended",
                                     revised_at=T0 + timedelta(minutes=30), throws=3))
        # Its recording's t=0 was 20 minutes before Start.
        d = {**doc([throw(1140.0), {**throw(1260.0), "clip": True}, throw(1320.0)],
                   T0.timestamp() - 1200.0, status="ended"), "since_s": 1200.0}
        w["store"].put_bytes(practice_key(S3, "r_yt"), json.dumps(d).encode())
        repo.put_session(PracticeSession(id="ps_yt", user_id="uid-sarah", sheet=3,
                                         watch_id="pw_yt", started_at=T0, hand="left",
                                         ended_at=T0 + timedelta(minutes=30),
                                         end_reason="stream_ended"))
        w["clock"].advance(24 * 3600)
        return "ps_yt"

    def test_it_reads_with_its_throws(self, w):
        v = view(w, self.stored(w)).json()
        assert [t["id"] for t in v["throws"]] == ["t_1260.0", "t_1320.0"]
        assert v["throws"][0]["released_at"].startswith("2026-09-11T12:01:00")
        assert (v["session"]["status"], v["session"]["end_reason"]) == ("ended", "stream_ended")

    def test_it_is_listed_with_its_count(self, w):
        sid = self.stored(w)
        (s,) = w["client"].get("/api/me/practice", headers=SARAH).json()["sessions"]
        assert (s["id"], s["throws"], s["active"]) == (sid, 2, False)

    def test_its_clip_still_plays(self, w):
        r = clip_url(w, self.stored(w), "t_1260.0")
        assert r.status_code == 200, r.text
        assert r.json() == {"url": "memory://" + clip_key(S3, "r_yt", "t_1260.0")}


class TestKeptApart:
    def test_a_practice_run_is_not_a_game(self, w):
        start(w)
        job = claim(w)
        games = w["client"].get("/api/games").json()
        assert all(g.get("video_id") != job["video_id"] for g in games.get("games", []))

    def test_the_league_poller_leaves_a_practice_playlist_alone(self, w):
        # A sheet's practice streams are never charted as league games.
        from curling_score.service import playlists

        got = playlists.poll(w["repo"], w["yt"], processing_version="v", now=T0,
                             force=True)
        assert got["live"] == [] and got["created"] == []

    def test_the_pages(self, w):
        c = w["client"]
        for path in ("/practice/sheet/3", "/practice", "/practice/session/ps_abc"):
            assert 'data-page="practice"' in c.get(path).text, path

    def test_the_pages_are_not_there_when_switched_off(self):
        c = _world(practice=False)["client"]
        for path in ("/practice/sheet/3", "/practice", "/practice/session/ps_abc"):
            assert c.get(path).status_code == 404, path

    def test_an_admin_can_mark_a_playlist_as_practice(self, w):
        r = w["client"].post("/api/admin/playlists", headers={"Authorization": "Bearer admin-secret"},
                             json={"playlist_id": "PLnew", "label": "More practice", "practice": True})
        assert r.status_code == 201, r.text
        assert next(p for p in w["repo"].list_playlists() if p.playlist_id == "PLnew").practice


def test_the_practice_document_has_a_key_of_its_own():
    assert practice_key("vid", "r_1") == "runs/vid/r_1/practice.json"


def clip_artifact(w, job, name, size=1000):
    return w["client"].post(f"/api/worker/jobs/{job['id']}/artifacts", headers=WORKER,
                            json={"worker_id": "ryzen", "detcache": [],
                                  "files": [{"name": name, "bytes": size}]})


class TestClipUploads:
    def test_a_practice_job_may_upload_a_throws_clip(self, w):
        start(w)
        job = claim(w)
        r = clip_artifact(w, job, "clips/t_60.0.mp4")
        assert r.status_code == 200, r.text
        (up,) = r.json()["uploads"]
        run = w["repo"].get_run(job["run_id"])
        assert up["key"] == clip_key(run.video_id, run.id, "t_60.0")
        assert up["key"] == f"runs/{run.video_id}/{run.id}/clips/t_60.0.mp4"
        assert up["headers"]["Content-Type"] == "video/mp4"

    @pytest.mark.parametrize("name", ["clips/../practice.json", "clips/t_1.mp4.exe",
                                      "t_60.0.mp4", "clips/x.mp4", "clips/t_60.0.mov"])
    def test_anything_else_is_refused(self, w, name):
        start(w)
        assert clip_artifact(w, claim(w), name).status_code == 400

    def test_a_clip_over_20_mb_is_refused(self, w):
        start(w)
        assert clip_artifact(w, claim(w), "clips/t_60.0.mp4",
                             size=20_000_001).status_code == 400

    def test_the_fallback_upload_takes_a_clip(self, w):
        start(w)
        job = claim(w)
        (up,) = clip_artifact(w, job, "clips/t_60.0.mp4").json()["uploads"]
        path = up["url"].split("chart.example", 1)[1]
        r = w["client"].put(path, content=b"mp4", headers={**WORKER, "Content-Type": "video/mp4"})
        assert r.status_code == 200, r.text
        assert w["store"].get_bytes(up["key"]) == b"mp4"


def clip_url(w, sid, tid, who=SARAH):
    return w["client"].get(f"/api/practice/sessions/{sid}/throws/{tid}/clip", headers=who)


class TestClipLinks:
    def published(self, w, clip=True):
        """Alex's Start made the watch; Sarah's, a minute later, joined it."""
        start(w, who=ALEX, fwd=CLUB_IP)
        job = claim(w)
        w["clock"].advance(60)
        sid = start(w).json()["id"]
        publish(w, job, doc([{**throw(30.0), "clip": clip}, {**throw(120.0), "clip": clip},
                             {**throw(180.0), "clip": False}], T0.timestamp()))
        w["clock"].advance(240)
        return sid, job

    def test_the_owner_gets_a_link_to_a_throws_clip(self, w):
        sid, job = self.published(w)
        r = clip_url(w, sid, "t_120.0")
        assert r.status_code == 200, r.text
        run = w["repo"].get_run(job["run_id"])
        assert r.json() == {"url": "memory://" + clip_key(run.video_id, run.id, "t_120.0")}

    def test_a_throw_without_a_clip_yet_is_a_404(self, w):
        sid, _ = self.published(w)
        assert clip_url(w, sid, "t_180.0").status_code == 404

    def test_a_throw_outside_the_sessions_window_is_a_404(self, w):
        sid, _ = self.published(w)
        assert clip_url(w, sid, "t_30.0").status_code == 404

    def test_someone_elses_session_is_a_404(self, w):
        sid, _ = self.published(w)
        assert clip_url(w, sid, "t_120.0", who=ALEX).status_code == 404

    def test_not_a_throw_is_a_404(self, w):
        sid, _ = self.published(w)
        assert clip_url(w, sid, "..%2Fpractice.json").status_code == 404
