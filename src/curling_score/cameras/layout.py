"""Where each of a sheet's four cameras goes in the picture the tracker reads.

The tracker, its models and its calibration were all built on YouTube's
composite: the two long views either side, and the two house cameras turned
into a grey-barred strip down the middle (``geometry/layout.py``). The camera
path rebuilds that picture from the raw cameras, so each sheet needs the crop,
turn and scale that put each camera where YouTube put it. Those are fitted
once per sheet from a YouTube frame (``scripts/cameras/fit_layout.py``) and
kept in ``layouts/sheet{N}.json``; panel sizes differ by sheet, and sheets 2-4's
long cameras were re-aimed for 2026-27.

``filtergraphs`` turns a layout into the FFmpeg ``-filter_complex`` graphs the
composer runs.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

Rect = tuple[int, int, int, int]  # x, y, w, h

LAYOUTS = Path(__file__).with_name("layouts")
WIDTH, HEIGHT, FPS = 1920, 1080, 60
ROTATIONS = (0, 90, -90)
# FFmpeg's transpose: 1 turns clockwise, 2 counter-clockwise, neither flips.
_TRANSPOSE = {90: 1, -90: 2}


@dataclass(frozen=True)
class Placement:
    """One camera's part of the picture: ``crop`` (raw camera pixels), turned
    by ``rotate`` degrees clockwise, scaled onto ``out`` (composite pixels)."""

    role: str
    crop: Rect
    rotate: int
    out: Rect

    def __post_init__(self):
        if self.rotate not in ROTATIONS:
            raise ValueError(f"{self.role}: rotate must be one of {ROTATIONS}, not {self.rotate}")


@dataclass(frozen=True)
class SheetLayout:
    sheet: int
    placements: tuple[Placement, ...]
    background: str  # the bars' grey, "0xRRGGBB"


def save(sheet_layout: SheetLayout, path, *, fit: dict | None = None) -> None:
    """Write the layout as JSON. ``fit`` is the fitter's record of how it got
    there (which frames, inliers, residuals); ``load`` ignores it."""
    doc = {
        "sheet": sheet_layout.sheet,
        "background": sheet_layout.background,
        "placements": [
            {"role": p.role, "crop": list(p.crop), "rotate": p.rotate, "out": list(p.out)}
            for p in sheet_layout.placements
        ],
    }
    if fit is not None:
        doc["fit"] = fit
    # A rectangle per line reads better than four.
    text = re.sub(r"\[[\s\d.,-]*\]", lambda m: json.dumps(json.loads(m.group())),
                  json.dumps(doc, indent=2))
    Path(path).write_text(text + "\n")


def load(sheet: int, *, root: Path = LAYOUTS) -> SheetLayout:
    path = Path(root) / f"sheet{sheet}.json"
    if not path.exists():
        raise LookupError(
            f"no camera layout for sheet {sheet} ({path}); fit one with scripts/cameras/fit_layout.py")
    doc = json.loads(path.read_text())
    return SheetLayout(
        sheet=doc["sheet"],
        placements=tuple(
            Placement(p["role"], tuple(p["crop"]), p["rotate"], tuple(p["out"]))
            for p in doc["placements"]
        ),
        background=doc["background"],
    )


def filtergraphs(sheet_layout: SheetLayout, *, inputs: dict[str, int]) -> list[str]:
    """The composer's ``-filter_complex`` graphs, in order: one per camera
    ``inputs[role]``, cutting, turning and scaling it; then one laying them
    out over a plain background, each holding its last frame if it stops. The
    result is ``[out]``.

    Nothing is shifted: with ``-copyts`` each camera's frames keep the times
    their capture gave them, seconds since the recording's zero, where the
    background starts.

    FFmpeg runs each graph on a thread of its own (7.0 on). All in one graph,
    that one thread could only just keep up with a sheet's 60 fps: on test
    patterns on the Ryzen it ran at 58-61 fps flat out and 50 paced to real
    time, so any other load put the composer behind for good. Split, it ran at
    98 fps flat out, and at 70 beside 24 busy threads.
    """
    graphs = []
    for p in sheet_layout.placements:
        cx, cy, cw, ch = p.crop
        _, _, ow, oh = p.out
        # exact: on the cameras' yuv420p, crop otherwise rounds an odd x, y, w
        # or h down to even, moving a 1:1 long view by a pixel.
        steps = [f"crop={cw}:{ch}:{cx}:{cy}:exact=1"]
        if p.rotate:
            steps.append(f"transpose={_TRANSPOSE[p.rotate]}")
        steps.append(f"scale={ow}:{oh}")
        graphs.append(f"[{inputs[p.role]}:v]{','.join(steps)}[{p.role}]")
    chains = [f"color=c={sheet_layout.background}:s={WIDTH}x{HEIGHT}:r={FPS}[base]"]
    below = "base"
    for i, p in enumerate(sheet_layout.placements, 1):
        ox, oy, _, _ = p.out
        above = "out" if i == len(sheet_layout.placements) else f"v{i}"
        chains.append(
            f"[{below}][{p.role}]overlay=x={ox}:y={oy}:eof_action=repeat:repeatlast=1[{above}]")
        below = above
    return [*graphs, ";".join(chains)]
