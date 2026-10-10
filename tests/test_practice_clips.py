"""A practice throw's clip: which camera when, and the cut itself."""

import shutil
import subprocess
from types import SimpleNamespace

import numpy as np
import pytest

from curling_score.ingest import frames
from curling_score.practice import clips as C

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="no ffmpeg")


def view(exit_m=20.0):
    """A long camera whose bottom row is ``exit_m`` from the far tee."""
    return SimpleNamespace(rect=(0, 0, 800, 1080), metres_at=lambda row: exit_m)


def both(exit_m=20.0):
    return {"left": view(exit_m), "right": view(exit_m)}


def throw(house="top", rel=100.0, t_hog=104.0, v_hog=None, t_far=114.0, rest=126.0,
          arrived=True):
    return {"id": f"t_{rel:.1f}", "house": house, "t_release_s": rel, "t_hog_s": t_hog,
            "v_hog_m_s": v_hog, "t_far_s": t_far, "t_rest_s": rest if arrived else None,
            "arrived": arrived, "clip": False}


class TestCameras:
    def test_to_the_top_house_the_right_camera_faces_the_thrower(self):
        p = C.plan(throw(house="top"), both())
        assert [x.view for x in p.pieces] == ["right", "left"]

    def test_to_the_bottom_house_the_left_camera_faces_the_thrower(self):
        p = C.plan(throw(house="bottom"), both())
        assert [x.view for x in p.pieces] == ["left", "right"]

    def test_it_starts_before_the_push_and_ends_after_rest(self):
        p = C.plan(throw(), both())
        assert p.pieces[0].t0 == 95.0
        assert p.pieces[1].t0 == 114.0 and p.pieces[1].t1 == 127.5
        assert p.end == 127.5


class TestLeavingTheFrame:
    def test_at_a_steady_speed_by_distance(self):
        # Hog to hog is 21.945 m in 10 s; the bottom row is 13.599 m past the
        # near hog line.
        t = C.exit_time(throw(), view(20.0))
        assert t == pytest.approx(110.197, abs=0.01)

    def test_slowing_down_from_its_speed_at_the_hog_line(self):
        t = C.exit_time(throw(v_hog=2.6), view(20.0))
        assert t == pytest.approx(109.745, abs=0.01)

    def test_a_frame_that_reaches_past_the_far_hog_line_ends_there(self):
        assert C.exit_time(throw(), view(30.0)) == 114.0

    def test_without_both_hog_times_it_is_not_known(self):
        assert C.exit_time(throw(t_hog=None), view()) is None
        assert C.exit_time(throw(t_far=None), view()) is None

    def test_the_first_piece_ends_as_the_rock_leaves(self):
        p = C.plan(throw(), both(20.0))
        assert p.pieces[0].t1 == pytest.approx(110.197, abs=0.01)


class TestWhatIsMissing:
    def test_no_hog_time_cuts_over_at_the_far_hog_line(self):
        p = C.plan(throw(t_hog=None), both())
        assert p.pieces[0].t1 == 114.0 and p.pieces[1].t0 == 114.0

    def test_no_hog_times_at_all_gives_the_thrower_ten_seconds(self):
        p = C.plan(throw(t_hog=None, t_far=None), both())
        assert p.pieces[0].t1 == 110.0
        assert p.pieces[1].t0 == 110.0 and p.pieces[1].t1 == 127.5

    def test_a_throw_that_never_arrived_is_one_piece(self):
        p = C.plan(throw(arrived=False, t_far=None), both())
        assert [(x.view, x.t0, x.t1) for x in p.pieces] == [("right", 95.0, 110.0)]

    def test_only_the_camera_behind_is_one_piece_from_it(self):
        p = C.plan(throw(house="top"), {"left": view()})
        assert [(x.view, x.t0, x.t1) for x in p.pieces] == [("left", 95.0, 127.5)]

    def test_only_the_camera_facing_is_one_piece_from_it(self):
        p = C.plan(throw(house="top"), {"right": view()})
        assert [(x.view, x.t0, x.t1) for x in p.pieces] == [("right", 95.0, 127.5)]

    def test_no_release_or_no_side_views_is_no_clip(self):
        assert C.plan({**throw(), "t_release_s": None}, both()) is None
        assert C.plan(throw(), None) is None and C.plan(throw(), {}) is None


class TestUntil:
    def test_a_plan_cut_short_where_the_recording_stops(self):
        p = C.plan(throw(), both()).until(120.0)
        assert p.end == 120.0 and len(p.pieces) == 2
        assert C.plan(throw(), both()).until(112.0).pieces[-1].view == "right"
        assert C.plan(throw(), both()).until(90.0) is None


@pytest.fixture(scope="module")
def composite(tmp_path_factory):
    """Ten seconds of a 320x180 'stream' at 10 fps: the left view red, then
    blue from 5 s; a grey strip; the right view lime, then white from 5 s. As
    MPEG-TS starting 30 s into its own clock, as a live recording does."""
    out = tmp_path_factory.mktemp("clips") / "rec.ts"
    graph = ("color=red:s=120x180:r=10:d=5[a];color=blue:s=120x180:r=10:d=5[b];"
             "[a][b]concat=n=2:v=1[L];color=gray:s=80x180:r=10:d=10[M];"
             "color=lime:s=120x180:r=10:d=5[c];color=white:s=120x180:r=10:d=5[d];"
             "[c][d]concat=n=2:v=1[R];[L][M][R]hstack=inputs=3[v]")
    subprocess.run(["ffmpeg", "-v", "error", "-filter_complex", graph, "-map", "[v]",
                    "-c:v", "libx264", "-g", "10", "-pix_fmt", "yuv420p",
                    "-output_ts_offset", "30", "-f", "mpegts", str(out)], check=True)
    return out


def views(src):
    return {"left": (src, (0, 0, 120, 180)), "right": (src, (200, 0, 120, 180))}


def colour_at(path, t):
    """The mean BGR of the clip's frame at ``t``."""
    _t, img = next(frames.window(path, t, t + 0.5, 10))
    return img.reshape(-1, 3).mean(axis=0)


def is_colour(bgr, want):
    return np.abs(np.array(bgr) - np.array(want)).max() < 60


@needs_ffmpeg
class TestCut:
    def test_the_fixture_starts_late_on_its_own_clock(self, composite):
        assert frames.stream_start_s(composite) > 29.0

    def test_each_piece_shows_its_view_at_its_time(self, composite, tmp_path):
        out = tmp_path / "t.mp4"
        p = C.ClipPlan((C.Piece("right", 1.0, 3.0), C.Piece("left", 6.0, 8.0)))
        C.cut(p, views(composite), out, size=(60, 90), fps=10)
        probe = frames.probe(out)
        assert (probe.width, probe.height) == (60, 90)
        assert probe.duration_s == pytest.approx(4.0, abs=0.3)
        assert is_colour(colour_at(out, 0.5), (0, 255, 0))       # right view, lime
        assert is_colour(colour_at(out, 3.2), (255, 0, 0))       # left view, blue

    def test_times_on_the_streams_clock(self, composite, tmp_path):
        out = tmp_path / "t.mp4"
        p = C.ClipPlan((C.Piece("left", 101.0, 103.0),))
        C.cut(p, views(composite), out, t0_s=100.0, size=(60, 90), fps=10)
        assert frames.probe(out).duration_s == pytest.approx(2.0, abs=0.3)
        assert is_colour(colour_at(out, 0.5), (0, 0, 255))       # left view, red

    def test_a_failed_cut_raises(self, tmp_path):
        p = C.ClipPlan((C.Piece("left", 1.0, 2.0),))
        with pytest.raises((subprocess.SubprocessError, OSError)):
            C.cut(p, views(tmp_path / "missing.ts"), tmp_path / "t.mp4")
