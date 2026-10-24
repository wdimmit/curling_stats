"""Practice sessions through the API: start, join, read, stop, hide -- and the
worker's side of a practice job, as phase 2's worker drives it."""

import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from curling_score.service.api import Settings, create_app
from curling_score.service.auth import FakeVerifier
from curling_score.service.records import Job, Run, WatchedPlaylist
from curling_score.service.repo import MemoryRepo
from curling_score.service.store import MemoryStore, clip_key, practice_key
from curling_score.service.youtube import FakeYouTube, VideoMeta
from tests.test_service_api import ADMIN, CLUB, T0, WORKER, Clock

SARAH = {"Authorization": "Bearer tok-sarah"}
ALEX = {"Authorization": "Bearer tok-alex"}
S3, S4, UP = "qJhDHMMtHsE", "dXYqdopieoU", "IqiFhMEOL4s"
CLUB_IP = "203.0.113.9"


def _world(practice=True, cameras=False, live=False):
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
                        practice_enabled=practice, live_enabled=live,
                        **({"practice_source": "cameras", "club_ips": frozenset({CLUB_IP})}
                           if cameras else {}))
    app = create_app(repo, store, yt, settings, now=clock, auth=auth)
    return {"client": TestClient(app), "repo": repo, "store": store, "yt": yt,
            "clock": clock}


@pytest.fixture
def w():
    return _world()


@pytest.fixture
def cams():
    """The club's cameras are the practice source: no YouTube stream is needed,
    and Start is for validated accounts or the club's own address."""
    return _world(cameras=True)


def start(w, sheet=3, who=SARAH, hand="right", fwd=None):
    """``fwd`` is the X-Forwarded-For the request arrives with."""
    return w["client"].post("/api/practice/sessions", json={"sheet": sheet, "hand": hand},
                            headers={**who, **({"X-Forwarded-For": fwd} if fwd else {})})


def claim_body(**extra):
    return {"worker_id": "ryzen", "model_id": "m-abc", "version": "2026.10.10", "gpu": "3080",
            "kinds": ["live", "practice"], **extra}


def claim(w, **extra):
    r = w["client"].post("/api/worker/claim", headers=WORKER, json=claim_body(**extra))
    assert r.status_code == 200, r.text
    return r.json()["job"]


def validate(w, uid, role="validated"):
    """Make ``uid`` a validated account. An account exists once it has been
    seen, so it is seen first."""
    who = {"uid-sarah": SARAH, "uid-alex": ALEX}[uid]
    w["client"].get("/api/me", headers=who)
    w["repo"].update_user(uid, role=role)


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


NOT_ALLOWED = "Practice on the club's cameras needs a validated account, or the club's Wi-Fi."
LEAGUE_ON = "A league game is on; practice opens when it ends."


def league_run(w, status="live", sheet=2, age_s=60.0, kind="live"):
    w["repo"].put_run(Run(id=f"r_league_{sheet}_{status}", video_id=f"league{sheet}",
                          processing_version="v", status=status, kind=kind, sheet=sheet,
                          created_at=w["clock"].t - timedelta(seconds=age_s)))


class TestOnTheClubsCameras:
    def test_camera_start_needs_validated_or_club_ip(self, cams):
        r = start(cams)
        assert r.status_code == 403 and r.json()["detail"] == NOT_ALLOWED
        validate(cams, "uid-sarah")
        assert start(cams).status_code == 201
        # Alex is not validated: the club's own address stands in for that.
        assert start(cams, who=ALEX, sheet=4).status_code == 403
        assert start(cams, who=ALEX, sheet=4, fwd=CLUB_IP).status_code == 201

    def test_an_admin_may_start(self, cams):
        validate(cams, "uid-sarah", role="admin")
        assert start(cams).status_code == 201

    def test_the_club_ip_still_needs_a_signed_in_user(self, cams):
        assert start(cams, who={}, fwd=CLUB_IP).status_code == 401

    def test_forged_forwarded_for_is_not_the_club(self, cams):
        # Our one proxy appended 198.51.100.7; the club's address in front of
        # it is the caller's own invention.
        r = start(cams, fwd=f"{CLUB_IP}, 198.51.100.7")
        assert r.status_code == 403 and r.json()["detail"] == NOT_ALLOWED

    def test_camera_start_needs_no_youtube_stream(self, cams):
        # Sheet 1 is "upcoming" on YouTube and sheet 5 has no stream at all.
        validate(cams, "uid-sarah")
        for sheet in (1, 5):
            r = start(cams, sheet=sheet)
            assert r.status_code == 201, r.text
            watch = cams["repo"].get_watch(cams["repo"].get_session(r.json()["id"]).watch_id)
            run = cams["repo"].get_run(watch.run_id)
            assert run.source == "cameras" and run.kind == "practice"
            assert run.video_id.startswith(f"cams{sheet}-") and watch.video_id == run.video_id
            assert cams["repo"].get_job(watch.job_id).kind == "practice"

    def test_a_sheet_the_club_has_no_cameras_on_is_a_404(self, cams):
        validate(cams, "uid-sarah")
        r = start(cams, sheet=6)
        assert r.status_code == 404 and "sheet 6" in r.json()["detail"]
        assert not cams["repo"].jobs

    def test_a_camera_start_never_asks_youtube(self, cams, monkeypatch):
        def boom(*a, **k):
            raise AssertionError("YouTube was asked")
        for name in ("playlist_video_ids", "videos"):
            monkeypatch.setattr(cams["yt"], name, boom)
        validate(cams, "uid-sarah")
        assert start(cams).status_code == 201

    def test_start_refused_while_a_league_is_live(self, cams):
        validate(cams, "uid-sarah")
        league_run(cams, status="live", sheet=2)
        r = start(cams, sheet=4)
        assert r.status_code == 409 and r.json()["detail"] == LEAGUE_ON
        assert not cams["repo"].jobs and not cams["repo"].sessions_for_user("uid-sarah")

    def test_a_refusal_does_not_spend_the_rate_limit(self, cams):
        validate(cams, "uid-sarah")
        league_run(cams)
        for _ in range(30):
            assert start(cams).status_code == 409
        cams["repo"].update_run("r_league_2_live", status="ready")
        assert start(cams).status_code == 201

    def test_the_league_closes_practice_to_the_club_ip_too(self, cams):
        league_run(cams)
        assert start(cams, fwd=CLUB_IP).status_code == 409

    def test_who_may_start_is_checked_before_the_league(self, cams):
        league_run(cams)
        assert start(cams).status_code == 403

    def test_second_player_joins_same_camera_watch_under_same_rule(self, cams):
        validate(cams, "uid-sarah")
        a = start(cams).json()
        assert start(cams, who=ALEX).status_code == 403     # joining is a Start
        r = start(cams, who=ALEX, fwd=CLUB_IP)
        assert r.status_code == 201 and r.json()["watch_id"] == a["watch_id"]
        assert len(cams["repo"].jobs) == 1

    def test_camera_practice_job_only_to_a_worker_with_cameras(self, cams):
        validate(cams, "uid-sarah")
        start(cams)
        r = cams["client"].post("/api/worker/claim", headers=WORKER, json=claim_body())
        assert r.status_code == 204
        job = claim(cams, cameras=True)
        assert (job["kind"], job["source"], job["lookback_s"], job["sheet"]) == (
            "practice", "cameras", 0, 3)
        assert job["video_id"].startswith("cams3-")

    def test_a_camera_watch_publishes_and_is_read_like_any_other(self, cams):
        validate(cams, "uid-sarah")
        sid = start(cams).json()["id"]
        job = claim(cams, cameras=True)
        # The recording starts at Start: its t=0 is when Start was tapped.
        got = publish(cams, job, doc([throw(60.0), throw(120.0)], T0.timestamp()))
        assert got["stop"] is False
        cams["clock"].advance(300)
        v = view(cams, sid).json()
        assert [t["id"] for t in v["throws"]] == ["t_60.0", "t_120.0"]
        run = cams["repo"].get_run(job["run_id"])
        assert cams["store"].exists(practice_key(run.video_id, run.id))

    def test_a_worker_without_cameras_still_takes_league_games(self, cams):
        league_run(cams, status="queued")
        cams["repo"].put_job(Job(id="j_league", run_id="r_league_2_queued", state="queued",
                                 created_at=T0, run_after=T0, kind="live"))
        assert claim(cams)["id"] == "j_league"

    def test_a_practice_job_from_before_the_switch_waits_for_a_camera_worker_too(self, cams):
        """Camera mode holds all practice back from a worker that cannot see
        the cameras, a job left over from the YouTube days included."""
        cams["repo"].put_run(Run(id="r_old", video_id=S3, processing_version="v",
                                 status="queued", kind="practice", sheet=3, created_at=T0))
        cams["repo"].put_job(Job(id="j_old", run_id="r_old", state="queued", created_at=T0,
                                 run_after=T0, kind="practice"))
        r = cams["client"].post("/api/worker/claim", headers=WORKER, json=claim_body())
        assert r.status_code == 204


def requeued(w, job, cameras=True):
    """The worker holding ``job`` is gone: its lease runs out, and the next
    claim requeues the job and hands it out again."""
    w["clock"].advance(job["lease_s"] + 1)
    again = claim(w, **({"cameras": True} if cameras else {}))
    assert again["id"] == job["id"] and again["attempt"] == job["attempt"] + 1
    return again


class TestResumingAfterARestart:
    """A camera recording dies with its worker. The job comes back, and the
    watch that picks it up carries on from what was published."""

    def test_claim_resume_after_requeue(self, cams):
        validate(cams, "uid-sarah")
        start(cams)
        job = claim(cams, cameras=True)
        assert "resume" not in job                     # a first claim starts afresh
        publish(cams, job, doc([throw(60.0)], T0.timestamp()))
        again = requeued(cams, job)
        assert again["resume"]["wall_t0"] == T0.timestamp()
        assert len(again["resume"]["throws"]) == 1
        assert again["resume"]["throws"][0]["id"] == "t_60.0"

    def test_a_watch_resumed_twice_stays_on_the_sessions_clock(self, cams):
        # The last document is a resumed watch's: its recording began 600 s
        # into the session, so the session's clock began 600 s before its wall_t0.
        validate(cams, "uid-sarah")
        start(cams)
        job = requeued(cams, claim(cams, cameras=True))
        publish(cams, job, {**doc([throw(60.0), throw(612.0)], T0.timestamp() + 600.0),
                            "t0_s": 600.0})
        again = requeued(cams, job)
        assert again["resume"]["wall_t0"] == T0.timestamp()
        assert [th["id"] for th in again["resume"]["throws"]] == ["t_60.0", "t_612.0"]

    def test_the_resumed_document_keeps_each_throw_in_the_session(self, cams):
        # What a resumed watch publishes: this recording began at Start+600 s
        # (wall_t0), 600 s into the session's clock (t0_s). The earlier throw
        # keeps its moment and the new one is dated on the same clock.
        validate(cams, "uid-sarah")
        sid = start(cams).json()["id"]
        job = requeued(cams, claim(cams, cameras=True))
        publish(cams, job, {**doc([throw(60.0), throw(612.0)], T0.timestamp() + 600.0),
                            "t0_s": 600.0})
        cams["clock"].advance(60)                      # read once both are thrown
        v = view(cams, sid).json()
        assert [t["id"] for t in v["throws"]] == ["t_60.0", "t_612.0"]
        assert v["throws"][0]["released_at"].startswith("2026-09-11T12:01:00")
        assert v["throws"][1]["released_at"].startswith("2026-09-11T12:10:12")

    def test_a_requeued_job_with_nothing_published_starts_afresh(self, cams):
        validate(cams, "uid-sarah")
        start(cams)
        assert "resume" not in requeued(cams, claim(cams, cameras=True))

    def test_a_requeued_youtube_watch_records_its_lookback_as_before(self, w):
        start(w)
        job = claim(w)
        publish(w, job, doc([throw(1260.0)], T0.timestamp() - 1200.0))
        again = requeued(w, job, cameras=False)
        assert "resume" not in again and again["lookback_s"] == 1200.0


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
            jobs.append(claim(w, cameras=True))
        return sids, jobs

    def test_league_live_ends_every_session(self):
        w = _world(cameras=True, live=True)
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
        w = _world(cameras=True, live=True)
        sids, _ = self.sessions_on(w, (4,))
        league_goes_live_on_sheet_2(w)
        w["clock"].advance(60 * 10)
        assert poll(w)["index"] == "not due"
        assert w["repo"].get_session(sids[0]).end_reason == "league"

    def test_a_poll_that_finds_no_league_ends_nothing(self):
        w = _world(cameras=True, live=True)
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
        w = _world(cameras=True, live=True)
        league_goes_live_on_sheet_2(w)
        assert len(poll(w)["live"]) == 1
        run, = w["repo"].runs_for_video("lg2sheet2001")
        w["repo"].update_run(run.id, status="ready")
        sids, _ = self.sessions_on(w, (3,))
        w["clock"].advance(3600)                        # the playlist is due again
        assert poll(w)["live"] == []
        assert w["repo"].get_session(sids[0]).active

    def test_it_ends_youtube_practice_too(self):
        w = _world(live=True)
        sid = start(w).json()["id"]
        job = claim(w)
        league_goes_live_on_sheet_2(w)
        assert len(poll(w)["live"]) == 1
        assert w["repo"].get_session(sid).end_reason == "league"
        assert heartbeat(w, job)["stop"] is True


class TestThePrivacyPage:
    def test_privacy_page_names_the_cameras(self, w):
        text = " ".join(w["client"].get("/privacy").text.split())
        assert "reads the club's cameras directly" in text
        assert "only from when a player starts a session" in text
        assert "kept as for games" in text
        assert "only measurements are kept" in text


class TestSettings:
    def test_practice_reads_youtube_unless_told_otherwise(self, monkeypatch):
        monkeypatch.delenv("PRACTICE_SOURCE", raising=False)
        monkeypatch.delenv("PRACTICE_CLUB_IPS", raising=False)
        got = Settings.from_env()
        assert got.practice_source == "youtube" and got.club_ips == frozenset()

    def test_the_cameras_and_the_clubs_addresses_come_from_the_environment(self, monkeypatch):
        monkeypatch.setenv("PRACTICE_SOURCE", "cameras")
        monkeypatch.setenv("PRACTICE_CLUB_IPS", "203.0.113.9, 198.51.100.7,")
        got = Settings.from_env()
        assert got.practice_source == "cameras"
        assert got.club_ips == frozenset({"203.0.113.9", "198.51.100.7"})

    def test_a_misspelt_source_stops_the_service_rather_than_being_ignored(self, monkeypatch):
        monkeypatch.setenv("PRACTICE_SOURCE", "camera")
        with pytest.raises(ValueError, match="PRACTICE_SOURCE"):
            Settings.from_env()


class TestTheLeagueGateInYouTubeMode:
    def test_a_league_closes_youtube_practice_too(self, w):
        league_run(w)
        r = start(w)
        assert r.status_code == 409 and r.json()["detail"] == LEAGUE_ON

    def test_youtube_practice_needs_no_validated_account(self, w):
        assert start(w).status_code == 201

    def test_a_youtube_job_has_no_source_and_keeps_its_lookback(self, w):
        start(w)
        job = claim(w)
        assert job["lookback_s"] == 1200.0 and "source" not in job


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

    def test_fifteen_minutes_without_a_throw_ends_a_session(self, w):
        sid = start(w).json()["id"]
        job = claim(w)
        publish(w, job, doc([throw(1260.0)], T0.timestamp() - 1200.0))
        w["clock"].t = T0 + timedelta(seconds=60 + 899)        # the throw was at Start+60
        assert heartbeat(w, job).get("stop") is not True
        w["clock"].t = T0 + timedelta(seconds=60 + 901)
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
        start(w, who=ALEX)
        got = w["client"].get("/api/me/practice", headers=SARAH).json()["sessions"]
        assert [s["id"] for s in got] == [b, a]
        assert got[0]["sheet"] == 4 and got[0]["active"] is True

    def test_each_session_counts_its_own_throws(self, w):
        # Two sessions on one watch, one after the other: each counts the
        # throws in its window that the page shows -- not a hidden one, and
        # not a stone placed by hand (arrived, nothing seen releasing it).
        a = start(w).json()["id"]
        job = claim(w)
        wall_t0 = T0.timestamp() - 1200.0
        w["clock"].advance(200)
        w["client"].post(f"/api/practice/sessions/{a}/stop", headers=SARAH)
        b = start(w).json()["id"]
        placed = {**throw(1400.0), "id": "t_1400.0", "t_release_s": None,
                  "release_source": None}
        publish(w, job, doc([throw(1140.0), throw(1260.0), throw(1300.0), throw(1420.0),
                             throw(1440.0), placed], wall_t0))
        w["clock"].advance(300)                    # read once they have all been thrown
        w["client"].patch(f"/api/practice/sessions/{b}/throws/t_1440.0", json={"hidden": True},
                          headers=SARAH)
        got = {s["id"]: s for s in
               w["client"].get("/api/me/practice", headers=SARAH).json()["sessions"]}
        assert got[a]["throws"] == 2 and got[a]["active"] is False
        assert got[b]["throws"] == 1

    def test_a_session_with_nothing_published_yet_has_no_throws(self, w):
        start(w)
        (s,) = w["client"].get("/api/me/practice", headers=SARAH).json()["sessions"]
        assert s["throws"] == 0


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
        for path in ("/practice/sheet/3", "/practice", "/practice/session/ps_abc"):
            assert 'data-page="practice"' in c.get(path).text, path
        assert c.get("/api/features").json()["practice"] is True

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
        r = clip_artifact(w, job, "clips/t_1260.0.mp4")
        assert r.status_code == 200, r.text
        (up,) = r.json()["uploads"]
        run = w["repo"].get_run(job["run_id"])
        assert up["key"] == clip_key(run.video_id, run.id, "t_1260.0")
        assert up["key"] == f"runs/{S3}/{run.id}/clips/t_1260.0.mp4"
        assert up["headers"]["Content-Type"] == "video/mp4"

    @pytest.mark.parametrize("name", ["clips/../practice.json", "clips/t_1.mp4.exe",
                                      "t_1260.0.mp4", "clips/x.mp4", "clips/t_1260.0.mov"])
    def test_anything_else_is_refused(self, w, name):
        start(w)
        assert clip_artifact(w, claim(w), name).status_code == 400

    def test_a_clip_over_20_mb_is_refused(self, w):
        start(w)
        assert clip_artifact(w, claim(w), "clips/t_1260.0.mp4",
                             size=20_000_001).status_code == 400

    def test_the_fallback_upload_takes_a_clip(self, w):
        start(w)
        job = claim(w)
        (up,) = clip_artifact(w, job, "clips/t_1260.0.mp4").json()["uploads"]
        path = up["url"].split("chart.example", 1)[1]
        r = w["client"].put(path, content=b"mp4", headers={**WORKER, "Content-Type": "video/mp4"})
        assert r.status_code == 200, r.text
        assert w["store"].get_bytes(up["key"]) == b"mp4"


def clip_url(w, sid, tid, who=SARAH):
    return w["client"].get(f"/api/practice/sessions/{sid}/throws/{tid}/clip", headers=who)


class TestClipLinks:
    def published(self, w, clip=True):
        sid = start(w).json()["id"]
        job = claim(w)
        wall_t0 = T0.timestamp() - 1200.0
        publish(w, job, doc([{**throw(1140.0), "clip": clip}, {**throw(1260.0), "clip": clip},
                             {**throw(1320.0), "clip": False}], wall_t0))
        w["clock"].advance(300)
        return sid, job

    def test_the_owner_gets_a_link_to_a_throws_clip(self, w):
        sid, job = self.published(w)
        r = clip_url(w, sid, "t_1260.0")
        assert r.status_code == 200, r.text
        run = w["repo"].get_run(job["run_id"])
        assert r.json() == {"url": "memory://" + clip_key(run.video_id, run.id, "t_1260.0")}

    def test_a_throw_without_a_clip_yet_is_a_404(self, w):
        sid, _ = self.published(w)
        assert clip_url(w, sid, "t_1320.0").status_code == 404

    def test_a_throw_outside_the_sessions_window_is_a_404(self, w):
        sid, _ = self.published(w)
        assert clip_url(w, sid, "t_1140.0").status_code == 404

    def test_someone_elses_session_is_a_404(self, w):
        sid, _ = self.published(w)
        assert clip_url(w, sid, "t_1260.0", who=ALEX).status_code == 404

    def test_not_a_throw_is_a_404(self, w):
        sid, _ = self.published(w)
        assert clip_url(w, sid, "..%2Fpractice.json").status_code == 404
