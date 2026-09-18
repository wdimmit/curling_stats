"""Read the digit printed on a hung scoreboard card, with a trained model.

Normalised intensity correlation against the board's own printed row was
measured and it failed: 46% on card digits, 0 of 11 distinct physical cards
read correctly in every frame, and -- decisively -- only 73% matching *printed*
digits, whose labels are known by construction, against printed templates from
a different frame. Same font, same board, same camera; only the frame differs.
So the representation was wrong, not the premise, and this module replaces the
matcher with a small classifier.

Its training data is free. The printed 1-14 row is in every board frame whether
or not any cards are hung, and it is self-labelling -- the digit at slot *k* is
*k* -- so `scripts/harvest_glyphs.py` pulls thousands of labelled glyphs off the
cached VOD with no human labelling. The hand-labelled real cards in
`datasets/board-cards` are never trained on; they are the gate.

Everything here is numpy, training included, and the weights ship as a small
`.npz`. `torch` lives only in the `gpu` extra, so a torch inference path would
break `curling-score analyze` on a base install (ruling R10).

Two models live here and answer the same `predict(glyph) -> (digit, confidence)`
contract: `Model`, the MLP, and `ConvModel`, the convolutional escalation
ruling R11 named for when augmentation alone did not close the alignment and
scale gap. Which one is better is a measurement, and the measurement is
`scripts/gate_digits.py`.
"""

from pathlib import Path

import cv2
import numpy as np

from curling_score.game.scoreboard import GLYPH_SHAPE, _normalise

# The digits a card can carry. Slots 10..14 of the printed row hold two digits
# in one slot width and are not valid card digits, so the harvest stops at 9
# and so does the label space.
LABELS = tuple(range(1, 10))
NCLASS = len(LABELS)
NFEAT = GLYPH_SHAPE[0] * GLYPH_SHAPE[1]

# The packaged weights: a `ConvModel`, trained on the GPU by
# `scripts/train_conv_torch.py` and exported to numpy (Task 6F). `Model`, the
# MLP, stays in this module for `scripts/gate_digits.py`'s comparison and for
# the frozen historical measurements in `tests/test_digits.py`, but it is no
# longer what ships or what this path holds.
WEIGHTS = Path(__file__).with_name("digit_weights.npz")

_default_model = None  # (path, ConvModel), cached so a read does not reload
                       # the weights every call


def load_default_model(path=WEIGHTS) -> "ConvModel":
    """The model `scoreboard.read_digit` actually predicts with.

    `ConvModel` is the shipped architecture: the escalation ruling R11 named,
    promoted to production once it cleared the leave-one-video-out gate at
    88.3-96.5% coverage with zero wrong reads, against the MLP's 45.2-81.4%
    (`scripts/gate_digits.py`, `.superpowers/sdd/2026-09-17-scoreboard-ocr/
    task-6E-report.md`). Cached at module scope: `scoreboard.read_digit` is
    called once per card, and reloading the `.npz` on every call would be
    needless disk I/O on the hot path.
    """
    global _default_model
    if _default_model is None or _default_model[0] != path:
        _default_model = (path, ConvModel.load(path))
    return _default_model[1]


def as_input(glyph) -> np.ndarray:
    """One glyph as the model's input vector.

    `_normalise` is what puts a card glyph and a printed glyph on the same
    footing at read time, and it is idempotent on something already the right
    shape, so running it here means callers can hand over either a raw ink
    patch or an already-normalised glyph and get the same answer.
    """
    g = np.asarray(glyph, dtype=np.float32)
    if g.ndim != 2 or g.size < 4:
        raise ValueError(f"a glyph is a 2-D patch, got {g.shape}")
    return _normalise(g).reshape(-1)


# ---------------------------------------------------------------- augmentation

def augment(x, rng) -> np.ndarray:
    """One randomly degraded copy of a normalised glyph.

    Correlation failed specifically on alignment and scale noise -- the same
    physical card read 7 six times, then 5, then 3, then 2 -- so the
    augmentation attacks exactly that, and teaching invariance to it is the
    whole reason a trained model is expected to beat the matcher (ruling R11).

    The degradations are the ones that actually separate two frames of the same
    glyph: where the ink bounding box landed (sub-pixel shift), how tightly it
    was cropped before being fitted to the box (scale), a card hung slightly
    askew (rotation), the camera's focus and the median stack's smearing
    (blur), the VOD's compression (quantisation and noise) and the rink's
    lighting drift over an evening (brightness and contrast).

    Brightness and contrast are applied *before* quantisation on purpose: on
    their own they would be undone by the closing re-normalisation, which is
    the point -- what survives is the different rounding they cause, which is
    the part that reaches a real read.
    """
    g = np.asarray(x, dtype=np.float32).reshape(GLYPH_SHAPE)
    h, w = GLYPH_SHAPE

    # The padding `_normalise` used is the patch median, and the border is the
    # only place left to read it off.
    border = np.concatenate([g[0], g[-1], g[:, 0], g[:, -1]])
    pad = float(np.median(border))

    # Scale and rotation about the centre, then a sub-pixel shift. warpAffine
    # interpolates, so a fractional shift is a genuine resampling rather than a
    # whole-pixel roll.
    sx = float(rng.uniform(0.85, 1.15))
    sy = float(rng.uniform(0.85, 1.15))
    ang = float(rng.uniform(-5.0, 5.0))
    dx = float(rng.uniform(-1.3, 1.3))
    dy = float(rng.uniform(-1.3, 1.3))
    m = cv2.getRotationMatrix2D((w / 2.0 - 0.5, h / 2.0 - 0.5), ang, 1.0)
    m[0, :2] *= sx
    m[1, :2] *= sy
    cx, cy = w / 2.0 - 0.5, h / 2.0 - 0.5
    m[0, 2] = m[0, 2] * sx + cx * (1 - sx) + dx
    m[1, 2] = m[1, 2] * sy + cy * (1 - sy) + dy
    g = cv2.warpAffine(g, m, (w, h), flags=cv2.INTER_LINEAR,
                       borderMode=cv2.BORDER_CONSTANT, borderValue=pad)

    # Focus and the median stack both smear a 20 px glyph noticeably.
    sigma = float(rng.uniform(0.0, 0.9))
    if sigma > 0.1:
        g = cv2.GaussianBlur(g, (0, 0), sigma)

    # Back onto an 8-bit intensity scale so brightness, contrast and the VOD's
    # quantisation can be applied where they actually happen.
    gain = float(rng.uniform(0.55, 1.5))
    level = float(rng.uniform(70.0, 190.0))
    spread = float(rng.uniform(14.0, 45.0))
    img = level + spread * gain * g
    step = float(rng.choice([1.0, 1.0, 2.0, 3.0, 5.0]))  # JPEG-like banding
    img = np.round(img / step) * step
    img = img + rng.normal(0.0, float(rng.uniform(0.0, 2.0)), img.shape)
    img = np.clip(img, 0.0, 255.0).astype(np.float32)

    # Re-normalised, because that is the state a glyph reaches the model in.
    out = img - img.mean()
    sd = float(out.std())
    return (out / sd if sd > 1e-6 else out).astype(np.float32)


def augmented_batch(x, y, rng, copies=1):
    """`copies` degraded copies of every glyph in `x`, labels tiled to match."""
    xs = np.empty((len(x) * copies, NFEAT), np.float32)
    i = 0
    for _ in range(copies):
        for g in x:
            xs[i] = augment(g, rng).reshape(-1)
            i += 1
    return xs, np.tile(np.asarray(y), copies)


# ----------------------------------------------------------------- the model

def _he(shape, rng):
    return (rng.normal(0.0, np.sqrt(2.0 / shape[0]), shape)).astype(np.float32)


def _softmax(z):
    z = z - z.max(axis=1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(axis=1, keepdims=True)


class Model:
    """A ReLU MLP over the normalised glyph, forward and backward in numpy.

    An MLP rather than a CNN because correlation's failure was alignment and
    scale noise, which augmentation addresses directly, and a CNN is the
    escalation if that is not enough (ruling R11). 352 inputs is small enough
    that a dense net trains in seconds on a CPU, which keeps the whole
    train-and-measure loop inside one numpy process.
    """

    def __init__(self, weights, biases, hidden=None):
        self.W = [np.asarray(w, np.float32) for w in weights]
        self.b = [np.asarray(v, np.float32) for v in biases]
        self.hidden = tuple(hidden) if hidden is not None else tuple(
            w.shape[1] for w in self.W[:-1]
        )

    @classmethod
    def initialise(cls, hidden=(256, 128), rng=None):
        rng = rng if rng is not None else np.random.default_rng(0)
        sizes = [NFEAT, *hidden, NCLASS]
        W = [_he((sizes[i], sizes[i + 1]), rng) for i in range(len(sizes) - 1)]
        b = [np.zeros(s, np.float32) for s in sizes[1:]]
        return cls(W, b, hidden=hidden)

    # -- inference ---------------------------------------------------------

    def _forward(self, X):
        """Activations layer by layer; the last entry is the logits."""
        acts = [X]
        a = X
        for i, (w, v) in enumerate(zip(self.W, self.b)):
            z = a @ w + v
            a = z if i == len(self.W) - 1 else np.maximum(z, 0.0)
            acts.append(a)
        return acts

    def logits(self, X) -> np.ndarray:
        X = np.atleast_2d(np.asarray(X, np.float32))
        return self._forward(X)[-1]

    def probs(self, X) -> np.ndarray:
        return _softmax(self.logits(X))

    def predict(self, glyph) -> tuple:
        """The digit on one glyph, and how confident the model is.

        Confidence is the winning class probability. It is *not* a presence
        test: `scoreboard.read_slots` decides whether a card is in the slot at
        all, because 54% of empty slots cleared the old correlation margin and
        folding presence into the digit read is what made that a problem.
        """
        if glyph is None:
            return None, 0.0
        g = np.asarray(glyph)
        if g.ndim != 2 or g.size < 4:
            return None, 0.0
        p = self.probs(as_input(g))[0]
        k = int(p.argmax())
        return LABELS[k], float(p[k])

    def predict_many(self, X):
        """Digits and confidences for a batch of already-prepared inputs."""
        p = self.probs(X)
        k = p.argmax(axis=1)
        return (np.asarray(LABELS)[k],
                p[np.arange(len(p)), k].astype(np.float64))

    # -- persistence -------------------------------------------------------

    def save(self, path=WEIGHTS):
        arrays = {f"W{i}": w for i, w in enumerate(self.W)}
        arrays.update({f"b{i}": v for i, v in enumerate(self.b)})
        np.savez_compressed(path, n_layers=np.int32(len(self.W)),
                            labels=np.asarray(LABELS, np.int16), **arrays)

    @classmethod
    def load(cls, path=WEIGHTS) -> "Model":
        with np.load(path) as z:
            n = int(z["n_layers"])
            labels = tuple(int(v) for v in z["labels"])
            if labels != LABELS:
                raise ValueError(f"weights are for labels {labels}, not {LABELS}")
            W = [z[f"W{i}"] for i in range(n)]
            b = [z[f"b{i}"] for i in range(n)]
        if W[0].shape[0] != NFEAT or W[-1].shape[1] != NCLASS:
            raise ValueError(f"weights do not fit {NFEAT}->{NCLASS}")
        return cls(W, b)

    # -- training ----------------------------------------------------------

    def fit(self, X, y, epochs=1, lr=1e-3, batch=128, weight_decay=1e-4,
            rng=None, on_epoch=None, augment_from=None, copies=6):
        """Adam over softmax cross-entropy.

        With `augment_from=(x0, y0)` a fresh augmented set is drawn every
        epoch, so the model never sees the same degraded copy twice -- which is
        what turns ~2000 real glyphs into an effectively unlimited training set.
        `X`/`y` are then only the fallback for a caller that has already made
        its own batch.
        """
        rng = rng if rng is not None else np.random.default_rng(0)
        mW = [np.zeros_like(w) for w in self.W]
        vW = [np.zeros_like(w) for w in self.W]
        mb = [np.zeros_like(v) for v in self.b]
        vb = [np.zeros_like(v) for v in self.b]
        beta1, beta2, eps = 0.9, 0.999, 1e-8
        step = 0

        idx_of = {d: i for i, d in enumerate(LABELS)}
        for epoch in range(epochs):
            if augment_from is not None:
                Xe, ye = augmented_batch(*augment_from, rng=rng, copies=copies)
            else:
                Xe, ye = X, y
            t = np.asarray([idx_of[int(v)] for v in ye])
            order = rng.permutation(len(Xe))
            total, seen = 0.0, 0
            for s in range(0, len(order), batch):
                sel = order[s:s + batch]
                xb, tb = Xe[sel], t[sel]
                acts = self._forward(xb)
                p = _softmax(acts[-1])
                n = len(sel)
                total += float(-np.log(np.maximum(p[np.arange(n), tb], 1e-12)).sum())
                seen += n

                d = p.copy()
                d[np.arange(n), tb] -= 1.0
                d /= n
                for i in range(len(self.W) - 1, -1, -1):
                    gW = acts[i].T @ d + weight_decay * self.W[i]
                    gb = d.sum(axis=0)
                    if i > 0:
                        d = (d @ self.W[i].T) * (acts[i] > 0)
                    step_i = step + 1
                    for arr, g, m, v in ((self.W[i], gW, mW, vW),
                                          (self.b[i], gb, mb, vb)):
                        m[i] = beta1 * m[i] + (1 - beta1) * g
                        v[i] = beta2 * v[i] + (1 - beta2) * (g * g)
                        mh = m[i] / (1 - beta1 ** step_i)
                        vh = v[i] / (1 - beta2 ** step_i)
                        arr -= (lr * mh / (np.sqrt(vh) + eps)).astype(np.float32)
                step += 1
            if on_epoch is not None:
                on_epoch(epoch, total / max(1, seen))
        return self


# ------------------------------------------------------- the convolutional one

def _im2col(x, k, pad):
    """Sliding k x k patches of a (N, C, H, W) batch as a matrix.

    Rows are (sample, output row, output column) in that order and columns are
    (channel, patch row, patch column), so a convolution is one matmul against
    a (C*k*k, filters) weight and its gradients fall out of the same matmul
    transposed. Stride is always 1 here: a 22x16 glyph is small enough that
    striding buys nothing and pooling is a clearer place to lose resolution.
    """
    n, c, h, w = x.shape
    if pad:
        x = np.pad(x, ((0, 0), (0, 0), (pad, pad), (pad, pad)))
    oh, ow = h + 2 * pad - k + 1, w + 2 * pad - k + 1
    windows = np.lib.stride_tricks.sliding_window_view(x, (k, k), axis=(2, 3))
    # (N, C, oh, ow, k, k) -> (N*oh*ow, C*k*k)
    cols = windows.transpose(0, 2, 3, 1, 4, 5).reshape(n * oh * ow, c * k * k)
    return np.ascontiguousarray(cols, dtype=x.dtype), oh, ow


def _col2im(cols, shape, k, pad, oh, ow):
    """The adjoint of `_im2col`: scatter patch gradients back onto the image.

    Overlapping patches means a pixel receives a gradient from every window it
    appeared in, so this accumulates rather than assigns. The k*k loop is over
    25 slices at most and each one is a vectorised add, which is far cheaper
    than `np.add.at` over the index arrays.
    """
    n, c, h, w = shape
    dx = np.zeros((n, c, h + 2 * pad, w + 2 * pad), cols.dtype)
    cols = cols.reshape(n, oh, ow, c, k, k)
    for i in range(k):
        for j in range(k):
            dx[:, :, i:i + oh, j:j + ow] += cols[:, :, :, :, i, j].transpose(
                0, 3, 1, 2)
    return dx[:, :, pad:pad + h, pad:pad + w] if pad else dx


def _maxpool2(x):
    """2x2 max pooling, stride 2, and the mask needed to send gradients back.

    An odd dimension drops its last row or column rather than padding it: a
    padded edge would invent a value the glyph does not have and pooling would
    then be able to pick it. Ties share the gradient equally, which keeps the
    backward pass the true subgradient instead of favouring whichever index
    `argmax` happened to return.
    """
    n, c, h, w = x.shape
    h2, w2 = h - h % 2, w - w % 2
    v = x[:, :, :h2, :w2].reshape(n, c, h2 // 2, 2, w2 // 2, 2)
    out = v.max(axis=(3, 5))
    mask = (v == out[:, :, :, None, :, None])
    mask = mask / mask.sum(axis=(3, 5), keepdims=True)
    return out, (mask.astype(x.dtype), x.shape)


def _maxpool2_back(d, cache):
    mask, shape = cache
    n, c, h, w = shape
    h2, w2 = h - h % 2, w - w % 2
    dx = np.zeros(shape, d.dtype)
    dx[:, :, :h2, :w2] = (mask * d[:, :, :, None, :, None]).reshape(
        n, c, h2, w2)
    return dx


class ConvModel:
    """A small convolutional net over the normalised glyph, numpy throughout.

    Why this exists: template correlation (46% on cards) and the MLP (60.8%
    coverage at the zero-wrong threshold) failed the same way, on alignment and
    scale noise. The MLP sees 352 independent inputs, so a glyph shifted one
    pixel is a different input vector and invariance to that has to be learned
    separately for every position from augmented examples. A convolution shares
    one filter across all positions, so the invariance is structural and the
    54 real cards buy a great deal more. Ruling R11 named this as the
    escalation if augmentation alone was not enough, and it was not.

    Deliberately small -- conv 5x5x16, pool, conv 3x3x32, pool, 64 hidden, 9
    out, about 47k weights -- because the real training set is 54 physical
    cards plus 2052 printed glyphs, augmented. A wider net would fit the
    augmentation, not the digits.

    `torch` is in the `gpu` extra and `curling-score analyze` must run on a base
    install (ruling R10), so the convolution, its adjoint and Adam are all here.
    """

    def __init__(self, params, spec):
        self.p = [np.asarray(v, np.float32) for v in params]
        self.spec = dict(spec)

    @classmethod
    def initialise(cls, c1=16, c2=32, k1=5, k2=3, fc=64, rng=None):
        rng = rng if rng is not None else np.random.default_rng(0)
        h, w = GLYPH_SHAPE
        h1, w1 = h // 2, w // 2                    # after the first pool
        h2, w2 = h1 // 2, w1 // 2                  # after the second
        flat = c2 * h2 * w2
        spec = dict(c1=c1, c2=c2, k1=k1, k2=k2, fc=fc, flat=flat)
        params = [
            _he((k1 * k1, c1), rng), np.zeros(c1, np.float32),
            _he((c1 * k2 * k2, c2), rng), np.zeros(c2, np.float32),
            _he((flat, fc), rng), np.zeros(fc, np.float32),
            _he((fc, NCLASS), rng), np.zeros(NCLASS, np.float32),
        ]
        return cls(params, spec)

    # -- inference ---------------------------------------------------------

    def _as_batch(self, X):
        """Anything a caller hands over, as (N, 1, 22, 16).

        `Model` takes flat rows and `predict` hands over one; a batch of glyphs
        arrives 3-D. All three are the same pixels in a different arrangement,
        and the convolution needs the arrangement the MLP threw away.
        """
        a = np.asarray(X, np.float32)
        if a.ndim == 1:
            a = a.reshape(1, *GLYPH_SHAPE)
        elif a.ndim == 2:
            a = (a.reshape(1, *GLYPH_SHAPE) if a.shape == GLYPH_SHAPE
                 else a.reshape(len(a), *GLYPH_SHAPE))
        return a.reshape(len(a), 1, *GLYPH_SHAPE)

    def _forward(self, X):
        """Logits, plus everything the backward pass needs to retrace."""
        W1, b1, W2, b2, W3, b3, W4, b4 = self.p
        k1, k2 = self.spec["k1"], self.spec["k2"]
        c1, c2 = self.spec["c1"], self.spec["c2"]
        n = len(X)

        cols1, oh1, ow1 = _im2col(X, k1, k1 // 2)
        z1 = (cols1 @ W1 + b1).reshape(n, oh1, ow1, c1).transpose(0, 3, 1, 2)
        a1 = np.maximum(z1, 0.0)
        p1, pc1 = _maxpool2(a1)

        cols2, oh2, ow2 = _im2col(p1, k2, k2 // 2)
        z2 = (cols2 @ W2 + b2).reshape(n, oh2, ow2, c2).transpose(0, 3, 1, 2)
        a2 = np.maximum(z2, 0.0)
        p2, pc2 = _maxpool2(a2)

        f = p2.reshape(n, -1)
        z3 = f @ W3 + b3
        a3 = np.maximum(z3, 0.0)
        logits = a3 @ W4 + b4
        cache = dict(X=X, cols1=cols1, z1=z1, a1=a1, pc1=pc1, p1=p1,
                     cols2=cols2, z2=z2, a2=a2, pc2=pc2, p2=p2,
                     f=f, z3=z3, a3=a3,
                     oh1=oh1, ow1=ow1, oh2=oh2, ow2=ow2)
        return logits, cache

    def _backward(self, cache, d):
        """Gradients for every parameter, given d(loss)/d(logits)."""
        W1, b1, W2, b2, W3, b3, W4, b4 = self.p
        k1, k2 = self.spec["k1"], self.spec["k2"]
        c1, c2 = self.spec["c1"], self.spec["c2"]
        n = len(cache["X"])

        gW4 = cache["a3"].T @ d
        gb4 = d.sum(axis=0)
        d3 = (d @ W4.T) * (cache["z3"] > 0)
        gW3 = cache["f"].T @ d3
        gb3 = d3.sum(axis=0)

        dp2 = (d3 @ W3.T).reshape(cache["p2"].shape)
        da2 = _maxpool2_back(dp2, cache["pc2"]) * (cache["z2"] > 0)
        d2 = da2.transpose(0, 2, 3, 1).reshape(-1, c2)
        gW2 = cache["cols2"].T @ d2
        gb2 = d2.sum(axis=0)

        dcols2 = d2 @ W2.T
        dp1 = _col2im(dcols2, cache["p1"].shape, k2, k2 // 2,
                      cache["oh2"], cache["ow2"])
        da1 = _maxpool2_back(dp1, cache["pc1"]) * (cache["z1"] > 0)
        d1 = da1.transpose(0, 2, 3, 1).reshape(-1, c1)
        gW1 = cache["cols1"].T @ d1
        gb1 = d1.sum(axis=0)
        return [gW1, gb1, gW2, gb2, gW3, gb3, gW4, gb4]

    def logits(self, X) -> np.ndarray:
        return self._forward(self._as_batch(X))[0]

    def probs(self, X) -> np.ndarray:
        return _softmax(self.logits(X))

    def predict(self, glyph) -> tuple:
        """The digit on one glyph and the winning class probability.

        Same contract as `Model.predict`, because everything downstream --
        `read_cards`, the reject threshold, the gate -- consumes that and must
        not care which of the two is loaded.
        """
        if glyph is None:
            return None, 0.0
        g = np.asarray(glyph)
        if g.ndim != 2 or g.size < 4:
            return None, 0.0
        p = self.probs(as_input(g))[0]
        k = int(p.argmax())
        return LABELS[k], float(p[k])

    def predict_many(self, X):
        p = self.probs(X)
        k = p.argmax(axis=1)
        return (np.asarray(LABELS)[k],
                p[np.arange(len(p)), k].astype(np.float64))

    # -- persistence -------------------------------------------------------

    def save(self, path=WEIGHTS):
        np.savez_compressed(
            path, kind=np.str_("conv"), labels=np.asarray(LABELS, np.int16),
            spec=np.asarray([self.spec[k] for k in
                             ("c1", "c2", "k1", "k2", "fc", "flat")], np.int32),
            **{f"p{i}": v for i, v in enumerate(self.p)})

    @classmethod
    def load(cls, path=WEIGHTS) -> "ConvModel":
        with np.load(path) as z:
            if str(z["kind"]) != "conv":
                raise ValueError(f"{path} holds a {z['kind']} model")
            labels = tuple(int(v) for v in z["labels"])
            if labels != LABELS:
                raise ValueError(f"weights are for labels {labels}, not {LABELS}")
            keys = ("c1", "c2", "k1", "k2", "fc", "flat")
            spec = dict(zip(keys, (int(v) for v in z["spec"])))
            params = [z[f"p{i}"] for i in range(8)]
        return cls(params, spec)

    # -- training ----------------------------------------------------------

    def fit(self, X, y, epochs=1, lr=1e-3, batch=128, weight_decay=1e-4,
            rng=None, on_epoch=None, augment_from=None, copies=6):
        """Adam over softmax cross-entropy, same recipe as `Model.fit`.

        Same signature, same defaults and the same fresh-augmentation-per-epoch
        behaviour, so the gate can swap one model for the other and change
        nothing else. Anything that differs between the two runs would otherwise
        be a candidate explanation for whatever the gate measures.
        """
        rng = rng if rng is not None else np.random.default_rng(0)
        m = [np.zeros_like(v) for v in self.p]
        v = [np.zeros_like(p) for p in self.p]
        beta1, beta2, eps = 0.9, 0.999, 1e-8
        step = 0

        idx_of = {d: i for i, d in enumerate(LABELS)}
        for epoch in range(epochs):
            if augment_from is not None:
                Xe, ye = augmented_batch(*augment_from, rng=rng, copies=copies)
            else:
                Xe, ye = X, y
            Xe = np.asarray(Xe, np.float32).reshape(len(Xe), 1, *GLYPH_SHAPE)
            t = np.asarray([idx_of[int(u)] for u in ye])
            order = rng.permutation(len(Xe))
            total, seen = 0.0, 0
            for s in range(0, len(order), batch):
                sel = order[s:s + batch]
                xb, tb = Xe[sel], t[sel]
                logits, cache = self._forward(xb)
                p = _softmax(logits)
                n = len(sel)
                total += float(-np.log(
                    np.maximum(p[np.arange(n), tb], 1e-12)).sum())
                seen += n

                d = p.copy()
                d[np.arange(n), tb] -= 1.0
                d /= n
                grads = self._backward(cache, d)
                step += 1
                for i, (arr, g) in enumerate(zip(self.p, grads)):
                    if arr.ndim > 1:
                        g = g + weight_decay * arr
                    m[i] = beta1 * m[i] + (1 - beta1) * g
                    v[i] = beta2 * v[i] + (1 - beta2) * (g * g)
                    mh = m[i] / (1 - beta1 ** step)
                    vh = v[i] / (1 - beta2 ** step)
                    arr -= (lr * mh / (np.sqrt(vh) + eps)).astype(np.float32)
            if on_epoch is not None:
                on_epoch(epoch, total / max(1, seen))
        return self


# ------------------------------------------------------------------ the split

def frame_split(t_s, window_s, holdout=0.2, block=10, seed=0):
    """Hold out whole frames, in contiguous blocks, with a buffer.

    Splitting on individual glyphs would leak: nine glyphs come off one frame,
    and augmented copies of one physical glyph on both sides of a split make
    the validation score a measurement of nothing. Holding out whole frames is
    necessary but not sufficient either -- neighbouring samples are median
    stacks of *overlapping* keyframe windows, so two frames 60 s apart can
    share source frames. So whole blocks of consecutive samples are held out,
    and any training frame within one stacking window of a held-out frame is
    dropped outright.

    Lives here rather than in the training script so that the script's
    validation number and the test's are the same number, measured the same
    way. A validation score nothing else can reproduce is not a measurement.
    """
    t_s = np.asarray(t_s, dtype=np.float64)
    times = np.unique(t_s)
    blocks = [times[i:i + block] for i in range(0, len(times), block)]
    rng = np.random.default_rng(seed)
    n_val = max(1, int(round(holdout * len(blocks))))
    chosen = set(rng.choice(len(blocks), size=n_val, replace=False).tolist())

    val_times = np.concatenate([b for i, b in enumerate(blocks) if i in chosen])
    buffer = 2.0 * float(window_s)
    keep = [t for t in times
            if t not in set(val_times.tolist())
            and np.abs(val_times - t).min() > buffer]
    train_times = np.asarray(keep, dtype=np.float64)

    return (np.isin(t_s, train_times), np.isin(t_s, val_times),
            train_times, val_times)


def accuracy(model, glyphs, labels):
    """Accuracy over unaugmented glyphs -- the read as it really arrives."""
    X = np.stack([as_input(g) for g in glyphs])
    pred, conf = model.predict_many(X)
    return float((pred == np.asarray(labels, int)).mean()), pred, conf


# --------------------------------------------------- the hand-labelled cards

def card_glyphs(root, labels="labels.json"):
    """The hand-labelled real cards, as (glyph, end, meta) triples.

    These are the gate and they are never trained on. Kept here rather than in
    the training script so that the script and the test measure the same set
    the same way -- a validation number nothing else can reproduce is not a
    measurement.
    """
    import json

    from curling_score.game import scoreboard as SB

    root = Path(root)
    rows = json.loads((root / labels).read_text())
    out = []
    cache = {}
    for r in rows:
        if r.get("end") is None:
            continue
        name = r["frame"]
        if name not in cache:
            img = cv2.imread(str(root / name))
            cache[name] = (img, SB.find_board(img) if img is not None else None)
        img, geom = cache[name]
        if geom is None:
            continue
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        glyph = SB._card_glyph(gray, geom, r["color"], int(r["slot"]))
        out.append((glyph, int(r["end"]), dict(r)))
    return out


def distinct_cards(cards):
    """Group labelled rows into the *physical* cards they photograph.

    The 69 labelled rows are only 11 physical cards, seen again in every frame
    they stay hung for, and the honest question is whether a card reads
    correctly in *every* frame -- correlation managed that for 0 of 11 while
    still scoring 46% row by row. A card is identified by its colour, its slot
    and its digit *within one game*: the board is cleared between games and the
    same slot is then filled again by a different card, which is why the board
    carries `yellow` slot 1 / end 1 twice over this VOD and those are two cards,
    not one.

    A new game is a frame where every colour that had a score has less of it
    than in the frame before. A single colour dropping out is a card the read
    missed, not a cleared board -- the red row flickers most, because that is
    the half of the board a spectator stands in front of.
    """
    times = sorted({m["t_s"] for _, _, m in cards})
    best = {c: 0 for c in ("yellow", "red")}
    game_of, game = {}, 0
    for t in times:
        now = {c: max((int(m["slot"]) for _, _, m in cards
                       if m["t_s"] == t and m["color"] == c), default=0)
               for c in best}
        dropped = [now[c] < best[c] for c in best if best[c] > 0]
        if dropped and all(dropped):
            game += 1
            best = {c: 0 for c in best}
        game_of[t] = game
        best = {c: max(best[c], now[c]) for c in best}

    groups = {}
    for glyph, end, meta in cards:
        key = (game_of[meta["t_s"]], meta["color"], int(meta["slot"]), end)
        groups.setdefault(key, []).append((glyph, end, meta))
    return groups


def empty_slot_glyphs(root, labels="labels.json"):
    """Every slot position in those frames that carries no labelled card.

    ~519 of them, and they are the other half of the question: a threshold is
    only useful if "a card is here" separates from "nothing here". Slots where
    no card tile is found at all yield None, which is a refusal reached before
    the model is ever asked.
    """
    import json

    from curling_score.game import scoreboard as SB

    root = Path(root)
    rows = json.loads((root / labels).read_text())
    taken = {(r["frame"], r["color"], int(r["slot"])) for r in rows}
    out = []
    for name in sorted({r["frame"] for r in rows}):
        img = cv2.imread(str(root / name))
        if img is None:
            continue
        geom = SB.find_board(img)
        if geom is None:
            continue
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        for color in SB.COLORS:
            for slot in range(1, SB.SLOTS + 1):
                if (name, color, slot) in taken:
                    continue
                glyph = SB._card_glyph(gray, geom, color, slot)
                out.append((glyph, dict(frame=name, color=color, slot=slot)))
    return out
