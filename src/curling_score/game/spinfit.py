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
from typing import NamedTuple

import numpy as np

FREQS = np.linspace(0.05, 1.0, 381)      # turns/s: 3 to 60 rpm
EARLY_TURNS = 1.25
EARLY_WINDOW_S = (5.0, 9.0)
MIN_SAMPLES = 30                         # 3 s at 10 fps
MIN_AMPLITUDE = 0.10                     # stone widths
MIN_SNR = 3.0
MIN_TURNS_IN_VIEW = 0.5
EARLY_AGREE = 0.25                       # the early rate against the whole stretch's, at most
DRIFTS = tuple(np.linspace(-0.02, 0.01, 13))   # turns/s^2, the whole stretch only

# A rock showing under a turn in view is timed with its handle's swing held --
# how far the grip swings across the neck, in stone widths, and about where.
# It is near-constant across stones, sheets and games (six replay windows over
# four videos, 2026-10-11: red 0.226, IQR 0.220-0.235; yellow 0.210, IQR
# 0.201-0.219; centre -0.02), which leaves only the rate and phase to fit. On
# clean rocks cut short: 5% median error at 0.5-0.75 turns (the free fit 19%),
# 10% at 0.35-0.5 (73%), the range holding the true rate 83-91% of the time.
SWING = {"red": (0.226, -0.024), "yellow": (0.210, -0.021)}
CLEAN_TURNS = 1.0              # a read this long and this clean carries its handle's swing
CLEAN_SNR = 5.0
HELD_BELOW_TURNS = 1.0         # under this in view, the held fit is the read
HELD_MIN_TURNS = 0.3           # under this not even a range holds
RANGE_BELOW_TURNS = 0.5        # under this the rate is given as a range
RANGE_REL = 0.5                # the range: every rate whose best cost is within 50% of the least
MAX_RANGE = 0.6                # a range wider than this share of its rate is not a reading
HELD_F = np.arange(0.005, 0.60, 0.0025)        # turns/s: 0.3 to 36 rpm
HELD_PH = np.linspace(0, 2 * np.pi, 120, endpoint=False)
HUBER_SCALE = 0.05             # stone widths: residuals past this count linearly


@dataclass(frozen=True)
class Spin:
    rpm: float              # the spin as thrown: the early window
    rpm_avg: float          # the whole stretch in view
    turns_in_view: float
    samples: int
    rpm_range: tuple | None = None     # (lo, hi) rpm, for a rock showing under half a turn
    swing: tuple | None = None         # (amplitude, centre) of a clean read's handle


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
    and k, least squares (Huber-reweighted) the rest. Returns (f, k, a, noise,
    c), noise the residuals' robust spread (1.4826 x their median absolute
    value), so a few stray frames -- a box on a broom -- do not read as noise."""
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
    _, f, k, (bs, bc, c), r = best
    return (float(f), float(k), float(np.hypot(bs, bc)), float(1.4826 * np.median(np.abs(r))),
            float(c))


def _peak(t, x, freqs=FREQS) -> float:
    return float(freqs[int(np.argmax(periodogram(t, x, freqs)))])


def read(t, s) -> Spin | None:
    """The rock's rotation from its swing, or None where it cannot be read."""
    t, s = np.asarray(t, float), np.asarray(s, float)
    if t.size < MIN_SAMPLES:
        return None
    order = np.argsort(t)
    t, s = t[order], s[order]
    f_all, _k, a, noise, c = fit(t, s, _peak(t, s - np.polyval(np.polyfit(t, s, 1), t)))
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
    # The two fits of one rock disagreeing by more than any real rock's do
    # (-14% to +19%, the spike's 388) -- the early one run out to the edge of
    # its anchor -- is a stretch too short to say: under half a turn, which
    # the whole-stretch fit reads 40-50% fast, past the turns gate.
    if abs(f_early / f_all - 1) > EARLY_AGREE:
        return None
    clean = turns >= CLEAN_TURNS and a / max(noise, 1e-9) >= CLEAN_SNR
    return Spin(rpm=60.0 * f_early, rpm_avg=60.0 * f_all, turns_in_view=turns, samples=int(t.size),
                swing=(a, c) if clean else None)


def fit_held(t, x, amp, centre):
    """x = amp sin(2pi f t + ph) + centre, amp and centre held: the f x ph grid,
    Huber cost. Returns (f, f_lo, f_hi), the span of every rate whose best cost
    is within RANGE_REL of the least -- how sharply the frames pin it."""
    tm = (t - t[0])[None, :]
    prof = np.empty(HELD_F.size)
    for i, f in enumerate(HELD_F):
        r = np.abs(x[None, :] - (amp * np.sin(2 * np.pi * f * tm + HELD_PH[:, None]) + centre))
        prof[i] = np.where(r < HUBER_SCALE, 0.5 * r ** 2,
                           HUBER_SCALE * (r - 0.5 * HUBER_SCALE)).sum(axis=1).min()
    i = int(np.argmin(prof))
    ok = np.flatnonzero(prof <= prof[i] * (1 + RANGE_REL))
    return float(HELD_F[i]), float(HELD_F[ok.min()]), float(HELD_F[ok.max()])


class Reading(NamedTuple):
    spin: Spin | None
    under_a_turn: bool      # held, it showed under a turn: more of its track would help


def read_with_swing(t, s, swing, first) -> Reading:
    """``first`` (``read``'s answer) unless the rock showed under a turn in
    view: then the read with its handle's ``swing`` (amplitude, centre) held --
    one rate from half a turn, a range under it.

    Today's read of a turn or more stands as it is, and so does today's read
    (or its absence) wherever the held fit cannot do better: a held fit that
    finds a turn or more (a rock the free fit refused there is noise, not
    slowness), or whose range is loose. A held rate is the whole stretch's, a
    few per cent under a slowing rock's as-thrown rate."""
    if first is not None and first.turns_in_view >= HELD_BELOW_TURNS:
        return Reading(first, False)
    t, s = np.asarray(t, float), np.asarray(s, float)
    if t.size < MIN_SAMPLES:
        return Reading(first, True)
    order = np.argsort(t)
    t, s = t[order], s[order]
    f, lo, hi = fit_held(t, s, *swing)
    turns = f * float(t[-1] - t[0])
    if turns >= HELD_BELOW_TURNS:
        return Reading(first, False)
    if turns < HELD_MIN_TURNS or (hi - lo) / f > MAX_RANGE:
        return Reading(first, True)
    return Reading(Spin(rpm=60.0 * f, rpm_avg=60.0 * f, turns_in_view=turns, samples=int(t.size),
                        rpm_range=(60.0 * lo, 60.0 * hi) if turns < RANGE_BELOW_TURNS else None), True)
