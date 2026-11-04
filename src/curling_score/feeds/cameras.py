"""A sheet's four cameras as a feed: each view in its camera's own file.

The recorder writes each camera to ``<role>.ts`` (``cameras/recorder.py``), on
the recording's clock. A view's ``Cut`` is its camera's ``Placement`` from the
sheet layout: the house cameras turned a quarter and scaled to their panel's
size, as YouTube's composite has them, so the models see what they were
trained on; the long cameras a plain crop, since they are placed 1:1.

The view names stay the composite's. The layouts put house_h above house_a
and long_a left of long_h on every sheet, so ``top`` is house_h and ``left``
long_a; the board hangs in long_h's picture.
"""

from __future__ import annotations

from pathlib import Path

from curling_score.cameras.layout import Placement, SheetLayout
from curling_score.feeds.cut import Cut
from curling_score.feeds.feed import Feed

VIEW_ROLE = {"top": "house_h", "bottom": "house_a", "left": "long_a",
             "right": "long_h", "board": "long_h"}
# The views a calibration has: two panels and two side views. The board is
# long_h's picture, read whole.
CUT_VIEWS = ("top", "bottom", "left", "right")


def camera_feed(directory, sheet: int) -> Feed:
    """The feed of a recording in ``directory``: each view in its camera's file."""
    directory = Path(directory)
    return Feed("cameras", {v: directory / f"{role}.ts" for v, role in VIEW_ROLE.items()},
                sheet=sheet)


def placement_for(sheet_layout: SheetLayout, name: str) -> Placement:
    """The placement of view ``name``'s camera."""
    role = VIEW_ROLE[name]
    for p in sheet_layout.placements:
        if p.role == role:
            return p
    raise LookupError(f"sheet {sheet_layout.sheet}'s layout has no {role}")


def view_cut(name: str, p: Placement) -> Cut:
    """How view ``name`` is taken from its camera's file. A camera placed 1:1
    is a plain crop: ``Cut.from_placement`` would give it a scale to its own
    size, which is the same picture but not a crop, so it would read through
    a filter graph and miss the crop's cache keys."""
    if p.rotate == 0 and tuple(p.out[2:]) == tuple(p.crop[2:]):
        return Cut(name, p.crop)
    return Cut.from_placement(name, p)


def view_cuts(sheet_layout: SheetLayout) -> dict[str, Cut]:
    """Every calibrated view's cut: the two panels and the two side views."""
    return {n: view_cut(n, placement_for(sheet_layout, n)) for n in CUT_VIEWS}
