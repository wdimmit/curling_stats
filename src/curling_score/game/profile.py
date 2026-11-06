"""Build a whole-video activity profile: stone counts per panel over time.

Uses the keyframe sweep, which decodes roughly one frame per five seconds. The
full four-hour reference VOD profiles in about a minute.

The stones are counted by the trained detector whenever there is one. The
colour thresholds also count people: 10/15 Thursday Mens sheet 1 read one to
three "stones" in the top house all through the 7 pm to 9 pm changeover -- the
ice being cleaned, the next teams coming on -- so the sheet never sat empty
long enough to end the game, and the last end ran on 400 s past its last rock
into a gap too short for the board to be asked. One fourteen-end game was
charted where two of seven were played. Later that night, players walking
through the far house read as two to four stones and cut an end off after its
eleventh rock. Of the 39 stones the thresholds counted in the top house over
that changeover, ds17b counted 2, and of the 63 in the far house over that end,
4; on its counts the night splits into its two games.
"""

from dataclasses import dataclass

from curling_score.detect import rocks
from curling_score.feeds import Cut, as_feed
from curling_score.game.segment import Sample
from curling_score.geometry import backpaint, calibrate, hogpaint, layout, lighting, sidepaint


@dataclass(frozen=True)
class PanelSetup:
    """Everything needed to read one panel: where it is and how it maps."""

    rect: tuple[int, int, int, int]
    calib: calibrate.PanelCalib
    # The panel's own hog line, found in its paint (``geometry/hogpaint.py``),
    # or None with the reason in ``hog_line_error``. The destination crossing,
    # and the throwing panel's cross-check, are timed against it; there is no
    # fallback. Defaulted so a setup pickled before this existed still loads --
    # though it then has no line, which ``scripts/replay_end.setups_for``
    # treats as stale.
    hog_line: hogpaint.HogLine | None = None
    hog_line_error: str | None = None
    # The panel's painted sidelines (``geometry/sidepaint.py``): a stone
    # beyond one is on the next sheet, and is not counted or tracked here.
    # None -- a setup pickled before this existed -- excludes nothing.
    sidelines: sidepaint.Sidelines | None = None
    # The view's name (``top``/``bottom``) and, for a camera that needs more
    # than a crop, how it is taken from its file. Defaulted so a setup pickled
    # before these existed still loads: it is nameless, and ``cut`` is a crop
    # of ``rect`` as it always was.
    name: str = ""
    transform: Cut | None = None

    def __post_init__(self):
        # What the readers of ``rect`` slice is the transform's output, so the
        # two must describe one panel: ``rect`` is the whole of it.
        t = self.transform
        if t is not None:
            if t.name != self.name:
                raise ValueError(
                    f"panel {self.name!r}: transform is for {t.name!r}")
            if tuple(self.rect) != (0, 0, *t.out_size):
                raise ValueError(
                    f"panel {self.name!r}: rect {tuple(self.rect)} is not the "
                    f"transform's output (0, 0, {t.out_size[0]}, {t.out_size[1]})")

    @property
    def cut(self) -> Cut:
        """How this panel is taken from its file: ``transform``, else a crop
        of ``rect``."""
        return self.transform or Cut(self.name, tuple(self.rect))

    # The panel's painted back line (``geometry/backpaint.py``): a stone
    # wholly beyond it is out of play, and the activity profile does not count
    # it. None -- not found, or a setup pickled before this existed -- excludes
    # nothing; ``back_line_error`` says why it was not found.
    back_line: backpaint.BackLine | None = None
    back_line_error: str | None = None

    def crop(self, frame):
        if self.transform is not None:
            return self.cut.apply(frame)
        x, y, w, h = self.rect
        return frame[y : y + h, x : x + w]

    def on_sheet(self, stones):
        """``stones`` without those beyond the panel's painted sidelines."""
        if self.sidelines is None:
            return stones
        return [s for s in stones if not self.sidelines.beyond(s.x_px, s.y_px)]

    def in_play(self, stones):
        """``stones`` on this sheet (:meth:`on_sheet`) without those wholly
        beyond the panel's painted back line: the stones that can keep an end
        going. Only the activity profile asks; tracking still follows a stone
        through the back line, which is how a delivery that leaves play is told
        from one that stops."""
        stones = self.on_sheet(stones)
        if self.back_line is None:
            return stones
        return [s for s in stones if not self.back_line.beyond(s.x_px, s.y_px, s.area_px)]

    @property
    def view_x_limit_m(self) -> float:
        """How far to either side of the centre line this panel can see.

        The side lines fall *outside* the overhead view -- these panels reach
        about 1.9 m against a side line at 2.233 m -- so a stone that rolls out
        is lost from view before it ever crosses the line. Delivery detection
        needs the edge of the view, not the edge of the sheet, to tell a
        shooter that left play from one that was merely occluded.
        """
        _x, _y, w, h = self.rect
        corners = ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1))
        return min(abs(self.calib.to_sheet(px, py)[0]) for px, py in corners)

    @property
    def view_y_min_m(self) -> float:
        """How far past the tee, down-sheet, this panel can see.

        On the club's top camera this is the back line itself, -1.97 m: a
        stone running through the house leaves the picture at the very moment
        it leaves play, and a box clipped by the image edge never puts its
        centre beyond the line. Whether such a stone "crossed the back line"
        has to be judged against the edge of the view, not the line.
        """
        _x, _y, w, h = self.rect
        corners = ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1))
        return min(self.calib.to_sheet(px, py)[1] for px, py in corners)


# Which way stones enter each panel: the two houses are played toward from
# opposite directions.
PANEL_SIDES = (("top", "bottom"), ("bottom", "top"))


def panel_median(frames, rect, name: str = "panel"):
    """The idle house's median over well-lit frames, BGR, cropped to ``rect``.

    What the rings and the hog line's paint are both read from. Split out of
    ``calibrate_panel`` so a test fixture can be built from exactly the image
    the pipeline sees.
    """
    import numpy as np

    x, y, w, h = rect
    lit = [f[y : y + h, x : x + w] for f in frames]
    lit = [p for p in lit if lighting.is_calibratable(p)]
    if not lit:
        raise calibrate.CalibrationError(f"{name} panel: no well-lit frames")
    return np.median(np.stack(lit), axis=0).astype("uint8")


def calibrate_panel(frames, rect, delivery_side: str, name: str = "") -> PanelSetup:
    """Calibrate one panel from the best-lit, cleanest frames available.

    The idle house -- the one not being played to -- is clean and unoccluded for
    most of every other end, so a median over well-lit frames gives an
    essentially stone-free, player-free reference to fit the rings on.

    One panel at a time so a caller can keep the panel that worked when the
    other one's lights are out. The club kills the lights on a sheet as it
    finishes while others play on, so that is a normal night, not a broken one.

    ``name`` is the panel's (``top``/``bottom``) and is stored on its setup,
    ``""`` when unknown; errors call it "panel" then.
    """
    label = name or "panel"
    median = panel_median(frames, rect, label)
    calib = calibrate.solve(median, delivery_side)
    # The same median the rings were read from shows the hog line's paint. A
    # panel whose paint cannot be trusted keeps its calibration and says why;
    # it simply times no far crossings.
    try:
        line, err = hogpaint.find_hog_line(median, calib), None
    except hogpaint.HogPaintError as exc:
        line, err = None, f"{label} panel: {exc}"
    # And the sidelines' paint, where the panel reaches them, and the back line's.
    back, back_err = backpaint.find_back_line(median, calib)
    return PanelSetup(rect=rect, calib=calib, hog_line=line, hog_line_error=err,
                      sidelines=sidepaint.find_sidelines(median, calib), name=name,
                      back_line=back,
                      back_line_error=None if back_err is None else f"{label} panel: {back_err}")


def calibrate_panels(frames, panels: layout.PanelLayout):
    """Calibrate both panels, or raise if either cannot be read."""
    rects = {"top": panels.top, "bottom": panels.bottom}
    return {
        name: calibrate_panel(frames, rects[name], side, name)
        for name, side in PANEL_SIDES
    }


def count_frame(frame, setups, detector=None) -> tuple[int, int, bool, bool]:
    """Stone counts and playability for both panels of one frame, counted by
    ``detector`` -- a trained one, see the module docstring -- or, without
    one, by the colour thresholds. A panel too dark to play counts none, and
    no panel counts a stone beyond its painted sidelines or wholly beyond its
    painted back line (`PanelSetup.in_play`)."""
    return _count({name: setups[name].crop(frame) for name in ("top", "bottom")},
                  setups, detector)


def count_views(views: dict, setups, detector=None) -> tuple[int, int, bool, bool]:
    """:func:`count_frame` for a camera feed: each house from its own view's
    picture (``views["top"]``, ``views["bottom"]``), as :func:`feed_sweep`
    pairs them -- already cut to the view, so the picture is the panel."""
    return _count({name: views[name] for name in ("top", "bottom")}, setups, detector)


def _count(panels, setups, detector):
    playable = {name: lighting.is_playable(panel) for name, panel in panels.items()}
    lit = [name for name in ("top", "bottom") if playable[name]]
    counts = {"top": 0, "bottom": 0}
    if detector is not None:
        found = detector.find_stones_each([panels[n] for n in lit],
                                          [setups[n].calib for n in lit])
    else:
        found = [rocks.find_stones(panels[n], setups[n].calib) for n in lit]
    for name, stones in zip(lit, found):
        counts[name] = len(setups[name].in_play(stones))
    return counts["top"], counts["bottom"], playable["top"], playable["bottom"]


def panels_file(video):
    """The one file both panels are read from: ``video`` is a path or a ``Feed``.

    The profile sweeps the keyframes of that file and counts the stones in
    both panels of each, so two files are two sweeps, which phase 2's cameras
    need and this does not do.
    """
    feed = as_feed(video)
    top, bottom = feed.path_for("top"), feed.path_for("bottom")
    if top != bottom:
        raise NotImplementedError("phase 2: the panels are in two files")
    return top


# A camera feed's two house cameras send keyframes on their own clocks, each
# about every 2 s: a top keyframe is paired with the nearest bottom one within
# this, and one with none that near is no sample.
PAIR_S = 1.1


def feed_sweep(video, start_s=None, end_s=None):
    """``(t, picture)`` for every keyframe the activity profile reads: of the
    one file both houses are in, or -- a camera feed, each house in its own
    camera's file -- ``(t, {"top": picture, "bottom": picture})``, every top
    keyframe with the nearest bottom one (:data:`PAIR_S`)."""
    from curling_score.ingest import frames as F

    feed = as_feed(video)
    top, bottom = feed.path_for("top"), feed.path_for("bottom")
    if top == bottom:
        return F.keyframe_sweep(top, start_s=start_s, end_s=end_s)
    return _paired_sweep(feed, start_s, end_s)


sweep = feed_sweep


def _paired_sweep(feed, start_s, end_s):
    from collections import deque

    from curling_score.cameras import layout as camera_layout
    from curling_score.feeds.cameras import view_cuts
    from curling_score.ingest import frames as F

    cuts = view_cuts(camera_layout.load(feed.sheet))
    tops = F.keyframe_sweep(feed, start_s=start_s, end_s=end_s, cut=cuts["top"])
    bottoms = F.keyframe_sweep(feed, start_s=start_s,
                               end_s=None if end_s is None else end_s + PAIR_S,
                               cut=cuts["bottom"])
    buf, done = deque(), False
    for t, top in tops:
        while not done and (not buf or buf[-1][0] <= t + PAIR_S):
            try:
                buf.append(next(bottoms))
            except StopIteration:
                done = True
        while buf and buf[0][0] < t - PAIR_S:
            buf.popleft()
        near = [b for b in buf if abs(b[0] - t) <= PAIR_S]
        if near:
            _bt, bottom = min(near, key=lambda b: abs(b[0] - t))
            yield t, {"top": top, "bottom": bottom}


def build_profile(video, setups, sweep=None, detector=None) -> list[Sample]:
    """Sweep the whole video and record activity at every keyframe, the stones
    counted by ``detector`` when there is one (:func:`count_frame`).
    ``video`` is a path or a ``Feed``; a ``sweep`` already made is used as is,
    and a camera feed's pairs each house's picture (:func:`feed_sweep`)."""
    source = sweep if sweep is not None else feed_sweep(video)
    out = []
    for t, frame in source:
        count = count_views if isinstance(frame, dict) else count_frame
        top, bottom, tp, bp = count(frame, setups, detector)
        out.append(
            Sample(
                t=t,
                top_stones=top,
                bottom_stones=bottom,
                top_playable=tp,
                bottom_playable=bp,
            )
        )
    return out
