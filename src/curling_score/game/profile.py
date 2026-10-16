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
from curling_score.game.segment import Sample
from curling_score.geometry import calibrate, hogpaint, layout, lighting


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

    def crop(self, frame):
        x, y, w, h = self.rect
        return frame[y : y + h, x : x + w]

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


def calibrate_panel(frames, rect, delivery_side: str, name: str = "panel") -> PanelSetup:
    """Calibrate one panel from the best-lit, cleanest frames available.

    The idle house -- the one not being played to -- is clean and unoccluded for
    most of every other end, so a median over well-lit frames gives an
    essentially stone-free, player-free reference to fit the rings on.

    One panel at a time so a caller can keep the panel that worked when the
    other one's lights are out. The club kills the lights on a sheet as it
    finishes while others play on, so that is a normal night, not a broken one.
    """
    median = panel_median(frames, rect, name)
    calib = calibrate.solve(median, delivery_side)
    # The same median the rings were read from shows the hog line's paint. A
    # panel whose paint cannot be trusted keeps its calibration and says why;
    # it simply times no far crossings.
    try:
        line, err = hogpaint.find_hog_line(median, calib), None
    except hogpaint.HogPaintError as exc:
        line, err = None, f"{name} panel: {exc}"
    return PanelSetup(rect=rect, calib=calib, hog_line=line, hog_line_error=err)


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
    one, by the colour thresholds. A panel too dark to play counts none."""
    panels = {name: setups[name].crop(frame) for name in ("top", "bottom")}
    playable = {name: lighting.is_playable(panel) for name, panel in panels.items()}
    lit = [name for name in ("top", "bottom") if playable[name]]
    counts = {"top": 0, "bottom": 0}
    if detector is not None:
        found = detector.find_stones_each([panels[n] for n in lit],
                                          [setups[n].calib for n in lit])
    else:
        found = [rocks.find_stones(panels[n], setups[n].calib) for n in lit]
    for name, stones in zip(lit, found):
        counts[name] = len(stones)
    return counts["top"], counts["bottom"], playable["top"], playable["bottom"]


def build_profile(video_path, setups, sweep=None, detector=None) -> list[Sample]:
    """Sweep the whole video and record activity at every keyframe, the stones
    counted by ``detector`` when there is one (:func:`count_frame`)."""
    from curling_score.ingest import frames as F

    source = sweep if sweep is not None else F.keyframe_sweep(video_path)
    out = []
    for t, frame in source:
        top, bottom, tp, bp = count_frame(frame, setups, detector)
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
