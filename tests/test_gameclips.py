"""A play's clips on the API side (service/gameclips.py): which rocks, what
has come of them, queueing them once, and handing them to the clips worker."""

from datetime import datetime, timedelta, timezone

from curling_score.service import gameclips
from curling_score.service.records import ClipJob, Play, Run
from curling_score.service.repo import MemoryRepo

T = datetime(2026, 10, 10, 18, 0, tzinfo=timezone.utc)
CAL = {"left": {"rect": [0, 0, 810, 1080], "tee_row": 430.0, "hog_row": 520.0},
       "right": {"rect": [1110, 0, 810, 1080], "tee_row": 431.0, "hog_row": 522.0},
       "top": {"rect": [810, 0, 300, 540], "px_per_m": 50.0}}


def shot(n, color, slot, **kw):
    s = {"number": n, "color": color, "thrower_slot": slot, "missing": False,
         "t_release_s": 100.0 * n, "t_rest_s": 100.0 * n + 20, "t_enter_s": 100.0 * n + 13,
         "t_hog_s": None, "v_hog_m_s": None, "t_far_s": None,
         "target_broom": {"x": 0.3, "y": 0.0, "seen": 2.0, "confidence": 0.9},
         "line": {"start": {"x": 0.1, "y": 38.0}, "at_hog": {"x": 0.2, "offset_m": 0.0},
                  "at_broom": {"x": 0.35, "miss_m": 0.05}, "path": [[1.0, 2.0]],
                  "delivery": [[0.0, 38.0, 0.1]]},
         "stones": [], "track": [[1.0, 0.0, 1.0]]}
    s.update(kw)
    return s


def a_doc(shots, end_id=None):
    end = {"number": 2, "house": "bottom", "shots": shots}
    if end_id is not None:
        end["id"] = end_id
    return {"schema_version": 8, "calibration": CAL, "games": [{"index": 1, "ends": [end]}]}


def world(status="ready"):
    repo = MemoryRepo()
    run = Run(id="r1", video_id="VID", processing_version="p", status=status, created_at=T)
    repo.put_run(run)
    play = Play(id="p1", user_id="u1", source_id="s1", color="red", slot=4,
                created_at=T, updated_at=T)
    return repo, run, play


def queued(repo, rocks, *, state="queued", results=None, at=T, run_id="r1", id="cj_x"):
    repo.put_clip_job(ClipJob(id=id, play_id="p1", user_id="u1", source_id="s1",
                              run_id=run_id, video_id="VID", state=state, created_at=at,
                              run_after=at, spec={"rocks": [{"name": n} for n in rocks]},
                              rocks=results or {}))


class TestPlayerRocks:
    def test_the_players_rocks_with_what_a_clip_needs(self):
        doc = a_doc([shot(13, "red", 4), shot(14, "yellow", 4), shot(15, "red", 4),
                     shot(11, "red", 3)])
        got = gameclips.player_rocks(doc, "red", 4)
        assert [r["name"] for r in got] == ["1.2.13", "1.2.15"]
        assert got[0] == {"name": "1.2.13", "house": "bottom", "t_release_s": 1300.0,
                          "t_rest_s": 1320.0, "t_enter_s": 1313.0, "t_hog_s": None,
                          "v_hog_m_s": None, "t_far_s": None,
                          "target_broom": {"x": 0.3, "y": 0.0, "seen": 2.0, "confidence": 0.9},
                          "line": {"start": {"x": 0.1, "y": 38.0},
                                   "at_hog": {"x": 0.2, "offset_m": 0.0},
                                   "at_broom": {"x": 0.35, "miss_m": 0.05},
                                   "delivery": [[0.0, 38.0, 0.1]]}}
        shots = doc["games"][0]["ends"][0]["shots"]
        assert [s.get("rock") for s in shots] == ["1.2.13", None, "1.2.15", None]

    def test_a_rocks_release_survives_to_the_clip_record(self):
        # timeline -> rock_spec -> throw_from_rock: the hosted path the
        # burn-in reads the release from.
        from curling_score.game import clips
        doc = a_doc([shot(13, "red", 4)])
        doc["games"][0]["ends"][0]["shots"][0]["line"]["release"] = [1.23, 30.12, -0.142]
        rock = gameclips.find_rock(doc, "1.2.13")
        assert rock["line"]["release"] == [1.23, 30.12, -0.142]
        assert clips.throw_from_rock(rock)["line"]["release"] == [1.23, 30.12, -0.142]

    def test_a_rock_without_a_release_gives_none_in_the_clip_record(self):
        from curling_score.game import clips
        rock = gameclips.find_rock(a_doc([shot(13, "red", 4)]), "1.2.13")
        assert "release" not in rock["line"]
        assert clips.throw_from_rock(rock)["line"]["release"] is None

    def test_names_follow_the_detected_numbers(self):
        # Grading moved rock 15 ahead and a start trim renumbered the end:
        # the clip stays with the rock the run detected.
        doc = a_doc([shot(13, "red", 4, id=15), shot(15, "red", 4, id=13)], end_id=5)
        assert [r["name"] for r in gameclips.player_rocks(doc, "red", 4)] == ["1.5.15", "1.5.13"]

    def test_a_rock_never_seen_or_without_a_line(self):
        doc = a_doc([shot(13, "red", 4, missing=True), shot(15, "red", 4, line=None)])
        got = gameclips.player_rocks(doc, "red", 4)
        assert [(r["name"], r["line"]) for r in got] == [("1.2.15", None)]


class TestStateFor:
    def test_on_its_way_made_none_coming_and_failed(self):
        repo, run, _ = world()
        queued(repo, ["1.2.13"], state="done", id="cj_a",
               results={"1.2.13": {"clip": True, "overlay": [{"t0": 0.0, "t1": 1.0}]}})
        queued(repo, ["1.2.15"], state="running", id="cj_b", at=T + timedelta(seconds=1))
        queued(repo, ["1.2.11"], state="failed", id="cj_c", at=T + timedelta(seconds=2))
        queued(repo, ["1.2.9"], state="done", id="cj_d", at=T + timedelta(seconds=3),
               results={"1.2.9": {"clip": None, "reason": "video_gone"}})
        got = gameclips.state_for(repo, "p1", run)
        assert got == {"rocks": {"1.2.13": {"clip": True, "overlay": [{"t0": 0.0, "t1": 1.0}]},
                                 "1.2.15": {"clip": False},
                                 "1.2.11": {"clip": None, "reason": "cut_failed"},
                                 "1.2.9": {"clip": None, "reason": "video_gone"}},
                       "pending": True}

    def test_only_this_run_and_a_made_clip_stays_made(self):
        repo, run, _ = world()
        queued(repo, ["1.2.13"], state="done", id="cj_a", results={"1.2.13": {"clip": True}})
        queued(repo, ["1.2.13"], state="queued", id="cj_b", at=T + timedelta(seconds=1))
        queued(repo, ["1.2.15"], state="queued", id="cj_c", run_id="r0")
        assert gameclips.state_for(repo, "p1", run) == {"rocks": {"1.2.13": {"clip": True}},
                                                          "pending": False}

    def test_a_clip_cut_again_replaces_it_and_one_on_its_way_or_failed_does_not(self):
        repo, run, _ = world()
        queued(repo, ["1.2.13", "1.2.15", "1.2.11"], state="done", id="cj_a",
               results={n: {"clip": True, "overlay": [{"t0": 0.0, "t1": 1.0}]}
                        for n in ("1.2.13", "1.2.15", "1.2.11")})
        queued(repo, ["1.2.13"], state="done", id="cj_b", at=T + timedelta(seconds=1),
               results={"1.2.13": {"clip": True, "burned": True}})
        queued(repo, ["1.2.15"], state="running", id="cj_c", at=T + timedelta(seconds=2))
        queued(repo, ["1.2.11"], state="done", id="cj_d", at=T + timedelta(seconds=3),
               results={"1.2.11": {"clip": None, "reason": "video_gone"}})
        got = gameclips.state_for(repo, "p1", run)
        old = {"clip": True, "overlay": [{"t0": 0.0, "t1": 1.0}]}
        assert got == {"rocks": {"1.2.13": {"clip": True, "burned": True},
                                 "1.2.15": old, "1.2.11": old}, "pending": False}

    def test_jobs_from_before_the_run_was_analysed_again_do_not_count(self):
        repo, run, _ = world()
        queued(repo, ["1.2.13"], state="done", id="cj_old", results={"1.2.13": {"clip": True}})
        queued(repo, ["1.2.15"], state="queued", id="cj_new", at=T + timedelta(seconds=120))
        run.ready_at = T + timedelta(seconds=60)
        assert gameclips.state_for(repo, "p1", run) == {"rocks": {"1.2.15": {"clip": False}},
                                                         "pending": True}


class TestEnsure:
    def test_queues_the_rocks_once(self):
        repo, run, play = world()
        rocks = gameclips.player_rocks(a_doc([shot(13, "red", 4), shot(15, "red", 4)]), "red", 4)
        got = gameclips.ensure(repo, play, run, rocks, CAL, T)
        assert got == {"rocks": {"1.2.13": {"clip": False}, "1.2.15": {"clip": False}},
                       "pending": True}
        (job,) = repo.clip_jobs_for_play("p1")
        assert (job.state, job.run_id, job.video_id, job.user_id) == ("queued", "r1", "VID", "u1")
        assert set(job.spec["calibration"]) == {"left", "right", "top"}
        assert job.spec["calibration"]["top"] == {"rect": [810, 0, 300, 540], "px_per_m": 50.0}
        assert [r["name"] for r in job.spec["rocks"]] == ["1.2.13", "1.2.15"]
        gameclips.ensure(repo, play, run, rocks, CAL, T + timedelta(seconds=5))
        assert len(repo.clip_jobs_for_play("p1")) == 1

    def test_only_rocks_with_nothing_yet(self):
        repo, run, play = world()
        queued(repo, ["1.2.13"], state="done", results={"1.2.13": {"clip": True}})
        rocks = gameclips.player_rocks(a_doc([shot(13, "red", 4), shot(15, "red", 4)]), "red", 4)
        gameclips.ensure(repo, play, run, rocks, CAL, T)
        newest = repo.clip_jobs_for_play("p1")[-1]
        assert [r["name"] for r in newest.spec["rocks"]] == ["1.2.15"]

    def test_again_asks_for_clips_cut_in_an_older_style(self):
        repo, run, play = world()
        queued(repo, ["1.2.13", "1.2.15", "1.2.11"], state="done",
               results={"1.2.13": {"clip": True, "overlay": []},
                        "1.2.15": {"clip": True, "burned": True,
                                   "style": gameclips.CLIP_STYLE},
                        "1.2.11": {"clip": None, "reason": "video_gone"}})
        rocks = gameclips.player_rocks(a_doc([shot(n, "red", 4) for n in (11, 13, 15)]),
                                       "red", 4)
        gameclips.ensure(repo, play, run, rocks, CAL, T + timedelta(seconds=1))
        assert len(repo.clip_jobs_for_play("p1")) == 1
        got = gameclips.ensure(repo, play, run, rocks, CAL, T + timedelta(seconds=1), again=True)
        newest = repo.clip_jobs_for_play("p1")[-1]
        assert [r["name"] for r in newest.spec["rocks"]] == ["1.2.13"]
        # The old clip plays on until the new one is cut.
        assert got["rocks"]["1.2.13"] == {"clip": True, "overlay": []}


class TestClaim:
    def test_oldest_first_and_taken(self):
        repo, _, _ = world()
        queued(repo, ["1.2.13"], id="cj_b", at=T + timedelta(seconds=1))
        queued(repo, ["1.2.15"], id="cj_a")
        got = gameclips.claim(repo, "clips", T + timedelta(seconds=2), 600.0)
        assert (got.id, got.state, got.worker_id) == ("cj_a", "running", "clips")
        assert repo.get_run("r1").status == "ready"

    def test_a_live_run_waits(self):
        repo, _, _ = world(status="live")
        queued(repo, ["1.2.13"])
        assert gameclips.claim(repo, "clips", T, 600.0) is None
        job = repo.get_clip_job("cj_x")
        assert job.state == "queued"
        assert job.run_after == T + timedelta(seconds=gameclips.LIVE_RETRY_S)
        repo.update_run("r1", status="ready")
        assert gameclips.claim(repo, "clips", T + timedelta(seconds=301), 600.0).id == "cj_x"
        assert repo.get_run("r1").status == "ready"

    def test_a_job_from_before_a_re_analysis_fails_and_nothing_is_taken(self):
        repo, run, _ = world()
        queued(repo, ["1.2.13"])
        repo.update_run("r1", ready_at=T + timedelta(seconds=60))
        assert gameclips.claim(repo, "clips", T + timedelta(seconds=90), 600.0) is None
        job = repo.get_clip_job("cj_x")
        assert (job.state, job.finished_at) == ("failed", T + timedelta(seconds=90))
        assert "analysed again" in job.error

    def test_a_live_run_just_ready_waits_for_its_recording_to_be_kept(self):
        repo, _, _ = world()
        ready = T + timedelta(seconds=10)
        repo.update_run("r1", kind="live", ready_at=ready)
        queued(repo, ["1.2.13"], at=T + timedelta(seconds=20))
        assert gameclips.claim(repo, "clips", T + timedelta(seconds=30), 600.0) is None
        job = repo.get_clip_job("cj_x")
        assert (job.state, job.run_after) == (
            "queued", ready + timedelta(seconds=gameclips.LIVE_SETTLE_S))
        after = ready + timedelta(seconds=gameclips.LIVE_SETTLE_S)
        assert gameclips.claim(repo, "clips", after, 600.0).id == "cj_x"

    def test_a_vod_run_just_ready_is_taken_at_once(self):
        repo, _, _ = world()
        repo.update_run("r1", kind="vod", ready_at=T + timedelta(seconds=10))
        queued(repo, ["1.2.13"], at=T + timedelta(seconds=20))
        assert gameclips.claim(repo, "clips", T + timedelta(seconds=30), 600.0).id == "cj_x"

    def test_not_before_its_time(self):
        repo, _, _ = world()
        queued(repo, ["1.2.13"], at=T + timedelta(seconds=60))
        assert gameclips.claim(repo, "clips", T, 600.0) is None

    def test_a_run_that_is_gone_fails_its_jobs(self):
        repo, _, _ = world()
        queued(repo, ["1.2.13"], run_id="r_gone")
        assert gameclips.claim(repo, "clips", T, 600.0) is None
        assert repo.get_clip_job("cj_x").state == "failed"

    def test_a_lapsed_lease_goes_back_and_fails_at_the_last_attempt(self):
        repo, _, _ = world()
        queued(repo, ["1.2.13"])
        gameclips.claim(repo, "clips", T, 600.0)
        later = T + timedelta(seconds=601)
        assert gameclips.claim(repo, "clips", later, 600.0).attempts == 2
        repo.update_clip_job("cj_x", attempts=3, lease_expires_at=T)
        assert gameclips.claim(repo, "clips", later, 600.0) is None
        assert repo.get_clip_job("cj_x").state == "failed"


class TestRelease:
    """The clips worker restarted: what it held comes back as if its leases
    had run out, without waiting for them to."""

    def test_its_own_running_jobs_go_back(self):
        repo, _, _ = world()
        queued(repo, ["1.2.13"], id="cj_mine")
        queued(repo, ["1.2.15"], id="cj_theirs", at=T + timedelta(seconds=1))
        queued(repo, ["1.2.11"], id="cj_waiting", at=T + timedelta(seconds=2))
        gameclips.claim(repo, "clips", T + timedelta(seconds=3), 600.0)
        gameclips.claim(repo, "other", T + timedelta(seconds=3), 600.0)
        assert gameclips.release(repo, "clips", T + timedelta(seconds=4)) == 1
        mine = repo.get_clip_job("cj_mine")
        assert (mine.state, mine.worker_id, mine.lease_expires_at) == ("queued", None, None)
        assert mine.attempts == 1 and mine.run_after == T + timedelta(seconds=4)
        assert repo.get_clip_job("cj_theirs").state == "running"
        assert repo.get_clip_job("cj_waiting").state == "queued"
        # Back in the queue behind the one that was waiting, as an expired
        # lease's job would be (run_after is now).
        later = T + timedelta(seconds=5)
        assert gameclips.claim(repo, "clips", later, 600.0).id == "cj_waiting"
        again = gameclips.claim(repo, "clips", later, 600.0)
        assert (again.id, again.attempts) == ("cj_mine", 2)

    def test_at_its_last_attempt_it_fails(self):
        repo, _, _ = world()
        queued(repo, ["1.2.13"])
        gameclips.claim(repo, "clips", T, 600.0)
        repo.update_clip_job("cj_x", attempts=3)
        assert gameclips.release(repo, "clips", T) == 1
        assert repo.get_clip_job("cj_x").state == "failed"

    def test_nothing_held_is_nothing_released(self):
        repo, _, _ = world()
        queued(repo, ["1.2.13"])
        assert gameclips.release(repo, "clips", T) == 0
        assert repo.get_clip_job("cj_x").state == "queued"


class TestResults:
    def test_kept_to_the_rocks_asked_and_the_shapes_the_page_reads(self):
        job = ClipJob(id="cj_x", play_id="p1", user_id="u1", source_id="s1", run_id="r1",
                      video_id="VID", state="running", created_at=T, run_after=T,
                      spec={"rocks": [{"name": "1.2.13"}, {"name": "1.2.15"}, {"name": "1.2.9"},
                                      {"name": "1.2.7"}]})
        got = gameclips.clean_results(job, {
            "1.2.13": {"clip": True, "overlay": [{"t0": 0.0, "t1": 2.0}], "extra": 1},
            "1.2.15": {"clip": None, "reason": "video_gone"},
            "1.2.9": {"clip": True, "overlay": [{"t0": 0.0, "pts": "x" * 20_000}]},
            "1.2.7": {"clip": True, "burned": True},
            "1.2.99": {"clip": True}})
        assert got == {"1.2.13": {"clip": True, "overlay": [{"t0": 0.0, "t1": 2.0}]},
                       "1.2.15": {"clip": None, "reason": "video_gone"},
                       "1.2.9": {"clip": True},
                       "1.2.7": {"clip": True, "burned": True}}
        assert gameclips.clean_results(job, {"1.2.7": {"clip": True, "burned": "yes"}})[
            "1.2.7"] == {"clip": True}
        assert gameclips.clean_results(job, {"1.2.7": {"clip": True, "burned": True,
                                                       "style": 2}})["1.2.7"]["style"] == 2
        assert "style" not in gameclips.clean_results(job, {"1.2.7": {
            "clip": True, "style": "2"}})["1.2.7"]
        assert gameclips.clean_results(job, {"1.2.13": {"clip": None, "reason": "nonsense"}})[
            "1.2.13"] == {"clip": None, "reason": "cut_failed"}

    def test_a_failure_is_tried_again_then_given_up(self):
        repo, _, _ = world()
        queued(repo, ["1.2.13"])
        job = gameclips.claim(repo, "clips", T, 600.0)
        assert gameclips.fail(repo, job, "boom", T) is False
        again = repo.get_clip_job("cj_x")
        assert (again.state, again.run_after) == ("queued", T + timedelta(seconds=gameclips.RETRY_S))
        repo.update_clip_job("cj_x", attempts=3)
        assert gameclips.fail(repo, repo.get_clip_job("cj_x"), "boom", T) is True
        assert repo.get_clip_job("cj_x").state == "failed"


class TestResultsFromAnythingElse:
    def test_a_result_that_is_not_a_dict_fails_every_rock(self):
        job = ClipJob(id="cj_x", play_id="p1", user_id="u1", source_id="s1", run_id="r1",
                      video_id="VID", state="running", created_at=T, run_after=T,
                      spec={"rocks": [{"name": "1.2.13"}, {"name": "1.2.15"}]})
        for junk in (None, [], "no", 3):
            assert gameclips.clean_results(job, junk) == {
                "1.2.13": {"clip": None, "reason": "cut_failed"},
                "1.2.15": {"clip": None, "reason": "cut_failed"}}


class TestShotClips:
    """A clip of one shot, asked for from its page."""

    def test_a_rock_is_found_by_its_name_as_a_play_would_record_it(self):
        doc = a_doc([shot(13, "red", 4), shot(14, "yellow", 4, missing=True)])
        assert gameclips.find_rock(doc, "1.2.13") == gameclips.player_rocks(doc, "red", 4)[0]
        assert gameclips.find_rock(doc, "1.2.14") is None
        assert gameclips.find_rock(doc, "1.2.99") is None

    def test_where_it_stands_and_how_many_are_ahead(self):
        repo, run, _ = world()
        spec = gameclips.find_rock(a_doc([shot(13, "red", 4)]), "1.2.13")
        queued(repo, ["1.1.1"], state="running", id="cj_busy")
        queued(repo, ["1.1.2"], id="cj_batch")                  # a play's: after ours
        got = gameclips.ask_shot(repo, run, "s1", "u2", spec, CAL, T + timedelta(seconds=9))
        assert got == {"state": "queued", "ahead": 1}
        job = repo.get_clip_job(gameclips.shot_job_id("r1", "1.2.13"))
        assert set(job.spec["calibration"]) == {"left", "right", "top"}
        repo.update_clip_job(job.id, state="done", rocks={"1.2.13": {"clip": True}})
        assert gameclips.shot_state(repo, run, "1.2.13", T) == {"state": "ready"}

    def test_one_from_before_the_run_was_analysed_again_is_none(self):
        repo, run, _ = world()
        spec = gameclips.find_rock(a_doc([shot(13, "red", 4)]), "1.2.13")
        gameclips.ask_shot(repo, run, "s1", "u2", spec, CAL, T)
        run.ready_at = T + timedelta(seconds=60)
        assert gameclips.shot_state(repo, run, "1.2.13", T) == {"state": "none"}
        again = gameclips.ask_shot(repo, run, "s1", "u2", spec, CAL, T + timedelta(seconds=90))
        assert again["state"] == "queued"

    def test_while_live_a_shot_is_cut_at_once_and_a_plays_batch_waits(self):
        repo, run, _ = world(status="live")
        run.kind = "live"
        spec = gameclips.find_rock(a_doc([shot(13, "red", 4)]), "1.2.13")
        queued(repo, ["1.2.15"], id="cj_batch")
        gameclips.ask_shot(repo, run, "s1", "u2", spec, CAL, T + timedelta(seconds=1))
        shot_job = repo.get_clip_job(gameclips.shot_job_id("r1", "1.2.13"))
        assert shot_job.spec["live"] is True
        later = T + timedelta(seconds=2)
        assert gameclips.claim(repo, "clips", later, 600.0).id == shot_job.id
        assert gameclips.claim(repo, "clips", later, 600.0) is None
        assert repo.get_clip_job("cj_batch").run_after > later

    def test_kept_ten_days_from_the_runs_latest_stamp(self):
        _, run, _ = world()
        days = timedelta(days=gameclips.SHOT_CLIP_DAYS)
        assert not gameclips.shot_expired(run, T + days)
        assert gameclips.shot_expired(run, T + days + timedelta(seconds=1))
        run.ready_at = T + timedelta(days=3)
        assert not gameclips.shot_expired(run, T + days + timedelta(days=2))


class TestRecut:
    def test_a_clip_older_than_the_style_now(self):
        state = {"rocks": {"a": {"clip": True}, "b": {"clip": True, "burned": True},
                           "c": {"clip": True, "burned": True, "style": gameclips.CLIP_STYLE},
                           "d": {"clip": None, "reason": "video_gone"}, "e": {"clip": False}}}
        assert [n for n in "abcde" if gameclips.recut(state, n)] == ["a", "b"]


def test_a_clip_is_drawn_by_four_facts_of_each_panel():
    cal = {"left": {"rect": [0, 0, 1, 1], "tee_row": 1.0}, "top": {
        "rect": [1, 2, 3, 4], "center_px": [5, 6], "px_per_m": 7.0, "flipped": False,
        "residual_m": 0.01, "hog_line": {"columns": 9}}, "bottom": None}
    assert gameclips.clip_calibration(cal) == {
        "left": {"rect": [0, 0, 1, 1], "tee_row": 1.0},
        "top": {"rect": [1, 2, 3, 4], "center_px": [5, 6], "px_per_m": 7.0, "flipped": False}}
    assert gameclips.clip_calibration(None) == {}
