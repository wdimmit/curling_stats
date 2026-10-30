"""Timing a stone across the hog line in a side view: the gates a proposer's
tracks go through. The view sees boots, brooms and the stones already in
play, so most of this is about refusing.
"""

import pytest

from curling_score.detect import longview
from curling_score.geometry.sideview import SideView

VIEW = SideView(rect=(0, 0, 810, 1080), tee_row=430.0, hog_row=520.0)


def track(rows, t0=10.0, step_s=0.1, width_px=52.0):
    """One key's samples, ``(t, edge_row, body_px)``, a sample a ``step_s``."""
    return [(t0 + i * step_s, float(r), width_px) for i, r in enumerate(rows)]


# 3.3 rows per 0.1 s is about 2.2 m/s near the hog line: a delivery.
THROUGH = [VIEW.hog_row - 8.0 + i * 3.3 for i in range(6)]


class TestTheGates:
    """What `crossing_from_tracks` refuses, whichever proposer made the
    track: a mistimed split is worse than a missing one."""

    def test_a_delivery_through_the_line_is_timed(self):
        got = longview.crossing_from_tracks({0: track(THROUGH)}, VIEW,
                                            offset_s=0.0, max_gap_s=None)
        assert got.key == longview.KEY_OK, got.reason
        assert 10.2 < got.t < 10.3

    def test_nothing_proposed_is_no_candidate(self):
        got = longview.crossing_from_tracks({}, VIEW, offset_s=0.0, max_gap_s=None)
        assert not got and got.key == longview.KEY_NO_CANDIDATE

    def test_a_stone_that_stops_short_of_the_line(self):
        short = [r - 30.0 for r in THROUGH]
        got = longview.crossing_from_tracks({0: track(short)}, VIEW,
                                            offset_s=0.0, max_gap_s=None)
        assert not got and got.key == longview.KEY_NEVER_REACHED

    def test_a_stone_drifting_the_wrong_way_is_no_candidate(self):
        got = longview.crossing_from_tracks({0: track(THROUGH[::-1])}, VIEW,
                                            offset_s=0.0, max_gap_s=None)
        assert not got and got.key == longview.KEY_NO_CANDIDATE

    def test_two_stones_crossing_in_the_same_window(self):
        both = {0: track(THROUGH), 5: track(THROUGH, t0=10.05)}
        got = longview.crossing_from_tracks(both, VIEW, offset_s=0.0, max_gap_s=None)
        assert not got and got.key == longview.KEY_AMBIGUOUS

    def test_a_stone_crawling_too_slowly_to_be_a_delivery(self):
        crawl = [VIEW.hog_row - 2.0 + i * 0.8 for i in range(6)]
        got = longview.crossing_from_tracks({0: track(crawl, step_s=0.5)}, VIEW,
                                            offset_s=0.0, max_gap_s=None)
        assert not got and got.key == longview.KEY_BAD_SPEED

    def test_a_jump_back_at_the_line_is_unsteady(self):
        jumpy = list(THROUGH)
        jumpy[2] = jumpy[1] - 5.0
        jumpy[3] = VIEW.hog_row + 1.0
        got = longview.crossing_from_tracks({0: track(jumpy)}, VIEW,
                                            offset_s=0.0, max_gap_s=None)
        assert not got and got.key == longview.KEY_UNSTEADY

    def test_the_window_covers_every_crossing_that_was_marked(self):
        """Marked by hand on 27 deliveries: release + 2.83 s to + 5.43 s."""
        assert longview.WINDOW_S[0] <= 2.83
        assert longview.WINDOW_S[1] >= 5.43


class TestOffsetBelongsToTheProposer:
    """``offset_s`` corrects where a proposer puts a stone's trailing edge
    against where a person judges its leading edge to touch the paint."""

    def _track(self, hog_row):
        # 3.3 rows per 0.1 s is about 33 rows a second, which near the hog line
        # is roughly 2.2 m/s -- mid-range for SPEED_BOUNDS_M_S. The first
        # version of this fixture moved 10 rows a sample, which is over the
        # bound, so `crossing_from_tracks` refused both calls and the test
        # compared None with None and passed while proving nothing.
        return {0: [(10.0 + i * 0.1, hog_row - 8.0 + i * 3.3, 52.0)
                    for i in range(6)]}

    def test_a_proposer_can_supply_its_own(self):
        view = SideView(rect=(0, 0, 810, 1080), tee_row=435.0, hog_row=520.0)
        base = longview.crossing_from_tracks(self._track(520.0), view,
                                             offset_s=0.0, max_gap_s=None)
        moved = longview.crossing_from_tracks(self._track(520.0), view,
                                              offset_s=0.25, max_gap_s=None)
        assert base.t is not None and moved.t is not None, base.reason
        assert moved.t - base.t == pytest.approx(0.25)

class TestABigWeightHit:
    """10/01 Mens sheet 2, 9 pm game, end 4, rock 16, flagged "weight not
    tracked": the long camera followed it across the hog line and refused it
    at 3.69 m/s, over a bound set from 27 hand marks that were nearly all
    draws. Of 35 hosted throws released at 3 m/s or more, 6 were refused this
    way (3.20-3.67 m/s), and 3 more crossed 1.77-1.86 s after their release,
    before the window opened."""

    VIEW = SideView(rect=(0, 0, 810, 1080), tee_row=435.0, hog_row=520.0)

    def _track(self, rows_per_sample):
        return {0: [(10.0 + i * 0.1, 520.0 - 8.0 + i * rows_per_sample, 52.0)
                    for i in range(6)]}

    def _speed(self, track):
        pts = track[0]
        return (abs(self.VIEW.metres_at(pts[-1][1]) - self.VIEW.metres_at(pts[0][1]))
                / (pts[-1][0] - pts[0][0]))

    def test_a_hit_at_rock_16s_speed_is_timed(self):
        track = self._track(6.0)
        assert 3.5 < self._speed(track) < 4.0          # the fixture is what it says
        got = longview.crossing_from_tracks(track, self.VIEW, offset_s=0.0, max_gap_s=None)
        assert got.key == longview.KEY_OK, got.reason

    def test_faster_than_any_delivery_is_still_refused(self):
        track = self._track(9.0)
        assert self._speed(track) > longview.SPEED_BOUNDS_M_S[1]
        got = longview.crossing_from_tracks(track, self.VIEW, offset_s=0.0, max_gap_s=None)
        assert got.key == longview.KEY_BAD_SPEED

    def test_the_bound_and_the_window(self):
        assert longview.SPEED_BOUNDS_M_S == (1.2, 5.0)
        assert longview.WINDOW_S == (1.0, 6.5)


class TestATrackSplitAtABinBoundary:
    """AEqL game 2, end 3, rock 3: the stone's column crossed 480 -- a 120-px
    key boundary -- exactly on the hog row, so its track came apart there and
    neither half straddled the line. It was timed "never reached" and the
    shot lost its split."""

    def _halves(self):
        pts = [(10.0 + i * 0.1, 504.0 + i * 3.3, 52.0) for i in range(12)]
        return {3: [p for p in pts if p[1] < 520.0], 4: [p for p in pts if p[1] >= 520.0]}

    def test_the_halves_are_joined_and_timed(self):
        got = longview.crossing_from_tracks(self._halves(), VIEW, offset_s=0.0, max_gap_s=None)
        assert got.key == longview.KEY_OK, got.reason
        assert got.t == pytest.approx(10.0 + 0.4 + (520.0 - 517.2) / 3.3 * 0.1, abs=1e-6)
        assert got.track_key == 3

    def test_the_later_half_alone_still_never_reaches(self):
        got = longview.crossing_from_tracks({4: self._halves()[4]}, VIEW, offset_s=0.0, max_gap_s=None)
        assert got.key == longview.KEY_NEVER_REACHED

    def test_a_crossing_within_one_key_is_timed_from_it_alone(self):
        one = {3: [(10.0 + i * 0.1, 504.0 + i * 3.3, 52.0) for i in range(12)]}
        got = longview.crossing_from_tracks(one, VIEW, offset_s=0.0, max_gap_s=None)
        assert got.key == longview.KEY_OK and got.track_key == 3

    def test_entries_may_carry_a_column_after_the_width(self):
        one = {3: [(10.0 + i * 0.1, 504.0 + i * 3.3, 52.0, 300.0 + i) for i in range(12)]}
        assert longview.crossing_from_tracks(one, VIEW, offset_s=0.0, max_gap_s=None).key == longview.KEY_OK


class TestAStraySampleIsNotACrossing:
    """S1 10/06 game 1, end 7, rock 15: the stone left key 3 for key 4 at row
    564, 21 rows short of the line, and crossed in key 4. Key 3 then picked up
    one stray red box past the line 2.6 s later, so its first and last rows
    straddled the line too and the throw was refused as two crossers.

    A crossing is a pair of samples either side of the line, close in time --
    for a proposer that sees the stone every frame. ``max_gap_s`` says how
    close."""

    GAP = 0.5           # the side model's, sidemodel.STRADDLE_GAP_MAX_S

    def _stone(self, t0, t1, t_cross=10.6, rows_per_s=40.0):
        n = int(round((t1 - t0) * 30))
        return [(t0 + i / 30, 520.0 + (t0 + i / 30 - t_cross) * rows_per_s, 52.0)
                for i in range(n + 1)]

    def _tracks(self):
        return {3: self._stone(10.0, 10.5) + [(13.1, 560.0, 52.0)],
                4: self._stone(10.533, 11.2)}

    def test_the_stone_is_timed_in_the_key_it_crossed_in(self):
        got = longview.crossing_from_tracks(self._tracks(), VIEW, offset_s=0.0,
                                            max_gap_s=self.GAP)
        assert got.key == longview.KEY_OK, got.reason
        assert got.track_key == 4
        assert got.t == pytest.approx(10.6, abs=0.01)

    def test_a_straddle_across_a_long_gap_alone_never_reaches_the_line(self):
        got = longview.crossing_from_tracks({3: self._tracks()[3]}, VIEW, offset_s=0.0,
                                            max_gap_s=self.GAP)
        assert got.key == longview.KEY_NEVER_REACHED

    def test_a_few_frames_lost_at_the_line_are_still_a_crossing(self):
        """The thrower's body hides the stone for a moment: the pair either
        side of the line is a few frames apart, not seconds."""
        hidden = [p for p in self._stone(10.0, 11.2) if not 10.5 < p[0] < 10.75]
        pair = [(a, b) for a, b in zip(hidden, hidden[1:]) if a[1] <= 520.0 <= b[1]][0]
        assert 0.2 < pair[1][0] - pair[0][0] <= self.GAP
        got = longview.crossing_from_tracks({3: hidden}, VIEW, offset_s=0.0,
                                            max_gap_s=self.GAP)
        assert got.key == longview.KEY_OK, got.reason
        assert got.t == pytest.approx(10.6, abs=0.01)

    def test_two_stones_both_crossing_are_still_ambiguous(self):
        both = {3: self._stone(10.0, 11.2), 5: self._stone(10.05, 11.25, t_cross=10.65)}
        got = longview.crossing_from_tracks(both, VIEW, offset_s=0.0, max_gap_s=self.GAP)
        assert got.key == longview.KEY_AMBIGUOUS

    def test_no_bound_takes_any_gap(self):
        """``max_gap_s=None``: the stray still counts, and a lone gapped
        straddle is interpolated."""
        assert longview.crossing_from_tracks(
            self._tracks(), VIEW, offset_s=0.0, max_gap_s=None).key == longview.KEY_AMBIGUOUS
        assert longview.crossing_from_tracks(
            {3: self._tracks()[3]}, VIEW, offset_s=0.0,
            max_gap_s=None).key == longview.KEY_OK
