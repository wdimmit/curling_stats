"""Work out how to read one video: where the panels are and what a metre is.

Every sheet is laid out differently and every night the cameras may have been
nudged, so this is per-video work that cannot be cached by sheet. It is also
the stage that decides whether a video is usable at all, and it must decide
that *quietly*: 130 unseen VODs will not all work, and a harvest that raises on
the first odd one is a harvest nobody can leave running.

Two failures are expected and handled rather than fatal:

* **No layout.** ``detect_panels`` insists on exactly two panels between three
  bars, and refuses rather than guessing. The video is recorded as unusable.
* **One dark house.** The club turns the lights off on a sheet as it finishes
  while others play on, so a panel that never lights is a normal night. The
  other panel still holds a whole game, so it is kept.

Everything is stored as JSON. ds8's setups were pickled into ``/tmp`` and died
with it, which is the reason that dataset can never be rebuilt.
"""

from dataclasses import dataclass

from curling_score.game.profile import PANEL_SIDES, PanelSetup, calibrate_panel
from curling_score.geometry import calibrate, layout, lighting

MIN_LAYOUT_FRAMES = 2


@dataclass(frozen=True)
class PanelInfo:
    """One house: where it is, what a metre is there, and why not."""

    name: str
    rect: tuple
    calib: calibrate.PanelCalib | None
    lighting: dict
    error: str | None = None

    def setup(self) -> PanelSetup:
        """The PanelSetup the rest of the pipeline expects."""
        if self.calib is None:
            raise ValueError(f"{self.name} panel was never calibrated: {self.error}")
        return PanelSetup(rect=tuple(self.rect), calib=self.calib)


@dataclass(frozen=True)
class VideoSetup:
    video_id: str
    frame_size: tuple
    panels: dict
    frame_times: tuple
    error: str | None = None


def usable_panels(setup: VideoSetup):
    """The panels that calibrated, in a stable order."""
    return [setup.panels[name] for name, _side in PANEL_SIDES
            if name in setup.panels and setup.panels[name].calib is not None]


def is_usable(setup: VideoSetup) -> bool:
    return bool(usable_panels(setup))


def derive(video_id: str, timed_frames) -> VideoSetup:
    """Find and calibrate both panels from frames spread across a video.

    ``timed_frames`` is ``(absolute_t, bgr)`` pairs. They must be far apart in
    time: panels are found by what does *not* change, so frames a second apart
    would read a settled house as a separator bar.
    """
    timed = list(timed_frames)
    if len(timed) < MIN_LAYOUT_FRAMES:
        raise ValueError(
            f"{video_id}: need at least {MIN_LAYOUT_FRAMES} frames spread across "
            f"the video to tell a bar from still ice, got {len(timed)}")

    times = tuple(round(float(t), 3) for t, _ in timed)
    frames = [f for _t, f in timed]
    height, width = frames[0].shape[:2]

    try:
        panels = layout.detect_panels(frames)
    except layout.LayoutError as exc:
        return VideoSetup(video_id, (width, height), {}, times, f"layout: {exc}")

    rects = {"top": panels.top, "bottom": panels.bottom}
    found = {}
    for name, delivery_side in PANEL_SIDES:
        rect = rects[name]
        x, y, w, h = rect
        seen = {}
        for frame in frames:
            state = lighting.classify(frame[y:y + h, x:x + w]).value
            seen[state] = seen.get(state, 0) + 1
        try:
            cal = calibrate_panel(frames, rect, delivery_side, name).calib
            err = None
        except (calibrate.CalibrationError, Exception) as exc:  # noqa: BLE001
            cal, err = None, str(exc)
        found[name] = PanelInfo(name=name, rect=tuple(rect), calib=cal,
                                lighting=seen, error=err)

    return VideoSetup(video_id, (width, height), found, times, None)


def _calib_to_json(cal):
    if cal is None:
        return None
    return {"center_px": list(cal.center_px), "px_per_m": cal.px_per_m,
            "edge_erosion_px": cal.edge_erosion_px, "residual_m": cal.residual_m,
            "flipped": cal.flipped}


def _calib_from_json(d):
    if d is None:
        return None
    return calibrate.PanelCalib(
        center_px=tuple(d["center_px"]), px_per_m=d["px_per_m"],
        edge_erosion_px=d["edge_erosion_px"], residual_m=d["residual_m"],
        flipped=d["flipped"])


def to_json(setup: VideoSetup) -> dict:
    return {
        "video_id": setup.video_id,
        "frame_size": list(setup.frame_size),
        "frame_times": list(setup.frame_times),
        "error": setup.error,
        "panels": {
            name: {"name": p.name, "rect": list(p.rect),
                   "calib": _calib_to_json(p.calib),
                   "lighting": p.lighting, "error": p.error}
            for name, p in setup.panels.items()
        },
    }


def from_json(d: dict) -> VideoSetup:
    return VideoSetup(
        video_id=d["video_id"],
        frame_size=tuple(d["frame_size"]),
        frame_times=tuple(d["frame_times"]),
        error=d.get("error"),
        panels={name: PanelInfo(name=p["name"], rect=tuple(p["rect"]),
                                calib=_calib_from_json(p["calib"]),
                                lighting=p["lighting"], error=p.get("error"))
                for name, p in d["panels"].items()},
    )
