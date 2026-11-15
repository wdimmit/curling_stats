"""The worker's live side: a manager thread that claims live jobs, records
them and keeps their leases, and the GPU lane that steps their sessions --
the end that has waited longest first -- and publishes what they build."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from curling_score.live import lane as lane_mod, manager as manager_mod
from curling_score.live.session import LiveError
from curling_score.service.worker import Lost


@pytest.fixture(autouse=True)
def no_hand_model(monkeypatch):
    """The tests here stub `analyze.load_models` with a line model, and the
    lane loads the hand model with it: kept from loading the real network
    once its weights are in the repo."""
    from curling_score.detect import releasemodel
    monkeypatch.setattr(releasemodel, "default_model", lambda: None)


def live_job(n):
    return {"id": f"j_{n}", "run_id": f"r_{n}", "video_id": f"liveVid000{n}",
            "kind": "live", "format": "fours", "sheet": n, "title": f"Sheet {n}"}


class Api:
    def __init__(self, jobs=()):
        self.jobs = list(jobs)
        self.claims, self.progress_calls, self.failed = [], [], []
        self.camera_claims = []
        self.uploads, self.published, self.completed = [], [], []
        self.artifact_files = []
        self.lose, self.stop = set(), set()
        self.games = {}       # job id -> the game a standby's heartbeat hands it
        self.extras = []      # (job id, the heartbeat's extra fields)

    def claim(self, worker_id, model_id, gpu, kinds=None, cameras=False):
        self.claims.append(kinds)
        self.camera_claims.append(cameras)
        return self.jobs.pop(0) if self.jobs else None

    def progress(self, job_id, worker_id, phase, fraction, message, extra=None):
        if job_id in self.lose:
            raise Lost("not yours")
        self.progress_calls.append((job_id, phase, message))
        if extra:
            self.extras.append((job_id, extra))
        if job_id in self.games:
            return {"ok": True, "game": self.games[job_id]}
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


def manager(api, max_streams=6, fail=(), clock=None, kinds=("live",), **kw):
    t = [0.0]
    m = manager_mod.LiveManager(
        api, "home", model_id="m-abc", gpu=None, max_streams=max_streams,
        make_recorder=lambda job: Recorder(job, fail=job["id"] in fail),
        heartbeat_s=60.0, clock=clock or (lambda: t[0]), kinds=kinds, **kw)
    return m, t


def practice_job(n, **extra):
    """A practice watch on sheet ``n``'s own cameras, as the API hands it out."""
    return {"id": f"p_{n}", "run_id": f"pr_{n}", "video_id": f"cams{n}-20261024-153000",
            "kind": "practice", "source": "cameras", "sheet": n, "title": None,
            "attempt": 1, **extra}


class TestPracticeInTheManager:
    def test_it_claims_the_kinds_it_was_given(self):
        api = Api([practice_job(1)])
        m, _ = manager(api, kinds=("live", "practice"))
        m.poll_once()
        assert api.claims[0] == ["live", "practice"]
        assert m.streams()[0].job["kind"] == "practice"

    def test_it_says_it_can_see_the_cameras_only_when_it_can(self):
        for cameras in (False, True):
            api = Api()
            m, _ = manager(api, kinds=("live", "practice"), cameras=cameras)
            m.poll_once()
            assert api.camera_claims == [cameras]

    def test_a_stop_in_a_heartbeat_is_passed_on_to_the_stream(self):
        api = Api([practice_job(1)])
        m, t = manager(api, kinds=("live", "practice"))
        m.poll_once()
        assert not m.streams()[0].stop_requested
        api.stop.add("p_1")
        t[0] = 61.0
        m.poll_once()
        assert m.streams()[0].stop_requested

    def test_a_league_game_is_still_recorded_from_youtube(self, monkeypatch, tmp_path):
        from curling_score.live import recorder

        made = {}
        monkeypatch.setattr(recorder, "YtDlpRecorder",
                            lambda vid, d, **kw: made.update(vid=vid, **kw) or "rec")
        m = manager_mod.LiveManager(Api(), "home", model_id="m", gpu=None, root=tmp_path)
        assert m._recorder(live_job(1)) == "rec"
        assert made["vid"] == "liveVid0001"

    def test_camera_job_gets_camera_recorder(self, monkeypatch, tmp_path):
        # The sheet's own cameras, never YouTube, recorded where finish() clears:
        # a practice recording is never kept.
        from curling_score.cameras import recorder as cameras
        from curling_score.live import recorder

        made = []

        class Spy(Recorder):
            def __init__(self, sheet, directory, **kw):
                super().__init__(None)
                made.append((sheet, directory, kw))
                Path(directory).mkdir(parents=True)
                (Path(directory) / "rec.0.ts").write_bytes(b"ts")

        def youtube(*a, **k):
            raise AssertionError("YouTube was asked")

        monkeypatch.setattr(cameras, "CameraRecorder", Spy)
        monkeypatch.setattr(recorder, "YtDlpRecorder", youtube)
        api = Api([practice_job(3)])
        m = manager_mod.LiveManager(api, "ryzen", model_id="m", gpu=None, root=tmp_path,
                                    kinds=("live", "practice"), cameras=True)
        m.poll_once()
        (s,) = m.streams()
        directory = tmp_path / "live" / "cams3-20261024-153000"
        assert made == [(3, directory, {})] and s.recorder.started
        m.finish(s)
        assert s.recorder.stopped and not directory.exists()


class TestTheManager:
    def test_it_claims_live_jobs_up_to_its_limit_and_records_them(self):
        api = Api([live_job(1), live_job(2), live_job(3)])
        m, _ = manager(api, max_streams=2)
        for _ in range(3):                  # one a poll
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
                no_file=False, job=None, on_kept=None):
        job = job or live_job(1)
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
            Api([job]), "home", model_id="m-abc", gpu=None, root=tmp_path,
            make_recorder=lambda job: Recorder(job), prune=lambda: pruned.append(1),
            background=lambda fn: fn(), kinds=("live", "practice"))
        m.poll_once()
        (s,) = m.streams()
        directory = tmp_path / "live" / job["video_id"]
        directory.mkdir(parents=True)
        (directory / "rec.0.ts").write_bytes(b"ts")
        s.recorder.whole = lambda: whole
        # This file's Recorder double has no `path` until a test gives it one,
        # so leaving it unset stands for a recorder that never wrote a file.
        # (The real recorder raises IndexError there; finish catches both.)
        if not no_file:
            s.recorder.path = directory / "rec.0.ts"
        if on_kept is None:
            m.finish(s, keep=keep)
        else:
            m.finish(s, keep=keep, on_kept=on_kept)
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

    def test_a_practice_recording_is_never_kept_however_it_ends(self, tmp_path, monkeypatch):
        # Lost, failed or finished: a window on a sheet's all-day stream is not
        # a game, and a dropout two hours in would file gigabytes under kept/.
        _, kept, partial, _, directory = self._finish(tmp_path, monkeypatch, whole=False,
                                                      job=practice_job(1))
        assert kept == [] and partial == [] and not directory.exists()

    def test_a_recorder_that_never_wrote_a_file_keeps_nothing(self, tmp_path, monkeypatch):
        _, kept, partial, pruned, directory = self._finish(tmp_path, monkeypatch,
                                                           no_file=True)
        assert kept == [] and partial == [] and pruned == [] and not directory.exists()

    def test_frames_are_saved_from_the_kept_file_before_the_recording_goes(
            self, tmp_path, monkeypatch):
        seen = []

        def on_kept(video):
            seen.append((video, (tmp_path / "live" / "liveVid0001").exists()))

        self._finish(tmp_path, monkeypatch, on_kept=on_kept)
        assert seen == [(tmp_path / "videos" / "liveVid0001.mp4", True)]

    def test_frames_are_cut_from_the_recording_when_keeping_it_failed(
            self, tmp_path, monkeypatch):
        seen = []

        def on_kept(video):
            seen.append((video, video.exists()))

        _, _, _, pruned, directory = self._finish(tmp_path, monkeypatch, fails=True,
                                                  on_kept=on_kept)
        assert seen == [(directory / "rec.0.ts", True)]
        assert not directory.exists() and pruned == [1]

    def test_a_raising_frame_save_still_deletes_the_recording(self, tmp_path, monkeypatch):
        def on_kept(video):
            raise RuntimeError("disk full")

        _, _, _, pruned, directory = self._finish(tmp_path, monkeypatch, on_kept=on_kept)
        assert not directory.exists() and pruned == [1]

    def test_a_sheet_nobody_played_saves_no_frames(self, tmp_path, monkeypatch):
        seen = []
        self._finish(tmp_path, monkeypatch, keep=False, on_kept=seen.append)
        assert seen == []

    def test_a_recorder_that_never_wrote_a_file_saves_no_frames(self, tmp_path, monkeypatch):
        seen = []
        self._finish(tmp_path, monkeypatch, no_file=True, on_kept=seen.append)
        assert seen == []

    def test_a_practice_recording_saves_no_frames(self, tmp_path, monkeypatch):
        seen = []
        self._finish(tmp_path, monkeypatch, job=practice_job(1), on_kept=seen.append)
        assert seen == []


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
            self.on_kept = []

        def streams(self):
            return list(self._streams)

        def has_streams(self):
            return bool(self._streams)

        def finish(self, s, keep=True, on_kept=None):
            self.finished.append(s.job["id"])
            self.kept.append(keep)
            self.on_kept.append(on_kept)
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

    def test_a_stream_with_a_recorder_saves_its_frames_when_its_recording_is_kept(
            self, tmp_path, monkeypatch):
        from curling_score.fpframes import store

        saved = []
        monkeypatch.setattr(store, "save_frames",
                            lambda rec, **kw: saved.append((rec, kw)) or 2)

        class Recorded(Session):
            fp_recorder = object()

        api = Api()
        ln, m, _ = lane(api, [stream(1)], {"j_1": lambda st, pub: Recorded(st, pub, ends=1)})
        ln.manager.root = tmp_path
        ln.fp_cap_gb = 7.5
        while ln.busy():
            ln.step()
        (on_kept,) = m.on_kept
        assert callable(on_kept)
        on_kept(tmp_path / "videos" / "x.mp4")
        assert saved == [(Recorded.fp_recorder, {
            "root": tmp_path, "video": tmp_path / "videos" / "x.mp4", "cap_gb": 7.5})]

    def test_a_stream_without_a_recorder_passes_no_callback(self):
        api = Api()
        ln, m, _ = lane(api, [stream(1)], {"j_1": lambda st, pub: Session(st, pub, ends=1)})
        while ln.busy():
            ln.step()
        assert m.on_kept == [None]

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
    def test_a_league_stream_gets_a_recorder_when_the_harvest_is_on(self, monkeypatch):
        from curling_score import analyze, fpframes, version

        monkeypatch.setattr(analyze, "load_models", lambda *a, **k: ("det", "broom", "line"))
        ids = []
        monkeypatch.setattr(fpframes, "model_ids",
                            lambda w, house=None: ids.append(w) or {"overhead": "o", "side": None})
        make = lane_mod.video_sessions("w.pt", progress=lambda m: None,
                                       fp_harvest=fpframes.Settings(True))
        a, b = make(stream(1), lambda d: None), make(stream(2), lambda d: None)
        rec = a.fp_recorder
        assert rec is not None and rec.video_id == "liveVid0001"
        assert b.fp_recorder is not rec and b.fp_recorder.video_id == "liveVid0002"
        assert rec.pipeline == version.PIPELINE_VERSION and rec.job == "j_1"
        assert ids == ["w.pt"]

    def test_the_switch_off_gives_no_recorder(self, monkeypatch):
        from curling_score import analyze, fpframes

        monkeypatch.setattr(analyze, "load_models", lambda *a, **k: ("det", "broom", "line"))
        for fp in (None, fpframes.Settings(False)):
            make = lane_mod.video_sessions("w.pt", progress=lambda m: None, fp_harvest=fp)
            assert make(stream(1), lambda d: None).fp_recorder is None

    def test_a_recorder_that_cannot_start_does_not_stop_the_stream(self, monkeypatch):
        from curling_score import analyze, fpframes

        monkeypatch.setattr(analyze, "load_models", lambda *a, **k: ("det", "broom", "line"))

        def boom(weights, house=None):
            raise FileNotFoundError("side weights")

        monkeypatch.setattr(fpframes, "model_ids", boom)
        make = lane_mod.video_sessions("w.pt", progress=lambda m: None,
                                       fp_harvest=fpframes.Settings(True))
        assert make(stream(1), lambda d: None).fp_recorder is None

    def test_a_practice_session_gets_no_recorder(self, monkeypatch):
        from curling_score import analyze, fpframes

        monkeypatch.setattr(analyze, "load_models", lambda *a, **k: ("det", "broom", "line"))
        monkeypatch.setattr(fpframes, "model_ids", lambda w, house=None: {"overhead": "o", "side": None})
        make = lane_mod.video_sessions("w.pt", progress=lambda m: None,
                                       fp_harvest=fpframes.Settings(True))
        watch = make(pstream(1), lambda d: None)
        assert getattr(watch, "fp_recorder", None) is None

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

    def test_watches_with_work_take_turns(self):
        # Found on the box: a watch catching up after a late calibration had
        # work at every call, and the watch after it waited minutes.
        class Busy(Watch):
            def step(self):
                self.steps += 1
                return True

        api, made = Api(), {}

        def busy(st, pub):
            made[st.job["id"]] = Busy(st, pub)
            return made[st.job["id"]]

        ln, _, _ = lane(api, [pstream(1), pstream(2)], {"p_1": busy, "p_2": busy})
        for _ in range(4):
            assert ln.step()
        assert (made["p_1"].steps, made["p_2"].steps) == (2, 2)

    def test_a_watch_with_nothing_to_do_passes_its_turn_on(self):
        class Busy(Watch):
            def step(self):
                self.steps += 1
                return True

        class Idle(Watch):
            def step(self):
                self.steps += 1
                return False

        api = Api()
        ln, _, _ = lane(api, [pstream(1), pstream(2), pstream(3)], {
            "p_1": lambda st, pub: Busy(st, pub), "p_2": lambda st, pub: Idle(st, pub),
            "p_3": lambda st, pub: Busy(st, pub)})
        streams = {s.job["id"]: s for s in ln.manager.streams()}
        for _ in range(4):
            assert ln.step()
        assert [streams[f"p_{n}"].session.steps for n in (1, 2, 3)] == [2, 2, 2]

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
        from curling_score.practice.watch import PracticeWatch

        monkeypatch.setattr(analyze, "load_models", lambda *a, **k: ("det", "broom", "line"))
        make = lane_mod.video_sessions("w.pt", progress=lambda m: None)
        ps = pstream(1)
        live, practice = make(stream(1), lambda d: None), make(ps, lambda d: None)
        assert isinstance(practice, PracticeWatch)
        assert practice.rec is ps.recorder
        assert practice.models is live.models
        assert practice.wall_t0 is not None


def cstream(n, **extra):
    """A camera job in hand, its recorder started at 1.7e9 by the wall clock."""
    job = practice_job(n, **extra)
    rec = Recorder(job)
    rec.wall_started = 1.7e9
    return manager_mod.Stream(job=job, recorder=rec)


def calibration(hog="paint"):
    from curling_score.live.session import Calibration

    # A transform: a camera feed's calibration is in its views' own pixels,
    # and the store keeps no other kind.
    return Calibration(panels=None, until_s=10.0, sideviews=None, side_expected=False,
                       setups={h: SimpleNamespace(hog_line=hog, transform=f"cut {h}")
                               for h in ("top", "bottom")})


class TestCameraWatchesInTheLane:
    @pytest.fixture(autouse=True)
    def models(self, monkeypatch):
        from curling_score import analyze

        monkeypatch.setattr(analyze, "load_models", lambda *a, **k: ("det", "broom", "line"))

    def test_camera_watch_since_zero(self):
        # The cameras' recording begins at Start: there is no footage from
        # before it to wait for.
        cs = cstream(3)
        watch = lane_mod.video_sessions("w.pt", progress=lambda m: None)(cs, lambda d: None)
        assert watch.rec is cs.recorder
        assert (watch.t0_s, watch.wall_t0) == (0.0, 1.7e9)
        assert watch.throws == []

    def test_without_somewhere_to_keep_them_there_is_no_saved_calibration(self):
        watch = lane_mod.video_sessions("w.pt", progress=lambda m: None)(cstream(3),
                                                                         lambda d: None)
        assert watch.saved is None and watch.save_calibration is None

    def test_a_camera_watch_checks_and_keeps_its_own_sheets_calibration(self, tmp_path):
        from curling_score.cameras.calstore import CalStore

        store = CalStore(tmp_path)
        store.save(3, calibration())
        store.save(4, calibration(hog="sheet 4"))
        make = lane_mod.video_sessions("w.pt", progress=lambda m: None, cal_root=tmp_path)
        watch = make(cstream(3), lambda d: None)
        assert watch.saved == calibration()
        watch.save_calibration(calibration(hog="moved"))
        assert store.load(3) == calibration(hog="moved")
        assert store.load(4) == calibration(hog="sheet 4")

    def test_a_resumed_camera_watch_carries_on_the_sessions_clock(self):
        # The worker restarted 600 s into the session. The new recording begins
        # at 1.7e9; the session's clock began at 1.7e9 - 600.
        from curling_score.service import practice

        earlier = {"id": "t_30.0", "t_release_s": 30.0, "t_rest_s": 55.0, "clip": True}
        cs = cstream(3, attempt=2, resume={"wall_t0": 1.7e9 - 600.0, "throws": [earlier]})
        watch = lane_mod.video_sessions("w.pt", progress=lambda m: None)(cs, lambda d: None)
        assert watch.t0_s == 600.0
        assert watch.throws == [earlier]
        # The API dates a throw at wall_t0 + (t - t0_s): the earlier throw keeps
        # its moment, and one 12 s into this recording is dated 12 s after it began.
        doc = watch.document()
        assert practice.thrown_at(doc, earlier).timestamp() == 1.7e9 - 600.0 + 30.0
        assert practice.thrown_at(doc, {"t_release_s": 612.0}).timestamp() == 1.7e9 + 12.0


class TestPracticeClipsInTheLane:
    def test_a_clip_goes_up_as_an_artifact_of_the_job(self, tmp_path):
        api = Api()
        s = pstream(1)
        ln, _, _ = lane(api, [s], {"p_1": lambda st, pub: Watch(st, pub)})
        ln.step()
        clip = tmp_path / "t_22.0.mp4"
        clip.write_bytes(b"\x00" * 1000)
        assert s.send_clip("t_22.0", clip) is True
        assert api.artifact_files[-1] == [{"name": "clips/t_22.0.mp4", "bytes": 1000}]
        assert api.uploads[-1][1] == b"\x00" * 1000

    def test_a_clip_that_cannot_go_up_says_so(self, tmp_path):
        api = Api()
        s = pstream(1)
        ln, _, _ = lane(api, [s], {"p_1": lambda st, pub: Watch(st, pub)})
        ln.step()

        def down(*a, **k):
            raise RuntimeError("503")

        api.artifacts = down
        clip = tmp_path / "t_22.0.mp4"
        clip.write_bytes(b"x")
        assert s.send_clip("t_22.0", clip) is False

    def test_a_lost_job_while_uploading_a_clip_is_raised(self, tmp_path):
        api = Api()
        s = pstream(1)
        ln, _, _ = lane(api, [s], {"p_1": lambda st, pub: Watch(st, pub)})
        ln.step()

        def lost(*a, **k):
            raise Lost("p_1")

        api.artifacts = lost
        clip = tmp_path / "t_22.0.mp4"
        clip.write_bytes(b"x")
        with pytest.raises(Lost):
            s.send_clip("t_22.0", clip)

    def test_a_practice_watch_gets_a_clipper_that_sends_through_the_lane(self, monkeypatch):
        from curling_score import analyze
        from curling_score.practice.clips import Clipper

        monkeypatch.setattr(analyze, "load_models", lambda *a, **k: (None, None, None))
        ps = pstream(1)
        ps.send_clip = lambda tid, path: True
        watch = lane_mod.video_sessions("w.pt", progress=lambda m: None)(ps, lambda d: None)
        assert isinstance(watch.clipper, Clipper) and watch.clipper.deliver is ps.send_clip
        assert watch.clipper.keep is False


# --- a league game read from its sheet's cameras ------------------------------------------

def camera_job(n):
    return {**live_job(n), "kind": "camera_live", "source": "cameras",
            "published_at": "2026-11-05T18:00:09+00:00"}


class CamRecorder(Recorder):
    """A camera recorder stand-in: finish_stream makes it whole."""

    def __init__(self, job):
        super().__init__(job)
        self.wall_started, self.finished_stream = 1793900701.0, 0
        self.dir = Path("/rec")

    def head_s(self):
        return 600.0

    def finish_stream(self):
        self.finished_stream += 1
        self.stopped = True

    def whole(self):
        return self.finished_stream > 0


class TestACameraGameInTheManager:
    def test_it_is_recorded_from_its_cameras_and_its_clock_is_followed(self, monkeypatch,
                                                                      tmp_path):
        from curling_score.cameras import recorder as cameras
        from curling_score.live import recorder

        made, aligners = [], []

        class Spy(CamRecorder):
            def __init__(self, sheet, directory, **kw):
                super().__init__(None)
                made.append((sheet, directory))

        monkeypatch.setattr(cameras, "CameraRecorder", Spy)
        monkeypatch.setattr(recorder, "YtDlpRecorder",
                            lambda *a, **k: (_ for _ in ()).throw(AssertionError("YouTube")))
        m = manager_mod.LiveManager(
            Api([camera_job(3)]), "ryzen", model_id="m", gpu=None, root=tmp_path,
            kinds=("live", "camera_live"), cameras=True,
            make_aligner=lambda job, rec: aligners.append((job["id"], rec)) or "aligner")
        m.poll_once()
        (s,) = m.streams()
        assert made == [(3, tmp_path / "live" / "liveVid0003")]
        assert aligners == [("j_3", s.recorder)] and s.aligner == "aligner"
        assert s.reference is None

    def test_with_keep_youtube_the_stream_is_recorded_beside_it_and_kept(self, monkeypatch,
                                                                        tmp_path):
        from curling_score.ingest import cache
        from curling_score.live import recorder

        refs, kept = [], []

        class Ref(Recorder):
            def __init__(self, vid, directory, **kw):
                super().__init__(None)
                self.vid, self.path = vid, Path(directory) / "rec.ts"
                refs.append(self)

            def whole(self):
                return True

        monkeypatch.setattr(recorder, "YtDlpRecorder", Ref)
        monkeypatch.setattr(cache, "keep_recording",
                            lambda path, vid, root: kept.append((path, vid)) or path)
        m = manager_mod.LiveManager(
            Api([camera_job(3)]), "ryzen", model_id="m", gpu=None, root=tmp_path,
            kinds=("camera_live",), cameras=True, keep_youtube=True,
            make_recorder=lambda job: CamRecorder(job),
            make_aligner=lambda job, rec: None, background=lambda fn: fn())
        m.poll_once()
        (s,) = m.streams()
        (ref,) = refs
        assert s.reference is ref and ref.started and ref.vid == "liveVid0003"
        m.finish(s)
        assert ref.stopped and kept == [(ref.path, "liveVid0003")]

    def test_a_youtube_recording_that_will_not_start_leaves_the_game_going(self, monkeypatch,
                                                                         tmp_path):
        from curling_score.live import recorder

        def broken(*a, **k):
            raise RuntimeError("bot check")

        monkeypatch.setattr(recorder, "YtDlpRecorder", broken)
        api = Api([camera_job(3)])
        m = manager_mod.LiveManager(
            api, "ryzen", model_id="m", gpu=None, root=tmp_path, kinds=("camera_live",),
            cameras=True, keep_youtube=True, make_recorder=lambda job: CamRecorder(job),
            make_aligner=lambda job, rec: None)
        m.poll_once()
        (s,) = m.streams()
        assert s.reference is None and api.failed == []


class Aligned:
    def __init__(self, clock=None):
        self.value, self.stopped = clock, False

    def clock(self):
        return self.value

    def stop(self):
        self.stopped = True


class ClockedSession(Session):
    def __init__(self, stream, publish, **kw):
        super().__init__(stream, publish, **kw)
        self.wait_for_clock, self.clocks = False, []

    def set_clock(self, clock):
        self.clocks.append(clock)


class TestACameraGameInTheLane:
    def camera_stream(self, n, clock=None):
        job = camera_job(n)
        return manager_mod.Stream(job=job, recorder=CamRecorder(job), aligner=Aligned(clock))

    def test_its_session_waits_for_the_clock_and_gets_it(self):
        api = Api()
        s = self.camera_stream(3)
        ln, _, _ = lane(api, [s], {"j_3": lambda st, pub: ClockedSession(st, pub, ends=5)})
        ln.step()
        assert s.session.wait_for_clock and s.session.clocks == []
        clock = {"kind": "cameras", "aligned": "segment", "offset_s": -908.0,
                 "steps": [[0.0, -908.0]]}
        s.aligner.value = clock
        ln.step()
        assert s.session.clocks[-1] == clock

    def test_the_end_of_its_stream_finishes_the_recording_once(self):
        api = Api()
        s = self.camera_stream(3)
        ln, m, _ = lane(api, [s], {"j_3": lambda st, pub: ClockedSession(st, pub, ends=2)})
        ln.step()
        s.stop_requested = True
        while ln.busy():
            ln.step()
        assert s.recorder.finished_stream == 1
        assert m.finished == ["j_3"]


def test_a_worker_that_sees_the_cameras_claims_camera_games(monkeypatch, tmp_path):
    from curling_score.service import worker

    seen = {}
    monkeypatch.setattr(manager_mod, "LiveManager",
                        lambda *a, **kw: seen.update(kw) or SimpleNamespace(start=lambda: None))
    for cameras, practice in (("1", "1"), ("0", "1"), ("1", "0")):
        monkeypatch.setenv("WORKER_LIVE", "1")
        monkeypatch.setenv("WORKER_CAMERAS", cameras)
        monkeypatch.setenv("WORKER_PRACTICE", practice)
        monkeypatch.setenv("CAMERA_KEEP_YOUTUBE", "1")
        worker.build_live("http://api", "t", "w", root=tmp_path, weights=None, start=False)
        assert ("camera_live" in seen["kinds"]) == (cameras == "1")
        assert seen["keep_youtube"] is True


class TestKeepingACameraGame:
    CLOCK = {"kind": "cameras", "aligned": "segment", "offset_s": -908.16,
             "steps": [[0.0, -908.16]]}

    def manager(self, tmp_path, whole):
        cams = tmp_path / "cams"

        class Files(CamRecorder):
            def __init__(self, job):
                super().__init__(job)
                self.dir = cams / "live" / "ryzen" / job["video_id"]
                self.dir.mkdir(parents=True)
                for role in ("house_a", "house_h", "long_a", "long_h"):
                    (self.dir / f"{role}.ts").write_bytes(b"ts")
                self.path = self.dir

            def whole(self):
                return whole

        m = manager_mod.LiveManager(
            Api([camera_job(3)]), "ryzen", model_id="m", gpu=None, root=tmp_path / "cache",
            kinds=("camera_live",), cameras=True, cams_root=cams,
            make_recorder=Files, make_aligner=lambda job, rec: Aligned(self.CLOCK),
            background=lambda fn: fn())
        m.poll_once()
        return m, cams

    def test_a_whole_game_is_kept_with_its_clock(self, tmp_path):
        import json

        m, cams = self.manager(tmp_path, whole=True)
        (s,) = m.streams()
        m.finish(s)
        kept = cams / "kept" / "liveVid0003"
        assert sorted(p.name for p in kept.iterdir()) == \
            ["clock.json", "house_a.ts", "house_h.ts", "long_a.ts", "long_h.ts"]
        meta = json.loads((kept / "clock.json").read_text())
        assert isinstance(meta.pop("kept_at"), float)
        layout = meta.pop("layout")
        assert layout["sheet"] == 3 and {p["role"] for p in layout["placements"]} == \
            {"house_a", "house_h", "long_a", "long_h"}
        assert meta == {"sheet": 3, "wall_started": 1793900701.0, "clock": self.CLOCK}
        assert not (cams / "live" / "ryzen" / "liveVid0003").exists()

    def test_a_camera_recording_names_its_video(self, tmp_path):
        m, cams = self.manager(tmp_path, whole=True)
        (s,) = m.streams()
        assert (s.recorder.dir / "video_id").read_text() == "liveVid0003"

    def test_a_game_that_did_not_end_with_its_stream_is_not_kept(self, tmp_path):
        m, cams = self.manager(tmp_path, whole=False)
        (s,) = m.streams()
        m.finish(s)
        assert not (cams / "kept").exists()
        assert not (cams / "live" / "ryzen" / "liveVid0003").exists()

    def test_a_camera_game_records_into_the_shared_place(self, monkeypatch, tmp_path):
        from curling_score.cameras import recorder as cameras

        made = []
        monkeypatch.setattr(cameras, "CameraRecorder",
                            lambda sheet, d, **kw: made.append(d) or CamRecorder(None))
        m = manager_mod.LiveManager(Api(), "ryzen", model_id="m", gpu=None,
                                    root=tmp_path / "cache", cams_root=tmp_path / "cams")
        m._recorder(camera_job(3))
        m._recorder({**camera_job(4), "kind": "practice"})
        assert made == [tmp_path / "cams" / "live" / "ryzen" / "liveVid0003",
                        tmp_path / "cache" / "live" / "liveVid0004"]


def test_the_worker_records_camera_games_in_the_shared_place(monkeypatch, tmp_path):
    from curling_score.ingest import prune
    from curling_score.service import worker

    seen, pruned = {}, []
    monkeypatch.setattr(manager_mod, "LiveManager",
                        lambda *a, **kw: seen.update(kw) or SimpleNamespace(start=lambda: None))
    monkeypatch.setattr(prune, "prune", lambda *a, **kw: pruned.append(kw) or [])
    cams = tmp_path / "cams"
    stale = cams / "live" / "w" / "oldVid"
    other = cams / "live" / "w2" / "theirs"
    stale.mkdir(parents=True)
    other.mkdir(parents=True)
    monkeypatch.setenv("WORKER_LIVE", "1")
    monkeypatch.setenv("WORKER_CAMERAS", "1")
    monkeypatch.setenv("WORKER_CAMS", str(cams))
    monkeypatch.setenv("WORKER_CAMERA_DAYS", "3")
    worker.build_live("http://api", "t", "w", root=tmp_path / "cache", weights=None,
                      start=False, cache_gb=300.0)
    assert seen["cams_root"] == cams
    assert not stale.exists() and other.exists()      # its own leftovers only
    seen["prune"]()
    assert pruned[0]["cams_root"] == cams and pruned[0]["camera_days"] == 3.0


# --- a sheet recorded from its schedule's start, ahead of its stream -----------------------

def standby_job(n):
    return {"id": f"j_{n}", "run_id": f"r_{n}", "video_id": f"standby-{n}-20261110T020000",
            "kind": "camera_standby", "source": "cameras", "sheet": n, "format": "fours",
            "title": f"Sheet {n} - Monday - waiting for the stream"}


def found(n):
    """What a standby's heartbeat says once the poll found its stream."""
    return {"kind": "camera_live", "video_id": f"liveVid000{n}", "title": f"Sheet {n}",
            "sheet": n, "format": "fours", "source": "cameras", "channel_id": "UCclub",
            "published_at": "2026-11-10T02:31:00+00:00"}


class TestAStandbyInTheManager:
    def manager(self, tmp_path, monkeypatch, api):
        from curling_score.live import recorder

        aligners, refs = [], []

        class Ref(Recorder):
            def __init__(self, vid, directory, **kw):
                super().__init__(None)
                self.vid = vid
                refs.append(self)

        monkeypatch.setattr(recorder, "YtDlpRecorder", Ref)
        t = [0.0]
        m = manager_mod.LiveManager(
            api, "ryzen", model_id="m", gpu=None, root=tmp_path, clock=lambda: t[0],
            kinds=("live", "camera_live", "camera_standby"), cameras=True, keep_youtube=True,
            make_recorder=lambda job: CamRecorder(job),
            make_aligner=lambda job, rec: aligners.append((dict(job), rec)) or Aligned())
        return m, t, aligners, refs

    def test_it_records_its_cameras_with_no_clock_and_no_stream_yet(self, tmp_path,
                                                                    monkeypatch):
        api = Api([standby_job(3)])
        m, _, aligners, refs = self.manager(tmp_path, monkeypatch, api)
        m.poll_once()
        (s,) = m.streams()
        assert s.recorder.started and s.aligner is None and s.reference is None
        assert aligners == [] and refs == []

    def test_its_heartbeat_hands_it_to_its_stream(self, tmp_path, monkeypatch):
        api = Api([standby_job(3)])
        m, t, aligners, refs = self.manager(tmp_path, monkeypatch, api)
        m.poll_once()
        (s,) = m.streams()
        recorder = s.recorder
        api.games["j_3"] = found(3)
        t[0] = 61.0
        m.poll_once()
        assert s.recorder is recorder and not recorder.stopped
        assert (s.job["kind"], s.job["video_id"], s.job["id"]) == \
            ("camera_live", "liveVid0003", "j_3")
        assert s.job["published_at"] == "2026-11-10T02:31:00+00:00"
        ((job, rec),) = aligners
        assert job["video_id"] == "liveVid0003" and rec is recorder
        (ref,) = refs
        assert s.reference is ref and ref.started and ref.vid == "liveVid0003"

    def test_it_is_handed_over_once(self, tmp_path, monkeypatch):
        api = Api([standby_job(3)])
        m, t, aligners, refs = self.manager(tmp_path, monkeypatch, api)
        m.poll_once()
        api.games["j_3"] = found(3)
        for k in (1, 2, 3):
            t[0] = 61.0 * k
            m.poll_once()
        assert len(aligners) == 1 and len(refs) == 1

    def test_given_up_it_is_lost_and_its_recording_deleted(self, tmp_path, monkeypatch):
        cams = tmp_path / "cams"

        class Files(CamRecorder):
            def __init__(self, job):
                super().__init__(job)
                self.dir = cams / "live" / "ryzen" / job["video_id"]
                self.dir.mkdir(parents=True)
                (self.dir / "long_a.ts").write_bytes(b"ts")

        api = Api([standby_job(3)])
        t = [0.0]
        m = manager_mod.LiveManager(
            api, "ryzen", model_id="m", gpu=None, root=tmp_path / "cache", clock=lambda: t[0],
            kinds=("camera_standby",), cameras=True, cams_root=cams, make_recorder=Files,
            make_aligner=lambda job, rec: None, background=lambda fn: fn())
        m.poll_once()
        (s,) = m.streams()
        api.lose.add("j_3")
        t[0] = 61.0
        m.poll_once()
        assert s.lost
        m.finish(s)
        assert not (cams / "live" / "ryzen" / "standby-3-20261110T020000").exists()
        assert not (cams / "kept").exists()


class TestAStandbyInTheLane:
    def test_it_gets_no_session_until_its_stream_is_found(self):
        api = Api()
        job = standby_job(3)
        s = manager_mod.Stream(job=job, recorder=CamRecorder(job))
        ln, _, made = lane(api, [s], {"j_3": lambda st, pub: ClockedSession(st, pub, ends=5)})
        assert ln.step() is False and s.session is None and made == []
        s.aligner = Aligned()
        s.job = {**job, **found(3)}
        ln.step()
        assert made == ["j_3"] and s.session.wait_for_clock


class TestASessionFromAStandby:
    def make(self, monkeypatch, published_at, wall_started):
        from curling_score import analyze

        monkeypatch.setattr(analyze, "load_models", lambda *a, **k: ("det", "broom", "line"))
        job = {**standby_job(3), **found(3), "published_at": published_at}
        rec = CamRecorder(job)
        rec.wall_started = wall_started
        st = manager_mod.Stream(job=job, recorder=rec)
        return lane_mod.video_sessions("w.pt", progress=lambda m: None)(st, lambda d: None)

    def test_it_is_read_from_where_its_stream_began(self, monkeypatch):
        from datetime import datetime

        began = datetime.fromisoformat("2026-11-10T02:31:00+00:00").timestamp()
        session = self.make(monkeypatch, "2026-11-10T02:31:00+00:00", began - 1860.0)
        assert session.start_s == 1860.0

    def test_a_stream_older_than_the_recording_is_read_from_the_start(self, monkeypatch):
        from datetime import datetime

        began = datetime.fromisoformat("2026-11-10T02:31:00+00:00").timestamp()
        assert self.make(monkeypatch, "2026-11-10T02:31:00+00:00", began + 200.0).start_s == 0.0
        assert self.make(monkeypatch, None, began).start_s == 0.0


def test_a_worker_that_sees_the_cameras_takes_standbys(monkeypatch, tmp_path):
    from curling_score.service import worker

    seen = {}
    monkeypatch.setattr(manager_mod, "LiveManager",
                        lambda *a, **kw: seen.update(kw) or SimpleNamespace(start=lambda: None))
    for cameras in ("1", "0"):
        monkeypatch.setenv("WORKER_LIVE", "1")
        monkeypatch.setenv("WORKER_CAMERAS", cameras)
        worker.build_live("http://api", "t", "w", root=tmp_path, weights=None, start=False)
        assert ("camera_standby" in seen["kinds"]) == (cameras == "1")


class TestKeepingAGameFromAStandby:
    def test_its_files_are_cut_where_its_stream_began(self, tmp_path, monkeypatch):
        from datetime import datetime

        from curling_score.cameras import trim

        cuts = []
        monkeypatch.setattr(trim, "trim_kept", lambda d, cut: cuts.append((d, cut)) or cut)
        cams = tmp_path / "cams"
        began = datetime.fromisoformat(found(3)["published_at"]).timestamp()

        class Files(CamRecorder):
            def __init__(self, job):
                super().__init__(job)
                self.wall_started = began - 1860.0
                self.dir = cams / "live" / "ryzen" / job["video_id"]
                self.dir.mkdir(parents=True)
                (self.dir / "long_a.ts").write_bytes(b"ts")

            def whole(self):
                return True

        api = Api([standby_job(3)])
        t = [0.0]
        m = manager_mod.LiveManager(
            api, "ryzen", model_id="m", gpu=None, root=tmp_path / "cache", clock=lambda: t[0],
            kinds=("camera_standby",), cameras=True, cams_root=cams, make_recorder=Files,
            make_aligner=lambda job, rec: Aligned(), background=lambda fn: fn())
        m.poll_once()
        api.games["j_3"] = found(3)
        t[0] = 61.0
        m.poll_once()
        (s,) = m.streams()
        m.finish(s)
        assert cuts == [(cams / "kept" / "liveVid0003", 1860.0)]

    def standby_manager(self, tmp_path, monkeypatch, trim_kept, background):
        from datetime import datetime

        from curling_score.cameras import trim

        monkeypatch.setattr(trim, "trim_kept", trim_kept)
        cams = tmp_path / "cams"
        began = datetime.fromisoformat(found(3)["published_at"]).timestamp()

        class Files(CamRecorder):
            def __init__(self, job):
                super().__init__(job)
                self.wall_started = began - 1860.0
                self.dir = cams / "live" / "ryzen" / job["video_id"]
                self.dir.mkdir(parents=True)
                (self.dir / "long_a.ts").write_bytes(b"ts")

            def whole(self):
                return True

        api = Api([standby_job(3)])
        t = [0.0]
        m = manager_mod.LiveManager(
            api, "ryzen", model_id="m", gpu=None, root=tmp_path / "cache", clock=lambda: t[0],
            kinds=("camera_standby",), cameras=True, cams_root=cams, make_recorder=Files,
            make_aligner=lambda job, rec: Aligned(), background=background)
        m.poll_once()
        api.games["j_3"] = found(3)
        t[0] = 61.0
        m.poll_once()
        return m, cams

    def test_a_standby_game_is_kept_with_its_trim_pending_until_cut(self, tmp_path,
                                                                     monkeypatch):
        import json

        from curling_score.cameras import trim

        def cut_beside(path, cut_s):
            out = path.with_name(path.name + ".trim")
            out.write_bytes(b"cut")
            return out

        monkeypatch.setattr(trim, "_write_trim", cut_beside)
        later = []
        m, cams = self.standby_manager(tmp_path, monkeypatch, trim.trim_kept, later.append)
        (s,) = m.streams()
        assert (s.recorder.dir / "video_id").read_text() == "liveVid0003"
        m.finish(s)
        meta_path = cams / "kept" / "liveVid0003" / "clock.json"
        assert json.loads(meta_path.read_text())["trim_pending"] is True
        for fn in later:
            fn()
        meta = json.loads(meta_path.read_text())
        assert "trim_pending" not in meta and isinstance(meta["kept_at"], float)

    def test_a_failed_trim_is_left_to_trim_and_the_disk_still_pruned(self, tmp_path,
                                                                       monkeypatch):
        import json

        def broken(d, cut):
            raise OSError("No space left on device")

        m, cams = self.standby_manager(tmp_path, monkeypatch, broken, lambda fn: fn())
        pruned = []
        m.prune = lambda: pruned.append(1)
        (s,) = m.streams()
        m.finish(s)
        meta = json.loads((cams / "kept" / "liveVid0003" / "clock.json").read_text())
        # trim_kept says whether its files are readable; the manager does not guess.
        assert meta["trim_pending"] is True and meta["trim_by"] == "ryzen"
        assert pruned

    def test_its_kept_files_begin_at_its_floor(self, tmp_path, monkeypatch):
        later = []
        cuts = []
        m, cams = self.standby_manager(tmp_path, monkeypatch,
                                       lambda d, cut: cuts.append(cut) or cut, later.append)
        (s,) = m.streams()
        s.session = SimpleNamespace(floor_s=5000.0)
        m.finish(s)
        for fn in later:
            fn()
        assert cuts == [5000.0]

    def test_a_game_recorded_from_its_claim_is_not_cut(self, tmp_path, monkeypatch):
        from curling_score.cameras import trim

        cuts = []
        monkeypatch.setattr(trim, "trim_kept", lambda d, cut: cuts.append((d, cut)) or cut)
        cams = tmp_path / "cams"

        class Files(CamRecorder):
            def __init__(self, job):
                super().__init__(job)
                self.dir = cams / "live" / "ryzen" / job["video_id"]
                self.dir.mkdir(parents=True)
                (self.dir / "long_a.ts").write_bytes(b"ts")

            def whole(self):
                return True

        # Claimed once its stream was found: recorded from after it began.
        job = {**camera_job(3), "published_at": "2026-11-05T17:40:00+00:00"}
        m = manager_mod.LiveManager(
            Api([job]), "ryzen", model_id="m", gpu=None, root=tmp_path / "cache",
            kinds=("camera_live",), cameras=True, cams_root=cams, make_recorder=Files,
            make_aligner=lambda job, rec: Aligned(), background=lambda fn: fn())
        m.poll_once()
        (s,) = m.streams()
        m.finish(s)
        assert cuts == [] and (cams / "kept" / "liveVid0003" / "clock.json").exists()


def test_a_games_clock_counts_its_waits_from_where_its_stream_began(monkeypatch):
    """Behind a standby the recording is already long when the stream is found:
    the clock's first checks and its give-up wait from the stream's start."""
    from datetime import datetime

    from curling_score.live import clock

    made = {}

    class Spy:
        def __init__(self, *a, head, **kw):
            made["head"] = head

        def start(self):
            return self

    monkeypatch.setattr(clock, "Aligner", Spy)
    job = {**standby_job(3), **found(3)}
    rec = CamRecorder(job)
    rec.wall_started = datetime.fromisoformat(job["published_at"]).timestamp() - 1860.0
    rec.head_s = lambda: 2000.0
    manager_mod.LiveManager(Api(), "ryzen", model_id="m", gpu=None)._aligner(job, rec)
    assert made["head"]() == 140.0



# ------------------------------------------- a sheet's games in a row (phase 3)
WALL0 = 1793900701.0


def iso(t):
    from datetime import datetime, timezone

    return datetime.fromtimestamp(t, timezone.utc).isoformat()


class OwnedSession(ClockedSession):
    """Records what it is told of its span; deciding until told its own end."""

    def __init__(self, stream, publish, **kw):
        super().__init__(stream, publish, **kw)
        self.ownership, self.own_until_s, self.decide = [], None, False

    def set_ownership(self, **kw):
        self.ownership.append(kw)
        if kw.get("stream_end_s") is not None:
            self.decide = True

    def deciding(self):
        return self.decide and self.own_until_s is None


class TestASheetsGamesInTheLane:
    def camera_stream(self, n=3, **handoff):
        job = {**camera_job(n), **handoff}
        st = manager_mod.Stream(job=job, recorder=CamRecorder(job), aligner=Aligned(None))
        st.handoff = dict(handoff)
        return st

    def test_a_lookahead_game_is_read_and_kept_from_its_opening(self):
        job = {**camera_job(3), "analysis_from": iso(WALL0 + 1200.0)}
        rec = CamRecorder(job)
        assert manager_mod.analysis_start_s(job, rec) == pytest.approx(1200.0)
        assert manager_mod.analysis_start_s(camera_job(3), rec) == \
            manager_mod.stream_start_s(camera_job(3), rec)

    def test_the_lane_passes_the_handoff_and_floor_to_the_session(self):
        api = Api()
        s = self.camera_stream(floor_pending=True, floor_at=None, handoff_at=None)
        ln, _, _ = lane(api, [s], {"j_3": lambda st, pub: OwnedSession(st, pub, ends=9)})
        ln.step()
        assert s.session.ownership[-1]["floor_pending"] is True
        s.handoff = {"floor_pending": False, "floor_at": iso(WALL0 + 300.0),
                     "handoff_at": iso(WALL0 + 3600.0)}
        ln.step()
        got = s.session.ownership[-1]
        assert got["floor_s"] == pytest.approx(300.0)
        assert got["handoff_s"] == pytest.approx(3600.0) and got["floor_pending"] is False

    def test_a_game_still_going_when_its_stream_ends_keeps_recording(self):
        api = Api()
        s = self.camera_stream()
        ln, _, _ = lane(api, [s], {"j_3": lambda st, pub: OwnedSession(st, pub, ends=99)})
        ln.step()
        s.stop_requested = True
        for _ in range(5):
            ln.step()
        assert s.recorder.finished_stream == 0
        assert [o["stream_end_s"] for o in s.session.ownership if "stream_end_s" in o] == [600.0]
        s.session.own_until_s = 100.0            # head 600 >= 100 + the board margin
        ln.step()
        assert s.recorder.finished_stream == 1
        assert s.own_until_wall == pytest.approx(WALL0 + 100.0)

    def test_it_gives_up_following_after_45_minutes(self):
        api = Api()
        s = self.camera_stream()
        t = [0.0]
        ln, _, _ = lane(api, [s], {"j_3": lambda st, pub: OwnedSession(st, pub, ends=99)})
        ln.clock = lambda: t[0]
        ln.step()
        s.stop_requested = True
        ln.step()
        t[0] = lane_mod.OVERRUN_MAX_S - 1
        ln.step()
        assert s.recorder.finished_stream == 0
        t[0] = lane_mod.OVERRUN_MAX_S + 1
        ln.step()
        assert s.recorder.finished_stream == 1


def test_own_until_is_sent_once():
    api = Api([camera_job(3)])
    t = [0.0]
    m = manager_mod.LiveManager(
        api, "ryzen", model_id="m", gpu=None, root=None, clock=lambda: t[0],
        kinds=("camera_live",), cameras=True, make_recorder=lambda job: CamRecorder(job),
        make_aligner=lambda job, rec: Aligned())
    m.poll_once()
    (s,) = m.streams()
    s.own_until_wall = WALL0 + 100.0
    for k in range(1, 4):
        t[0] = 61.0 * k
        m.poll_once()
    assert api.extras == [("j_3", {"own_until": iso(WALL0 + 100.0)})]



class TestTheReviewFixes:
    def camera_stream(self, **handoff):
        job = {**camera_job(3), **handoff}
        st = manager_mod.Stream(job=job, recorder=CamRecorder(job), aligner=Aligned(None))
        st.handoff = dict(handoff)
        return st

    def test_own_until_is_known_as_soon_as_decided(self):
        api = Api()
        s = self.camera_stream()
        ln, _, _ = lane(api, [s], {"j_3": lambda st, pub: OwnedSession(st, pub, ends=99)})
        ln.step()
        s.session.own_until_s = 100.0
        ln.step()
        assert s.own_until_wall == pytest.approx(WALL0 + 100.0)

    def test_a_game_that_found_its_end_stops_recording_before_its_stream_ends(self):
        api = Api()
        s = self.camera_stream()
        ln, _, _ = lane(api, [s], {"j_3": lambda st, pub: OwnedSession(st, pub, ends=99)})
        ln.step()
        s.session.own_until_s = 100.0                 # head 600 >= 100 + the margin
        ln.step()
        assert s.recorder.finished_stream == 1 and not s.stop_requested

    def test_the_stream_end_comes_from_the_api(self):
        api = Api()
        s = self.camera_stream(stream_ended_at=iso(WALL0 + 500.0))
        ln, _, _ = lane(api, [s], {"j_3": lambda st, pub: OwnedSession(st, pub, ends=99)})
        ln.step()
        s.stop_requested = True
        ln.step()
        assert [o["stream_end_s"] for o in s.session.ownership
                if "stream_end_s" in o] == [pytest.approx(500.0)]

    def test_complete_carries_own_until(self):
        api = Api()
        s = self.camera_stream()
        ln, _, _ = lane(api, [s], {"j_3": lambda st, pub: OwnedSession(st, pub, ends=1)})
        ln.step()
        s.own_until_wall = WALL0 + 100.0
        while ln.busy():
            ln.step()
        assert api.completed[-1][1]["own_until"] == iso(WALL0 + 100.0)


def test_a_worker_claims_one_live_job_a_poll():
    api = Api([camera_job(3), camera_job(4), camera_job(5)])
    m = manager_mod.LiveManager(
        api, "ryzen", model_id="m", gpu=None, root=None, kinds=("camera_live",), cameras=True,
        make_recorder=lambda job: CamRecorder(job), make_aligner=lambda job, rec: Aligned())
    m.poll_once()
    assert len(m.streams()) == 1
    m.poll_once()
    assert len(m.streams()) == 2


def test_own_until_is_sent_at_once():
    api = Api([camera_job(3)])
    t = [0.0]
    m = manager_mod.LiveManager(
        api, "ryzen", model_id="m", gpu=None, root=None, clock=lambda: t[0],
        kinds=("camera_live",), cameras=True, make_recorder=lambda job: CamRecorder(job),
        make_aligner=lambda job, rec: Aligned())
    m.poll_once()
    t[0] = 61.0
    m.poll_once()                      # a heartbeat with nothing to say
    (s,) = m.streams()
    s.own_until_wall = WALL0 + 100.0
    t[0] = 70.0                        # well inside the next minute
    m.poll_once()
    assert api.extras == [("j_3", {"own_until": iso(WALL0 + 100.0)})]
