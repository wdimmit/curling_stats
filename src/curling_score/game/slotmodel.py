"""Whether a card hangs in a scoreboard slot, from the slot's own window.

`scoreboard.read_slots` used to decide it with two brightness thresholds: a
card is a white tile (the box's 92nd percentile at least 12 above the row) with
dark ink on it (the 8th percentile at least 30 below). A "1" broke both. Its
stroke is 8% of the box, so the 8th percentile barely reaches it (sheet 4's
yellow end-1 card, 2026-09-29, read 23); and a card hung high over the rule
leaves only its lower half in the box, so the tile's brightest pixels fell
short (09/28 sheet 1's red end-1 card, +8.5 to +11.6). Every end-1 card is a
"1", so the end-1 score went missing and the reader inferred a blank end.

A small convolutional net over `scoreboard.card_window` -- the window the
digit reader already reads, reaching up to the printed rule -- replaces both,
trained on slot windows from ten videos of both seasons' boards, labelled card
or blank (a person or arm in front, glare and the board's edges are blank:
nothing readable hangs there). The digit on a card is still read by
`digits`, which never got a missed "1" wrong.

Inference is numpy, like `digits.ConvModel` (ruling R10: `curling-score
analyze` runs on a base install); `scripts/slots/train.py` trains with torch
and exports the weights here, checking the two forward passes agree.
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from curling_score.game.digits import _im2col, _maxpool2, _softmax

SLOT_SHAPE = (32, 24)          # (rows, cols) the window is resized to
WEIGHTS = Path(__file__).with_name("slot_weights.npz")
# A card needs this much of the model's belief. Held out one video at a time
# over the 10,892 labelled slots (datasets/slots, 2026-09-30), cards missed /
# blanks called cards: 0.5 -> 5 / 24, 0.8 -> 8 / 14, 0.9 -> 11 / 12,
# 0.95 -> 13 / 10, 0.98 -> 20 / 8; the thresholds it replaces, 55 / 20. A
# missed card blanks an end's score and a false one can invent one, so the cut
# sits where the two are even.
MIN_P_CARD = 0.9


CHANNELS = 2


def as_input(window) -> np.ndarray:
    """A slot window as the net's input, (2, rows, cols): its brightness as it
    is, and again contrast-normalised. The first keeps what tells a card from a
    person -- a white tile is brighter than the board, a person far darker --
    and the second shows a tile and a stroke whatever the exposure."""
    w = np.asarray(window, np.float32)
    w = cv2.resize(w, (SLOT_SHAPE[1], SLOT_SHAPE[0]), interpolation=cv2.INTER_AREA)
    return np.stack([(w - 128.0) / 64.0, (w - w.mean()) / (w.std() + 8.0)])


class SlotModel:
    """Two input channels (`as_input`); conv 3x3x8, pool, conv 3x3x16, pool,
    32 hidden, 2 out (blank, card)."""

    def __init__(self, params, spec):
        self.p = [np.asarray(v, np.float32) for v in params]
        self.spec = dict(spec)

    def probs(self, X) -> np.ndarray:
        """P(blank), P(card) for a batch of `as_input` arrays."""
        a = np.asarray(X, np.float32).reshape(-1, CHANNELS, *SLOT_SHAPE)
        W1, b1, W2, b2, W3, b3, W4, b4 = self.p
        k1, k2, c1, c2 = (self.spec[k] for k in ("k1", "k2", "c1", "c2"))
        n = len(a)
        cols1, oh1, ow1 = _im2col(a, k1, k1 // 2)
        a1 = np.maximum((cols1 @ W1 + b1).reshape(n, oh1, ow1, c1).transpose(0, 3, 1, 2), 0.0)
        p1, _ = _maxpool2(a1)
        cols2, oh2, ow2 = _im2col(p1, k2, k2 // 2)
        a2 = np.maximum((cols2 @ W2 + b2).reshape(n, oh2, ow2, c2).transpose(0, 3, 1, 2), 0.0)
        p2, _ = _maxpool2(a2)
        a3 = np.maximum(p2.reshape(n, -1) @ W3 + b3, 0.0)
        return _softmax(a3 @ W4 + b4)

    def p_card(self, window) -> float:
        return float(self.probs(as_input(window)[None])[0, 1])

    def save(self, path=WEIGHTS):
        np.savez_compressed(path, spec=np.asarray([self.spec[k] for k in ("c1", "c2", "k1", "k2", "fc", "flat")], np.int32),
                            **{f"p{i}": v for i, v in enumerate(self.p)})

    @classmethod
    def load(cls, path=WEIGHTS) -> "SlotModel":
        z = np.load(path)
        spec = dict(zip(("c1", "c2", "k1", "k2", "fc", "flat"), (int(v) for v in z["spec"])))
        return cls([z[f"p{i}"] for i in range(8)], spec)


_default = None


def load_default(path=WEIGHTS) -> SlotModel:
    """The shipped model, loaded once: `read_slots` asks it 28 times a read."""
    global _default
    if _default is None or _default[0] != path:
        _default = (path, SlotModel.load(path))
    return _default[1]
