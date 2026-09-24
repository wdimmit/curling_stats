"""Where each video's two side views are, and where the far end's paint sits.

``harvest/setups.py`` does this for the overhead panels; this is the same job
for the wide cameras at the ends of the sheet, and it makes the same promise:
120 unseen VODs will not all work, so a video that cannot be read is recorded
with the reason rather than raising and stopping the harvest.

The reason to record rather than drop is that this is the first real test of
constants measured on ten views from five sheets -- ``PLAUSIBLE_ROWS``,
``_GREEN_THRESHOLD``, ``_HOUSE_SEARCH``, ``_HOG_SEARCH_PX`` in
``geometry/sideview.py``. How many of 240 views calibrate is the finding.
"""

from dataclasses import dataclass

import numpy as np

from curling_score.geometry import layout, sideview

MIN_PLATE_FRAMES = 2


@dataclass(frozen=True)
class ViewInfo:
    """One wide camera: where it is, where the paint is, and why not."""

    name: str
    rect: tuple
    view: sideview.SideView | None
    error: str | None = None


@dataclass(frozen=True)
class VideoViews:
    video_id: str
    frame_size: tuple
    views: dict
    error: str | None = None


def plate(frames):
    """A clean plate: the median over frames far apart in time.

    Players and stones move and the paint does not, so the median is the paint.
    Frames close together would leave a settled house in the plate and the
    house's own rings are what the tee fit looks for.
    """
    stack = np.stack([np.asarray(f, dtype=np.float32) for f in frames])
    return np.median(stack, axis=0)


def derive(video_id: str, frames) -> VideoViews:
    """Locate and solve both side views of one video."""
    frames = list(frames)
    if len(frames) < MIN_PLATE_FRAMES:
        raise ValueError(
            f"{video_id}: need at least {MIN_PLATE_FRAMES} frames spread across "
            f"the video to build a clean plate, got {len(frames)}")
    height, width = frames[0].shape[:2]
    try:
        panels = layout.detect_panels(frames)
    except layout.LayoutError as exc:
        return VideoViews(video_id, (width, height), {}, f"layout: {exc}")
    try:
        rects = sideview.locate(panels, width, height)
    except sideview.SideViewError as exc:
        return VideoViews(video_id, (width, height), {}, f"locate: {exc}")

    p = plate(frames)
    found = {}
    for name, rect in rects.items():
        try:
            v, err = sideview.solve(p, rect, name=f"{video_id}-{name}"), None
        except Exception as exc:  # noqa: BLE001 -- a bad view must not stop 119 others
            v, err = None, f"{type(exc).__name__}: {exc}"
        found[name] = ViewInfo(name=name, rect=tuple(rect), view=v, error=err)
    return VideoViews(video_id, (width, height), found, None)


def usable_views(v: VideoViews):
    return [(name, v.views[name].view) for name in ("left", "right")
            if name in v.views and v.views[name].view is not None]


def is_usable(v: VideoViews) -> bool:
    return bool(usable_views(v))


def _view_to_json(v: sideview.SideView | None):
    if v is None:
        return None
    return {"tee_row": v.tee_row, "hog_row": v.hog_row, "d_m": v.d_m,
            "centre_col": v.centre_col,
            "lat_px_per_m_at_tee": v.lat_px_per_m_at_tee,
            "centre_line": list(v.centre_line) if v.centre_line is not None else None}


def _view_from_json(d, rect: tuple):
    if d is None:
        return None
    return sideview.SideView(
        rect=rect, tee_row=d["tee_row"], hog_row=d["hog_row"], d_m=d["d_m"],
        centre_col=d.get("centre_col"),
        lat_px_per_m_at_tee=d.get("lat_px_per_m_at_tee"),
        centre_line=tuple(d["centre_line"]) if d.get("centre_line") else None)


def to_json(v: VideoViews) -> dict:
    return {
        "video_id": v.video_id,
        "frame_size": list(v.frame_size),
        "error": v.error,
        "views": {
            name: {"name": info.name, "rect": list(info.rect),
                   "view": _view_to_json(info.view), "error": info.error}
            for name, info in v.views.items()
        },
    }


def from_json(d: dict) -> VideoViews:
    views = {}
    for name, info in d["views"].items():
        rect = tuple(info["rect"])
        views[name] = ViewInfo(
            name=info["name"], rect=rect,
            view=_view_from_json(info["view"], rect),
            error=info.get("error"))
    return VideoViews(
        video_id=d["video_id"],
        frame_size=tuple(d["frame_size"]),
        error=d.get("error"),
        views=views,
    )
