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
