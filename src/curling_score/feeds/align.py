"""Where a camera recording sits on the YouTube video's clock.

A camera game is read on its recording's clock and published on the video's
(``timeline.build_document``'s ``clock``): ``published_t = camera_t +
offset_s``. The offset is found by matching a piece of the video -- a live
stream's HLS segment, or a cut of a kept recording -- against the ``long_a``
camera, which the club's composite shows as its left view at 1:1 (the sheet
layout's ``long_a`` placement: ``crop`` in the camera, ``out`` in the
composite).

Both sides go through the same small picture: the view, scaled to 80x108,
grey, 60 fps, each frame normalised to zero mean and unit spread so the
video's exposure and re-encode do not count. The piece is slid along the
camera's frames and the mean absolute difference taken at every frame offset;
a parabola through the least and its neighbours gives the fraction of a frame.
Phase 0 (scratch/camera-direct/align.py) held to a frame over two hours this
way. The margin -- the second least, away from the best, over the best -- says
how sure the match is: an empty sheet or a still picture matches everywhere
about equally, and is no match at all.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from curling_score.cameras import layout
from curling_score.ingest import frames as F

W, H, FPS = 80, 108, 60
# A best match must beat the next best (away from it) by this much.
MIN_MARGIN = 1.15
# How far from the best the next best is looked for, in frames. The least is
# broad: the picture moves little from one frame to the next, so the errors a
# few frames either side are nearly as low (11/05 sheet 3: 0.047 at the best,
# 0.052 four frames off, a median of 0.21). A rival that matters -- a swing
# of the broom repeated -- lies seconds away.
NEAR_FRAMES = 30


@dataclass(frozen=True)
class Match:
    offset_s: float   # published_t = camera_t + offset_s
    margin: float     # second-best error / best error


def _frames(path: Path, crop, start_s: float | None = None,
            dur_s: float | None = None) -> np.ndarray:
    x, y, w, h = crop
    cmd = ["ffmpeg", "-nostdin", "-loglevel", "error"]
    trim = None
    if start_s is not None:
        # An MPEG-TS file is entered a lead early and cut exactly on decoded
        # frames: a seek straight to the target can land past it, and near
        # the file's start loses frames (``frames.seek_lead``).
        enter = max(0.0, start_s - F.seek_lead(path))
        if enter > 0:
            cmd += ["-ss", f"{enter:.3f}"]
        trim = start_s - enter
    cmd += ["-i", str(path)]
    if trim:
        cmd += ["-ss", f"{trim:.3f}"]
    if dur_s is not None:
        cmd += ["-t", f"{dur_s:.3f}"]
    cmd += ["-vf", f"crop={w}:{h}:{x}:{y},scale={W}:{H}:flags=area,format=gray,fps={FPS}",
            "-f", "rawvideo", "-"]
    out = subprocess.run(cmd, check=True, capture_output=True).stdout
    frames = np.frombuffer(out, np.uint8).reshape(-1, H, W).astype(np.float32)
    frames -= frames.mean(axis=(1, 2), keepdims=True)
    return frames / (frames.std(axis=(1, 2), keepdims=True) + 1e-6)


def match(piece: Path, piece_t_s: float, camera: Path, guess_s: float, sheet: int, *,
          search_s: float = 30.0) -> Match | None:
    """The offset of ``camera`` (a ``long_a.ts``) against the video, from
    ``piece`` (composite video whose first frame is at ``piece_t_s`` on the
    video's clock), looked for within ``search_s`` of ``guess_s``. None when
    the camera's file does not cover the search, when the best match is at the
    search's edge (the true one may lie beyond), or when it is not clearly the
    best (``MIN_MARGIN``)."""
    placement = next(p for p in layout.load(sheet).placements if p.role == "long_a")
    seg = _frames(Path(piece), placement.out)
    n = len(seg)
    if n < FPS // 2:
        return None
    start = piece_t_s - guess_s - search_s
    if start < 0:
        return None
    ref = _frames(Path(camera), placement.crop, start, n / FPS + 2 * search_s)
    wanted = int(round(2 * search_s * FPS)) + 1
    if len(ref) - n + 1 < wanted:
        return None
    errs = np.array([np.abs(ref[o:o + n] - seg).mean() for o in range(len(ref) - n + 1)])
    best = int(errs.argmin())
    if best == 0 or best == len(errs) - 1:
        return None
    away = np.concatenate([errs[:max(0, best - NEAR_FRAMES)], errs[best + NEAR_FRAMES + 1:]])
    if not len(away):
        return None
    margin = float(away.min() / (errs[best] + 1e-6))
    if margin < MIN_MARGIN:
        return None
    a, b, c = errs[best - 1], errs[best], errs[best + 1]
    denom = a - 2 * b + c
    frac = float(0.5 * (a - c) / denom) if denom > 0 else 0.0
    camera_t = start + (best + frac) / FPS
    return Match(offset_s=round(piece_t_s - camera_t, 4), margin=round(margin, 3))


def agree(a: Match, b: Match, frames: int = 2) -> bool:
    """Two matches that put the camera at the same place, within ``frames``."""
    return abs(a.offset_s - b.offset_s) <= frames / FPS + 1e-9
