"""Save the chosen frames, and the facts about them, under ``harvest/fp``.

Layout: ``<root>/harvest/fp/<video_id>/<stem>.jpg`` for each frame and one
``frames.jsonl`` per video with a row for each. Frames are whole and
untouched, since the labelling round draws its own boxes. Files and
directories are made readable by everyone so the person labelling can read
what a worker running as another user wrote.

Nothing here may raise into a job.
"""

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from .choose import choose

log = logging.getLogger(__name__)

JPEG_QUALITY = 92
_CAP_LOGGED = False


def video_dir(root: Path, video_id: str) -> Path:
    return Path(root) / "harvest" / "fp" / video_id


def read_rows(root, video_id: str) -> list[dict]:
    """The rows saved so far for a video; a torn or unreadable line is skipped."""
    try:
        text = (video_dir(root, video_id) / "frames.jsonl").read_text()
    except OSError:
        return []
    rows = []
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def has_models(rows: list[dict], models: dict) -> bool:
    """Whether a row was saved by these detectors, so a rerun adds nothing."""
    return any(r.get("models") == models for r in rows)


def size_gb(root) -> float:
    """What the harvest takes on disk, in GB."""
    total = 0
    for dirpath, _dirs, files in os.walk(Path(root) / "harvest" / "fp"):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(dirpath, name))
            except OSError:
                pass
    return total / 1e9


def stem_for(video_id: str, firing) -> str:
    """The file name, without extension, a firing's frame is saved under."""
    t = firing.t
    stem = f"{video_id}_{firing.where}_{t:09.2f}".replace(".", "_")
    return stem + "_" + firing.check.split(".", 1)[1]


def read_frame(video, t: float):
    """The frame at ``t`` seconds, or None when the video has none there."""
    from ..ingest import frames

    for _t, img in frames.window(video, max(0.0, t - 1 / 60), t + 0.25, 60.0):
        return img
    return None


def _mkdir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o755)


def _row(recorder, firing, stem: str, saved_at: str) -> dict:
    return {
        "stem": stem,
        "check": firing.check,
        "camera": firing.camera,
        "where": firing.where,
        "t": round(firing.t, 3),
        "box": [round(float(v), 1) for v in firing.box],
        "top_estimated": firing.top_estimated,
        "color": firing.color,
        "conf": firing.conf,
        "info": firing.info,
        "rect": list(firing.rect) if firing.rect is not None else None,
        "game": firing.game,
        "end": firing.end,
        "house": firing.house,
        "video_id": recorder.video_id,
        "title": recorder.title,
        "pipeline": recorder.pipeline,
        "models": recorder.models,
        "job": recorder.job,
        "saved_at": saved_at,
    }


def _append(path: Path, row: dict) -> None:
    line = json.dumps(row, default=str) + "\n"
    # A line torn by a crash has no newline; start a fresh one so ours parses.
    torn = path.exists() and path.stat().st_size > 0
    if torn:
        with open(path, "rb") as fh:
            fh.seek(-1, os.SEEK_END)
            torn = fh.read(1) != b"\n"
    with open(path, "a") as fh:
        fh.write(("\n" if torn else "") + line)
    os.chmod(path, 0o644)


def save_frames(recorder, *, root, video=None, cap_gb: float = 20.0,
                reader=read_frame, now=time.time) -> int:
    """Save the frames chosen from the recorder's firings; how many were saved.

    Saves nothing when the disk cap is reached, or when this video already
    has frames from the same detectors (a rerun would only repeat them). A
    frame that cannot be read is skipped and the rest are still saved.
    """
    global _CAP_LOGGED
    try:
        video = video if video is not None else recorder.video
        if video is None:
            return 0
        if size_gb(root) >= cap_gb:
            if not _CAP_LOGGED:
                _CAP_LOGGED = True
                log.warning("fpframes: %s is at the %.3g GB cap; not saving",
                            Path(root) / "harvest" / "fp", cap_gb)
            return 0
        if has_models(read_rows(root, recorder.video_id), recorder.models):
            return 0
        picks = choose(recorder.firings())
        if not picks:
            return 0

        import cv2

        d = video_dir(root, recorder.video_id)
        _mkdir(d.parent.parent)
        _mkdir(d.parent)
        _mkdir(d)
        saved_at = datetime.fromtimestamp(now(), timezone.utc).isoformat()
        saved = 0
        for f in picks:
            try:
                img = reader(video, f.t)
                if not isinstance(img, np.ndarray):
                    continue
                stem = stem_for(recorder.video_id, f)
                path = d / f"{stem}.jpg"
                if not cv2.imwrite(str(path),
                                   img, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY]):
                    continue
                os.chmod(path, 0o644)
                _append(d / "frames.jsonl", _row(recorder, f, stem, saved_at))
                saved += 1
            except Exception:
                log.exception("fpframes: frame at %.2f s of %s", f.t, video)
        return saved
    except Exception:
        log.exception("fpframes.save_frames")
        return 0
