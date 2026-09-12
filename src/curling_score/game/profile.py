"""Build a whole-video activity profile: stone counts per panel over time.

Uses the keyframe sweep, which decodes roughly one frame per five seconds. The
full four-hour reference VOD profiles in about a minute.
"""

from dataclasses import dataclass

from curling_score.detect import rocks
from curling_score.game.segment import Sample
from curling_score.geometry import calibrate, layout, lighting


@dataclass(frozen=True)
class PanelSetup:
    """Everything needed to read one panel: where it is and how it maps."""

    rect: tuple[int, int, int, int]
    calib: calibrate.PanelCalib

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


def calibrate_panel(frames, rect, delivery_side: str, name: str = "panel") -> PanelSetup:
    """Calibrate one panel from the best-lit, cleanest frames available.

    The idle house -- the one not being played to -- is clean and unoccluded for
    most of every other end, so a median over well-lit frames gives an
    essentially stone-free, player-free reference to fit the rings on.

    One panel at a time so a caller can keep the panel that worked when the
    other one's lights are out. The club kills the lights on a sheet as it
    finishes while others play on, so that is a normal night, not a broken one.
    """
    import numpy as np

    x, y, w, h = rect
    lit = [f[y : y + h, x : x + w] for f in frames]
    lit = [p for p in lit if lighting.is_calibratable(p)]
    if not lit:
        raise calibrate.CalibrationError(f"{name} panel: no well-lit frames")
    median = np.median(np.stack(lit), axis=0).astype("uint8")
    return PanelSetup(rect=rect, calib=calibrate.solve(median, delivery_side))


def calibrate_panels(frames, panels: layout.PanelLayout):
    """Calibrate both panels, or raise if either cannot be read."""
    rects = {"top": panels.top, "bottom": panels.bottom}
    return {
        name: calibrate_panel(frames, rects[name], side, name)
        for name, side in PANEL_SIDES
    }


def count_frame(frame, setups) -> tuple[int, int, bool, bool]:
    """Stone counts and playability for both panels of one frame."""
    counts, playable = {}, {}
    for name in ("top", "bottom"):
        panel = setups[name].crop(frame)
        ok = lighting.is_playable(panel)
        playable[name] = ok
        counts[name] = len(rocks.find_stones(panel, setups[name].calib)) if ok else 0
    return counts["top"], counts["bottom"], playable["top"], playable["bottom"]


def build_profile(video_path, setups, sweep=None) -> list[Sample]:
    """Sweep the whole video and record activity at every keyframe."""
    from curling_score.ingest import frames as F

    source = sweep if sweep is not None else F.keyframe_sweep(video_path)
    out = []
    for t, frame in source:
        top, bottom, tp, bp = count_frame(frame, setups)
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
