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

# How wide a stone reads at the hog line, and how far from that a candidate may
# be before it is refused.
#
# UNMEASURED, both of them, and they are load-bearing: every width gate and
# every proposed label box rests on the 52. It comes from a single check
# against the reference VOD's side views during Task 4 of the long-camera plan
# -- the geometry predicted 53.6 px from a camera 40.233 m out and about 52 was
# what a stone actually spanned -- not from a distribution over sheets. The
# bounds were then set wide enough to keep every crossing that pass found.
#
# What would measure them: the widths the detector already records. Crossing
# carries width_px on every success, and scripts/split_coverage.py walks every
# shot of every cached video -- so a run of it over more than one video yields
# the distribution these two numbers are standing in for, per sheet and per
# view. Worth doing before either is trusted on a sheet nobody has checked.
WIDTH_BOUNDS = (0.5, 1.6)
STONE_WIDTH_AT_HOG_PX = 52.0
# A delivery crosses its hog line between 1.2 and 3.2 m/s -- the range the 27
# hand-marked crossings imply. Slower than that is a stone being nudged aside,
# or a tracker that has drifted onto one already at rest.
SPEED_BOUNDS_M_S = (1.2, 3.2)
# How much darker than the ice granite is, in luminance levels.
#
# The painted hog line dips about 25 levels below the ice (238 -> ~212), so
# this sits deliberately ABOVE that: a threshold below 25 would pick the paint
# up as the stone's own trailing edge, which is the one confusion it exists to
# prevent. (This comment used to say "well below it", which is backwards
# against its own value and against tests/synth.py:69-76, where the same
# reasoning is stated correctly.)
#
# The 45 itself is a reasoned estimate, not a measurement: it is comfortably
# clear of the paint's 25 and comfortably under granite's own contrast against
# ice. Measuring it would mean sampling the luminance gap between stone and ice
# across the five sheets' side views, the way _GREEN_THRESHOLD in
# geometry/sideview.py was measured against real plates.
BODY_DARKER_THAN_ICE = 45
_MIN_SAMPLES = 6

# The detector's own trailing edge is the stone's lowest dark row, which
# includes its contact shadow -- a little past where a person judged the
# leading edge to touch the paint's near side, so the raw crossing reads
# early. Measured as the median error across the 22 of 27 hand-marked
# crossings (``datasets/hogmarks``) this detector could resolve at all:
# -0.073 s. The other 5 are not scatter around that median -- they are a
# different failure (see tests/test_longview.py::TestAgainstHandMarkedCrossings
# and task-5-report.md) that this constant is not meant to paper over.
OFFSET_S = 0.073


# Stable, closed set of kinds ``Crossing.key`` takes. ``reason`` stays free
# prose for a person (it carries a speed or a candidate count); a caller that
# wants to bucket many refusals -- a coverage report -- should count on
# ``key`` instead, since two different speeds or candidate counts are the
# same *kind* of refusal and prose fragments a histogram into one row each.
KEY_OK = "ok"
KEY_NO_FRAMES = "no_frames"
KEY_NO_CANDIDATE = "no_candidate"
KEY_AMBIGUOUS = "ambiguous"
KEY_NEVER_REACHED = "never_reached"
KEY_UNSTEADY = "unsteady"
KEY_BAD_SPEED = "bad_speed"

KEYS = frozenset({
    KEY_OK, KEY_NO_FRAMES, KEY_NO_CANDIDATE, KEY_AMBIGUOUS,
    KEY_NEVER_REACHED, KEY_UNSTEADY, KEY_BAD_SPEED,
})


@dataclass(frozen=True)
class Crossing:
    """When a stone crossed the line, or why we will not say.

    ``key`` is ``reason``'s stable classification, drawn from the closed set
    ``KEYS`` -- see the note above it. Defaults to ``""`` (unclassified) so a
    ``Crossing`` built outside this module, such as a test double standing in
    for the detector, is not forced to pick one.
    """

    t: float | None
    reason: str
    key: str = ""
    # The median body width over the track. Nothing reads it today -- it is
    # kept because it is the measurement STONE_WIDTH_AT_HOG_PX and WIDTH_BOUNDS
    # are standing in for without one (see their comment above), and it costs a
    # median over a list that already exists. A run of
    # scripts/split_coverage.py that collected these would settle both numbers.
    width_px: float = 0.0

    def __bool__(self) -> bool:
        return self.t is not None


def colour_mask(win, color):
    """Which pixels of ``win`` read as ``color``'s stone handle.

    Public because ``harvest/sidepool.py`` needs the same colour test to tell
    a moment with a coloured blob under no granite (``occluded``) from a
    moment with no colour at all (``clear``); that is another package, so it
    must import this rather than carry a second copy that could drift.
    """
    r, g, b = win[:, :, 0], win[:, :, 1], win[:, :, 2]
    if color == "red":
        return (r - np.maximum(g, b) > 28) & (r > 90)
    return (np.minimum(r, g) - b > 40) & (r > 120) & (g > 110)


def runs(flags, min_len=1):
    """Contiguous ``True`` spans of ``flags`` as ``(start, stop)`` pairs.

    Public because ``harvest/sidepool.py`` needs the same run-finding to spot
    a body standing beside a stone (``crowding``); that is another package, so
    it must import this rather than carry a second copy that could drift.
    """
    out, start = [], None
    for i, on in enumerate(list(flags) + [False]):
        if on and start is None:
            start = i
        elif not on and start is not None:
            if i - start >= min_len:
                out.append((start, i))
            start = None
    return out


@dataclass(frozen=True)
class Proposal:
    """One candidate stone, as a box a person can correct.

    Every edge is measured: ``body_px`` and ``edge_row`` from the granite's
    dark span, ``top_row`` from the colour handle's own topmost row. Nothing
    here assumes how tall a stone is.
    """

    cx: float
    top_row: float
    edge_row: float
    body_px: float


def candidates(win, color, expect_px):
    """Every coloured blob with a granite body of the right width under it."""
    mask = colour_mask(win, color)
    if mask.sum() < 20:
        return []
    grey = win.mean(axis=2)
    ice = np.percentile(grey, 90)
    out = []
    for x0, x1 in runs(mask.sum(axis=0) > 0):
        sub = mask[:, x0:x1]
        if sub.sum() < 20:
            continue
        # Scan from just below the handle, not from it: the handle's own
        # colour patch is itself a few pixels wide and dark enough to pass
        # for a narrow "body" on its own, which is exactly a broom pad with
        # no stone under it -- a bare colour patch, no granite.
        handle_bottom = int(np.max(np.nonzero(sub)[0]))
        handle_top = int(np.min(np.nonzero(sub)[0]))
        cx = (x0 + x1) // 2
        half = int(expect_px * 0.9)
        band = grey[:, max(0, cx - half):cx + half]
        wide = (band < ice - BODY_DARKER_THAN_ICE).sum(axis=1)
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
        out.append(Proposal(float(cx), float(handle_top),
                            _sub_row(rows, wide, lower), body))
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


def _crossing_index(track, hog_row):
    """Index ``i`` such that ``track[i], track[i + 1]`` straddle ``hog_row``."""
    for i, ((_, r0, _), (_, r1, _)) in enumerate(zip(track, track[1:])):
        if r0 <= hog_row <= r1 and r1 != r0:
            return i
    return None


def find_in_frames(frames, view, color, times) -> Crossing:
    """Time the crossing of ``view.hog_row`` in already-decoded frames."""
    x, y, _w, _h = view.rect
    tracks: dict[int, list] = {}
    for frame, t in zip(frames, times):
        win = np.asarray(frame, dtype=np.float32)[y:, x:]
        for p in candidates(win, color, STONE_WIDTH_AT_HOG_PX):
            key = int(p.cx // 120)        # a stone never moves 120 px sideways
            tracks.setdefault(key, []).append((t, p.edge_row, p.body_px))

    moving = [tr for tr in tracks.values()
              if len(tr) >= _MIN_SAMPLES and tr[-1][1] > tr[0][1]]
    if not moving:
        return Crossing(None, "no candidate that could be a stone in flight", KEY_NO_CANDIDATE)
    crossed = [tr for tr in moving
               if tr[0][1] <= view.hog_row <= tr[-1][1]]
    if len(crossed) > 1:
        # Step 3 of this task's brief suggested picking the crosser whose
        # body width best matches STONE_WIDTH_AT_HOG_PX and refusing only
        # when two are within 15% of each other. Tried against the three
        # hand-marked windows that actually produce two crossers: it does
        # pick the better-matching candidate, but "better of two bad
        # options" is not the same as "a stone" -- on one of the three, the
        # winner still missed by 0.90 s, far worse than refusing. Two
        # candidates this evenly matched by width are not reliably
        # distinguishable this way, so this refuses unconditionally rather
        # than publish a guess with no safety margin behind it.
        return Crossing(None, f"two candidates crossed the line ({len(crossed)})", KEY_AMBIGUOUS)
    if not crossed:
        return Crossing(None, "the stone never reached the line", KEY_NEVER_REACHED)
    track = crossed[0]

    idx = _crossing_index(track, view.hog_row)
    if idx is None:
        return Crossing(None, "the stone never reached the line", KEY_NEVER_REACHED)
    # Steadiness only has to hold up to the crossing itself, and only in the
    # samples immediately around it: what the same x-bin was doing several
    # seconds earlier (often still noisy while the handle first resolves out
    # of the thrower's hand) or afterwards (arriving at the house, passing
    # stones already there) has no bearing on when it passed the hog line,
    # and checking the whole track discarded good crossings over a blip
    # nowhere near the row being measured.
    lo = max(0, idx - (_MIN_SAMPLES - 2))
    local = track[lo:idx + 2]
    if any(b - a < -1.0 for (_, a, _), (_, b, _) in zip(local, local[1:])):
        return Crossing(None, "the candidate did not travel steadily", KEY_UNSTEADY)
    span = track[-1][0] - track[0][0]
    if span > 0:
        metres = abs(view.metres_at(track[-1][1]) - view.metres_at(track[0][1]))
        speed = metres / span
        if not SPEED_BOUNDS_M_S[0] <= speed <= SPEED_BOUNDS_M_S[1]:
            return Crossing(None, f"speed {speed:.2f} m/s is not a delivery", KEY_BAD_SPEED)
    (t0, r0, _), (t1, r1, _) = track[idx], track[idx + 1]
    frac = (view.hog_row - r0) / (r1 - r0)
    t = t0 + frac * (t1 - t0) + OFFSET_S
    return Crossing(t, "ok", KEY_OK, width_px=float(np.median([b for _, _, b in track])))


def decode(video, rect, t0: float, t1: float, fps: float = 30.0):
    """Frames of one window, cropped to the side view. About 0.25 s a call."""
    x, y, w, h = rect
    # crop's dimensions are otherwise rounded down to the input's chroma
    # subsampling (yuv420p halves both axes), silently handing back an even
    # width or height one px short of what was asked for. Every side view's
    # rect comes from wherever the overhead strip's edge lands, which is odd
    # as often as even, so this bites about half of them: real footage did,
    # at rect width 813, and the reshape below failed on the size mismatch.
    # exact=1 crops to the exact pixel count regardless of chroma alignment.
    raw = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{t0}",
         "-i", str(video), "-t", f"{t1 - t0 + 0.05}",
         "-vf", f"crop={w}:{h}:{x}:{y}:exact=1,fps={fps}",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True).stdout
    frames = np.frombuffer(raw, dtype=np.uint8).reshape(-1, h, w, 3)
    return frames, [t0 + i / fps for i in range(frames.shape[0])]


def find_crossing(video, view, color: str, t0: float, t1: float,
                  fps: float = 30.0) -> Crossing:
    """Seek, decode and time one crossing."""
    frames, times = decode(video, view.rect, t0, t1, fps)
    if not len(frames):
        return Crossing(None, "no frames decoded", KEY_NO_FRAMES)
    shifted = type(view)(rect=(0, 0, view.rect[2], view.rect[3]),
                         tee_row=view.tee_row, hog_row=view.hog_row, d_m=view.d_m)
    return find_in_frames(frames, shifted, color, times)
