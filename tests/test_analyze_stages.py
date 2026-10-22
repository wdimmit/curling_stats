"""The per-stage seconds an end's log line reports."""
from curling_score import analyze


class FakeClock:
    def __init__(self, *times):
        self.times = list(times)

    def __call__(self):
        return self.times.pop(0)


class TestStageClock:
    def test_each_mark_charges_the_time_since_the_one_before(self):
        clock = analyze.StageClock(FakeClock(0.0, 50.0, 53.0, 80.0))
        clock.mark("detect")
        clock.mark("rules")
        clock.mark("lines")
        assert clock.seconds == {"detect": 50.0, "rules": 3.0, "lines": 27.0}

    def test_a_stage_marked_twice_adds_up(self):
        clock = analyze.StageClock(FakeClock(0.0, 10.0, 12.0, 30.0))
        clock.mark("detect")
        clock.mark("rules")
        clock.mark("detect")
        assert clock.seconds == {"detect": 28.0, "rules": 2.0}

    def test_the_line_runs_in_pipeline_order_with_the_total(self):
        clock = analyze.StageClock(FakeClock(0.0, 27.0, 30.0, 90.5))
        clock.mark("spin")
        clock.mark("rules")
        clock.mark("detect")
        assert clock.line() == "detect 60.5, rules 3.0, spin 27.0 s (total 90.5 s)"


def test_the_release_read_is_reported_between_the_lines_and_the_spin():
    clock = analyze.StageClock(FakeClock(0.0, 1.0, 2.0, 4.0))
    clock.mark("spin")
    clock.mark("release")
    clock.mark("lines")
    assert clock.line().startswith("lines 2.0, release 1.0, spin 1.0 s")


class TestTheReleaseModel:
    """Loaded with the others, before any detection runs, and only with the
    line: the release is read along the line's delivery path."""

    def load(self, monkeypatch, line_model, model):
        from curling_score import weights as weights_mod
        from curling_score.detect import releasemodel
        calls, said = [], []
        monkeypatch.setattr(releasemodel, "default_model",
                            lambda: calls.append(1) or model)
        monkeypatch.setattr(weights_mod, "release_path", lambda: "weights/release1.pt")
        got = analyze.load_release_model(line_model, said.append)
        return got, calls, said

    def test_it_is_loaded_with_the_line_model(self, monkeypatch):
        model = object()
        got, calls, said = self.load(monkeypatch, "line", model)
        assert got is model and calls == [1]
        assert said == ["reading releases with weights/release1.pt"]

    def test_no_line_no_release_model(self, monkeypatch):
        got, calls, said = self.load(monkeypatch, None, object())
        assert got is None and calls == [] and said == []

    def test_none_configured_says_nothing(self, monkeypatch):
        got, calls, said = self.load(monkeypatch, "line", None)
        assert got is None and calls == [1] and said == []
