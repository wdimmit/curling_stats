"""scripts/broom/recrop_edits.py: a labelled box keeps its pixels on a taller crop."""
import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "broom_recrop", Path(__file__).parents[1] / "scripts" / "broom" / "recrop_edits.py")
recrop = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(recrop)

OLD = {"f": {"crop_top": 299, "width": 810, "height": 184}}
NEW = {"f": {"crop_top": 299, "width": 810, "height": 236}}
EDITS = {"scope": "broom:wave1", "reviewed": ["g"],
         "boxes": {"f": [[0, 0.40, 0.50, 0.04, 0.10]]}}


class TestRestate:
    def test_a_box_keeps_its_pixels(self):
        got = recrop.restate(EDITS, OLD, NEW, "broom:wave1b")["boxes"]["f"][0]
        cls, cx, cy, w, h = got
        assert (cls, cx, w) == (0, 0.40, 0.04)              # across: untouched
        assert cy * 236 == pytest.approx(0.50 * 184, abs=1e-3)   # same row
        assert h * 236 == pytest.approx(0.10 * 184, abs=1e-3)    # same height

    def test_what_was_reviewed_is_carried_not_widened(self):
        out = recrop.restate(EDITS, OLD, NEW, "broom:wave1b")
        assert out["reviewed"] == ["g"] and out["scope"] == "broom:wave1b"

    def test_a_crop_whose_top_moved_is_refused(self):
        moved = {"f": {**NEW["f"], "crop_top": 280}}
        with pytest.raises(ValueError):
            recrop.restate(EDITS, OLD, moved, "broom:wave1b")
