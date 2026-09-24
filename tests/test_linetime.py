"""Was the rock thrown at the broom: the line past the hog line, against it."""
import pytest

from curling_score.detect import longview
from curling_score.game import linetime as L
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

    def test_a_lone_false_start_is_skipped(self):
        times, per = self.frames(stray_first=True)
        path = L.chain(times, per, self.FIT)
        assert len(path) > 40
        assert len(path) > 40 and path[0][0] > 22.0

    def test_nothing_near_the_line_is_no_path(self):
        times = [0.0, 0.2]
        assert L.chain(times, [[(0.0, 2.0, 20.0, 0, 0, 0.9)], []], self.FIT) == []
