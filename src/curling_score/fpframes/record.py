"""Collect the firings of the pipeline's false-positive checks, per end.

Each check in ``fpframes.CHECKS`` calls a hook on the active `Recorder`
where it rejects something. A hook only notes the numbers it was given: no
I/O, no frame reads, and the long-camera ones keep just the best candidate
per half second so a noisy end stays cheap. When the end is built,
``close_end`` stamps game, end and house on what was noted, finds each
overhead sample's box in the panel's own detections, and keeps the result
under ``(game, end)`` so rebuilding an end replaces its firings.

Nothing here may raise into a job; every public method logs and swallows.
"""

import functools
import logging
import math
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from pathlib import Path

from . import WIDTH_FAR

log = logging.getLogger(__name__)

# Overhead tracks can run to hundreds of samples; a handful spread over the
# flight is enough to find a frame where the false positive shows.
MAX_SAMPLES = 5

# A stone is 0.291 m across (the sheet's 29.1 cm), used to size a lone box.
STONE_WIDTH_M = 0.291

_ACTIVE: ContextVar = ContextVar("fpframes_recorder", default=None)


@dataclass
class Firing:
    """One check's rejection, with where in the video to look for it."""

    check: str
    t: float
    camera: str  # "overhead" or "side"
    color: str | None
    conf: float | None
    score: float
    info: dict
    box: tuple | None = None  # whole-frame x0, y0, x1, y1
    top_estimated: bool = False
    samples: tuple = ()  # overhead (t, x, y), until joined to a detection
    rect: tuple | None = None
    where: str | None = None
    house: str | None = None
    game: int | None = None
    end: int | None = None


def width_check(ratio: float) -> str:
    """Which side-width check a long-camera width ratio belongs to."""
    lo, hi = WIDTH_FAR
    return "side.width_far" if ratio <= lo or ratio >= hi else "side.width_off"


def active():
    """The recorder for the job running in this context, or None."""
    return _ACTIVE.get()


@contextmanager
def recording(recorder):
    """Make ``recorder`` the active one; None switches recording off."""
    token = _ACTIVE.set(recorder)
    try:
        yield recorder
    finally:
        _ACTIVE.reset(token)


def _guarded(default=None):
    """Log and swallow whatever a hook raises, returning ``default`` instead."""

    def wrap(fn):
        @functools.wraps(fn)
        def inner(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Exception:
                log.exception("fpframes.%s", fn.__name__)
                return default() if callable(default) else default

        return inner

    return wrap


def _spread(points: list, k: int = MAX_SAMPLES) -> tuple:
    """Up to ``k`` points spread evenly from first to last."""
    if len(points) <= k:
        return tuple(points)
    step = (len(points) - 1) / (k - 1)
    return tuple(points[round(i * step)] for i in range(k))


def _find(by_t: dict, sample, color):
    """The detection a track sample was made from, or None."""
    t, x, y = sample
    for d in by_t.get(round(t, 3), ()):
        if d.color == color and abs(d.x_m - x) < 1e-6 and abs(d.y_m - y) < 1e-6:
            return d
    return None


class Recorder:
    """The firings of one job's checks, kept by end."""

    def __init__(self, video_id: str, *, models: dict, pipeline: str,
                 job: str | None = None, title: str | None = None):
        self.video_id = video_id
        self.models = models
        self.pipeline = pipeline
        self.job = job
        self.title = title
        self.video: Path | None = None
        self._view = None
        self._overhead: list[Firing] = []
        self._side: dict[tuple, Firing] = {}
        self._ends: dict[tuple, list[Firing]] = {}

    # -- hooks, called where a check rejects something ----------------------

    @_guarded()
    def overhead_track(self, check, track, end_i=None, **info) -> None:
        """An overhead track a check rejected, cut at ``end_i``."""
        n = len(track.ts) if end_i is None else end_i + 1
        pts = list(zip(track.ts[:n], track.xs[:n], track.ys[:n]))
        if not pts:
            return
        self._overhead.append(Firing(
            check=check, t=float(pts[0][0]), camera="overhead",
            color=track.color, conf=None, score=float(len(pts)), info=dict(info),
            samples=_spread(pts)))

    @_guarded()
    def side_width(self, view, t, box, conf, ratio, *, color, pass_,
                   top_estimated) -> None:
        """A long-camera box whose width is off for a stone at its row."""
        self._keep_side(Firing(
            check=width_check(ratio), t=float(t), camera="side", color=color,
            conf=float(conf), score=abs(math.log(ratio)),
            info={"ratio": ratio, "pass": pass_}, box=tuple(box),
            top_estimated=top_estimated, rect=tuple(view.rect)))

    @contextmanager
    def side_view(self, view):
        """The view ``side_lone`` reads, for the length of the block."""
        old, self._view = self._view, view
        try:
            yield
        finally:
            self._view = old

    @_guarded()
    def side_lone(self, t, cx, row, conf) -> None:
        """A long-camera box no overhead track accounts for.

        Only its centre and foot row are known, so the box is a stone's
        width at that row, standing on it; the top is a guess.
        """
        view = self._view
        if view is None:
            return
        w = STONE_WIDTH_M * view.lateral_px_per_m(row)
        self._keep_side(Firing(
            check="side.lone", t=float(t), camera="side", color=None,
            conf=float(conf), score=float(conf), info={},
            box=(cx - w / 2, row - w / 2, cx + w / 2, row), top_estimated=True,
            rect=tuple(view.rect)))

    def _keep_side(self, firing: Firing) -> None:
        """Keep the best firing per check, view and half second."""
        key = (firing.check, firing.rect, round(firing.t * 2))
        best = self._side.get(key)
        if best is None or firing.score > best.score:
            self._side[key] = firing

    # -- end bookkeeping ----------------------------------------------------

    @_guarded()
    def begin_end(self) -> None:
        """Drop what the last end left pending."""
        self._overhead = []
        self._side = {}

    @_guarded()
    def close_end(self, *, game, end, house, video, near_rect, near_seq,
                  cameras) -> None:
        """Stamp and store the pending firings under ``(game, end)``.

        Overhead firings gain a box from the detection their middle sample
        came from and are dropped when none of their samples can be found.
        Side boxes move from view pixels to whole-frame ones.
        """
        try:
            self.video = video
            by_t = {round(t, 3): list(dets) for t, dets in near_seq}
            out = []
            for f in self._overhead:
                joined = self._join(f, by_t, near_rect, house)
                if joined is not None:
                    out.append(joined)
            for f in self._side.values():
                x0, y0, x1, y1 = f.box
                dx, dy = f.rect[0], f.rect[1]
                out.append(replace(
                    f, box=(x0 + dx, y0 + dy, x1 + dx, y1 + dy),
                    where=cameras.get(f.rect, "side")))
            self._ends[(game, end)] = [
                replace(f, house=house, game=game, end=end) for f in out]
        finally:
            self.begin_end()

    @staticmethod
    def _join(f: Firing, by_t: dict, near_rect, house):
        matched = []
        for s in f.samples:
            d = _find(by_t, s, f.color)
            if d is not None:
                matched.append((s, d))
        if not matched:
            return None
        (t, _x, _y), d = matched[len(matched) // 2]
        cx, cy = d.x_px + near_rect[0], d.y_px + near_rect[1]
        half = math.sqrt(d.area_px) / 2
        return replace(
            f, t=float(t), conf=float(d.confidence), rect=tuple(near_rect),
            box=(cx - half, cy - half, cx + half, cy + half), where=house)

    @_guarded(list)
    def pending(self) -> list[Firing]:
        """What the current end has collected, joined or not."""
        return self._overhead + list(self._side.values())

    @_guarded(list)
    def firings(self) -> list[Firing]:
        """Every closed end's firings."""
        return [f for fs in self._ends.values() for f in fs]
