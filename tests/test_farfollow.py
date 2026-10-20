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


def _stop_then_swept(entry_t, stop_y=0.82, hold_s=2.4, x=0.05, dt=0.1):
    """A stone coming in at the far edge, slowing to a stop at ``stop_y``,
    then swept on out of the house by the players clearing it."""
    # Constant deceleration from the far edge to the stop, as a sliding stone does.
    run = 4.75 - stop_y
    v0 = 0.7
    a = v0 * v0 / (2 * run)
    pts, k = [], 0
    while k * dt < v0 / a:
        tt = k * dt
        pts.append((entry_t + tt, 4.75 - (v0 * tt - a * tt * tt / 2)))
        k += 1
    t = entry_t + k * dt
    for _ in range(int(hold_s / dt)):
        pts.append((t, stop_y))
        t += dt
    y = stop_y
    for _ in range(40):
        y -= 0.07
        pts.append((t, y))
        t += dt
    return [(pt, [Detection(color="yellow", x_m=x, y_m=py, x_px=0.0, y_px=0.0, area_px=100.0,
                            confidence=0.9)]) for pt, py in pts]


class TestEntry:
    def test_a_stone_running_in_in_time_runs_through(self):
        frames = _track_frames("red", T0 + 15.0, [4.6 - 0.07 * k for k in range(90)])
        o, track = FF.entry(frames, "red", T0)
        assert o.kind == FF.THROUGH and track[0][0] == pytest.approx(T0 + 15.0)

    def test_one_that_stopped_in_play_rests_there_whatever_happened_next(self):
        """Tuesday Super 3/3 S4 end 4 rock 16 came in at 18.2 s, stopped at
        (+0.05, +0.82), and the players clearing the house swept it out 2.4 s
        later; read whole, the track looked like a stone that ran through."""
        o, track = FF.entry(_stop_then_swept(T0 + 18.2), "yellow", T0)
        assert o.kind == FF.REST
        assert (o.x_m, o.y_m) == pytest.approx((0.05, 0.82), abs=0.05)
        assert track[-1][2] == pytest.approx(0.82, abs=0.05)

    def test_one_entering_late_is_a_hog_being_pushed(self):
        frames = _track_frames("red", T0 + 22.0, [4.6 - 0.07 * k for k in range(90)])
        assert FF.entry(frames, "red", T0) is None

    def test_one_stopping_out_of_play_behind_the_house_ran_through(self):
        ys = [4.6 - 0.07 * k for k in range(95)] + [4.6 - 0.07 * 94] * 20
        o, _track = FF.entry(_track_frames("red", T0 + 15.0, ys), "red", T0)
        assert o.kind == FF.THROUGH

    def test_the_other_colour_is_not_this_throw(self):
        frames = _track_frames("yellow", T0 + 15.0, [4.6 - 0.07 * k for k in range(90)])
        assert FF.entry(frames, "red", T0) is None

    def test_a_short_run_that_never_stops_says_nothing(self):
        """Seen a moment at the far edge, then lost: the camera is asked."""
        frames = _track_frames("red", T0 + 15.0, [4.6 - 0.07 * k for k in range(10)])
        assert FF.entry(frames, "red", T0) is None


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

    def test_one_the_overhead_saw_stop_in_play_rests_there(self):
        r = _release(color="yellow")
        _a, _p, out, n = FF.place([R.as_delivery(r)], {}, [], _stop_then_swept(T0 + 18.2),
                                  follow_fn=lambda c, t: None)
        assert (n, out[0].reason, out[0].came_to_rest) == (1, R.REASON_REST, True)
        assert (out[0].rest_x_m, out[0].rest_y_m) == pytest.approx((0.05, 0.82), abs=0.05)

    def _house_add(self, r, x=1.75, y=-1.94):
        """The house's account of a throw it never saw arrive: a stone of its
        colour that appeared somewhere by the time `release.settle` looked."""
        return D.Delivery(color=r.color, t_enter=r.t, t_rest=r.t + 36.0, entry_y_m=C.HOGGED_Y_M,
                          rest_x_m=x, rest_y_m=y, travel_m=C.HOGGED_Y_M - y, came_to_rest=True,
                          reason=R.REASON_ADD, release=r)

    def test_a_release_the_house_placed_rests_where_the_overhead_saw_it_stop(self):
        """Tuesday Super 10/13 S1 end 1 rock 16 (f_1XMj): a draw stopped at
        (-0.72, +1.20) and was swept off by the players clearing the house, so
        its arrival was refused; 36 s on, `settle` read the clearing and took a
        yellow being pushed to (+1.75, -1.94) for the rock. The overhead saw it
        come in on time and stop: that is where it rests."""
        r = _release(color="yellow")
        _a, _p, out, n = FF.place([self._house_add(r)], {}, [], _stop_then_swept(T0 + 18.2),
                                  follow_fn=lambda c, t: None)
        assert (n, out[0].reason, out[0].came_to_rest) == (1, R.REASON_REST, True)
        assert (out[0].rest_x_m, out[0].rest_y_m) == pytest.approx((0.05, 0.82), abs=0.05)
        assert out[0].release is r

    def test_a_release_the_house_says_rolled_out_runs_through_when_seen_to(self):
        r = _release()
        removed = D.Delivery(color="red", t_enter=T0, t_rest=T0 + 36.0, entry_y_m=C.HOGGED_Y_M,
                             rest_x_m=0.0, rest_y_m=C.THROUGH_BACK_Y_M,
                             travel_m=C.HOGGED_Y_M - C.THROUGH_BACK_Y_M, came_to_rest=False,
                             reason=R.REASON_REMOVE, release=r)
        frames = _track_frames("red", T0 + 15.0, [4.6 - 0.07 * k for k in range(90)])
        _a, _p, out, n = FF.place([removed], {}, [], frames, follow_fn=lambda c, t: None)
        assert (n, out[0].reason) == (1, R.REASON_THROUGH)

    def test_only_the_end_s_last_throw(self):
        """Mid-end the house is read before the next rock arrives and nobody is
        clearing it, and a tap or a raise moves two stones of one colour --
        which the house and a track can each take for the throw (Sunday Skips
        10/11 S1 e3 r4 and Thursday Morning 10/15 S2 e4 r13 checked on video:
        the house was right). Only the last throw's read falls in the clearing."""
        r, later = _release(color="yellow"), _release(color="red", t=T0 + 40.0)
        arrival = D.Delivery(color="red", t_enter=T0 + 55.0, t_rest=T0 + 60.0, entry_y_m=4.5,
                             rest_x_m=0.0, rest_y_m=1.0, travel_m=3.5, came_to_rest=True)
        add = self._house_add(r)
        _a, _p, out, n = FF.place([add], {later: arrival}, [arrival], _stop_then_swept(T0 + 18.2),
                                  follow_fn=lambda c, t: None)
        assert (n, out) == (0, [add])

    def test_house_settled_names_settle_s_two_placements(self):
        assert set(FF.HOUSE_SETTLED) == {R.REASON_ADD, R.REASON_REMOVE}

    def test_unseen_by_the_overhead_the_house_s_account_stands(self):
        """The camera is not asked: the house saw the throw's stone, which is
        more than a follow from the long camera says."""
        r, calls = _release(color="yellow"), []
        add = self._house_add(r)
        _a, _p, out, n = FF.place([add], {}, [], [], follow_fn=lambda c, t: calls.append(t))
        assert (n, out, calls) == (0, [add], [])

    def _at_the_back_edge(self):
        ys = [4.6 - 0.1 * k for k in range(66)]
        return _track_frames("red", T0 + 18.2, ys + [ys[-1]] * 20, x=-0.68)

    def test_a_stop_at_the_back_edge_of_the_view_ran_through_unless_the_camera_saw_it_rest(self):
        """Sunday Skips 09/27 S2 end 6 rock 6 ran out of the overhead's view
        at the back line, where it looked still at (-0.68, -1.97)."""
        r = _release()
        _a, _p, out, n = FF.place([R.as_delivery(r)], {}, [], self._at_the_back_edge(),
                                  follow_fn=lambda c, t: FF.Outcome(FF.THROUGH, T0 + 25, -0.7, -2.5, 60))
        assert (n, out[0].reason) == (1, R.REASON_THROUGH)
        _a, _p, out, n = FF.place([R.as_delivery(r)], {}, [], self._at_the_back_edge(),
                                  follow_fn=lambda c, t: FF.Outcome(FF.REST, T0 + 28, -0.7, -1.8, 60))
        assert (n, out[0].reason) == (1, R.REASON_REST)

    def _barely_moved(self):
        """Knock the Dust Off 10/17 D1 S1 end 4 rock 3 (f_1A7j): 7 cm and
        still, at the far edge of the overhead's view."""
        ys = [5.51 - 0.01 * k for k in range(7)] + [5.44] * 6
        return _track_frames("yellow", T0 + 18.3, ys, x=-0.38)

    def test_a_stop_the_overhead_saw_come_barely_any_way_is_not_the_throw_the_camera_saw_hogged(self):
        r, calls = _release(color="yellow"), []

        def follow(color, t):
            calls.append(t)
            return FF.Outcome(FF.HOG, T0 + 25.8, -0.24, 7.11, 80)
        _a, _p, out, n = FF.place([R.as_delivery(r)], {}, [], self._barely_moved(), follow_fn=follow)
        assert (n, out[0].reason, calls) == (0, R.REASON, [T0])

    def test_nor_one_the_camera_could_not_follow(self):
        r = _release(color="yellow")
        _a, _p, out, n = FF.place([R.as_delivery(r)], {}, [], self._barely_moved(),
                                  follow_fn=lambda c, t: None)
        assert (n, out[0].reason) == (0, R.REASON)

    def test_but_one_the_camera_saw_stop_too_rests_where_the_overhead_saw_it(self):
        r = _release(color="yellow")
        follow = lambda c, t: FF.Outcome(FF.REST, T0 + 26, -0.3, 5.9, 80)
        _a, _p, out, n = FF.place([R.as_delivery(r)], {}, [], self._barely_moved(), follow_fn=follow)
        assert (n, out[0].reason) == (1, R.REASON_REST)
        assert (out[0].rest_x_m, out[0].rest_y_m) == pytest.approx((-0.38, 5.44))

    def test_a_house_placement_stands_against_a_stop_that_barely_moved(self):
        r = _release(color="yellow")
        add = self._house_add(r)
        _a, _p, out, n = FF.place([add], {}, [], self._barely_moved(), follow_fn=lambda c, t: None)
        assert (n, out) == (0, [add])

    def test_a_late_arrival_that_ran_on_is_the_hog_pushed_down_the_sheet(self):
        """Sunday Skips 09/27 S2 end 3 rock 15: paired with a gap-search arrival
        21.4 s on that ran through; the user saw a hog pushed down."""
        r, pushed = self._late_pair()
        arrivals, pairs, out, n = FF.place([], {r: pushed}, [pushed], [],
                                           follow_fn=lambda c, t: None)
        assert (n, arrivals, pairs) == (1, [], {})
        assert out[0].reason == R.REASON and out[0].release is r

    def test_a_hog_taken_back_from_its_arrival_was_matched(self):
        """Wednesday Womens 10/14 S2 game 2 end 1 rock 1: the fit has to know
        this hog was seen matched, or anything unreleased outranks it."""
        r, pushed = self._late_pair()
        before = {r: pushed}
        _a, _p, out, _n = FF.place([], before, [pushed], [], follow_fn=lambda c, t: None)
        assert FF.taken_back(out, before) == out

    def test_a_release_never_matched_was_not_taken_back(self):
        _a, _p, out, _n = FF.place([R.as_delivery(_release())], {}, [], [],
                                   follow_fn=lambda c, t: None)
        assert out[0].reason == R.REASON and FF.taken_back(out, {}) == []

    def test_unless_the_camera_saw_the_throw_itself_arrive(self):
        r, pushed = self._late_pair()
        follow = lambda c, t: FF.Outcome(FF.THROUGH, T0 + 19, 0.2, 0.5, 60)
        arrivals, pairs, out, n = FF.place([], {r: pushed}, [pushed], [], follow_fn=follow)
        assert (n, arrivals, pairs, out) == (0, [pushed], {r: pushed}, [])

    def test_a_late_arrival_that_struck_the_house_is_a_real_throw(self):
        """Sunday Skips 09/27 S2 end 3 rock 16: in at draw weight 19.6 s after
        its release, it took stones out. A pushed hog changes nothing."""
        r = _release()
        hit = D.Delivery(color="red", t_enter=T0 + 19.6, t_rest=T0 + 32.0, entry_y_m=4.55,
                         rest_x_m=0.4, rest_y_m=-2.0, travel_m=6.1, came_to_rest=False,
                         reason="house-remove")
        calls = []
        _a, pairs, out, n = FF.place([], {r: hit}, [hit], [], follow_fn=lambda c, t: calls.append(t))
        assert (n, pairs, out, calls) == (0, {r: hit}, [], [])

    def _push_past_the_window(self, **kw):
        """Friday Evening 10/09 S4 end 2 rock 12: stopped on the far hog line,
        then pushed into the house 32 s after its release, out of view beside
        the player after 2.5 m."""
        fields = dict(color="red", t_enter=T0 + 31.8, t_rest=T0 + 37.5, entry_y_m=4.215,
                      rest_x_m=1.8, rest_y_m=1.74, travel_m=2.475, came_to_rest=False,
                      reason="left-view")
        fields.update(kw)
        return D.Delivery(**fields)

    def test_an_unpaired_push_past_the_pairing_window_goes_with_its_hog(self):
        r = _release()
        pushed = self._push_past_the_window()
        follow = lambda c, t: FF.Outcome(FF.HOG, T0 + 26, 1.4, 6.4, 80)
        arrivals, pairs, out, n = FF.place([R.as_delivery(r)], {}, [pushed], [],
                                           follow_fn=follow)
        assert (n, arrivals, pairs) == (1, [], {})
        assert out[0].reason == R.REASON and out[0].release is r

    def test_so_does_one_the_camera_could_not_follow(self):
        r = _release()
        arrivals, _p, out, n = FF.place([R.as_delivery(r)], {}, [self._push_past_the_window()], [],
                                        follow_fn=lambda c, t: None)
        assert (n, arrivals, out[0].reason) == (1, [], R.REASON)

    def test_but_not_once_the_camera_placed_the_throw(self):
        r = _release()
        pushed = self._push_past_the_window()
        follow = lambda c, t: FF.Outcome(FF.REST, T0 + 24, -0.6, 4.9, 80)
        arrivals, _p, out, _n = FF.place([R.as_delivery(r)], {}, [pushed], [], follow_fn=follow)
        assert arrivals == [pushed] and out[0].reason == R.REASON_REST

    @pytest.mark.parametrize("kw", [
        dict(color="yellow"),                       # the other team's
        dict(t_enter=T0 + 50.0),                    # later than a hog is moved
        dict(reason="house-remove"),                # it changed the house
        dict(came_to_rest=True, rest_y_m=1.2),      # it stopped in play
        dict(entry_y_m=2.0),                        # first seen inside the house
    ])
    def test_an_unpaired_arrival_that_is_not_that_push_is_kept(self, kw):
        r = _release()
        other = self._push_past_the_window(**kw)
        arrivals, _p, out, n = FF.place([R.as_delivery(r)], {}, [other], [],
                                        follow_fn=lambda c, t: None)
        assert (n, arrivals, out[0].reason) == (0, [other], R.REASON)

    def test_an_arrival_paired_with_another_release_is_not_this_push(self):
        r, mine = _release(), _release(t=T0 + 10.0)
        theirs = self._push_past_the_window()
        arrivals, pairs, out, n = FF.place([R.as_delivery(r)], {mine: theirs}, [theirs], [],
                                           follow_fn=lambda c, t: None)
        assert (n, arrivals, pairs) == (0, [theirs], {mine: theirs})

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
