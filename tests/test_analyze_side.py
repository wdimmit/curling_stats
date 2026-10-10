"""The side views' lateral calibration, as analyze fits and publishes it."""
import dataclasses

import numpy as np
import pytest

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


class TestTheStripChecksTheRing:
    """The 4 ft strip down the sheet (geometry/sideview.solve_strip): the ring's
    fit stands where the strip agrees, and the strip's replaces it where it
    does not, or where the ring could not be read."""

    def lateral(self, lat):
        return dataclasses.replace(_view(), centre_col=390.0, lat_px_per_m_at_tee=lat)

    def test_a_ring_the_strip_agrees_with_stands(self):
        plate = synth.side_view_strip(synth.side_view_house(lat_px_per_m=148.0),
                                      self.lateral(148.0))
        said = []
        got = analyze._with_lateral(plate, _view(), "left", said.append)
        ring = sideview.solve_centre_line(plate, sideview.solve_lateral(plate, _view()))
        assert got == ring and not said

    def test_a_ring_the_strip_disagrees_with_gives_way_to_it(self):
        # The house reads 148 px/m but the strip, all down the sheet, 175.
        plate = synth.side_view_strip(synth.side_view_house(lat_px_per_m=148.0),
                                      self.lateral(175.0))
        said = []
        got = analyze._with_lateral(plate, _view(), "left", said.append)
        assert got.lat_px_per_m_at_tee == pytest.approx(175.0, rel=0.02)
        assert any("strip" in m and "left" in m for m in said)

    def test_a_ring_that_could_not_be_read_is_replaced_by_the_strip(self):
        plate = synth.side_view_strip(np.full((1080, 810, 3), synth.SIDE_ICE, np.uint8),
                                      self.lateral(148.0))
        said = []
        got = analyze._with_lateral(plate, _view(), "left", said.append)
        assert got.has_lateral and got.lat_px_per_m_at_tee == pytest.approx(148.0, rel=0.02)
        assert any("strip" in m for m in said)


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
