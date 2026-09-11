"""Decide which games go where, once, and write it down.

ds8 could not be rebuilt because the set was a *function* of what segmentation
happened to find that day: ``picked = ends[::len(ends)//4][:4]``. Shift one end
boundary and the whole dataset reshapes. So ds11 begins by writing an explicit
list of games, splits and resolutions to a file that goes into git, and every
later stage reads it rather than deciding anything.
"""

import datetime as _dt

from curling_score.harvest import playlist

MIN_HEIGHT = 1080


def build(entries, formats, n_val: int = 5, playlist_url: str = "",
          min_height: int = MIN_HEIGHT, all_val: bool = False) -> dict:
    """The season plan: every game, its split, and how it will be fetched.

    ``formats`` maps video id to the survey of what YouTube will serve, keyed
    ``chosen_h`` / ``chosen_fps`` / ``chosen_id``.

    Games served below ``min_height`` are dropped, not demoted. Two Tuesdays of
    this season were streamed at 720p, where a stone is about 14 px across
    against 20 px at 1080p -- a different detection problem wearing the same
    clothes. They are recorded in ``dropped`` so the decision stays visible and
    can be revisited with one parameter.
    """
    kept, dropped = [], []
    for e in entries:
        height = int(formats.get(e.video_id, {}).get("chosen_h") or 0)
        (kept if height >= min_height else dropped).append((e, height))
    if not kept:
        raise ValueError(
            f"every one of {len(entries)} videos is below {min_height}p; "
            f"nothing to build a dataset from")

    entries = [e for e, _h in kept]
    if all_val:
        val_dates = sorted({e.date for e in entries})
        splits = {e.video_id: "val" for e in entries}
    else:
        val_dates = playlist.pick_val_dates([e.date for e in entries], n=n_val)
        splits = playlist.assign_splits(entries, val_dates)

    videos = []
    for e in sorted(entries, key=lambda e: (e.date, e.sheet)):
        fmt = formats.get(e.video_id, {})
        videos.append({
            "video_id": e.video_id,
            "title": e.title,
            "date": e.date,
            "sheet": e.sheet,
            "duration_s": e.duration_s,
            "height": int(fmt.get("chosen_h") or 0),
            "fps": float(fmt.get("chosen_fps") or 0.0),
            "format_id": str(fmt.get("chosen_id") or ""),
            "split": splits[e.video_id],
        })

    return {
        "created": _dt.date.today().isoformat(),
        "playlist": playlist_url,
        "n_val_dates": n_val,
        "min_height": min_height,
        "val_dates": val_dates,
        "dropped": [{"video_id": e.video_id, "date": e.date, "sheet": e.sheet,
                     "height": h, "reason": f"below {min_height}p"}
                    for e, h in sorted(dropped, key=lambda t: (t[0].date, t[0].sheet))],
        "summary": {
            "videos": len(videos),
            "dropped_videos": len(dropped),
            "dates": len({v["date"] for v in videos}),
            "train_videos": sum(1 for v in videos if v["split"] == "train"),
            "val_videos": sum(1 for v in videos if v["split"] == "val"),
            "heights": _counts(v["height"] for v in videos),
            "fps": _counts(v["fps"] for v in videos),
        },
        "videos": videos,
    }


def _counts(values) -> dict:
    out: dict = {}
    for v in values:
        out[str(v)] = out.get(str(v), 0) + 1
    return dict(sorted(out.items()))
