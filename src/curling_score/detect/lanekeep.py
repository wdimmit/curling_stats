"""A grey strip of the delivery lane, kept from the long camera's own decodes.

The pipeline already decodes the long camera twice a rock -- hogtime's hog-line
read and linetime's delivery read, both through `longview.decode`. Reading the
release point needs the thrower's hands over the same seconds, and decoding the
camera a third time costs more than keeping what the first two already hold.
A `LaneKeeper` is offered those decodes (`longview.keeping` wires it into
`decode`) and keeps, for the windows it was given, a small grey crop of the
lane: every source frame, once, however many decodes covered it.
"""
from __future__ import annotations

import math

import cv2
import numpy as np

# The lane, in metres from the far tee toward the camera: from just past the
# tee to the hack end of the throw. Up and down are how far past those two
# rows the strip reaches, for a hand reaching over the stone and a boot under
# it; across is either side of the centre line.
FROM_M = -0.5
TO_M = 8.4
UP_M = 0.6
DOWN_M = 0.15
ACROSS_M = 1.5

# The club's videos are 30 fps, frames on a 1/30 s grid. Every frame is kept;
# `every` stays a parameter so keeping every other one is a change to this.
SOURCE_FPS = 30.0
KEEP_EVERY = 1

# Slack on frame times, so a label a float's width off a frame boundary
# still lands on that frame, and a window edge on a frame is not lost to it.
_EPS = 1e-6


def strip_box(view) -> tuple[int, int, int, int]:
    """``(row0, row1, col0, col1)`` of the lane in the view's own pixels,
    clipped to the view: the rows from the far end of the lane to the near
    end, the columns wide enough for ``ACROSS_M`` either side of the centre
    line on both."""
    top, bottom = view.row_for(FROM_M), view.row_for(TO_M)
    row0 = top - UP_M * view.lateral_px_per_m(top)
    row1 = bottom + DOWN_M * view.lateral_px_per_m(bottom)
    cols = []
    for row in (top, bottom):
        centre = view.centre_col_at(row)
        reach = ACROSS_M * view.lateral_px_per_m(row)
        cols += [centre - reach, centre + reach]
    _x, _y, w, h = view.rect
    return (max(0, math.floor(row0)), min(h, math.ceil(row1)),
            max(0, math.floor(min(cols))), min(w, math.ceil(max(cols))))


class LaneKeeper:
    """Strips of one view's lane for the given ``windows`` of source time,
    each half-open ``[t0, t1)``.

    Frames are keyed by their source frame index, so two decodes that overlap
    -- hogtime's and linetime's -- keep a frame once, and the time a frame is
    kept under is the one it was filmed at.
    """

    def __init__(self, view, windows, every: int = KEEP_EVERY):
        self.view = view
        self.windows = [(float(t0), float(t1)) for t0, t1 in windows]
        self.every = every
        self._row0, self._row1, self._col0, self._col1 = strip_box(view)
        self._strips: dict[int, np.ndarray] = {}

    @property
    def origin(self) -> tuple[int, int]:
        """``(col0, row0)`` of the strips in the view's pixels."""
        return self._col0, self._row0

    @property
    def nbytes(self) -> int:
        return sum(s.nbytes for s in self._strips.values())

    def clear(self) -> None:
        self._strips.clear()

    def _wanted(self, t: float) -> bool:
        return any(t0 - _EPS <= t < t1 - _EPS for t0, t1 in self.windows)

    def offer(self, rect, frames, times, fps) -> None:
        """Keep what is wanted of one decode: ``frames`` RGB, cropped to
        ``rect``, labelled ``times`` as `longview.decode` does.

        Ignored unless it is this view's own rect decoded at the source frame
        rate. A 10 fps decode of this camera labels its frames from ``t0`` in
        tenths, and ffmpeg's frame for a label can lag it by up to a source
        frame, so those labels name no frame exactly.

        `decode` labels frame i ``t0 + i / fps``, but ffmpeg hands back the
        first source frame at or after ``t0``, so the frames are the source
        frames from ``ceil(t0 * 30)``, and that index is what names them.
        """
        if fps != SOURCE_FPS or len(frames) == 0:
            return
        if tuple(rect) != tuple(self.view.rect):
            return
        first = math.ceil(times[0] * SOURCE_FPS - _EPS)
        for i, frame in enumerate(frames):
            idx = first + i
            if idx % self.every or idx in self._strips:
                continue
            if not self._wanted(idx / SOURCE_FPS):
                continue
            crop = frame[self._row0:self._row1, self._col0:self._col1]
            self._strips[idx] = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)

    def frames_between(self, t0: float, t1: float) -> tuple[np.ndarray, np.ndarray]:
        """The strips kept with time in ``[t0, t1)``: ``(N, h, w)`` uint8 and
        their ``(N,)`` times, earliest first."""
        have = sorted(idx for idx in self._strips
                      if t0 - _EPS <= idx / SOURCE_FPS < t1 - _EPS)
        if not have:
            return (np.empty((0, self._row1 - self._row0, self._col1 - self._col0),
                             dtype=np.uint8), np.empty(0))
        return (np.stack([self._strips[i] for i in have]),
                np.array([i / SOURCE_FPS for i in have]))
