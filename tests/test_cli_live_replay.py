"""`curling-score live-replay`: a cached video, processed as if it were live."""

import json

from curling_score import cli


def _model(tmp_path):
    """A weights file to name: load_models is faked, so it is never read."""
    path = tmp_path / "model.pt"
    path.write_bytes(b"x")
    return str(path)


def test_each_published_timeline_lands_in_the_out_directory(tmp_path, monkeypatch):
    from curling_score import analyze
    from curling_score.live import replay, runner

    started = {}

    class Rec:
        def __init__(self, source, path, speed=1.0):
            started.update(source=str(source), speed=speed)
            self.path = path

        def start(self):
            return self

        def stop(self):
            started["stopped"] = True

    def run(session, **kw):
        for n in (1, 2):
            session.publish({"games": [{"ends": [{}] * n}],
                             "live": {"in_progress": n < 2, "recorded_s": 900.0 * n}})
        session.done = True

    monkeypatch.setattr(replay, "ReplayRecording", Rec)
    monkeypatch.setattr(runner, "run_session", run)
    monkeypatch.setattr(analyze, "load_models", lambda *a, **k: (None, None, None))
    out = tmp_path / "live"
    assert cli.main(["live-replay", str(tmp_path / "abcdefghijk.mp4"), "--out", str(out),
                     "--speed", "4", "--weights", _model(tmp_path)]) == 0
    assert started == {"source": str(tmp_path / "abcdefghijk.mp4"), "speed": 4.0,
                       "stopped": True}
    doc = json.loads((out / "timeline.json").read_text())
    assert doc["live"]["in_progress"] is False
    assert sorted(p.name for p in (out / "publishes").iterdir()) == ["001.json", "002.json"]


# --- a sheet's camera files --------------------------------------------------------

import pytest


def _camera_replay(monkeypatch, started, sessions):
    from curling_score import analyze
    from curling_score.live import replay, runner

    class Cams:
        def __init__(self, source_dir, out_dir, sheet, speed=1.0):
            started.update(source=str(source_dir), sheet=sheet, speed=speed)

        def start(self):
            return self

        def stop(self):
            started["stopped"] = True

    def run(session, **kw):
        sessions.append(session)
        session.publish({"games": [], "live": {"in_progress": False, "recorded_s": 60.0}})
        session.done = True

    monkeypatch.setattr(replay, "CameraReplay", Cams)
    monkeypatch.setattr(runner, "run_session", run)
    monkeypatch.setattr(analyze, "load_models", lambda *a, **k: (None, None, None))


def test_live_replay_cameras_replays_the_four_files(tmp_path, monkeypatch):
    started, sessions = {}, []
    _camera_replay(monkeypatch, started, sessions)
    out = tmp_path / "out"
    assert cli.main(["live-replay", "--cameras", str(tmp_path / "s3-1"), "--sheet", "3",
                     "--video-id", "dbV9wuA45Z0", "--out", str(out), "--weights", "none"]) == 0
    assert started == {"source": str(tmp_path / "s3-1"), "sheet": 3, "speed": 1.0, "stopped": True}
    assert sessions[0].video_id == "dbV9wuA45Z0" and sessions[0].sheet == 3
    assert (out / "publishes" / "001.json").exists()


def test_live_replay_cameras_needs_a_sheet(tmp_path, monkeypatch):
    _camera_replay(monkeypatch, {}, [])
    with pytest.raises(SystemExit) as exc:
        cli.main(["live-replay", "--cameras", str(tmp_path), "--out", str(tmp_path / "o")])
    assert exc.value.code == 2


def test_live_replay_offset_goes_on_the_session(tmp_path, monkeypatch):
    sessions = []
    _camera_replay(monkeypatch, {}, sessions)
    assert cli.main(["live-replay", "--cameras", str(tmp_path), "--sheet", "3",
                     "--offset-s", "600", "--out", str(tmp_path / "o"), "--weights", "none"]) == 0
    assert sessions[0].clock == {"kind": "cameras", "aligned": "segment", "offset_s": 600.0,
                                 "steps": [[0.0, 600.0]]}


def test_align_to_puts_the_tracked_clock_on_the_session(tmp_path, monkeypatch):
    from curling_score.feeds import align

    sessions, asked = [], {}
    _camera_replay(monkeypatch, {}, sessions)
    track = align.ClockTrack()
    track.add(300.0, align.Match(-908.94, 2.0))
    track.add(900.0, align.Match(-908.94, 2.0))

    def fake_track(video, camera, sheet, guess_s, length_s, **kw):
        asked.update(video=str(video), camera=str(camera), sheet=sheet, guess=guess_s)
        return track

    monkeypatch.setattr(align, "track_recording", fake_track)
    monkeypatch.setattr(align, "length_s", lambda p: 11698.0)
    cams = tmp_path / "s4-1"
    assert cli.main(["live-replay", "--cameras", str(cams), "--sheet", "4",
                     "--align-to", str(tmp_path / "v.mp4"), "--guess-s", "-905",
                     "--out", str(tmp_path / "o"), "--weights", "none"]) == 0
    assert asked == {"video": str(tmp_path / "v.mp4"), "camera": str(cams / "long_a.ts"),
                     "sheet": 4, "guess": -905.0}
    assert sessions[0].clock == track.clock()


def test_align_to_that_finds_no_clock_exits_1(tmp_path, monkeypatch):
    from curling_score.feeds import align

    sessions = []
    _camera_replay(monkeypatch, {}, sessions)
    monkeypatch.setattr(align, "track_recording", lambda *a, **k: align.ClockTrack())
    monkeypatch.setattr(align, "length_s", lambda p: 11698.0)
    assert cli.main(["live-replay", "--cameras", str(tmp_path), "--sheet", "4",
                     "--align-to", str(tmp_path / "v.mp4"), "--guess-s", "-905",
                     "--out", str(tmp_path / "o"), "--weights", "none"]) == 1
    assert sessions == []
