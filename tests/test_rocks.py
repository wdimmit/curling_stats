import numpy as np
import pytest

from curling_score.detect import rocks
from curling_score.geometry import calibrate, constants as C
from tests import synth

PX_PER_M = 76.0
CX, CY = 148.0, 150.0


@pytest.fixture(scope="module")
def cal():
    img = synth.house_panel(cx=CX, cy=CY, px_per_m=PX_PER_M)
    return calibrate.solve(img, delivery_side="bottom")


@pytest.fixture
def empty():
    return synth.house_panel(cx=CX, cy=CY, px_per_m=PX_PER_M)


def at(x_m, y_m, cal):
    return cal.to_pixels(x_m, y_m)


class TestFindStones:
    def test_an_empty_house_yields_nothing(self, empty, cal):
        assert rocks.find_stones(empty, cal) == []

    def test_the_painted_rings_are_not_mistaken_for_stones(self, empty, cal):
        # The blue 4-foot and green 12-foot are large saturated areas; neither
        # is stone-coloured, and neither may produce a detection.
        assert rocks.find_stones(empty, cal) == []

    def test_finds_a_single_red_stone_where_it_was_placed(self, empty, cal):
        px, py = at(0.4, -0.3, cal)
        img = synth.with_stone(empty, px, py, "red")
        found = rocks.find_stones(img, cal)
        assert len(found) == 1
        assert found[0].color == "red"
        assert (found[0].x_m, found[0].y_m) == pytest.approx((0.4, -0.3), abs=0.05)

    def test_distinguishes_red_from_yellow(self, empty, cal):
        img = synth.with_stone(empty, *at(-0.5, 0.2, cal), "red")
        img = synth.with_stone(img, *at(0.5, 0.2, cal), "yellow")
        found = sorted(rocks.find_stones(img, cal), key=lambda s: s.x_m)
        assert [s.color for s in found] == ["red", "yellow"]

    def test_finds_a_full_house_of_stones(self, empty, cal):
        img = empty
        placed = [(0.1, 0.1), (-0.4, 0.5), (0.7, -0.6), (-0.9, -0.2), (1.2, 0.4)]
        for i, (x, y) in enumerate(placed):
            img = synth.with_stone(img, *at(x, y, cal), "red" if i % 2 else "yellow")
        assert len(rocks.find_stones(img, cal)) == len(placed)

    def test_ignores_a_stone_on_the_neighbouring_sheet(self, empty, cal):
        # Sheet 1's panel shows a sliver of the adjacent sheet; a stone resting
        # there is outside our side lines and is not part of this game.
        outside = C.SIDELINE_ABS_X_M + 0.5
        img = synth.with_stone(empty, *at(outside, 0.0, cal), "red")
        assert rocks.find_stones(img, cal) == []


class TestTouchingStones:
    def test_separates_stones_that_touch(self):
        # Handles stay apart even when the granite bodies touch, which is why
        # we detect the handle rather than the body.
        img = synth.house_panel(cx=CX, cy=CY, px_per_m=PX_PER_M)
        cal = calibrate.solve(img, delivery_side="bottom")
        gap = 2 * C.STONE_RADIUS_M  # exactly touching
        img = synth.with_stone(img, *at(-gap / 2, 0.0, cal), "red")
        img = synth.with_stone(img, *at(gap / 2, 0.0, cal), "yellow")
        found = rocks.find_stones(img, cal)
        assert len(found) == 2
        assert {s.color for s in found} == {"red", "yellow"}


class TestConfidence:
    def test_a_clean_well_formed_stone_scores_high(self, empty, cal):
        img = synth.with_stone(empty, *at(0.3, 0.2, cal), "red")
        found = rocks.find_stones(img, cal)
        assert len(found) == 1
        assert found[0].confidence > 0.7

    def test_a_ragged_partial_blob_scores_low(self, empty, cal):
        import cv2

        img = empty.copy()
        px, py = at(0.3, 0.2, cal)
        # A thin crescent: stone-coloured, but nothing like a handle.
        cv2.ellipse(img, (int(px), int(py)), (9, 2), 30.0, 0, 360, (40, 40, 210), -1)
        found = rocks.find_stones(img, cal)
        assert all(f.confidence < 0.6 for f in found)


class TestExpectedHandleRadius:
    def test_is_largest_near_the_house_and_falls_off_up_sheet(self):
        # Measured over 274 detections across all five sheets: the camera sits
        # above the house, so handles nearer the hog line are seen obliquely.
        near = rocks.expected_handle_radius_m(0.0)
        far = rocks.expected_handle_radius_m(4.0)
        assert 0.09 < near < 0.11
        assert far < near * 0.7

    def test_is_flat_enough_across_the_scoring_region(self):
        # Everything that can score lies within 1.971 m of the tee.
        vals = [rocks.expected_handle_radius_m(y) for y in (-1.9, -1.0, 0.0, 1.0, 1.9)]
        assert max(vals) / min(vals) < 1.3


class TestOnRealFootage:
    def test_reads_the_nine_stone_cluster_exactly(self, known_frame, harvested_frames):
        # The case that justifies detecting handles rather than granite bodies:
        # nine stones packed into the 4-foot with the bodies touching.
        import cv2
        from curling_score.geometry import layout, lighting

        imgs = harvested_frames("13REHKrIE9I")
        x, y, w, h = layout.detect_panels(imgs).top
        lit = [
            im[y : y + h, x : x + w]
            for im in imgs
            if lighting.is_calibratable(im[y : y + h, x : x + w])
        ]
        cal = calibrate.solve(
            np.median(np.stack(lit), axis=0).astype(np.uint8), delivery_side="bottom"
        )

        panel = known_frame("sheet1_t9000_cluster9.png")[y : y + h, x : x + w]
        found = rocks.find_stones(panel, cal)
        reds = [d for d in found if d.color == "red"]
        yellows = [d for d in found if d.color == "yellow"]
        assert (len(reds), len(yellows)) == (4, 5)
        # Seven of the nine are in the house, two are guards out front.
        assert sum(1 for d in found if d.distance_to_tee <= C.IN_HOUSE_MAX_D_M) == 7
        assert all(d.confidence > 0.5 for d in found)

    def test_finds_nothing_in_a_dark_panel(self, known_frame, harvested_frames):
        import cv2
        from curling_score.geometry import layout, lighting

        imgs = harvested_frames("QnWHfqaLzzc")
        x, y, w, h = layout.detect_panels(imgs).bottom
        lit = [
            im[y : y + h, x : x + w]
            for im in imgs
            if lighting.is_calibratable(im[y : y + h, x : x + w])
        ]
        cal = calibrate.solve(
            np.median(np.stack(lit), axis=0).astype(np.uint8), delivery_side="top"
        )
        panel = known_frame("sheet4_t9000_lightsout.png")[y : y + h, x : x + w]
        assert lighting.is_playable(panel) is False
        assert rocks.find_stones(panel, cal) == []


class TestMinimumSeparation:
    """Two stones cannot occupy the same place.

    A stone is 0.284 m across, so two detections of the same colour closer than
    that are not two stones -- one is spurious. Observed during a sweeper pass:
    pairs of yellow detections 0.02 m apart, which are patches of one player's
    yellow jacket.
    """

    def test_two_detections_closer_than_a_stone_collapse_to_one(self, cal):
        from curling_score.detect.rocks import Detection

        a = Detection(color="yellow", x_m=0.50, y_m=2.60, x_px=100, y_px=200,
                      area_px=520, confidence=0.59)
        b = Detection(color="yellow", x_m=0.52, y_m=2.59, x_px=102, y_px=201,
                      area_px=513, confidence=0.54)
        got = rocks.enforce_separation([a, b])
        assert len(got) == 1
        assert got[0].confidence == 0.59, "the more confident one survives"

    def test_stones_a_full_diameter_apart_are_both_kept(self):
        from curling_score.detect.rocks import Detection

        a = Detection(color="red", x_m=0.0, y_m=0.0, x_px=0, y_px=0,
                      area_px=140, confidence=0.9)
        b = Detection(color="red", x_m=0.30, y_m=0.0, x_px=0, y_px=0,
                      area_px=140, confidence=0.9)
        assert len(rocks.enforce_separation([a, b])) == 2

    def test_different_colours_may_touch(self):
        from curling_score.detect.rocks import Detection

        a = Detection(color="red", x_m=0.0, y_m=0.0, x_px=0, y_px=0,
                      area_px=140, confidence=0.9)
        b = Detection(color="yellow", x_m=0.02, y_m=0.0, x_px=0, y_px=0,
                      area_px=140, confidence=0.9)
        # Touching stones of opposite colours are common and legitimate; the
        # handles are what we localise and they stay apart.
        assert len(rocks.enforce_separation([a, b])) == 2

    def test_a_cluster_collapses_to_the_best_of_each_group(self):
        from curling_score.detect.rocks import Detection

        cluster = [
            Detection(color="yellow", x_m=0.50 + 0.01 * i, y_m=2.60,
                      x_px=0, y_px=0, area_px=500, confidence=0.3 + 0.1 * i)
            for i in range(4)
        ]
        got = rocks.enforce_separation(cluster)
        assert len(got) == 1
        assert got[0].confidence == pytest.approx(0.6)

    def test_an_empty_list_is_fine(self):
        assert rocks.enforce_separation([]) == []
