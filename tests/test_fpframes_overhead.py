"""The overhead checks and the end's lifecycle feed the recorder.

``find_deliveries`` notes the tracks it rejects as wandering or sideways, and
``build_one_end`` opens an end, notes the strays between its kept rocks, and
closes it with the panel's rect in the original video's coordinates.
"""

import json

import pytest

from curling_score import analyze
from curling_score.detect import delivery
from curling_score.detect.rocks import Detection
from curling_score.fpframes.record import Recorder, recording
from curling_score.game import format as format_mod
from curling_score.game.profile import PanelSetup
from curling_score.game.segment import EndSegment, GameSegment
from curling_score.geometry.calibrate import PanelCalib
from tests.test_delivery import det, merge, thrown

OFFSET = (807, 554)


def recorder():
    return Recorder("vid", models={"overhead": "o-1", "side": None},
                    pipeline="2026.10.21.1")


def wander_frames():
    # Like test_delivery's loitering blob, but it sweeps back and forth over
    # 0.4 m of sheet and stops 0.4 m below where it began: enough net travel
    # to be judged as a flight, a small share of the path it covered.
    wander = []
    for i in range(8 * 15 + 5):
        k = i % 8
        y = 4.5 - 0.1 * (k if k <= 4 else 8 - k)
        wander.append((i / 5.0, det("yellow", 0.45, y)))
    return merge(wander)


def sideways_frames():
    # Moves a metre down the sheet but two across it.
    trace = [(i / 5.0, det("red", -1.0 + 2.0 * i / 9, 6.0 - i / 9))
             for i in range(10)]
    return merge(trace)


class TestFindDeliveries:
    def test_a_wandering_blob_is_a_wander_firing(self):
        rec = recorder()
        with recording(rec):
            assert delivery.find_deliveries(wander_frames()) == []
        assert [f.check for f in rec.pending()] == ["ovh.wander"]
        assert rec.pending()[0].color == "yellow"

    def test_a_track_moving_sideways_is_a_lateral_firing(self):
        rec = recorder()
        with recording(rec):
            assert delivery.find_deliveries(sideways_frames()) == []
        assert [f.check for f in rec.pending()] == ["ovh.lateral"]
        assert rec.pending()[0].color == "red"

    def test_nothing_is_recorded_without_a_recorder(self):
        assert delivery.find_deliveries(wander_frames()) == []
        assert delivery.find_deliveries(sideways_frames()) == []
        assert delivery.find_deliveries(merge(thrown("red")))[0].color == "red"


def panel(flipped, origin=(0, 0)):
    return PanelSetup(rect=(origin[0], origin[1], 297, 514),
                      calib=PanelCalib(center_px=(150.0, 160.0), px_per_m=76.0,
                                       edge_erosion_px=2.0, residual_m=0.001,
                                       flipped=flipped))


def setups(origin=(0, 0)):
    return {"top": panel(False, origin), "bottom": panel(True, origin)}


GAME = GameSegment(index=0, start_s=1000.0, end_s=3000.0, ends=[
    EndSegment(number=1, house="top", start_s=1000.0, end_s=1900.0)])


def stone(color, x, t0, t1):
    """A stone held in place from t0 to t1 on every frame."""
    return [(round(t0 + i * 0.2, 3), det(color, x, -0.9))
            for i in range(int((t1 - t0) * 5) + 1)]


def end_frames():
    """Two kept rocks and one stray red box between them."""
    red = thrown("red", t0=1100.0, x=0.2)
    yellow = thrown("yellow", t0=1160.0, x=-0.6)
    # `thrown` rests for 30 s only; keep both held to the end of the span.
    held = stone("red", 0.2, 1140.0, 1300.0) + stone("yellow", -0.6, 1190.0, 1300.0)
    stray = [(1135.0 + i * 0.2,
              Detection(color="red", x_m=1.5, y_m=1.0, x_px=40, y_px=60,
                        area_px=400, confidence=0.7))
             for i in range(6)]
    return merge(red, yellow, held, stray)


@pytest.fixture
def detected(monkeypatch):
    frames = end_frames()
    monkeypatch.setattr(analyze.sequence, "detect_end",
                        lambda *a, **k: frames)
    monkeypatch.setattr(analyze.sequence, "detect_span", lambda *a, **k: [])


def context(frame_setups=None):
    return analyze.EndContext(
        path="original.mp4", read_path="proxy.mp4", read_setups=setups(),
        sideviews=None, detector=None, broom_model=None, line_model=None,
        fmt=format_mod.FOURS, frame_setups=frame_setups)


def build(ctx):
    return analyze.build_one_end(ctx, GAME, GAME.ends[0], prev_end_s=None,
                                 board_score=None)[0]


def quiet(rec):
    return [f for f in rec.firings() if f.check == "ovh.quiet"]


class TestTheEndsLifecycle:
    def test_a_stray_box_between_two_kept_rocks_is_a_quiet_firing(self, detected):
        rec = recorder()
        with recording(rec):
            build(context())
        got = quiet(rec)
        assert len(got) == 1
        f = got[0]
        assert f.t == pytest.approx(1135.5, abs=0.6)
        cx, cy = (f.box[0] + f.box[2]) / 2, (f.box[1] + f.box[3]) / 2
        assert (cx, cy) == (40, 60)
        assert (f.game, f.end) == (0, 1)
        assert f.info["quiet"][0] < f.t < f.info["quiet"][1]

    def test_the_end_is_byte_identical_with_and_without_a_recorder(self, detected):
        plain = build(context())
        rec = recorder()
        with recording(rec):
            recorded = build(context())
        assert json.dumps(recorded, sort_keys=True) == json.dumps(plain, sort_keys=True)
        assert quiet(rec)

    def test_frame_setups_place_the_box_in_the_original(self, detected):
        rec = recorder()
        with recording(rec):
            build(context(frame_setups=setups(OFFSET)))
        f = quiet(rec)[0]
        cx, cy = (f.box[0] + f.box[2]) / 2, (f.box[1] + f.box[3]) / 2
        assert (cx, cy) == (40 + OFFSET[0], 60 + OFFSET[1])
