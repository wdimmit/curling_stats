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
        self.lose = set()

    def claim(self, worker_id, model_id, gpu, kinds=None):
        self.claims.append(kinds)
        return self.jobs.pop(0) if self.jobs else None

    def progress(self, job_id, worker_id, phase, fraction, message):
        if job_id in self.lose:
            raise Lost("not yours")
        self.progress_calls.append((job_id, phase, message))

    def fail(self, job_id, worker_id, error, kind, retry_after_s=None):
        self.failed.append((job_id, kind, error))

    def artifacts(self, job_id, worker_id, files, detcache):
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


def manager(api, max_streams=6, fail=(), clock=None):
    t = [0.0]
    m = manager_mod.LiveManager(
        api, "home", model_id="m-abc", gpu=None, max_streams=max_streams,
        make_recorder=lambda job: Recorder(job, fail=job["id"] in fail),
        heartbeat_s=60.0, clock=clock or (lambda: t[0]))
    return m, t


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


class Session:
    """Builds an end whenever asked while one is due; done after ``ends``."""

    def __init__(self, stream, publish, due=None, ends=1, error=None):
        self.stream, self.publish, self.due = stream, publish, due
        self.ends, self.built, self.error, self.done = ends, 0, error, False

    def next_end_due(self):
        return self.due

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
            self._streams, self.finished = list(streams), []

        def streams(self):
            return list(self._streams)

        def has_streams(self):
            return bool(self._streams)

        def finish(self, s):
            self.finished.append(s.job["id"])
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
        assert m.finished == ["j_1"]

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
