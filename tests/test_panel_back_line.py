"""Each panel's calibration also finds its back line, and a stone wholly
beyond it -- out of play -- is not counted in the activity profile, though it
is still tracked."""

import numpy as np

from curling_score import analyze
from curling_score.detect.rocks import Detection
from curling_score.game import profile
from tests.test_backpaint import TOP, plate

PAST = (150.0, 4.0, 256.0)       # wholly past the paint at row 14
ON = (150.0, 200.0, 400.0)       # in the house


def _stone(x_px, y_px, area_px, color="yellow"):
    return Detection(color=color, x_m=0.0, y_m=0.0, x_px=x_px, y_px=y_px,
                     area_px=area_px, confidence=0.9)


def _calibrate(monkeypatch, img):
    monkeypatch.setattr(profile.lighting, "is_calibratable", lambda p: True)
    monkeypatch.setattr(profile.calibrate, "solve", lambda median, side: TOP)
    return profile.calibrate_panel([img.copy() for _ in range(3)],
                                   (0, 0, img.shape[1], img.shape[0]), "bottom", name="top")


def test_a_panel_finds_its_back_line_in_its_calibration_median(monkeypatch):
    setup = _calibrate(monkeypatch, plate())
    assert setup.back_line is not None and setup.back_line_error is None


def test_a_panel_without_paint_says_why_and_excludes_nothing(monkeypatch):
    setup = _calibrate(monkeypatch, np.full((534, 300, 3), 225, np.uint8))
    assert setup.back_line is None and setup.back_line_error.startswith("top panel: ")
    stones = [_stone(*PAST), _stone(*ON)]
    assert setup.in_play(stones) == stones


def test_a_setup_made_without_one_excludes_nothing():
    """A PanelSetup pickled before back lines existed loads with None."""
    s = profile.PanelSetup(rect=(0, 0, 300, 534), calib=TOP)
    assert s.back_line is None
    assert s.in_play([_stone(*PAST)]) == [_stone(*PAST)]


def test_in_play_drops_a_stone_wholly_past_the_line_and_on_sheet_keeps_it(monkeypatch):
    setup = _calibrate(monkeypatch, plate())
    stones = [_stone(*PAST), _stone(*ON)]
    assert setup.in_play(stones) == [_stone(*ON)]
    # Tracking reads on_sheet, and still follows a stone through the line.
    assert setup.on_sheet(stones) == stones


def test_the_profile_does_not_count_it(monkeypatch):
    setup = _calibrate(monkeypatch, plate())

    class Detector:
        def find_stones_each(self, panels, calibs):
            return [[_stone(*PAST), _stone(*ON)] for _ in panels]

    monkeypatch.setattr(profile.lighting, "is_playable", lambda panel: True)
    frame = np.zeros((534, 300, 3), np.uint8)
    top, bottom, _, _ = profile.count_frame(frame, {"top": setup, "bottom": setup}, Detector())
    assert (top, bottom) == (1, 1)


def test_moving_a_panel_into_proxy_coordinates_keeps_its_line(monkeypatch):
    setup = _calibrate(monkeypatch, plate())
    moved = analyze._proxy_setups({"top": setup}, (0, 0, 300, 1060))["top"]
    assert moved.back_line is setup.back_line


def test_the_timeline_records_each_panel_s_line(monkeypatch):
    setup = _calibrate(monkeypatch, plate())
    block = analyze.calibration_block({"top": setup}, None)
    assert block["top"]["back_line"]["columns"] == setup.back_line.columns
    assert block["top"]["back_line_error"] is None
