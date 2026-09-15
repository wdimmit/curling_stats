import numpy as np
import pytest

from curling_score.geometry import sideview
from curling_score.harvest import sidepool
from tests import synth

VIEW = sideview.SideView(rect=(0, 0, 810, 1080), tee_row=430.0, hog_row=520.0)


def moments(rows, *, color="red", fps=5.0, t0=100.0):
    """`(t, {"left": rgb})` with one stone at each of `rows`, or none for None."""
    out = []
    for i, row in enumerate(rows):
        plate = synth.side_view()
        img = plate if row is None else synth.side_view_stone(
            plate, row, width_px=52, color=color)
        out.append((t0 + i / fps, {"left": img}))
    return out


class TestCrowding:
    def test_a_lone_stone_is_uncrowded(self):
        win = np.asarray(synth.side_view_stone(synth.side_view(), 520.0,
                                               width_px=52), np.float32)
        assert sidepool.crowding(win, cx=405.0, edge_row=520.0, expect_px=52.0) == 0

    def test_a_body_beside_the_stone_counts(self):
        win = np.asarray(synth.side_view_stone(synth.side_view(), 520.0,
                                               width_px=52), np.float32)
        win[420:521, 520:600] = 20.0          # a sweeper: dark, tall, beside it
        assert sidepool.crowding(win, cx=405.0, edge_row=520.0, expect_px=52.0) == 1

    def test_the_stone_does_not_count_itself(self):
        win = np.asarray(synth.side_view_stone(synth.side_view(), 520.0,
                                               width_px=52), np.float32)
        assert sidepool.crowding(win, cx=405.0, edge_row=520.0, expect_px=52.0) == 0


class TestScanMoments:
    def test_a_stone_is_binned_by_where_it_sits(self):
        got = sidepool.scan_moments("v1", moments([470.0, 520.0, 570.0]),
                                    {"left": VIEW}, clip_start_s=100.0)
        by_t = {c.t_abs: c for c in got if c.view == "left" and c.color == "red"}
        assert [by_t[t].position for t in sorted(by_t)] == \
            ["approach", "crossing", "past"]

    def test_a_moment_with_no_stone_is_still_a_candidate(self):
        """"clear" and "occluded" are bins, so empty moments must survive."""
        got = sidepool.scan_moments("v1", moments([None, None, None]),
                                    {"left": VIEW}, clip_start_s=100.0)
        assert got, "an empty band produced no candidate at all"
        assert {c.position for c in got} == {"clear"}
        assert all(c.labels == () for c in got)

    def test_a_handle_with_no_granite_under_it_is_occluded(self):
        plate = synth.side_view()
        plate[500:512, 395:415] = (210, 40, 40)      # a broom pad, no stone
        got = sidepool.scan_moments("v1", [(100.0, {"left": plate})],
                                    {"left": VIEW}, clip_start_s=100.0)
        assert [c.position for c in got if c.color == "red"] == ["occluded"]

    def test_every_candidate_carries_the_window_outcome(self):
        """A moment's outcome is its clip-view-colour verdict, not its own."""
        rows = [440.0 + 10 * i for i in range(12)]   # a clean crossing
        got = sidepool.scan_moments("v1", moments(rows), {"left": VIEW},
                                    clip_start_s=100.0)
        red = [c for c in got if c.color == "red"]
        assert {c.outcome for c in red} == {"ok"}

    def test_a_window_the_detector_refuses_keeps_its_reason(self):
        got = sidepool.scan_moments("v1", moments([470.0, 470.0, 470.0]),
                                    {"left": VIEW}, clip_start_s=100.0)
        red = [c for c in got if c.color == "red"]
        assert {c.outcome for c in red} <= {"no_candidate", "never_reached"}

    def test_a_proposal_becomes_a_box_whose_bottom_is_the_ice(self):
        got = sidepool.scan_moments("v1", moments([520.0]), {"left": VIEW},
                                    clip_start_s=100.0)
        (lab,) = [c.labels[0] for c in got if c.labels]
        bottom = (lab.cy + lab.h / 2) * 1080
        assert bottom == pytest.approx(520.0, abs=3.0)


class TestWriteCap:
    def test_writes_are_capped_per_clip_and_view(self, tmp_path):
        """A 24 s clip at 5 fps must not write 120 frames per view."""
        rows = [440.0 + 2 * i for i in range(120)]
        cands = sidepool.scan_moments("v1", moments(rows), {"left": VIEW},
                                      clip_start_s=100.0)
        kept = sidepool.pick_writes(cands, max_per_clip_view=8)
        assert len(kept) <= 8

    def test_the_cap_still_spans_the_bins_the_clip_produced(self, tmp_path):
        rows = [470.0] * 4 + [520.0] * 4 + [570.0] * 4
        cands = sidepool.scan_moments("v1", moments(rows), {"left": VIEW},
                                      clip_start_s=100.0)
        kept = sidepool.pick_writes(cands, max_per_clip_view=6)
        assert len({c.position for c in kept}) >= 3
