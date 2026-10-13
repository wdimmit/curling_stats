"""The clips worker loop, against a fake API (service/clipworker.py)."""

from pathlib import Path

import pytest

from curling_score.service import clipworker, worker
from tests.test_game_clips import CAL, made, rock


class FakeClipApi:
    def __init__(self, jobs, lost_on=None):
        self.jobs, self.lost_on = list(jobs), lost_on
        self.completed, self.failed, self.uploads, self.progress = [], [], {}, 0

    def claim_clips(self, worker_id):
        return self.jobs.pop(0) if self.jobs else None

    def clip_progress(self, job_id, worker_id):
        if self.lost_on == "progress":
            raise worker.Lost("given to another worker")
        self.progress += 1
        return {"ok": True}

    def clip_artifacts(self, job_id, worker_id, files):
        return {"uploads": [{"name": f["name"], "url": f"memory://{f['name']}", "headers": {}}
                            for f in files]}

    def upload(self, url, headers, data):
        self.uploads[url] = data

    def clip_complete(self, job_id, worker_id, rocks):
        if self.lost_on == "complete":
            raise worker.Lost("given to another worker")
        self.completed.append((job_id, rocks))
        return {"ok": True}

    def clip_fail(self, job_id, worker_id, error):
        self.failed.append((job_id, error))
        return {"ok": True}


def a_job(*rocks):
    return {"id": "cj_1", "run_id": "r1", "video_id": "VID",
            "spec": {"calibration": CAL, "rocks": list(rocks) or [rock()]}}


@pytest.fixture
def cache(tmp_path):
    (tmp_path / "c2" / "videos").mkdir(parents=True)
    (tmp_path / "c2" / "videos" / "VID.mp4").write_bytes(b"video")
    return [tmp_path / "c1", tmp_path / "c2"]


class TestOneJob:
    def test_video_gone_means_no_clips_and_no_cutting(self, tmp_path):
        api = FakeClipApi([])
        got = clipworker.process(a_job(rock(), rock(name="0.3.7")), api, "w",
                                 sources=[tmp_path], workdir=tmp_path / "w", cut=made)
        assert got == {"0.3.5": {"clip": None, "reason": "video_gone"},
                       "0.3.7": {"clip": None, "reason": "video_gone"}}
        assert api.completed == [("cj_1", got)] and api.uploads == {}

    def test_one_asked_while_live_waits_out_a_recording_in_no_cache(self, tmp_path):
        """Between the stream ending and its recording being filed there is
        a moment with neither: tried again shortly, not called gone."""
        api = FakeClipApi([])
        job = a_job()
        job["spec"]["live"] = True
        with pytest.raises(RuntimeError):
            clipworker.process(job, api, "w", sources=[tmp_path], workdir=tmp_path / "w",
                               cut=made)
        assert api.completed == []

    def test_clips_are_cut_from_whichever_cache_has_the_video(self, cache, tmp_path):
        seen = []

        def cut(plan, views, out, **kw):
            seen.append(views["left"][0])
            Path(out).write_bytes(b"mp4")

        api = FakeClipApi([])
        got = clipworker.process(a_job(rock(), rock(name="0.3.7", t_release_s=None)), api, "w",
                                 sources=cache, workdir=tmp_path / "w", cut=cut)
        assert seen == [cache[1] / "videos" / "VID.mp4"]
        assert api.uploads == {"memory://0.3.5.mp4": b"mp4"}
        assert got["0.3.5"]["clip"] is True and got["0.3.7"]["reason"] == "no_release"
        assert api.progress == 2
        assert api.completed == [("cj_1", got)]

    def test_an_upload_that_fails_costs_that_rock(self, cache, tmp_path):
        api = FakeClipApi([])

        def broken(url, headers, data):
            raise OSError("network")

        api.upload = broken
        got = clipworker.process(a_job(), api, "w", sources=cache, workdir=tmp_path / "w",
                                 cut=made)
        assert got == {"0.3.5": {"clip": None, "reason": "cut_failed"}}


class TestTheLoop:
    def test_a_job_lost_mid_way_is_walked_away_from(self, cache, tmp_path):
        api = FakeClipApi([a_job()], lost_on="progress")
        clipworker.run_forever(api, "w", sources=cache, workdir=tmp_path / "w",
                               sleep=lambda s: None, once=True, cut=made)
        assert api.completed == [] and api.failed == []

    def test_a_complete_refused_as_lost_is_left_alone(self, cache, tmp_path):
        api = FakeClipApi([a_job()], lost_on="complete")
        clipworker.run_forever(api, "w", sources=cache, workdir=tmp_path / "w",
                               sleep=lambda s: None, once=True, cut=made)
        assert api.failed == []

    def test_a_job_that_breaks_is_reported(self, tmp_path, monkeypatch):
        api = FakeClipApi([a_job()])

        def boom(*a, **kw):
            raise ValueError("bad spec")

        monkeypatch.setattr(clipworker.game_clips, "find_video", boom)
        clipworker.run_forever(api, "w", sources=[tmp_path], workdir=tmp_path / "w",
                               sleep=lambda s: None, once=True)
        assert api.failed == [("cj_1", "ValueError: bad spec")]

    def test_nothing_to_do(self, tmp_path):
        naps = []
        clipworker.run_forever(FakeClipApi([]), "w", sources=[tmp_path], workdir=tmp_path,
                               sleep=naps.append, once=True)
        assert naps == []


def test_the_caches_from_the_environment():
    assert clipworker.sources_from_env("/data/src1:/data/src2::") == [
        Path("/data/src1"), Path("/data/src2")]
    assert clipworker.sources_from_env(None) == []


def _clip_env(monkeypatch, tmp_path, sources):
    monkeypatch.setenv("API_URL", "http://api")
    monkeypatch.setenv("WORKER_TOKEN", "t")
    monkeypatch.setenv("CURLING_SCORE_CACHE", str(tmp_path))
    monkeypatch.setenv("WORKER_ID", "ryzen-clips")
    monkeypatch.setenv("WORKER_CLIP_SOURCES", sources)


def test_worker_role_clips_runs_the_clips_main_and_nothing_else(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKER_ROLE", "clips")
    monkeypatch.setattr(clipworker, "main", lambda argv=None: 7)
    monkeypatch.setattr(worker, "run_forever", lambda *a, **kw: pytest.fail("pipeline loop ran"))
    monkeypatch.setattr(worker, "resolve_weights", lambda: pytest.fail("a model was loaded"))
    _clip_env(monkeypatch, tmp_path, "/x")
    assert worker.main([]) == 7


class TestMain:
    def test_reads_the_environment(self, tmp_path, monkeypatch):
        got = {}
        src = tmp_path / "src1"
        (src / "videos").mkdir(parents=True)
        _clip_env(monkeypatch, tmp_path / "cache", f"{src}:/data/src2")
        monkeypatch.setattr(clipworker, "run_forever",
                            lambda api, wid, **kw: got.update(wid=wid, **kw))
        assert clipworker.main(["--once"]) == 0
        assert got["wid"] == "ryzen-clips"
        assert got["sources"] == [src, Path("/data/src2")]
        assert got["workdir"] == tmp_path / "cache" / "clips"
        assert got["once"] is True

    @pytest.mark.parametrize("sources", ["", "NOVIDEOS"])
    def test_refuses_to_start_without_a_cache_holding_videos(self, tmp_path, monkeypatch,
                                                              sources, caplog):
        if sources:
            (tmp_path / "empty").mkdir()
            sources = str(tmp_path / "empty")
        _clip_env(monkeypatch, tmp_path, sources)
        monkeypatch.setattr(clipworker, "run_forever",
                            lambda *a, **kw: pytest.fail("it started"))
        assert clipworker.main([]) == 2
        assert "WORKER_CLIP_SOURCES" in caplog.text

    def test_one_good_cache_is_enough(self, tmp_path, monkeypatch):
        (tmp_path / "good" / "videos").mkdir(parents=True)
        _clip_env(monkeypatch, tmp_path, f"{tmp_path / 'missing'}:{tmp_path / 'good'}")
        ran = []
        monkeypatch.setattr(clipworker, "run_forever", lambda *a, **kw: ran.append(1))
        assert clipworker.main([]) == 0 and ran == [1]


class TestScratchAndRetries:
    def test_the_scratch_is_gone_after_a_job(self, cache, tmp_path):
        work = tmp_path / "w"
        clipworker.process(a_job(), FakeClipApi([]), "w", sources=cache, workdir=work, cut=made)
        assert list(work.iterdir()) == []
        clipworker.process(a_job(), FakeClipApi([]), "w", sources=[tmp_path / "none"],
                           workdir=work)
        assert list(work.iterdir()) == []

    def test_a_transient_complete_failure_is_retried(self, cache, tmp_path):
        api = FakeClipApi([a_job()])
        real, calls, naps = api.clip_complete, [], []

        def flaky(job_id, worker_id, rocks):
            calls.append(1)
            if len(calls) < 3:
                raise OSError("network")
            return real(job_id, worker_id, rocks)

        api.clip_complete = flaky
        clipworker.run_forever(api, "w", sources=cache, workdir=tmp_path / "w",
                               sleep=naps.append, once=True, cut=made)
        assert len(api.completed) == 1 and api.failed == []
        assert naps == [clipworker.COMPLETE_RETRY_S] * 2

    def test_three_failures_reach_clip_fail(self, cache, tmp_path):
        api = FakeClipApi([a_job()])

        def down(*a):
            raise OSError("network")

        api.clip_complete = down
        clipworker.run_forever(api, "w", sources=cache, workdir=tmp_path / "w",
                               sleep=lambda s: None, once=True, cut=made)
        assert api.failed == [("cj_1", "OSError: network")]
