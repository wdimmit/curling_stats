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
        emit an indistinguishable "clear" row at the same stem -- two objects
        for one JPEG. `sideframes.select` dedupes on the stem itself, not on
        object identity, so it alone would not be fooled by the duplicate --
        but `pick_writes` and `sidestages._supply` read the pool one row at a
        time too, and should not each have to notice and collapse a
        duplicate this module had no reason to create.
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


class TestIceBounds:
    """The band a delivery crossing the hog line can occupy.

    The first attempt let `longview.candidates` propose anywhere in the view,
    and the racks of stones stored beside the sheet are red and in every frame
    -- the entire 10:1 red skew was furniture. Measured on real frames from
    VXU9xwmugRg, the racks sit at rows ~365-405 against a tee at ~435 and a hog
    at ~520, i.e. *above* the house, on the platform behind it.

    The bound is therefore a DEPTH bound, not the lateral one the plan first
    asked for: `SideView` models row <-> metres-along-the-sheet and carries no
    lateral scale at all, so there is nothing to build a sideline from.
    """

    def test_the_band_runs_from_the_tee_to_past_the_hog_line(self):
        top, bottom = sidepool.ice_bounds(VIEW)
        assert top == pytest.approx(VIEW.tee_row, abs=1)
        assert bottom > VIEW.hog_row

    def test_the_racks_are_outside_it(self):
        """Measured rack rows on real frames. The hack line is NOT a usable
        top bound -- it computes to row ~398, inside that band."""
        top, _bottom = sidepool.ice_bounds(VIEW)
        for rack_row in (365.0, 385.0, 405.0):
            assert rack_row < top, f"a rack at row {rack_row} is inside the ice band"

    def test_a_stone_on_the_line_is_inside_it(self):
        top, bottom = sidepool.ice_bounds(VIEW)
        assert top < VIEW.hog_row < bottom


class TestFramesForWindow:
    def _window(self, t0=100.0, t1=104.5, color="red"):
        from curling_score.harvest.sideshots import Window
        return Window(video_id="v1", camera="left", color=color, t0=t0, t1=t1,
                      end_number=1, shot_number=3, t_release=98.0, t_rest=None)

    def test_a_rack_like_blob_above_the_ice_proposes_nothing(self):
        """A red blob where the racks are must not become a box."""
        plate = synth.side_view()
        # A rack-shaped red mass on the platform behind the house.
        plate[370:400, 60:220] = (205, 45, 45)
        got = sidepool.frames_for_window(
            [(100.0 + i / 5.0, plate) for i in range(12)],
            VIEW, self._window(), n_near=3, n_far=1)
        assert got, "the window produced no frames at all"
        assert not any(c.labels for c in got), \
            "a rack outside the ice band was proposed as a stone"

    def test_a_window_with_no_stone_still_yields_frames(self):
        """The shot list says a delivery was here, so the frame is worth a
        person's eye even when the detector sees nothing."""
        got = sidepool.frames_for_window(
            [(100.0 + i / 5.0, synth.side_view()) for i in range(12)],
            VIEW, self._window(), n_near=3, n_far=1)
        assert len(got) == 3
        assert all(c.labels == () for c in got)

    def test_frames_are_chosen_by_nearness_to_the_line(self):
        rows = [480.0, 490.0, 500.0, 512.0, 525.0, 545.0, 570.0, 600.0]
        moments = [(100.0 + i / 5.0,
                    synth.side_view_stone(synth.side_view(), r, width_px=52))
                   for i, r in enumerate(rows)]
        got = sidepool.frames_for_window(moments, VIEW, self._window(),
                                         n_near=3, n_far=0)
        assert len(got) == 3
        # Nearest three of the eight are 525, 512 and 500 against a hog of 520.
        # Asserted against the ranking rather than a pixel threshold: the
        # detected edge sits ~0.5 row off the painted one, so any fixed cutoff
        # here would be pinning the fixture's rendering, not the selection.
        picked = sorted(round(c.edge_row) for c in got)
        assert picked == [500, 512, 525], picked

    def test_the_approach_is_represented_too(self):
        rows = [470.0, 485.0, 500.0, 512.0, 520.0, 528.0]
        moments = [(100.0 + i / 5.0,
                    synth.side_view_stone(synth.side_view(), r, width_px=52))
                   for i, r in enumerate(rows)]
        got = sidepool.frames_for_window(moments, VIEW, self._window(),
                                         n_near=2, n_far=2)
        assert any(c.position == "approach" for c in got)

    def test_one_frame_cannot_be_taken_twice(self):
        rows = [512.0, 514.0, 516.0]
        moments = [(100.0 + i / 5.0,
                    synth.side_view_stone(synth.side_view(), r, width_px=52))
                   for i, r in enumerate(rows)]
        got = sidepool.frames_for_window(moments, VIEW, self._window(),
                                         n_near=3, n_far=3)
        assert len({c.stem for c in got}) == len(got)

    def test_the_window_groups_the_frames_it_produced(self):
        """`sideframes.select` caps per clip; a window is this pool's clip, so
        MAX_PER_CLIP stops one delivery filling a bin with near duplicates."""
        w = self._window()
        got = sidepool.frames_for_window(
            [(100.0 + i / 5.0, synth.side_view()) for i in range(12)],
            VIEW, w, n_near=2, n_far=0)
        assert {c.clip_start_s for c in got} == {w.t0}

    def test_a_person_shaped_blob_on_the_ice_is_not_a_stone(self):
        """With the racks excluded, what is left to be mistaken for a stone is
        a player wearing the scanned colour -- and the crouching sweeper stands
        at the very rows a delivery occupies, so no row bound separates them.
        Measured over 492 real on-ice proposals the height:width ratio is
        bimodal: a mode at 0.4-0.6 on the physical 0.40, a trough at 1.0-2.0,
        a second mode at 2.0-3.0 running out past 15."""
        plate = synth.side_view()
        # A tall red torso standing on the ice, its foot at the hog line.
        plate[400:520, 380:430] = (205, 45, 45)
        got = sidepool.frames_for_window(
            [(100.0 + i / 5.0, plate) for i in range(12)],
            VIEW, self._window(), n_near=3, n_far=1)
        assert not any(c.labels for c in got), \
            "a body-shaped blob on the ice was proposed as a stone"

    def test_a_real_stone_survives_the_aspect_bound(self):
        got = sidepool.frames_for_window(
            [(100.0 + i / 5.0,
              synth.side_view_stone(synth.side_view(), 518.0, width_px=52))
             for i in range(12)],
            VIEW, self._window(), n_near=2, n_far=0)
        assert all(c.labels for c in got), "the aspect bound rejected a stone"


class TestBandCrop:
    """Cropping is what lets a labelling session be COMPLETE. Out of band sit
    parked stones, racks, the far wall; a reviewer either boxes all of them --
    work with no bearing on timing a hog crossing -- or leaves them unboxed,
    which teaches the model that stones are background.
    """

    def test_it_spans_the_ice_band_and_a_little_past(self):
        top, bottom = sidepool.band_crop(VIEW)
        ice_top, ice_bottom = sidepool.ice_bounds(VIEW)
        assert top == ice_top
        assert bottom == ice_bottom + sidepool.BAND_PAD_BELOW

    def test_there_is_no_pad_above_because_the_racks_are_there(self):
        """Measured on real frames: racks at rows ~365-405, tee at ~435. Any
        upward padding walks them back into the training image."""
        top, _ = sidepool.band_crop(VIEW)
        for rack_row in (365.0, 385.0, 405.0):
            assert rack_row < top

    def test_the_hog_line_sits_inside_the_crop(self):
        top, bottom = sidepool.band_crop(VIEW)
        assert top < VIEW.hog_row < bottom

    def test_a_label_moves_with_the_pixels(self):
        from curling_score.train import dataset
        # A box centred on the hog line in a 1080-row view.
        lab = dataset.Label(cls=0, cx=0.5, cy=520.0 / 1080, w=0.064,
                            h=22.0 / 1080)
        y0, y1 = sidepool.band_crop(VIEW)
        out = sidepool.crop_label(lab, y0, y1, 1080)
        assert out.cy * (y1 - y0) + y0 == pytest.approx(520.0)
        assert out.h * (y1 - y0) == pytest.approx(22.0)

    def test_a_crop_is_tall_enough_to_hold_a_stone_with_room(self):
        y0, y1 = sidepool.band_crop(VIEW)
        at_hog = VIEW.stone_width_at(VIEW.hog_row, 52.0) * 0.42
        assert (y1 - y0) > 6 * at_hog


class TestShiftedKeepsLateral:
    def test_moving_the_origin_keeps_the_across_calibration(self):
        from curling_score.geometry.sideview import SideView
        from curling_score.harvest import sidepool
        v = SideView(rect=(1107, 0, 813, 1080), tee_row=436.45, hog_row=514.0,
                     centre_col=414.7, lat_px_per_m_at_tee=128.4)
        got = sidepool._shifted(v)
        assert got.rect == (0, 0, 813, 1080)
        # centre_col is in the view's own columns, so a shift leaves it alone
        assert (got.centre_col, got.lat_px_per_m_at_tee) == (414.7, 128.4)
