"""A sheet's saved calibration, and the check that it still fits what the cameras see."""

import logging

import numpy as np
import pytest

from curling_score.cameras import calstore
from curling_score.cameras.calstore import HouseCheck
from curling_score.game.profile import PANEL_SIDES, PanelSetup
from curling_score.geometry import calibrate
from curling_score.live.session import Calibration
from tests import synth

# Two house panels side by side in one frame, as the composite lays them out.
TOP_RECT, BOTTOM_RECT = (20, 10, 297, 514), (360, 10, 297, 514)
FRAME_W, FRAME_H = 700, 540


def frame(top=None, bottom=None, stone=None):
    """A composite frame holding a house in each panel; ``stone`` (x, y) is
    painted on the top panel, as a sweeper or a rock would be."""
    top = synth.house_panel(**(top or {}))
    bottom = synth.house_panel(**(bottom or {"cx": 148.0, "cy": 360.0}))
    if stone is not None:
        top = synth.with_stone(top, *stone)
    out = np.full((FRAME_H, FRAME_W, 3), 235, np.uint8)
    for (x, y, w, h), panel in ((TOP_RECT, top), (BOTTOM_RECT, bottom)):
        out[y:y + h, x:x + w] = panel
    return out


def calibration(frames=None) -> Calibration:
    """A calibration made from ``frames`` the way the pipeline makes one."""
    frames = frames or [frame()]
    rects = {"top": TOP_RECT, "bottom": BOTTOM_RECT}
    setups = {}
    for name, side in PANEL_SIDES:
        x, y, w, h = rects[name]
        calib = calibrate.solve(frames[0][y:y + h, x:x + w], side)
        setups[name] = PanelSetup(rect=rects[name], calib=calib)
    return Calibration(panels=None, setups=setups, sideviews=None, until_s=60.0,
                       side_expected=False)


# --- the store -----------------------------------------------------------------


def test_calstore_roundtrip(tmp_path):
    store, cal = calstore.CalStore(tmp_path), calibration()
    store.save(3, cal)
    assert (tmp_path / "cameras" / "cal" / "sheet3.pkl").is_file()
    assert store.load(3) == cal
    assert store.load(2) is None                   # each sheet has its own


def test_calstore_missing_is_none(tmp_path):
    assert calstore.CalStore(tmp_path).load(1) is None


def test_a_save_replaces_the_one_before_and_leaves_nothing_else_behind(tmp_path):
    store = calstore.CalStore(tmp_path)
    store.save(1, calibration())
    newer = calibration()
    newer.until_s = 99.0
    store.save(1, newer)
    assert store.load(1).until_s == 99.0
    assert [p.name for p in (tmp_path / "cameras" / "cal").iterdir()] == ["sheet1.pkl"]


@pytest.mark.parametrize("content", [b"", b"not a pickle", b"\x80\x04N."])
def test_an_unreadable_file_is_none_with_a_warning(tmp_path, caplog, content):
    # b"\x80\x04N." is a good pickle of None: readable, but not a calibration.
    path = tmp_path / "cameras" / "cal" / "sheet1.pkl"
    path.parent.mkdir(parents=True)
    path.write_bytes(content)
    with caplog.at_level(logging.WARNING, logger=calstore.__name__):
        assert calstore.CalStore(tmp_path).load(1) is None
    assert "sheet1.pkl" in caplog.text


# --- the check -----------------------------------------------------------------


def test_the_house_agrees_on_the_panels_it_was_made_from():
    frames = [frame()]
    assert calstore.check_house(calibration(frames), frames) is HouseCheck.AGREES


def test_the_check_reads_the_median_not_one_frame():
    # A stone on the house in each frame, in a different place: the median
    # has none, so the rings are read whole.
    frames = [frame(stone=at) for at in ((100, 100), (180, 200), (150, 60))]
    assert calstore.check_house(calibration(), frames) is HouseCheck.AGREES


def test_the_house_agrees_within_the_ring_tolerance():
    moved = [frame(top={"cx": 148.0 + 3.0})]
    assert calstore.check_house(calibration(), moved) is HouseCheck.AGREES


def test_rings_moved_8px_disagree():
    moved = [frame(top={"cx": 148.0 + 8.0})]
    assert calstore.check_house(calibration(), moved) is HouseCheck.DISAGREES


def test_both_panels_must_agree():
    moved = [frame(bottom={"cx": 148.0, "cy": 360.0 + 8.0})]
    assert calstore.check_house(calibration(), moved) is HouseCheck.DISAGREES


def test_a_changed_scale_disagrees():
    zoomed = [frame(top={"px_per_m": 75.0 * 1.06})]
    assert calstore.check_house(calibration(), zoomed) is HouseCheck.DISAGREES


def test_no_frames_cannot_be_read():
    assert calstore.check_house(calibration(), []) is HouseCheck.UNREADABLE


def test_no_house_in_view_cannot_be_read():
    # Unreadable is not a disagreement: players standing in the house hide its
    # rings, and say nothing about where the camera points.
    blank = np.full((FRAME_H, FRAME_W, 3), 235, np.uint8)
    assert calstore.check_house(calibration(), [blank, blank]) is HouseCheck.UNREADABLE


def test_one_house_hidden_and_the_other_fitting_cannot_be_read():
    hidden = frame()
    hidden[TOP_RECT[1]:TOP_RECT[1] + TOP_RECT[3], TOP_RECT[0]:TOP_RECT[0] + TOP_RECT[2]] = 235
    assert calstore.check_house(calibration(), [hidden]) is HouseCheck.UNREADABLE


def test_one_house_hidden_and_the_other_moved_disagrees():
    # A ring found off is a camera that moved, whatever the other panel shows.
    moved = frame(bottom={"cx": 148.0, "cy": 360.0 + 8.0})
    moved[TOP_RECT[1]:TOP_RECT[1] + TOP_RECT[3], TOP_RECT[0]:TOP_RECT[0] + TOP_RECT[2]] = 235
    assert calstore.check_house(calibration(), [moved]) is HouseCheck.DISAGREES
