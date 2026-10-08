"""Timing a stone across the hog line in a side view.

The overhead panel loses about 40% of throws before the hog line, under the
thrower and sweepers. From the end of the sheet the sweepers are beside the
stone rather than over it, so this view sees crossings the panel cannot --
but it also sees their boots, their brooms and the stones already in play,
so most of this module is about refusing.
"""

import numpy as np
import pytest

from curling_score.detect import longview
from curling_score.geometry import constants as C, sideview
from tests import synth

VIEW = sideview.SideView(rect=(0, 0, 810, 1080), tee_row=430.0, hog_row=520.0)


def travelling(speed_m_s=2.0, t_cross=0.5, span_s=1.0, fps=30.0, color="red",
               width_px=52, x=None, view=VIEW):
    """A stone crossing the hog line at ``speed_m_s``, timed to cross at
    ``t_cross``.

    Rows come from ``view.row_for`` rather than from a hand-picked pixel range,
    so a fixture cannot drift out of the speed gate's range without the test
    saying which bound it broke.
    """
    times = [i / fps for i in range(int(span_s * fps) + 1)]
    frames = [synth.side_view_stone(
                  synth.side_view(),
                  view.row_for(C.TEE_TO_HOGLINE_M + (t - t_cross) * speed_m_s),
                  width_px, color, x=x)
              for t in times]
    return frames, times


class TestProposal:
    def test_a_proposal_spans_handle_to_ice(self):
        # side_view_stone takes the plate as its first argument and paints a
        # grey body above `row` with a coloured handle above that, so the
        # trailing edge is `row` and the handle's top is well above it.
        win = synth.side_view_stone(synth.side_view(), 520.0,
                                    width_px=52, color="red")
        props = longview.candidates(np.asarray(win, dtype=np.float32), "red", 52.0)
        assert len(props) == 1
        prop = props[0]
        assert prop.edge_row == pytest.approx(520.0, abs=2.0)
        # The handle's TOP row, which is the whole point of the field -- not
        # its bottom, which the scan already had. side_view_stone paints a body
        # int(0.42 * width_px) = 21 rows tall ending at `row` (499..520) and a
        # handle width_px // 4 // 2 = 6 rows above that, topping out at 493. A
        # loose `top_row < edge_row - 10` passes on 499 just as happily as on
        # 493, so it would not notice the one mistake there is to make here.
        assert prop.top_row == pytest.approx(493.0, abs=1.5)
        assert prop.body_px == pytest.approx(52.0, rel=0.2)


class TestFindingTheCrossing:
    def test_it_times_the_frame_the_stone_reaches_the_line(self):
        frames, times = travelling(speed_m_s=2.0, t_cross=0.5)
        got = longview.find_in_frames(frames, VIEW, "red", times)
        assert got, got.reason
        # OFFSET_S corrects for real footage's contact shadow, which this
        # clean synthetic fixture does not draw, so it shows up here as a
        # constant, expected shift rather than error.
        assert got.t == pytest.approx(0.5 + longview.OFFSET_S, abs=0.04)

    def test_it_interpolates_between_frames_rather_than_snapping(self):
        frames, times = travelling(speed_m_s=2.0, t_cross=0.517)
        got = longview.find_in_frames(frames, VIEW, "red", times)
        assert got.t == pytest.approx(0.517 + longview.OFFSET_S, abs=0.04)
        assert got.t not in times

    def test_it_finds_a_yellow_stone_too(self):
        frames, times = travelling(color="yellow")   # RGB fixture, see synth
        assert longview.find_in_frames(frames, VIEW, "yellow", times)


class TestWhatItRefuses:
    def test_a_stone_that_stops_short_of_the_line(self):
        # crosses at t = 3.0 s, well past the end of a 1 s window
        frames, times = travelling(speed_m_s=2.0, t_cross=3.0)
        got = longview.find_in_frames(frames, VIEW, "red", times)
        assert not got and "never reached" in got.reason

    def test_an_empty_stretch_of_ice(self):
        frames = [synth.side_view() for _ in range(20)]
        got = longview.find_in_frames(frames, VIEW, "red",
                                      [i / 30 for i in range(20)])
        assert not got and "no candidate" in got.reason

    def test_two_stones_crossing_in_the_same_window(self):
        """One is the throw and one is a rock already in play being cleared.
        Nothing here can tell which, so it refuses rather than pick."""
        left, times = travelling(speed_m_s=2.0, t_cross=0.5, x=250)
        right, _ = travelling(speed_m_s=2.0, t_cross=0.55, x=560)
        # Overlay right's stone onto left's background. Comparing against the
        # bare background (not SIDE_ICE) matters: the hog line itself is
        # "not ice" too, and it is at the same row in both -- diffing against
        # SIDE_ICE would let right's copy of the line paint over left's own
        # stone at the exact moment left is crossing it.
        bg = synth.side_view()
        frames = list(left)
        for i, f in enumerate(right):
            frames[i] = np.where(f != bg, f, frames[i])
        got = longview.find_in_frames(frames, VIEW, "red", times)
        assert not got and "two candidates" in got.reason

    def test_a_broom_pad_with_no_stone_under_it(self):
        frames, times = travelling(speed_m_s=2.0, t_cross=0.5)
        stripped = []
        for f, t in zip(frames, times):
            r = int(VIEW.row_for(C.TEE_TO_HOGLINE_M + (t - 0.5) * 2.0))
            g = f.copy()
            g[max(0, r - 22):r, 379:431] = synth.SIDE_ICE   # erase the granite
            stripped.append(g)
        got = longview.find_in_frames(stripped, VIEW, "red", times)
        assert not got and "no candidate" in got.reason

    def test_a_blob_far_too_wide_to_be_a_stone(self):
        frames, times = travelling(speed_m_s=2.0, t_cross=0.5, width_px=160)
        assert not longview.find_in_frames(frames, VIEW, "red", times)

    def test_a_stone_crawling_too_slowly_to_be_a_delivery(self):
        """A stone being nudged aside by a sweeper, or one already at rest that
        the tracker drifted onto. A delivery crosses its hog line between
        1.2 and 3.2 m/s -- the range the 27 hand marks imply."""
        frames, times = travelling(speed_m_s=0.3, t_cross=1.0, span_s=2.0)
        got = longview.find_in_frames(frames, VIEW, "red", times)
        assert not got and "speed" in got.reason

    def test_a_blur_far_too_fast_to_be_a_stone(self):
        frames, times = travelling(speed_m_s=6.0, t_cross=0.5)
        got = longview.find_in_frames(frames, VIEW, "red", times)
        assert not got and "speed" in got.reason

    def test_a_stone_drifting_the_wrong_way(self):
        frames, times = travelling(speed_m_s=-2.0, t_cross=0.5)
        got = longview.find_in_frames(frames, VIEW, "red", times)
        assert not got

    def test_the_window_covers_every_crossing_that_was_marked(self):
        """Marked by hand on 27 deliveries: release + 2.83 s to + 5.43 s."""
        assert longview.WINDOW_S[0] <= 2.83
        assert longview.WINDOW_S[1] >= 5.43


import json
from pathlib import Path

MARKS = Path(__file__).resolve().parents[1] / "datasets/hogmarks/VXU9xwmugRg.json"

# A stone moves about 1.1 px per frame at the hog line and reads ~52 px across,
# so a detector that finds the right object should land inside a couple of
# frames of where a person put it. Anything looser is finding something else.
TOLERANCE_S = 0.15


@pytest.mark.slow
class TestAgainstHandMarkedCrossings:
    """The 27 marks are the ground truth this detector answers to."""

    def _views(self, primary_video):
        from curling_score import analyze as A
        from curling_score.geometry import layout
        from curling_score.ingest import frames as F

        calib = F.sample_keyframes(primary_video, count=A.CALIB_FRAMES,
                                   stride=A.CALIB_STRIDE)
        panels = layout.detect_panels(calib)
        plate = np.median(np.stack([f.astype(np.float32) for f in calib]), axis=0)
        h, w = plate.shape[:2]
        rects = sideview.locate(panels, width=w, height=h)
        return {n: sideview.solve(plate, r, name=n) for n, r in rects.items()}

    def test_it_lands_where_a_person_marked_the_paint(self, primary_video):
        doc = json.loads(MARKS.read_text())
        views = self._views(primary_video)
        errors, refused = [], []
        for end in doc["ends"]:
            view = views[end["side_view"]]
            for m in end["marks"]:
                lo = m["release_t_s"] + longview.WINDOW_S[0]
                hi = m["release_t_s"] + longview.WINDOW_S[1]
                got = longview.find_crossing(primary_video, view, m["color"], lo, hi)
                if not got:
                    refused.append((m["release_t_s"], got.reason))
                    continue
                errors.append(got.t - m["hog_crossing_s"])
        assert errors, "every crossing was refused"
        worst = max(abs(e) for e in errors)
        assert worst <= TOLERANCE_S, (
            f"worst error {worst:.3f} s over {len(errors)} crossings; "
            f"{len(refused)} refused: {refused}")

    def test_it_finds_most_of_them(self, primary_video):
        """Coverage is the point of the whole exercise. The panel manages 46%."""
        doc = json.loads(MARKS.read_text())
        views = self._views(primary_video)
        found = total = 0
        for end in doc["ends"]:
            view = views[end["side_view"]]
            for m in end["marks"]:
                total += 1
                lo = m["release_t_s"] + longview.WINDOW_S[0]
                hi = m["release_t_s"] + longview.WINDOW_S[1]
                found += bool(longview.find_crossing(primary_video, view,
                                                     m["color"], lo, hi))
        assert found / total >= 0.85, f"found {found} of {total}"


SideView = sideview.SideView


class TestOffsetBelongsToTheProposer:
    """`OFFSET_S` corrects where a proposer puts a stone's trailing edge against
    where a person judges its leading edge to touch the paint. The colour scan
    reads early because its edge includes the contact shadow; a trained
    detector draws the granite's own edge and does not. Sharing one constant
    between them made the model read 0.073 s late on all 27 hand marks.
    """

    def _track(self, hog_row):
        # 3.3 rows per 0.1 s is about 33 rows a second, which near the hog line
        # is roughly 2.2 m/s -- mid-range for SPEED_BOUNDS_M_S. The first
        # version of this fixture moved 10 rows a sample, which is over the
        # bound, so `crossing_from_tracks` refused both calls and the test
        # compared None with None and passed while proving nothing.
        return {0: [(10.0 + i * 0.1, hog_row - 8.0 + i * 3.3, 52.0)
                    for i in range(6)]}

    def test_the_default_is_the_colour_scans_own_offset(self):
        view = SideView(rect=(0, 0, 810, 1080), tee_row=435.0, hog_row=520.0)
        a = longview.crossing_from_tracks(self._track(520.0), view)
        b = longview.crossing_from_tracks(self._track(520.0), view,
                                          offset_s=longview.OFFSET_S)
        assert a.t is not None, a.reason
        assert a.t == pytest.approx(b.t)

    def test_a_proposer_can_supply_its_own(self):
        view = SideView(rect=(0, 0, 810, 1080), tee_row=435.0, hog_row=520.0)
        base = longview.crossing_from_tracks(self._track(520.0), view,
                                             offset_s=0.0)
        moved = longview.crossing_from_tracks(self._track(520.0), view,
                                              offset_s=0.25)
        assert base.t is not None and moved.t is not None, base.reason
        assert moved.t - base.t == pytest.approx(0.25)

    def test_the_model_proposer_does_not_inherit_it(self):
        from curling_score.detect import sidemodel
        assert sidemodel.OFFSET_S != longview.OFFSET_S


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
        got = longview.crossing_from_tracks(track, self.VIEW, offset_s=0.0)
        assert got.key == longview.KEY_OK, got.reason

    def test_faster_than_any_delivery_is_still_refused(self):
        track = self._track(9.0)
        assert self._speed(track) > longview.SPEED_BOUNDS_M_S[1]
        got = longview.crossing_from_tracks(track, self.VIEW, offset_s=0.0)
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
        got = longview.crossing_from_tracks(self._halves(), VIEW, offset_s=0.0)
        assert got.key == longview.KEY_OK, got.reason
        assert got.t == pytest.approx(10.0 + 0.4 + (520.0 - 517.2) / 3.3 * 0.1, abs=1e-6)
        assert got.track_key == 3

    def test_the_later_half_alone_still_never_reaches(self):
        got = longview.crossing_from_tracks({4: self._halves()[4]}, VIEW, offset_s=0.0)
        assert got.key == longview.KEY_NEVER_REACHED

    def test_a_crossing_within_one_key_is_timed_from_it_alone(self):
        one = {3: [(10.0 + i * 0.1, 504.0 + i * 3.3, 52.0) for i in range(12)]}
        got = longview.crossing_from_tracks(one, VIEW, offset_s=0.0)
        assert got.key == longview.KEY_OK and got.track_key == 3

    def test_entries_may_carry_a_column_after_the_width(self):
        one = {3: [(10.0 + i * 0.1, 504.0 + i * 3.3, 52.0, 300.0 + i) for i in range(12)]}
        assert longview.crossing_from_tracks(one, VIEW, offset_s=0.0).key == longview.KEY_OK


class TestAStraySampleIsNotACrossing:
    """S1 10/06 game 1, end 7, rock 15: the stone left key 3 for key 4 at row
    564, 21 rows short of the line, and crossed in key 4. Key 3 then picked up
    one stray red box past the line 2.6 s later, so its first and last rows
    straddled the line too and the throw was refused as two crossers.

    A crossing is a pair of samples either side of the line, close in time --
    for a proposer that sees the stone every frame. ``max_gap_s`` says how
    close; the colour scan passes none and keeps interpolating across gaps."""

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

    def test_the_colour_scan_bounds_no_gap(self):
        """Its default: the stray still counts, as it always did, and a lone
        gapped straddle is still interpolated."""
        assert longview.crossing_from_tracks(
            self._tracks(), VIEW, offset_s=0.0).key == longview.KEY_AMBIGUOUS
        assert longview.crossing_from_tracks(
            {3: self._tracks()[3]}, VIEW, offset_s=0.0).key == longview.KEY_OK
