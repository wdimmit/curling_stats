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
        # A lone stone's ~22 dark rows are only 21% of the 105-row crowding
        # band, under the `> 0.5` column rule, so `runs(dark)` finds no span
        # at all there and the self-exclusion branch (`not a <= cx <= b`)
        # never runs -- `test_a_lone_stone_is_uncrowded` above exercises
        # nothing that test doesn't. A full-width dark feature spanning the
        # whole band *does* clear that 50% rule, producing one span across
        # the entire width that contains `cx`; without the self-exclusion
        # this would count as a second body standing beside the stone.
        win = np.asarray(synth.side_view_stone(synth.side_view(), 520.0,
                                               width_px=52), np.float32)
        edge_row, expect_px = 520.0, 52.0
        top = int(edge_row - 2 * expect_px)
        win[top:int(edge_row) + 1, :] = 20.0
        assert sidepool.crowding(win, cx=405.0, edge_row=edge_row,
                                 expect_px=expect_px) == 0


class TestScanMoments:
    def test_a_stone_is_binned_by_where_it_sits(self):
        # 490 rather than 470 for the approach: the synthetic 12-ft annulus
        # runs rows 409-453, a stone's handle sits 27 rows above its trailing
        # edge, and the granite scan in `longview.candidates` stops the moment
        # the dark span stops being stone-sized. A stone at 470 has its handle
        # in the paint, so the scan finds no body and the frame reads
        # "occluded" -- correctly. 490 puts the handle at 463, clear of it.
        got = sidepool.scan_moments("v1", moments([490.0, 520.0, 570.0]),
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

    def test_an_empty_moment_yields_exactly_one_candidate(self):
        """Both colour scans find nothing in an empty moment and would each
        emit an indistinguishable "clear" row at the same stem -- one moment
        must produce one candidate, or `sideframes.select`, which dedupes by
        object identity rather than by stem, would count the same JPEG twice
        against a quota.
        """
        got = sidepool.scan_moments("v1", moments([None]),
                                    {"left": VIEW}, clip_start_s=100.0)
        assert len(got) == 1

    def test_a_handle_with_no_granite_under_it_is_occluded(self):
        plate = synth.side_view()
        plate[500:512, 395:415] = (210, 40, 40)      # a broom pad, no stone
        got = sidepool.scan_moments("v1", [(100.0, {"left": plate})],
                                    {"left": VIEW}, clip_start_s=100.0)
        assert [c.position for c in got if c.color == "red"] == ["occluded"]

    def test_every_candidate_carries_the_window_outcome(self):
        """A moment's outcome is its clip-view-colour verdict, not its own."""
        # 490 -> 570 over 12 frames at 5 fps is 4.64 m in 2.20 s = 2.11 m/s,
        # inside longview.SPEED_BOUNDS_M_S of (1.2, 3.2). The 440 -> 550 this
        # fixture first used is 7.28 m in the same time -- 3.31 m/s, over the
        # bound -- so the "clean crossing" was refused as bad_speed and this
        # test could never have passed.
        rows = [490.0 + 80.0 * i / 11 for i in range(12)]   # a clean crossing
        got = sidepool.scan_moments("v1", moments(rows), {"left": VIEW},
                                    clip_start_s=100.0)
        red = [c for c in got if c.color == "red"]
        assert {c.outcome for c in red} == {"ok"}

    def test_a_window_the_detector_refuses_keeps_its_reason(self):
        # 490, not 470: 470 puts the stone's handle in the synthetic 12-ft
        # annulus (rows 409.3-452.7), so `candidates()` finds no body at all
        # and the refusal there comes from the annulus collision, not from a
        # stone that is genuinely not moving. 490 clears it.
        got = sidepool.scan_moments("v1", moments([490.0, 490.0, 490.0]),
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
        # 490, not 470, for the approach group -- 470 puts the stone's handle
        # in the synthetic 12-ft annulus, so `candidates()` finds no body and
        # the moment reads "occluded" rather than "approach", leaving the
        # bins actually produced {crossing, occluded, past} instead of the
        # {approach, crossing, past} this test's name claims.
        rows = [490.0] * 4 + [520.0] * 4 + [570.0] * 4
        cands = sidepool.scan_moments("v1", moments(rows), {"left": VIEW},
                                      clip_start_s=100.0)
        kept = sidepool.pick_writes(cands, max_per_clip_view=6)
        assert len({c.position for c in kept}) >= 3

    def test_a_yellow_delivery_is_not_thrown_away_for_the_red_clear_row(self):
        """Every moment of a yellow delivery also gets a red "clear" row (no
        red stone there), sharing a stem with the real yellow row. Both sort
        equal under `(t_abs, stem)`, so a stable dedupe that always keeps the
        first-seen (red before yellow, since `scan_moments` scans red first)
        would keep the uninformative "clear" row and lose the yellow
        crossing -- a yellow delivery would never make it into the pool.
        """
        rows = [490.0 + 80.0 * i / 11 for i in range(12)]   # a clean crossing
        cands = sidepool.scan_moments("v1", moments(rows, color="yellow"),
                                      {"left": VIEW}, clip_start_s=100.0)
        kept = sidepool.pick_writes(cands, max_per_clip_view=8)
        assert any(c.color == "yellow" and c.position == "crossing"
                  for c in kept), \
            "the yellow crossing frame was dropped in favour of a red clear row"
