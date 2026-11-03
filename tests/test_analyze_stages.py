"""The per-stage seconds an end's log line reports, and the long-camera
passes they time."""
import logging
from dataclasses import replace
from types import SimpleNamespace

from curling_score import analyze
from curling_score.detect import longview
from curling_score.game import (broomtime, hogtime, linetime, releasetime, sidereleases,
                                spintime)
from tests.test_analyze_split import GAME, context as split_context, seen  # noqa: F401


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


class KeeperStub:
    """A lane keeper as `build_one_end` handles it."""

    def __init__(self, calls):
        self.calls, self.cleared, self.nbytes = calls, False, 3e6

    def rewindow(self, windows):
        self.calls.append(("rewindow", longview._keeping))

    def clear(self):
        self.cleared = True


class TestTheLongCameraPasses:
    """An end's passes over the long cameras, in order, the release read on
    the frames the passes before it kept."""

    def build(self, monkeypatch, fail=()):
        calls, said, marks = [], [], []
        keeper = KeeperStub(calls)

        def stage(name):
            def run(*a, **k):
                calls.append((name, longview._keeping))
                if name in fail:
                    raise RuntimeError(f"{name} fell over")
                return 0
            return run

        for mod, fn, name in ((hogtime, "time_hog_crossings", "hog"),
                              (sidereleases, "time_side_releases", "side"),
                              (broomtime, "time_target_brooms", "brooms"),
                              (linetime, "time_lines", "lines"),
                              (releasetime, "time_releases", "release"),
                              (spintime, "time_spins", "spin")):
            monkeypatch.setattr(mod, fn, stage(name))
        monkeypatch.setattr(releasetime, "keeper_for", lambda shots, view, model: keeper)
        mark = analyze.StageClock.mark
        monkeypatch.setattr(analyze.StageClock, "mark",
                            lambda self, name: marks.append(name) or mark(self, name))
        views = {n: SimpleNamespace(name=n, rect=r, has_lateral=True)
                 for n, r in (("left", (0, 0, 810, 1080)), ("right", (1107, 0, 813, 1080)))}
        ctx = replace(split_context(), sideviews=views, release_model="hand",
                      progress=said.append)
        try:
            built = analyze.build_one_end(ctx, GAME, GAME.ends[0], prev_end_s=None,
                                          board_score=None)[0]
        except RuntimeError as exc:
            built = exc
        return built, calls, keeper, marks, said

    def test_the_release_is_read_after_the_lines_and_before_the_spin(self, monkeypatch,
                                                                      seen):  # noqa: F811
        built, calls, keeper, marks, said = self.build(monkeypatch)
        assert [c[0] for c in calls] == ["hog", "side", "rewindow", "brooms", "lines",
                                         "release", "spin"]
        # The keeper watches the decodes from the hog crossing to the lines,
        # and nothing after.
        assert all(k is keeper for _name, k in calls[:5])
        assert all(k is None for _name, k in calls[5:])
        assert keeper.cleared and longview._keeping is None
        assert marks.index("lines") < marks.index("release") < marks.index("aim") \
            < marks.index("spin")
        assert "    end 1: release 0/0 in 0.0 s (3 MB kept)" in said
        assert "    end 1: aim lines 0/0" in said
        assert built["shots"] == []

    def test_a_pass_that_raises_still_lets_the_keeper_go(self, monkeypatch,
                                                         seen):  # noqa: F811
        built, calls, keeper, _marks, _said = self.build(monkeypatch, fail=("lines",))
        assert isinstance(built, RuntimeError)
        assert keeper.cleared and longview._keeping is None
        assert "release" not in [c[0] for c in calls]

    def test_a_release_read_that_raises_costs_only_the_releases(self, monkeypatch, caplog,
                                                                seen):  # noqa: F811
        with caplog.at_level(logging.ERROR):
            built, calls, keeper, _marks, _said = self.build(monkeypatch, fail=("release",))
        assert not isinstance(built, Exception) and built["number"] == 1
        assert [c[0] for c in calls][-2:] == ["release", "spin"]
        assert keeper.cleared and longview._keeping is None
        assert "release fell over" in caplog.text
