import numpy as np
import pytest

from curling_score.geometry import lighting


def panel(value):
    """A uniform BGR panel at the given brightness."""
    return np.full((514, 297, 3), value, dtype=np.uint8)


class TestClassify:
    def test_a_black_panel_is_dark(self):
        assert lighting.classify(panel(3)) is lighting.Lighting.DARK

    def test_a_normally_lit_panel_is_lit(self):
        assert lighting.classify(panel(200)) is lighting.Lighting.LIT

    def test_a_partially_lit_panel_is_dim(self):
        assert lighting.classify(panel(120)) is lighting.Lighting.DIM


class TestUsability:
    def test_a_dark_panel_is_not_playable(self):
        assert lighting.is_playable(panel(3)) is False

    def test_a_dim_panel_is_still_playable(self):
        # The house is visible, just poorly lit; activity can still be read.
        assert lighting.is_playable(panel(120)) is True

    def test_only_a_well_lit_panel_may_be_calibrated_from(self):
        assert lighting.is_calibratable(panel(200)) is True
        assert lighting.is_calibratable(panel(120)) is False
        assert lighting.is_calibratable(panel(3)) is False


class TestOnRealFootage:
    def test_lights_out_panel_is_dark_not_an_empty_house(self, known_frame):
        # Regression: Sheet 4 at t=9000 has its bottom-end lights off. Reporting
        # this as an empty house would invent a blank end that never happened.
        img = known_frame("sheet4_t9000_lightsout.png")
        bottom = img[554:1070, 813:1107]
        assert lighting.classify(bottom) is lighting.Lighting.DARK
        assert lighting.is_playable(bottom) is False

    def test_dim_panel_is_recognised_as_unfit_for_calibration(self, known_frame):
        img = known_frame("sheet4_t9000_lightsout.png")
        top = img[10:537, 813:1107]
        assert lighting.classify(top) is lighting.Lighting.DIM
        assert lighting.is_playable(top) is True
        assert lighting.is_calibratable(top) is False

    def test_normal_play_panels_are_lit(self, known_frame):
        img = known_frame("sheet1_t9000_cluster9.png")
        assert lighting.is_calibratable(img[11:521, 812:1110]) is True
        assert lighting.is_calibratable(img[554:1070, 812:1110]) is True
