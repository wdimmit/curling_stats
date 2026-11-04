"""Where the slide was aimed and where the release sent the rock, at the broom.

Two more straight lines through the far camera's track of the rock, beside the
final line ``linetime`` fits just past the hog line:

- the slide line, from halfway between the hack line and the back line to the
  release point: where the slide was aimed;
- the release line, over the first 9 ft past the release point: where the
  hand sent the rock.

Each is extended to the skip's broom (to the destination tee for a rock nobody
held a broom for), and the set -- the release line less the slide line, signed
toward wide -- says how much the release itself moved the line. The user's
definitions, 2026-10-31: docs/superpowers/specs/
2026-10-31-slide-release-final-lines-design.md. This replaces PR #2's
browser-side slide fit (frontend/core/slide.mjs, never merged), so the game
page and the practice card read the same numbers.

The samples are 30 fps, so neighbours' errors are not independent: each line's
error at the broom counts the samples as fewer, by their residuals' lag-1
autocorrelation, before a figure or a set is shown.

Attach-only, like ``releasetime``: it runs after the release is read, adds
``slide``, ``release_line`` and ``set`` to a shot's line, and moves nothing else.
"""
from __future__ import annotations

import dataclasses
import logging
from dataclasses import dataclass

import numpy as np

from curling_score.game import linetime
from curling_score.geometry import constants as C

log = logging.getLogger(__name__)

# The slide starts halfway between the hack line and the back line, behind
# the throwing tee: the stone is moving by then and the push is over.
SLIDE_FROM_M = -(C.TEE_TO_HACKLINE_M + C.TEE_TO_BACKLINE_M) / 2
SLIDE_MIN_N, SLIDE_MIN_SPAN_M = 8, 2.0
RELEASE_MIN_N, RELEASE_MIN_SPAN_M = 15, 2.0
# A line whose standard error at the broom is over this is not shown. Set on
# the A/B of 2026-10-31 (583 rocks, three games on sheets 1-3): the slide's
# error at the broom runs median 2.4 in, p90 5.3, p95 6.6, and the release's
# 1.7 / 3.5 / 4.1, so 6 in hides the worst 6% of slides (18% on 09/23 sheet 3,
# whose hack end reads noisier) and 1% of releases.
SE_MAX_M = 0.15
# The set is shown only when it is this many standard errors of itself.
SET_SIGMAS = 2.0
# However smooth the residuals, a fit stands on at least this many samples.
MIN_N_EFF = 3.0
MAX_RHO = 0.95
SEAM_TOL_M = 1e-6


@dataclass(frozen=True)
class AimLine:
    a: float                      # x = a + b*y, the timeline's frame
    b: float
    n: int
    n_eff: float                  # n counted down for correlated neighbours
    rms: float
    se_m: float                   # standard error at the broom's y (the tee's, broomless)
    at_broom_x: float | None
    miss: float | None            # at_broom_x - broom x, signed right
    side: str | None              # "wide" | "narrow"
    at_tee_x: float | None        # only without a broom
    shown: bool                   # se_m <= SE_MAX_M


@dataclass(frozen=True)
class AimSet:
    m: float                      # release less slide: + wide, or + right without a curl
    se_m: float
    shown: bool


def _past_tee(y: float) -> float:
    return linetime.TEE_Y - y


def slide_points(line) -> list[tuple[float, float]]:
    """``(y, x)`` of the delivery from SLIDE_FROM_M to the release point, in
    travel order; [] without a release point."""
    if line.release is None:
        return []
    d_rel = _past_tee(line.release[1])
    return [(y, x) for _t, y, x in line.delivery if SLIDE_FROM_M <= _past_tee(y) <= d_rel]


def release_points(line) -> list[tuple[float, float]]:
    """``(y, x)`` over the first 9 ft past the release point, in travel order:
    the delivery's samples, then the hog-line track's past where the delivery
    stops (just past hog + 1.5 m), each depth once. [] without a release point."""
    if line.release is None:
        return []
    lo = _past_tee(line.release[1])
    hi = lo + linetime.NINE_FT_M
    pts = [(y, x) for _t, y, x in line.delivery if lo <= _past_tee(y) <= hi]
    last = max((_past_tee(y) for _t, y, _x in line.delivery), default=-np.inf)
    # A track sample at the delivery's last depth (to rounding) is already in.
    pts += [(y, x) for _t, x, y, yp in sorted(line.track)
            if yp > last + SEAM_TOL_M and lo <= yp <= hi]
    return pts


def _n_eff(resid: np.ndarray) -> float:
    n = len(resid)
    if n < 3 or np.std(resid[:-1]) == 0 or np.std(resid[1:]) == 0:
        return float(n)
    rho = float(np.clip(np.corrcoef(resid[:-1], resid[1:])[0, 1], 0.0, MAX_RHO))
    return max(MIN_N_EFF, n * (1 - rho) / (1 + rho))


def fit_aim(points, min_n: int, min_span: float, broom, curl) -> AimLine | None:
    """A straight x(y) through ``points`` (``(y, x)``, travel order), read at
    ``broom`` (``(x, y)``, or None to pin it at the destination tee). None with
    fewer than ``min_n`` points or a span under ``min_span``."""
    if len(points) < min_n:
        return None
    ys = np.array([p[0] for p in points], float)
    xs = np.array([p[1] for p in points], float)
    if np.ptp(ys) < min_span:
        return None
    b, a = np.polyfit(ys, xs, 1)
    resid = xs - (a + b * ys)
    n = len(ys)
    rms = float(np.sqrt(np.sum(resid ** 2) / (n - 2)))
    n_eff = _n_eff(resid)
    y_star = 0.0 if broom is None else float(broom[1])
    sxx = float(np.sum((ys - ys.mean()) ** 2))
    se = rms * float(np.sqrt(1 / n_eff + (y_star - ys.mean()) ** 2 / (sxx * n_eff / n)))
    at_broom_x = miss = side = at_tee_x = None
    if broom is None:
        at_tee_x = float(a)
    else:
        at_broom_x = float(a + b * y_star)
        miss = at_broom_x - float(broom[0])
        side = linetime.side_of(miss, curl)
    return AimLine(a=float(a), b=float(b), n=n, n_eff=float(n_eff), rms=rms, se_m=se,
                   at_broom_x=at_broom_x, miss=miss, side=side, at_tee_x=at_tee_x,
                   shown=se <= SE_MAX_M)


def aim_set(slide: AimLine | None, release: AimLine | None, curl) -> AimSet | None:
    """How far the release moved the line from the slide's at the broom,
    signed toward wide (to the right without a curl). None without both lines
    read at a broom; not shown unless both are and it clears SET_SIGMAS."""
    if slide is None or release is None or slide.miss is None or release.miss is None:
        return None
    toward = 1.0 if curl == "right" else -1.0 if curl == "left" else None
    wide = (lambda v: v) if toward is None else (lambda v: -toward * v)
    m = wide(release.miss) - wide(slide.miss)
    se = float(np.hypot(slide.se_m, release.se_m))
    return AimSet(m=m, se_m=se,
                  shown=slide.shown and release.shown and abs(m) > SET_SIGMAS * se)


def _broom_of(shot):
    b = getattr(shot, "target_broom", None)
    return None if b is None else (float(b.x_m), float(b.y_m))


def attach_aim_lines(shots) -> int:
    """Give each shot's line its ``slide``, ``release_line`` and ``set``, in
    place; return how many got a release line. A rock with no line or no
    release point is passed over; a failure on one rock is logged and leaves
    its line as it was."""
    n = 0
    for shot in shots:
        line = getattr(shot, "line", None)
        if line is None or line.release is None:
            continue
        try:
            broom = _broom_of(shot)
            slide = fit_aim(slide_points(line), SLIDE_MIN_N, SLIDE_MIN_SPAN_M, broom, line.curl)
            release = fit_aim(release_points(line), RELEASE_MIN_N, RELEASE_MIN_SPAN_M, broom,
                              line.curl)
        except Exception:  # noqa: BLE001 - attach-only: the rock keeps its line
            log.exception("aim lines failed on shot %s; its line keeps none",
                          getattr(shot, "number", "?"))
            continue
        shot.line = dataclasses.replace(line, slide=slide, release_line=release,
                                        set=aim_set(slide, release, line.curl))
        n += release is not None
    return n


def _r(v, n=4):
    return None if v is None else round(float(v), n)


def aim_json(a: AimLine | None) -> dict | None:
    """An AimLine as the timeline and the practice record carry it."""
    if a is None:
        return None
    out = {"at_broom": None if a.at_broom_x is None else {"x": _r(a.at_broom_x), "miss_m": _r(a.miss)},
           "side": a.side, "shown": bool(a.shown),
           "fit": {"n": int(a.n), "n_eff": _r(a.n_eff, 1), "rms_m": _r(a.rms), "se_m": _r(a.se_m)}}
    if a.at_tee_x is not None:
        out["at_tee"] = {"x": _r(a.at_tee_x)}
    return out


def set_json(s: AimSet | None) -> dict | None:
    return None if s is None else {"m": _r(s.m), "se_m": _r(s.se_m), "shown": bool(s.shown)}
