"""scripts/broom/eval_heldout.py: the bar is per shot, against the user's boxes."""
import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "broom_eval", Path(__file__).parents[1] / "scripts" / "broom" / "eval_heldout.py")
ev = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ev)

ROW = {"rect": [0, 0, 810, 1080], "tee_row": 430.0, "hog_row": 520.0,
       "centre_col": 390.0, "lat_px_per_m_at_tee": 148.0, "crop_top": 300,
       "width": 810, "height": 235}


class TestTruth:
    def test_a_box_s_foot_on_the_tee_row_at_the_centre_is_the_tee(self):
        foot = (430.0 - 300) / 235
        box = [0, 390 / 810, foot - 0.02, 0.03, 0.04]      # bottom = cy + h/2 = foot
        (x, y), = ev.truth(ROW, [box])
        assert (x, y) == pytest.approx((0.0, 0.0), abs=0.01)

    def test_no_boxes_no_truth(self):
        assert ev.truth(ROW, []) == []


class TestScore:
    def test_the_bar_counts_hits_within_a_stone_width(self):
        results = [([(0.0, 0.0)], (0.1, 0.1)),        # hit (0.14 m)
                   ([(0.0, 0.0)], (0.5, 0.0)),        # miss (0.5 m), not wild
                   ([(0.0, 0.0)], None),              # no call
                   ([], (1.0, 1.0)),                  # a call where no pad was boxed
                   ([(0.0, 0.0)], (0.9, 0.0))]        # wild (0.9 m)
        got = ev.score(results)
        assert got["with_pad"] == 4 and got["hits"] == 1
        assert got["markers"] == 4 and got["wild"] == 2   # >0.60 m off, or no pad

    def test_a_hit_on_either_of_two_boxed_pads_counts(self):
        got = ev.score([([(2.0, 0.9), (0.5, 0.3)], (0.5, 0.35))])
        assert got["hits"] == 1 and got["wild"] == 0
