"""The broom detector reads the crop the labels were made on."""
from types import SimpleNamespace

import numpy as np
import pytest

from curling_score.detect import broommodel
from curling_score.geometry.sideview import SideView

VIEW = SideView(rect=(0, 0, 810, 1080), tee_row=430.0, hog_row=520.0,
                centre_col=390.0, lat_px_per_m_at_tee=148.0)


class FakeModel:
    """Returns one box per crop, in CROP pixels, and records what it was fed."""
    def __init__(self, box=(380.0, 100.0, 410.0, 125.0), conf=0.9):
        self.box, self.conf, self.fed = box, conf, []

    def predict(self, crops, imgsz, conf, verbose):
        self.fed.append((len(crops), crops[0].shape, imgsz, conf))
        t = lambda a: SimpleNamespace(cpu=lambda: SimpleNamespace(numpy=lambda: np.array(a)))
        boxes = SimpleNamespace(xyxy=t([self.box]), conf=t([self.conf]), cls=t([0]))
        return [SimpleNamespace(boxes=boxes) for _ in crops]


class TestCropRows:
    def test_it_runs_from_the_skip_s_legs_to_past_the_hog_line(self):
        top, bot = broommodel.crop_rows(VIEW)
        assert top == 300 and bot > VIEW.hog_row


class TestFind:
    def test_boxes_come_back_in_view_rows_at_their_foot(self):
        frames = [np.zeros((1080, 810, 3), np.uint8)] * 3
        got = broommodel.find(FakeModel(), frames, VIEW)
        top, _ = broommodel.crop_rows(VIEW)
        assert len(got) == 3
        p = got[0][0]
        assert (p.col, p.row) == (395.0, 125.0 + top)     # bottom-centre, lifted
        assert p.conf == pytest.approx(0.9)

    def test_the_model_sees_the_crop_in_bgr_at_800(self):
        m = FakeModel()
        frame = np.zeros((1080, 810, 3), np.uint8)
        frame[..., 0] = 200                                 # red in RGB
        broommodel.find(m, [frame], VIEW)
        n, shape, imgsz, _ = m.fed[0]
        top, bot = broommodel.crop_rows(VIEW)
        assert shape == (bot - top, 810, 3) and imgsz == 800

    def test_a_weak_box_is_dropped(self):
        got = broommodel.find(FakeModel(conf=0.1), [np.zeros((1080, 810, 3), np.uint8)], VIEW)
        assert got == [[]]

    def test_no_frames_no_calls(self):
        m = FakeModel()
        assert broommodel.find(m, [], VIEW) == [] and m.fed == []
