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
