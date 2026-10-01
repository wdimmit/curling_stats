"""Was the rock thrown at the broom: the line past the hog line, against it."""
from types import SimpleNamespace

import pytest

from curling_score.detect import longview
from curling_score.game import linetime as L
from curling_score.game.broomtime import TargetBroom
from curling_score.geometry import constants as C
from curling_score.geometry.sideview import SideView
from curling_score.harvest import sidepool

TEE = C.TEE_TO_TEE_M


def straight(a, b, lo=6.5, hi=9.9, n=40):
    """A track on x = a + b*y, sampled between lo and hi metres past the throwing tee."""
    out = []
    for i in range(n):
        yp = lo + (hi - lo) * i / (n - 1)
        y = TEE - yp
        out.append((i / 30.0, a + b * y, y, yp))
    return out


class TestFitLine:
    def test_it_recovers_a_straight_line(self):
        fit = L.fit_line(straight(0.3, -0.02))
        assert (fit.a, fit.b, fit.n) == (pytest.approx(0.3), pytest.approx(-0.02), 40)
        assert fit.rms == pytest.approx(0.0, abs=1e-9)

    def test_samples_before_the_hog_line_are_left_out(self):
        slide = [(0.0, 5.0, TEE - 3.0, 3.0)]            # the slide: in the hand, not free
        assert L.fit_line(straight(0.3, -0.02) + slide).a == pytest.approx(0.3)

    def test_too_few_or_too_short_is_no_fit(self):
        assert L.fit_line(straight(0.3, -0.02, n=10)) is None
        assert L.fit_line(straight(0.3, -0.02, lo=6.5, hi=8.5)) is None


class TestMeasure:
    START, BROOM = (0.0, 38.405), (1.0, 0.0)

    def aimed(self, shift=0.0):
        b = (self.BROOM[0] - self.START[0]) / (self.BROOM[1] - self.START[1])
        a = self.START[0] - b * self.START[1] + shift
        return straight(a, b)

    def test_a_rock_thrown_at_the_broom_misses_by_nothing(self):
        track = self.aimed()
        line = L.measure(L.fit_line(track), track, self.START, self.BROOM)
        assert line.miss == pytest.approx(0.0, abs=1e-9)
        assert line.at_hog_offset == pytest.approx(0.0, abs=1e-9)

    def test_a_parallel_line_ten_centimetres_out_misses_by_ten(self):
        track = self.aimed(0.10)
        line = L.measure(L.fit_line(track), track, self.START, self.BROOM)
        assert (line.miss, line.at_hog_offset) == (pytest.approx(0.10), pytest.approx(0.10))

    def test_no_start_means_no_offset_at_the_hog_line(self):
        track = self.aimed()
        assert L.measure(L.fit_line(track), track, None, self.BROOM).at_hog_offset is None

    def test_wide_is_the_side_away_from_the_curl(self):
        track = self.aimed(0.30)                        # 30 cm right of a broom on the right
        rest = (0.2, 0.5)                               # ...and it curled back left to rest
        line = L.measure(L.fit_line(track), track, self.START, self.BROOM, rest=rest)
        assert (line.curl, line.side) == ("left", "wide")

    def test_narrow_is_the_side_the_rock_curls_toward(self):
        track = self.aimed(-0.30)
        line = L.measure(L.fit_line(track), track, self.START, self.BROOM, rest=(0.2, 0.5))
        assert (line.curl, line.side) == ("left", "narrow")

    def test_a_miss_of_nothing_is_still_a_side_and_no_error(self):
        assert L.side_of(0.0, "left") == "narrow"

    def test_no_curl_direction_is_no_side(self):
        track = self.aimed(0.30)
        line = L.measure(L.fit_line(track), track, self.START, self.BROOM)
        assert (line.curl, line.side) == (None, None)

    def test_no_broom_is_a_line_with_no_broom_figures(self):
        track = self.aimed(0.30)
        fit = L.fit_line(track)
        line = L.measure(fit, track, self.START, None, rest=(0.2, 0.5))
        assert (line.at_broom_x, line.miss, line.at_hog_offset, line.side) == (None, None, None, None)
        assert line.curl == "left"
        assert line.at_hog_x == pytest.approx(fit.x(L.HOG_Y))
        assert line.at_tee_x == pytest.approx(fit.x(0.0))
        assert line.fit_n == 40

    def test_no_broom_and_no_start_is_still_a_line(self):
        track = self.aimed()
        line = L.measure(L.fit_line(track), track, None, None)
        assert line.start is None and line.at_tee_x is not None

    def test_a_broom_line_has_no_tee_point(self):
        track = self.aimed()
        assert L.measure(L.fit_line(track), track, self.START, self.BROOM).at_tee_x is None


class TestConfirmedBy:
    FIT = L.Fit(a=0.5, b=0.01, n=40, rms=0.002)

    def path(self, off=0.0, top=20.0):
        return [(y, self.FIT.x(y) + off) for y in [top - 0.25 * i for i in range(40)]]

    def test_a_path_on_the_line_confirms_it(self):
        assert L.confirmed_by(self.path(), self.FIT) is True

    def test_a_path_thirty_centimetres_off_disagrees(self):
        assert L.confirmed_by(self.path(0.30), self.FIT) is False

    def test_a_path_first_seen_too_near_the_house_says_nothing(self):
        assert L.confirmed_by(self.path(top=10.0), self.FIT) is None
        assert L.confirmed_by([], self.FIT) is None

    def test_a_path_first_seen_near_the_house_after_it_curled_says_nothing(self):
        # first seen 16 m out, already 30 cm off the thrown line: that is curl
        late = [(y, self.FIT.x(y) + 0.30) for y in [16.0 - 0.25 * i for i in range(40)]]
        assert L.confirmed_by(late, self.FIT) is None

    def test_only_the_window_is_compared_not_where_it_curled_later(self):
        on_then_curls = [(y, self.FIT.x(y) + (0.0 if y >= 19.0 else 0.4)) for y in
                         [23.5 - 0.25 * i for i in range(60)]]
        assert L.confirmed_by(on_then_curls, self.FIT) is True


class TestThin:
    def test_about_one_point_per_half_metre_and_the_last_kept(self):
        pts = [(20.0 - 0.1 * i, 0.0) for i in range(100)]
        got = L.thin(pts)
        assert len(got) == 21 and got[-1] == pts[-1]


HOG_VIEW = SideView(rect=(0, 0, 810, 1080), tee_row=430.0, hog_row=520.0,
                    centre_col=390.0, lat_px_per_m_at_tee=150.0, centre_line=(386.0, 0.004))


def sample_at(view, x_dest, yp_centre, t):
    """The (t, cx, edge_row, body_px) the proposer gives for a stone centred at
    destination x and yp metres past the throwing tee."""
    edge = view.row_for(yp_centre + C.STONE_RADIUS_M)
    rc = view.row_for(yp_centre)
    cx = view.centre_col_at(rc) + (-x_dest) * view.lateral_px_per_m(rc)
    return (t, cx, edge, 52.0)


def crossing_for(samples, t=10.3, key=None):
    key = int(samples[len(samples) // 2][1] // 120) if key is None else key
    return longview.Crossing(t, "ok", longview.KEY_OK, track_key=key, samples=tuple(samples))


class TestHogTrack:
    def stone(self, n=60, x0=-0.2, dx=-0.03):
        return [sample_at(HOG_VIEW, x0 + dx * (3.0 + 0.12 * i), 3.0 + 0.12 * i, 9.0 + i / 30)
                for i in range(n)]

    def test_it_recovers_the_stone_in_the_destination_frame(self):
        track = L.hog_track(crossing_for(self.stone()), HOG_VIEW)
        _t, x, y, yp = track[10]
        assert yp == pytest.approx(3.0 + 0.12 * 10, abs=1e-6)
        assert y == pytest.approx(TEE - yp, abs=1e-6)
        assert x == pytest.approx(-0.2 - 0.03 * yp, abs=1e-6)

    def test_another_stone_far_across_is_not_linked(self):
        other = [sample_at(HOG_VIEW, 1.5, 5.0, 9.0 + i / 30) for i in range(60)]
        track = L.hog_track(crossing_for(self.stone() + other, key=int(self.stone()[30][1] // 120)), HOG_VIEW)
        assert all(x < 0.5 for _t, x, _y, _yp in track)

    def test_samples_at_the_crop_s_bottom_edge_are_dropped(self):
        bottom = sidepool.band_crop(HOG_VIEW)[1]
        edge = (99.0, 300.0, bottom - 2.0, 52.0)
        track = L.hog_track(crossing_for(self.stone() + [edge]), HOG_VIEW)
        assert all(t != 99.0 for t, *_ in track)

    def test_a_colour_scan_crossing_has_no_track(self):
        assert L.hog_track(longview.Crossing(10.0, "ok", longview.KEY_OK), HOG_VIEW) == []

    def test_a_refused_crossing_with_only_crop_edge_samples_has_no_track(self):
        c = longview.Crossing(None, "speed 3.44 m/s is not a delivery", longview.KEY_BAD_SPEED,
                              track_key=2,
                              samples=((9.0, 300.0, sidepool.band_crop(HOG_VIEW)[1] - 2.0, 52.0),))
        assert L.hog_track(c, HOG_VIEW) == []


class TestPickStart:
    def box_at(self, x_dest, behind_tee_m):
        yp = -behind_tee_m
        rc = HOG_VIEW.row_for(yp)
        cx = HOG_VIEW.centre_col_at(rc) + (-x_dest) * HOG_VIEW.lateral_px_per_m(rc)
        return (cx, HOG_VIEW.row_for(yp + C.STONE_RADIUS_M), 40.0, 0.9)

    def test_the_stone_in_front_of_the_left_hack(self):
        got = L.pick_start([self.box_at(-0.15, 3.2)] * 5, HOG_VIEW)
        assert got == (pytest.approx(-0.15, abs=1e-6), pytest.approx(TEE + 3.2, abs=1e-6))

    def test_the_slide_and_the_far_side_are_ignored(self):
        boxes = [self.box_at(-0.15, 3.2)] * 3 + [self.box_at(-0.1, 1.0)] * 4 + [self.box_at(0.9, 3.2)] * 4
        assert L.pick_start(boxes, HOG_VIEW)[0] == pytest.approx(-0.15, abs=1e-6)

    def test_fewer_than_three_sightings_is_no_start(self):
        assert L.pick_start([self.box_at(-0.15, 3.2)] * 2, HOG_VIEW) is None


class TestChain:
    FIT = L.Fit(a=0.3, b=0.02, n=40, rms=0.002)

    def frames(self, n=80, gap=(), static=None, stray_first=False):
        times = [i * 0.2 for i in range(n)]
        per = []
        for i, t in enumerate(times):
            dets = []
            y = 23.5 - 2.2 * t + 0.05 * t * t                  # slowing down
            if y > 1.0 and i not in gap:
                dets.append((t, self.FIT.x(y) - 0.002 * t * t, y, 0, 0, 0.9))
            if static is not None:
                dets.append((t, *static, 0, 0, 0.8))
            if stray_first and i == 0:
                dets = [(t, self.FIT.x(21.0), 21.0, 0, 0, 0.6)]
            per.append(dets)
        return times, per

    def test_it_follows_the_moving_stone_not_one_at_rest_near_the_line(self):
        times, per = self.frames(static=(self.FIT.x(20.0) + 0.2, 20.0))
        path = L.chain(times, per, self.FIT)
        ys = [y for y, _x in path]
        assert len(path) > 40 and ys == sorted(ys, reverse=True) and ys[-1] < 5.0

    def test_it_carries_on_across_a_four_second_gap(self):
        times, per = self.frames(gap=range(20, 40))
        assert L.chain(times, per, self.FIT)[-1][0] < 5.0

    def test_a_stone_seen_resting_ahead_is_not_taken_for_the_rock_after_a_gap(self):
        # s_0sJsCcB7bMaXKTKy4 end 6 rock 1: hidden for 2.4 s, the rock's widened
        # gates reached the placed guard it had been seen ahead of all along,
        # and the path ended there instead of where the rock stopped.
        guard = (self.FIT.x(4.0) + 0.05, 4.0)
        times, per = self.frames(gap=range(42, 54), static=guard)
        path = L.chain(times, per, self.FIT)
        assert path[-1][0] < 1.5
        assert not any(abs(y - guard[1]) < 0.01 and abs(x - guard[0]) < 0.01 for y, x in path)

    def test_a_rock_that_stops_is_followed_to_its_rest(self):
        times = [i * 0.2 for i in range(80)]           # at rest from 13 s
        ys = [max(2.0, 21.5 - 1.5 * t) for t in times]
        per = [[(t, self.FIT.x(y), y, 0, 0, 0.9)] for t, y in zip(times, ys)]
        path = L.chain(times, per, self.FIT)
        assert len(path) == 80 and path[-1][0] == 2.0

    def test_a_rock_followed_into_a_freeze_is_followed_to_its_rest(self):
        # hOKZoeJNTpM end 6 rock 5: seen every frame up to the stone it froze
        # to, which this camera reads 0.15 m behind it.
        stop = (19.5 / 0.06) ** 0.5                    # 0.12 m/s/s from 21.5 m to rest at 2.0
        times = [i * 0.2 for i in range(110)]
        ys = [2.0 + 0.06 * max(0.0, stop - t) ** 2 for t in times]
        per = [[(t, self.FIT.x(y), y, 0, 0, 0.9), (t, self.FIT.x(1.85), 1.85, 0, 0, 0.8)]
               for t, y in zip(times, ys)]
        path = L.chain(times, per, self.FIT)
        assert path[-1][0] == 2.0

    def guard_read_as_the_rock(self, misread=(38, 39), gone_after=None):
        # The rock hidden from 4.0 s to 8.0 s; a stone of the other colour
        # sitting where the widened gates reach at 7.6 s, read once as the
        # rock's colour too (a sweeper's pad pressed against it).
        x, y = 0.09, 9.0
        times, per = self.frames(gap=range(20, 40))
        others = []
        for i, t in enumerate(times):
            there = gone_after is None or t <= gone_after
            others.append([(t, x, y, 0, 0, 0.85)] if there else [])
            if i in misread:
                per[i].append((t, x, y, 0, 0, 0.6))
        return (y, x), times, per, others

    def test_a_stone_of_the_other_colour_read_as_the_rock_is_not_the_rock(self):
        # s_1PbxeFSujkOVgtmLS end 3 rock 11: hidden 4 s, the yellow rock's
        # gates reached a red guard a yellow pad had made read yellow for two
        # frames, and the house view drew the rock running into the guard.
        guard, times, per, others = self.guard_read_as_the_rock()
        path = L.chain(times, per, self.FIT, others)
        assert path[-1][0] < 1.5
        assert guard not in path

    def test_without_the_other_colour_the_misread_guard_is_taken(self):
        guard, times, per, _others = self.guard_read_as_the_rock()
        assert guard in L.chain(times, per, self.FIT)

    def test_a_rock_that_stops_where_a_stone_it_knocked_away_sat_is_the_rock(self):
        # Hit and stick, hidden: the struck stone is gone from its spot when
        # the rock is next seen there, so that sighting is the rock.
        guard, times, per, others = self.guard_read_as_the_rock(misread=(38,), gone_after=7.5)
        assert guard in L.chain(times, per, self.FIT, others)

    def test_a_rock_hidden_into_a_freeze_on_the_other_colour_is_followed_to_its_rest(self):
        # s_1V5GRT57payxP8XCp end 5 rock 16: lost on the way in, then seen at
        # rest 0.2 m short of the stone of the other colour it froze to.
        stop = (19.5 / 0.06) ** 0.5
        times = [i * 0.2 for i in range(110)]
        ys = [2.0 + 0.06 * max(0.0, stop - t) ** 2 for t in times]
        per = [[(t, self.FIT.x(y), y, 0, 0, 0.9)] if not 2.05 < y < 5.0 else []
               for t, y in zip(times, ys)]
        others = [[(t, self.FIT.x(1.8), 1.8, 0, 0, 0.85)] for t in times]
        assert L.chain(times, per, self.FIT, others)[-1][0] == 2.0

    def test_a_lone_false_start_is_skipped(self):
        times, per = self.frames(stray_first=True)
        path = L.chain(times, per, self.FIT)
        assert len(path) > 40 and path[0][0] > 22.0

    def test_nothing_near_the_line_is_no_path(self):
        times = [0.0, 0.2]
        assert L.chain(times, [[(0.0, 2.0, 20.0, 0, 0, 0.9)], []], self.FIT) == []


DEST_VIEW = SideView(rect=(1110, 0, 810, 1080), tee_row=465.0, hog_row=547.0,
                     centre_col=411.0, lat_px_per_m_at_tee=140.0, centre_line=(411.0, 0.0))


class TestFindPath:
    def test_the_rock_s_colour_and_the_other_are_read_in_one_pass_per_band(self):
        asked = []

        def decode(video, rect, t0, t1, fps):
            return [object()] * 3, [t0, t0 + 0.2, t0 + 0.4]

        def detect(model, frames, times, lo, hi, colors, imgsz=800, conf=0.35):
            asked.append(tuple(colors))
            return [[[] for _ in frames] for _ in colors]

        L.find_path(object(), "v.mp4", DEST_VIEW, "yellow", 100.0, 120.0, TestChain.FIT,
                    decode=decode, detect=detect)
        assert asked == [("yellow", "red")] * 2


class TestTimeLines:
    def shot(self, crossing, **kw):
        base = dict(missing=False, color="red", release=SimpleNamespace(t=100.0),
                    hog_crossing=crossing, t_hog_s=103.8, t_rest_s=120.0,
                    target_broom=TargetBroom(1.0, 0.0, 1.0, 0.9), stones=[],
                    delivered_stone_index=None, line=None)
        base.update(kw)
        return SimpleNamespace(**base)

    def stone_to(self, hi_yp):
        n = int((hi_yp - 3.0) / 0.12)
        return [sample_at(HOG_VIEW, 0.0 + 0.026 * (3.0 + 0.12 * i), 3.0 + 0.12 * i, 100.0 + i / 30)
                for i in range(n)]

    def run(self, shots, extra=(), **kw):
        decoded = []

        def decode(video, rect, t0, t1, fps):
            decoded.append((round(t0 - 100.0, 2), round(t1 - 100.0, 2), fps))
            return [object()] * 3, [t0, t0 + 0.1, t0 + 0.2]

        def detect(model, frames, times, lo, hi, color, imgsz=800, conf=0.35):
            return [list(extra) if i == 0 else [] for i in range(len(frames))]

        self.n = L.time_lines(shots, "v.mp4", HOG_VIEW, DEST_VIEW, model=object(),
                              decode=decode, detect=detect, **kw)
        return decoded

    def test_a_shot_with_a_crossing_and_a_broom_gets_a_line(self):
        s = self.shot(crossing_for(self.stone_to(11.5)))
        self.run([s])
        assert s.line is not None and s.line.fit_n >= 15
        assert self.n == 1

    def test_a_failure_measuring_one_shot_leaves_it_without_a_line_and_does_not_raise(self):
        # The start-window decode raises for `bad` (release at t=100) only;
        # `ok` (release at t=200) still gets its line in the same call.
        bad = self.shot(crossing_for(self.stone_to(11.5)), release=SimpleNamespace(t=100.0))
        ok = self.shot(crossing_for(self.stone_to(11.5)), release=SimpleNamespace(t=200.0))

        def decode(video, rect, t0, t1, fps):
            if round(t0, 2) == 97.0:
                raise RuntimeError("boom")
            return [object()] * 3, [t0, t0 + 0.1, t0 + 0.2]

        def detect(model, frames, times, lo, hi, color, imgsz=800, conf=0.35):
            return [[] for _ in frames]

        n = L.time_lines([bad, ok], "v.mp4", HOG_VIEW, DEST_VIEW, model=object(),
                         decode=decode, detect=detect)
        assert bad.line is None
        assert ok.line is not None
        assert n == 1

    def test_the_window_is_extended_only_for_a_track_that_stops_short(self):
        long_, short = self.shot(crossing_for(self.stone_to(11.5))), self.shot(crossing_for(self.stone_to(8.7)))
        assert (6.5, 9.0, 30.0) not in self.run([long_])
        assert (6.5, 9.0, 30.0) in self.run([short])

    def test_a_refused_crossing_with_samples_is_still_measured(self):
        c = crossing_for(self.stone_to(11.5))
        refused = longview.Crossing(None, "speed 3.44 m/s is not a delivery", longview.KEY_BAD_SPEED,
                                    track_key=c.track_key, samples=c.samples)
        s = self.shot(refused)
        self.run([s])
        assert s.line is not None

    def test_no_broom_no_release_missing_or_no_model_means_no_line(self):
        c = crossing_for(self.stone_to(11.5))
        shots = [self.shot(c, target_broom=None), self.shot(c, release=None), self.shot(c, missing=True)]
        self.run(shots)
        assert all(s.line is None for s in shots)
        s = self.shot(c)
        L.time_lines([s], "v.mp4", HOG_VIEW, DEST_VIEW, model=None)
        assert s.line is None

    def test_without_broom_a_shot_with_no_broom_gets_a_broomless_line(self):
        s = self.shot(crossing_for(self.stone_to(11.5)), target_broom=None)
        self.run([s], without_broom=True)
        assert s.line is not None
        assert s.line.at_broom_x is None and s.line.at_tee_x is not None
        assert self.n == 1

    def test_by_default_no_broom_is_still_no_line(self):
        s = self.shot(crossing_for(self.stone_to(11.5)), target_broom=None)
        self.run([s])
        assert s.line is None and self.n == 0

    def test_a_doubles_rock_with_a_broom_keeps_the_broom_line(self):
        c = crossing_for(self.stone_to(11.5))
        plain, doubles = self.shot(c), self.shot(c)
        self.run([plain])
        self.run([doubles], without_broom=True)
        assert doubles.line == plain.line
        assert doubles.line.at_tee_x is None

    def test_without_broom_still_needs_a_release_and_a_seen_rock(self):
        c = crossing_for(self.stone_to(11.5))
        shots = [self.shot(c, target_broom=None, release=None),
                 self.shot(c, target_broom=None, missing=True)]
        self.run(shots, without_broom=True)
        assert all(s.line is None for s in shots)

    def test_the_pipeline_asks_for_broomless_lines_by_format(self):
        from pathlib import Path
        src = (Path(__file__).resolve().parents[1] / "src/curling_score/analyze.py").read_text()
        assert "without_broom=fmt.line_without_broom" in src


class TestAnalyzeCallsIt:
    def test_it_runs_after_the_broom_with_both_cameras(self):
        from pathlib import Path
        src = (Path(__file__).resolve().parents[1] / "src/curling_score/analyze.py").read_text()
        assert src.index("linetime.time_lines(") > src.index("broomtime.time_target_brooms(")
        call = src[src.index("linetime.time_lines("):][:260]
        assert "sideviews[hogtime.CAMERA_FOR[OTHER_HOUSE[end.house]]]" in call
        assert "sideviews[hogtime.CAMERA_FOR[end.house]]" in call

    def test_a_shot_starts_with_no_line(self):
        from curling_score.game.shots import Shot
        assert Shot(number=1, color="red", stones=[], t_rest_s=0.0).line is None


def box_for(view, x_dest, yp):
    """A side-model box for a stone centred at destination x, yp metres past the throwing tee."""
    rc = view.row_for(yp)
    cx = view.centre_col_at(rc) + (-x_dest) * view.lateral_px_per_m(rc)
    return (cx, view.row_for(yp + C.STONE_RADIUS_M), 40.0, 0.9)


class Delivery:
    """A rock sitting at (-0.15, -3.2) in front of the left hack until 0.5 s
    before the release at t=100, then sliding at 2 m/s and drifting right."""
    T = 100.0

    def rock(self, t):
        if t < 99.5:
            return (-0.15, -3.2)
        return (-0.15 + 0.04 * (t - 99.5), -3.2 + 2.0 * (t - 99.5))

    def track(self):
        """The band's 30 fps samples, 3.0 m past the tee on."""
        out = []
        for k in range(300):
            t = 97.0 + k / 30
            x, yp = self.rock(t)
            if 2.99 <= yp <= 11.5:              # from 102.6 s, on the read's grid
                out.append((t, x, TEE - yp, yp))
        return out

    def read(self, hidden=(), others=(), t_band=None, only=None, short_at=None):
        self.decoded, self.rows = [], []

        def decode(video, rect, t0, t1, fps):
            self.decoded.append((round(t0 - self.T, 2), round(t1 - self.T, 2), fps))
            n = int((t1 - t0 + 0.05) * fps + 1e-9)
            return [object()] * n, [t0 + i / fps for i in range(n)]

        def detect(model, frames, times, lo, hi, color, imgsz=800, conf=0.35):
            self.rows.append((lo, hi))
            out = []
            for i, t in enumerate(times):
                if only is not None and not only(i, t):
                    out.append([]); continue
                stones = [] if any(a < t < b for a, b in hidden) else [self.rock(t)]
                if short_at and abs(t - short_at[0]) < 1e-6:
                    stones = [(x, yp - short_at[1]) for x, yp in stones]
                boxes = [box_for(HOG_VIEW, x, yp) for x, yp in stones + list(others)]
                out.append([b for b in boxes if lo <= b[1] < hi])
            return out

        t_band = self.track()[0][0] if t_band is None else t_band
        return L.read_delivery(object(), "v.mp4", HOG_VIEW, "red", self.T, t_band,
                               decode=decode, detect=detect)

    def path(self, track=None, **kw):
        return L.delivery_path(self.track() if track is None else track, self.read(**kw), self.T)


yp_of = lambda p: TEE - p[1]


class TestReadDelivery(Delivery):
    def test_one_read_from_before_the_push_to_just_past_the_band_s_first_sample(self):
        self.read()
        assert self.decoded == [(-3.0, round(self.track()[0][0] + 0.1 - self.T, 2), 10.0)]

    def test_the_read_stops_five_seconds_after_the_release(self):
        self.read(t_band=108.0)
        assert self.decoded == [(-3.0, 5.0, 10.0)]

    def test_the_crop_runs_from_a_metre_behind_the_hack_to_past_the_band_s_first_rows(self):
        self.read()
        assert self.rows == [(int(HOG_VIEW.row_for(-(C.TEE_TO_HACKLINE_M + 1.0))) - 30,
                              int(HOG_VIEW.row_for(L.DELIVERY_TO_M + C.STONE_RADIUS_M)) + 30)]

    def test_the_start_is_picked_from_the_five_fps_frames_before_the_push(self):
        got = self.read(only=lambda i, t: i % 2 == 0).start
        assert got == (pytest.approx(-0.15, abs=1e-6), pytest.approx(TEE + 3.2, abs=1e-6))
        # Today's 5 fps read would not have had these frames.
        assert self.read(only=lambda i, t: i % 2 == 1).start is None
        assert self.read(only=lambda i, t: t > self.T - 0.2 + 1e-6).start is None

    def test_a_stone_parked_at_the_side_is_no_candidate(self):
        frames = self.read(others=[(1.4, -3.2)])
        assert all(abs(x) <= L.DELIVERY_MAX_X_M for per in frames.dets for x, _yp in per)

    def test_a_box_cut_by_the_crop_s_bottom_edge_is_no_candidate(self):
        bot = int(HOG_VIEW.row_for(L.DELIVERY_TO_M + C.STONE_RADIUS_M)) + 30
        yp_edge = HOG_VIEW.metres_at(bot - 2) - C.STONE_RADIUS_M
        frames = self.read(others=[(0.0, yp_edge)])
        assert all(abs(yp - yp_edge) > 0.01 for per in frames.dets for _x, yp in per)


class TestDeliveryPath(Delivery):
    def test_it_follows_the_rock_back_to_where_it_sat(self):
        d = self.path()
        assert d[0][0] == pytest.approx(-3.0)
        assert (d[0][2], yp_of(d[0])) == (pytest.approx(-0.15, abs=1e-6), pytest.approx(-3.2, abs=1e-6))

    def test_the_stone_at_rest_is_one_dot_drawn_twice(self):
        rest = [p for p in self.path() if abs(yp_of(p) + 3.2) < 1e-6]
        assert [round(p[0], 2) for p in rest] == [-3.0, -0.5]

    def test_it_is_on_the_tenth_of_a_second_throughout(self):
        d = self.path()
        assert all(abs(p[0] * 10 - round(p[0] * 10)) < 1e-6 for p in d)
        steps = [round((b[0] - a[0]) * 10) for a, b in zip(d[1:], d[2:])]
        assert set(steps) == {1}

    def test_it_ends_at_the_first_sample_past_the_hog_line_and_a_half(self):
        yps = [yp_of(p) for p in self.path()]
        assert yps[-1] > L.DELIVERY_END_M >= yps[-2]
        assert max(yps) <= L.DELIVERY_KEEP_M

    def test_across_is_where_the_rock_was(self):
        for t, _y, x in self.path():
            assert x == pytest.approx(self.rock(t + self.T)[0], abs=1e-6)

    def test_a_stone_waiting_at_the_other_hack_is_not_the_rock(self):
        d = self.path(others=[(0.15, -3.2)], hidden=[(97.5, 98.5)])
        assert d[0][0] == pytest.approx(-3.0)
        for t, _y, x in d:
            assert x == pytest.approx(self.rock(t + self.T)[0], abs=1e-6)

    def test_a_stone_parked_at_the_side_is_not_the_rock(self):
        d = self.path(others=[(0.9, -3.0)], hidden=[(97.5, 98.5)])
        for t, _y, x in d:
            assert x == pytest.approx(self.rock(t + self.T)[0], abs=1e-6)

    def test_a_second_unseen_is_bridged(self):
        assert self.path(hidden=[(98.0, 99.0)])[0][0] == pytest.approx(-3.0)

    def test_it_stops_rather_than_guess_after_more_than_1_2_s_unseen(self):
        # Last seen before the gap at 97.5, again at 98.9.
        assert self.path(hidden=[(97.5, 98.9)])[0][0] == pytest.approx(-1.1)

    def test_a_band_sample_read_short_gives_way_to_the_next(self, monkeypatch):
        track = self.track()
        t, x, _y, yp = track[0]
        track[0] = (t, x, TEE - (yp - 0.6), yp - 0.6)
        at = lambda d: [yp_of(p) for p in d if abs(p[0] - (t - self.T)) < 1e-6]
        d = self.path(track=track)
        assert d[0][0] == pytest.approx(-3.0)
        assert at(d) == [pytest.approx(yp, abs=1e-6)]
        monkeypatch.setattr(L, "DELIVERY_ANCHOR_S", 0.0)       # no second try
        assert at(self.path(track=track)) == [pytest.approx(yp - 0.6, abs=1e-6)]

    def test_one_reading_half_a_metre_short_does_not_lose_the_rock(self):
        # AEqL e1 r1: one box read 0.5 m short along in the slide, and a speed
        # taken from that one reading ran every prediction after it off.
        d = self.path(others=(), short_at=(99.9, 0.5))
        assert d[0][0] == pytest.approx(-3.0)

    def test_with_no_frames_it_is_the_band_alone(self):
        track = self.track()
        d = L.delivery_path(track, L.DeliveryFrames((), (), None), self.T)
        assert d[0][0] == pytest.approx(track[0][0] - self.T)

    def test_no_track_is_no_delivery(self):
        assert L.delivery_path([], L.DeliveryFrames((), (), None), self.T) == ()


class TestTheLineCarriesItsDelivery:
    def setup_method(self):
        self.t = TestTimeLines()

    def test_a_line_has_its_delivery_from_one_read_that_also_gives_the_start(self):
        s = self.t.shot(crossing_for(self.t.stone_to(11.5)))
        decoded = self.t.run([s])
        assert s.line.delivery and yp_of(s.line.delivery[-1]) > L.DELIVERY_END_M
        assert (-3.0, 0.1, 10.0) in decoded
        assert (-3.0, -0.2, 5.0) not in decoded

    def test_a_delivery_that_fails_to_assemble_costs_only_the_delivery(self, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("boom")
        monkeypatch.setattr(L, "delivery_path", boom)
        s = self.t.shot(crossing_for(self.t.stone_to(11.5)))
        self.t.run([s])
        assert s.line is not None and s.line.delivery == ()
