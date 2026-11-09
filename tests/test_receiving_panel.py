"""Two overhead models: overhead-4 reads each end's receiving panel, where its
player class lets the house read tell a stone from a sweeper's shoe;
overhead-3 reads the thrower's panel, the profile that finds games and ends,
and practice. With one weights file for both -- until overhead-4 ships --
nothing loads twice and nothing changes."""

from types import SimpleNamespace

import pytest

from curling_score import analyze, version
from curling_score import weights as weights_mod
from curling_score.game import format as format_mod
from curling_score.game.profile import PanelSetup
from curling_score.game.segment import EndSegment, GameSegment
from curling_score.geometry.calibrate import PanelCalib


def panel(rect):
    return PanelSetup(rect=rect, calib=PanelCalib(center_px=(80.0, 120.0), px_per_m=40.0,
                                                  edge_erosion_px=2.0, residual_m=0.001,
                                                  flipped=False))


TOP, BOTTOM = panel((0, 0, 297, 514)), panel((0, 540, 297, 516))


class TestBuildOneEnd:
    """The stubs stand in for the detection calls and note which detector
    read which panel."""

    def build(self, monkeypatch, *, one_pass, house_detector):
        used = {}

        def spans(path, specs, end_s, detector=None):
            for spec in specs:
                used.setdefault(spec[0].rect, []).append(
                    spec[3] if len(spec) > 3 else detector)
            return [[] for _ in specs]

        def end(path, setup, end, fps, detector=None, **kw):
            used.setdefault(setup.rect, []).append(detector)
            return []

        def span(path, setup, start_s, end_s, fps, detector=None, **kw):
            used.setdefault(setup.rect, []).append(detector)
            return []

        monkeypatch.setattr(analyze.sequence, "detect_spans", spans)
        monkeypatch.setattr(analyze.sequence, "detect_end", end)
        monkeypatch.setattr(analyze.sequence, "detect_span", span)
        ctx = analyze.EndContext(
            path="rec.ts", read_path="rec.ts", read_setups={"top": TOP, "bottom": BOTTOM},
            sideviews=None, detector="overhead-3", house_detector=house_detector,
            broom_model=None, line_model=None, fmt=format_mod.FOURS, use_cache=False,
            one_pass=one_pass)
        game = GameSegment(index=0, start_s=1000.0, end_s=1900.0,
                           ends=[EndSegment(number=1, house="top", start_s=1000.0,
                                            end_s=1900.0)])
        analyze.build_one_end(ctx, game, game.ends[0], prev_end_s=None, board_score=None)
        return used

    @pytest.mark.parametrize("one_pass", [True, False])
    def test_receiving_panel_reads_with_the_house_detector(self, monkeypatch, one_pass):
        used = self.build(monkeypatch, one_pass=one_pass, house_detector="overhead-4")
        assert used == {TOP.rect: ["overhead-4"], BOTTOM.rect: ["overhead-3"]}

    @pytest.mark.parametrize("one_pass", [True, False])
    def test_without_one_both_panels_read_with_the_stone_detector(self, monkeypatch,
                                                                    one_pass):
        used = self.build(monkeypatch, one_pass=one_pass, house_detector=None)
        assert used == {TOP.rect: ["overhead-3"], BOTTOM.rect: ["overhead-3"]}


class TestHeldUnderAPlayer:
    """A stone put back because a player stood over it (`shots.from_deliveries`
    reports it in `held`) is one progress line in the end's log."""

    def run(self, monkeypatch, held):
        monkeypatch.setattr(analyze.sequence, "detect_end", lambda *a, **kw: [])
        monkeypatch.setattr(analyze.sequence, "detect_span", lambda *a, **kw: [])

        def from_deliveries(*a, held=None, **kw):
            held.extend(held_rows)
            return []

        held_rows = held
        monkeypatch.setattr(analyze.shots_mod, "from_deliveries", from_deliveries)
        lines = []
        ctx = analyze.EndContext(
            path="rec.ts", read_path="rec.ts", read_setups={"top": TOP, "bottom": BOTTOM},
            sideviews=None, detector=None, broom_model=None, line_model=None,
            fmt=format_mod.FOURS, use_cache=False, one_pass=False, progress=lines.append)
        game = GameSegment(index=0, start_s=1000.0, end_s=1900.0,
                           ends=[EndSegment(number=3, house="top", start_s=1000.0,
                                            end_s=1900.0)])
        analyze.build_one_end(ctx, game, game.ends[0], prev_end_s=None, board_score=None)
        return lines

    def test_a_held_stone_is_logged_with_its_rock(self, monkeypatch):
        lines = self.run(monkeypatch, [{"shot": 15, "color": "red", "x": -1.65, "y": -1.52},
                                       {"shot": 16, "color": "yellow", "x": 0.0, "y": 0.5}])
        assert "    end 3: 2 stone(s) held under a player: rock 15 red, rock 16 yellow" in lines

    def test_nothing_held_logs_nothing(self, monkeypatch):
        assert not [l for l in self.run(monkeypatch, []) if "held" in l]


class TestLoadHouseDetector:
    class Fake:
        made = []

        def __init__(self, weights, conf, device, imgsz):
            self.weights, self.conf, self.device, self.imgsz = weights, conf, device, imgsz
            self.model = SimpleNamespace(overrides={})
            self.made.append(self)

    @pytest.fixture
    def fake(self, monkeypatch):
        from curling_score.detect import yolo

        self.Fake.made = []
        monkeypatch.setattr(yolo, "YoloDetector", self.Fake)
        return self.Fake

    def test_one_file_for_both_loads_nothing(self, fake, tmp_path):
        """One model in memory, and the detection cache's keys as before."""
        w = tmp_path / "overhead-3.pt"; w.write_bytes(b"3")
        stones = object()
        assert analyze.load_house_detector(stones, w, str(w), 448, None,
                                           progress=lambda *_: None) is stones
        assert analyze.load_house_detector(stones, w, None, 448, None,
                                           progress=lambda *_: None) is stones
        assert fake.made == []

    def test_a_model_of_its_own_is_loaded_as_the_first_was(self, fake, tmp_path):
        w3, w4 = tmp_path / "overhead-3.pt", tmp_path / "overhead-4.pt"
        w3.write_bytes(b"3"); w4.write_bytes(b"4")
        got = analyze.load_house_detector(object(), w3, w4, 640, "cuda:0",
                                          progress=lambda *_: None)
        assert fake.made == [got]
        assert (got.weights, got.conf, got.device, got.imgsz) == (w4, 0.30, "cuda:0", 640)
        assert got.model.overrides["half"] is True


class TestAnalyze:
    """The video entry point: the profile keeps overhead-3, the ends get both,
    and the processing version names both."""

    def run(self, monkeypatch, tmp_path, stones_weights):
        seen = {}
        monkeypatch.setattr(analyze.cache, "ensure_cached", lambda url, **kw: "v.mp4")
        monkeypatch.setattr(analyze.F, "sample_keyframes", lambda *a, **kw: [])
        monkeypatch.setattr(analyze, "calibrate_from", lambda frames, **kw: (
            None, {"top": TOP, "bottom": BOTTOM}, None))
        monkeypatch.setattr(analyze, "load_models",
                            lambda weights, *a, **kw: ("overhead-3", None, None))
        real_load = analyze.load_house_detector

        def load_house(detector, weights, house_weights, *a, **kw):
            if weights_mod.same_file(weights, house_weights) or house_weights is None:
                return real_load(detector, weights, house_weights, *a, **kw)
            return "overhead-4"

        monkeypatch.setattr(analyze, "load_house_detector", load_house)

        def build_profile(path, setups, sweep=None, detector=None):
            seen["profile"] = detector
            return []

        monkeypatch.setattr(analyze.profile, "build_profile", build_profile)
        monkeypatch.setattr(analyze.segment, "segment_games", lambda samples, **kw: [])
        monkeypatch.setattr(analyze, "build_games",
                            lambda ctx, games, **kw: seen.setdefault("ctx", ctx) and [])
        monkeypatch.setattr(analyze, "calibration_block", lambda *a: {})
        monkeypatch.setattr(analyze.timeline, "format_check", lambda *a: None)
        monkeypatch.setattr(analyze.timeline, "build_document",
                            lambda **kw: seen.setdefault("doc", kw))
        info = SimpleNamespace(title="Sheet 3", duration_s=3600.0, sheet=3,
                               video_id="vid00000001")
        analyze.analyze("https://www.youtube.com/watch?v=vid00000001", root=tmp_path,
                        use_proxy=False, weights=stones_weights, info=info,
                        skip_scoreboard=True, skip_longview=True, progress=lambda *_: None)
        return seen

    def test_profile_keeps_the_stone_detector(self, monkeypatch, tmp_path):
        o4 = tmp_path / "overhead-4.pt"; o4.write_bytes(b"4")
        monkeypatch.setenv(weights_mod.RECEIVING_ENV_VAR, str(o4))
        stones = weights_mod.default_path()
        seen = self.run(monkeypatch, tmp_path, stones)
        assert seen["profile"] == "overhead-3"
        assert (seen["ctx"].detector, seen["ctx"].house_detector) == ("overhead-3",
                                                                      "overhead-4")
        assert f"+house-{version.model_id(o4)}" in seen["doc"]["processing_version"]

    def test_one_weights_file_changes_nothing(self, monkeypatch, tmp_path):
        stones = weights_mod.default_path()
        monkeypatch.setenv(weights_mod.RECEIVING_ENV_VAR, str(stones))
        seen = self.run(monkeypatch, tmp_path, stones)
        assert seen["profile"] == "overhead-3"
        assert seen["ctx"].house_detector == "overhead-3"
        assert "+house" not in seen["doc"]["processing_version"]

    def test_unpinned_the_receiving_panel_reads_with_the_bundled_model(
            self, monkeypatch, tmp_path):
        monkeypatch.delenv(weights_mod.RECEIVING_ENV_VAR, raising=False)
        monkeypatch.delenv(weights_mod.ENV_VAR, raising=False)
        seen = self.run(monkeypatch, tmp_path, weights_mod.default_path())
        assert seen["ctx"].house_detector == "overhead-4"   # the fake's second model
        assert "+house-overhead-5-" in seen["doc"]["processing_version"]
