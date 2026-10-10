"""A practice throw's clip: the camera facing the thrower, then the one behind.

The camera at the destination end looks back up the sheet at the thrower, so
it shows the push, the slide and the release, until the rock comes too close
and runs out of the bottom of its frame. From the far hog line on, the camera
at the throwing end, behind the thrower, follows the rock to rest.

A clip is planned as pieces of named camera views, on the stream's clock like
every other practice time. Only `cut` knows that today both views are crops of
one composite stream; with the club's own cameras each view will be a file of
its own, and nothing else changes.
"""

import logging
import math
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from curling_score.game.hogtime import CAMERA_FOR
from curling_score.geometry import constants as G
from curling_score.ingest import frames
from curling_score.practice.enrich import OTHER_HOUSE

log = logging.getLogger(__name__)

# Before the release, to take in the push from the hack and the slide.
PUSH_LEAD_S = 5.0
# A throw nothing saw arrive, or with no hog line times: this long after release.
FRONT_MAX_S = 10.0
# After the rock stops, so the clip does not end on the stop itself.
REST_TAIL_S = 1.5

# The two hog lines, as metres from the throwing tee.
NEAR_HOG_M = G.TEE_TO_HOGLINE_M
FAR_HOG_M = G.TEE_TO_TEE_M - G.TEE_TO_HOGLINE_M


@dataclass(frozen=True)
class Piece:
    view: str          # "left" or "right"
    t0: float
    t1: float


@dataclass(frozen=True)
class ClipPlan:
    pieces: tuple

    @property
    def end(self) -> float:
        return self.pieces[-1].t1

    def until(self, t: float):
        """This plan, stopping at ``t``: where a recording ended early. None
        when nothing of it is left."""
        kept = tuple(Piece(p.view, p.t0, min(p.t1, t)) for p in self.pieces if p.t0 < t)
        return ClipPlan(kept) if kept else None


def exit_time(throw: dict, view) -> float | None:
    """When the rock runs out of the bottom of ``view``, the camera facing the
    thrower, or None if that cannot be told.

    The bottom row's distance from the throwing tee comes from the view's paint
    calibration (extrapolated past the hog row it was fitted at). The rock is
    taken to slow evenly between the hog lines: it leaves the near one at its
    measured speed and reaches the far one when the panel saw it. Without a
    speed, it goes at the average.
    """
    t_hog, t_far, v = throw.get("t_hog_s"), throw.get("t_far_s"), throw.get("v_hog_m_s")
    if t_hog is None or t_far is None or t_far <= t_hog:
        return None
    try:
        at = view.metres_at(view.rect[3] - 1)
    except (ZeroDivisionError, ValueError):
        return None
    if not math.isfinite(at) or at <= NEAR_HOG_M:
        return None
    if at >= FAR_HOG_M:
        return t_far
    span, gone, dist = t_far - t_hog, at - NEAR_HOG_M, FAR_HOG_M - NEAR_HOG_M
    if v is None or v <= dist / span:
        return t_hog + span * gone / dist
    slow = 2.0 * (v * span - dist) / span ** 2          # deceleration, m/s^2
    disc = v * v - 2.0 * slow * gone
    if disc < 0:
        return t_far
    return t_hog + (v - math.sqrt(disc)) / slow


def plan(throw: dict, sideviews) -> ClipPlan | None:
    """The clip for ``throw``, or None: a stone nobody released has none, and
    neither does a sheet whose long cameras were not found."""
    rel = throw.get("t_release_s")
    if rel is None or not sideviews:
        return None
    house = throw["house"]
    facing, behind = CAMERA_FOR[OTHER_HOUSE[house]], CAMERA_FOR[house]
    if facing not in sideviews and behind not in sideviews:
        return None
    start = rel - PUSH_LEAD_S
    rest = throw.get("t_rest_s")
    last = rest + REST_TAIL_S if throw.get("arrived") and rest is not None else None
    if facing not in sideviews or behind not in sideviews or last is None:
        one = facing if facing in sideviews else behind
        if last is None:
            out = exit_time(throw, sideviews[facing]) if one == facing else None
            end = min(out, rel + FRONT_MAX_S) if out is not None else rel + FRONT_MAX_S
        else:
            end = last
        return ClipPlan((Piece(one, start, end),))
    t_far = throw.get("t_far_s")
    leaves = exit_time(throw, sideviews[facing])
    if leaves is None:
        leaves = t_far if t_far is not None else rel + FRONT_MAX_S
    picks_up = t_far if t_far is not None else leaves
    pieces = [Piece(facing, start, leaves)]
    if last > picks_up:
        pieces.append(Piece(behind, picks_up, last))
    return ClipPlan(tuple(pieces))


# Portrait, for a phone: a long camera's panel is about 800x1080 of the stream.
CLIP_SIZE = (600, 810)
CLIP_FPS = 30
CLIP_MAX_BYTES = 20_000_000
CUT_TIMEOUT_S = 120.0


def cut(plan: ClipPlan, views: dict, out_path, *, t0_s: float = 0.0, size=CLIP_SIZE,
        fps: int = CLIP_FPS, timeout_s: float = CUT_TIMEOUT_S, run=subprocess.run) -> None:
    """Cut ``plan`` into ``out_path`` with one ffmpeg run.

    ``views`` maps a view to ``(source, crop)``: the file it is in and the
    rectangle of it, or None for a whole frame. Times are on the stream's
    clock; ``t0_s`` is where the sources begin on it.

    Timestamps are kept (``-copyts``) and each piece is trimmed by the source's
    own timestamps, the first frame's start added, which is the frame reader's
    convention (`frames.window`): a release read at t is at t here. An MPEG-TS
    recording has no index and a seek lands on the keyframe after its target,
    so each input is entered `frames.seek_lead` early. Bounded by `-fs` and a
    timeout, as `harvest/clips.py` learned to be.
    """
    sources = list(dict.fromkeys(views[p.view][0] for p in plan.pieces))
    width, height = size
    args = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-copyts"]
    offsets = {}
    for src in sources:
        try:
            offsets[src] = frames.stream_start_s(src)
        except Exception as exc:  # noqa: BLE001 - PyAV raises its own kinds
            raise OSError(f"cannot read {src}: {exc}") from exc
        mine = [p for p in plan.pieces if views[p.view][0] == src]
        lo = min(p.t0 for p in mine) - t0_s
        hi = max(p.t1 for p in mine) - t0_s
        args += ["-ss", f"{max(0.0, lo - frames.seek_lead(src)):.3f}",
                 "-to", f"{hi + 1.0:.3f}", "-i", str(src)]
    graph, branches = [], {}
    for i, src in enumerate(sources):
        n = sum(1 for p in plan.pieces if views[p.view][0] == src)
        labels = [f"s{i}_{j}" for j in range(n)]
        branches[src] = list(labels)
        graph.append(f"[{i}:v]split={n}" + "".join(f"[{x}]" for x in labels)
                     if n > 1 else f"[{i}:v]null[{labels[0]}]")
    for k, p in enumerate(plan.pieces):
        src, crop = views[p.view]
        a = p.t0 - t0_s + offsets[src]
        b = p.t1 - t0_s + offsets[src]
        box = f"crop={crop[2]}:{crop[3]}:{crop[0]}:{crop[1]}," if crop else ""
        graph.append(f"[{branches[src].pop(0)}]trim=start={a:.3f}:end={b:.3f},"
                     f"setpts=PTS-STARTPTS,{box}scale={width}:{height},setsar=1,"
                     f"fps={fps}[p{k}]")
    graph.append("".join(f"[p{k}]" for k in range(len(plan.pieces)))
                 + f"concat=n={len(plan.pieces)}:v=1:a=0[v]")
    args += ["-filter_complex", ";".join(graph), "-map", "[v]", "-an",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "27", "-pix_fmt", "yuv420p",
             "-movflags", "+faststart", "-fs", str(CLIP_MAX_BYTES), str(out_path)]
    run(args, check=True, capture_output=True, timeout=timeout_s)
    if not Path(out_path).is_file() or Path(out_path).stat().st_size == 0:
        raise OSError(f"ffmpeg wrote no clip at {out_path}")


class Clipper:
    """Cuts each throw's clip from the recording a watch is reading, and hands
    the file to ``deliver(throw_id, path) -> bool``: an upload on a worker, a
    directory on a replay. The file is removed afterwards unless ``keep``."""

    def __init__(self, recording, deliver, workdir, *, keep: bool = False, cut=cut,
                 progress=log.info):
        self.recording, self.deliver = recording, deliver
        self.workdir, self.keep = Path(workdir), keep
        self.cut, self.progress = cut, progress

    def plan(self, throw, cal):
        return plan(throw, getattr(cal, "sideviews", None))

    def make(self, throw, plan_, cal, *, t0_s: float = 0.0) -> bool:
        """Cut and deliver one clip; False if either did not happen."""
        views = {name: (self.recording.path, sv.rect) for name, sv in cal.sideviews.items()}
        self.workdir.mkdir(parents=True, exist_ok=True)
        out = self.workdir / f"{throw['id']}.mp4"
        began = time.monotonic()
        try:
            self.cut(plan_, views, out, t0_s=t0_s)
        except (subprocess.SubprocessError, OSError) as exc:
            log.warning("clip %s was not cut: %s", throw["id"], exc)
            out.unlink(missing_ok=True)
            return False
        took, size = time.monotonic() - began, out.stat().st_size
        try:
            sent = bool(self.deliver(throw["id"], out))
        finally:
            if not self.keep:
                out.unlink(missing_ok=True)
        self.progress(f"clip {throw['id']}: {plan_.end - plan_.pieces[0].t0:.1f} s of "
                      f"video, cut in {took:.1f} s, {size / 1e6:.2f} MB"
                      + ("" if sent else ", not delivered"))
        return sent
