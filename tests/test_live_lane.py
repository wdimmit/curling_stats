"""The worker's live side: a manager thread that claims live jobs, records
them and keeps their leases, and the GPU lane that steps their sessions --
the end that has waited longest first -- and publishes what they build."""

import pytest

from curling_score.live import lane as lane_mod, manager as manager_mod
from curling_score.live.session import LiveError
from curling_score.service.worker import Lost


def live_job(n):
    return {"id": f"j_{n}", "run_id": f"r_{n}", "video_id": f"liveVid000{n}",
            "kind": "live", "format": "fours", "sheet": n, "title": f"Sheet {n}"}


class Api:
    def __init__(self, jobs=()):
        self.jobs = list(jobs)
        self.claims, self.progress_calls, self.failed = [], [], []
        self.uploads, self.published, self.completed = [], [], []
        self.artifact_files = []
        self.lose, self.stop = set(), set()

    def claim(self, worker_id, model_id, gpu, kinds=None):
        self.claims.append(kinds)
        return self.jobs.pop(0) if self.jobs else None

    def progress(self, job_id, worker_id, phase, fraction, message):
        if job_id in self.lose:
            raise Lost("not yours")
        self.progress_calls.append((job_id, phase, message))
        return {"stop": True} if job_id in self.stop else None

    def fail(self, job_id, worker_id, error, kind, retry_after_s=None):
        self.failed.append((job_id, kind, error))

    def artifacts(self, job_id, worker_id, files, detcache):
        self.artifact_files.append(files)
        return {"uploads": [{"name": "timeline.json", "url": f"memory://{job_id}",
                             "headers": {}}]}

    def upload(self, url, headers, data):
        self.uploads.append((url, data))

    def publish(self, job_id, worker_id, payload):
        if job_id in self.lose:
            raise Lost("not yours")
        self.published.append((job_id, payload))

    def complete(self, job_id, worker_id, payload):
        self.completed.append((job_id, payload))


class Recorder:
    def __init__(self, job, fail=False):
        self.job, self.fail = job, fail
        self.started = self.stopped = False
        self.checks = 0

    def start(self):
        if self.fail:
            raise LiveError("no DVR")
        self.started = True
        return self

    def check(self):
        self.checks += 1

    def stop(self):
        self.stopped = True


def manager(api, max_streams=6, fail=(), clock=None, kinds=("live",)):
    t = [0.0]
    m = manager_mod.LiveManager(
        api, "home", model_id="m-abc", gpu=None, max_streams=max_streams,
        make_recorder=lambda job: Recorder(job, fail=job["id"] in fail),
        heartbeat_s=60.0, clock=clock or (lambda: t[0]), kinds=kinds)
    return m, t


def practice_job(n):
    return {"id": f"p_{n}", "run_id": f"pr_{n}", "video_id": f"pracVid000{n}",
            "kind": "practice", "sheet": n, "title": f"Sheet {n} practice"}


class TestPracticeInTheManager:
    def test_it_claims_the_kinds_it_was_given(self):
        api = Api([practice_job(1)])
        m, _ = manager(api, kinds=("live", "practice"))
        m.poll_once()
        assert api.claims[0] == ["live", "practice"]
        assert m.streams()[0].job["kind"] == "practice"

    def test_a_stop_in_a_heartbeat_is_passed_on_to_the_stream(self):
        api = Api([practice_job(1)])
        m, t = manager(api, kinds=("live", "practice"))
        m.poll_once()
        assert not m.streams()[0].stop_requested
        api.stop.add("p_1")
        t[0] = 61.0
        m.poll_once()
        assert m.streams()[0].stop_requested

    def test_a_practice_job_is_recorded_from_its_lookback(self, monkeypatch, tmp_path):
        from curling_score.live import recorder

        made = {}
        monkeypatch.setattr(recorder, "YtDlpRecorder",
                            lambda vid, d, **kw: made.update(vid=vid, **kw) or "rec")
        m = manager_mod.LiveManager(Api(), "home", model_id="m", gpu=None, root=tmp_path)
        assert m._recorder(practice_job(1)) == "rec"
        assert made["vid"] == "pracVid0001" and made["lookback_s"] == 1200.0
        m._recorder(live_job(1))
        assert made["lookback_s"] is None


class TestTheManager:
    def test_it_claims_live_jobs_up_to_its_limit_and_records_them(self):
        api = Api([live_job(1), live_job(2), live_job(3)])
        m, _ = manager(api, max_streams=2)
        m.poll_once()
        assert [s.job["id"] for s in m.streams()] == ["j_1", "j_2"]
        assert all(s.recorder.started for s in m.streams())
        assert set(map(tuple, api.claims)) == {("live",)}

    def test_a_stream_that_cannot_be_recorded_fails_its_job(self):
        api = Api([live_job(1)])
        m, _ = manager(api, fail={"j_1"})
        m.poll_once()
        assert m.streams() == []
        assert api.failed == [("j_1", "permanent", "no DVR")]

    def test_it_keeps_every_live_lease_alive(self):
        api = Api([live_job(1), live_job(2)])
        m, t = manager(api)
        m.poll_once()
        api.progress_calls.clear()
        t[0] = 30.0
        m.poll_once()
        assert api.progress_calls == []
        t[0] = 61.0
        m.poll_once()
        assert sorted(c[0] for c in api.progress_calls) == ["j_1", "j_2"]
        assert all(c[1] == "live" for c in api.progress_calls)

    def test_a_stream_whose_job_was_taken_away_is_marked_lost(self):
        api = Api([live_job(1)])
        m, t = manager(api)
        m.poll_once()
        api.lose.add("j_1")
        t[0] = 61.0
        m.poll_once()
        (s,) = m.streams()
        assert s.lost

    def test_it_keeps_the_recorders_going(self):
        api = Api([live_job(1)])
        m, _ = manager(api)
        m.poll_once()
        m.poll_once()
        assert m.streams()[0].recorder.checks >= 1

    def test_finishing_a_stream_stops_its_recorder_and_lets_it_go(self):
        api = Api([live_job(1)])
        m, _ = manager(api)
        m.poll_once()
        (s,) = m.streams()
        m.finish(s)
        assert s.recorder.stopped and m.streams() == [] and not m.has_streams()


class TestKeepingTheRecording:
    """A whole recording goes into the video cache when its stream is let go,
    so reprocessing the game needs no download; anything else is deleted."""

    def _finish(self, tmp_path, monkeypatch, whole=True, keep=True, fails=False,
                no_file=False):
        kept, partial, pruned = [], [], []

        def keep_recording(path, vid, root):
            if fails:
                raise RuntimeError("ffmpeg fell over")
            kept.append((path, vid, root))
            return root / "videos" / f"{vid}.mp4"

        def keep_partial(path, vid, root):
            partial.append((path, vid, root))
            return root / "kept" / f"{vid}.ts"

        monkeypatch.setattr(manager_mod.cache, "keep_recording", keep_recording)
        monkeypatch.setattr(manager_mod.cache, "keep_partial", keep_partial)
        m = manager_mod.LiveManager(
            Api([live_job(1)]), "home", model_id="m-abc", gpu=None, root=tmp_path,
            make_recorder=lambda job: Recorder(job), prune=lambda: pruned.append(1),
            background=lambda fn: fn())
        m.poll_once()
        (s,) = m.streams()
        directory = tmp_path / "live" / "liveVid0001"
        directory.mkdir(parents=True)
        (directory / "rec.0.ts").write_bytes(b"ts")
        s.recorder.whole = lambda: whole
        # This file's Recorder double has no `path` until a test gives it one,
        # so leaving it unset stands for a recorder that never wrote a file.
        # (The real recorder raises IndexError there; finish catches both.)
        if not no_file:
            s.recorder.path = directory / "rec.0.ts"
        m.finish(s, keep=keep)
        assert s.recorder.stopped and m.streams() == []
        return s, kept, partial, pruned, directory

    def test_a_whole_recording_is_kept_then_the_cache_pruned(self, tmp_path, monkeypatch):
        _, kept, partial, pruned, directory = self._finish(tmp_path, monkeypatch)
        assert kept == [(directory / "rec.0.ts", "liveVid0001", tmp_path)]
        assert partial == [] and pruned == [1] and not directory.exists()

    def test_a_recording_that_is_not_whole_is_kept_as_a_partial(self, tmp_path, monkeypatch):
        _, kept, partial, pruned, directory = self._finish(tmp_path, monkeypatch, whole=False)
        assert kept == [] and partial == [(directory / "rec.0.ts", "liveVid0001", tmp_path)]
        assert pruned == [1] and not directory.exists()

    def test_a_recording_turned_down_is_deleted(self, tmp_path, monkeypatch):
        _, kept, partial, _, directory = self._finish(tmp_path, monkeypatch, keep=False)
        assert kept == [] and partial == [] and not directory.exists()

    def test_a_recording_that_cannot_be_kept_is_still_deleted(self, tmp_path, monkeypatch):
        _, _, _, pruned, directory = self._finish(tmp_path, monkeypatch, fails=True)
        assert not directory.exists() and pruned == [1]

    def test_a_recorder_that_never_wrote_a_file_keeps_nothing(self, tmp_path, monkeypatch):
        _, kept, partial, pruned, directory = self._finish(tmp_path, monkeypatch,
                                                           no_file=True)
        assert kept == [] and partial == [] and pruned == [] and not directory.exists()


class Session:
    """Builds an end whenever asked while one is due; done after ``ends``."""

    def __init__(self, stream, publish, due=None, ends=1, error=None):
        self.stream, self.publish, self.due = stream, publish, due
        self.ends, self.built, self.error, self.done = ends, 0, error, False

    def next_end_due(self):
        return self.due

    def needs_calibration(self):
        return False

    def step(self):
        if self.error:
            raise self.error
        if self.built >= self.ends:
            self.done = True
            self.publish(doc(self.built, final=True))
            return "finished"
        self.built += 1
        self.publish(doc(self.built))
        return "end"


def doc(n, final=False):
    return {"games": [{"index": 0, "start_s": 600.0, "end_s": 600.0 + 900 * n,
                       "ends": [{}] * n}],
            "live": {"in_progress": not final, "recorded_s": 900.0 * n + 300}}


def lane(api, streams, sessions):
    class M:
        def __init__(self):
            self._streams, self.finished, self.kept = list(streams), [], []

        def streams(self):
            return list(self._streams)

        def has_streams(self):
            return bool(self._streams)

        def finish(self, s, keep=True):
            self.finished.append(s.job["id"])
            self.kept.append(keep)
            self._streams.remove(s)

    m = M()
    made = []

    def make_session(stream, publish):
        made.append(stream.job["id"])
        return sessions[stream.job["id"]](stream, publish)

    return lane_mod.LiveLane(m, api, "home", make_session=make_session), m, made


def stream(n):
    return manager_mod.Stream(job=live_job(n), recorder=Recorder(live_job(n)))


class TestTheLane:
    def test_the_end_that_has_waited_longest_is_built_first(self):
        api = Api()
        s1, s2 = stream(1), stream(2)
        ln, _, made = lane(api, [s1, s2], {
            "j_1": lambda st, pub: Session(st, pub, due=900.0, ends=5),
            "j_2": lambda st, pub: Session(st, pub, due=600.0, ends=5)})
        assert ln.step()
        assert [p[0] for p in api.published] == ["j_2"]
        assert sorted(made) == ["j_1", "j_2"]

    def test_a_publish_uploads_the_timeline_and_posts_its_games(self):
        api = Api()
        ln, _, _ = lane(api, [stream(1)], {"j_1": lambda st, pub: Session(st, pub, ends=5)})
        ln.step()
        (url, data), = api.uploads
        assert url == "memory://j_1" and b'"games"' in data
        (job_id, payload), = api.published
        assert payload["games"] == [{"index": 0, "start_s": 600.0, "end_s": 1500.0, "ends": 1}]
        assert payload["title"] == "Sheet 1" and payload["sheet"] == 1

    def test_a_finished_session_completes_its_job_and_is_let_go(self):
        api = Api()
        ln, m, _ = lane(api, [stream(1)], {"j_1": lambda st, pub: Session(st, pub, ends=1)})
        while ln.busy():
            ln.step()
        (job_id, payload), = api.completed
        assert job_id == "j_1" and payload["format"] == "fours"
        assert payload["games"][0]["ends"] == 1
        assert m.finished == ["j_1"] and m.kept == [True]

    def test_a_sheet_nobody_played_does_not_keep_its_recording(self):
        class Empty(Session):
            def step(self):
                self.done = True
                self.publish({"games": [], "live": {"in_progress": False,
                                                    "recorded_s": 5400.0}})
                return "finished"

        api = Api()
        ln, m, _ = lane(api, [stream(1)], {"j_1": lambda st, pub: Empty(st, pub)})
        while ln.busy():
            ln.step()
        assert [c[0] for c in api.completed] == ["j_1"] and m.kept == [False]

    def test_a_session_that_cannot_go_on_fails_its_job(self):
        api = Api()
        ln, m, _ = lane(api, [stream(1)], {
            "j_1": lambda st, pub: Session(st, pub, error=LiveError("no calibration"))})
        ln.step()
        assert api.failed == [("j_1", "permanent", "no calibration")]
        assert m.finished == ["j_1"]

    def test_a_lost_job_is_let_go_without_completing(self):
        api = Api()
        api.lose.add("j_1")
        ln, m, _ = lane(api, [stream(1)], {"j_1": lambda st, pub: Session(st, pub, ends=3)})
        ln.step()
        assert m.finished == ["j_1"] and api.completed == []

    def test_a_stream_marked_lost_by_the_manager_is_let_go(self):
        api = Api()
        s = stream(1)
        s.lost = True
        ln, m, _ = lane(api, [s], {"j_1": lambda st, pub: Session(st, pub, ends=3)})
        ln.step()
        assert m.finished == ["j_1"] and api.published == []

    def test_nothing_ready_is_a_step_that_did_nothing(self):
        class Idle(Session):
            def step(self):
                return None

        api = Api()
        ln, _, _ = lane(api, [stream(1)], {"j_1": lambda st, pub: Idle(st, pub)})
        assert ln.step() is False


class TestVideoSessions:
    def test_each_stream_gets_a_session_and_the_models_load_once(self, monkeypatch):
        from curling_score import analyze
        from curling_score.live import session as live

        loads = []
        monkeypatch.setattr(analyze, "load_models",
                            lambda *a, **k: loads.append(1) or ("det", "broom", "line"))
        make = lane_mod.video_sessions("w.pt", progress=lambda m: None)
        s1, s2 = stream(1), stream(2)
        s2.job["format"] = "doubles"
        a, b = make(s1, lambda d: None), make(s2, lambda d: None)
        assert isinstance(a, live.LiveSession) and a.recording is s1.recorder
        assert (a.fmt.name, b.fmt.name) == ("fours", "doubles")
        assert a.models.detector == "det" and b.models is a.models
        assert a.url == "https://www.youtube.com/watch?v=liveVid0001"
        assert loads == [1]


def test_a_recording_that_cannot_go_on_fails_its_job_and_is_let_go():
    api = Api([live_job(1)])
    m, _ = manager(api)
    m.poll_once()
    (s,) = m.streams()

    def broken():
        raise LiveError("window moved on")

    s.recorder.check = broken
    m.poll_once()
    assert api.failed == [("j_1", "permanent", "window moved on")]
    assert s.lost


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def lane_with_clock(api, streams, sessions):
    ln, m, made = lane(api, streams, sessions)
    ln.clock = Clock()
    return ln, m, made


class TestTheLaneKeepsGoing:
    def test_one_streams_unexpected_error_stops_neither_the_lane_nor_the_others(self):
        class Broken(Session):
            def step(self):
                raise RuntimeError("an analysis bug in this end")

        api = Api()
        s1, s2 = stream(1), stream(2)
        ln, m, _ = lane_with_clock(api, [s1, s2], {
            "j_1": lambda st, pub: Broken(st, pub, due=600.0),
            "j_2": lambda st, pub: Session(st, pub, due=900.0, ends=5)})
        ln.step()                                  # j_1 fails once, backs off
        ln.step()                                  # j_2 goes meanwhile
        assert [p[0] for p in api.published] == ["j_2"]
        assert api.failed == []
        for _ in range(lane_mod.MAX_FAILURES):
            ln.clock.t += lane_mod.RETRY_S + 1
            ln.step()
        assert ("j_1", "permanent") in [(f[0], f[1]) for f in api.failed]
        assert "j_1" in m.finished and "j_2" not in m.finished

    def test_an_error_while_asking_what_is_due_is_contained_to_its_stream(self):
        class Wedged(Session):
            def next_end_due(self):
                raise LiveError("recording cannot resume")

        api = Api()
        ln, m, _ = lane_with_clock(api, [stream(1)],
                                   {"j_1": lambda st, pub: Wedged(st, pub)})
        ln.step()
        assert api.failed == [("j_1", "permanent", "recording cannot resume")]
        assert m.finished == ["j_1"]

    def test_a_publish_that_fails_is_sent_again_without_rebuilding_the_end(self):
        class Flaky(Api):
            fails = 1

            def publish(self, job_id, worker_id, payload):
                if self.fails:
                    self.fails -= 1
                    raise ConnectionError("503")
                super().publish(job_id, worker_id, payload)

        api = Flaky()
        sessions = {}

        def make(st, pub):
            sessions[st.job["id"]] = Session(st, pub, due=600.0, ends=5)
            return sessions["j_1"]

        ln, _, _ = lane_with_clock(api, [stream(1)], {"j_1": make})
        ln.step()                                  # builds end 1; publish fails
        assert api.published == [] and sessions["j_1"].built == 1
        ln.clock.t += lane_mod.RETRY_S + 1
        ln.step()                                  # sends it again first
        assert [len(p[1]["games"]) for p in api.published][:1] == [1]
        assert api.published[0][1]["games"][0]["ends"] == 1

    def test_completion_that_fails_is_tried_again_before_letting_go(self):
        class Flaky(Api):
            fails = 1

            def complete(self, job_id, worker_id, payload):
                if self.fails:
                    self.fails -= 1
                    raise ConnectionError("503")
                super().complete(job_id, worker_id, payload)

        api = Flaky()
        ln, m, _ = lane_with_clock(api, [stream(1)],
                                   {"j_1": lambda st, pub: Session(st, pub, ends=1)})
        for _ in range(3):
            ln.step()
        assert api.completed == [] and m.finished == []
        ln.clock.t += lane_mod.RETRY_S + 1
        while ln.busy():
            ln.step()
            ln.clock.t += lane_mod.RETRY_S + 1
        assert [c[0] for c in api.completed] == ["j_1"] and m.finished == ["j_1"]

    def test_streams_with_nothing_due_take_turns(self):
        class Busy(Session):
            def step(self):
                self.built += 1
                return "calibrated"

        api = Api()
        made = {}

        def factory(n):
            def make(st, pub):
                made[n] = Busy(st, pub)
                return made[n]
            return make

        ln, _, _ = lane_with_clock(api, [stream(1), stream(2)],
                                   {"j_1": factory(1), "j_2": factory(2)})
        for _ in range(4):
            ln.step()
        assert (made[1].built, made[2].built) == (2, 2)

    def test_the_sheet_comes_from_the_title_when_the_job_has_none(self):
        api = Api()
        s = stream(1)
        s.job["sheet"], s.job["title"] = None, "10/1 - Sheet 3 - Thursday League"
        ln, _, _ = lane_with_clock(api, [s], {"j_1": lambda st, pub: Session(st, pub, ends=5)})
        ln.step()
        assert api.published[0][1]["sheet"] == 3



def test_a_stream_waiting_for_its_first_calibration_goes_before_any_end():
    # Found in the rehearsal: with ends always due elsewhere, a stream with no
    # calibration -- so nothing due of its own -- never got a turn.
    class Uncalibrated(Session):
        calibrated = False

        def needs_calibration(self):
            return not self.calibrated

        def step(self):
            self.calibrated = True
            return "calibrated"

    api = Api()
    made = {}

    def uncal(st, pub):
        made["j_2"] = Uncalibrated(st, pub)
        return made["j_2"]

    ln, _, _ = lane_with_clock(api, [stream(1), stream(2)], {
        "j_1": lambda st, pub: Session(st, pub, due=600.0, ends=9),
        "j_2": uncal})
    ln.step()
    assert made["j_2"].calibrated and api.published == []


class Watch:
    """A practice watch: does work while it has some, ends when told or out of it."""

    def __init__(self, stream, publish, work=3):
        self.stream, self.publish, self.work = stream, publish, work
        self.done, self.stops, self.steps = False, 0, 0

    def request_stop(self):
        self.stops += 1
        self.work = 0

    def step(self):
        self.steps += 1
        if self.work > 0:
            self.work -= 1
            self.publish(pdoc(3 - self.work))
            return True
        self.done = True
        self.publish(pdoc(3, status="ended"))
        return True


def pdoc(n, status="watching"):
    return {"practice": 1, "status": status, "t0_s": 0.0, "wall_t0": 1.7e9,
            "since_s": 1200.0, "recorded_s": 1300.0 + n, "throws": [{"id": f"t_{n}"}] * n}


def pstream(n):
    return manager_mod.Stream(job=practice_job(n), recorder=Recorder(practice_job(n)))


class TestPracticeInTheLane:
    def test_practice_goes_before_an_end_that_is_due(self):
        api = Api()
        ln, _, _ = lane(api, [stream(1), pstream(1)], {
            "j_1": lambda st, pub: Session(st, pub, due=600.0, ends=5),
            "p_1": lambda st, pub: Watch(st, pub)})
        ln.step()
        assert [p[0] for p in api.published] == ["p_1"]

    def test_with_nothing_to_do_a_practice_watch_lets_a_due_end_be_built(self):
        class Idle(Watch):
            def step(self):
                self.steps += 1
                return False

        api = Api()
        ln, _, _ = lane(api, [pstream(1), stream(1)], {
            "j_1": lambda st, pub: Session(st, pub, due=600.0, ends=5),
            "p_1": lambda st, pub: Idle(st, pub)})
        assert ln.step()
        assert [p[0] for p in api.published] == ["j_1"]

    def test_a_practice_publish_is_its_document_and_a_summary(self):
        api = Api()
        ln, _, _ = lane(api, [pstream(1)], {"p_1": lambda st, pub: Watch(st, pub)})
        ln.step()
        assert api.artifact_files[-1] == [{"name": "practice.json",
                                           "bytes": len(api.uploads[-1][1])}]
        (job_id, payload), = api.published
        assert job_id == "p_1"
        assert payload == {"practice": {"status": "watching", "recorded_s": 1301.0,
                                        "throws": 1}, "sheet": 1}

    def test_a_stop_from_the_api_reaches_the_watch_and_it_completes(self):
        api = Api()
        s = pstream(1)
        ln, m, _ = lane(api, [s], {"p_1": lambda st, pub: Watch(st, pub)})
        s.stop_requested = True
        for _ in range(20):
            if not ln.busy():
                break
            ln.step()
        assert s.session.stops >= 1
        (job_id, payload), = api.completed
        assert job_id == "p_1" and payload["practice"]["status"] == "ended"
        assert m.finished == ["p_1"] and m.kept == [False]

    def test_a_practice_job_gets_a_watch_sharing_the_models(self, monkeypatch):
        from curling_score import analyze
        from curling_score.practice.watch import LOOKBACK_S, PracticeWatch

        monkeypatch.setattr(analyze, "load_models", lambda *a, **k: ("det", "broom", "line"))
        make = lane_mod.video_sessions("w.pt", progress=lambda m: None)
        ps = pstream(1)
        live, practice = make(stream(1), lambda d: None), make(ps, lambda d: None)
        assert isinstance(practice, PracticeWatch)
        assert practice.rec is ps.recorder
        assert practice.since_s == LOOKBACK_S and practice.models is live.models
        assert practice.wall_t0 is not None
