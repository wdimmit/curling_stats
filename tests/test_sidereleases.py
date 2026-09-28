"""Releases the overhead panel lost, timed from the long camera that faces the
thrower: the slide's fit, the end's own calibration against the overhead, and
the attach-only contract."""

from types import SimpleNamespace

import numpy as np
import pytest

from curling_score.detect import release as R
from curling_score.game import sidereleases as S
from curling_score.game import thinking
from curling_score.game.shots import Shot
from curling_score.geometry import constants as C
from curling_score.geometry.sideview import SideView


def slide(t_push, v=2.0, y0=-3.3, t_end=None, fps=10.0, x=0.05, noise=0.0, seed=0):
    """A stone at rest in front of the hack until ``t_push``, then sliding at
    ``v`` m/s: (t, y, x) samples, y past the throwing tee."""
    rng = np.random.default_rng(seed)
    t_end = t_push + 4.0 if t_end is None else t_end
    out = []
    for t in np.arange(t_push - 2.0, t_end, 1.0 / fps):
        y = y0 + max(0.0, t - t_push) * v
        if y > C.TEE_TO_HOGLINE_M:
            break
        out.append((round(float(t), 3), float(y + rng.normal(0, noise)), x))
    return out


class TestFitSlide:
    def test_it_times_the_slide_where_it_crosses_the_reference(self):
        pts = slide(100.0, v=2.0, noise=0.03)
        got = S.fit_slide(pts)
        # -3.3 at the push, so -2.0 is 0.65 s later at 2 m/s
        assert got.t_ref == pytest.approx(100.65, abs=0.03)
        assert got.t_tee == pytest.approx(100.0 + 3.3 / 2.0, abs=0.03)
        assert got.speed_m_s == pytest.approx(2.0, rel=0.03)

    def test_the_stone_at_rest_before_the_push_is_not_the_slide(self):
        """Eight seconds parked in front of the hack must not drag the line flat."""
        pts = slide(100.0, v=2.2, t_end=103.0)
        pts = [(round(99.0 - 0.1 * k, 3), -3.3, 0.05) for k in range(60)] + pts
        got = S.fit_slide(pts)
        assert got.t_tee == pytest.approx(100.0 + 3.3 / 2.2, abs=0.03)

    def test_a_stray_detection_off_the_line_is_left_out(self):
        pts = slide(100.0, v=2.0) + [(100.9, 2.8, 0.1), (101.5, -2.9, 0.0)]
        got = S.fit_slide(pts)
        assert got.t_ref == pytest.approx(100.65, abs=0.03)

    def test_a_few_sightings_are_not_a_slide(self):
        assert S.fit_slide(slide(100.0)[20:23]) is None

    def test_a_stone_barely_moving_is_not_a_slide(self):
        assert S.fit_slide(slide(100.0, v=0.4, t_end=110.0)) is None

    def test_a_slide_seen_over_too_short_a_stretch_is_refused(self):
        pts = [p for p in slide(100.0) if -2.4 <= p[1] <= -1.8]
        assert S.fit_slide(pts) is None


def view(tee=362.0, hog=435.0):
    return SideView(rect=(1111, 0, 809, 1080), tee_row=tee, hog_row=hog,
                    centre_col=400.0, lat_px_per_m_at_tee=125.0)


class TestStonePoints:
    def test_boxes_become_distances_past_the_throwing_tee(self):
        v = view()
        want = [(10.0, -2.5), (10.1, -2.3), (10.2, 0.0), (10.3, 1.0)]
        per = [[(400.0, v.row_for(y + C.STONE_RADIUS_M), 38.0, 0.9)] for _t, y in want]
        got = S.stone_points(v, [t for t, _ in want], per)
        assert [round(y, 3) for _t, y, _x in got] == [y for _t, y in want]

    def test_boxes_off_the_centre_line_are_dropped(self):
        v = view()
        row = v.row_for(-1.0 + C.STONE_RADIUS_M)
        wide = v.centre_col_at(row) + v.lateral_px_per_m(row) * 1.2
        per = [[(v.centre_col_at(row), row, 38.0, 0.9), (wide, row, 38.0, 0.9)]]
        got = S.stone_points(v, [10.0], per)
        assert len(got) == 1 and abs(got[0][2]) < 0.05


def overhead(t, color="yellow", v=2.0, y0=-1.9):
    """An overhead release first seen at ``y0``, climbing through the tee."""
    track = tuple((round(t + k * 0.2, 3), 0.1, y0 + v * k * 0.2) for k in range(12))
    return R.Release(color, t, track[-1][2], v, track=track)


def shot(n, color, *, rel=None, t_hog=None, t_enter=None, missing=False):
    s = Shot(number=n, color=color, stones=[], t_rest_s=(t_enter or 0.0) + 8.0,
             missing=missing,
             delivery=None if t_enter is None else SimpleNamespace(t_enter=t_enter))
    s.release, s.t_hog_s = rel, t_hog
    return s


class Slides:
    """A long camera that sees each rock push off at a known instant: the slide
    of rock k starts ``pushes[k]`` and is found in any window holding it."""

    def __init__(self, pushes, v=2.0):
        self.pushes, self.v, self.calls = pushes, v, []

    def __call__(self, video, view, color, t0, t1):
        self.calls.append((color, t0, t1))
        for (c, t_push) in self.pushes:
            if c == color and t0 <= t_push <= t1:
                return [p for p in slide(t_push, v=self.v) if t0 <= p[0] <= t1]
        return []


def hog_after(t_push, v=2.0, y0=-3.3):
    return t_push + (C.TEE_TO_HOGLINE_M - y0) / v


class TestTimeSideReleases:
    def test_a_shot_the_overhead_lost_gets_a_release_from_the_long_camera(self):
        s = shot(2, "red", t_hog=hog_after(200.0), t_enter=215.0)
        n = S.time_side_releases([s], "v.mp4", view(), points=Slides([("red", 200.0)]))
        assert n == 1
        assert s.release.source == "side"
        # uncalibrated: the slide's own crossing of the reference line
        assert s.release.t == pytest.approx(200.65, abs=0.05)
        assert s.release.speed_m_s == pytest.approx(2.0, rel=0.05)

    def test_an_overhead_release_is_never_replaced(self):
        mine = overhead(99.7)
        s = shot(1, "yellow", rel=mine, t_hog=hog_after(99.0), t_enter=115.0)
        S.time_side_releases([s], "v.mp4", view(), points=Slides([("yellow", 99.0)]))
        assert s.release is mine

    def test_an_end_with_no_lost_release_reads_no_video(self):
        calls = Slides([("yellow", 99.0)])
        s = shot(1, "yellow", rel=overhead(99.7), t_hog=hog_after(99.0), t_enter=115.0)
        assert S.time_side_releases([s], "v.mp4", view(), points=calls) == 0
        assert calls.calls == []

    def test_a_placeholder_is_left_alone(self):
        s = shot(3, "red", missing=True)
        assert S.time_side_releases([s], "v.mp4", view(), points=Slides([])) == 0
        assert s.release is None

    def test_the_end_s_own_releases_calibrate_the_ones_it_fills(self):
        """Where the overhead and the long camera both saw a rock, their
        difference is what the filled release is corrected by -- per end, since
        on 2026-09-27 it ran from -0.11 to +0.23 s between games and one end
        of a game read 0.45 s off the rest."""
        pushes = [("yellow", 100.0), ("yellow", 160.0), ("yellow", 220.0), ("red", 280.0)]
        # the overhead sees each yellow 0.30 s after the long camera's reference
        shots = [shot(i + 1, "yellow", rel=overhead(t + 0.65 + 0.30), t_hog=hog_after(t),
                      t_enter=t + 15.0) for i, (_c, t) in enumerate(pushes[:3])]
        lost = shot(4, "red", t_hog=hog_after(280.0), t_enter=295.0)
        S.time_side_releases(shots + [lost], "v.mp4", view(), points=Slides(pushes))
        assert lost.release.t == pytest.approx(280.65 + 0.30, abs=0.05)

    def test_too_few_to_calibrate_falls_back_to_no_correction(self):
        pushes = [("yellow", 100.0), ("red", 280.0)]
        one = shot(1, "yellow", rel=overhead(100.65 + 0.4), t_hog=hog_after(100.0), t_enter=115.0)
        lost = shot(2, "red", t_hog=hog_after(280.0), t_enter=295.0)
        S.time_side_releases([one, lost], "v.mp4", view(), points=Slides(pushes))
        assert lost.release.t == pytest.approx(280.65, abs=0.05)

    def test_its_tee_crossing_replaces_the_estimate(self):
        lost = shot(2, "red", t_hog=hog_after(280.0), t_enter=295.0)
        lost.tee_s, lost.tee_estimated = 290.0, True   # the arrival's guess
        S.time_side_releases([lost], "v.mp4", view(), points=Slides([("red", 280.0)]))
        assert thinking.tee_crossing(lost) == pytest.approx(280.0 + 3.3 / 2.0, abs=0.05)
        assert lost.tee_estimated is False

    def test_without_a_hog_crossing_the_arrival_anchors_the_search(self):
        lost = shot(2, "red", t_enter=280.0 + 15.0)
        S.time_side_releases([lost], "v.mp4", view(), points=Slides([("red", 280.0)]))
        assert lost.release is not None and lost.release.source == "side"

    def test_a_long_camera_that_sees_nothing_leaves_the_shot_as_it_was(self):
        lost = shot(2, "red", t_hog=hog_after(280.0), t_enter=295.0)
        assert S.time_side_releases([lost], "v.mp4", view(), points=Slides([])) == 0
        assert lost.release is None

    def test_an_error_on_one_rock_costs_that_rock_only(self):
        def flaky(video, v, color, t0, t1):
            if color == "red":
                raise RuntimeError("decode failed")
            return Slides([("yellow", 400.0)])(video, v, color, t0, t1)

        bad = shot(2, "red", t_hog=hog_after(280.0), t_enter=295.0)
        good = shot(3, "yellow", t_hog=hog_after(400.0), t_enter=415.0)
        assert S.time_side_releases([bad, good], "v.mp4", view(), points=flaky) == 1
        assert bad.release is None and good.release is not None

    def test_a_view_without_lateral_calibration_is_a_no_op(self):
        v = SideView(rect=(1111, 0, 809, 1080), tee_row=362.0, hog_row=435.0)
        lost = shot(2, "red", t_hog=hog_after(280.0), t_enter=295.0)
        assert S.time_side_releases([lost], "v.mp4", v, points=Slides([("red", 280.0)])) == 0


class TestTheOverheadReleaseRecordSaysWhereItCameFrom:
    def test_an_overhead_release_is_marked_overhead_by_default(self):
        assert R.Release("red", 1.0, 0.0, 2.0).source == "overhead"


class TestAnalyzeCallsIt:
    def test_it_runs_after_hogtime_and_before_brooms_and_lines_on_hogtime_s_camera(self):
        """After the hog crossings, which anchor it; before the brooms and lines,
        which read the tee crossing and the release it gives."""
        from pathlib import Path
        src = (Path(__file__).resolve().parents[1] / "src/curling_score/analyze.py").read_text()
        i_hog = src.index("hogtime.time_hog_crossings(")
        i_rel = src.index("sidereleases.time_side_releases(")
        i_broom = src.index("broomtime.time_target_brooms(")
        i_line = src.index("linetime.time_lines(")
        assert i_hog < i_rel < i_broom < i_line
        call = src[i_rel:][:200]
        assert "sideviews[hogtime.CAMERA_FOR[OTHER_HOUSE[end.house]]]" in call
