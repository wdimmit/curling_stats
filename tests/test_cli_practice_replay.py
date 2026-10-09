"""`curling-score practice-replay`: a cached video, watched as a practice session."""

import json

from curling_score import cli


def test_a_replay_begins_a_lookback_before_start_and_writes_its_throws(tmp_path, monkeypatch):
    from curling_score import analyze
    from curling_score.live import replay
    from curling_score.practice import harness

    made = {}

    class Rec:
        def __init__(self, source, path, speed=1.0, *, start_s=0.0, end_s=None, burst_s=0.0):
            made.update(source=str(source), speed=speed, start_s=start_s, end_s=end_s,
                        burst_s=burst_s)
            self.path, self.t0_s = path, 1795.0

        def start(self):
            return self

        def stop(self):
            made["stopped"] = True

    class Heads:
        def start(self, rec):
            return self

        def stop(self):
            pass

        def reached(self, t):
            return 0.0

    def run(watch, **kw):
        made.update(since_s=watch.since_s, t0_s=watch.t0_s)
        watch.publish({"practice": 1, "status": "ended", "t0_s": 1795.0, "throws": []})
        watch.done = True

    monkeypatch.setattr(replay, "ReplayRecording", Rec)
    monkeypatch.setattr(harness, "HeadClock", Heads)
    monkeypatch.setattr(harness, "run", run)
    monkeypatch.setattr(analyze, "load_models", lambda *a, **k: (None, None, None))
    out = tmp_path / "practice"
    assert cli.main(["practice-replay", str(tmp_path / "abcdefghijk.mp4"), "--from", "3000",
                     "--to", "4800", "--out", str(out), "--weights", "none"]) == 0
    assert made == {"source": str(tmp_path / "abcdefghijk.mp4"), "speed": 1.0,
                    "start_s": 1800.0, "end_s": 4800.0, "burst_s": 1200.0,
                    "since_s": 1205.0, "t0_s": 1795.0, "stopped": True}
    assert json.loads((out / "practice.json").read_text())["status"] == "ended"
