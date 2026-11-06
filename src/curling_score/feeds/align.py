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


# --- following the clock through a game -----------------------------------------
#
# A YouTube video can lose picture partway with no gap in its time: on 11/05
# sheet 4 it sat 908.94 s from the cameras until about video t 3000 and 914.72 s
# after. So a camera game's clock is a list of steps, found by checking the
# video against the camera every so often and narrowing down where a change
# happened.

# A step is placed to within this much of the video's time ...
PLACE_S = 10.0
# ... with at most this many probes; then it stays at the middle of what is left.
MAX_PROBES = 8


class ClockTrack:
    """The steps of a camera game's clock, from checks of the video against the
    camera (``add``) and probes between them (``probe``/``add_probe``).

    The first offset is confirmed by two checks in a row that agree. After it,
    a check that disagrees is a candidate, and the next check agreeing with the
    candidate confirms a step between the last check that agreed with the old
    offset and the candidate. Pure: the caller fetches and matches.
    """

    def __init__(self, frames: int = 2):
        self.frames = frames
        self._first: Match | None = None
        self._offset: float | None = None
        self._last_t: float | None = None       # the last check agreeing with _offset
        self._candidate: tuple[float, Match] | None = None
        self._steps: list[list[float]] = []
        self._placing: dict | None = None

    def _agree(self, a: float, b: Match) -> bool:
        return agree(Match(a, 0.0), b, self.frames)

    def add(self, video_t_s: float, m: Match | None) -> None:
        """A check: the match of the video at ``video_t_s`` (None: no match)."""
        if m is None:
            return
        if self._offset is None:
            if self._first is not None and self._agree(self._first.offset_s, m):
                self._offset, self._last_t = self._first.offset_s, video_t_s
                self._steps = [[0.0, self._offset]]
            else:
                self._first = m
            return
        if self._agree(self._offset, m):
            self._last_t, self._candidate = video_t_s, None
            return
        if self._candidate is not None and agree(self._candidate[1], m, self.frames):
            cand_t, cand = self._candidate
            old, new = self._offset, cand.offset_s
            self._placing = {"lo": self._last_t, "hi": cand_t, "old": old, "new": new,
                             "probes": 0}
            self._steps.append([(self._last_t + cand_t) / 2 - new, new])
            self._offset, self._last_t, self._candidate = new, video_t_s, None
            return
        self._candidate = (video_t_s, m)

    def probe(self) -> float | None:
        """A video time to check next, while a step is not yet placed; else None."""
        p = self._placing
        if p is None:
            return None
        if p["hi"] - p["lo"] <= PLACE_S or p["probes"] >= MAX_PROBES:
            self._placing = None
            return None
        return (p["lo"] + p["hi"]) / 2

    def add_probe(self, video_t_s: float, m: Match | None) -> None:
        """What a probe at ``video_t_s`` found."""
        p = self._placing
        if p is None:
            return
        p["probes"] += 1
        if m is not None and self._agree(p["old"], m):
            p["lo"] = video_t_s
        elif m is not None and self._agree(p["new"], m):
            p["hi"] = video_t_s
        self._steps[-1][0] = (p["lo"] + p["hi"]) / 2 - p["new"]

    def steps(self) -> list[list[float]]:
        """``[[camera_from_s, offset_s], ...]``; empty before the first offset."""
        return [list(s) for s in self._steps]

    def clock(self, aligned: str = "segment") -> dict | None:
        """The clock for ``timeline.build_document``; None before the first offset."""
        if not self._steps:
            return None
        return {"kind": "cameras", "aligned": aligned, "offset_s": self._steps[0][1],
                "steps": self.steps()}


# --- a kept recording of the video, all at once ------------------------------------

# How often the video is checked against the camera through a game.
RECHECK_S = 600.0
PIECE_S = 5.0


def cut_piece(video: Path, t: float, dur_s: float = PIECE_S) -> Path:
    """``dur_s`` of ``video`` from ``t``, re-encoded into a file of its own whose
    first frame is ``t`` (an MP4 is cut exactly: it is indexed)."""
    import tempfile

    out = Path(tempfile.mkdtemp(prefix="align-")) / "piece.ts"
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-ss", f"{t:.3f}", "-i", str(video),
                    "-t", f"{dur_s:.3f}", "-an", "-c:v", "libx264", "-preset", "ultrafast",
                    str(out)], check=True)
    return out


def length_s(path: Path) -> float:
    """How long a media file is, by ffprobe."""
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", str(path)], capture_output=True, text=True,
                         check=True).stdout.strip()
    return float(out)


def track_recording(video: Path, camera: Path, sheet: int, guess_s: float, length_s: float, *,
                    every_s: float = RECHECK_S, start_s: float = 300.0, search_s: float = 30.0,
                    cut=cut_piece, match=match) -> ClockTrack:
    """The clock of a camera recording ``length_s`` long against a kept
    recording of the video (``video``), checked every ``every_s`` of the
    camera's time from ``start_s``, with probes wherever a step turns up.
    ``guess_s`` is the offset expected before the first check."""
    track, offset = ClockTrack(), guess_s

    def look(video_t):
        piece = cut(video, video_t)
        try:
            return match(piece, video_t, camera, offset, sheet, search_s=search_s)
        finally:
            Path(piece).unlink(missing_ok=True)

    camera_t = start_s
    while camera_t + PIECE_S <= length_s:
        video_t = camera_t + offset
        if video_t >= 0:
            found = look(video_t)
            track.add(video_t, found)
            if found is not None:
                offset = found.offset_s
            while (t := track.probe()) is not None:
                track.add_probe(t, look(t))
        camera_t += every_s
    return track
