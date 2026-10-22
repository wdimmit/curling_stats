"""A claimed rock's clip, cut from the game's video (game/clips.py)."""

import subprocess
from pathlib import Path

import pytest

from curling_score import analyze
from curling_score.game import clips as gc
from curling_score.game.clipburn import Panel
from curling_score.geometry.sideview import SideView
from curling_score.service import gameclips

# The left camera with a lateral fit, the right without: the right's piece
# of a clip is cut but has no lines.
LEFT = SideView(rect=(0, 0, 810, 1080), tee_row=429.95, hog_row=520.0, centre_col=390.23,
                lat_px_per_m_at_tee=150.923, centre_line=(381.563, 0.008215))
RIGHT = SideView(rect=(1110, 0, 810, 1080), tee_row=431.0, hog_row=522.0)
CAL = analyze._side_calibration({"left": LEFT, "right": RIGHT})


def rock(**kw):
    """A rock spec as the API hands it over (service/gameclips.player_rocks)."""
    base = {"name": "0.3.5", "house": "top", "t_release_s": 1000.0, "t_rest_s": 1020.0,
            "t_enter_s": 1013.0, "t_hog_s": None, "v_hog_m_s": None, "t_far_s": None,
            "target_broom": {"x": 0.4, "y": 0.0, "seen": 3.0, "confidence": 0.9},
            "line": {"start": {"x": -0.1, "y": 38.2}, "at_hog": {"x": 0.12, "offset_m": 0.01},
                     "at_broom": {"x": 0.45, "miss_m": 0.05}}}
    base.update(kw)
    return base


def made(plan, views, out, **kw):
    Path(out).write_bytes(b"mp4")


class TestThrowFromRock:
    def test_every_field_the_practice_cutter_reads(self):
        t = gc.throw_from_rock(rock(t_hog_s=1003.1, v_hog_m_s=3.2, t_far_s=1015.5))
        assert t == {"id": "0.3.5", "house": "top", "t_release_s": 1000.0, "t_rest_s": 1020.0,
                     "arrived": True, "t_hog_s": 1003.1, "v_hog_m_s": 3.2, "t_far_s": 1015.5,
                     "t_enter_s": 1013.0,
                     "broom": {"x": 0.4, "y": 0.0},
                     "line": {"at_hog_x": 0.12, "miss_m": 0.05, "aim_x": None,
                              "start": {"x": -0.1, "y": 38.2}, "delivery": None,
                              "release": None}}

    def test_without_the_far_hog_time_the_arrival_stands_in(self):
        assert gc.throw_from_rock(rock())["t_far_s"] == 1013.0

    def test_a_broomless_line_is_aimed_at_the_tee(self):
        t = gc.throw_from_rock(rock(target_broom=None,
                                    line={"at_hog": {"x": 0.1}, "at_broom": None,
                                          "at_tee": {"x": -0.2}}))
        assert t["broom"] is None
        assert t["line"] == {"at_hog_x": 0.1, "miss_m": None, "aim_x": -0.2, "start": None,
                             "delivery": None, "release": None}

    def test_the_delivery_comes_along_for_its_dots(self):
        d = [[-3.0, 38.0, 0.1], [0.5, 30.0, 0.15]]
        line = {**rock()["line"], "delivery": d}
        assert gc.throw_from_rock(rock(line=line))["line"]["delivery"] == d

    def test_the_release_comes_along_for_the_burn_in(self):
        r = [1.23, 30.12, -0.142]
        line = {**rock()["line"], "release": r}
        assert gc.throw_from_rock(rock(line=line))["line"]["release"] == r

    def test_a_line_without_a_release_carries_none(self):
        assert gc.throw_from_rock(rock())["line"]["release"] is None

    def test_no_line_and_never_arrived(self):
        t = gc.throw_from_rock(rock(line=None, t_enter_s=None))
        assert t["line"] is None and t["arrived"] is False and t["t_far_s"] is None


class TestSideviewsFromCalibration:
    def test_the_runs_record_comes_back_as_the_views(self):
        got = gc.sideviews_from_calibration({**CAL, "top": {"rect": [1, 2, 3, 4], "px_per_m": 50.0}})
        assert set(got) == {"left", "right"}
        assert got["left"].rect == LEFT.rect and got["left"].has_lateral
        assert got["left"].tee_row == pytest.approx(LEFT.tee_row, abs=0.01)
        assert got["left"].centre_line == pytest.approx(LEFT.centre_line, abs=1e-6)
        assert got["left"].lat_px_per_m_at_tee == pytest.approx(150.923)
        assert not got["right"].has_lateral

    def test_nothing_calibrated(self):
        assert gc.sideviews_from_calibration(None) == {}
        assert gc.sideviews_from_calibration({"top": {"rect": [0, 0, 1, 1]}}) == {}


class TestFindVideo:
    def test_the_first_cache_that_has_it(self, tmp_path):
        a, b = tmp_path / "a", tmp_path / "b"
        (a / "kept").mkdir(parents=True)
        (a / "kept" / "VID.ts").write_bytes(b"partial")
        (b / "videos").mkdir(parents=True)
        (b / "videos" / "VID.mp4").write_bytes(b"x")
        assert gc.find_video("VID", [a, b]) == b / "videos" / "VID.mp4"

    def test_nowhere(self, tmp_path):
        assert gc.find_video("VID", [tmp_path, tmp_path / "missing"]) is None

    def test_while_its_stream_is_on_the_longest_recording_of_it(self, tmp_path):
        a, b = tmp_path / "a", tmp_path / "b"
        for root, part, size in ((a, "rec.0.ts", 300), (b, "rec.0.ts", 500), (b, "rec.1.ts", 200)):
            (root / "live" / "VID").mkdir(parents=True, exist_ok=True)
            (root / "live" / "VID" / part).write_bytes(b"x" * size)
        (a / "live" / "OTHER").mkdir()
        (a / "live" / "OTHER" / "rec.0.ts").write_bytes(b"x" * 900)
        assert gc.find_video("VID", [a, b]) == b / "live" / "VID" / "rec.0.ts"

    def test_a_filed_video_beats_the_recording(self, tmp_path):
        (tmp_path / "live" / "VID").mkdir(parents=True)
        (tmp_path / "live" / "VID" / "rec.0.ts").write_bytes(b"x" * 900)
        (tmp_path / "videos").mkdir()
        (tmp_path / "videos" / "VID.mp4").write_bytes(b"x")
        assert gc.find_video("VID", [tmp_path]) == tmp_path / "videos" / "VID.mp4"


class TestClipRocks:
    VIEWS = {"left": LEFT, "right": RIGHT}

    def test_a_clip_is_cut_from_both_cameras_marked_and_delivered(self, tmp_path):
        cuts, sent = [], []

        def cut(plan, views, out, **kw):
            cuts.append((plan, views, kw))
            Path(out).write_bytes(b"mp4")

        def deliver(name, path):
            sent.append((name, Path(path).read_bytes()))
            return True

        video = tmp_path / "v.mp4"
        got = gc.clip_rocks([rock()], video, self.VIEWS, tmp_path / "w", deliver, cut=cut)
        assert sent == [("0.3.5", b"mp4")]
        assert cuts[0][1] == {"left": (video, LEFT.rect), "right": (video, RIGHT.rect)}
        # Thrown to the top house: the right camera faces the thrower, then the
        # left one follows to rest. With no far hog time the switch is at the
        # arrival (1013), the end 1.5 s after rest.
        pieces = [(p.view, p.t0, p.t1) for p in cuts[0][0].pieces]
        assert pieces == [("right", 997.0, 1013.0), ("left", 1013.0, 1021.5)]
        # The cutter marks it from the rock and the cameras; nothing is left
        # for the page to draw.
        assert cuts[0][2]["throw"]["id"] == "0.3.5"
        assert cuts[0][2]["sideviews"] is self.VIEWS
        assert got["0.3.5"] == {"clip": True, "burned": True, "style": gc.CLIP_STYLE}
        assert list((tmp_path / "w").iterdir()) == []

    def test_with_the_overhead_panel_the_arrival_is_seen_from_above(self, tmp_path):
        cuts = []

        def cut(plan, views, out, **kw):
            cuts.append((plan, views, kw))
            Path(out).write_bytes(b"mp4")

        video = tmp_path / "v.mp4"
        top = Panel(rect=(812, 11, 298, 510), center_px=(145.0, 136.0), px_per_m=67.0,
                    flipped=False)
        got = gc.clip_rocks([rock()], video, self.VIEWS, tmp_path / "w", lambda n, p: True,
                            panels={"top": top}, cut=cut)
        plan, views, kw = cuts[0]
        # To the top house: the right camera faces the thrower; the top panel
        # takes over half a second before the rock enters it (1013).
        assert [(p.view, p.t0, p.t1) for p in plan.pieces] == \
            [("right", 997.0, 1012.5), (gc.OVERHEAD, 1012.5, 1021.5)]
        assert views[gc.OVERHEAD] == (video, top.rect)
        assert kw["panels"] == {gc.OVERHEAD: top}
        assert got["0.3.5"]["style"] == gc.CLIP_STYLE

    def test_no_release_no_camera_and_a_failed_cut(self, tmp_path):
        def cut(plan, views, out, **kw):
            raise subprocess.CalledProcessError(1, "ffmpeg")

        got = gc.clip_rocks([rock(name="0.1.1", t_release_s=None), rock(name="0.1.3")],
                            tmp_path / "v.mp4", self.VIEWS, tmp_path / "w",
                            lambda n, p: True, cut=cut)
        assert got == {"0.1.1": {"clip": None, "reason": "no_release"},
                       "0.1.3": {"clip": None, "reason": "cut_failed"}}
        got = gc.clip_rocks([rock()], tmp_path / "v.mp4", {}, tmp_path / "w",
                            lambda n, p: True, cut=cut)
        assert got == {"0.3.5": {"clip": None, "reason": "no_camera"}}

    def test_a_clip_that_did_not_upload_is_no_clip(self, tmp_path):
        got = gc.clip_rocks([rock()], tmp_path / "v.mp4", self.VIEWS, tmp_path / "w",
                            lambda n, p: False, cut=made)
        assert got == {"0.3.5": {"clip": None, "reason": "cut_failed"}}

    def test_each_rock_is_reported_as_it_is_done(self, tmp_path):
        seen = []
        gc.clip_rocks([rock(name="0.1.1", t_release_s=None), rock(name="0.1.3")],
                      tmp_path / "v.mp4", self.VIEWS, tmp_path / "w", lambda n, p: True,
                      cut=made, on_rock=lambda n, r: seen.append((n, r["clip"])))
        assert seen == [("0.1.1", None), ("0.1.3", True)]

    def test_a_delivery_that_raises_stops_the_job_and_cleans_up(self, tmp_path):
        def deliver(name, path):
            raise RuntimeError("the job is not ours any more")

        with pytest.raises(RuntimeError):
            gc.clip_rocks([rock()], tmp_path / "v.mp4", self.VIEWS, tmp_path / "w", deliver,
                          cut=made)
        assert list((tmp_path / "w").iterdir()) == []


PANEL = Panel(rect=(812, 554, 298, 516), center_px=(139.5, 347.7), px_per_m=75.4, flipped=True)


class TestPlan:
    """A game clip's pieces: the camera facing the thrower, then the overhead
    camera above the house the rock was thrown to."""
    VIEWS = {"left": LEFT, "right": RIGHT}

    def pieces(self, throw, panel=PANEL, views=None):
        got = gc.plan(throw, views or self.VIEWS, panel)
        return None if got is None else [(p.view, p.t0, p.t1) for p in got.pieces]

    def test_from_just_before_the_rock_enters_the_panel_or_crosses_the_far_hog(self):
        t = gc.throw_from_rock(rock(t_far_s=1014.0))
        assert self.pieces(t) == [("right", 997.0, 1012.5), (gc.OVERHEAD, 1012.5, 1021.5)]
        t = gc.throw_from_rock(rock(t_far_s=1011.0))
        assert self.pieces(t) == [("right", 997.0, 1010.5), (gc.OVERHEAD, 1010.5, 1021.5)]

    def test_the_camera_behind_where_there_is_no_panel_or_no_arrival(self):
        t = gc.throw_from_rock(rock())
        assert self.pieces(t, panel=None) == [("right", 997.0, 1013.0), ("left", 1013.0, 1021.5)]
        never = gc.throw_from_rock(rock(t_enter_s=None))
        assert self.pieces(never) == self.pieces(never, panel=None)

    def test_the_camera_behind_where_none_faces_the_thrower(self):
        t = gc.throw_from_rock(rock())
        assert self.pieces(t, views={"left": LEFT}) == [("left", 997.0, 1021.5)]

    def test_the_overhead_camera_even_without_the_one_behind(self):
        t = gc.throw_from_rock(rock())
        assert self.pieces(t, views={"right": RIGHT}) == \
            [("right", 997.0, 1012.5), (gc.OVERHEAD, 1012.5, 1021.5)]

    def test_too_short_a_look_from_overhead_is_not_worth_the_cut(self):
        t = gc.throw_from_rock(rock(t_rest_s=1011.0, t_enter_s=1012.5))
        assert self.pieces(t) == self.pieces(t, panel=None)


class TestPanelsFromCalibration:
    def test_the_panels_the_run_recorded(self):
        got = gc.panels_from_calibration({**CAL, "bottom": {
            "rect": [812, 554, 298, 516], "center_px": [139.5, 347.7], "px_per_m": 75.4,
            "flipped": True, "hog_line": {}}, "top": {"rect": [1, 2, 3, 4]}})
        assert got == {"bottom": PANEL}

    def test_none(self):
        assert gc.panels_from_calibration(None) == {}


def test_the_worker_and_the_api_agree_on_the_clip_style():
    assert gc.CLIP_STYLE == gameclips.CLIP_STYLE
