import numpy as np
import pytest

from curling_score.detect import yolo


class FakeBox:
    def __init__(self, cls, conf, xywh):
        self.cls = np.array([cls], dtype=float)
        self.conf = np.array([conf], dtype=float)
        self.xywh = np.array([xywh], dtype=float)


class FakeResult:
    def __init__(self, boxes):
        self.boxes = boxes


@pytest.fixture
def cal():
    from curling_score.geometry import calibrate
    from tests import synth

    return calibrate.solve(
        synth.house_panel(cx=148, cy=150, px_per_m=76.0), delivery_side="bottom"
    )


class TestBoxesToStones:
    def test_maps_a_box_centre_to_sheet_coordinates(self, cal):
        px, py = cal.to_pixels(0.4, -0.3)
        got = yolo.boxes_to_stones([FakeBox(0, 0.9, [px, py, 22, 22])], cal)
        assert len(got) == 1
        assert got[0].color == "red"
        assert (got[0].x_m, got[0].y_m) == pytest.approx((0.4, -0.3), abs=0.01)

    def test_class_one_is_yellow(self, cal):
        got = yolo.boxes_to_stones([FakeBox(1, 0.9, [148, 150, 22, 22])], cal)
        assert got[0].color == "yellow"

    def test_carries_the_model_confidence(self, cal):
        got = yolo.boxes_to_stones([FakeBox(0, 0.73, [148, 150, 22, 22])], cal)
        assert got[0].confidence == pytest.approx(0.73)

    def test_drops_a_stone_on_the_neighbouring_sheet(self, cal):
        from curling_score.geometry import constants as C

        px, py = cal.to_pixels(C.SIDELINE_ABS_X_M + 0.4, 0.0)
        assert yolo.boxes_to_stones([FakeBox(0, 0.9, [px, py, 22, 22])], cal) == []

    def test_no_boxes_gives_no_stones(self, cal):
        assert yolo.boxes_to_stones([], cal) == []

    def test_area_is_taken_from_the_box(self, cal):
        got = yolo.boxes_to_stones([FakeBox(0, 0.9, [148, 150, 20, 24])], cal)
        assert got[0].area_px == pytest.approx(20 * 24)


class TestSeparationIsEnforced:
    def test_two_boxes_on_the_same_spot_collapse(self, cal):
        px, py = cal.to_pixels(0.5, 2.6)
        got = yolo.boxes_to_stones(
            [FakeBox(1, 0.59, [px, py, 22, 22]),
             FakeBox(1, 0.54, [px + 1.5, py, 22, 22])],
            cal,
        )
        assert len(got) == 1
        assert got[0].confidence == pytest.approx(0.59)
