"""Each panel's calibration also finds its sidelines, and a stone beyond them --
on the next sheet -- is neither counted nor tracked."""

import numpy as np

from curling_score import analyze
from curling_score.detect import cache, sequence
from curling_score.detect.rocks import Detection
from curling_score.game import profile
from curling_score.geometry import sidepaint
from tests.synth_hogline import TOP
from tests.test_sidepaint import left_col, plate

ROW = 500
OFF = left_col(ROW) - 4      # beyond the left line
ON = 150.0                   # on the centre line


def _stone(x_px, y_px=ROW, color="yellow"):
    return Detection(color=color, x_m=0.0, y_m=0.0, x_px=float(x_px), y_px=float(y_px),
                     area_px=300.0, confidence=0.9)


def _setup(**kw):
    return profile.PanelSetup(rect=(0, 0, 300, 534), calib=TOP,
                              sidelines=sidepaint.find_sidelines(plate(), TOP), **kw)


def _calibrate(monkeypatch, img):
    monkeypatch.setattr(profile.lighting, "is_calibratable", lambda p: True)
    monkeypatch.setattr(profile.calibrate, "solve", lambda median, side: TOP)
    return profile.calibrate_panel([img.copy() for _ in range(3)],
                                   (0, 0, img.shape[1], img.shape[0]), "bottom", name="top")


def test_a_panel_finds_its_sidelines_in_its_calibration_median(monkeypatch):
    setup = _calibrate(monkeypatch, plate())
    assert setup.sidelines.left is not None and setup.sidelines.right is not None


def test_a_panel_without_paint_has_lines_that_exclude_nothing(monkeypatch):
    setup = _calibrate(monkeypatch, np.full((534, 300, 3), 225, np.uint8))
    stones = [_stone(OFF), _stone(ON)]
    assert setup.on_sheet(stones) == stones


def test_a_setup_made_without_them_excludes_nothing():
    """A PanelSetup pickled before sidelines existed loads with None."""
    s = profile.PanelSetup(rect=(0, 0, 300, 534), calib=TOP)
    assert s.sidelines is None
    assert s.on_sheet([_stone(OFF)]) == [_stone(OFF)]


def test_on_sheet_keeps_only_the_stones_this_side_of_the_lines():
    assert _setup().on_sheet([_stone(OFF), _stone(ON)]) == [_stone(ON)]


def test_moving_a_panel_into_proxy_coordinates_keeps_its_lines():
    setup = _setup()
    moved = analyze._proxy_setups({"top": setup}, (0, 0, 300, 1060))["top"]
    assert moved.sidelines is setup.sidelines


def test_the_timeline_records_each_panel_s_lines():
    block = analyze.calibration_block({"top": _setup()}, None)
    assert block["top"]["sidelines"]["left"]["side"] == "left"
    assert block["top"]["sidelines"]["missing"] == []


class _Detector:
    """Finds the stones it was built with in every panel."""

    def __init__(self, stones):
        self.stones = stones

    def find_stones_each(self, panels, calibs):
        return [list(self.stones) for _ in panels]

    def find_stones_batch(self, imgs, calib):
        return [list(self.stones) for _ in imgs]


def test_the_profile_does_not_count_a_stone_on_the_next_sheet(monkeypatch):
    monkeypatch.setattr(profile.lighting, "is_playable", lambda panel: True)
    setups = {"top": _setup(), "bottom": _setup()}
    frame = np.zeros((534, 300, 3), np.uint8)
    got = profile.count_frame(frame, setups, _Detector([_stone(OFF), _stone(ON)]))
    assert got == (1, 1, True, True)


def _chunks(path, start_s, end_s, fps, size, crop):
    yield [(0.0, np.zeros((534, 300, 3), np.uint8)), (0.2, np.zeros((534, 300, 3), np.uint8))]


def test_an_end_s_detections_leave_out_the_next_sheet_s_stones(monkeypatch, tmp_path):
    monkeypatch.setattr(sequence.F, "chunked", _chunks)
    monkeypatch.setattr(cache, "cache_dir", lambda: tmp_path)
    det = _Detector([_stone(OFF), _stone(ON)])
    got = sequence.detect_span("v.mp4", _setup(), 0.0, 0.2, 5.0, det)
    assert [[s.x_px for s in stones] for _, stones in got] == [[ON], [ON]]
    # ... while the cache keeps everything the detector saw, so finding a
    # panel's lines never sends a video back through the GPU.
    stored = cache.load(cache.key("v.mp4", _setup().rect, TOP, det, 0.0, 0.2, 0.2))
    assert [len(stones) for _, stones in stored] == [2, 2]
    again = sequence.detect_span("v.mp4", _setup(), 0.0, 0.2, 5.0, det)
    assert [[s.x_px for s in stones] for _, stones in again] == [[ON], [ON]]


def test_a_live_end_s_detections_leave_them_out_too(monkeypatch):
    def windows(path, specs, end_s):
        for i in range(len(specs)):
            yield i, 0.0, np.zeros((534, 300, 3), np.uint8)
    monkeypatch.setattr(sequence.F, "windows", windows)
    det = _Detector([_stone(OFF), _stone(ON)])
    got = sequence.detect_spans("v.ts", [(_setup(), 0.0, 5.0), (_setup(), 0.0, 5.0)], 1.0, det)
    assert [[[s.x_px for s in st] for _, st in seq] for seq in got] == [[[ON]], [[ON]]]
