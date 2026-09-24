"""The trained side-view proposer: what it keeps from each box."""
import numpy as np
import pytest

from curling_score.detect import longview, sidemodel
from curling_score.geometry.sideview import SideView
from curling_score.harvest import sidepool

VIEW = SideView(rect=(0, 0, 810, 1080), tee_row=430.0, hog_row=520.0)


class _Arr:
    def __init__(self, a):
        self.a = np.asarray(a, float)

    def cpu(self):
        return self

    def numpy(self):
        return self.a


class _Boxes:
    def __init__(self, rows):
        rows = np.asarray(rows, float).reshape(-1, 6)
        self.xyxy, self.cls, self.conf = _Arr(rows[:, :4]), _Arr(rows[:, 4]), _Arr(rows[:, 5])


class _Res:
    def __init__(self, rows):
        self.boxes = _Boxes(rows)


class FakeModel:
    """Returns, for frame k, the boxes ``per_frame[k]`` as (x0,y0,x1,y1,cls,conf) in CROP rows."""

    def __init__(self, per_frame):
        self.per_frame, self.i = per_frame, 0

    def predict(self, crops, imgsz, conf, verbose):
        out = [_Res(self.per_frame[self.i + k]) for k in range(len(crops))]
        self.i += len(crops)
        return out


def _moving_stone(n=12, cls=0):
    lo = max(0, sidepool.band_crop(VIEW)[0])
    frames = [np.zeros((1080, 810, 3), np.uint8)] * n
    times = [10.0 + i * 0.1 for i in range(n)]
    per = []
    for i in range(n):
        edge = 504.0 + i * 3.3
        w = VIEW.stone_width_at(edge, longview.STONE_WIDTH_AT_HOG_PX)
        cx = 300.0 + 2.0 * i
        per.append([[cx - w / 2, edge - lo - 30, cx + w / 2, edge - lo, cls, 0.9]])
    return frames, times, per


class TestItKeepsTheColumn:
    def test_each_track_entry_carries_the_box_centre(self):
        frames, times, per = _moving_stone()
        tracks = sidemodel.propose(FakeModel(per), frames, VIEW, "red", times)
        (entries,) = tracks.values()
        assert [e[3] for e in entries] == pytest.approx([300.0 + 2.0 * i for i in range(12)])

    def test_the_crossing_carries_every_sample_and_its_key(self):
        frames, times, per = _moving_stone()
        got = sidemodel.find_in_frames(FakeModel(per), frames, VIEW, "red", times)
        assert got.key == longview.KEY_OK, got.reason
        assert got.track_key == 2          # int(300 // 120)
        assert len(got.samples) == 12
        t, cx, row, w = got.samples[0]
        assert (t, cx, row) == pytest.approx((10.0, 300.0, 504.0))


class TestDetectBand:
    def test_boxes_come_back_per_frame_in_view_rows_for_the_colour_asked(self):
        frames = [np.zeros((1080, 810, 3), np.uint8)] * 2
        per = [[[100, 10, 150, 40, 0, 0.9], [200, 10, 250, 40, 1, 0.8]], []]
        got = sidemodel.detect_band(FakeModel(per), frames, [0.0, 0.2], 300, 700, "red")
        assert got == [[pytest.approx((125.0, 340.0, 50.0, 0.9))], []]
