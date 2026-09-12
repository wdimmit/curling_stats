"""The worker loop, against a fake API and a fake pipeline."""

import json
from pathlib import Path

import pytest

from curling_score.ingest import cache
from curling_score.ingest.source import VideoInfo
from curling_score.service import worker


class FakeApi:
    def __init__(self, jobs):
        self.jobs = list(jobs)
        self.progress_calls = []
        self.uploads = {}
        self.completed = []
        self.failed = []

    def claim(self, worker_id, model_id, gpu):
        return self.jobs.pop(0) if self.jobs else None

    def progress(self, job_id, worker_id, phase, fraction, message):
        self.progress_calls.append((phase, fraction))
        return {"ok": True}

    def artifacts(self, job_id, worker_id, files, detcache):
        return {"uploads": [{"name": f["name"], "url": f"memory://{f['name']}", "headers": {}}
                            for f in files],
                "detcache_uploads": [{"digest": d["digest"], "url": f"memory://{d['digest']}",
                                      "headers": {}} for d in detcache]}

    def upload(self, url, headers, data):
        self.uploads[url] = data

    def complete(self, job_id, worker_id, payload):
        self.completed.append(payload)
        return {"ok": True}

    def fail(self, job_id, worker_id, error, kind, retry_after_s=None):
        self.failed.append((kind, error))
        return {"ok": True}


def fake_doc():
    return {"schema_version": 2, "processing_version": "p+m",
            "source": {"video_id": "VXU9xwmugRg", "sheet": 2},
            "games": [{"index": 0, "start_s": 10.0, "end_s": 6000.0, "ends": [{}, {}]}]}


def fake_info(url):
    return VideoInfo("VXU9xwmugRg", "Sheet 2", 14000.0, True, False, None, 2, "UCclub")


JOB = {"id": "j_1", "run_id": "r_1", "video_id": "VXU9xwmugRg",
       "window_start_s": None, "window_end_s": None, "sheet": None}


class TestProcessJob:
    def test_it_runs_the_pipeline_uploads_and_completes(self, tmp_path):
        api = FakeApi([])
        seen = {}

        def analyze_fn(url, **kw):
            seen.update(kw)
            kw["on_phase"]("detect", 0.5, "end 3")
            (tmp_path / "detections").mkdir(exist_ok=True)
            (tmp_path / "detections" / "deadbeef.npz").write_bytes(b"npz")
            return fake_doc()

        worker.process_job(JOB, api, "home", root=tmp_path, weights=None,
                           out_dir=tmp_path / "out", analyze_fn=analyze_fn,
                           fetch_info=fake_info)
        assert seen["skip_scoreboard"] is True and seen["download_attempts"] == 1
        assert seen["info"].video_id == "VXU9xwmugRg"
        assert json.loads(api.uploads["memory://timeline.json"])["games"][0]["index"] == 0
        assert api.uploads["memory://deadbeef"] == b"npz"
        meta = json.loads(api.uploads["memory://meta.json"])
        assert meta["games"] == [{"index": 0, "start_s": 10.0, "end_s": 6000.0, "ends": 2}]
        assert api.completed[0]["detcache_digests"] == ["deadbeef"]
        assert api.completed[0]["games"][0]["ends"] == 2
        assert (tmp_path / "out" / "r_1" / "timeline.json").is_file()
        phases = [p for p, _f in api.progress_calls]
        assert phases[0] == "download" and "upload" in phases

    def test_the_window_and_sheet_reach_the_pipeline(self, tmp_path):
        api = FakeApi([])
        seen = {}

        def analyze_fn(url, **kw):
            seen.update(kw)
            return fake_doc()

        job = {**JOB, "window_start_s": 19400.0, "window_end_s": 34400.0, "sheet": 4}
        worker.process_job(job, api, "home", root=tmp_path, weights=None,
                           out_dir=tmp_path / "out", analyze_fn=analyze_fn,
                           fetch_info=fake_info)
        assert (seen["start_s"], seen["end_s"], seen["sheet"]) == (19400.0, 34400.0, 4)

    def test_a_detcache_hit_is_not_reuploaded(self, tmp_path):
        det = tmp_path / "detections"
        det.mkdir()
        (det / "old.npz").write_bytes(b"old")
        api = FakeApi([])
        worker.process_job(JOB, api, "home", root=tmp_path, weights=None,
                           out_dir=tmp_path / "out", analyze_fn=lambda url, **kw: fake_doc(),
                           fetch_info=fake_info)
        assert "memory://old" not in api.uploads


class TestClassifyingFailures:
    def test_blocked(self):
        assert worker.classify_error(cache.BlockedError("not a bot")) == "blocked"

    def test_permanent(self):
        assert worker.classify_error(RuntimeError("ERROR: Private video")) == "permanent"
        assert worker.classify_error(ValueError("could not find two panels")) == "permanent"

    def test_transient_by_default(self):
        assert worker.classify_error(OSError("connection reset")) == "transient"


class TestLoop:
    def test_one_job_then_stop(self, tmp_path, monkeypatch):
        monkeypatch.setattr(worker, "process_job",
                            lambda job, api, wid, **kw: api.complete(job["id"], wid, {}))
        api = FakeApi([JOB])
        worker.run_forever(api, "home", root=tmp_path, weights=None, out_dir=tmp_path,
                           cache_gb=1.0, sleep=lambda s: None, once=True)
        assert len(api.completed) == 1

    def test_a_failure_is_reported_with_its_kind(self, tmp_path, monkeypatch):
        def boom(job, api, wid, **kw):
            raise cache.BlockedError("Sign in to confirm you're not a bot")
        monkeypatch.setattr(worker, "process_job", boom)
        api = FakeApi([JOB])
        worker.run_forever(api, "home", root=tmp_path, weights=None, out_dir=tmp_path,
                           cache_gb=1.0, sleep=lambda s: None, once=True)
        assert api.failed == [("blocked", "BlockedError: Sign in to confirm you're not a bot")]

    def test_nothing_to_do_returns_when_asked_to_run_once(self, tmp_path):
        api = FakeApi([])
        worker.run_forever(api, "home", root=tmp_path, weights=None, out_dir=tmp_path,
                           cache_gb=1.0, sleep=lambda s: None, once=True)
        assert api.completed == [] and api.failed == []


class TestResolveWeights:
    def test_an_explicit_setting_wins(self, monkeypatch):
        # A deployment must be able to pin its own model.
        monkeypatch.setenv("WEIGHTS", "/models/pinned.pt")
        assert worker.resolve_weights() == "/models/pinned.pt"

    def test_none_selects_the_classical_detector(self, monkeypatch):
        monkeypatch.setenv("WEIGHTS", "none")
        assert worker.resolve_weights() is None

    def test_unset_means_the_project_default(self, monkeypatch):
        # Standardised on ds11a: unset used to mean the colour detector.
        monkeypatch.delenv("WEIGHTS", raising=False)
        monkeypatch.delenv("CURLING_SCORE_WEIGHTS", raising=False)
        got = worker.resolve_weights()
        assert got is not None and got.endswith("ds11a.pt")

    def test_a_missing_default_falls_back_loudly_not_silently(
            self, tmp_path, monkeypatch, caplog):
        # Safe only because model_id writes the fallback into every timeline's
        # processing_version, so it is recorded rather than hidden.
        monkeypatch.delenv("WEIGHTS", raising=False)
        monkeypatch.setenv("CURLING_SCORE_WEIGHTS", str(tmp_path / "absent.pt"))
        with caplog.at_level("WARNING"):
            assert worker.resolve_weights() is None
        assert "classical" in caplog.text


class TestProgressIsBestEffort:
    """A 30-minute job must not die because telemetry did."""

    class FlakyApi(FakeApi):
        def __init__(self, jobs, fail_times=1, exc=None):
            super().__init__(jobs)
            self.fail_times = fail_times
            self.exc = exc or TimeoutError("read timed out")

        def progress(self, *a, **kw):
            if self.fail_times > 0:
                self.fail_times -= 1
                raise self.exc
            return super().progress(*a, **kw)

    def test_a_failed_progress_post_does_not_abort_the_job(self, tmp_path):
        api = self.FlakyApi([], fail_times=2)
        worker.process_job(JOB, api, "home", root=tmp_path, weights=None,
                           out_dir=tmp_path / "out",
                           analyze_fn=lambda url, **kw: (
                               kw["on_phase"]("detect", 0.5, "end 3") or fake_doc()),
                           fetch_info=fake_info)
        assert len(api.completed) == 1, "the job should still have completed"

    def test_losing_the_job_does_stop_it(self, tmp_path):
        api = self.FlakyApi([], fail_times=1, exc=worker.Lost("not yours"))
        with pytest.raises(worker.Lost):
            worker.process_job(JOB, api, "home", root=tmp_path, weights=None,
                               out_dir=tmp_path / "out",
                               analyze_fn=lambda url, **kw: (
                                   kw["on_phase"]("detect", 0.5, "x") or fake_doc()),
                               fetch_info=fake_info)
        assert api.completed == []

    def test_a_failure_that_is_not_progress_still_fails_the_job(self, tmp_path):
        api = FakeApi([])
        def boom(url, **kw):
            raise RuntimeError("ffmpeg exploded")
        with pytest.raises(RuntimeError):
            worker.process_job(JOB, api, "home", root=tmp_path, weights=None,
                               out_dir=tmp_path / "out", analyze_fn=boom,
                               fetch_info=fake_info)


class TestStackDumpsCanBeRequested:
    def test_sigusr1_is_registered_for_a_stack_dump(self):
        """A stalled worker has to be inspectable, or a hang is unfalsifiable."""
        import faulthandler
        import signal

        worker._enable_stack_dumps()
        assert faulthandler.is_enabled()
        # Registering again is harmless and proves the handler is ours.
        faulthandler.unregister(signal.SIGUSR1)
