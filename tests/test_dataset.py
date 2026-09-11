import numpy as np
import pytest

from curling_score.detect.rocks import Detection
from curling_score.train import dataset


def det(color, x, y, conf=0.8):
    return Detection(color=color, x_m=x, y_m=y, x_px=100.0 + 10 * x,
                     y_px=200.0 + 10 * y, area_px=140.0, confidence=conf)


class TestYoloLabel:
    def test_encodes_a_box_normalised_to_the_image(self):
        lab = dataset.to_label(det("red", 0.0, 0.0), box_px=20.0, w=297, h=514)
        assert lab.cls == dataset.CLASSES.index("red_stone")
        assert lab.cx == pytest.approx(100.0 / 297, abs=1e-6)
        assert lab.cy == pytest.approx(200.0 / 514, abs=1e-6)
        assert lab.w == pytest.approx(20.0 / 297, abs=1e-6)

    def test_yellow_gets_its_own_class(self):
        lab = dataset.to_label(det("yellow", 0.0, 0.0), box_px=20.0, w=297, h=514)
        assert lab.cls == dataset.CLASSES.index("yellow_stone")

    def test_renders_the_ultralytics_text_form(self):
        lab = dataset.to_label(det("red", 0.0, 0.0), box_px=20.0, w=297, h=514)
        parts = lab.render().split()
        assert parts[0] == "0"
        assert len(parts) == 5
        assert all(0.0 <= float(v) <= 1.0 for v in parts[1:])

    def test_a_box_over_the_edge_is_dropped_by_default(self):
        # Clipping is off by default: it added 2023 edge labels, many of them
        # people standing at the panel edge, and the model trained with them
        # found 11 of 17 observed deliveries against 16 without.
        edge = Detection(color="red", x_m=0, y_m=0, x_px=2.0, y_px=200.0,
                         area_px=140.0, confidence=0.8)
        assert dataset.to_label(edge, box_px=20.0, w=297, h=514) is None

    def test_it_can_be_clipped_instead_when_asked(self):
        edge = Detection(color="red", x_m=0, y_m=0, x_px=2.0, y_px=200.0,
                         area_px=140.0, confidence=0.8)
        lab = dataset.to_label(edge, box_px=20.0, w=297, h=514, clip=True)
        assert lab is not None
        assert lab.cx - lab.w / 2 >= -1e-9
        assert lab.w < 20.0 / 297  # narrower, because part of it is off-frame


class TestInterpolatedLabels:
    """Fill short gaps so the model learns the frames colour thresholding misses."""

    def _seq(self, gap_at=()):
        out = []
        for i in range(40):
            t = i * 0.1
            if i in gap_at:
                out.append((t, []))
            else:
                out.append((t, [det("red", 0.5, 1.0 - 0.02 * i)]))
        return out

    def test_a_short_gap_is_filled(self):
        got = dataset.interpolate_gaps(self._seq(gap_at={10, 11}), max_gap_s=0.5)
        assert len(got[10][1]) == 1
        assert len(got[11][1]) == 1

    def test_the_filled_position_lies_between_its_neighbours(self):
        got = dataset.interpolate_gaps(self._seq(gap_at={10}), max_gap_s=0.5)
        y = got[10][1][0].y_m
        assert got[11][1][0].y_m < y < got[9][1][0].y_m

    def test_a_long_gap_is_left_alone(self):
        # Probably a player standing over it; inventing a label there would
        # teach the model to hallucinate stones behind people.
        gap = set(range(10, 25))
        got = dataset.interpolate_gaps(self._seq(gap_at=gap), max_gap_s=0.5)
        assert all(got[i][1] == [] for i in gap)

    def test_frames_that_need_no_filling_are_unchanged(self):
        seq = self._seq()
        got = dataset.interpolate_gaps(seq, max_gap_s=0.5)
        assert [len(d) for _, d in got] == [len(d) for _, d in seq]

    def test_interpolated_detections_are_marked_lower_confidence(self):
        got = dataset.interpolate_gaps(self._seq(gap_at={10}), max_gap_s=0.5)
        assert got[10][1][0].confidence < 0.8


class TestInterpolatedPixels:
    """Labels are written in pixels, so a filled gap must carry real pixels."""

    def test_pixel_position_matches_the_metre_position(self):
        from curling_score.geometry import calibrate
        from tests import synth

        img = synth.house_panel(cx=148, cy=150, px_per_m=76.0)
        cal = calibrate.solve(img, delivery_side="bottom")

        def at(x_m, y_m, color="red"):
            px, py = cal.to_pixels(x_m, y_m)
            return Detection(color=color, x_m=x_m, y_m=y_m, x_px=px, y_px=py,
                             area_px=140.0, confidence=0.8)

        seq = []
        for i in range(20):
            t, y = i * 0.1, 1.0 - 0.05 * i
            seq.append((t, [] if i == 10 else [at(0.4, y)]))

        got = dataset.interpolate_gaps(seq, max_gap_s=0.5, calib=cal)
        filled = got[10][1][0]
        want_px, want_py = cal.to_pixels(filled.x_m, filled.y_m)
        assert filled.x_px == pytest.approx(want_px, abs=0.5)
        assert filled.y_px == pytest.approx(want_py, abs=0.5)


class TestLabelCleaning:
    """Only label things that behave like stones.

    The first training set was auto-labelled straight from the colour detector,
    which reads a sweeper's team-coloured jacket as stones. The model learned
    that, so filtering the phantoms out downstream was the only defence -- and
    it cost 11 points of delivery recall.

    A stone earns its label by behaving like one: either it sits still for a
    good while, or it belongs to a track confirmed as a delivery. A jacket
    crossing the frame does neither.
    """

    def _static(self, color, x, y, t0, t1, fps=10.0):
        n = int((t1 - t0) * fps)
        return [(t0 + i / fps, det(color, x, y)) for i in range(n)]

    def _merge(self, *traces):
        frames = {}
        for tr in traces:
            for t, d in tr:
                frames.setdefault(round(t, 3), []).append(d)
        return [(t, frames[t]) for t in sorted(frames)]

    def test_a_resting_stone_keeps_its_labels(self):
        seq = self._merge(self._static("red", 0.3, 1.1, 0.0, 30.0))
        got = dataset.keep_stone_like(seq)
        assert sum(len(d) for _, d in got) > 250

    def test_a_blob_that_crosses_and_vanishes_is_dropped(self):
        # A sweeper's jacket: moves down the sheet, then gone.
        sweep = []
        t, y = 0.0, 4.5
        while y > 2.0:
            sweep.append((t, det("yellow", 0.5, y)))
            y -= 0.10
            t += 0.1
        seq = self._merge(sweep, self._static("red", 0.3, 1.1, 0.0, 30.0))
        got = dataset.keep_stone_like(seq)
        assert all(d.color == "red" for _, ds in got for d in ds)

    def test_a_delivery_that_settles_keeps_its_in_flight_labels(self):
        fly = []
        t, y = 0.0, 4.2
        while y > 1.0:
            fly.append((t, det("yellow", 0.4, y)))
            y -= 0.10
            t += 0.1
        fly += self._static("yellow", 0.4, 1.0, t, t + 30.0)
        got = dataset.keep_stone_like(self._merge(fly))
        # The moving frames must survive, or the model never sees a stone
        # in flight -- which is exactly what delivery detection needs.
        moving = [ds for t, ds in got if t < 30.0 and ds]
        assert len(moving) > 20

    def test_nothing_in_gives_nothing_out(self):
        assert dataset.keep_stone_like([]) == []


class TestInFlightLabelsSurvive:
    """The model must see stones in motion, or it cannot spot a delivery.

    The first cleaner dropped a delivered red stone's in-flight frames because
    it only recognised deliveries that came to rest -- this one left the sheet
    out the back, which is what a takeout does.
    """

    def _merge(self, *traces):
        frames = {}
        for tr in traces:
            for t, d in tr:
                frames.setdefault(round(t, 3), []).append(d)
        return [(t, frames[t]) for t in sorted(frames)]

    def test_a_takeout_that_leaves_the_sheet_keeps_its_flight_labels(self):
        fly, t, y = [], 0.0, 3.9
        while y > -2.6:
            fly.append((t, det("red", 0.4, y)))
            y -= 0.16
            t += 0.1
        fly = [(t, d) for t, d in fly if d.y_m > -2.25]
        got = dataset.keep_stone_like(self._merge(fly))
        kept = sum(len(ds) for _, ds in got)
        assert kept > 20, f"only {kept} in-flight labels survived"

    def test_a_settling_delivery_keeps_its_flight_labels(self):
        fly, t, y = [], 0.0, 4.2
        while y > 1.0:
            fly.append((t, det("yellow", 0.4, y)))
            y -= 0.10
            t += 0.1
        n = int(30 * 10)
        fly += [(t + i / 10.0, det("yellow", 0.4, 1.0)) for i in range(n)]
        got = dataset.keep_stone_like(self._merge(fly))
        moving = sum(1 for tt, ds in got if tt < 3.2 and ds)
        assert moving > 20


class TestQuietIntervals:
    """Between one stone settling and the next being thrown, nothing on the ice
    moves but people -- which is what makes honest labels possible without
    hand-labelling."""

    def dv(self, color, t, dur=8.0):
        from curling_score.detect.delivery import Delivery

        return Delivery(color=color, t_enter=t, t_rest=t + dur, entry_y_m=4.0,
                        rest_x_m=0.0, rest_y_m=0.0, travel_m=3.0)

    def test_the_span_between_two_deliveries_is_quiet(self):
        got = dataset.quiet_intervals(
            [self.dv("red", 100), self.dv("yellow", 200)], 0.0, 300.0)
        assert (114.0, 194.0) in got

    def test_a_delivery_in_flight_is_not_inside_one(self):
        got = dataset.quiet_intervals(
            [self.dv("red", 100), self.dv("yellow", 200)], 0.0, 300.0)
        for lo, hi in got:
            assert not (lo < 104.0 < hi), (lo, hi)
            assert not (lo < 203.0 < hi), (lo, hi)

    def test_before_the_first_and_after_the_last_count_too(self):
        # Spans of a comparable length either side, so neither looks like it is
        # hiding a delivery. With a single delivery in a five-minute end they
        # both would -- and the rule then rightly declines to clean them.
        found = [self.dv("red", 100), self.dv("yellow", 160),
                 self.dv("red", 220)]
        got = dataset.quiet_intervals(found, 40.0, 290.0)
        assert any(lo < 60.0 < hi for lo, hi in got), got
        assert any(lo < 270.0 < hi for lo, hi in got), got

    def test_a_lone_delivery_still_yields_its_spans(self):
        # No cap on span length any more: `settled_only` keeps runs that
        # travel down-sheet, so a stone in a long span survives the cleaning
        # and the span can be cleaned like any other.
        got = dataset.quiet_intervals([self.dv("red", 100)], 0.0, 300.0)
        assert len(got) == 2, got

    def test_too_short_a_span_is_not_used(self):
        got = dataset.quiet_intervals(
            [self.dv("red", 100), self.dv("yellow", 130)], 0.0, 300.0)
        assert all(hi - lo >= dataset.MIN_QUIET_S for lo, hi in got)

    def test_no_deliveries_means_nothing_to_bound(self):
        assert dataset.quiet_intervals([], 0.0, 300.0) == []


class TestSettledOnly:
    """A player's red shoes are rung as a stone and followed around the sheet.
    In a quiet span they are provably not one, because they do not stay put."""

    def frames(self):
        out = []
        t = 100.0
        while t <= 160.0:
            dets = [det("red", 0.5, 1.2), det("yellow", -0.4, 0.3)]
            # Something wandering: down the sheet and back up again.
            y = 3.0 - abs(130.0 - t) * 0.05
            dets.append(det("red", 0.1, y, conf=0.6))
            out.append((round(t, 2), dets))
            t += 0.2
        return out

    def test_the_settled_stones_are_kept(self):
        got = dataset.settled_only(self.frames(), 105.0, 155.0)
        assert got
        for _t, dets in got:
            assert any(d.color == "red" and abs(d.x_m - 0.5) < 0.01 for d in dets)
            assert any(d.color == "yellow" for d in dets)

    def test_the_wanderer_is_dropped(self):
        got = dataset.settled_only(self.frames(), 105.0, 155.0)
        assert got
        assert all(len(dets) == 2 for _t, dets in got), \
            [len(d) for _t, d in got[:5]]

    def test_a_span_with_nothing_to_go_on_yields_nothing(self):
        assert dataset.settled_only([], 0.0, 10.0) == []


class TestQuietSpanCleaning:
    """`keep_stone_like` asks only that a detection hold its place for six
    seconds, which a person standing still does too -- a player's red shoes
    held theirs for twelve. The end's own structure says when *everything*
    should be stationary, and then anything moving is provably not a stone."""

    def dv(self, color, t, dur=8.0):
        from curling_score.detect.delivery import Delivery

        return Delivery(color=color, t_enter=t, t_rest=t + dur, entry_y_m=4.0,
                        rest_x_m=0.0, rest_y_m=0.0, travel_m=3.0)

    def frames(self):
        out, t = [], 100.0
        while t <= 200.0:
            dets = [det("red", 0.5, 1.2), det("yellow", -0.4, 0.3)]
            dets.append(det("red", 0.1, 3.0 - abs(160.0 - t) * 0.04, conf=0.6))
            out.append((round(t, 2), dets))
            t += 0.2
        return out

    def test_the_wanderer_is_stripped_inside_a_quiet_span(self):
        got = dataset._clean_quiet_spans(
            self.frames(), [self.dv("red", 90), self.dv("yellow", 210)],
            80.0, 240.0)
        inside = [d for t, d in got if 120.0 <= t <= 180.0]
        assert inside
        assert all(len(d) == 2 for d in inside)

    def test_frames_outside_a_quiet_span_are_untouched(self):
        raw = self.frames()
        got = dict(dataset._clean_quiet_spans(
            raw, [self.dv("red", 90), self.dv("yellow", 210)], 80.0, 240.0))
        # 100.0 is inside the first delivery's flight-and-settle margin.
        assert len(got[100.0]) == len(dict(raw)[100.0])

    def test_no_deliveries_leaves_everything_alone(self):
        raw = self.frames()
        got = dataset._clean_quiet_spans(raw, [], 80.0, 240.0)
        assert got == raw

    def test_every_frame_survives_even_if_its_labels_do_not(self):
        raw = self.frames()
        got = dataset._clean_quiet_spans(
            raw, [self.dv("red", 90), self.dv("yellow", 210)], 80.0, 240.0)
        assert [t for t, _ in got] == [t for t, _ in raw]


class TestEdgeStonesAreLabelled:
    """Stones at the panel edge are cut off by the crop, and dropping them
    excludes them from training altogether, so the model never learns to see
    one.

    The panels reach about 1.9 m either side of the centre line, so a stone
    against the edge can still be 1.84 m from the tee -- inside the house and
    counting. One sat unlabelled at the left edge of every frame of game 1
    end 1 around 420 s, found by the classical detector at 0.86 confidence and
    thrown away by the label writer.
    """

    def test_a_stone_against_the_edge_is_still_labelled(self):
        d = det("yellow", 0.0, 0.0)
        d = Detection(color="yellow", x_m=1.8, y_m=0.37, x_px=9.0, y_px=250.0,
                      area_px=173.0, confidence=0.86)
        got = dataset.to_label(d, 22.0, 297, 516, clip=True)
        assert got is not None
        assert got.cls == 1

    def test_the_box_stays_inside_the_image(self):
        d = Detection(color="yellow", x_m=1.8, y_m=0.37, x_px=9.0, y_px=250.0,
                      area_px=173.0, confidence=0.86)
        got = dataset.to_label(d, 22.0, 297, 516, clip=True)
        assert got.cx - got.w / 2 >= -1e-9
        assert got.cx + got.w / 2 <= 1.0 + 1e-9

    def test_it_describes_only_what_can_be_seen(self):
        # Half the stone is off the edge, so the box is half as wide.
        d = Detection(color="red", x_m=0.0, y_m=0.0, x_px=0.0, y_px=250.0,
                      area_px=173.0, confidence=0.8)
        got = dataset.to_label(d, 22.0, 297, 516, clip=True)
        assert got is None or got.w == pytest.approx(11.0 / 297, abs=1e-6)

    def test_a_sliver_is_still_dropped(self):
        # Almost entirely outside: nothing to learn from.
        d = Detection(color="red", x_m=0.0, y_m=0.0, x_px=-9.0, y_px=250.0,
                      area_px=173.0, confidence=0.8)
        assert dataset.to_label(d, 22.0, 297, 516, clip=True) is None

    def test_a_stone_well_inside_is_unchanged(self):
        d = Detection(color="red", x_m=0.0, y_m=0.0, x_px=148.0, y_px=250.0,
                      area_px=173.0, confidence=0.8)
        got = dataset.to_label(d, 22.0, 297, 516, clip=True)
        assert got.cx == pytest.approx(148.0 / 297)
        assert got.w == pytest.approx(22.0 / 297)


class TestPlaySpan:
    """Outside the period of play the labels are unreliable in both directions
    at once: in game 1 end 3 at 2588 s, a second after the last stone came to
    rest, a player in a yellow jacket is labelled as a yellow stone while real
    reds in the house go unlabelled."""

    def dv(self, color, t, dur=8.0):
        from curling_score.detect.delivery import Delivery

        return Delivery(color=color, t_enter=t, t_rest=t + dur, entry_y_m=4.0,
                        rest_x_m=0.0, rest_y_m=0.0, travel_m=3.0)

    def test_it_stops_short_at_both_ends(self):
        # The first delivery cannot be told apart from the setup before it and
        # the last cannot be told from the clearing after, so both go.
        got = dataset.play_span(
            [self.dv("red", 100), self.dv("yellow", 400),
             self.dv("red", 700), self.dv("yellow", 800)], 0.0, 900.0)
        assert got == (395.0, 708.0)

    def test_a_single_delivery_is_kept_rather_than_nothing(self):
        got = dataset.play_span([self.dv("red", 100)], 0.0, 900.0)
        assert got[1] == 108.0

    def test_it_does_not_reach_past_the_end(self):
        got = dataset.play_span([self.dv("red", 100)], 0.0, 105.0)
        assert got[1] <= 105.0

    def test_no_deliveries_means_no_span(self):
        assert dataset.play_span([], 0.0, 900.0) is None


class TestFillThresholdCoversAFrame:
    """The threshold was smaller than one frame interval at the rate the set is
    built at, so a single dropped frame was never filled -- 0 of 24 gaps."""

    def test_it_is_larger_than_a_frame_interval(self):
        assert dataset.MAX_FILL_GAP_S > 1.0 / 2.0

    def test_a_one_frame_dropout_is_filled(self):
        frames = [(0.0, [det("red", 0.5, 1.0)]),
                  (0.5, [det("red", 0.5, 1.0)]),
                  (1.0, []),
                  (1.5, [det("red", 0.5, 1.0)]),
                  (2.0, [det("red", 0.5, 1.0)])]
        got = dict(dataset.interpolate_gaps(frames))
        assert len(got[1.0]) == 1

    def test_a_long_occlusion_is_not_filled(self):
        frames = [(0.0, [det("red", 0.5, 1.0)])]
        frames += [(0.5 * i, []) for i in range(1, 12)]
        frames.append((6.0, [det("red", 0.5, 1.0)]))
        got = dict(dataset.interpolate_gaps(frames))
        assert got[3.0] == []


class TestMergingTwoDetectors:
    """Neither detector sees everything: over 3133 sampled frames the model
    found 369 stones the classical detector missed and the classical detector
    found 141 the model missed."""

    def test_stones_only_one_saw_are_all_kept(self):
        got = dataset.merge_detections(
            [det("red", 0.5, 1.0)], [det("yellow", -0.5, 2.0)])
        assert len(got) == 2

    def test_the_same_stone_seen_twice_is_one_stone(self):
        got = dataset.merge_detections(
            [det("red", 0.50, 1.00, conf=0.4)],
            [det("red", 0.52, 1.01, conf=0.9)])
        assert len(got) == 1

    def test_the_more_confident_reading_wins(self):
        got = dataset.merge_detections(
            [det("red", 0.50, 1.00, conf=0.4)],
            [det("red", 0.52, 1.01, conf=0.9)])
        assert got[0].confidence == pytest.approx(0.9)

    def test_two_real_stones_side_by_side_are_not_merged(self):
        # A stone's width apart is two stones, not one seen twice.
        got = dataset.merge_detections(
            [det("red", 0.0, 0.0)], [det("red", 0.0, 0.4)])
        assert len(got) == 2

    def test_different_colours_never_merge(self):
        got = dataset.merge_detections(
            [det("red", 0.5, 1.0)], [det("yellow", 0.5, 1.0)])
        assert len(got) == 2

    def test_nothing_extra_leaves_the_input_alone(self):
        got = dataset.merge_detections([det("red", 0.5, 1.0)], [])
        assert len(got) == 1




class TestBoxPx:
    def test_needs_no_detection(self):
        # An empty frame has no detection to take a box size from, and it is
        # exactly the frame where a reviewer will be adding the first box.
        from curling_score.geometry import calibrate

        cal = calibrate.PanelCalib((148.0, 150.0), 75.0, 1.7, 0.003, False)
        assert dataset.box_px(cal) == pytest.approx(2 * 0.142 * 75.0)

    def test_matches_what_the_detection_form_returns(self):
        from curling_score.detect.rocks import Detection
        from curling_score.geometry import calibrate

        cal = calibrate.PanelCalib((148.0, 150.0), 75.0, 1.7, 0.003, False)
        det = Detection("red", 0.0, 0.0, 148.0, 150.0, 100.0, 0.9)
        assert dataset.box_px(cal) == dataset.box_px_for(det, cal)


def _sample(n_dets=1, w=297, h=514):
    import numpy as np

    from curling_score.detect.rocks import Detection

    img = np.zeros((h, w, 3), np.uint8)
    dets = [Detection("red", 0.0, 0.0, 148.0, 150.0 + 40 * i, 100.0, 0.9)
            for i in range(n_dets)]
    return img, dets


def _calib():
    from curling_score.geometry import calibrate

    return calibrate.PanelCalib((148.0, 150.0), 75.0, 1.7, 0.003, False)


class TestWriteSplitNegatives:
    def test_drops_an_empty_frame_by_default(self):
        import tempfile
        from pathlib import Path

        img, _ = _sample()
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            n = dataset.write_split(out, "train", [(1.0, img, [])], _calib())
            assert n == 0
            assert not list((out / "images" / "train").glob("*.jpg"))

    def test_keeps_an_empty_frame_when_asked(self):
        # Every dataset so far skipped these by construction, so the model has
        # never been shown a frame whose right answer is "nothing". A person
        # confirming an empty house is what makes that answer trustworthy.
        import tempfile
        from pathlib import Path

        img, _ = _sample()
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            n = dataset.write_split(out, "train", [(1.0, img, [])], _calib(),
                                    keep_empty=True)
            assert n == 1
            (label,) = (out / "labels" / "train").glob("*.txt")
            assert label.read_text() == ""
            assert (out / "images" / "train" / f"{label.stem}.jpg").exists()

    def test_still_writes_a_frame_that_has_stones(self):
        import tempfile
        from pathlib import Path

        img, dets = _sample(3)
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            n = dataset.write_split(out, "train", [(1.0, img, dets)], _calib(),
                                    keep_empty=True, clip_edges=True)
            assert n == 1
            (label,) = (out / "labels" / "train").glob("*.txt")
            assert len(label.read_text().strip().splitlines()) == 3

    def test_a_frame_whose_boxes_all_clip_away_still_counts_as_empty(self):
        import tempfile
        from pathlib import Path

        from curling_score.detect.rocks import Detection

        img, _ = _sample()
        off = [Detection("red", 0.0, 0.0, -500.0, -500.0, 100.0, 0.9)]
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            n = dataset.write_split(out, "train", [(1.0, img, off)], _calib(),
                                    keep_empty=True, clip_edges=True)
            assert n == 1
            (label,) = (out / "labels" / "train").glob("*.txt")
            assert label.read_text() == ""


class TestWriteYaml:
    def test_bakes_in_no_absolute_path(self):
        # ds10's yaml only ever worked at the one path it was written at, so
        # phase10.sh had to sed it on every move. Ultralytics resolves a
        # missing "path:" against the yaml's own directory, which is what we
        # want; "path: ." is NOT the same, because "." exists and resolves
        # against the working directory instead.
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as d:
            text = dataset.write_yaml(Path(d)).read_text()
            assert "path:" not in text
            assert d not in text

    def test_names_the_splits_and_classes(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as d:
            text = dataset.write_yaml(Path(d)).read_text()
            assert "train: images/train" in text
            assert "val: images/val" in text
            for i, name in enumerate(dataset.CLASSES):
                assert f"  {i}: {name}" in text

    def test_can_still_be_given_an_explicit_root(self):
        # A combined ds10+ds11 run needs splits from two directories, which
        # only works with a shared root above both.
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as d:
            text = dataset.write_yaml(Path(d), root="/data/wdd/curling",
                                      train=["ds10/images/train", "ds11/images/train"],
                                      val="ds11/images/val").read_text()
            assert "path: /data/wdd/curling" in text
            assert "  - ds10/images/train" in text
            assert "  - ds11/images/train" in text
