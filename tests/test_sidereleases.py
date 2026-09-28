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


# -- Rocks the overhead saw at neither end -------------------------------------

def arrival(color, t, reason="rest"):
    from curling_score.detect.delivery import Delivery
    return Delivery(color=color, t_enter=t, t_rest=t + 8.0, entry_y_m=4.5,
                    rest_x_m=0.0, rest_y_m=0.0, travel_m=4.5, reason=reason)


def seen_end(first=5417.3, n=15, every=55.0, lag=17.0, first_color="yellow"):
    """An end as PHbZ3EKOhMI end 6 was seen: ``n`` alternating arrivals, each
    paired with the overhead release ``lag`` before it."""
    other = {"red": "yellow", "yellow": "red"}
    arrivals, thrown_by, color = [], {}, first_color
    for k in range(n):
        d = arrival(color, first + k * every)
        thrown_by[R.Release(color, d.t_enter - lag, 4.5, 2.0)] = d
        arrivals.append(d)
        color = other[color]
    return arrivals, thrown_by


class Scan:
    """A long camera scan that sees each rock push off at a known instant, at
    the scan's own rate: records the windows it was asked for."""

    def __init__(self, pushes, v=2.0):
        self.pushes, self.v, self.calls = pushes, v, []

    def __call__(self, video, view, t0, t1):
        self.calls.append((t0, t1))
        out = {"red": [], "yellow": []}
        for c, t_push in self.pushes:
            out[c] += [p for p in slide(t_push, v=self.v, fps=S.SCAN_FPS) if t0 <= p[0] <= t1]
        return out


def lost(arrivals, thrown_by, scan, t0=5297.0, t1=6455.0, v=None, frames=()):
    return S.lost_rocks("v.mp4", view() if v is None else v, kept=arrivals,
                        releases=list(thrown_by), thrown_by=thrown_by,
                        arrivals=arrivals, house_frames=frames, t0=t0, t1=t1, scan=scan)


class TestFindSlides:
    def test_each_slide_in_a_stretch_is_found_once(self):
        pts = slide(100.0, fps=S.SCAN_FPS) + slide(160.0, fps=S.SCAN_FPS)
        got = S.find_slides(pts, 90.0, 180.0)
        assert [s.t_ref for s in got] == pytest.approx([100.65, 160.65], abs=0.03)

    def test_a_stretch_with_no_slide_has_none(self):
        parked = [(round(100.0 + 0.2 * k, 3), -3.3, 0.05) for k in range(100)]
        assert S.find_slides(parked, 100.0, 120.0) == []


def turns(*ts, first="yellow"):
    """Rocks thrown at ``ts``, the colours alternating from ``first``."""
    other = {"red": "yellow", "yellow": "red"}
    out, c = [], first
    for t in ts:
        out.append((t, c))
        c = other[c]
    return out


class TestWhereToLook:
    def test_before_the_first_rock_after_the_last_and_in_a_gap_that_can_hold_one(self):
        rocks = turns(100.0, 150.0, 200.0, 250.0, 400.0, 450.0)   # median gap 50 s
        got = S.search_windows(rocks, 0.0, 1000.0)
        reach = S.GAP_FACTOR * 50.0
        assert got == [(100.0 - reach, 90.0), (260.0, 390.0), (460.0, 450.0 + reach)]

    def test_between_two_of_a_colour_however_short_the_gap(self):
        """s_1aAUMPjTB3eCfHqHM end 2: a red lost between yellows 87.4 s apart,
        with the end's reach at 88.4 s."""
        rocks = (turns(935.2, 979.4, 1025.3, 1073.2, 1127.6, 1175.4, 1221.0, 1271.2, 1323.8)
                 + turns(1411.2, 1446.8, 1514.0, 1597.6, 1692.8, 1778.4))
        got = S.search_windows(rocks, 915.0, 1835.0)
        assert (1333.8, pytest.approx(1401.2)) in got

    def test_it_never_leaves_the_end(self):
        got = S.search_windows(turns(100.0, 150.0, 200.0), 60.0, 205.0)
        assert got == [(60.0, 90.0)]

    def test_an_end_with_no_rocks_is_searched_whole(self):
        assert S.search_windows([], 10.0, 70.0) == [(10.0, 70.0)]


class TestWhenEachRockWasThrown:
    def test_a_paired_release_an_arrival_it_stood_in_for_and_an_arrival_alone(self):
        paired = arrival("red", 120.0)
        hogged = arrival("yellow", 150.0, reason=R.REASON)
        alone = arrival("red", 230.0)
        thrown_by = {R.Release("red", 101.0, 4.5, 2.0): paired}
        got = S.rock_times([paired, hogged, alone], thrown_by, lags=[19.0])
        assert got == [(101.0, "red"), (150.0, "yellow"), (230.0 - 19.0, "red")]


class TestLostRocks:
    def test_phbz_end_6_rock_1_hogged_by_a_thrower_the_overhead_never_saw(self):
        arrivals, thrown_by = seen_end()
        got = lost(arrivals, thrown_by, Scan([("red", 5340.5)]))
        assert [(d.color, d.reason) for d in got] == [("red", R.REASON)]
        assert got[0].t_enter == pytest.approx(5341.15, abs=0.05)

    def test_it_is_placed_first_and_the_hammer_changes_hands(self):
        from curling_score.game import fit, shots as shots_mod

        arrivals, thrown_by = seen_end()
        before = fit.fit_end(arrivals, paired=fit.paired_ids(thrown_by))
        extra = lost(arrivals, thrown_by, Scan([("red", 5340.5)]))
        after = fit.fit_end(arrivals + extra, paired=fit.paired_ids(thrown_by))
        assert (len(before), len(after)) == (15, 16)
        assert after[0] is extra[0]
        house = [(float(t), []) for t in range(5290, 6500)]
        built = lambda kept: shots_mod.from_deliveries(kept, house)
        assert [s.missing for s in built(before)] == [False] * 15 + [True]
        assert [s.missing for s in built(after)] == [False] * 16
        hammer = lambda kept: shots_mod.hammer_from_shots(built(kept))
        assert (hammer(before), hammer(after)) == ("red", "yellow")

    def test_a_slide_the_overhead_already_timed_is_that_release_whatever_its_colour(self):
        arrivals, thrown_by = seen_end()
        # every seen rock's own slide, one read the wrong colour
        pushes = [(("red" if k == 3 else d.color), d.t_enter - 17.0 - 0.65)
                  for k, d in enumerate(arrivals)]
        assert lost(arrivals, thrown_by, Scan(pushes), t0=5000.0) == []

    def test_a_slide_whose_arrival_no_release_claimed_is_that_arrival_s_release(self):
        arrivals, thrown_by = seen_end()
        first = arrivals[0]
        del thrown_by[next(r for r, d in thrown_by.items() if d is first)]
        # the first rock's release, lost by the overhead, and 17 s before it arrived
        assert lost(arrivals, thrown_by, Scan([("yellow", first.t_enter - 17.65)])) == []

    def test_an_end_that_is_not_short_is_not_the_caller_s_to_search(self):
        """analyze asks only when the rules left the end short; with a full end
        the search still costs nothing it did not find."""
        arrivals, thrown_by = seen_end(n=16)
        assert lost(arrivals, thrown_by, Scan([])) == []

    def test_it_looks_only_where_a_rock_could_hide(self):
        arrivals, thrown_by = seen_end()
        scan = Scan([])
        lost(arrivals, thrown_by, scan)
        first_release = arrivals[0].t_enter - 17.0
        last_release = arrivals[-1].t_enter - 17.0
        reach = S.GAP_FACTOR * 55.0
        assert scan.calls == [
            (pytest.approx(max(5297.0 + S.TURNAROUND_S, first_release - reach)),
             pytest.approx(first_release - R.MIN_SEPARATION_S)),
            (pytest.approx(last_release + R.MIN_SEPARATION_S), pytest.approx(last_release + reach)),
        ]                                # no gap in a steady end is long enough

    def test_a_stone_pushed_along_at_walking_pace_is_not_a_throw(self):
        """bLkgfZaDSKw end 6's run-up: one yellow at 0.81 m/s, 45 s before the
        end's first rock."""
        arrivals, thrown_by = seen_end()
        assert lost(arrivals, thrown_by, Scan([("red", 5340.5)], v=0.85)) == []

    def test_stones_pushed_about_together_are_not_throws(self):
        """bLkgfZaDSKw end 2's run-up: four slides of both colours inside 6 s,
        the players clearing end 1's stones through this end's throwing house.
        Pushed at a delivery's pace here, so only their number gives them away."""
        arrivals, thrown_by = seen_end()
        pushes = [("red", 5335.0), ("yellow", 5336.5), ("red", 5340.5), ("yellow", 5341.0)]
        assert lost(arrivals, thrown_by, Scan(pushes), t0=5300.0) == []

    def test_nothing_thrown_straight_after_the_last_end_closed_is_searched(self):
        arrivals, thrown_by = seen_end()
        scan = Scan([("red", 5340.5)])
        assert lost(arrivals, thrown_by, scan, t0=5330.0) == []
        assert scan.calls[0][0] == pytest.approx(5330.0 + S.TURNAROUND_S)

    def test_a_practice_slide_long_before_the_end_is_out_of_reach(self):
        arrivals, thrown_by = seen_end()
        assert lost(arrivals, thrown_by, Scan([("red", 5150.0)]), t0=5000.0) == []

    def test_a_slide_lost_before_the_tee_does_not_add_a_rock(self):
        """bLkgfZaDSKw end 3's one slide with no rock behind it: 1.46 m, to
        0.58 m short of the tee. A rock's own slide is let off lighter."""
        arrivals, thrown_by = seen_end()

        class Short(Scan):
            def __call__(self, video, view, t0, t1):
                self.calls.append((t0, t1))
                pts = [p for p in slide(5340.5, fps=S.SCAN_FPS, t_end=5340.5 + 1.4)
                       if t0 <= p[0] <= t1]
                return {"red": pts, "yellow": []}
        assert S.fit_slide(Short([])("v", view(), 5330.0, 5350.0)["red"]) is not None
        assert lost(arrivals, thrown_by, Short([])) == []

    def test_a_house_that_gained_its_colour_makes_it_an_unseen_arrival(self):
        from curling_score.detect.rocks import Detection

        arrivals, thrown_by = seen_end()
        t = 5341.15

        def house(stones):
            return [Detection(color=c, x_m=x, y_m=y, x_px=0.0, y_px=0.0, area_px=140.0,
                              confidence=0.9) for c, x, y in stones]
        frames = ([(round(t - 6.0 + 0.1 * k, 2), house([])) for k in range(60)]
                  + [(round(t + R.SETTLE_S + 0.1 * k, 2), house([("red", 0.2, 0.4)]))
                     for k in range(80)])
        got = lost(arrivals, thrown_by, Scan([("red", 5340.5)]), frames=frames)
        assert [(d.color, d.reason) for d in got] == [("red", R.REASON_ADD)]

    def test_a_view_without_lateral_calibration_reads_nothing(self):
        arrivals, thrown_by = seen_end()
        scan = Scan([("red", 5340.5)])
        v = SideView(rect=(1111, 0, 809, 1080), tee_row=362.0, hog_row=435.0)
        assert lost(arrivals, thrown_by, scan, v=v) == [] and scan.calls == []

    def test_a_failed_read_costs_only_what_it_would_have_found(self):
        arrivals, thrown_by = seen_end()

        def broken(video, v, t0, t1):
            raise RuntimeError("decode failed")
        assert lost(arrivals, thrown_by, broken) == []


class TestScanPoints:
    def test_each_stretch_is_decoded_once_for_both_colours(self):
        v = view()
        decoded = []

        def decode(video, rect, t0, t1, fps):
            decoded.append((t0, t1, fps))
            times = [t0 + k / fps for k in range(int((t1 - t0) * fps))]
            return np.zeros((len(times), 4, 4, 3), np.uint8), times

        def detect(model, frames, times, lo, hi, color):
            row = v.row_for(-1.0 + C.STONE_RADIUS_M)
            return [[(v.centre_col_at(row), row, 38.0, 0.9)] if color == "red" else []
                    for _ in times]

        got = S.scan_points("v.mp4", v, 100.0, 145.0, model=object(), decode=decode,
                            detect=detect)
        assert decoded == [(100.0, 120.0, S.SCAN_FPS), (120.0, 140.0, S.SCAN_FPS),
                           (140.0, 145.0, S.SCAN_FPS)]
        assert len(got["red"]) == 45 * S.SCAN_FPS and got["yellow"] == []


class TestAnalyzeSearchesAShortEnd:
    def test_it_searches_before_the_audit_and_the_shot_list_on_the_thrower_s_camera(self):
        """The rock it finds has to be a candidate when the rules fit the end,
        and only an end the rules left short is searched."""
        from pathlib import Path
        src = (Path(__file__).resolve().parents[1] / "src/curling_score/analyze.py").read_text()
        i_pair = src.index("release.find_and_pair(")
        i_lost = src.index("sidereleases.lost_rocks(")
        i_audit = src.index("endcheck.check(")
        i_shots = src.index("shots_mod.from_deliveries(")
        assert i_pair < i_lost < i_audit < i_shots
        guard = src[:i_lost][-200:]
        assert "0 < short <= shots_mod.MAX_FILL" in guard
        call = src[i_lost:][:200]
        assert "sideviews[hogtime.CAMERA_FOR[OTHER_HOUSE[end.house]]]" in call
