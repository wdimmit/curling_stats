"""`curling-score live-replay`: a cached video, processed as if it were live."""

import json

from curling_score import cli


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
                     "--speed", "4", "--weights", "none"]) == 0
    assert started == {"source": str(tmp_path / "abcdefghijk.mp4"), "speed": 4.0,
                       "stopped": True}
    doc = json.loads((out / "timeline.json").read_text())
    assert doc["live"]["in_progress"] is False
    assert sorted(p.name for p in (out / "publishes").iterdir()) == ["001.json", "002.json"]
