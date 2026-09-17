"""What a shot was, judged from its flight and what it changed."""

from dataclasses import replace

import pytest

from curling_score.detect.delivery import Delivery
from curling_score.detect.release import Release
from curling_score.game import classify
from curling_score.game import split as split_mod
from curling_score.geometry import constants as C


def flight(y0=4.2, y1=0.0, x=0.2, speed=1.5, fps=10.0, color="red"):
    """A track running down-sheet at a constant speed."""
    out, t, y = [], 100.0, y0
    step = 1.0 / fps
    while y > y1:
        out.append((t, x, y))
        y -= speed * step
        t += step
    out.append((t, x, y1))
    return tuple(out)


def delivery(**kw):
    track = kw.pop("track", None)
    if track is None:
        track = flight(y1=kw.get("rest_y_m", 0.0), x=kw.get("rest_x_m", 0.2),
                       speed=kw.pop("speed", 1.5))
    base = dict(
        color="red",
        t_enter=track[0][0] if track else 100.0,
        t_rest=track[-1][0] if track else 110.0,
        entry_y_m=track[0][2] if track else 4.2,
        rest_x_m=0.2,
        rest_y_m=0.0,
        travel_m=4.2,
        came_to_rest=True,
        reason="rest",
        track=track,
    )
    base.update(kw)
    return Delivery(**base)


def climb(t_cross, speed=2.0, fps=10.0, span=1.0, x=0.0):
    """A stone leaving the thrower's house, crossing the paint at ``t_cross``."""
    n = max(1, int(span * fps / speed))
    return tuple(
        (t_cross + i / fps, x, split_mod.HOG_APPARENT_Y_M + speed * i / fps)
        for i in range(-n, n + 1)
    )


def paired(split_s, near_speed=2.0, far_speed=1.2, rest_y_m=-2.5):
    """A release and the delivery it was paired to, timed ``split_s`` apart.

    Both tracks cross the hog line's paint, so the split is exact by
    construction -- which is the whole reason it is measured there.
    """
    dv = delivery(track=flight(y0=5.6, y1=rest_y_m, speed=far_speed),
                  came_to_rest=False, reason="left-view", rest_y_m=rest_y_m)
    far = split_mod.crossing_time(dv.track, split_mod.HOG_APPARENT_Y_M)
    near = far - split_s
    rel = Release(color="red", t=near - 1.0, y_exit_m=5.5,
                  speed_m_s=near_speed, track=climb(near, speed=near_speed))
    return rel, dv


class TestSpeedAt:
    def test_it_measures_the_entry_speed_not_the_average(self):
        # Fast at the top, stopped at the bottom: the average is far lower
        # than the speed the stone actually entered at.
        track = tuple((100.0 + i * 0.1, 0.0, 4.0 - min(i, 10) * 0.3)
                      for i in range(40))
        dv = delivery(track=track, rest_y_m=1.0)
        assert dv.speed_at() == pytest.approx(3.0, abs=0.2)

    def test_a_track_too_short_to_measure_reports_zero(self):
        assert delivery(track=()).speed_at() == 0.0
        assert delivery(track=((1.0, 0.0, 4.0),)).speed_at() == 0.0

    def test_it_counts_sideways_motion_too(self):
        track = ((100.0, 0.0, 4.0), (100.5, 3.0, 4.0))
        assert delivery(track=track).speed_at() == pytest.approx(6.0)


class TestHitEvidence:
    """The house is the whole of it: a stone that moved another one is a hit."""

    def test_a_removed_stone_makes_it_a_hit(self):
        got, conf = classify.classify(
            delivery(speed=1.2),
            {"added": [], "removed": [{"color": "yellow", "x": 0.1, "y": 0.2}],
             "moved": []},
        )
        assert got == classify.HIT
        assert conf == classify.CONF_HOUSE_CHANGED

    def test_a_shifted_stone_makes_it_a_hit(self):
        got, _ = classify.classify(
            delivery(speed=1.2),
            {"added": [], "removed": [],
             "moved": [{"color": "yellow", "x": 0.1, "y": 0.2,
                        "from_x": 0.1, "from_y": 0.9, "distance_m": 0.7}]},
        )
        assert got == classify.HIT

    def test_merely_adding_its_own_stone_is_not_a_hit(self):
        # Every draw adds a stone. Only removals and shifts are contact.
        got, _ = classify.classify(
            delivery(speed=1.2),
            {"added": [{"color": "red", "x": 0.2, "y": 0.0}],
             "removed": [], "moved": []},
        )
        assert got == classify.DRAW

    def test_a_shooter_that_rolled_out_after_contact_is_still_a_hit(self):
        got, _ = classify.classify(
            delivery(speed=3.0, came_to_rest=False, reason="left-view",
                     rest_y_m=-2.4),
            {"added": [], "removed": [{"color": "yellow", "x": 0.0, "y": 0.3}],
             "moved": []},
        )
        assert got == classify.HIT


class TestRestGeometry:
    def test_a_stone_in_the_rings_is_a_draw(self):
        got, conf = classify.classify(delivery(rest_x_m=0.3, rest_y_m=0.5), None)
        assert got == classify.DRAW
        assert conf == classify.CONF_CLEAR_REST

    def test_a_stone_short_of_the_house_is_a_guard(self):
        got, conf = classify.classify(delivery(rest_x_m=0.1, rest_y_m=3.2), None)
        assert got == classify.GUARD
        assert conf == classify.CONF_CLEAR_REST

    def test_a_stone_past_the_tee_and_outside_the_rings_is_a_guard(self):
        # The tee line decides nothing: in the twelve-foot is a draw, and
        # everything else still in play is a guard, in front of the house or
        # behind it. Short of the back line at y = -1.971, so it is in play.
        dv = delivery(rest_x_m=1.5, rest_y_m=-1.8, came_to_rest=True)
        assert (dv.rest_x_m**2 + dv.rest_y_m**2) ** 0.5 > C.IN_HOUSE_MAX_D_M
        assert dv.rest_y_m > C.THROUGH_BACK_Y_M
        got, _ = classify.classify(dv, None)
        assert got == classify.GUARD

    def test_resting_just_outside_the_rings_is_flagged_as_uncertain_too(self):
        got, conf = classify.classify(
            delivery(rest_x_m=0.0, rest_y_m=C.IN_HOUSE_MAX_D_M + 0.05), None)
        assert got == classify.GUARD
        assert conf == classify.CONF_BOUNDARY

    def test_resting_on_the_house_edge_is_flagged_as_uncertain(self):
        got, conf = classify.classify(
            delivery(rest_x_m=0.0, rest_y_m=C.IN_HOUSE_MAX_D_M - 0.05), None)
        assert got == classify.DRAW
        assert conf == classify.CONF_BOUNDARY


class TestWeightDecidesNothing:
    """Entry speed does not separate takeouts from draws -- measured.

    Across the reference VOD, deliveries that moved a stone entered at a median
    of 0.80 m/s (max 1.73) and those that moved nothing at 0.38 (max 3.29). The
    stone is only in this view for its last few metres, by which point a takeout
    has shed its weight, so the two populations overlap end to end. These pin
    that down: where the house says nothing, speed must not be allowed to
    invent a hit.
    """

    def test_a_fast_stone_that_touched_nothing_is_not_called_a_hit(self):
        got, _ = classify.classify(delivery(speed=3.5, rest_y_m=0.4), None)
        assert got == classify.DRAW

    def test_a_slow_stone_that_removed_one_is_still_a_hit(self):
        got, _ = classify.classify(
            delivery(speed=0.3, rest_y_m=0.4),
            {"added": [], "removed": [{"color": "yellow", "x": 0, "y": 0}],
             "moved": []})
        assert got == classify.HIT

    def test_speed_is_reported_even_though_it_decides_nothing(self):
        # The charter watching the video can use it; the classifier cannot.
        dv = delivery(speed=3.5, rest_y_m=0.4)
        assert dv.speed_at() > 3.0


class TestOutOfPlay:
    """A rock that left play having touched nothing, named by its long split.

    It was either a draw thrown far too heavy or a takeout that flashed, and
    hog to hog those are minutes apart in kind: a takeout crosses in well under
    12.5 s, a draw in well over. That is the one measurement that separates
    them -- see ``TestWeightDecidesNothing`` for the one that does not.
    """

    def test_a_slow_stone_that_ran_out_the_back_was_a_draw_thrown_through(self):
        rel, dv = paired(18.0)
        got, conf = classify.classify(dv, None, rel)
        assert got == classify.DRAW_THROUGH
        assert conf == classify.CONF_CLEAR_REST

    def test_a_quick_stone_that_ran_out_the_back_was_a_takeout_that_flashed(self):
        rel, dv = paired(9.0)
        got, conf = classify.classify(dv, None, rel)
        assert got == classify.FLASHED
        assert conf == classify.CONF_CLEAR_REST

    def test_a_split_exactly_on_the_threshold_reads_as_a_flash(self):
        rel, dv = paired(classify.SPLIT_HIT_MAX_S)
        got, _ = classify.classify(dv, None, rel)
        assert got == classify.FLASHED

    def test_entry_speed_still_decides_nothing_here(self):
        # A draw thrown through, entering the panel faster than most takeouts.
        # The split says draw and the panel's speed is not allowed to argue.
        rel, dv = paired(18.0, near_speed=4.0, far_speed=3.5)
        assert dv.speed_at() > 3.0
        got, _ = classify.classify(dv, None, rel)
        assert got == classify.DRAW_THROUGH

    def test_a_stone_that_stopped_past_the_back_line_is_out_of_play_too(self):
        # Not every one of these leaves the panel: some are seen settling
        # behind the back line, which is just as much out of play.
        rel, dv = paired(9.0, rest_y_m=-2.5)
        got, _ = classify.classify(replace(dv, reason="rest", came_to_rest=True),
                                   None, rel)
        assert got == classify.FLASHED

    def test_without_a_split_it_is_a_flash_and_says_so(self):
        # Most throws are never timed at both hog lines, so this is the common
        # case. Choosing the takeout is a policy rather than evidence, and the
        # confidence is what keeps that honest.
        got, conf = classify.classify(
            delivery(speed=1.4, came_to_rest=False, reason="left-view",
                     rest_y_m=-2.5), None)
        assert got == classify.FLASHED
        assert conf == classify.CONF_BOUNDARY

    def test_a_release_the_camera_lost_before_the_paint_leaves_no_split(self):
        # Followed out of the far house, but not as far as the hog line. A
        # split is never extrapolated, so there is none, and this falls to the
        # same default as a shot with no release at all.
        rel, dv = paired(18.0)
        got, conf = classify.classify(dv, None, replace(rel, track=()))
        assert got == classify.FLASHED
        assert conf == classify.CONF_BOUNDARY

    def test_a_shot_is_classified_with_the_release_paired_to_it(self):
        # The split is the only evidence separating a draw thrown through from
        # a flash, and it lives on the release, not the arrival. A shot that
        # dropped it on the way through here would always read as a flash.
        from curling_score.game.shots import Shot

        rel, dv = paired(18.0)
        s = Shot(number=5, color="red", stones=[], t_rest_s=dv.t_rest,
                 delivery=dv, release=rel)
        assert classify.classify_shot(s)[0] == classify.DRAW_THROUGH


class TestRefusingToGuess:
    def test_no_delivery_is_unknown(self):
        assert classify.classify(None, None) == (classify.UNKNOWN, 0.0)

    def test_a_stone_never_seen_to_stop_or_leave_is_unknown(self):
        got, conf = classify.classify(
            delivery(speed=1.2, came_to_rest=False, reason="rest",
                     rest_y_m=1.0), None)
        assert got == classify.UNKNOWN
        assert conf == 0.0

    def test_a_placeholder_shot_is_unknown(self):
        from curling_score.game.shots import Shot

        blank = Shot(number=3, color="red", stones=[], t_rest_s=float("nan"),
                     missing=True, state_known=False)
        assert classify.classify_shot(blank) == (classify.UNKNOWN, 0.0)
