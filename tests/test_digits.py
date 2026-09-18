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
    """The MLP frozen as it stood at the end of Task 6B.

    `digits.WEIGHTS` (the packaged `.npz`) holds `digits.ConvModel` now --
    Task 6F promoted it to production -- so the MLP this module's historical
    gate tests measure is no longer the shipped weights and is loaded from a
    frozen copy kept only so those measurements keep meaning what they said
    when they were written. See `TestShippedModel` below for the model
    `scoreboard.read_digit` actually predicts with.
    """
    from pathlib import Path

    path = Path(__file__).parent / "fixtures" / "legacy_mlp_digit_weights.npz"
    if not path.is_file():
        pytest.skip(f"{path} missing")
    return D.Model.load(path)


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
    """Round-tripping the frozen MLP fixture (`model`), not the shipped
    weights -- see `TestShippedModel` for those."""

    def test_the_frozen_mlp_weights_load_and_read_a_glyph(self, model, cards):
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

    def test_it_needs_no_torch(self):
        import sys

        assert "torch" not in sys.modules


class TestTheConvolutionalModel:
    """The escalation named by ruling R11, added in Task 6E.

    Correlation and the MLP failed the same way, on alignment and scale noise.
    An MLP has to learn invariance to a one-pixel shift separately at every
    position; a convolution shares one filter across positions, so the
    invariance is structural. These tests check that the numpy implementation
    of that is correct -- whether it is *enough* is a measurement, not a test,
    and it lives in `scripts/gate_digits.py`.
    """

    def test_im2col_and_col2im_are_adjoint(self):
        """<conv(x), y> == <x, conv^T(y)> -- the identity the backward pass is.

        If the scatter back onto the image is not the exact transpose of the
        gather, every gradient below the first convolution is quietly wrong and
        training still appears to work, just worse. Cheaper to assert than to
        diagnose.
        """
        rng = np.random.default_rng(0)
        x = rng.normal(size=(3, 2, 22, 16))
        k, pad = 3, 1
        cols, oh, ow = D._im2col(x, k, pad)
        g = rng.normal(size=cols.shape)
        back = D._col2im(g, x.shape, k, pad, oh, ow)
        assert float((cols * g).sum()) == pytest.approx(float((x * back).sum()),
                                                        rel=1e-9)

    def test_the_gradients_match_finite_differences(self):
        """Every parameter, against a central difference in float64.

        float32 and a kink-prone ReLU make this check meaningless at single
        precision -- an earlier run of it showed 12% "error" that was entirely
        the step size crossing ReLU kinks -- so the weights are promoted to
        float64 and the step is 1e-6.
        """
        rng = np.random.default_rng(0)
        m = D.ConvModel.initialise(c1=4, c2=6, fc=7, rng=rng)
        m.p = [np.asarray(v, np.float64) for v in m.p]
        n = 6
        X = rng.normal(size=(n, 1, *SB.GLYPH_SHAPE))
        t = rng.integers(0, D.NCLASS, n)

        def loss_and_grads():
            logits, cache = m._forward(X)
            p = D._softmax(logits)
            loss = float(-np.log(p[np.arange(n), t]).sum() / n)
            d = p.copy()
            d[np.arange(n), t] -= 1.0
            return loss, m._backward(cache, d / n)

        _, grads = loss_and_grads()
        eps = 1e-6
        for i, arr in enumerate(m.p):
            flat = arr.reshape(-1)
            for j in rng.choice(flat.size, size=min(6, flat.size),
                                replace=False):
                old = float(flat[j])
                flat[j] = old + eps
                up, _ = loss_and_grads()
                flat[j] = old - eps
                down, _ = loss_and_grads()
                flat[j] = old
                numeric = (up - down) / (2 * eps)
                assert numeric == pytest.approx(
                    float(grads[i].reshape(-1)[j]), abs=1e-5, rel=1e-3)

    def test_max_pooling_drops_an_odd_edge_rather_than_padding_it(self):
        """22x16 halves to 11x8, and 11 is odd. A padded edge would invent a
        value the glyph does not have, and pooling could then select it."""
        x = np.zeros((1, 1, 11, 8), np.float32)
        x[0, 0, 10, 0] = 99.0          # in the row that gets dropped
        out, _cache = D._maxpool2(x)
        assert out.shape == (1, 1, 5, 4)
        assert float(out.max()) == 0.0

    def test_it_keeps_the_predict_contract(self):
        """`predict` and `predict_many` are what everything downstream
        consumes, so the two models have to be interchangeable behind them."""
        rng = np.random.default_rng(0)
        m = D.ConvModel.initialise(rng=rng)
        glyph = rng.normal(size=SB.GLYPH_SHAPE).astype(np.float32)
        digit, conf = m.predict(glyph)
        assert digit in D.LABELS
        assert 0.0 <= conf <= 1.0
        assert m.predict(None) == (None, 0.0)

        batch = np.stack([D.as_input(glyph) for _ in range(4)])
        digits, confs = m.predict_many(batch)
        assert list(digits) == [digit] * 4
        assert confs == pytest.approx([conf] * 4, abs=1e-6)

    def test_it_learns_the_digits_it_is_shown(self):
        """A sanity check on the whole loop, not a measurement of anything.

        Nine printed glyphs, one per class, trained on without augmentation:
        if the forward pass, the backward pass and Adam agree, this is easy,
        and if any of them disagree it is impossible.
        """
        x, y = _printed_holdout()
        one_each = [np.flatnonzero(y == k)[0] for k in D.LABELS]
        xs = np.stack([D.as_input(x[i]) for i in one_each])
        ys = np.asarray([y[i] for i in one_each])
        m = D.ConvModel.initialise(rng=np.random.default_rng(0))
        m.fit(xs, ys, epochs=60, batch=9, rng=np.random.default_rng(0))
        pred, _conf = m.predict_many(xs)
        assert (pred == ys).all(), pred

    def test_a_saved_conv_model_reads_identically(self, tmp_path):
        rng = np.random.default_rng(1)
        m = D.ConvModel.initialise(rng=rng)
        path = tmp_path / "conv.npz"
        m.save(path)
        again = D.ConvModel.load(path)
        X = rng.normal(size=(5, D.NFEAT)).astype(np.float32)
        assert np.allclose(m.probs(X), again.probs(X))

    def test_the_two_model_files_do_not_load_as_each_other(self, tmp_path):
        """A conv `.npz` and an MLP `.npz` both end in .npz and both hold
        `labels`, so refusing the wrong one explicitly is the difference
        between an error and a silently wrong reader."""
        conv = tmp_path / "conv.npz"
        D.ConvModel.initialise(rng=np.random.default_rng(0)).save(conv)
        with pytest.raises(Exception):
            D.Model.load(conv)
        mlp = tmp_path / "mlp.npz"
        D.Model.initialise(rng=np.random.default_rng(0)).save(mlp)
        with pytest.raises(Exception):
            D.ConvModel.load(mlp)


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
                "D.ConvModel.initialise(); "
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
