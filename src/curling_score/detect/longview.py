"""Time a delivery across the throwing end's hog line, in a side view.

The overhead panel loses the throw before the hog line on about 40% of
deliveries -- it looks straight down at a stone with the thrower and sweepers
standing over it. The camera at the far end is 22 m away and level with the
ice, so the sweepers are beside the stone rather than on top of it, and the
line it crosses is painted and fixed.

Most of this module is refusal. The same view shows boots, broom pads and the
stones already in play, and a mistimed split is worse than a missing one, so a
crossing has to survive every gate below or it does not exist.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass

import numpy as np

# Measured by hand on 27 deliveries (``datasets/hogmarks``): a stone crosses
# its hog line between 2.83 s and 5.43 s after the panel first sees it leave
# the hack. The window is wider at both ends than every mark observed.
WINDOW_S = (2.0, 6.5)

# The stone reads about 52 px across at the hog line. These bounds reject a
# broom pad and a sweeper's shadow while keeping every stone seen so far.
WIDTH_BOUNDS = (0.5, 1.6)
STONE_WIDTH_AT_HOG_PX = 52.0
# A delivery crosses its hog line between 1.2 and 3.2 m/s -- the range the 27
# hand-marked crossings imply. Slower than that is a stone being nudged aside,
# or a tracker that has drifted onto one already at rest.
SPEED_BOUNDS_M_S = (1.2, 3.2)
# How much darker than the ice granite is. The painted hog line is a dip of
# about 25 levels, so the threshold has to sit well below it or the line gets
# picked up as the stone's own edge.
_BODY_DARKER_THAN_ICE = 45
_MIN_SAMPLES = 6


@dataclass(frozen=True)
class Crossing:
    """When a stone crossed the line, or why we will not say."""

    t: float | None
    reason: str
    width_px: float = 0.0

    def __bool__(self) -> bool:
        return self.t is not None


def _colour_mask(win, color):
    r, g, b = win[:, :, 0], win[:, :, 1], win[:, :, 2]
    if color == "red":
        return (r - np.maximum(g, b) > 28) & (r > 90)
    return (np.minimum(r, g) - b > 40) & (r > 120) & (g > 110)


def _runs(flags, min_len=1):
    out, start = [], None
    for i, on in enumerate(list(flags) + [False]):
        if on and start is None:
            start = i
        elif not on and start is not None:
            if i - start >= min_len:
                out.append((start, i))
            start = None
    return out


def _candidates(win, color, expect_px):
    """Every coloured blob with a granite body of the right width under it."""
    mask = _colour_mask(win, color)
    if mask.sum() < 20:
        return []
    grey = win.mean(axis=2)
    ice = np.percentile(grey, 90)
    out = []
    for x0, x1 in _runs(mask.sum(axis=0) > 0):
        sub = mask[:, x0:x1]
        if sub.sum() < 20:
            continue
        # Scan from just below the handle, not from it: the handle's own
        # colour patch is itself a few pixels wide and dark enough to pass
        # for a narrow "body" on its own, which is exactly a broom pad with
        # no stone under it -- a bare colour patch, no granite.
        handle_bottom = int(np.max(np.nonzero(sub)[0]))
        cx = (x0 + x1) // 2
        half = int(expect_px * 0.9)
        band = grey[:, max(0, cx - half):cx + half]
        wide = (band < ice - _BODY_DARKER_THAN_ICE).sum(axis=1)
        # Contiguous from there only, and only while the dark span looks
        # stone-sized: a full-width feature nearby -- the hog line, or the
        # house's own painted rings, both wider than any stone -- fills the
        # whole band and would otherwise get counted as part of the body just
        # because it lies within the look-ahead window.
        lower, upper = expect_px * 0.45, WIDTH_BOUNDS[1] * expect_px
        rows = []
        for y in range(handle_bottom + 1,
                       min(handle_bottom + 1 + int(expect_px), band.shape[0])):
            if not lower < wide[y] <= upper:
                break
            rows.append(y)
        if not rows:
            continue
        body = float(max(wide[y] for y in rows))
        if not WIDTH_BOUNDS[0] * expect_px <= body <= WIDTH_BOUNDS[1] * expect_px:
            continue
        out.append((float(cx), _sub_row(rows, wide, lower), body))
    return out


def _sub_row(rows, wide, lower):
    """Where the body's trailing edge crosses ``lower``, to the sub-pixel.

    ``rows`` is one discrete pixel row per frame; snapping to it would let a
    delivery's crossing time land exactly on a sampled frame time by nothing
    more than where the stone happened to fall on the pixel grid.
    """
    y = rows[-1]
    nxt = y + 1
    if nxt >= len(wide) or wide[nxt] >= wide[y]:
        return float(y)
    a, b = wide[y], wide[nxt]
    return y + max(0.0, min(1.0, (a - lower) / (a - b)))


def find_in_frames(frames, view, color, times) -> Crossing:
    """Time the crossing of ``view.hog_row`` in already-decoded frames."""
    x, y, _w, _h = view.rect
    tracks: dict[int, list] = {}
    for frame, t in zip(frames, times):
        win = np.asarray(frame, dtype=np.float32)[y:, x:]
        for cx, edge, body in _candidates(win, color, STONE_WIDTH_AT_HOG_PX):
            key = int(cx // 120)          # a stone never moves 120 px sideways
            tracks.setdefault(key, []).append((t, edge, body))

    moving = [tr for tr in tracks.values()
              if len(tr) >= _MIN_SAMPLES and tr[-1][1] > tr[0][1]]
    if not moving:
        return Crossing(None, "no candidate that could be a stone in flight")
    crossed = [tr for tr in moving
               if tr[0][1] <= view.hog_row <= tr[-1][1]]
    if len(crossed) > 1:
        return Crossing(None, f"two candidates crossed the line ({len(crossed)})")
    if not crossed:
        return Crossing(None, "the stone never reached the line")
    track = crossed[0]
    if any(b - a < -1.0 for (_, a, _), (_, b, _) in zip(track, track[1:])):
        return Crossing(None, "the candidate did not travel steadily")
    span = track[-1][0] - track[0][0]
    if span > 0:
        metres = abs(view.metres_at(track[-1][1]) - view.metres_at(track[0][1]))
        speed = metres / span
        if not SPEED_BOUNDS_M_S[0] <= speed <= SPEED_BOUNDS_M_S[1]:
            return Crossing(None, f"speed {speed:.2f} m/s is not a delivery")
    for (t0, r0, _), (t1, r1, _) in zip(track, track[1:]):
        if r0 <= view.hog_row <= r1 and r1 != r0:
            frac = (view.hog_row - r0) / (r1 - r0)
            return Crossing(t0 + frac * (t1 - t0), "ok",
                            width_px=float(np.median([b for _, _, b in track])))
    return Crossing(None, "the stone never reached the line")


def decode(video, rect, t0: float, t1: float, fps: float = 30.0):
    """Frames of one window, cropped to the side view. About 0.25 s a call."""
    x, y, w, h = rect
    raw = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{t0}",
         "-i", str(video), "-t", f"{t1 - t0 + 0.05}",
         "-vf", f"crop={w}:{h}:{x}:{y},fps={fps}",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True).stdout
    frames = np.frombuffer(raw, dtype=np.uint8).reshape(-1, h, w, 3)
    return frames, [t0 + i / fps for i in range(frames.shape[0])]


def find_crossing(video, view, color: str, t0: float, t1: float,
                  fps: float = 30.0) -> Crossing:
    """Seek, decode and time one crossing."""
    frames, times = decode(video, view.rect, t0, t1, fps)
    if not len(frames):
        return Crossing(None, "no frames decoded")
    shifted = type(view)(rect=(0, 0, view.rect[2], view.rect[3]),
                         tee_row=view.tee_row, hog_row=view.hog_row, d_m=view.d_m)
    return find_in_frames(frames, shifted, color, times)
