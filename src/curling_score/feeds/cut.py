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
from fractions import Fraction
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from curling_score.cameras.layout import Placement

Rect = tuple[int, int, int, int]  # x, y, w, h

ROTATIONS = (0, 90, -90)
# Part of a turned or scaled cut's cache key, not a crop's (a crop's key is
# its rect, so warm caches keep hitting). Bump it with any change in how such a
# cut is produced: the scaler's flags, the PyAV graph against numpy, the order
# colour is converted in.
CUT_IMPL = 1
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
        object.__setattr__(self, "crop", tuple(int(v) for v in self.crop))
        if self.size is not None:
            object.__setattr__(self, "size", tuple(int(v) for v in self.size))
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

    def frames(self):
        """``av.VideoFrame -> BGR ndarray`` for one pass over a file.

        A crop converts the frame to BGR and slices it (``apply``), exactly as
        before cuts. A turned or scaled cut runs ``steps()`` in a PyAV filter
        graph, built from the first frame and kept for the pass, then converts
        to BGR in the graph: the chain an FFmpeg reader runs, in the same
        order, so the pixels match it.
        """
        if self.is_crop:
            from av.video.reformatter import VideoReformatter

            reformatter = VideoReformatter()
            return lambda frame: self.apply(
                reformatter.reformat(frame, format="bgr24").to_ndarray())
        graph = None

        def cut(frame):
            nonlocal graph
            if graph is None:
                graph = _graph(frame, self.steps())
            graph.push(frame)
            return np.ascontiguousarray(graph.pull().to_ndarray())

        return cut

    def apply(self, bgr: np.ndarray) -> np.ndarray:
        """Cut a decoded frame. Only a crop can be taken in numpy here; a
        turned or scaled cut is made by ``frames()``.

        This slices the frame after it is converted to BGR; ``steps()`` crops
        in yuv420p, before. They give the same pixels only at even x and y
        (chroma is sited on pairs); at an odd offset the edge's chroma differs.
        ``steps()`` is the reference for pixel parity."""
        if not self.is_crop:
            raise NotImplementedError("a turned or scaled cut is made by frames()")
        x, y, w, h = self.crop
        return np.ascontiguousarray(bgr[y:y + h, x:x + w])

    def key(self) -> tuple:
        """The cut's part of a detection-cache key. A crop is its rect, as the
        key was before cuts, so warm caches keep hitting."""
        if self.is_crop:
            return self.crop
        return (self.crop, self.rotate, self.size, CUT_IMPL)

    @classmethod
    def of(cls, rect_or_cut, name: str = "") -> Cut:
        """A ``Cut`` passes through (a ``name`` that is not its own is an
        error: it would read another view); a 4-tuple becomes a crop."""
        if isinstance(rect_or_cut, cls):
            if name and name != rect_or_cut.name:
                raise ValueError(
                    f"cut is {rect_or_cut.name!r}, not {name!r}")
            return rect_or_cut
        x, y, w, h = rect_or_cut
        return cls(name, (int(x), int(y), int(w), int(h)))

    @classmethod
    def from_placement(cls, name: str, p: Placement) -> Cut:
        """The cut that puts a camera where its ``Placement`` says: the scale
        is the placement's ``out`` size (its x, y are only where it lands)."""
        _, _, ow, oh = p.out
        return cls(name, tuple(p.crop), p.rotate, (ow, oh))


def _graph(frame, steps: str):
    """A configured filter graph taking frames like ``frame`` through
    ``steps`` (an FFmpeg chain, ``name=args`` joined by commas) to BGR."""
    import av.filter

    graph = av.filter.Graph()
    nodes = [graph.add_buffer(width=frame.width, height=frame.height,
                              format=frame.format.name,
                              time_base=frame.time_base or Fraction(1, 1000))]
    for step in steps.split(","):
        name, _, args = step.partition("=")
        nodes.append(graph.add(name, args or None))
    nodes.append(graph.add("format", "bgr24"))
    nodes.append(graph.add("buffersink"))
    graph.link_nodes(*nodes).configure()
    return graph
