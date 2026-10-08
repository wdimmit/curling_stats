"""The long split: hog line to hog line, each timed at its panel's own paint."""

import pytest

from curling_score.game import split
from curling_score.geometry import constants as C
from curling_score.geometry import hogpaint
from tests.synth_hogline import TOP, line_at

LINE = line_at(4.44)       # a flat painted line whose tripwire sits at 4.44


def climbing(t0=0.0, y0=-2.0, y1=4.8, speed=2.0, fps=5.0, x=0.05):
    """A release track climbing up-sheet at a steady speed."""
    out, t, y = [], t0, y0
    while y <= y1 + 1e-9:
        out.append((round(t, 3), x, round(y, 4)))
        y += speed / fps
        t += 1.0 / fps
    return tuple(out)


def arriving(t0=20.0, y0=4.55, y1=0.2, speed=0.8, fps=10.0, x=0.0):
    """An arrival track running down-sheet toward the tee."""
    out, t, y = [], t0, y0
    while y >= y1 - 1e-9:
        out.append((round(t, 3), x, round(y, 4)))
        y -= speed / fps
        t += 1.0 / fps
    return tuple(out)


class TestCrossingTime:
    """The scalar crossing is still what the thinking-time clock uses at the tee."""

    def test_finds_the_moment_a_descending_track_passes_a_line(self):
        tr = arriving(t0=100.0, y0=4.5, y1=0.5, speed=1.0, fps=10.0)
        assert split.crossing_time(tr, 3.4) == pytest.approx(101.1, abs=0.02)

    def test_finds_the_moment_an_ascending_track_passes_a_line(self):
        tr = climbing(t0=50.0, y0=-2.0, y1=3.6, speed=2.0, fps=5.0)
        assert split.crossing_time(tr, 0.0) == pytest.approx(51.0, abs=0.02)

    def test_interpolates_between_samples_rather_than_snapping(self):
        tr = arriving(t0=0.0, y0=4.0, y1=1.0, speed=1.0, fps=2.0)
        t = split.crossing_time(tr, 3.25)
        assert t == pytest.approx(0.75, abs=0.01)
        assert t not in [p[0] for p in tr]

    def test_a_line_the_track_never_reaches_is_not_invented(self):
        assert split.crossing_time(arriving(y0=4.0, y1=2.0), 6.401) is None

    def test_an_empty_track_crosses_nothing(self):
        assert split.crossing_time((), 3.4) is None


class TestLineCrossing:
    def test_times_a_track_across_a_flat_painted_line(self):
        tr = arriving(t0=20.0, y0=4.8, speed=1.0, fps=10.0)
        assert split.line_crossing(tr, LINE) == pytest.approx(20.36, abs=0.01)

    def test_reads_a_bowed_line_at_the_stone_s_own_position(self):
        """These lenses bow the paint, and a curled stone crosses it well off
        the centre line: the tripwire it meets is the one at its own x."""
        bowed = hogpaint.HogLine(coef=(0.0004, -0.12, 525.0), calib=TOP,
                                 columns=300, scatter_px=0.0, width_px=4.0)
        assert bowed.y_at(1.0) > bowed.y_at(0.0) + 0.02
        tr = arriving(t0=20.0, y0=4.8, speed=1.0, fps=10.0, x=1.0)
        t = split.line_crossing(tr, bowed)
        assert t == pytest.approx(20.0 + (4.8 - bowed.y_at(1.0)) / 1.0, abs=0.01)

    def test_a_track_that_never_reaches_the_line_crosses_nothing(self):
        assert split.line_crossing(arriving(y0=4.0), LINE) is None
        assert split.line_crossing((), LINE) is None
        assert split.line_crossing(None, LINE) is None

    def test_a_departing_track_is_timed_at_the_inside_edge(self):
        """A departing stone meets the paint from the house side, at
        ``departure_y_at`` -- well before the outer edge ``y_at`` reads, which
        is what an arrival meets."""
        tr = climbing(t0=0.0, y0=-2.0, y1=4.8, speed=2.0, fps=5.0)
        y_line = LINE.departure_y_at(0.05)
        t = split.line_crossing(tr, LINE, departing=True)
        assert t == pytest.approx((y_line - (-2.0)) / 2.0, abs=0.02)
        assert t < split.line_crossing(tr, LINE)


class TestFarCrossing:
    """An arrival first seen past its painted line is reached back for, a little."""

    def test_an_observed_crossing_is_not_extrapolated(self):
        t, reach = split.far_crossing(arriving(y0=4.8), LINE, max_reach=split.FAR_REACH_MAX_U)
        assert t is not None and reach == 0.0

    def test_a_track_beginning_just_past_the_line_is_reached_for(self):
        tr = arriving(t0=20.0, y0=4.34, speed=1.0, fps=10.0)
        t, reach = split.far_crossing(tr, LINE, max_reach=split.FAR_REACH_MAX_U)
        assert reach == pytest.approx(0.10, abs=1e-6)
        assert t == pytest.approx(19.90, abs=0.01)
        assert t < tr[0][0]

    def test_the_cap_is_0_20_units(self):
        assert split.FAR_REACH_MAX_U == 0.20
        inside = arriving(t0=20.0, y0=4.241, speed=1.0, fps=10.0)     # reach 0.199
        outside = arriving(t0=20.0, y0=4.239, speed=1.0, fps=10.0)    # reach 0.201
        assert split.far_crossing(inside, LINE, max_reach=0.20)[0] is not None
        assert split.far_crossing(outside, LINE, max_reach=0.20) == (None, 0.0)

    def test_too_little_track_to_fit_is_refused(self):
        tr = arriving(t0=20.0, y0=4.34, y1=4.10, speed=1.0, fps=10.0)[:3]
        assert split.far_crossing(tr, LINE, max_reach=0.20) == (None, 0.0)

    def test_the_fit_is_the_earliest_samples_in_time(self):
        """A stone that dwells after it arrives must not steer the fit: the
        four highest-y samples would be the dwell, not the approach."""
        approach = arriving(t0=20.0, y0=4.34, y1=4.04, speed=1.0, fps=10.0)
        dwell = tuple((round(20.4 + i / 10.0, 3), 0.0, 4.34 - 0.001 * i) for i in range(1, 4))
        tr = tuple(sorted(approach + dwell))
        t, reach = split.far_crossing(tr, LINE, max_reach=split.FAR_REACH_MAX_U)
        assert reach == pytest.approx(0.10, abs=1e-6)
        assert t == pytest.approx(19.90, abs=0.01)

    def test_a_dwelling_track_is_refused(self):
        """Nearly still at first sighting, so dt/dy is huge and a straight
        line through it reaches back seconds -- 14 s here."""
        tr = tuple((round(20.0 + i / 10.0, 3), 0.0, round(4.30 - 0.001 * i, 4)) for i in range(8))
        assert split.far_crossing(tr, LINE, max_reach=split.FAR_REACH_MAX_U) == (None, 0.0)

    def test_a_fit_that_lands_after_the_first_sighting_is_refused(self):
        """A jittery track drifting the wrong way has a fit slope of the wrong
        sign, so it extrapolates forward, past its own first sample -- the
        highest-y fit timed this one at 12.97, three seconds after 10.0."""
        line = line_at(4.55)
        ys = (4.40, 4.41, 4.40, 4.42, 4.41, 4.43, 4.42, 4.44, 4.43)
        tr = tuple((round(10.0 + i / 10.0, 3), 0.0, y) for i, y in enumerate(ys))
        assert split.far_crossing(tr, line, max_reach=split.FAR_REACH_MAX_U) == (None, 0.0)

    def test_a_stone_first_seen_on_the_line_keeps_its_crossing(self):
        """Seen 0.005 u short of the tripwire, a noisy fit can land a few
        hundredths after the first sighting. Within a frame that is noise, not a
        wrong-sign slope, and the crossing is the first sighting itself."""
        assert split.FAR_LEAD_SLACK_S == 0.1
        tr = ((20.0, 0.0, 4.435), (20.1, 0.0, 4.42), (20.2, 0.0, 4.33), (20.3, 0.0, 4.23))
        t, reach = split.far_crossing(tr, LINE, max_reach=split.FAR_REACH_MAX_U)
        assert t == pytest.approx(20.0)
        assert reach == pytest.approx(0.005, abs=1e-6)

    def test_the_lead_cap_is_one_second(self):
        """A clean but slow arrival: 0.1 u/s, so reach 0.09 is a 0.9 s lead
        and reach 0.12 a 1.2 s one."""
        assert split.FAR_LEAD_MAX_S == 1.0
        inside = arriving(t0=20.0, y0=4.35, speed=0.1, fps=10.0, y1=4.2)
        outside = arriving(t0=20.0, y0=4.32, speed=0.1, fps=10.0, y1=4.2)
        t, reach = split.far_crossing(inside, LINE, max_reach=split.FAR_REACH_MAX_U)
        assert t == pytest.approx(19.10, abs=0.01) and reach == pytest.approx(0.09, abs=1e-6)
        assert split.far_crossing(outside, LINE, max_reach=split.FAR_REACH_MAX_U) == (None, 0.0)


class TestSpeedAtLine:
    def test_reads_the_rate_across_the_paint(self):
        assert split.speed_at_line(arriving(y0=4.8, speed=0.8), LINE) == pytest.approx(0.8, abs=0.01)

    def test_no_crossing_means_no_speed(self):
        assert split.speed_at_line(arriving(y0=4.0), LINE) is None
        assert split.speed_at_line(None, LINE) is None

    def test_a_gap_across_the_paint_is_no_speed(self):
        """S1 10/06 e8 r12: the release track lost the stone at y 2.15 and
        picked something up at 4.58 3.4 s later. That pair straddles the line,
        but the rate between them is an average over half the panel, 0.71
        against the arrival's 1.01, and it refused a good split."""
        gapped = ((20.0, 0.0, 4.80), (20.1, 0.0, 4.72), (23.5, 0.0, 4.00), (23.6, 0.0, 3.92))
        assert split.speed_at_line(gapped, LINE) is None

    def test_a_sample_or_two_dropped_at_the_paint_is_still_a_speed(self):
        dropped = tuple(p for p in arriving(y0=4.8, speed=0.8) if not 4.35 < p[2] < 4.6)
        pair = [(a, b) for a, b in zip(dropped, dropped[1:]) if a[2] > 4.44 >= b[2]][0]
        assert 0.2 < pair[1][0] - pair[0][0] <= split.SPEED_SPAN_MAX_S
        assert split.speed_at_line(dropped, LINE) == pytest.approx(0.8, abs=0.01)

    def test_the_departing_line_is_held_to_the_same_span(self):
        """S1 10/06 e7 r16, read at the live run's sample phase: 4.18 at
        6055.72, then 5.51 at 6058.72 -- 0.44 against the arrival's 0.63."""
        trip = LINE.departure_y_at(0.0)
        gapped = ((6055.52, 0.0, trip - 0.6), (6055.72, 0.0, trip - 0.2),
                  (6058.72, 0.0, trip + 1.1))
        assert split.speed_at_line(gapped, LINE, departing=True) is None


class TestTheBaseline:
    def test_it_is_what_the_leading_edge_covers(self):
        """Leading edge first touching each line: the throwing line's inside
        edge, the destination line's outer edge."""
        assert split.BASELINE_M == pytest.approx(21.843, abs=1e-3)
        assert split.BASELINE_M == pytest.approx(
            C.TEE_TO_TEE_M - C.TEE_TO_HOGLINE_M - (C.TEE_TO_HOGLINE_M + C.HOGLINE_WIDTH_M))


def far(t=30.0, reach=0.0, v_far=None, t_near_panel=None, v_near=None):
    return split.FarCrossing(t=t, reach=reach, v_far=v_far,
                             t_near_panel=t_near_panel, v_near=v_near)


DELIVERY = object()


class TestLongSplit:
    def test_measures_between_the_two_crossings(self):
        s = split.long_split(DELIVERY, t_hog=10.0, v_hog=None, far=far(t=30.0))
        assert s.seconds == pytest.approx(20.0)
        assert s.t_start == 10.0 and s.t_end == 30.0
        assert s.baseline_m == pytest.approx(split.BASELINE_M)

    def test_every_input_is_required(self):
        """The long-camera merge added a required input and updated one of
        long_split's two callers; the other silently lost every draw-through.
        A forgotten argument must now fail loudly."""
        with pytest.raises(TypeError):
            split.long_split(DELIVERY, t_hog=10.0, v_hog=None)
        with pytest.raises(TypeError):
            split.long_split(DELIVERY, v_hog=None, far=far())
        with pytest.raises(TypeError):
            split.long_split(DELIVERY, t_hog=10.0, far=far())

    def test_either_end_missing_means_no_split(self):
        assert split.long_split(None, t_hog=10.0, v_hog=None, far=far()) is None
        assert split.long_split(DELIVERY, t_hog=None, v_hog=None, far=far()) is None
        assert split.long_split(DELIVERY, t_hog=10.0, v_hog=None, far=None) is None
        assert split.long_split(DELIVERY, t_hog=10.0, v_hog=None, far=far(t=None)) is None

    def test_a_backwards_split_is_refused(self):
        assert split.long_split(DELIVERY, t_hog=40.0, v_hog=None, far=far(t=30.0)) is None

    def test_a_split_records_how_far_it_reached(self):
        assert split.long_split(DELIVERY, t_hog=10.0, v_hog=None, far=far(reach=0.12)).far_reach == 0.12
        assert split.long_split(DELIVERY, t_hog=10.0, v_hog=None, far=far()).far_reach == 0.0

    def test_the_split_reports_its_own_mean_speed(self):
        s = split.long_split(DELIVERY, t_hog=10.0, v_hog=None, far=far(t=24.0))
        assert s.speed_m_s == pytest.approx(split.BASELINE_M / 14.0)


class TestThePanelCrossCheckIsRecordedNotObeyed:
    def test_a_split_records_the_panel_disagreement(self):
        s = split.long_split(DELIVERY, t_hog=10.0, v_hog=None, far=far(t_near_panel=9.95))
        assert s.panel_delta == pytest.approx(0.05)

    def test_a_large_disagreement_is_recorded_not_refused(self):
        s = split.long_split(DELIVERY, t_hog=10.0, v_hog=None,
                             far=far(t_near_panel=10.0 - 3 * split.CROSS_CHECK_S))
        assert s is not None
        assert s.panel_delta == pytest.approx(3 * split.CROSS_CHECK_S)

    def test_no_panel_reading_is_none_not_zero(self):
        assert split.long_split(DELIVERY, t_hog=10.0, v_hog=None, far=far()).panel_delta is None


class TestItRefusesAPhysicallyImpossibleSplit:
    """A stone only ever slows, so it cannot cross the far line faster than the
    near one; both are read in their own panel's units, distorted alike."""

    def test_the_tolerance_forgives_measurement_noise_only(self):
        assert 1.0 < split.SPEED_TOLERANCE <= 1.5

    def test_a_stone_apparently_faster_at_the_far_line_is_refused(self):
        assert split.long_split(DELIVERY, t_hog=10.0, v_hog=None,
                                far=far(v_near=1.0, v_far=1.5)) is None

    def test_a_stone_that_slowed_is_kept(self):
        assert split.long_split(DELIVERY, t_hog=10.0, v_hog=None,
                                far=far(v_near=2.0, v_far=1.2)) is not None

    def test_inside_the_tolerance_is_kept(self):
        assert split.long_split(DELIVERY, t_hog=10.0, v_hog=None,
                                far=far(v_near=1.0, v_far=1.3)) is not None

    def test_without_a_near_panel_speed_the_side_view_speed_bounds_the_mean(self):
        # 21.843 m in 2 s is ~10.9 m/s against a 2.0 m/s near crossing.
        assert split.long_split(DELIVERY, t_hog=10.0, v_hog=2.0, far=far(t=12.0)) is None
        assert split.long_split(DELIVERY, t_hog=10.0, v_hog=2.5, far=far(t=20.0)) is not None

    def test_with_neither_speed_the_split_is_published_unchecked(self):
        assert split.long_split(DELIVERY, t_hog=10.0, v_hog=None, far=far(t=12.0)) is not None

    def test_an_extrapolated_crossing_with_no_far_speed_still_publishes(self):
        """speed_at_line needs a bracket, so a reached-for crossing has no far
        speed and the panel check cannot run; far_reach is the reader's mark."""
        s = split.long_split(DELIVERY, t_hog=10.0, v_hog=None,
                             far=far(reach=0.1, v_far=None, v_near=2.0))
        assert s is not None and s.far_reach == 0.1

    def test_an_extrapolated_crossing_is_still_bounded_by_the_side_view_speed(self):
        """A reached-for crossing escapes the panel check, so the mean-speed
        bound runs even with a throwing-panel speed: 21.843 m in 2 s is
        ~10.9 m/s against a 2.0 m/s near crossing."""
        assert split.long_split(DELIVERY, t_hog=10.0, v_hog=2.0,
                                far=far(t=12.0, reach=0.1, v_far=None, v_near=2.0)) is None
