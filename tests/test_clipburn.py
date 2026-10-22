"""A game clip with its marks burned in (game/clipburn.py)."""

import shutil
import subprocess

import numpy as np
import pytest

from curling_score.game import clipburn as B
from curling_score.game.linetime import DELIVERY_END_M, TEE_Y
from curling_score.geometry.sideview import SideView
from curling_score.ingest import frames
from curling_score.practice import clips as practice_clips
from curling_score.practice import overlay as practice_overlay

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="no ffmpeg")

# A calibrated long camera (VXU9's left), and one that can only time.
LEFT = SideView(rect=(0, 0, 810, 1080), tee_row=429.95, hog_row=520.0, centre_col=390.23,
                lat_px_per_m_at_tee=150.923, centre_line=(381.563, 0.008215))
RIGHT = SideView(rect=(1110, 0, 810, 1080), tee_row=431.0, hog_row=522.0)
VIEWS = {"left": LEFT, "right": RIGHT}
DELIVERY = [[-3.0, 38.2, -0.15], [-1.0, 38.0, -0.15], [0.0, 37.0, -0.16],
            [1.5, 33.0, -0.25], [3.6, 26.85, -0.36]]


def throw(**kw):
    """To the bottom house: the left camera faces the thrower."""
    base = {"id": "1.3.12", "house": "bottom", "t_release_s": 100.0, "t_rest_s": 125.0,
            "arrived": True, "t_hog_s": 103.0, "v_hog_m_s": 2.5, "t_far_s": 113.0,
            "broom": {"x": -0.6, "y": 1.8},
            "line": {"at_hog_x": -0.36, "miss_m": -0.08, "aim_x": None,
                     "start": {"x": -0.15, "y": 38.18}, "delivery": DELIVERY}}
    base.update(kw)
    return base


def plan():
    return practice_clips.ClipPlan((practice_clips.Piece("left", 97.0, 110.0),
                                    practice_clips.Piece("right", 113.0, 126.5)))


class TestDotColour:
    def test_before_the_release_after_it_and_unknown(self):
        assert B.dot_colour(0.5, 1.0) == B.DOT_BEFORE == (0x6b, 0x1e, 0x2d)
        assert B.dot_colour(1.0, 1.0) == B.DOT_AFTER == (0x00, 0x7a, 0xa0)
        assert B.dot_colour(0.5, None) == B.DOT_UNKNOWN == (0x8c, 0x7a, 0x1f)

    def test_no_ramp_remains(self):
        assert not hasattr(B, "RAMP") and not hasattr(B, "ramp")


class TestDeliveryDone:
    def test_at_the_deliverys_last_sample(self):
        assert B.delivery_done(throw()) == pytest.approx(103.6)

    def test_without_one_from_the_hog_line_at_its_speed(self):
        got = B.delivery_done(throw(line={"start": None}))
        assert got == pytest.approx(103.0 + 1.5 / 2.5, abs=0.01)

    def test_without_a_hog_time_a_while_after_release(self):
        assert B.delivery_done(throw(line=None, t_hog_s=None)) == 100.0 + B.DELIVERY_S

    def test_no_release_no_time(self):
        assert B.delivery_done(throw(t_release_s=None)) is None


class TestFraming:
    def test_the_hack_and_the_hog_line_in_a_picture_half_the_panels_height(self):
        fr = B.framing(throw(), plan(), VIEWS)
        assert fr.view == "left" and fr.t_out == pytest.approx(103.6)
        u, v, w, h = fr.box
        assert (w, h) == (0.5, 0.5)
        hack = practice_overlay.to_view(LEFT, True, -0.15, 38.18)
        past = practice_overlay.to_view(LEFT, True, -0.36, TEE_Y - DELIVERY_END_M)
        assert (hack[1] - v) / h == pytest.approx(B.HACK_AT, abs=1e-3)
        assert 0 < (past[1] - v) / h <= B.DELIVERY_END_BY
        assert u + w / 2 == pytest.approx((hack[0] + past[0]) / 2, abs=1e-3)

    def test_kept_inside_the_panel(self):
        # The camera facing the thrower sees the sheet mirrored: the thrower's
        # right is the picture's left.
        def u(x):
            return B.framing(throw(line={"start": {"x": x, "y": 38.18}, "at_hog_x": x}),
                             plan(), VIEWS).box[0]

        assert (u(9.0), u(-9.0)) == (0.0, 0.5)

    def test_with_no_line_from_the_middle_of_the_hack(self):
        fr = B.framing(throw(line=None), plan(), VIEWS)
        hack = practice_overlay.to_view(LEFT, True, 0.0, B.HACK_START_Y)
        assert fr.box[0] + 0.25 == pytest.approx(hack[0], abs=0.02)

    def test_none_when_the_clip_opens_behind_the_thrower_or_cannot_place(self):
        behind = practice_clips.ClipPlan((practice_clips.Piece("right", 97.0, 110.0),))
        assert B.framing(throw(), behind, VIEWS) is None
        assert B.framing(throw(house="top"), plan(), VIEWS) is None
        assert B.framing(throw(), plan(), {"left": RIGHT}) is None


class TestMarks:
    def test_the_dots_on_the_streams_clock_split_in_the_charts_colours_at_the_release(self):
        line = {**throw()["line"], "release": [0.0, 37.0, -0.16]}
        m = B.marks(throw(line=line), plan(), VIEWS)
        dots = m[0]["dots"]
        assert [d[0] for d in dots] == [97.0, 99.0, 100.0, 101.5, 103.6]
        assert [d[3] for d in dots] == [B.DOT_BEFORE] * 2 + [B.DOT_AFTER] * 3
        assert dots[0][1:3] == tuple(practice_overlay.to_view(LEFT, True, -0.15, 38.2))
        assert m[0]["thrown"] and m[0]["aim"]

    def test_without_a_release_every_dot_is_unknown(self):
        for line in (throw()["line"], {**throw()["line"], "release": None}):
            dots = B.marks(throw(line=line), plan(), VIEWS)[0]["dots"]
            assert len(dots) == 5 and {d[3] for d in dots} == {B.DOT_UNKNOWN}

    # As the viewer judges one (`deliveryRelease`): the first three entries
    # must be finite numbers.
    @pytest.mark.parametrize("release", [[], "abc", [None, 1, 2], "1.5",
                                         [float("nan"), 1, 2], 5, [0.0, 37.0],
                                         [1, float("nan"), 2], [0.0, 37.0, float("inf")],
                                         ["0.0", 37.0, -0.16], [0.0, None, -0.16, 4],
                                         [True, 37.0, -0.16]])
    def test_a_malformed_release_is_no_release(self, release):
        line = {**throw()["line"], "release": release}
        dots = B.marks(throw(line=line), plan(), VIEWS)[0]["dots"]
        assert len(dots) == 5 and {d[3] for d in dots} == {B.DOT_UNKNOWN}

    def test_entries_past_the_third_are_not_looked_at(self):
        # The record may grow (a model version, say); the clips still split.
        line = {**throw()["line"], "release": [0.0, 37.0, -0.16, "release1-0123abcd"]}
        dots = B.marks(throw(line=line), plan(), VIEWS)[0]["dots"]
        assert [d[3] for d in dots] == [B.DOT_BEFORE] * 2 + [B.DOT_AFTER] * 3

    def test_a_camera_that_cannot_place_has_none(self):
        m = B.marks(throw(), plan(), VIEWS)
        assert m[1] == {"thrown": None, "aim": None, "broom": None, "dots": []}

    def test_no_delivery_no_dots_and_no_lines_no_marks(self):
        assert B.marks(throw(line={**throw()["line"], "delivery": None}), plan(),
                       VIEWS)[0]["dots"] == []
        bare = B.marks(throw(line=None, broom=None), plan(), VIEWS)
        assert bare[0] == {"thrown": None, "aim": None, "broom": None, "dots": []}


class TestThin:
    def test_dots_closer_than_the_gap_are_one_and_the_last_stays(self):
        dots = [(t, 0.5, 0.5 + 0.001 * t, None) for t in range(10)]
        got = B.thin(dots, 1000, 1000, gap=3)
        assert [d[0] for d in got] == [0, 3, 6, 9]
        assert [d[0] for d in B.thin(dots[:5], 1000, 1000, gap=3)] == [0, 3, 4]


class TestBoxAt:
    def test_zoomed_then_pulled_back_on_its_own_camera_only(self):
        fr = B.Framing("left", (0.3, 0.2, 0.5, 0.5), 103.0)
        assert B.box_at(fr, "left", 101.0) == (0.3, 0.2, 0.5, 0.5)
        mid = B.box_at(fr, "left", 103.0 + B.PULL_BACK_S / 2)
        assert mid == pytest.approx((0.15, 0.1, 0.75, 0.75))
        assert B.box_at(fr, "left", 103.0 + B.PULL_BACK_S) == B.WHOLE
        assert B.box_at(fr, "right", 101.0) == B.WHOLE
        assert B.box_at(None, "left", 101.0) == B.WHOLE


def magenta(img):
    b, g, r = (img[..., i].astype(int) for i in range(3))
    return (r > 200) & (b > 150) & (g < 120)


class TestDraw:
    def test_the_lines_the_broom_and_the_dots_reached(self):
        marks = {"thrown": [[0.2, 0.0], [0.2, 1.0]], "aim": [[0.6, 0.0], [0.6, 1.0]],
                 "broom": [0.4, 0.5],
                 "dots": [(1.0, 0.8, 0.2, (0, 0, 255)), (2.0, 0.8, 0.8, (0, 0, 255))]}
        img = np.full((100, 100, 3), 128, np.uint8)
        B.draw(img, marks, B.WHOLE, 1.5)
        assert magenta(img)[:, 18:23].any()                        # thrown
        assert (img[:, 58:63, 0] > 200).any()                      # aim: cyan's blue
        assert magenta(img)[50, 50:56].any()                       # the broom's ring
        assert img[20, 80, 2] > 200 and img[80, 80, 2] < 200       # the dot reached, not the next

    def test_drawn_where_the_zoomed_picture_shows_them(self):
        marks = {"thrown": [[0.6, 0.0], [0.6, 1.0]], "aim": None, "broom": None, "dots": []}
        img = np.full((100, 100, 3), 128, np.uint8)
        B.draw(img, marks, (0.5, 0.0, 0.5, 0.5), 0.0)
        assert magenta(img)[:, 18:23].any() and not magenta(img)[:, 55:65].any()

    def test_a_line_far_out_of_the_picture_is_quick_and_harmless(self):
        marks = {"thrown": None, "aim": [[0.5, -5000.0], [0.5, 5000.0]], "broom": [90.0, 90.0],
                 "dots": []}
        img = np.full((100, 100, 3), 128, np.uint8)
        B.draw(img, marks, B.WHOLE, 0.0)
        assert (img[:, 48:53, 0] > 200).any()


@pytest.fixture(scope="module")
def stream(tmp_path_factory):
    """Six seconds at 10 fps of a 'stream' whose left view is red above and
    blue below, and whose right view is lime."""
    out = tmp_path_factory.mktemp("burn") / "rec.mp4"
    graph = ("color=red:s=120x90:r=10:d=6[a];color=blue:s=120x90:r=10:d=6[b];"
             "[a][b]vstack[L];color=lime:s=120x180:r=10:d=6[R];[L][R]hstack[v]")
    subprocess.run(["ffmpeg", "-v", "error", "-filter_complex", graph, "-map", "[v]",
                    "-c:v", "libx264", "-g", "10", "-pix_fmt", "yuv420p", str(out)], check=True)
    return out


def frame_at(path, t):
    return next(frames.window(path, t, t + 0.5, 10))[1]


def near(bgr, want):
    return np.abs(np.asarray(bgr, float) - np.asarray(want, float)).max() < 60


@needs_ffmpeg
class TestRender:
    def test_zoomed_marked_pulled_back_then_the_next_camera(self, stream, tmp_path):
        out = tmp_path / "c.mp4"
        p = practice_clips.ClipPlan((practice_clips.Piece("left", 0.5, 3.5),
                                     practice_clips.Piece("right", 4.0, 5.0)))
        views = {"left": (stream, (0, 0, 120, 180)), "right": (stream, (120, 0, 120, 180))}
        marks = [{"thrown": [[0.25, 0.0], [0.25, 1.0]]}, {}]
        fr = B.Framing("left", (0.0, 0.0, 0.5, 0.5), 2.0)
        B.render(p, views, out, marks, fr, size=(60, 90), fps=10)
        probe = frames.probe(out)
        assert (probe.width, probe.height) == (60, 90)
        assert probe.duration_s == pytest.approx(4.0, abs=0.3)
        zoomed = frame_at(out, 0.5)
        assert near(zoomed[60:, 50:].reshape(-1, 3).mean(0), (0, 0, 255))   # all red
        assert magenta(zoomed)[:, 25:35].any()          # the line, at u .25 of a half-wide box
        whole = frame_at(out, 2.7)
        assert near(whole[70:, 50:].reshape(-1, 3).mean(0), (255, 0, 0))    # blue below
        assert magenta(whole)[:, 12:18].any()
        assert near(frame_at(out, 3.5).reshape(-1, 3).mean(0), (0, 255, 0))  # the right, lime

    def test_a_cut_past_the_end_raises(self, stream, tmp_path):
        p = practice_clips.ClipPlan((practice_clips.Piece("left", 20.0, 22.0),))
        with pytest.raises((OSError, subprocess.SubprocessError)):
            B.render(p, {"left": (stream, (0, 0, 120, 180))}, tmp_path / "c.mp4", [{}],
                     size=(60, 90), fps=10)

    def test_a_missing_source_raises(self, tmp_path):
        p = practice_clips.ClipPlan((practice_clips.Piece("left", 1.0, 2.0),))
        with pytest.raises((OSError, subprocess.SubprocessError)):
            B.render(p, {"left": (tmp_path / "gone.mp4", None)}, tmp_path / "c.mp4", [{}],
                     size=(60, 90), fps=10)

    def test_marks_that_cannot_be_placed_cost_the_marks_not_the_clip(self, stream, tmp_path,
                                                                     monkeypatch):
        def broken(*a, **k):
            raise ValueError("no")

        monkeypatch.setattr(B, "marks", broken)
        p = practice_clips.ClipPlan((practice_clips.Piece("left", 0.5, 1.5),))
        B.cut(p, {"left": (stream, (0, 0, 120, 180))}, tmp_path / "c.mp4", throw=throw(),
              sideviews=VIEWS, size=(60, 90), fps=10)
        assert frames.probe(tmp_path / "c.mp4").duration_s == pytest.approx(1.0, abs=0.3)


# ------------------------------------------------------------- overhead panels
UPRIGHT = B.Panel(rect=(812, 11, 298, 510), center_px=(145.0, 136.0), px_per_m=67.0,
                  flipped=False)
TURNED = B.Panel(rect=(812, 554, 298, 516), center_px=(139.5, 347.7), px_per_m=75.4,
                 flipped=True)


class TestPanel:
    """The house as the clip shows it: the thrower's end at the bottom, the
    thrower's right to the right."""

    def test_an_upright_panel_as_it_is(self):
        assert UPRIGHT.to_frac(0.0, 0.0) == pytest.approx([145 / 298, 136 / 510])
        u, v = UPRIGHT.to_frac(1.0, 1.0)
        assert u > 145 / 298 and v > 136 / 510          # right, and toward the thrower

    def test_a_flipped_panel_turned_the_same_way_round(self):
        assert TURNED.to_frac(0.0, 0.0) == pytest.approx([1 - 139.5 / 298, 1 - 347.7 / 516])
        u0, v0 = TURNED.to_frac(0.0, 0.0)
        u, v = TURNED.to_frac(1.0, 1.0)
        assert u - u0 == pytest.approx(75.4 / 298) and v - v0 == pytest.approx(75.4 / 516)

    def test_full_height_at_its_own_shape_black_either_side(self):
        u, v, w, h = TURNED.box((600, 810))
        assert (v, h) == (0.0, 1.0)
        assert w == pytest.approx(600 * 516 / (810 * 298))
        assert u == pytest.approx((1 - w) / 2)

    def test_from_the_timelines_record(self):
        rec = {"rect": [812, 554, 298, 516], "center_px": [139.5, 347.7], "px_per_m": 75.4,
               "flipped": True, "residual_m": 0.007}
        assert B.Panel.from_calibration(rec) == TURNED
        assert B.Panel.from_calibration({"rect": [1, 2, 3, 4]}) is None
        assert B.Panel.from_calibration(None) is None

    def test_the_lines_and_the_broom_over_it(self):
        m = B.panel_marks(throw(), TURNED)
        assert m["broom"] == TURNED.to_frac(-0.6, 1.8)
        assert len(m["thrown"]) == B.PANEL_SAMPLES and len(m["aim"]) == B.PANEL_SAMPLES
        assert m["dots"] == []
        # The thrown line ends at the broom's depth, off it by the miss.
        assert m["thrown"][-1] == pytest.approx(TURNED.to_frac(-0.6 - 0.08, 1.8))

    def test_cut_marks_the_overhead_piece_from_its_panel(self, monkeypatch):
        seen = {}

        def render(plan, views, out, marks, fr, **kw):
            seen.update(marks=marks, fr=fr, panels=kw["panels"])

        monkeypatch.setattr(B, "render", render)
        p = practice_clips.ClipPlan((practice_clips.Piece("left", 97.0, 110.0),
                                     practice_clips.Piece("overhead", 112.5, 126.5)))
        B.cut(p, {}, "x.mp4", throw=throw(), sideviews=VIEWS, panels={"overhead": TURNED})
        assert seen["marks"][1] == B.panel_marks(throw(), TURNED)
        assert seen["marks"][0]["dots"] and seen["fr"].view == "left"
        assert seen["panels"] == {"overhead": TURNED}


@pytest.fixture(scope="module")
def with_panel(tmp_path_factory):
    """Six seconds of a 'stream': a left view red above blue, and beside it a
    60x120 overhead panel white above green (black below it)."""
    out = tmp_path_factory.mktemp("panel") / "rec.mp4"
    graph = ("color=red:s=120x90:r=10:d=6[a];color=blue:s=120x90:r=10:d=6[b];[a][b]vstack[L];"
             "color=white:s=60x60:r=10:d=6[c];color=green:s=60x60:r=10:d=6[d];[c][d]vstack,"
             "pad=60:180:0:0:black[P];[L][P]hstack[v]")
    subprocess.run(["ffmpeg", "-v", "error", "-filter_complex", graph, "-map", "[v]",
                    "-c:v", "libx264", "-g", "10", "-pix_fmt", "yuv420p", str(out)], check=True)
    return out


@needs_ffmpeg
class TestRenderOverhead:
    def test_turned_at_its_own_shape_and_marked(self, with_panel, tmp_path):
        out = tmp_path / "c.mp4"
        panel = B.Panel(rect=(120, 0, 60, 120), center_px=(30.0, 60.0), px_per_m=10.0,
                        flipped=True)
        p = practice_clips.ClipPlan((practice_clips.Piece("left", 0.5, 1.5),
                                     practice_clips.Piece("overhead", 2.0, 3.0)))
        views = {"left": (with_panel, (0, 0, 120, 180)), "overhead": (with_panel, panel.rect)}
        marks = [{}, {"thrown": [[0.25, 0.0], [0.25, 1.0]]}]
        B.render(p, views, out, marks, panels={"overhead": panel}, size=(60, 90), fps=10)
        img = frame_at(out, 1.5)
        mid = img[:, 20:40]
        # Flipped, so turned: the green half on top, the white below.
        assert near(mid[5:35].reshape(-1, 3).mean(0), (0, 128, 0))
        assert near(mid[55:85].reshape(-1, 3).mean(0), (255, 255, 255))
        # Kept at its shape: black down both sides.
        assert img[:, :4].mean() < 30 and img[:, -4:].mean() < 30
        u0, _v, w, _h = panel.box((60, 90))
        x = int(round((0.25 - u0) * 60 / w))
        assert magenta(img)[:, x - 3:x + 4].any()
