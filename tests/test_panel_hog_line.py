"""Each panel's calibration also finds its hog line, and nothing downstream drops it."""

import numpy as np

from curling_score import analyze
from curling_score.game import profile
from tests.synth_hogline import TOP, plate_with_line


def _calibrate(monkeypatch, plate):
    monkeypatch.setattr(profile.lighting, "is_calibratable", lambda p: True)
    monkeypatch.setattr(profile.calibrate, "solve", lambda median, side: TOP)
    frames = [plate.copy() for _ in range(3)]
    return profile.calibrate_panel(frames, (0, 0, plate.shape[1], plate.shape[0]),
                                   "bottom", name="top")


def test_a_panel_whose_paint_is_found_carries_its_line(monkeypatch):
    setup = _calibrate(monkeypatch, plate_with_line())
    assert setup.hog_line is not None
    assert setup.hog_line_error is None
    assert setup.hog_line.calib == TOP


def test_a_panel_whose_paint_is_not_found_says_why_and_still_calibrates(monkeypatch):
    setup = _calibrate(monkeypatch, np.full((534, 300, 3), 225, np.uint8))
    assert setup.calib == TOP
    assert setup.hog_line is None
    assert setup.hog_line_error.startswith("top panel:")
    assert "columns" in setup.hog_line_error


def test_a_setup_made_without_one_defaults_to_none():
    """A PanelSetup pickled before hog lines existed loads with these defaults."""
    s = profile.PanelSetup(rect=(0, 0, 300, 534), calib=TOP)
    assert s.hog_line is None and s.hog_line_error is None


def test_moving_a_panel_into_proxy_coordinates_keeps_its_line(monkeypatch):
    """analyze._proxy_setups rebuilds each PanelSetup; dropping the line there
    would silently leave every proxy-read panel with no far tripwire."""
    setup = _calibrate(monkeypatch, plate_with_line())
    moved = analyze._proxy_setups({"top": setup}, (0, 0, 300, 1060))["top"]
    assert moved.hog_line is setup.hog_line
    assert moved.hog_line_error == setup.hog_line_error


def test_panel_median_is_the_median_of_the_lit_frames(monkeypatch):
    monkeypatch.setattr(profile.lighting, "is_calibratable", lambda p: True)
    a = np.zeros((4, 4, 3), np.uint8)
    b = np.full((4, 4, 3), 100, np.uint8)
    got = profile.panel_median([a, b, b], (0, 0, 4, 4))
    assert got.dtype == np.uint8 and int(got[0, 0, 0]) == 100
