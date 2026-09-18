"""The digit classifier, and the gate it has to clear.

Trained on the printed row, which labels itself. The gate is the real cards it
never saw, because those are what it has to read.
"""

import numpy as np
import pytest

from curling_score.game import digits as D
from curling_score.game import scoreboard as SB

CARDS = "datasets/board-cards"
GLYPHS = "datasets/board-glyphs/glyphs.npz"

# What correlation managed, from the spec's *Why template matching failed*.
CORRELATION_PRINTED = 0.73
CORRELATION_CARDS_CONSISTENT = 0


@pytest.fixture(scope="module")
def model():
    from pathlib import Path

    if not Path(D.WEIGHTS).is_file():
        pytest.skip(f"{D.WEIGHTS} not trained; run scripts/train_digits.py")
    return D.Model.load()


@pytest.fixture(scope="module")
def cards():
    from pathlib import Path

    if not (Path(CARDS) / "labels.json").is_file():
        pytest.skip(f"{CARDS} not harvested")
    return D.card_glyphs(CARDS)


class TestAugmentation:
    """The degradations that separate two frames of the same glyph."""

    def test_it_returns_a_normalised_glyph_of_the_right_shape(self):
        rng = np.random.default_rng(0)
        g = rng.normal(size=SB.GLYPH_SHAPE).astype(np.float32)
        out = D.augment(g, rng)
        assert out.shape == SB.GLYPH_SHAPE
        assert out.dtype == np.float32
        # A glyph reaches the model mean-centred at unit variance, so an
        # augmented copy has to arrive in the same state or the model is being
        # trained on a distribution it never sees at read time.
        assert abs(float(out.mean())) < 1e-4
        assert float(out.std()) == pytest.approx(1.0, abs=1e-3)

    def test_it_actually_changes_the_glyph(self, cards):
        rng = np.random.default_rng(1)
        g = D.as_input(cards[0][0]).reshape(SB.GLYPH_SHAPE)
        copies = np.stack([D.augment(g, rng) for _ in range(32)])
        # Degraded, but still the same digit: no two copies alike, yet every
        # one still strongly correlated with the original.
        assert len(np.unique(copies.round(3), axis=0)) == len(copies)
        corr = [(c * g).mean() for c in copies]
        assert min(corr) > 0.3, min(corr)

    def test_it_never_produces_nan(self):
        rng = np.random.default_rng(2)
        g = np.zeros(SB.GLYPH_SHAPE, np.float32)
        for _ in range(64):
            assert np.isfinite(D.augment(g, rng)).all()


@pytest.fixture(scope="module")
def harvest():
    from pathlib import Path

    if not Path(GLYPHS).is_file():
        pytest.skip(f"{GLYPHS} not harvested")
    with np.load(GLYPHS, allow_pickle=False) as z:
        return z["x"], z["y"].astype(int), z["t_s"]


class TestTheHarvest:
    """The printed row is self-labelling, which is where training data comes
    from at no cost: the digit at slot *k* is *k*."""

    def test_every_label_is_a_digit_a_card_can_carry(self, harvest):
        _, y, _ = harvest
        # Slots 10..14 hold two digits in one slot width, so they are excluded.
        assert set(y.tolist()) == set(D.LABELS)

    def test_the_classes_are_exactly_balanced(self, harvest):
        _, y, _ = harvest
        counts = {k: int((y == k).sum()) for k in D.LABELS}
        # Each frame contributes one glyph per slot, so they must be.
        assert len(set(counts.values())) == 1, counts

    def test_it_holds_thousands_of_clean_glyphs_over_many_frames(self, harvest):
        x, y, t_s = harvest
        assert x.shape[1:] == SB.GLYPH_SHAPE
        assert np.isfinite(x).all()
        assert len(x) >= 1500, len(x)
        assert len(np.unique(t_s)) >= 150, len(np.unique(t_s))

    def test_the_sample_time_travels_with_every_glyph(self, harvest):
        x, _, t_s = harvest
        # Without it a split cannot hold out whole frames, and augmented copies
        # of one physical glyph would land on both sides of it.
        assert len(t_s) == len(x)
        assert len(np.unique(t_s)) * len(D.LABELS) == len(x)


def _printed_holdout():
    from pathlib import Path

    if not Path(GLYPHS).is_file():
        pytest.skip(f"{GLYPHS} not harvested")
    with np.load(GLYPHS, allow_pickle=False) as z:
        x, y, t_s = z["x"], z["y"].astype(int), z["t_s"]
        window_s = float(z["window_s"])
    # The same split the training script used, from the same helper, so this
    # re-measures the shipped weights rather than a lookalike.
    _, val, _, _ = D.frame_split(t_s, window_s)
    return x[val], y[val]


class TestTheDigitModel:
    """Trained on the printed row, which labels itself. The gate is the real
    cards it never saw, because those are what it has to read."""

    def test_it_reads_held_out_printed_digits(self, model):
        """The correlation matcher managed 73% here."""
        x, y = _printed_holdout()
        acc, *_ = D.accuracy(model, x, y)
        # Held out by whole blocks of frames, with a buffer wider than the
        # median-stacking window, so these glyphs share no source frame with
        # anything trained on. Measured 447/450 = 0.9933 on the shipped
        # weights, against correlation's 0.73.
        assert acc > CORRELATION_PRINTED, acc
        assert acc >= 0.98, acc

    def test_it_reads_the_printed_row_of_the_card_frames_too(self, model):
        """The other half of the gate-2 story, and the reason it is damning.

        These are the printed digits *in the 21 card frames themselves*, so
        the same frame, lighting and camera as the cards it misreads.
        Measured 188/189.
        """
        import glob

        import cv2

        from curling_score.game import scoreboard as SB

        frames = sorted(glob.glob(f"{CARDS}/board_t*.png"))
        if not frames:
            pytest.skip(f"{CARDS} not harvested")
        ok = n = 0
        for p in frames:
            img = cv2.imread(p)
            tmpl = SB.templates(img, SB.find_board(img))
            for k in D.LABELS:
                ok += model.predict(tmpl[k])[0] == k
                n += 1
        assert ok / n >= 0.98, f"{ok}/{n}"

    def test_it_beats_correlation_on_the_real_cards(self, model, cards):
        """Correlation read 32 of the 69 labelled rows, 46%.

        Measured 43/69 = 62.3%, so the classifier is better -- but see
        `test_it_reads_every_distinct_real_card`, which is the actual gate and
        which it does not clear.
        """
        ok = sum(1 for g, end, _ in cards if model.predict(g)[0] == end)
        assert ok / len(cards) > 0.46, f"{ok}/{len(cards)}"

    def test_digit_two_is_where_the_transfer_fails(self, model, cards):
        """The failure is one class, near-total, and confident.

        Every labelled card carrying a 2 is misread, 15 of 17 of them as a 7 --
        which is one of the confusions correlation made on cards as well. The
        printed 2 in the very same frames reads correctly, so this is the
        printed-to-card gap and nothing else. Pinned as a test because it is
        the finding the fallback decision rests on: if it ever changes, the
        gate needs re-measuring.
        """
        twos = [(g, e) for g, e, _ in cards if e == 2]
        assert len(twos) == 17, len(twos)
        reads = [model.predict(g)[0] for g, _ in twos]
        assert sum(1 for d in reads if d == 2) == 0, reads
        assert reads.count(7) >= 12, reads

    @pytest.mark.xfail(
        strict=True,
        reason="GATE NOT MET: 4 of 11, measured. Transfer from printed glyphs "
               "to card glyphs is the risk the spec named and it is real -- "
               "digit 2 reads as 7 on every card that carries it, confidently, "
               "while the printed 2 in the same frame reads correctly. The "
               "fallback is hand-labelling real cards across more VODs, which "
               "is the user's call. Delete this marker when that lands; strict "
               "means an unexpected pass fails the suite rather than going "
               "quietly green.",
    )
    def test_it_reads_every_distinct_real_card(self, model, cards):
        """The 11 hand-labelled cards in datasets/board-cards. Correlation read
        0 of 11 correctly in every frame they appear in."""
        groups = D.distinct_cards(cards)
        assert len(groups) == 11, sorted(groups)

        wrong = []
        right = []
        for key, entries in sorted(groups.items()):
            end = key[-1]
            reads = [model.predict(g)[0] for g, _, _ in entries]
            (right if all(d == end for d in reads) else wrong).append(
                (key, reads))
        # Better than correlation's 0 of 11, and not the gate.
        assert len(right) > CORRELATION_CARDS_CONSISTENT, wrong
        # These are held out and must never be trained on, so this is a
        # measurement of transfer from printed glyphs to card glyphs.
        assert len(right) == 11, wrong

    def test_the_distinct_cards_are_the_eleven_the_spec_counts(self, cards):
        """69 labelled rows, 11 physical cards, and the board cleared once
        between two games -- which is why one slot holds two of them."""
        groups = D.distinct_cards(cards)
        assert len(groups) == 11, sorted(groups)
        assert sum(len(v) for v in groups.values()) == 69
        assert len({g for g, _, _, _ in groups}) == 2

    def test_presence_is_refused_before_the_model_is_ever_asked(self, model):
        """54% of empty slots cleared the old correlation threshold, which is
        why presence detection cannot be folded into the digit read."""
        empties = D.empty_slot_glyphs(CARDS)
        assert len(empties) == 519, len(empties)

        reached = [g for g, _ in empties if g is not None]
        # The separation is not the model's at all: no bright card tile in the
        # slot means there is nothing to read, and `_card_glyph` says so first.
        # Measured 501 of 519, so 18 reach the model.
        assert (len(empties) - len(reached)) / len(empties) > 0.95

    def test_confidence_is_lower_on_an_empty_slot_than_on_a_card(self, model):
        """But only barely, which is the point: it cannot carry a threshold.

        Measured medians are 0.946 empty against 0.991 card, and the empty
        distribution reaches 1.0. So presence has to stay with `read_slots`;
        this records that the confidence was measured and found unusable for
        it, rather than leaving the next reader to assume otherwise.
        """
        empties = D.empty_slot_glyphs(CARDS)
        empty_conf = np.asarray([model.predict(g)[1]
                                 for g, _ in empties if g is not None])
        card_conf = np.asarray([model.predict(g)[1]
                                for g, _, _ in D.card_glyphs(CARDS)])
        assert np.median(card_conf) > 0.9, float(np.median(card_conf))
        assert np.median(empty_conf) < np.median(card_conf)
        # Overlapping badly: the most confident empty slot beats the median
        # card, so no threshold on this number separates the two.
        assert empty_conf.max() >= np.median(card_conf)


class TestWeightsRoundTrip:
    def test_the_packaged_weights_load_and_read_a_glyph(self, model, cards):
        digit, conf = model.predict(cards[0][0])
        assert digit in D.LABELS
        assert 0.0 <= conf <= 1.0

    def test_a_saved_model_reads_identically(self, model, cards, tmp_path):
        path = tmp_path / "w.npz"
        model.save(path)
        again = D.Model.load(path)
        X = np.stack([D.as_input(g) for g, _, _ in cards])
        assert np.allclose(model.probs(X), again.probs(X))

    def test_it_needs_no_torch(self):
        import sys

        assert "torch" not in sys.modules
