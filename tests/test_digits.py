"""The digit classifier: its augmentation, its training data and the shipped model.

The printed row labels itself, which is most of the training data. Whether the
model generalises is held out by league night in
`scripts/cards2627/gate2627.py`, not here.
"""

import numpy as np
import pytest

from curling_score.game import digits as D
from curling_score.game import scoreboard as SB

CARDS = "datasets/board-cards"
GLYPHS = "datasets/board-glyphs/glyphs.npz"


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


class TestDistinctCards:
    """`distinct_cards`, which `scripts/smoke_reference_cards.py` groups by."""

    def test_the_distinct_cards_are_the_eleven_the_spec_counts(self, cards):
        """69 labelled rows, 11 physical cards, and the board cleared once
        between two games -- which is why one slot holds two of them."""
        groups = D.distinct_cards(cards)
        assert len(groups) == 11, sorted(groups)
        assert sum(len(v) for v in groups.values()) == 69
        assert len({g for g, _, _, _ in groups}) == 2


@pytest.fixture(scope="module")
def shipped_model():
    from pathlib import Path

    if not Path(D.WEIGHTS).is_file():
        pytest.skip(f"{D.WEIGHTS} not trained")
    return D.ConvModel.load(D.WEIGHTS)


class TestShippedModel:
    """The weights `scoreboard.read_digit` actually loads and predicts with:
    `digits.ConvModel`, packaged at `digits.WEIGHTS`, promoted to production
    in Task 6F.

    The 11 reference cards in `datasets/board-cards` were the smoke test for
    transfer through Task 6E, held out of every training run up to and
    including the leave-one-video-out gate. This shipping run does not hold
    them out -- `scripts/train_conv_torch.py`'s default folds them into
    training, because the model that ships should see everything available
    and no fold is held out for it. So the test below is no longer a transfer
    measurement: reading a card the model was trained on is the expected
    outcome, not evidence the model generalises. It is a smoke test that the
    packaged artefact -- this exact `.npz`, loaded the way production loads
    it -- loads and reads correctly, nothing more. An earlier round of this
    work (Task 6A/6B) mistook a number from this same 11-card set, measured
    when it genuinely was held out, for a gate; it is not one here either,
    for the opposite reason.
    """

    def test_the_packaged_weights_load_as_the_conv_model(self, shipped_model):
        assert isinstance(shipped_model, D.ConvModel)

    def test_it_reads_the_reference_cards_with_zero_wrong(
            self, shipped_model, cards):
        wrong = [(meta["frame"], meta["color"], meta["slot"], end, pred)
                 for g, end, meta in cards
                 for pred, _conf in [shipped_model.predict(g)] if pred != end]
        assert wrong == []


class TestTheConvolutionalModel:
    """The escalation named by ruling R11, added in Task 6E.

    Correlation and the MLP failed the same way, on alignment and scale noise.
    An MLP has to learn invariance to a one-pixel shift separately at every
    position; a convolution shares one filter across positions, so the
    invariance is structural. These tests check the numpy read path -- whether
    the model is *good enough* is a measurement, not a test, and it lives in
    `scripts/gate_digits.py` and `scripts/cards2627/gate2627.py`.
    """

    def test_max_pooling_drops_an_odd_edge_rather_than_padding_it(self):
        """22x16 halves to 11x8, and 11 is odd. A padded edge would invent a
        value the glyph does not have, and pooling could then select it."""
        x = np.zeros((1, 1, 11, 8), np.float32)
        x[0, 0, 10, 0] = 99.0          # in the row that gets dropped
        out, _cache = D._maxpool2(x)
        assert out.shape == (1, 1, 5, 4)
        assert float(out.max()) == 0.0

    def test_it_keeps_the_predict_contract(self, shipped_model):
        """`predict` and `predict_many` are what everything downstream
        consumes, so a single read and a batch have to agree."""
        rng = np.random.default_rng(0)
        m = shipped_model
        glyph = rng.normal(size=SB.GLYPH_SHAPE).astype(np.float32)
        digit, conf = m.predict(glyph)
        assert digit in D.LABELS
        assert 0.0 <= conf <= 1.0
        assert m.predict(None) == (None, 0.0)

        batch = np.stack([D.as_input(glyph) for _ in range(4)])
        digits, confs = m.predict_many(batch)
        assert list(digits) == [digit] * 4
        assert confs == pytest.approx([conf] * 4, abs=1e-6)

    def test_a_saved_conv_model_reads_identically(self, shipped_model, tmp_path):
        rng = np.random.default_rng(1)
        m = shipped_model
        path = tmp_path / "conv.npz"
        m.save(path)
        again = D.ConvModel.load(path)
        X = rng.normal(size=(5, D.NFEAT)).astype(np.float32)
        assert np.allclose(m.probs(X), again.probs(X))


class TestTrainingOnTheGpuAndReadingWithNumpy:
    """Training is torch on a GPU; inference is the numpy forward pass that
    ships. Those are two implementations of one function, and this is what
    stops them drifting apart -- a skew would show up as a worse gate number
    and read as "convolution does not help" (ruling R10 as amended: inference
    must be numpy because `curling-score analyze` runs on base dependencies;
    training ships nothing and may use whatever the box has).

    Skips without torch, which is the `gpu` extra and is deliberately absent
    from the venv the suite normally runs in.
    """

    def _trainer(self):
        import importlib.util
        from pathlib import Path

        path = Path(__file__).resolve().parents[1] / "scripts" / "train_conv_torch.py"
        if not path.is_file():
            pytest.skip("scripts/train_conv_torch.py missing")
        spec = importlib.util.spec_from_file_location("train_conv_torch", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_the_numpy_forward_matches_the_torch_one(self):
        torch = pytest.importorskip("torch")
        trainer = self._trainer()
        net = trainer._net(trainer.ARCH, 0.3, torch.device("cpu"))
        model = trainer.to_numpy_model(net, trainer.ARCH)
        # Measured 1.5e-6 on this architecture with TF32 disabled; the
        # tolerance is float32 accumulation order, not a layout that nearly
        # matches. A transposed weight or a flatten in the wrong order lands
        # at order 1, not 1e-6.
        assert trainer.check_equivalence(net, model) < 2e-4

    def test_nothing_the_reader_imports_needs_torch(self):
        """The shipped path must not reach torch even transitively."""
        import subprocess
        import sys

        code = ("import sys; import curling_score.game.digits as D; "
                "D.load_default_model(); "
                "assert 'torch' not in sys.modules; print('ok')")
        out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                             text=True)
        assert out.returncode == 0, out.stderr
        assert "ok" in out.stdout

    def test_read_digit_works_end_to_end_with_no_torch_installed(self):
        """The actual call `curling-score analyze` makes, in a fresh process,
        with no torch importable at all -- not merely unimported. `gpu` is an
        extra, so a base install has no torch on `sys.path`; this simulates
        that by simply never installing it in this venv (see
        `pyproject.toml`), rather than only checking `sys.modules`.
        """
        import subprocess
        import sys

        code = (
            "import sys;"
            "import numpy as np;"
            "from curling_score.game import scoreboard as SB;"
            "from curling_score.game import digits as D;"
            "rng = np.random.default_rng(0);"
            "glyph = rng.normal(size=SB.GLYPH_SHAPE).astype(np.float32);"
            "digit, conf = SB.read_digit(glyph);"
            "assert digit is None or digit in D.LABELS;"
            "assert 0.0 <= conf <= 1.0;"
            "assert 'torch' not in sys.modules;"
            "print('ok')"
        )
        out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                             text=True)
        assert out.returncode == 0, out.stderr
        assert "ok" in out.stdout
