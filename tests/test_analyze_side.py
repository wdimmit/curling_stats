"""The side views' lateral calibration, as analyze fits and publishes it."""
import numpy as np

from curling_score import analyze
from curling_score.geometry import sideview
from tests import synth


def _view():
    return sideview.SideView(rect=(0, 0, 810, 1080), tee_row=430.0, hog_row=520.0)


class TestWithLateral:
    def test_a_readable_ring_gives_the_view_its_across_scale(self):
        got = analyze._with_lateral(synth.side_view_house(), _view(), "left",
                                    lambda m: None)
        assert got.has_lateral

    def test_an_unreadable_one_costs_the_view_its_brooms_and_nothing_else(self):
        said = []
        plate = np.full((1080, 810, 3), synth.SIDE_ICE, np.uint8)
        got = analyze._with_lateral(plate, _view(), "left", said.append)
        assert got == _view()                  # depth calibration untouched
        assert said and "left" in said[0]


class TestSideCalibration:
    def test_lateral_fields_are_published_when_fitted(self):
        v = sideview.SideView(rect=(0, 0, 810, 1080), tee_row=429.951,
                              hog_row=520.0, centre_col=390.1234,
                              lat_px_per_m_at_tee=148.5678)
        got = analyze._side_calibration({"left": v})
        assert got == {"left": {"rect": [0, 0, 810, 1080], "tee_row": 429.95,
                                "hog_row": 520.0, "centre_col": 390.12,
                                "lat_px_per_m_at_tee": 148.568}}

    def test_a_depth_only_view_publishes_exactly_what_it_did_before(self):
        got = analyze._side_calibration({"right": _view()})
        assert got == {"right": {"rect": [0, 0, 810, 1080], "tee_row": 430.0,
                                 "hog_row": 520.0}}

    def test_no_side_views_publish_nothing(self):
        assert analyze._side_calibration(None) == {}
