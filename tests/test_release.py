"""Deliveries seen leaving the thrower's house, and the hogged rocks among them.

Measured on the club's feed: a release enters the delivery-end panel within
0.1 m of the back edge of the view, climbs at 1.5-2.1 m/s and is followed
4.5-6.7 m before the sweepers close over it; the arrival in the far house
comes 18-19 s later on the two deliveries timed, though the club says 6 s is
possible and 10-15 s usual.
"""

import pytest

from curling_score.detect import release
from curling_score.detect.delivery import Delivery
from curling_score.detect.rocks import Detection

VIEW_Y_MIN = -2.25
# The club's bottom panel. Its centre-line bound is 1.85 * 0.35 = 0.647 m.
VIEW_X_LIMIT = 1.85


def det(color, x, y):
    return Detection(color=color, x_m=x, y_m=y, x_px=0.0, y_px=0.0,
                     area_px=140.0, confidence=0.9)


def leaving(color, t0, y0=-2.2, y1=2.4, speed=2.0, fps=5.0, x=0.1):
    """A stone crossing the delivery-end panel up-sheet."""
    out, t, y = [], t0, y0
    while y < y1:
        out.append((round(t, 3), det(color, x, y)))
        y += speed / fps
        t += 1.0 / fps
    return out


def frames(*traces):
    by_t = {}
    for tr in traces:
        for t, d in tr:
            by_t.setdefault(t, []).append(d)
    return [(t, by_t[t]) for t in sorted(by_t)]


def arrival(color, t):
    return Delivery(color=color, t_enter=t, t_rest=t + 8.0, entry_y_m=4.5,
                    rest_x_m=0.0, rest_y_m=0.0, travel_m=4.5)


def track(color, samples):
    """A `_Track` from `(t, x, y)` samples, as `_build_tracks` would build it."""
    from curling_score.detect import delivery as D

    (t0, x0, y0), *rest = samples
    tk = D._Track(color, t0, x0, y0)
    for t, x, y in rest:
        tk.add(t, x, y)
    return tk


class TestMergingFragments:
    """One stone the detector split in two is one track, not two.

    These drive `_merge_fragments` directly rather than through
    `find_releases`, and that is deliberate. Coaxing `_build_tracks` into
    splitting a synthetic trace is not reliable -- it re-acquires across a
    0.4 s gap and a 1.4 m jump, so a trace built to look fragmented comes
    back already joined and the test passes whether the merge exists or
    not. The samples below are e1 s1's real ones.
    """

    def test_two_fragments_of_one_stone_merge(self):
        low = track("red", [(17.0, 0.13, -1.88), (17.2, 0.11, -1.39),
                            (17.4, 0.13, -1.19)])
        high = track("red", [(17.4, 0.11, -0.87), (17.6, 0.10, -0.30),
                             (17.8, 0.12, 0.05), (18.2, 0.08, 1.33),
                             (18.4, 0.08, 1.86)])
        (merged,) = release._merge_fragments([low, high])
        assert merged.ys[0] == -1.88
        assert merged.ys[-1] == 1.86
        assert len(merged.ts) == 7          # 3 + 5, less the clash at 17.4

    def test_the_better_sampled_box_wins_a_collision(self):
        # Both fragments hold a sample at 17.4 and disagree: -0.87 against
        # -1.19. Interpolating the stone's neighbours puts it at -0.85.
        low = track("red", [(17.0, 0.13, -1.88), (17.2, 0.11, -1.39),
                            (17.4, 0.13, -1.19)])
        high = track("red", [(17.4, 0.11, -0.87), (17.6, 0.10, -0.30),
                             (17.8, 0.12, 0.05), (18.2, 0.08, 1.33),
                             (18.4, 0.08, 1.86)])
        (merged,) = release._merge_fragments([low, high])
        assert -0.87 in merged.ys
        assert -1.19 not in merged.ys

    def test_tracks_far_apart_laterally_do_not_merge(self):
        # e6 s6: 0.60 m apart and overlapping in time, genuinely two objects.
        a = track("red", [(4566.3, 0.00, -2.07), (4566.9, 0.00, -0.50),
                          (4568.1, 0.00, 1.82)])
        b = track("red", [(4566.9, -0.60, -1.14), (4568.9, -0.60, 2.97)])
        assert len(release._merge_fragments([a, b])) == 2

    def test_tracks_with_a_gap_between_them_do_not_merge(self):
        a = track("red", [(100.0, 0.05, -2.0), (100.4, 0.05, -1.0),
                          (100.8, 0.05, 0.0)])
        b = track("red", [(400.0, 0.05, -2.0), (400.4, 0.05, -1.0),
                          (400.8, 0.05, 0.0)])
        assert len(release._merge_fragments([a, b])) == 2

    def test_two_different_objects_do_not_merge(self):
        # 0.6 m apart laterally is e6 s6: a real delivery and something else
        # crossing beside it. Merging those would invent a track neither had.
        real = leaving("red", 100.0, y0=-2.0, y1=2.4, x=0.00)
        other = leaving("red", 100.2, y0=-1.1, y1=1.0, x=0.60)
        got = release.find_releases(frames(real, other), VIEW_Y_MIN)
        assert len(got) == 1
        assert len(got[0].track) == len(real)

    def test_a_gap_in_time_is_not_a_fragment(self):
        # Two separate throws on the centre line, minutes apart. Same stone
        # colour, same lane, and nothing to do with each other.
        first = leaving("red", 100.0, y0=-2.0, y1=2.4, x=0.05)
        second = leaving("red", 400.0, y0=-2.0, y1=2.4, x=0.05)
        got = release.find_releases(frames(first, second), VIEW_Y_MIN)
        assert len(got) == 2

    def test_a_long_interferer_cannot_erase_a_short_delivery(self):
        """The failure this guard exists for.

        `_join` gives every collision to the longer track, and both tracks sit
        on the same 5 fps grid, so without a y-agreement test a long blob in
        the same lane takes every instant and the delivery's own samples are
        gone -- the merged track is flat, fails the stage-1 line, and the throw
        is lost. Before merging existed the delivery survived on its own.
        """
        delivery = track("red", [(100.0, 0.25, -2.0), (100.2, 0.25, -1.25),
                                 (100.4, 0.25, -0.5), (100.6, 0.25, 0.25),
                                 (100.8, 0.25, 1.0)])
        blob = track("red", [(100.0 + i * 0.2, 0.05, -1.10) for i in range(15)])
        out = release._merge_fragments([delivery, blob])
        assert len(out) == 2, "two objects in one lane are not one stone"
        assert any(tr.ys[-1] == 1.0 for tr in out), "the delivery must survive"

    def test_no_merged_track_implies_an_impossible_speed(self):
        """A sweeper acquired mid-delivery must not be grafted on.

        The graft reads 3.4 m in 0.2 s, passes every gate, and leaves
        `y_exit_m` describing where the sweeper went rather than the stone.
        """
        delivery = track("red", [(100.0 + i * 0.2, 0.25, -2.0 + i * 0.44)
                                 for i in range(8)])
        sweeper = track("red", [(101.4 + i * 0.2, 0.45, 4.5 + i * 0.1)
                                for i in range(6)])
        for tr in release._merge_fragments([delivery, sweeper]):
            steps = [(b - a) / (tb - ta) for (ta, a), (tb, b)
                     in zip(zip(tr.ts, tr.ys), zip(tr.ts[1:], tr.ys[1:]))
                     if tb > ta]
            assert all(abs(v) <= release.MAX_SPEED_M_S for v in steps), (
                "a merged track must stay physically possible")


class TestFindingReleases:
    def test_a_stone_climbing_from_the_back_edge_is_a_release(self):
        got = release.find_releases(frames(leaving("red", 100.0)), VIEW_Y_MIN)
        assert len(got) == 1
        assert got[0].color == "red"
        assert got[0].t == pytest.approx(100.0)
        assert 1.5 < got[0].speed_m_s < 2.5

    def test_a_sweeper_starting_mid_panel_is_not(self):
        # Game 3 end 2: a red fragment climbing beside the yellow release from
        # y = +0.70 at 2 m/s. Never came from the hack.
        got = release.find_releases(frames(leaving("red", 100.0, y0=0.7, y1=2.8)), VIEW_Y_MIN)
        assert got == []

    def test_a_stone_drifting_up_slowly_is_not(self):
        # Pushed back toward the hack between ends.
        got = release.find_releases(frames(leaving("red", 100.0, speed=0.4)), VIEW_Y_MIN)
        assert got == []

    def test_a_climb_that_stops_just_past_the_stage1_line_is_a_release(self):
        # The 63 -> 88 change. This trace's last sample is y = -0.2, a foot
        # BEHIND the tee -- past STAGE1_Y_M but well short of the T-line.
        # MIN_TRAVEL_M used to demand three metres of climb, which finishes
        # about a metre PAST the T-line, and on 30% of throws the sweepers
        # close over the stone before it gets there. Measured on
        # AEqLTgM25Tc: the rocks with no release have a median top-of-track of
        # y = 1.02 m, against 3.48 m for the rocks that do produce one.
        got = release.find_releases(frames(leaving("red", 100.0, y1=0.0)),
                                    VIEW_Y_MIN)
        assert len(got) == 1

    def test_a_climb_that_stops_short_of_the_line_is_not(self):
        got = release.find_releases(frames(leaving("red", 100.0, y1=-0.8)),
                                    VIEW_Y_MIN)
        assert got == []

    def test_two_sightings_inside_the_separation_are_one_throw(self):
        # The stone and a same-coloured broom beside it, both from the edge.
        both = frames(leaving("red", 100.0, x=0.1, y1=4.4), leaving("red", 100.4, x=0.9, y1=1.5))
        got = release.find_releases(both, VIEW_Y_MIN)
        assert len(got) == 1
        assert got[0].y_exit_m > 4.0   # kept because it carries more samples,
                                       # not because it went further


class TestTheCentreLineBound:
    """`CENTRE_FRACTION`: a thrower starts in the hack, on the centre line.

    Measured over 88 deliveries on AEqLTgM25Tc the widest ran |x| = 0.32 m,
    against a median of 1.10 m for everything else the panel offers.
    """

    def test_a_delivery_on_the_centre_line_survives_the_bound(self):
        got = release.find_releases(frames(leaving("red", 100.0, x=0.32)),
                                    VIEW_Y_MIN, VIEW_X_LIMIT)
        assert len(got) == 1, "the widest real delivery measured must still pass"

    def test_a_stone_parked_at_the_edge_is_refused(self):
        # Spare rocks sit at the sides of the throwing view between ends; on
        # AEqLTgM25Tc end 6 they read at x = 1.45 and -1.58.
        got = release.find_releases(frames(leaving("red", 100.0, x=1.45)),
                                    VIEW_Y_MIN, VIEW_X_LIMIT)
        assert got == []

    def test_the_thrower_sliding_up_sheet_is_refused(self):
        # The failure 0.50 was not tight enough for: boxes on the delivering
        # player's arm and shoulder at x = 0.78-0.85. See datasets/ds11/hardneg.
        got = release.find_releases(frames(leaving("red", 100.0, x=0.82)),
                                    VIEW_Y_MIN, VIEW_X_LIMIT)
        assert got == [], "0.82 m is inside a 0.50 bound and must be outside this one"

    def test_a_wide_box_cannot_extend_a_good_track(self):
        """The reason the bound filters detections rather than whole tracks.

        A real delivery, and boxes on the thrower continuing up-sheet after the
        stone has gone. `_build_tracks` joins the two, so judging the track
        afterwards would judge one that already has the bad samples in it.
        """
        contaminated = frames(leaving("red", 100.0, y0=-2.2, y1=1.5, x=0.25),
                              leaving("red", 101.85, y0=1.5, y1=3.4, x=0.82))
        loose = release.find_releases(contaminated, VIEW_Y_MIN)
        bounded = release.find_releases(contaminated, VIEW_Y_MIN, VIEW_X_LIMIT)
        assert len(loose) == 1 and len(bounded) == 1
        assert loose[0].y_exit_m > 3.0, "unbounded, the track runs on past the stone"
        assert bounded[0].y_exit_m < 2.0, "bounded, it stops where the stone did"

    def test_no_limit_means_no_bound(self):
        # Every pre-existing caller passes nothing, and must be unaffected.
        wide = frames(leaving("red", 100.0, x=1.45))
        assert release.find_releases(wide, VIEW_Y_MIN) != []
        assert release.on_centre_line(99.0, None) is True

    def test_find_and_pair_threads_the_limit_through(self):
        fs = frames(leaving("red", 100.0, x=1.45))
        rels, _matched, _un = release.find_and_pair(
            fs, VIEW_Y_MIN, [arrival("red", 118.0)], view_x_limit_m=VIEW_X_LIMIT)
        assert rels == [], "the bound must reach find_releases through find_and_pair"


class TestTheStage1Line:
    """A throw's first event: the stone leaves the hack and crosses the line."""

    def test_three_samples_are_enough(self):
        # e2 s12 on AEqLTgM25Tc is a real delivery the panel caught exactly
        # three times -- y -2.16 -> +0.57 at 2.73 m/s on the centre line. A
        # minimum of four discards it and costs the 88th rock.
        got = release.find_releases(
            frames(leaving("red", 100.0, y0=-1.0, y1=0.0)), VIEW_Y_MIN)
        assert len(got) == 1
        assert len(got[0].track) == 3

    def test_two_samples_are_not(self):
        got = release.find_releases(
            frames(leaving("red", 100.0, y0=-0.6, y1=0.0)), VIEW_Y_MIN)
        assert got == []

    def test_a_stone_crossing_faster_than_any_delivery_is_not_one(self):
        # 4.8 m/s across the line. MIN_SPEED and MAX_SPEED stay exactly as
        # they were: among tracks acquired above the T-line, deliveries run
        # 1.47-2.15 m/s and everything else 0.12-0.61, a gap with nothing in
        # it. They are discriminators, not fitted thresholds.
        got = release.find_releases(
            frames(leaving("red", 100.0, speed=4.8)), VIEW_Y_MIN)
        assert got == []

    def test_a_panel_that_cannot_see_behind_the_tee_is_an_error(self):
        # Stage 1 is unmeasurable on a crop that does not reach the hack, and
        # silently returning nothing would look like a game with no throws.
        with pytest.raises(ValueError, match="stage-1 line"):
            release.find_releases(frames(leaving("red", 100.0)),
                                  view_y_min_m=0.5)

    def test_the_better_sampled_rival_wins_not_the_one_followed_furthest(self):
        # e6 s6 on AEqLTgM25Tc: the real delivery carries eight samples and
        # reaches y = +1.82; the thing crossing 0.60 m beside it carries two
        # and reaches +2.97. Keeping the larger y_exit_m picks the wrong one.
        real = leaving("red", 100.0, y0=-2.0, y1=1.9, x=0.0)    # 10 samples
        rival = leaving("red", 100.2, y0=-0.6, y1=2.0, x=0.6)   # 7, but higher
        assert len(real) > len(rival)
        got = release.find_releases(frames(real, rival), VIEW_Y_MIN)
        assert len(got) == 1
        assert len(got[0].track) == len(real)
        assert got[0].y_exit_m < 1.7        # the real one does not reach as far


class TestPairing:
    def _rel(self, color, t):
        return release.Release(color, t, 4.0, 2.0)

    def test_an_arrival_inside_the_window_matches(self):
        m, u = release.pair([self._rel("red", 100.0)], [arrival("red", 118.5)])
        assert len(m) == 1 and u == []

    def test_the_fastest_plausible_lag_is_six_seconds(self):
        m, u = release.pair([self._rel("red", 100.0)], [arrival("red", 106.0)])
        assert len(m) == 1
        m, u = release.pair([self._rel("red", 100.0)], [arrival("red", 104.0)])
        assert u and not m

    def test_an_arrival_too_late_is_a_different_rock(self):
        m, u = release.pair([self._rel("red", 100.0)], [arrival("red", 135.0)])
        assert u and not m

    def test_the_slowest_arrival_measured_is_inside_the_window(self):
        # 24 s from the start of the slide, game 4 end 4's first red.
        m, u = release.pair([self._rel("red", 3451.6)], [arrival("red", 3475.4)])
        assert len(m) == 1

    def test_colours_are_never_matched_across(self):
        m, u = release.pair([self._rel("red", 100.0)], [arrival("yellow", 115.0)])
        assert u and not m

    def test_each_arrival_answers_for_one_release(self):
        rels = [self._rel("red", 100.0), self._rel("red", 112.0)]
        m, u = release.pair(rels, [arrival("red", 118.0)])
        assert len(m) == 1 and len(u) == 1
        assert u[0].t == 112.0

    def test_the_hogged_first_rock_of_game_3_end_2(self):
        # Releases at 1109 (red), 1162 (yellow), 1208 (red); arrivals at 1180
        # (yellow) and 1226 (red). The red at 1109 is the hogged one.
        rels = [self._rel("red", 1109.3), self._rel("yellow", 1162.4), self._rel("red", 1208.0)]
        arr = [arrival("yellow", 1180.1), arrival("red", 1226.5)]
        hogged = release.hogged(rels, arr)
        assert [(d.color, round(d.t_enter)) for d in hogged] == [("red", 1109)]


class TestAHoggedRockAsADelivery:
    def test_it_is_timed_at_the_release_and_never_at_rest(self):
        d = release.as_delivery(release.Release("red", 1109.3, 4.0, 2.0))
        assert d.t_enter == 1109.3
        assert d.came_to_rest is False
        assert d.reason == "hogged"
        assert d.rest_y_m > 6.0     # beyond the hog line: not on the sheet we read

    def test_the_classifier_calls_it_hogged(self):
        from curling_score.game import classify

        d = release.as_delivery(release.Release("red", 1109.3, 4.0, 2.0))
        assert classify.classify(d, {"added": [], "removed": [], "moved": []})[0] == "hogged"


class TestWhatBecameOfAnUnseenArrival:
    """A release with no arrival is settled by what the far house did."""

    def _rel(self, color="red", t=100.0):
        return release.Release(color, t, 4.5, 2.0)

    def _house(self, before, after, t=100.0):
        out = []
        for k in range(0, 60):
            tt = t - 6.0 + k * 0.1
            out.append((round(tt, 2), [det(c, x, y) for c, x, y in before]))
        for k in range(0, 80):
            tt = t + release.SETTLE_S + k * 0.1
            out.append((round(tt, 2), [det(c, x, y) for c, x, y in after]))
        return out

    def test_nothing_changed_means_hogged(self):
        got = release.settle(self._rel(), self._house([("yellow", 0.3, 1.0)], [("yellow", 0.3, 1.0)]))
        assert got.reason == "hogged" and got.came_to_rest is False

    def test_a_stone_of_its_colour_appearing_means_it_arrived_unseen(self):
        got = release.settle(self._rel(), self._house([("yellow", 0.3, 1.0)],
                                                      [("yellow", 0.3, 1.0), ("red", -0.4, 0.6)]))
        assert got.reason == "release-add" and got.came_to_rest is True
        assert (got.rest_x_m, got.rest_y_m) == pytest.approx((-0.4, 0.6), abs=0.05)

    def test_a_stone_vanishing_means_it_hit_and_ran_through(self):
        from curling_score.game import classify

        got = release.settle(self._rel(), self._house([("yellow", 0.3, 1.0)], []))
        assert got.reason == "release-remove" and got.came_to_rest is False
        assert classify.classify(got, {"removed": [{"color": "yellow"}]})[0] == "hit"

    def test_with_no_house_to_read_it_is_taken_as_hogged(self):
        got = release.unaccounted([self._rel()], [], frames=())
        assert [d.reason for d in got] == ["hogged"]


class TestAMisreadHandle:
    def test_an_other_coloured_arrival_nobody_else_claims_explains_the_release(self):
        # Game 4 end 2: a "red" release at 1616, a yellow arriving at 1635 with
        # no yellow release seen, and no red arriving at all. One throw.
        rels = [release.Release("red", 1616.0, 3.7, 1.6)]
        got = release.unaccounted(rels, [arrival("yellow", 1635.0)])
        assert got == []

    def test_but_an_arrival_already_paired_does_not(self):
        # The yellow's own release was seen and paired; the red at 1616 is on
        # its own and stands.
        rels = [release.Release("yellow", 1617.0, 4.4, 1.6), release.Release("red", 1616.0, 3.7, 1.6)]
        got = release.unaccounted(rels, [arrival("yellow", 1635.0)])
        assert [d.color for d in got] == ["red"]


class TestAReleaseNeverOutranksAnArrival:
    def test_every_release_reason_weighs_less_than_any_arrival_route(self):
        from curling_score.game import fit

        arrivals = [w for k, w in fit.WEIGHT_BY_REASON.items() if not k.startswith("release") and k != "hogged"]
        for k in ("release-add", "release-remove", "hogged"):
            assert fit.WEIGHT_BY_REASON[k] < min(arrivals)


# --- the release carries its own track, and pairing is computed once ---------


def test_a_release_keeps_the_track_it_was_found_from():
    """Without the track there is nothing to time a line crossing against."""
    fs = frames(leaving("red", 100.0))
    [r] = release.find_releases(fs, VIEW_Y_MIN)
    assert r.track, "the release dropped the track it was built from"
    ts = [t for t, _x, _y in r.track]
    ys = [y for _t, _x, y in r.track]
    assert ts == sorted(ts)
    assert ts[0] == r.t
    assert ys[0] < release.STAGE1_Y_M
    assert ys[-1] == pytest.approx(r.y_exit_m)


def test_the_track_spans_the_climb_across_the_line():
    """From below the stage-1 line to at or above it.

    It used to assert a climb of at least MIN_TRAVEL_M. There is no such
    minimum now: a release is a line crossing, and how far the stone then
    ran is the hog line's business, not this panel's.
    """
    fs = frames(leaving("yellow", 50.0, y0=-2.2, y1=2.4, speed=2.0, fps=5.0))
    [r] = release.find_releases(fs, VIEW_Y_MIN)
    ys = [y for _t, _x, y in r.track]
    assert ys[0] < release.STAGE1_Y_M <= ys[-1]


def test_find_and_pair_agrees_with_the_two_calls_it_replaces():
    fs = frames(leaving("red", 100.0), leaving("yellow", 200.0))
    ds = [arrival("red", 115.0)]
    releases, matched, un = release.find_and_pair(fs, VIEW_Y_MIN, ds)

    assert releases == release.find_releases(fs, VIEW_Y_MIN)
    want_matched, want_un = release.pair(releases, ds)
    assert matched == want_matched
    assert [u.color for u in un] == [u.color for u in release.unaccounted(releases, ds)]


def test_unaccounted_accepts_a_pairing_rather_than_recomputing_it():
    fs = frames(leaving("red", 100.0))
    ds = []
    releases = release.find_releases(fs, VIEW_Y_MIN)
    pairing = release.pair(releases, ds)
    assert (
        [d.reason for d in release.unaccounted(releases, ds, pairing=pairing)]
        == [d.reason for d in release.unaccounted(releases, ds)]
    )


def test_a_matched_release_is_reported_alongside_its_arrival():
    """The pairing is the whole point: it is what carries a release onto a shot."""
    fs = frames(leaving("red", 100.0))
    a = arrival("red", 115.0)
    _releases, matched, un = release.find_and_pair(fs, VIEW_Y_MIN, [a])
    assert un == []
    assert list(matched.values()) == [a]
    assert list(matched)[0].t == pytest.approx(100.0)
