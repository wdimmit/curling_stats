"""The slot presence model on real slot windows from datasets/slots."""
from pathlib import Path

import numpy as np
import pytest

from curling_score.game import slotmodel as SM

DATA = Path(__file__).resolve().parents[1] / "datasets" / "slots" / "windows.npz"


@pytest.fixture(scope="module")
def window():
    z = np.load(DATA)
    shapes = z["shapes"].astype(int)
    offs = np.concatenate([[0], np.cumsum(shapes[:, 0] * shapes[:, 1])])
    index = {str(i): k for k, i in enumerate(z["ids"])}

    def get(slot_id):
        k = index[slot_id]
        return z["pixels"][offs[k]:offs[k + 1]].reshape(shapes[k])
    return get


class TestTheCardsTheThresholdsMissed:
    """Both were "1"s, and both end-1 cards: the thresholds never saw them."""

    def test_a_thin_one(self, window):
        """Sheet 4's yellow end-1 card, 2026-09-29: 8% ink in the box."""
        assert SM.load_default().p_card(window("w_aFsB4HwUE_003060_y01")) >= SM.MIN_P_CARD

    def test_a_one_hung_high(self, window):
        """09/28 sheet 1's red end-1 card, over the rule: its tile's brightest
        pixels fell short of the old bright test."""
        assert SM.load_default().p_card(window("1xmDI5EkwOU_001260_r01")) >= SM.MIN_P_CARD


class TestWhatIsNoCard:
    def test_a_person_the_digit_reader_calls_a_five(self, window):
        """64% of the box dark, read as a "5" at 0.99992 by `digits`."""
        assert SM.load_default().p_card(window("1xmDI5EkwOU_000360_r14")) < SM.MIN_P_CARD

    def test_an_empty_slot(self, window):
        assert SM.load_default().p_card(window("w_aFsB4HwUE_003060_y05")) < SM.MIN_P_CARD


def test_the_input_is_the_model_shape_whatever_the_window():
    for shape in ((21, 23), (30, 28), (40, 12)):
        assert SM.as_input(np.full(shape, 128, np.uint8)).shape == (SM.CHANNELS, *SM.SLOT_SHAPE)
