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
