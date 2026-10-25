"""How a view is taken from a file.

On YouTube a view is a rectangle of the composite, and every reader cropped
with a bare ``(x, y, w, h)``. A camera's own file needs more: the house views
are turned a quarter and scaled to the size the models were trained at
(``cameras/layout.Placement``). A ``Cut`` is that, a crop, then optionally a
quarter turn, then optionally a scale, and a crop-only ``Cut`` is exactly the
old rectangle: the same FFmpeg step, the same numpy slice, the same cache key.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from curling_score.cameras.layout import Placement

Rect = tuple[int, int, int, int]  # x, y, w, h

ROTATIONS = (0, 90, -90)
# FFmpeg's transpose: 1 turns clockwise, 2 counter-clockwise, neither flips.
TRANSPOSE = {90: 1, -90: 2}


@dataclass(frozen=True)
class Cut:
    """``crop`` (the source file's pixels), turned ``rotate`` degrees clockwise,
    then scaled to ``size`` (w, h). ``name`` is the view's, ``""`` when it
    isn't known (a rect from before views had names)."""

    name: str
    crop: Rect
    rotate: int = 0
    size: tuple[int, int] | None = None

    def __post_init__(self):
        if self.rotate not in ROTATIONS:
            raise ValueError(
                f"{self.name or 'cut'}: rotate must be one of {ROTATIONS}, not {self.rotate}")

    @property
    def out_size(self) -> tuple[int, int]:
        """(w, h) of what the cut produces: ``size``, else the crop's own,
        swapped by a quarter turn."""
        if self.size is not None:
            return self.size
        _, _, w, h = self.crop
        return (h, w) if self.rotate else (w, h)

    @property
    def is_crop(self) -> bool:
        return self.rotate == 0 and self.size is None

    def steps(self) -> str:
        """The FFmpeg filter chain, without a frame rate."""
        x, y, w, h = self.crop
        # exact: on yuv420p, crop otherwise rounds an odd x, y, w or h down to
        # even, moving a 1:1 view by a pixel.
        steps = [f"crop={w}:{h}:{x}:{y}:exact=1"]
        if self.rotate:
            steps.append(f"transpose={TRANSPOSE[self.rotate]}")
        if self.size is not None:
            steps.append("scale={}:{}".format(*self.size))
        return ",".join(steps)

    def apply(self, bgr: np.ndarray) -> np.ndarray:
        """Cut a decoded frame. Only a crop can be taken in numpy here; a
        turned or scaled cut is made by FFmpeg."""
        if not self.is_crop:
            raise NotImplementedError("phase 2")
        x, y, w, h = self.crop
        return np.ascontiguousarray(bgr[y:y + h, x:x + w])

    def key(self) -> tuple:
        """The cut's part of a detection-cache key. A crop is its rect, as the
        key was before cuts, so warm caches keep hitting."""
        return self.crop if self.is_crop else (self.crop, self.rotate, self.size)

    @classmethod
    def of(cls, rect_or_cut, name: str = "") -> Cut:
        """A ``Cut`` passes through; a 4-tuple becomes a crop."""
        if isinstance(rect_or_cut, cls):
            return rect_or_cut
        x, y, w, h = rect_or_cut
        return cls(name, (int(x), int(y), int(w), int(h)))

    @classmethod
    def from_placement(cls, name: str, p: Placement) -> Cut:
        """The cut that puts a camera where its ``Placement`` says: the scale
        is the placement's ``out`` size (its x, y are only where it lands)."""
        _, _, ow, oh = p.out
        return cls(name, tuple(p.crop), p.rotate, (ow, oh))
