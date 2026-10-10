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

import math
from dataclasses import dataclass

from curling_score.game.hogtime import CAMERA_FOR
from curling_score.geometry import constants as G
from curling_score.practice.enrich import OTHER_HOUSE

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
