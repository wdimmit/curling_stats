"""A rock's rotation from the swing of its handle.

Seen from the long camera facing the thrower, the grip end of the handle sits
r*sin(theta) across from the neck, theta the handle's angle from the line of
sight -- so ``s`` (the grip's offset across from the neck, in stone widths)
swings once per turn. The rate is its frequency: a periodogram for where to
start, then a sinusoid fitted with a grid over frequency (and, for the whole
stretch, a slow drift) and least squares, reweighted so a stray frame cannot
pull it.

Two rates: ``rpm`` is the spin as thrown -- a constant rate over the first
~1.25 turns -- and ``rpm_avg`` the whole stretch in view, which a rock that
slows reads up to ~15% lower. A slow-down fitted through the whole stretch and
extrapolated back to the hog line was tried in the spike and over-read badly.

Direction is not here: the swing is the same either way round.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

FREQS = np.linspace(0.05, 1.0, 381)      # turns/s: 3 to 60 rpm
EARLY_TURNS = 1.25
EARLY_WINDOW_S = (5.0, 9.0)
MIN_SAMPLES = 30                         # 3 s at 10 fps
MIN_AMPLITUDE = 0.10                     # stone widths
MIN_SNR = 3.0
MIN_TURNS_IN_VIEW = 0.5
DRIFTS = tuple(np.linspace(-0.02, 0.01, 13))   # turns/s^2, the whole stretch only


@dataclass(frozen=True)
class Spin:
    rpm: float              # the spin as thrown: the early window
    rpm_avg: float          # the whole stretch in view
    turns_in_view: float
    samples: int


def periodogram(t, x, freqs=FREQS):
    """Lomb-Scargle power at each frequency, normalised by the variance."""
    x = x - x.mean()
    var = max(float(x.var()), 1e-12)
    out = np.empty(len(freqs))
    for i, f in enumerate(freqs):
        w = 2 * np.pi * f
        tau = np.arctan2(np.sum(np.sin(2 * w * t)), np.sum(np.cos(2 * w * t))) / (2 * w)
        c, s = np.cos(w * (t - tau)), np.sin(w * (t - tau))
        out[i] = 0.5 * ((x @ c) ** 2 / (c @ c) + (x @ s) ** 2 / (s @ s)) / var
    return out


def fit(t, x, f0, drifts=DRIFTS):
    """x = a sin(2pi(f tm + k tm^2 / 2) + ph) + c, f within 30% of f0. For a
    given (f, k) it is linear in (a cos ph, a sin ph, c): the grid searches f
    and k, least squares (Huber-reweighted) the rest. Returns (f, k, a, noise),
    noise the residuals' robust spread (1.4826 x their median absolute value),
    so a few stray frames -- a box on a broom -- do not read as noise."""
    tm = t - t.mean()
    best = None
    for f in np.linspace(0.7 * f0, 1.3 * f0, 121):
        for k in drifts:
            ph = 2 * np.pi * (f * tm + 0.5 * k * tm ** 2)
            A = np.stack([np.sin(ph), np.cos(ph), np.ones_like(tm)], axis=1)
            wts = np.ones_like(x)
            for _ in range(4):
                coef, *_ = np.linalg.lstsq(A * wts[:, None], x * wts, rcond=None)
                r = x - A @ coef
                scale = 1.4826 * np.median(np.abs(r)) + 1e-6
                wts = np.sqrt(np.minimum(1.0, 1.5 * scale / np.maximum(np.abs(r), 1e-9)))
            cost = float(np.sum((wts * r) ** 2))
            if best is None or cost < best[0]:
                best = (cost, f, k, coef, r)
    _, f, k, (bs, bc, _c), r = best
    return float(f), float(k), float(np.hypot(bs, bc)), float(1.4826 * np.median(np.abs(r)))


def _peak(t, x, freqs=FREQS) -> float:
    return float(freqs[int(np.argmax(periodogram(t, x, freqs)))])


def read(t, s) -> Spin | None:
    """The rock's rotation from its swing, or None where it cannot be read."""
    t, s = np.asarray(t, float), np.asarray(s, float)
    if t.size < MIN_SAMPLES:
        return None
    order = np.argsort(t)
    t, s = t[order], s[order]
    f_all, _k, a, noise = fit(t, s, _peak(t, s - np.polyval(np.polyfit(t, s, 1), t)))
    span = float(t[-1] - t[0])
    turns = f_all * span
    if a < MIN_AMPLITUDE or a / max(noise, 1e-9) < MIN_SNR or turns < MIN_TURNS_IN_VIEW:
        return None
    w = float(np.clip(EARLY_TURNS / max(f_all, 1e-3), *EARLY_WINDOW_S))
    m = t <= t[0] + w
    if m.sum() < MIN_SAMPLES:
        return None
    # Anchored on the whole stretch's rate, not on the window's own peak: a
    # sweeper hiding the rock for a second can pull that peak off, and a search
    # around it may then not reach the true rate. The early rate sits within
    # -2% to +12% of the whole stretch's on most rocks (spike, 84 rocks).
    f_early, *_ = fit(t[m], s[m], f_all, drifts=(0.0,))
    return Spin(rpm=60.0 * f_early, rpm_avg=60.0 * f_all, turns_in_view=turns, samples=int(t.size))
