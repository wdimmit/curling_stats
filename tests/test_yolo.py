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


class TestFramesCarryPlayerBoxes:
    """Class 2 is a player: never a stone, kept on the frame for the cover check."""

    def test_two_class_rows_give_the_same_stones(self, cal):
        rows = [(150, 150, 40, 40, 0.9, 0), (100, 100, 22, 22, 0.7, 1)]
        got = yolo.frame_from_rows(rows, cal)
        assert got == yolo.stones_from_rows(rows, cal)
        assert got.players == ()

    def test_player_rows_are_not_stones(self, cal):
        rows = [(150, 150, 40, 40, 0.9, 0), (100, 100, 60, 80, 0.7, 2)]
        got = yolo.frame_from_rows(rows, cal)
        assert len(got) == 1
        (p,) = got.players
        assert (p.x0_px, p.y0_px, p.x1_px, p.y1_px) == (70, 60, 130, 140)
        assert p.confidence == 0.7
        x0, y0 = cal.to_sheet(70, 60)
        x1, y1 = cal.to_sheet(130, 140)
        assert (p.x0_m, p.y0_m, p.x1_m, p.y1_m) == pytest.approx(
            (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)))

    def test_a_player_beyond_the_sideline_is_kept(self, cal):
        from curling_score.geometry import constants as C

        px, py = cal.to_pixels(C.SIDELINE_ABS_X_M + 0.4, 0.0)
        got = yolo.frame_from_rows([(px, py, 30, 30, 0.9, 2)], cal)
        assert len(got.players) == 1

    def test_conf_filters_players_like_stones(self, cal):
        rows = [(100, 100, 60, 80, 0.3, 2), (150, 150, 40, 40, 0.2, 0)]
        got = yolo.frame_from_rows(rows, cal, conf=0.5)
        assert len(got) == 0 and got.players == ()

    def test_stones_from_rows_does_not_choke_on_a_player_row(self, cal):
        assert yolo.stones_from_rows([(100, 100, 60, 80, 0.7, 2)], cal) == []

    def test_flipped_calib_player_box_is_ordered(self, cal):
        import dataclasses

        flipped = dataclasses.replace(cal, flipped=True)
        assert flipped.flipped
        p = yolo.frame_from_rows([(100, 100, 60, 80, 0.7, 2)], flipped).players[0]
        assert p.x0_m <= p.x1_m and p.y0_m <= p.y1_m
        assert (p.x0_m, p.y0_m) != (p.x1_m, p.y1_m)

    def test_seen_pickles_with_players(self, cal):
        import pickle

        seen = yolo.frame_from_rows(
            [(150, 150, 40, 40, 0.9, 0), (100, 100, 60, 80, 0.7, 2)], cal)
        back = pickle.loads(pickle.dumps(seen))
        assert back.players == seen.players
        assert back == seen


class TestABatchLeavesTheGpuOnce:
    """`result_rows` reads a batch in one copy; the stones must be exactly the
    ones reading every box on its own gives."""

    def _results(self):
        import torch
        from ultralytics.engine.results import Boxes

        def boxes(rows):
            data = torch.tensor(rows, dtype=torch.float32).reshape(-1, 6)
            return Boxes(data, orig_shape=(300, 300))

        return [
            FakeResult(boxes([[137.1, 139.3, 159.7, 161.2, 0.91, 0.0],
                              [60.25, 80.5, 81.75, 103.0, 0.42, 1.0]])),
            FakeResult(boxes([])),
            FakeResult(boxes([[10.0, 12.5, 33.0, 35.25, 0.33, 1.0]])),
        ]

    def test_the_same_stones_as_box_by_box(self, cal):
        results = self._results()
        rows = yolo.result_rows(results)
        assert [len(r) for r in rows] == [2, 0, 1]
        for r, got in zip(results, rows):
            assert yolo.stones_from_rows(got, cal) == yolo.boxes_to_stones(r.boxes, cal)

    def test_no_results_no_rows(self):
        assert yolo.result_rows([]) == []


class TestHalfPrecisionKeepsOnePredictor:
    class Model:
        def __init__(self, **overrides):
            self.overrides = overrides

    def test_half_is_asked_for_as_quantize(self, monkeypatch):
        monkeypatch.setattr(yolo, "_knows_quantize", lambda: True)
        assert yolo._precision(self.Model(half=True)) == {"quantize": 16}

    def test_an_ultralytics_from_before_quantize_is_asked_for_half_as_ever(self, monkeypatch):
        monkeypatch.setattr(yolo, "_knows_quantize", lambda: False)
        assert yolo._precision(self.Model(half=True)) == {}

    def test_full_precision_asks_for_nothing(self):
        assert yolo._precision(self.Model()) == {}
        assert yolo._precision(self.Model(half=False)) == {}
