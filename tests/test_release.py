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

    def test_a_short_climb_is_not(self):
        got = release.find_releases(frames(leaving("red", 100.0, y1=0.0)), VIEW_Y_MIN)
        assert got == []

    def test_two_sightings_inside_the_separation_are_one_throw(self):
        # The stone and a same-coloured broom beside it, both from the edge.
        both = frames(leaving("red", 100.0, x=0.1, y1=4.4), leaving("red", 100.4, x=0.9, y1=1.5))
        got = release.find_releases(both, VIEW_Y_MIN)
        assert len(got) == 1
        assert got[0].y_exit_m > 4.0   # the one followed further


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
