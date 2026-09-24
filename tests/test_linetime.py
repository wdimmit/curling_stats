"""Was the rock thrown at the broom: the line past the hog line, against it."""
import pytest

from curling_score.game import linetime as L
from curling_score.geometry import constants as C

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
