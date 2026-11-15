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


class TestTheContextCarriesFeeds:
    """`analyze` hands the pipeline a `Feed`: the original for ``path``, and for
    ``read_path`` the same, or with the overhead panels on the strip proxy."""

    class Stop(Exception):
        pass

    def run(self, monkeypatch, tmp_path, use_proxy):
        from pathlib import Path

        from curling_score import weights as weights_mod  # noqa: F401
        from curling_score.game import boardsplit, profile, segment
        from curling_score.ingest import cache, frames as F, proxy

        seen = {}
        original = tmp_path / "VXU9xwmugRg.mp4"
        strip_file = tmp_path / "proxies" / "VXU9xwmugRg.strip.mp4"
        panels = SimpleNamespace(top=(0, 0, 297, 514), bottom=(0, 514, 297, 514))
        setups = {n: split_context().read_setups[n] for n in ("top", "bottom")}
        setups = {n: replace(s, name=n) for n, s in setups.items()}
        monkeypatch.setattr(cache, "ensure_cached", lambda *a, **k: original)
        monkeypatch.setattr(F, "sample_keyframes", lambda *a, **k: ["frame"])
        monkeypatch.setattr(analyze, "calibrate_from",
                            lambda *a, **k: (panels, setups, None))
        monkeypatch.setattr(proxy, "ensure_proxy", lambda *a, **k: strip_file)
        monkeypatch.setattr(analyze, "load_models", lambda *a, **k: (None, None, None))
        monkeypatch.setattr(analyze, "load_release_model", lambda *a, **k: None)
        monkeypatch.setattr(F, "keyframe_sweep",
                            lambda path, **k: seen.setdefault("sweep", []).append(path) or [])
        monkeypatch.setattr(profile, "build_profile",
                            lambda video, setups, sweep=None, detector=None:
                            seen.update(profiled=video) or [])
        monkeypatch.setattr(segment, "segment_games", lambda *a, **k: [])
        monkeypatch.setattr(boardsplit, "join_games", lambda games, states: games)
        monkeypatch.setattr(boardsplit, "split_games", lambda games, states: games)

        def build_games(ctx, games, **k):
            seen.update(ctx=ctx, board_reader=k["read_board"])
            raise self.Stop

        monkeypatch.setattr(analyze, "build_games", build_games)
        info = SimpleNamespace(title="t", duration_s=1.0, sheet=2, video_id="VXU9xwmugRg")
        try:
            analyze.analyze("VXU9xwmugRg", info=info, use_proxy=use_proxy,
                            start_s=0.0, end_s=60.0)
        except self.Stop:
            pass
        return seen, original, strip_file

    def test_endcontext_paths_are_feeds(self, monkeypatch, tmp_path):
        from curling_score.feeds import Feed

        seen, original, proxy_file = self.run(monkeypatch, tmp_path, use_proxy=True)
        ctx = seen["ctx"]
        assert isinstance(ctx.path, Feed) and isinstance(ctx.read_path, Feed)
        assert ctx.path.single() == original
        # The strip proxy holds the houses; the side views and the board stay on the original.
        assert ctx.read_path.path_for("top") == proxy_file
        assert ctx.read_path.path_for("bottom") == proxy_file
        for view in ("left", "right", "board"):
            assert ctx.read_path.path_for(view) == original
        # The profile and its sweep read the proxy too.
        assert seen["profiled"] == ctx.read_path
        assert seen["sweep"] == [proxy_file]

    def test_without_a_proxy_the_read_path_is_the_path(self, monkeypatch, tmp_path):
        seen, original, _ = self.run(monkeypatch, tmp_path, use_proxy=False)
        ctx = seen["ctx"]
        assert ctx.read_path == ctx.path
        assert ctx.path.single() == original
        assert seen["sweep"] == [original]


class TestAnalysingACameraGame:
    """Phase 3: a camera game read again from its kept files -- no download, no
    strip proxy, calibrated from the cameras, published on its clock."""

    def test_its_files_are_read_and_its_clock_kept(self, monkeypatch, tmp_path):
        from curling_score.feeds import cameras
        from curling_score.game import boardsplit, profile, segment
        from curling_score.ingest import cache, frames as F, proxy

        def refuse(*a, **k):
            raise AssertionError("asked for a download or a proxy")

        panels = SimpleNamespace(top=(0, 0, 297, 514), bottom=(0, 514, 297, 514))
        setups = {n: split_context().read_setups[n] for n in ("top", "bottom")}
        setups = {n: replace(s, name=n) for n, s in setups.items()}
        seen = {}
        monkeypatch.setattr(cache, "ensure_cached", refuse)
        monkeypatch.setattr(proxy, "ensure_proxy", refuse)
        monkeypatch.setattr(cameras, "calibrate",
                            lambda feed, until_s, **k: seen.update(cal=(feed, until_s))
                            or (panels, setups, None))
        monkeypatch.setattr(analyze, "load_models", lambda *a, **k: (None, None, None))
        monkeypatch.setattr(analyze, "load_release_model", lambda *a, **k: None)
        monkeypatch.setattr(F, "keyframe_sweep", lambda path, **k: [])
        monkeypatch.setattr(profile, "build_profile", lambda *a, **k: [])
        monkeypatch.setattr(segment, "segment_games", lambda *a, **k: [])
        monkeypatch.setattr(boardsplit, "join_games", lambda games, states: games)
        monkeypatch.setattr(boardsplit, "split_games", lambda games, states: games)
        monkeypatch.setattr(analyze, "build_games",
                            lambda ctx, games, **k: seen.update(ctx=ctx) or [])
        feed = cameras.camera_feed(tmp_path / "kept" / "dbV9wuA45Z0", 3)
        clock = {"kind": "cameras", "aligned": "segment", "offset_s": -908.16,
                 "steps": [[0.0, -908.16]]}
        info = SimpleNamespace(title="11/5 - Sheet 3", duration_s=11699.0, sheet=3,
                               video_id="dbV9wuA45Z0")
        doc = analyze.analyze("dbV9wuA45Z0", info=info, feed=feed, clock=clock,
                              skip_scoreboard=True)
        assert seen["cal"] == (feed, 11699.0)
        assert seen["ctx"].path == feed and seen["ctx"].read_path == feed
        assert doc["source"]["clock"] == clock
        assert doc["source"]["duration_s"] == round(11699.0 - 908.16, 2)


class TestCalibrateGivenPanels:
    """Phase 2: a camera feed's panels and side views come from the sheet's
    layout, not from looking for them."""

    def test_calibrate_from_with_given_panels_skips_detection(self, monkeypatch):
        import numpy as np

        from curling_score.game import profile
        from curling_score.geometry import layout as glayout
        from curling_score.geometry import sideview

        def refuse(*a, **k):
            raise AssertionError("looked for what it was given")

        seen = {}
        monkeypatch.setattr(glayout, "detect_panels", refuse)
        monkeypatch.setattr(sideview, "locate", refuse)
        monkeypatch.setattr(profile, "calibrate_panels",
                            lambda frames, panels: seen.setdefault("panels", panels) and {})
        monkeypatch.setattr(sideview, "solve",
                            lambda plate, rect, name="": seen.setdefault(name, rect))
        monkeypatch.setattr(analyze, "_with_lateral", lambda plate, v, n, progress: v)
        panels = glayout.PanelLayout(top=(810, 10, 297, 514), bottom=(810, 552, 297, 517))
        rects = {"left": (0, 0, 800, 1080), "right": (1120, 0, 800, 1080)}
        frames = [np.zeros((1080, 1920, 3), np.uint8)] * 2
        got_panels, _, sides = analyze.calibrate_from(
            frames, skip_longview=False, progress=lambda m: None, panels=panels,
            side_rects=rects)
        assert got_panels is panels and seen["panels"] is panels
        assert seen["left"] == rects["left"] and seen["right"] == rects["right"]
        assert sides == rects

    def test_pipeline_calibrates_a_camera_feed_through_the_layout(self, monkeypatch):
        from pathlib import Path
        from types import SimpleNamespace

        import numpy as np

        from curling_score.cameras import layout as clayout
        from curling_score.feeds import cameras
        from curling_score.game import profile
        from curling_score.live.session import VideoPipeline

        sl = clayout.load(2)
        outs = {n: cameras.placement_for(sl, n).out for n in cameras.CUT_VIEWS}
        views = {n: [np.zeros((h, w, 3), np.uint8)] * 3 for n, (_, _, w, h) in outs.items()}
        monkeypatch.setattr(cameras, "view_keyframes", lambda feed, cuts, until_s, count, **k: views)
        seen = {}

        def calibrate_from(frames, *, skip_longview, progress, panels=None, side_rects=None):
            seen.update(n=len(frames), shape=frames[0].shape, panels=panels, rects=side_rects)
            setups = {n: profile.PanelSetup(rect=getattr(panels, n),
                                            calib=SimpleNamespace(px_per_m=100.0))
                      for n in ("top", "bottom")}
            from curling_score.geometry.sideview import SideView
            sides = {n: SideView(rect=r, tee_row=1.0, hog_row=2.0, name=n)
                     for n, r in side_rects.items()}
            return panels, setups, sides

        monkeypatch.setattr(analyze, "calibrate_from", calibrate_from)
        cal = VideoPipeline(progress=lambda m: None).calibrate(cameras.camera_feed(Path("d"), 2),
                                                               60.0)
        assert seen["n"] == 3 and seen["shape"] == (1080, 1920, 3)
        assert (seen["panels"].top, seen["panels"].bottom) == (outs["top"], outs["bottom"])
        assert seen["rects"] == {"left": outs["left"], "right": outs["right"]}
        cuts = cameras.view_cuts(sl)
        assert cal.setups["top"].transform == cuts["top"]
        assert cal.setups["top"].rect == (0, 0, *cuts["top"].out_size)
        assert cal.sideviews["right"].transform == cuts["right"]
        assert cal.until_s == 60.0
