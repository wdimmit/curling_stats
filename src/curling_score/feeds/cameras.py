"""A sheet's four cameras as a feed: each view in its camera's own file.

The recorder writes each camera to ``<role>.ts`` (``cameras/recorder.py``), on
the recording's clock. A view's ``Cut`` is its camera's ``Placement`` from the
sheet layout: the house cameras turned a quarter and scaled to their panel's
size, as YouTube's composite has them, so the models see what they were
trained on; the long cameras a plain crop, since they are placed 1:1.

The view names stay the composite's. The layouts put house_h above house_a
and long_a left of long_h on every sheet, so ``top`` is house_h and ``left``
long_a; the board hangs in long_h's picture, and is read through long_h's crop
(``board_cut``).
"""

from __future__ import annotations

from pathlib import Path

from curling_score.cameras.layout import Placement, SheetLayout
from curling_score.feeds.cut import Cut
from curling_score.feeds.feed import Feed

VIEW_ROLE = {"top": "house_h", "bottom": "house_a", "left": "long_a",
             "right": "long_h", "board": "long_h"}
# The views a calibration has: two panels and two side views. The board is
# long_h's picture, read whole within long_h's crop (``board_cut``).
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


def board_cut(feed) -> Cut | None:
    """How the wall board's frames are taken: on a camera feed, long_h through
    its placement's crop -- the pixels YouTube's composite shows, where the
    home board is the only whole one. The raw camera also shows the next
    sheets' boards whole, and ``scoreboard.find_board`` took one of those
    (Gate A, 11/05 sheets 3 and 4). None for a YouTube feed: the whole frame."""
    from curling_score.cameras import layout

    if feed.kind != "cameras":
        return None
    return view_cut("board", placement_for(layout.load(feed.sheet), "board"))


# --- calibrating a camera feed ----------------------------------------------------
#
# Calibration finds the panels' rings and paint and the side views' lines in
# whole composite frames (`analyze.calibrate_from`). A camera feed has none, so
# its views' keyframes are composed into one, each where the layout puts it,
# and the panels and side views are taken to be exactly there: nothing is
# looked for that the layout already says. What is found there is in
# composite pixels, which differ from the view's own by its placement's
# origin alone; ``localize`` moves it into the view's.


def view_keyframes(feed: Feed, cuts: dict[str, Cut], until_s: float, count: int, *,
                   start_s: float | None = None) -> dict[str, list]:
    """Each view's keyframes, cut, spread over ``[start_s or 0, until_s]``:
    ``count`` of them, or as many as the view with fewest has. The cameras
    key independently, so the i-th of each view is of about the same moment,
    which is as near as a calibration's median needs."""
    from curling_score.ingest import frames as F

    views = {n: F.spread_keyframes(feed, until_s, count=count, start_s=start_s, cut=cut)
             for n, cut in cuts.items()}
    n = min((len(v) for v in views.values()), default=0)
    return {name: v[:n] for name, v in views.items()}


def compose(views: dict, sheet_layout: SheetLayout):
    """One 1920x1080 BGR frame of the layout's grey with each view's frame
    where its placement puts it: what YouTube showed, for calibrating."""
    import numpy as np

    from curling_score.cameras import layout

    grey = int(sheet_layout.background, 16)
    bgr = (grey & 0xFF, (grey >> 8) & 0xFF, (grey >> 16) & 0xFF)
    frame = np.empty((layout.HEIGHT, layout.WIDTH, 3), np.uint8)
    frame[:] = bgr
    for name, img in views.items():
        x, y, w, h = placement_for(sheet_layout, name).out
        if img.shape[:2] != (h, w):
            raise ValueError(f"{name}: a {img.shape[1]}x{img.shape[0]} frame where its "
                             f"placement is {w}x{h}")
        frame[y:y + h, x:x + w] = img
    return frame


def localize(setups: dict, sideviews: dict | None, sheet_layout: SheetLayout):
    """Panels and side views found in composed frames (at their placements'
    ``out``), moved into their views' own pixels: each rect becomes the whole
    of its view, and its transform the view's cut. Their coordinates were
    already relative to their rects, so nothing else moves."""
    import dataclasses

    def move(name, found):
        p = placement_for(sheet_layout, name)
        if tuple(found.rect) != tuple(p.out):
            raise ValueError(f"{name}: found at {tuple(found.rect)}, not at its "
                             f"placement {tuple(p.out)}")
        cut = view_cut(name, p)
        return dataclasses.replace(found, rect=(0, 0, *cut.out_size), name=name, transform=cut)

    setups = {n: move(n, s) for n, s in setups.items()}
    if sideviews is not None:
        sideviews = {n: move(n, v) for n, v in sideviews.items()}
    return setups, sideviews


def calibrate(feed: Feed, until_s: float, *, skip_longview: bool, progress,
              start_s: float | None = None):
    """``analyze.calibrate_from`` for a camera feed: ``(panels, setups,
    sideviews)``, the panels as composed and the rest in their views' pixels,
    from keyframes over ``[start_s or 0, until_s]``."""
    from curling_score import analyze
    from curling_score.cameras import layout
    from curling_score.geometry.calibrate import CalibrationError
    from curling_score.geometry.layout import PanelLayout

    sheet_layout = layout.load(feed.sheet)
    views = view_keyframes(feed, view_cuts(sheet_layout), until_s, analyze.CALIB_FRAMES,
                           start_s=start_s)
    n = len(views["top"])
    if n < 2:
        raise CalibrationError(f"{n} keyframe(s) in the first {until_s:.0f} s; need at least two")
    frames = [compose({name: v[i] for name, v in views.items()}, sheet_layout) for i in range(n)]

    def out(name):
        return tuple(placement_for(sheet_layout, name).out)

    panels, setups, sideviews = analyze.calibrate_from(
        frames, skip_longview=skip_longview, progress=progress,
        panels=PanelLayout(top=out("top"), bottom=out("bottom")),
        side_rects={"left": out("left"), "right": out("right")})
    setups, sideviews = localize(setups, sideviews, sheet_layout)
    return panels, setups, sideviews
