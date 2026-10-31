"""The long-camera checks feed the recorder, and change nothing they decide.

A box of the wrong width at its row is how a frame-sized or half-sized false
positive shows; a path start with no second sighting is how a lone one does.
"""

from types import SimpleNamespace

import pytest

from curling_score.detect import longview, sidemodel
from curling_score.fpframes.record import Recorder, recording
from curling_score.game import farfollow as FF
from curling_score.game import linetime as L
from curling_score.game import sidereleases as S
from curling_score.geometry import constants as C
from curling_score.geometry.sideview import band_crop
from tests import test_farfollow as FFT
from tests import test_sidereleases
from tests import test_linetime as LT
from tests.test_linetime import DEST_VIEW
from tests.test_sidemodel import VIEW, FakeModel, _moving_stone


def recorder():
    return Recorder("vid", models={"overhead": None, "side": "s-1"},
                    pipeline="2026.10.21.1")


def only(rec, check):
    return [f for f in rec.pending() if f.check == check]


def test_a_frame_sized_box_at_the_hog_is_a_far_width_firing():
    frames, times, per = _moving_stone(n=1)
    x0, y0, x1, y1, cls, conf = per[0][0]
    w = x1 - x0
    per[0][0] = [x0 - 2.5 * w, y0, x1 + 2.5 * w, y1, cls, conf]      # 6x as wide
    rec = recorder()
    with recording(rec):
        tracks = sidemodel.propose(FakeModel(per), frames, VIEW, "red", times)
    assert tracks == {}
    (f,) = only(rec, "side.width_far")
    assert f.info["pass"] == "hog" and f.color == "red"
    assert f.info["ratio"] == pytest.approx(6.0, rel=0.01)
    expect = VIEW.stone_width_at(f.box[3], longview.STONE_WIDTH_AT_HOG_PX)
    assert f.box[2] - f.box[0] == pytest.approx(6.0 * expect, rel=0.01)
    assert f.top_estimated is False
    assert len(rec.pending()) == 1


def test_an_unchecked_box_at_the_hog_records_nothing_and_proposes_the_same():
    frames, times, per = _moving_stone()
    rec = recorder()
    with recording(rec):
        got = sidemodel.propose(FakeModel(per), frames, VIEW, "red", times)
    assert got == sidemodel.propose(FakeModel(_moving_stone()[2]), frames, VIEW, "red", times)
    assert rec.pending() == []


def test_a_half_width_box_on_the_path_is_an_off_width_firing():
    row = 700.0
    w = 0.55 * 0.291 * DEST_VIEW.lateral_px_per_m(row)
    rec = recorder()
    with recording(rec):
        got = L.path_points([[(400.0, row, w, 0.8)]], [1.0], DEST_VIEW)
    assert got == [[]]
    (f,) = only(rec, "side.width_off")
    assert f.info["pass"] == "path" and f.color is None and f.top_estimated is True
    assert f.box == pytest.approx((400.0 - w / 2, row - w / 2, 400.0 + w / 2, row))
    assert f.info["ratio"] == pytest.approx(0.55)


def test_a_box_of_the_right_width_on_the_path_records_nothing():
    row = 700.0
    w = 0.291 * DEST_VIEW.lateral_px_per_m(row)
    rec = recorder()
    with recording(rec):
        got = L.path_points([[(400.0, row, w, 0.8)]], [1.0], DEST_VIEW)
    assert len(got[0]) == 1 and rec.pending() == []


def test_a_lone_start_is_a_lone_firing():
    times, per = LT.TestChain().frames(stray_first=True)
    plain = L.chain(times, per, LT.TestChain.FIT)
    rec = recorder()
    with recording(rec), rec.side_view(DEST_VIEW):
        got = L.chain(times, per, LT.TestChain.FIT)
    assert got == plain and got[0][0] > 22.0
    (f,) = only(rec, "side.lone")
    assert f.t == 0.0 and f.box is not None and len(rec.pending()) == 1


def test_a_lone_start_outside_a_view_records_nothing():
    times, per = LT.TestChain().frames(stray_first=True)
    rec = recorder()
    with recording(rec):
        L.chain(times, per, LT.TestChain.FIT)
    assert rec.pending() == []


def test_find_path_hands_the_chain_its_view(monkeypatch):
    seen = []

    def chain(times, per, fit, others=None):
        seen.append(rec._view)
        return []

    monkeypatch.setattr(L, "chain", chain)

    def decode(video, rect, t0, t1, fps):
        return [object()] * 3, [t0, t0 + 0.2, t0 + 0.4]

    def detect(model, frames, times, lo, hi, colors, imgsz=800, conf=0.35):
        return [[[] for _ in frames] for _ in colors]

    rec = recorder()
    with recording(rec):
        L.find_path(object(), "v.mp4", DEST_VIEW, "yellow", 100.0, 120.0,
                    LT.TestChain.FIT, decode=decode, detect=detect)
    assert seen == [DEST_VIEW] and rec._view is None


def wide_follow_inputs():
    """A stone sliding in and resting a metre past the tee, and in one frame a
    box 2.5 times too wide for its row."""
    v = test_sidereleases.view()
    path = FFT.hold(FFT.slide(1.0, start_y=8.0, start_at=FFT.T0 + 6.0,
                              stop_at=FFT.T0 + 20.0), FFT.T0 + 45.0)
    times = [t for t, _x, _y in path]
    boxes = []
    for i, (_t, _x, y) in enumerate(path):
        row = v.row_for(y)
        expect = v.stone_width_at(row, longview.STONE_WIDTH_AT_HOG_PX)
        w = 2.5 * expect if i == 3 else expect
        boxes.append([(v.centre_col_at(row), row, w, 0.9)])

    def decode(video, rect, t0, t1, fps):
        return [object()] * len(times), list(times)

    def detect(model, frames, ts, lo, hi, color, imgsz=800, conf=0.3):
        return boxes

    return v, decode, detect, boxes[3][0][1]


def test_the_follow_records_a_wide_box_and_still_returns_the_same_outcome():
    v, decode, detect, _row = wide_follow_inputs()
    plain = FF.follow("v.mp4", v, "red", FFT.T0, model=object(), decode=decode, detect=detect)
    rec = recorder()
    with recording(rec):
        got = FF.follow("v.mp4", v, "red", FFT.T0, model=object(), decode=decode, detect=detect)
    assert got == plain and got.kind == FF.REST
    (f,) = only(rec, "side.width_far")
    assert f.info["pass"] == "follow" and f.color == "red"
    assert f.info["ratio"] == pytest.approx(2.5)
    assert len(rec.pending()) == 1


def test_the_follow_skips_a_wide_box_on_the_edge_rows():
    v, decode, detect, _row = wide_follow_inputs()
    hi = min(int(v.rect[3]), int(v.hog_row) + FF.ROWS_BELOW_HOG)
    edge = float(hi - 1)
    wide = 2.5 * v.stone_width_at(edge, longview.STONE_WIDTH_AT_HOG_PX)

    def detect_edge(model, frames, ts, lo, hi_, color, imgsz=800, conf=0.3):
        return [[(v.centre_col_at(edge), edge, wide, 0.9)] for _ in frames]

    rec = recorder()
    with recording(rec):
        FF.follow("v.mp4", v, "red", FFT.T0, model=object(), decode=decode, detect=detect_edge)
    assert rec.pending() == []


def test_stone_points_records_a_wide_box_without_changing_its_points():
    v = test_sidereleases.view()
    row = v.row_for(-1.0 + C.STONE_RADIUS_M)
    expect = v.stone_width_at(row, longview.STONE_WIDTH_AT_HOG_PX)
    per = [[(v.centre_col_at(row), row, expect, 0.9)],
           [(v.centre_col_at(row), row, 2.5 * expect, 0.9)]]
    plain = S.stone_points(v, [10.0, 10.1], per)
    rec = recorder()
    with recording(rec):
        got = S.stone_points(v, [10.0, 10.1], per)
    assert got == plain and len(got) == 2
    (f,) = only(rec, "side.width_far")
    assert f.info["pass"] == "slide" and f.color is None and f.t == 10.1
    assert len(rec.pending()) == 1
