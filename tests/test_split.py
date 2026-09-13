"""The long split: near hog line to a fixed line inside the arrival panel.

Neither hog line is inside either overhead panel -- the view reaches about
+4.6 m against a hog line at 6.401 -- so the split is measured over a stated
baseline rather than hog to hog. The arrival end is observed outright; the
throwing end is reached by extrapolating the release at the slide speed it was
measured climbing at, which is near-constant because the thrower is still
moving with the stone.
"""

import pytest

from curling_score.detect.delivery import Delivery
from curling_score.detect.release import Release
from curling_score.game import split
from curling_score.geometry import constants as C


def climbing(t0=0.0, y0=-2.0, y1=3.6, speed=2.0, fps=5.0):
    """A release track climbing up-sheet at a steady speed."""
    out, t, y = [], t0, y0
    while y <= y1 + 1e-9:
        out.append((round(t, 3), 0.05, round(y, 4)))
        y += speed / fps
        t += 1.0 / fps
    return tuple(out)


def release_at(t0=0.0, speed=2.0, y1=3.6):
    tr = climbing(t0=t0, speed=speed, y1=y1)
    return Release(color="red", t=tr[0][0], y_exit_m=tr[-1][2],
                   speed_m_s=speed, track=tr)


def arriving(t0=20.0, y0=4.5, y1=0.2, speed=0.8, fps=10.0):
    """An arrival track running down-sheet toward the tee."""
    out, t, y = [], t0, y0
    while y >= y1 - 1e-9:
        out.append((round(t, 3), 0.0, round(y, 4)))
        y -= speed / fps
        t += 1.0 / fps
    return tuple(out)


def delivery_at(t0=20.0, **kw):
    tr = arriving(t0=t0, **kw)
    return Delivery(color="red", t_enter=tr[0][0], t_rest=tr[-1][0],
                    entry_y_m=tr[0][2], rest_x_m=0.0, rest_y_m=tr[-1][2],
                    travel_m=tr[0][2] - tr[-1][2], track=tr)


class TestCrossingTime:
    def test_finds_the_moment_a_descending_track_passes_a_line(self):
        tr = arriving(t0=100.0, y0=4.5, y1=0.5, speed=1.0, fps=10.0)
        # 4.5 -> 3.4 is 1.1 m at 1.0 m/s
        assert split.crossing_time(tr, 3.4) == pytest.approx(101.1, abs=0.02)

    def test_finds_the_moment_an_ascending_track_passes_a_line(self):
        tr = climbing(t0=50.0, y0=-2.0, y1=3.6, speed=2.0, fps=5.0)
        # -2.0 -> 0.0 is 2.0 m at 2.0 m/s
        assert split.crossing_time(tr, 0.0) == pytest.approx(51.0, abs=0.02)

    def test_interpolates_between_samples_rather_than_snapping(self):
        tr = arriving(t0=0.0, y0=4.0, y1=1.0, speed=1.0, fps=2.0)  # 0.5 m apart
        t = split.crossing_time(tr, 3.25)
        assert t == pytest.approx(0.75, abs=0.01)
        assert t not in [p[0] for p in tr]

    def test_a_line_the_track_never_reaches_is_not_invented(self):
        assert split.crossing_time(arriving(y0=4.0, y1=2.0), 6.401) is None
        assert split.crossing_time(climbing(y0=-2.0, y1=1.0), 3.4) is None

    def test_an_empty_track_crosses_nothing(self):
        assert split.crossing_time((), 3.4) is None


class TestHogCrossing:
    def test_extrapolates_the_slide_forward_to_the_hog_line(self):
        # climbs to 3.6 m at 2.0 m/s; the hog line is 2.801 m further on
        r = release_at(t0=0.0, speed=2.0, y1=3.6)
        want = r.track[-1][0] + (C.TEE_TO_HOGLINE_M - 3.6) / 2.0
        assert split.hog_crossing(r) == pytest.approx(want, abs=0.05)

    def test_a_release_that_reaches_the_line_is_not_extrapolated(self):
        r = release_at(t0=0.0, speed=2.0, y1=6.8)
        assert split.hog_crossing(r) == pytest.approx(
            split.crossing_time(r.track, C.TEE_TO_HOGLINE_M), abs=1e-6)

    def test_refuses_a_throw_lost_too_far_short_of_the_line(self):
        # followed only to +1.0 m, so 5.4 m of guessing
        assert split.hog_crossing(release_at(y1=1.0)) is None

    def test_a_release_with_no_track_gives_nothing(self):
        assert split.hog_crossing(
            Release(color="red", t=1.0, y_exit_m=3.0, speed_m_s=2.0)) is None


class TestLongSplit:
    def test_measures_over_the_stated_baseline(self):
        r = release_at(t0=0.0, speed=2.0, y1=3.6)
        d = delivery_at(t0=30.0, y0=4.5, speed=0.8)
        s = split.long_split(r, d)
        assert s is not None
        assert s.baseline_m == pytest.approx(split.BASELINE_M, abs=1e-6)
        assert s.seconds == pytest.approx(s.t_end - s.t_start, abs=1e-9)
        assert s.seconds > 0

    def test_the_baseline_is_the_sheet_minus_both_offsets(self):
        assert split.BASELINE_M == pytest.approx(
            C.TEE_TO_TEE_M - C.TEE_TO_HOGLINE_M - split.ARRIVAL_LINE_Y_M, abs=1e-9)

    def test_records_how_much_was_extrapolated(self):
        r = release_at(y1=3.6)
        s = split.long_split(r, delivery_at())
        assert s.extrapolated_m == pytest.approx(C.TEE_TO_HOGLINE_M - 3.6, abs=1e-6)

    def test_an_arrival_that_never_reaches_the_line_is_unmeasured(self):
        r = release_at(y1=3.6)
        d = delivery_at(t0=30.0, y0=3.0, y1=0.2)   # enters below the line
        assert split.long_split(r, d) is None

    def test_a_throw_lost_early_is_unmeasured(self):
        assert split.long_split(release_at(y1=1.0), delivery_at()) is None

    def test_no_release_means_no_split(self):
        assert split.long_split(None, delivery_at()) is None

    def test_no_delivery_means_no_split(self):
        assert split.long_split(release_at(), None) is None

    def test_a_backwards_split_is_refused(self):
        """An arrival timed before its own throw is a pairing error, not a split."""
        r = release_at(t0=100.0, y1=3.6)
        d = delivery_at(t0=10.0)
        assert split.long_split(r, d) is None


class TestItRefusesAPhysicallyImpossibleSplit:
    """The pairing is not precise enough to time with, so the split checks itself.

    ``release.pair`` answers "did this throw arrive at all?" inside a 6-30 s
    window, which is ample for a yes/no but is the whole measurement here. On
    game 1 end 4 it paired three arrivals to a release 10-15 s earlier where the
    clean ones run 18-20 s, giving draws that crossed 24.9 m in 8.5 s.

    A stone only ever slows, so its mean speed over the baseline cannot exceed
    the speed it was measured sliding at before the hog line. Both are measured
    independently, so the comparison needs no new constant.
    """

    def test_a_split_faster_than_the_slide_is_refused(self):
        # climbs at 1.4 m/s, then supposedly averages 2.4 over the baseline
        r = release_at(t0=0.0, speed=1.4, y1=3.6)
        start = split.hog_crossing(r)
        d = delivery_at(t0=start + split.BASELINE_M / 2.4, y0=4.5, speed=0.8)
        assert split.long_split(r, d) is None

    def test_a_draw_within_the_slide_speed_is_kept(self):
        r = release_at(t0=0.0, speed=2.0, y1=3.6)
        start = split.hog_crossing(r)
        # The arrival is timed where it crosses the line, not where its track
        # begins, so back the start off by the descent from 4.5 m to it.
        descent = (4.5 - split.ARRIVAL_LINE_Y_M) / 0.8
        d = delivery_at(t0=start + split.BASELINE_M / 1.5 - descent,
                        y0=4.5, speed=0.8)
        s = split.long_split(r, d)
        assert s is not None
        assert s.speed_m_s == pytest.approx(1.5, abs=0.05)

    def test_a_peel_thrown_hard_is_kept(self):
        """A takeout is fast at both ends; the check must not punish weight."""
        r = release_at(t0=0.0, speed=2.8, y1=3.6)
        start = split.hog_crossing(r)
        d = delivery_at(t0=start + split.BASELINE_M / 2.5, y0=4.5, speed=1.5)
        assert split.long_split(r, d) is not None

    def test_the_tolerance_forgives_measurement_noise_only(self):
        assert 1.0 < split.SPEED_TOLERANCE <= 1.2

    def test_the_split_reports_its_own_mean_speed(self):
        r = release_at(t0=0.0, speed=2.0, y1=3.6)
        start = split.hog_crossing(r)
        descent = (4.5 - split.ARRIVAL_LINE_Y_M) / 0.8
        d = delivery_at(t0=start + split.BASELINE_M / 1.6 - descent,
                        y0=4.5, speed=0.8)
        s = split.long_split(r, d)
        assert s.speed_m_s == pytest.approx(1.6, abs=0.05)
        assert s.speed_m_s == pytest.approx(s.baseline_m / s.seconds, abs=1e-9)
