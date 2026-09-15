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


class TestFindingTheCrossing:
    def test_it_times_the_frame_the_stone_reaches_the_line(self):
        frames, times = travelling(speed_m_s=2.0, t_cross=0.5)
        got = longview.find_in_frames(frames, VIEW, "red", times)
        assert got, got.reason
        assert got.t == pytest.approx(0.5, abs=0.04)

    def test_it_interpolates_between_frames_rather_than_snapping(self):
        frames, times = travelling(speed_m_s=2.0, t_cross=0.517)
        got = longview.find_in_frames(frames, VIEW, "red", times)
        assert got.t == pytest.approx(0.517, abs=0.04)
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
