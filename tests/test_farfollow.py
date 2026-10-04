"""Where a throw went that the far house's overhead could not place, from the
long camera facing that house (game.farfollow)."""
from types import SimpleNamespace

import pytest

from curling_score.detect import delivery as D, release as R
from curling_score.detect.rocks import Detection
from curling_score.game import farfollow as FF
from curling_score.geometry import constants as C

T0 = 100.0          # the release


def slide(stop_y, *, x=0.3, stop_at=T0 + 24.0, start_y=15.0, start_at=T0 + 10.0, fps=4.0):
    """(t, x, y) of a stone sliding down the sheet and stopping at ``stop_y``."""
    out, t = [], start_at
    while t <= stop_at:
        f = (t - start_at) / (stop_at - start_at)
        # Decelerating: covers ground fast early, slowly near the stop.
        y = stop_y + (start_y - stop_y) * (1 - f) ** 2
        out.append((round(t, 2), x, y))
        t += 1 / fps
    return out


def samples(*paths, static=(), until=T0 + 45.0, fps=4.0):
    """Per-frame detections from stone paths (each extended by ``hold``) and
    stones sitting still all window."""
    by_t = {}
    t = T0 + 6.0
    while t <= until:
        by_t[round(t, 2)] = [(x, y, 0.9) for x, y in static]
        t += 1 / fps
    for path in paths:
        for pt, x, y in path:
            if round(pt, 2) in by_t:
                by_t[round(pt, 2)].append((x, y, 0.9))
    return sorted(by_t.items())


def hold(path, until, fps=4.0):
    """The path, then its last position held still until ``until``."""
    t, x, y = path[-1]
    out = list(path)
    while t + 1 / fps <= until:
        t = round(t + 1 / fps, 2)
        out.append((t, x, y))
    return out


def push(path, to_y, over_s, x=-0.6, fps=4.0):
    """The path, then a player pushing the stone on down the sheet."""
    t, x0, y0 = path[-1]
    out = list(path)
    n = int(over_s * fps)
    for k in range(1, n + 1):
        out.append((round(t + k / fps, 2), x0 + (x - x0) * k / n, y0 + (to_y - y0) * k / n))
    return out


class TestReadOutcome:
    def test_a_guard_that_stops_inside_the_hog_line_and_stays_is_a_rest(self):
        """Thursday Mens 10/01 S5 game 2 end 4 rock 13: stopped at (-0.61,
        +4.88), frozen to a yellow guard, and stayed."""
        p = hold(slide(4.9, x=-0.6), T0 + 45)
        got = FF.read_outcome(samples(p, static=[(-0.35, 4.75)]), T0)
        assert got.kind == FF.REST
        assert (got.x_m, got.y_m) == pytest.approx((-0.6, 4.9), abs=0.15)

    def test_a_guard_frozen_beside_another_keeps_its_own_spot(self):
        """Wednesday Womens 09/30 S1 end 3 rock 11 stopped at (-0.23, +5.61),
        frozen beside a red at (-0.59, +5.68); at a flat 0.45 m gate the
        follower stepped over to the old stone and reported its place."""
        p = hold(slide(5.61, x=-0.23), T0 + 45)
        got = FF.read_outcome(samples(p, static=[(-0.59, 5.68)]), T0)
        assert got.kind == FF.REST
        assert got.x_m == pytest.approx(-0.23, abs=0.05)

    def test_a_stone_stopped_on_the_line_then_pushed_on_is_a_hog(self):
        """Tuesday Super 3/3 S4 end 6 rock 1: stopped at 6.35 m, pushed within
        seconds down to behind the house."""
        p = push(hold(slide(6.4, x=0.05, stop_at=T0 + 26), T0 + 28), -4.0, 6.0)
        got = FF.read_outcome(samples(p), T0)
        assert got.kind == FF.HOG
        assert got.y_m == pytest.approx(6.4, abs=0.2)

    def test_a_hog_pushed_on_after_a_brief_stop_is_still_a_hog(self):
        """Pushed before it had stood still for long: a sliding stone only
        slows, so stopping and speeding up again is a person."""
        p = push(hold(slide(6.4, stop_at=T0 + 24), T0 + 24.75), -4.0, 6.0)
        assert FF.read_outcome(samples(p), T0).kind == FF.HOG

    def test_a_stone_running_through_the_house_is_through(self):
        p = [(T0 + 10 + k * 0.25, 0.2, 15.0 - k * 0.45) for k in range(40)]
        p = [q for q in p if q[2] >= -1.0]
        got = FF.read_outcome(samples(p), T0)
        assert got.kind == FF.THROUGH

    def test_running_through_too_late_to_have_the_pace_is_not_through(self):
        """Crossing the hog line 25 s after the release: no throw that slow
        reaches the back of the house."""
        p = [(T0 + 20 + k * 0.25, 0.2, 15.0 - k * 0.45) for k in range(40)]
        p = [q for q in p if q[2] >= -1.0]
        assert FF.read_outcome(samples(p), T0) is None

    def test_coming_to_rest_behind_the_back_line_in_time_is_through(self):
        """Thursday Mens 10/01 S5 end 2 rock 2 ran through and came to rest
        parked at (+2.02, -4.60), out of play."""
        p = hold(slide(-4.6, x=1.0, stop_at=T0 + 26, start_at=T0 + 8), T0 + 45)
        assert FF.read_outcome(samples(p), T0).kind == FF.THROUGH

    def test_nothing_followed_says_nothing(self):
        assert FF.read_outcome(samples(static=[(0.4, 2.0)]), T0) is None

    def test_stones_sitting_still_are_not_the_throw(self):
        """Nothing up-sheet to follow: a guard already in play is not a throw."""
        assert FF.read_outcome(samples(static=[(0.4, 5.2), (-0.3, 3.1)]), T0) is None

    def test_stopped_in_play_and_moved_promptly_is_left_to_the_overhead(self):
        """Ruled neither a guard (it did not stay) nor a hog (too far inside
        the line for a misjudged one)."""
        p = push(hold(slide(3.0, stop_at=T0 + 24), T0 + 27), -4.0, 6.0)
        assert FF.read_outcome(samples(p), T0) is None


def _track_frames(color, entry_t, ys, x=0.1, dt=0.1):
    return [(entry_t + k * dt, [Detection(color=color, x_m=x, y_m=y, x_px=0.0, y_px=0.0, area_px=100.0, confidence=0.9)])
            for k, y in enumerate(ys)]


class TestEntryTrack:
    def test_a_stone_entering_in_time_to_run_through_is_found(self):
        frames = _track_frames("red", T0 + 15.0, [4.6 - 0.07 * k for k in range(90)])
        tr = FF.entry_track(frames, "red", T0)
        assert tr and tr[0][0] == pytest.approx(T0 + 15.0)

    def test_one_entering_late_is_a_hog_being_pushed(self):
        frames = _track_frames("red", T0 + 22.0, [4.6 - 0.07 * k for k in range(90)])
        assert FF.entry_track(frames, "red", T0) is None

    def test_the_other_colour_is_not_this_throw(self):
        frames = _track_frames("yellow", T0 + 15.0, [4.6 - 0.07 * k for k in range(90)])
        assert FF.entry_track(frames, "red", T0) is None


def _release(color="red", t=T0):
    return R.Release(color=color, t=t, y_exit_m=4.0, speed_m_s=2.0)


class TestPlace:
    def test_a_hogged_release_the_camera_saw_stop_in_play_rests_there(self):
        r = _release()
        follow = lambda color, t: FF.Outcome(FF.REST, T0 + 24, -0.6, 4.9, 80)
        arrivals, pairs, out, n = FF.place([R.as_delivery(r)], {}, [], [], follow_fn=follow)
        assert n == 1
        assert out[0].reason == R.REASON_REST
        assert (out[0].rest_x_m, out[0].rest_y_m, out[0].came_to_rest) == (-0.6, 4.9, True)
        assert out[0].t_enter == T0 and out[0].release is r

    def test_one_it_saw_hogged_stays_hogged(self):
        r = _release()
        follow = lambda color, t: FF.Outcome(FF.HOG, T0 + 26, 0.0, 6.4, 80)
        frames = _track_frames("red", T0 + 22.0, [4.6 - 0.07 * k for k in range(90)])
        _a, _p, out, n = FF.place([R.as_delivery(r)], {}, [], frames, follow_fn=follow)
        assert (n, out[0].reason) == (0, R.REASON)

    def test_the_overhead_seeing_it_run_in_in_time_outranks_the_camera(self):
        """Sunday Skips 09/27 S2 end 6 rock 5 ran through past a guard; the
        camera lost it for a frame beside the guard and took the guard."""
        r = _release()
        calls = []
        def follow(color, t):
            calls.append(t)
            return FF.Outcome(FF.REST, T0 + 22, -0.1, 2.4, 80)
        frames = _track_frames("red", T0 + 17.9, [4.6 - 0.07 * k for k in range(90)])
        _a, _p, out, n = FF.place([R.as_delivery(r)], {}, [], frames, follow_fn=follow)
        assert (n, out[0].reason, calls) == (1, R.REASON_THROUGH, [])

    def test_the_camera_unsure_and_the_overhead_seeing_it_enter_in_time_runs_it_through(self):
        r = _release()
        frames = _track_frames("red", T0 + 15.0, [4.6 - 0.07 * k for k in range(90)])
        _a, _p, out, n = FF.place([R.as_delivery(r)], {}, [], frames, follow_fn=lambda c, t: None)
        assert n == 1
        assert out[0].reason == R.REASON_THROUGH
        assert out[0].rest_y_m == C.THROUGH_BACK_Y_M and not out[0].came_to_rest
        assert out[0].track[0][0] == pytest.approx(T0 + 15.0)

    def test_the_camera_unsure_and_nothing_in_time_stays_hogged(self):
        r = _release()
        frames = _track_frames("red", T0 + 22.0, [4.6 - 0.07 * k for k in range(90)])
        _a, _p, out, n = FF.place([R.as_delivery(r)], {}, [], frames, follow_fn=lambda c, t: None)
        assert (n, out[0].reason) == (0, R.REASON)

    def test_a_release_settled_otherwise_is_left_alone(self):
        r = _release()
        added = R.came_to_rest_at(r, T0 + 30, 0.1, 1.0)
        calls = []
        _a, _p, out, n = FF.place([added], {}, [], [], follow_fn=lambda c, t: calls.append(t))
        assert (n, out, calls) == (0, [added], [])

    def _late_pair(self):
        r = _release()
        pushed = D.Delivery(color="red", t_enter=T0 + 21.4, t_rest=T0 + 30.0, entry_y_m=4.56,
                            rest_x_m=1.5, rest_y_m=-2.0, travel_m=6.5, came_to_rest=False,
                            reason="gap-search")
        return r, pushed

    def test_a_late_arrival_that_ran_on_is_the_hog_pushed_down_the_sheet(self):
        """Sunday Skips 09/27 S2 end 3 rock 15: paired with a gap-search arrival
        21.4 s on that ran through; the user saw a hog pushed down."""
        r, pushed = self._late_pair()
        arrivals, pairs, out, n = FF.place([], {r: pushed}, [pushed], [],
                                           follow_fn=lambda c, t: None)
        assert (n, arrivals, pairs) == (1, [], {})
        assert out[0].reason == R.REASON and out[0].release is r

    def test_unless_the_camera_saw_the_throw_itself_arrive(self):
        r, pushed = self._late_pair()
        follow = lambda c, t: FF.Outcome(FF.THROUGH, T0 + 19, 0.2, 0.5, 60)
        arrivals, pairs, out, n = FF.place([], {r: pushed}, [pushed], [], follow_fn=follow)
        assert (n, arrivals, pairs, out) == (0, [pushed], {r: pushed}, [])

    def test_a_late_arrival_that_came_to_rest_is_kept(self):
        """A light draw reaches the far edge late; that is not a push."""
        r = _release()
        guard = D.Delivery(color="red", t_enter=T0 + 21.0, t_rest=T0 + 26.0, entry_y_m=4.5,
                           rest_x_m=-0.2, rest_y_m=3.8, travel_m=0.7, came_to_rest=True)
        calls = []
        _a, pairs, out, n = FF.place([], {r: guard}, [guard], [], follow_fn=lambda c, t: calls.append(t))
        assert (n, pairs, out, calls) == (0, {r: guard}, [], [])


class TestFollowNeedsTheCamera:
    def test_no_view_or_no_lateral_calibration_says_nothing(self):
        assert FF.follow("v.mp4", None, "red", T0) is None
        assert FF.follow("v.mp4", SimpleNamespace(has_lateral=False), "red", T0) is None
