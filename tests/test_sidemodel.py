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


class TestRefusalNamesTheStoneThatMoved:
    """A refusal still has to name a track for the line pass, and the track
    seen in the most frames is not necessarily the one that moved: a stone
    already at rest in a neighbouring sheet's house sits in the band the
    whole window, so it racks up the most samples while barely travelling."""

    def test_the_fallback_key_is_the_track_that_travelled_furthest(self):
        n = 40
        lo = max(0, sidepool.band_crop(VIEW)[0])
        times = [10.0 + i * 0.1 for i in range(n)]
        frames = [np.zeros((1080, 810, 3), np.uint8)] * n
        per = []
        for i in range(n):
            boxes = []
            # Parked in a far column: present every frame, rises 1 px total --
            # the longest track by far, but not a delivery.
            park_row = 480.0 + i / (n - 1)
            pw = VIEW.stone_width_at(park_row, longview.STONE_WIDTH_AT_HOG_PX)
            pcx = 650.0
            boxes.append([pcx - pw / 2, park_row - lo - 30, pcx + pw / 2,
                         park_row - lo, 0, 0.9])
            # Moving, in its own column: only the first 20 frames, rising well
            # short of the hog row (520) -- fewer samples, far more travel.
            if i < 20:
                mv_row = 440.0 + i * (500.0 - 440.0) / 19
                mw = VIEW.stone_width_at(mv_row, longview.STONE_WIDTH_AT_HOG_PX)
                mcx = 100.0
                boxes.append([mcx - mw / 2, mv_row - lo - 30, mcx + mw / 2,
                             mv_row - lo, 0, 0.9])
            per.append(boxes)
        got = sidemodel.find_in_frames(FakeModel(per), frames, VIEW, "red", times)
        assert got.key == longview.KEY_NEVER_REACHED, got.reason
        assert got.track_key == 0          # int(100 // 120): the mover, not the park


class TestDetectBand:
    def test_boxes_come_back_per_frame_in_view_rows_for_the_colour_asked(self):
        frames = [np.zeros((1080, 810, 3), np.uint8)] * 2
        per = [[[100, 10, 150, 40, 0, 0.9], [200, 10, 250, 40, 1, 0.8]], []]
        got = sidemodel.detect_band(FakeModel(per), frames, [0.0, 0.2], 300, 700, "red")
        assert got == [[pytest.approx((125.0, 340.0, 50.0, 0.9))], []]
