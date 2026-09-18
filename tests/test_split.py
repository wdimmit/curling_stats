"""The long split: hog line to hog line, timed where the paint is.

Both hog lines *are* inside the overhead panels. What is not inside them is a
trustworthy metre: the calibration fits one ``px_per_m`` from the house rings,
and the along-sheet scale collapses to roughly a third of it by the top of the
frame, so the hog line reports as +4.44 rather than 6.401. A crossing does not
care -- it needs where the paint sits in the panel's own numbers, and that is
``HOG_APPARENT_Y_M``, measured by hand against the paint.
"""

import pytest

from curling_score.detect.delivery import Delivery
from curling_score.detect.release import Release
from curling_score.game import split
from curling_score.geometry import constants as C

LINE = split.HOG_APPARENT_Y_M


def climbing(t0=0.0, y0=-2.0, y1=4.8, speed=2.0, fps=5.0):
    """A release track climbing up-sheet at a steady speed.

    Carried a little past the line so the samples bracket it; a real
    release is followed to about +4.55 when it is followed at all, and
    ``TestAgainstHandMarkedCrossings`` uses real ones.
    """
    out, t, y = [], t0, y0
    while y <= y1 + 1e-9:
        out.append((round(t, 3), 0.05, round(y, 4)))
        y += speed / fps
        t += 1.0 / fps
    return tuple(out)


def release_at(t0=0.0, speed=2.0, y1=4.8):
    tr = climbing(t0=t0, speed=speed, y1=y1)
    return Release(color="red", t=tr[0][0], y_exit_m=tr[-1][2],
                   speed_m_s=speed, track=tr)


def arriving(t0=20.0, y0=4.55, y1=0.2, speed=0.8, fps=10.0):
    """An arrival track running down-sheet toward the tee.

    It starts at +4.55: measured over a whole end, arrivals are first seen
    between +4.52 and +4.56, every one of them above the line.
    """
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


class TestTheHogLineIsNotWhereThePanelSaysItIs:
    """The constant is a panel coordinate, not a distance. Keep them apart."""

    def test_the_tripwire_sits_well_short_of_the_real_hog_line(self):
        assert LINE < C.TEE_TO_HOGLINE_M - 1.5

    def test_it_is_inside_what_the_panel_can_actually_see(self):
        # Releases are followed to about +4.55 and arrivals first seen between
        # +4.52 and +4.56, so the line has to sit below both.
        assert C.IN_HOUSE_MAX_D_M < LINE < 4.52

    def test_the_baseline_is_tee_to_tee_less_a_hog_line_at_each_end(self):
        # A radius short, because what was marked was the stone's leading edge
        # touching the line while the panel tracks its centre.
        assert split.BASELINE_M == pytest.approx(
            C.TEE_TO_TEE_M - 2 * (C.TEE_TO_HOGLINE_M - C.STONE_RADIUS_M), abs=1e-9)
        assert split.BASELINE_M == pytest.approx(22.229, abs=0.001)


class TestAgainstHandMarkedCrossings:
    """Real releases, against a person watching the paint in the side camera.

    This is what the constant rests on, so it is what a change to it has to
    answer to. Each track is the tail of a delivery from game 1 end 5 of
    ``VXU9xwmugRg``; each time is where a person marked the stone's leading
    edge touching the hog line, using ``scripts/mark_hog.py``.
    """

    MARKED = [
        # yellow released 3824.0
        (3827.233, (
            (3826.2, 0.372, 3.236), (3826.4, 0.37, 3.533), (3826.6, 0.363, 3.796),
            (3826.8, 0.359, 4.028), (3827.0, 0.349, 4.231), (3827.2, 0.349, 4.412),
            (3827.4, 0.347, 4.554),
        )),
        # red released 3871.8
        (3875.9, (
            (3874.6, 0.438, 3.119), (3874.8, 0.445, 3.382), (3875.0, 0.455, 3.634),
            (3875.2, 0.455, 3.848), (3875.4, 0.455, 4.043), (3875.6, 0.46, 4.216),
            (3875.8, 0.464, 4.374), (3876.0, 0.475, 4.464),
        )),
        # yellow released 3920.4
        (3923.467, (
            (3922.4, 0.396, 3.052), (3922.6, 0.396, 3.405), (3922.8, 0.396, 3.705),
            (3923.0, 0.396, 3.968), (3923.2, 0.389, 4.197), (3923.4, 0.393, 4.389),
            (3923.6, 0.393, 4.547),
        )),
        # yellow released 4445.8
        (4449.0, (
            (4448.0, 0.257, 3.292), (4448.2, 0.257, 3.593), (4448.4, 0.259, 3.863),
            (4448.6, 0.255, 4.104), (4448.8, 0.246, 4.314), (4449.0, 0.248, 4.464),
        )),
    ]

    def test_the_tripwire_lands_where_a_person_marked_the_paint(self):
        for marked, track in self.MARKED:
            got = split.hog_crossing(track)
            assert got is not None
            assert got == pytest.approx(marked, abs=0.06), f"marked {marked}"

    def test_reading_the_panel_s_own_metres_instead_is_a_second_late(self):
        """What the old code did, and why it was worth changing.

        Extrapolating to 6.401 in the panel's coordinates overshoots the paint
        by more than two of its units, which on these throws is about a second.
        """
        for marked, track in self.MARKED:
            t_last, _x, y_last = track[-1]
            speed = (track[-1][2] - track[0][2]) / (track[-1][0] - track[0][0])
            old = t_last + (C.TEE_TO_HOGLINE_M - y_last) / speed
            assert old - marked > 0.7


class TestHogCrossing:
    def test_times_the_crossing_of_the_paint(self):
        tr = climbing(t0=0.0, y0=-2.0, y1=4.8, speed=2.0, fps=5.0)
        assert split.hog_crossing(tr) == pytest.approx(
            split.crossing_time(tr, LINE), abs=1e-9)

    def test_a_throw_lost_short_of_the_line_is_not_carried_there(self):
        """Not even a little. The scale is collapsing fastest right there, so
        the apparent speed at the end of the track means nothing."""
        assert split.hog_crossing(climbing(y1=LINE - 0.1)) is None
        assert split.hog_crossing(climbing(y1=1.0)) is None

    def test_a_release_with_no_track_gives_nothing(self):
        assert split.hog_crossing(()) is None
        assert split.hog_crossing(None) is None


class TestLongSplit:
    def test_measures_between_the_two_crossings(self):
        r = release_at(t0=0.0, speed=2.0)
        d = delivery_at(t0=30.0, speed=0.8)
        s = split.long_split(r, d, t_hog=split.hog_crossing(r.track))
        assert s is not None
        assert s.baseline_m == pytest.approx(split.BASELINE_M, abs=1e-6)
        assert s.t_start == pytest.approx(split.crossing_time(r.track, LINE), abs=1e-9)
        assert s.t_end == pytest.approx(split.crossing_time(d.track, LINE), abs=1e-9)
        assert s.seconds == pytest.approx(s.t_end - s.t_start, abs=1e-9)

    def test_an_arrival_first_seen_below_the_line_is_unmeasured(self):
        r = release_at()
        d = delivery_at(t0=30.0, y0=3.0, y1=0.2)
        assert split.long_split(r, d, t_hog=split.hog_crossing(r.track)) is None

    def test_a_throw_lost_before_the_line_is_unmeasured(self):
        r = release_at(y1=1.0)
        assert split.long_split(
            r, delivery_at(), t_hog=split.hog_crossing(r.track)) is None

    def test_no_release_no_longer_means_no_split(self):
        """Deliberately reversed. A release feeds neither end of a hog-to-hog
        split; it fed two checks, both already conditional. Requiring one
        refused 35 shots on VXU9xwmugRg that had both crossings -- and the
        overhead camera losing ~40% of throws before the hog line is the whole
        reason the side view was built. See TestReleaseIsOptional below for
        what replaced the mispairing check it did buy.
        """
        assert split.long_split(None, delivery_at(), t_hog=0.0) is not None

    def test_no_delivery_means_no_split(self):
        r = release_at()
        assert split.long_split(
            r, None, t_hog=split.hog_crossing(r.track)) is None

    def test_a_backwards_split_is_refused(self):
        """An arrival timed before its own throw is a pairing error, not a split."""
        r = release_at(t0=100.0)
        d = delivery_at(t0=10.0)
        assert split.long_split(r, d, t_hog=split.hog_crossing(r.track)) is None


class TestItRefusesAPhysicallyImpossibleSplit:
    """The pairing is not precise enough to time with, so the split checks itself.

    ``release.pair`` answers "did this throw arrive at all?" inside a 6-30 s
    window, which is ample for a yes/no but is the whole measurement here. On
    game 1 end 4 it paired three arrivals to a release 10-15 s earlier where the
    clean ones run 18-20 s.

    A stone only ever slows, so it cannot cross the far hog line faster than it
    crossed the near one. Both are read at the same place in their own panel,
    in the panel's own units, so the comparison never converts anything -- the
    distortion that makes those units wrong is the same on both sides.
    """

    def _arrival_crossing_at(self, t_cross, y0=4.55, speed=0.8):
        """An arrival whose crossing of the line lands at ``t_cross``."""
        return delivery_at(t0=t_cross - (y0 - LINE) / speed, y0=y0, speed=speed)

    def test_speed_at_line_reads_the_rate_across_the_paint(self):
        tr = climbing(t0=0.0, speed=2.0, fps=5.0)
        assert split.speed_at_line(tr) == pytest.approx(2.0, abs=0.01)
        assert split.speed_at_line(arriving(speed=0.8)) == pytest.approx(0.8, abs=0.01)

    def test_a_track_that_never_reaches_the_line_has_no_speed_there(self):
        assert split.speed_at_line(climbing(y1=1.0)) is None
        assert split.speed_at_line(()) is None
        assert split.speed_at_line(None) is None

    def test_a_stone_apparently_faster_at_the_far_line_is_refused(self):
        r = release_at(t0=0.0, speed=1.0)
        start = split.hog_crossing(r.track)
        d = self._arrival_crossing_at(start + 12.0, speed=2.0)
        assert split.speed_at_line(d.track) > split.speed_at_line(r.track)
        assert split.long_split(r, d, t_hog=start) is None

    def test_a_stone_that_slowed_on_the_way_down_is_kept(self):
        r = release_at(t0=0.0, speed=2.0)
        start = split.hog_crossing(r.track)
        s = split.long_split(
            r, self._arrival_crossing_at(start + split.BASELINE_M / 1.5),
            t_hog=start)
        assert s is not None
        assert s.speed_m_s == pytest.approx(1.5, abs=0.05)

    def test_a_peel_thrown_hard_is_kept(self):
        """A takeout is fast at both ends; the check must not punish weight."""
        r = release_at(t0=0.0, speed=2.8)
        start = split.hog_crossing(r.track)
        assert split.long_split(
            r, self._arrival_crossing_at(start + split.BASELINE_M / 2.5,
                                         speed=2.5),
            t_hog=start) is not None

    def test_the_tolerance_forgives_measurement_noise_only(self):
        assert 1.0 < split.SPEED_TOLERANCE <= 1.5

    def test_the_split_reports_its_own_mean_speed(self):
        r = release_at(t0=0.0, speed=2.0)
        start = split.hog_crossing(r.track)
        s = split.long_split(
            r, self._arrival_crossing_at(start + split.BASELINE_M / 1.6),
            t_hog=start)
        assert s.speed_m_s == pytest.approx(1.6, abs=0.05)
        assert s.speed_m_s == pytest.approx(s.baseline_m / s.seconds, abs=1e-9)


class TestTheSideViewIsTheSourceForTheThrowingEnd:
    """The panel keeps timing the target end, and cross-checks the other one."""

    def test_the_throwing_end_comes_from_the_side_view_when_given(self):
        r = release_at(t0=0.0, speed=2.0)
        d = delivery_at(t0=40.0, speed=0.8)
        panel = split.hog_crossing(r.track)
        s = split.long_split(r, d, t_hog=panel + 0.1)
        assert s.t_start == pytest.approx(panel + 0.1, abs=1e-9)

    def test_without_one_there_is_no_split_even_if_the_panel_saw_it(self):
        """One method per game: a shot the side view refused has no split,
        rather than a second-best number that cannot be compared with its
        neighbours."""
        r = release_at(t0=0.0, speed=2.0)
        assert split.hog_crossing(r.track) is not None
        assert split.long_split(r, delivery_at(t0=40.0), t_hog=None) is None

    def test_the_two_disagreeing_no_longer_refuses_both(self):
        """Deliberately reversed. The long camera is the primary timing source
        and a disagreement is recorded, not obeyed.

        The veto was discarding correct answers: of three refused shots on
        AEqLTgM25Tc with an independent hand mark, the side view was right to
        0.03 s and the panel wrong by up to 0.95 s every time. The sync pass
        then found why -- eight of nine cached recordings have a camera pair
        out of step -- so the panel is not a second opinion about the same
        instant, it is a clock that disagrees, and a threshold cannot tell that
        from a mispairing.
        """
        r = release_at(t0=0.0, speed=2.0)
        panel = split.hog_crossing(r.track)
        d = delivery_at(t0=40.0, speed=0.8)
        far = split.CROSS_CHECK_S * 3
        sp = split.long_split(r, d, t_hog=panel + far)
        assert sp is not None
        assert sp.panel_delta == pytest.approx(far, abs=1e-6)

    def test_a_split_records_the_panel_disagreement(self):
        r = release_at(t0=0.0, speed=2.0)
        panel = split.hog_crossing(r.track)
        d = delivery_at(t0=40.0, speed=0.8)
        sp = split.long_split(r, d, t_hog=panel + 0.05)
        assert sp.panel_delta == pytest.approx(0.05, abs=1e-6)

    def test_no_panel_reading_means_no_disagreement_to_record(self):
        """None, not zero: 'the panel did not see it' and 'the panel agreed
        exactly' are different facts and the sync report reads them apart."""
        d = delivery_at(t0=40.0, speed=0.8)
        sp = split.long_split(None, d, t_hog=5.0, v_hog=2.5)
        assert sp is not None and sp.panel_delta is None

    def test_a_disagreement_inside_the_tolerance_is_kept(self):
        r = release_at(t0=0.0, speed=2.0)
        panel = split.hog_crossing(r.track)
        d = delivery_at(t0=40.0, speed=0.8)
        assert split.long_split(r, d, t_hog=panel + split.CROSS_CHECK_S / 2)

    def test_a_disagreement_of_exactly_the_tolerance_is_kept(self):
        """The check is a strict ``>``, so a disagreement of exactly
        ``CROSS_CHECK_S`` is a decision, not an accident: pin it down."""
        r = release_at(t0=0.0, speed=2.0)
        panel = split.hog_crossing(r.track)
        d = delivery_at(t0=40.0, speed=0.8)
        assert split.long_split(r, d, t_hog=panel + split.CROSS_CHECK_S)

    def test_a_throw_the_panel_never_saw_still_gets_a_split(self):
        """This is the whole point: 40% of throws are lost before the line."""
        r = release_at(t0=0.0, speed=2.0, y1=2.5)     # lost well short
        assert split.hog_crossing(r.track) is None
        s = split.long_split(r, delivery_at(t0=40.0), t_hog=5.0)
        assert s is not None and s.t_start == pytest.approx(5.0)


class TestReleaseIsOptional:
    """`long_split` is hog to hog: the near crossing comes from the side view
    and the far one from the arriving end's panel. A release track feeds
    neither, only two checks -- and requiring one anyway refused 35 shots on
    VXU9xwmugRg that had both crossings. The overhead camera losing ~40% of
    throws before the hog line is the reason the side view exists; demanding a
    release put that loss straight back.
    """

    def _delivery(self, t_far):
        class D:
            track = tuple((t_far - 1 + i * 0.5, 0.0, split.HOG_APPARENT_Y_M - 1 + i)
                          for i in range(4))
        return D()

    def test_a_split_publishes_with_no_release_at_all(self):
        sp = split.long_split(None, self._delivery(20.0), t_hog=10.0, v_hog=2.5)
        assert sp is not None
        assert sp.seconds > 0

    def test_it_still_needs_both_crossings(self):
        assert split.long_split(None, None, t_hog=10.0, v_hog=2.5) is None
        assert split.long_split(None, self._delivery(20.0), t_hog=None) is None

    def test_a_mean_speed_the_near_crossing_cannot_account_for_is_refused(self):
        """Without a release there is no panel speed to compare against, so
        the bound is physical: a stone only slows, so its mean speed over the
        baseline cannot exceed the speed it crossed the first line at."""
        # 22.229 m in 2 s is 11 m/s, against a near crossing of 2.0 m/s.
        assert split.long_split(None, self._delivery(12.0), t_hog=10.0,
                                v_hog=2.0) is None

    def test_that_bound_does_not_fire_on_a_plausible_split(self):
        sp = split.long_split(None, self._delivery(20.0), t_hog=10.0, v_hog=2.5)
        assert sp is not None

    def test_without_v_hog_the_bound_cannot_run_and_does_not_pretend_to(self):
        """A split with neither a release nor a side-view speed is published
        unchecked for pairing. That is a real gap, not an oversight -- it is
        worth knowing rather than hiding behind a default."""
        sp = split.long_split(None, self._delivery(12.0), t_hog=10.0, v_hog=None)
        assert sp is not None


class TestFarCrossingExtrapolation:
    """The arriving panel's track often begins just below its hog line, so the
    crossing is never observed. Reaching back for it is allowed, but only a
    little, and the result is marked.

    Measured over all 139 tracks on VXU9xwmugRg that DO cross, by hiding
    everything above a cut: at a reach of 0.05 about 9 in 10 land inside the
    0.15 s a crossing is judged by; at 0.14 that falls to 8 in 10 and the bias
    grows. There is no physical threshold here -- it is a judgement about how
    much unverifiable error to accept.
    """

    def _track(self, y0, y1, n=8, t0=10.0, dt=0.1):
        step = (y1 - y0) / (n - 1)
        return tuple((t0 + i * dt, 0.0, y0 + i * step) for i in range(n))

    def test_an_observed_crossing_is_not_extrapolated(self):
        tr = self._track(4.60, 4.20)
        t, reach = split.far_crossing(tr, split.HOG_APPARENT_Y_M, max_reach=0.05)
        assert t is not None
        assert reach == 0.0

    def test_a_track_beginning_just_below_the_line_is_reached_for(self):
        tr = self._track(4.42, 4.10)
        t, reach = split.far_crossing(tr, split.HOG_APPARENT_Y_M, max_reach=0.05)
        assert t is not None
        assert reach == pytest.approx(split.HOG_APPARENT_Y_M - 4.42, abs=1e-6)

    def test_a_track_beginning_far_below_it_is_refused(self):
        tr = self._track(4.20, 3.90)
        t, reach = split.far_crossing(tr, split.HOG_APPARENT_Y_M, max_reach=0.05)
        assert t is None and reach == 0.0

    def test_the_reach_lands_before_the_first_sample(self):
        """The stone reached the line before the panel had it, so the time must
        be earlier than the track's own first point."""
        tr = self._track(4.42, 4.10)
        t, _ = split.far_crossing(tr, split.HOG_APPARENT_Y_M, max_reach=0.05)
        assert t < tr[0][0]

    def test_no_reach_at_all_is_the_old_behaviour(self):
        tr = self._track(4.42, 4.10)
        assert split.far_crossing(tr, split.HOG_APPARENT_Y_M)[0] is None

    def test_a_split_records_how_far_it_reached(self):
        # t_hog=2.0, not 5.0: the far crossing lands near 9.9 s, and over the
        # 22.229 m baseline a 4.9 s split implies 4.54 m/s, which the pairing
        # bound rightly refuses against a 2.5 m/s near crossing. The fixture was
        # wrong, not the guard.
        class D:
            track = None
        D.track = self._track(4.42, 4.10)
        sp = split.long_split(None, D(), t_hog=2.0, v_hog=2.5)
        assert sp is not None
        assert sp.far_reach > 0

    def test_an_observed_split_records_no_reach(self):
        class D:
            track = None
        D.track = self._track(4.60, 4.10)
        sp = split.long_split(None, D(), t_hog=2.0, v_hog=2.5)
        assert sp is not None
        assert sp.far_reach == 0.0
