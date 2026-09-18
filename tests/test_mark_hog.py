"""``scripts/mark_hog.py`` is a script, not a package module, so it is loaded
by path rather than imported. It is worth testing anyway: the window it shows a
person is what every hand mark in ``datasets/hogmarks`` was made through, and
those marks are this project's only ground truth for when a stone crossed.
"""

import importlib.util
from pathlib import Path

import pytest

from curling_score.geometry.sideview import SideView

_SPEC = importlib.util.spec_from_file_location(
    "mark_hog", Path(__file__).resolve().parents[1] / "scripts" / "mark_hog.py")
mark_hog = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mark_hog)


def view(hog, rect=(0, 0, 810, 1080)):
    return SideView(rect=rect, tee_row=hog - 85, hog_row=hog)


class TestCropFollowsTheCalibration:
    """The hog line's row moves with the camera -- 520 on VXU9xwmugRg's left
    view against 514 on its right, and other nights differ again -- so a window
    measured on one video can miss the paint entirely on another.
    """

    def test_without_a_view_it_falls_back_to_the_measured_window(self):
        assert mark_hog.crop_for("left") == mark_hog.CROPS["left"]

    def test_with_a_view_the_rows_straddle_that_views_hog_line(self):
        y0, y1, _x0, _x1 = mark_hog.crop_for("left", view(588.0))
        assert y0 < 588 < y1
        assert y1 - y0 == mark_hog.CROP_ROWS_ABOVE + mark_hog.CROP_ROWS_BELOW

    def test_a_different_hog_row_gets_a_different_window(self):
        assert mark_hog.crop_for("left", view(520.0)) != \
               mark_hog.crop_for("left", view(588.0))

    def test_the_columns_stay_inside_the_view(self):
        """The outer thirds are the neighbouring sheets and the wall; a crop
        running past the rect would show a person the wrong sheet's paint."""
        v = view(514.0, rect=(1107, 0, 813, 1080))
        _y0, _y1, x0, x1 = mark_hog.crop_for("right", v)
        assert 1107 <= x0 < x1 <= 1107 + 813

    def test_the_fallback_window_would_have_missed_a_higher_line(self):
        """Why this exists: ds13's own census found fitted hog rows from 560 to
        588 on other videos, and the fallback window stops at 570."""
        y0, y1, _x0, _x1 = mark_hog.CROPS["left"]
        assert not (y0 < 588 < y1)
        a, b, _c, _d = mark_hog.crop_for("left", view(588.0))
        assert a < 588 < b
