"""Where a throw went that the far house's overhead could not place, from the
long camera facing that house.

A release paired with no arrival is settled from the overhead's before/after
read of the far house (`detect.release.settle`), and "nothing changed" made it
hogged. The user reviewed the 39 such rocks on cached video (2026-10-04,
~/curling-work/unmatched): 15 were hogs, 12 crossed the hog line and left
through the house or out the side, and 8 stopped in play as guards.

* A guard in the overhead's last strip, just inside the hog line, is often not
  a stone of its own there -- frozen to another, it merges with it -- so the
  house never changed.
* A hogged stone is pushed down the sheet by a player afterwards, and the
  overhead sees that push enter the house and run through it exactly as a
  through-shot does.

The long camera facing the destination sees all of it. It follows the
delivered stone from ~15 m out to where it stops, at a precision across the
sheet of about a centimetre and along it of a few tenths of a metre: the six
guards stopped 3.3-5.6 m from the tee and stayed, the hogs stopped on the
line (6.35-6.45 m) and were moved within seconds, and a stone that ran through
never stopped at all. So `follow` reads one of REST, HOG or THROUGH, or None
when the camera could not say.

Where the camera cannot say, the overhead's timing is the fallback
(`entry_track`): a stone with the pace to run through the house reaches the
overhead's far edge within ~19 s of its release (reviewed throughs 13.7-18.2 s),
and a pushed hog later (19.9-27.4 s).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from curling_score.geometry import constants as C

REST = "rest"
HOG = "hog"
THROUGH = "through"

# The decode: from before the stone reaches the far third of the sheet (seen
# from ~15 m out at release + 12 s) to well past when a hog is moved.
WINDOW_S = (6.0, 45.0)
FPS = 4.0
# Rows of the view the detector reads: from above the tee (the house) to well
# below the hog line (the stone on its way).
ROWS_ABOVE_TEE = 80
ROWS_BELOW_HOG = 260
CONF = 0.3
# A stone crossing the band's near edge is cut off there, its foot pinned to
# the edge row while it moves -- which reads as a stone standing still. Boxes
# whose foot is this close to that edge are not where the stone is.
EDGE_ROWS = 6

# The stone on its way: first seen this far up-sheet of the tee, on the sheet.
SEED_MIN_Y_M = 7.5
SEED_BY_S = 26.0                # after the release
ON_SHEET_X_M = 2.2
# Its pace there, to aim the first gate: ~1 m/s at 10-15 m from the tee in the
# reviewed rocks, faster for a takeout.
SEED_SPEED_M_S = 1.2
# Following it from one sample to the next.
# Across the sheet the stone barely moves -- even curling, under 0.15 m/s --
# and the camera reads it to about a centimetre, so the gate there grows with
# the stone's own sideways speed and the time since it was last seen, from a
# tenth of a metre. A guard frozen to another sits 0.29 m from it, centre to
# centre, and must not hand the follower over to its neighbour (Wednesday
# Womens 09/30 S1 end 3 rock 11 did, at a flat 0.45 m).
GATE_X_MIN_M = 0.08
GATE_X_PER_S = 0.12
GATE_X_MAX_M = 0.45
GATE_Y_MIN_M = 0.6              # along the sheet the camera reads depth coarsely
LOST_AFTER_S = 2.5
# Stopped: still to within these over this long.
STILL_X_M = 0.15
STILL_Y_M = 0.5
STILL_S = 1.5
STOPPED_Y_M = 0.25
MOVING_M_S = 0.2
# What happens next: a guard stays until a later shot (the next is 30 s and
# more away); a hog is moved by a player within seconds of stopping.
STAY_FROM_S = 3.0
STAY_TO_S = 12.0
STAY_X_M = 0.2
STAY_Y_M = 0.6
STAY_SHARE = 0.5
# Depth error near the hog line is a few tenths of a metre, so the line itself
# decides nothing alone: a stone moved promptly after stopping this far out is
# a hog, and one that stays was ruled in play.
HOG_NEAR_Y_M = 5.5
IN_PLAY_MAX_Y_M = C.TEE_TO_HOGLINE_M + 0.3
# Running through: seen in the house still moving, having crossed the hog line
# in time to have the pace for it (`THROUGH_MAX_LAG_S`), and never having
# slowed nearly to a stop and then sped up again -- a sliding stone only slows,
# so that is a hog stopped and pushed on by a player.
THROUGH_MAX_Y_M = 1.0
SPEED_SPAN_S = 1.0
NEAR_STOP_M_S = 0.15
RESTART_M_S = 0.35
RESTART_AWAY_M = 0.6

# The overhead fallback (`entry_track`).
THROUGH_MAX_LAG_S = 19.0
ENTRY_MIN_Y_M = 3.5
ENTRY_MIN_TRAVEL_M = 3.0


@dataclass(frozen=True)
class Outcome:
    kind: str                   # REST, HOG or THROUGH
    t: float                    # when it stopped, or was last seen running
    x_m: float
    y_m: float
    samples: int                # how many sightings it was followed through


def _still_from(path, i):
    """Index past the end of a still run starting at ``i``, or None."""
    t0, x0, y0 = path[i]
    j = i
    while j + 1 < len(path) and abs(path[j + 1][1] - x0) <= STILL_X_M \
            and abs(path[j + 1][2] - y0) <= STILL_Y_M:
        j += 1
    return j + 1 if path[j][0] - t0 >= STILL_S else None


def _speeds(path):
    """``(t, y, speed)`` along the path, each speed over the SPEED_SPAN_S before it."""
    out = []
    j = 0
    for i, (t, x, y) in enumerate(path):
        while path[j][0] < t - SPEED_SPAN_S and j < i:
            j += 1
        if j < i:
            t0, x0, y0 = path[j]
            out.append((t, y, math.hypot(x - x0, y - y0) / (t - t0)))
    return out


def _restarted(path):
    """The first near-stop the stone was moved on from, as an index into
    ``path``, or None. Moved on means sped up again AND went somewhere: the
    camera's depth jitter alone can make a parked stone read 0.2-0.3 m/s."""
    sp = _speeds(path)
    offset = len(path) - len(sp)
    for i, (t, y, v) in enumerate(sp):
        if v > NEAR_STOP_M_S:
            continue
        k = i + offset
        _t, x0, y0 = path[k]
        rest = sp[i + 1:]
        if any(v2 >= RESTART_M_S for _t2, _y2, v2 in rest) and any(
                math.hypot(px - x0, py - y0) >= RESTART_AWAY_M for _pt, px, py in path[k + 1:]):
            return k
    return None


def _follow(samples, t_release):
    """The delivered stone's sightings, as (t, x, y), from first to last."""
    seed = None
    for t, dets in samples:
        if t - t_release > SEED_BY_S:
            break
        up = [d for d in dets if d[1] >= SEED_MIN_Y_M and abs(d[0]) <= ON_SHEET_X_M]
        if up:
            x, y = max(up, key=lambda d: d[1])[:2]
            seed = (t, x, y)
            break
    if seed is None:
        return []
    path = [seed]
    vx, vy = 0.0, -SEED_SPEED_M_S
    for t, dets in samples:
        if t <= path[-1][0]:
            continue
        lt, lx, ly = path[-1]
        if t - lt > LOST_AFTER_S:
            break
        dt = t - lt
        px, py = lx + vx * dt, ly + vy * dt
        gate_x = min(GATE_X_MAX_M, GATE_X_MIN_M + GATE_X_PER_S * dt + abs(vx) * dt * 0.6)
        gate_y = max(GATE_Y_MIN_M, abs(vy) * dt * 0.6 + 0.4)
        near = [d for d in dets if abs(d[0] - px) <= gate_x and abs(d[1] - py) <= gate_y]
        if not near:
            continue
        x, y = min(near, key=lambda d: math.hypot(d[0] - px, d[1] - py))[:2]
        vx = 0.5 * vx + 0.5 * (x - lx) / dt
        vy = 0.5 * vy + 0.5 * (y - ly) / dt
        path.append((t, x, y))
    return path


def _in_time(path, t_release) -> bool:
    """Whether the stone crossed the far hog line soon enough after its release
    to have had the pace to run through the house."""
    crossed = next((pt for pt, _px, py in path if py <= C.TEE_TO_HOGLINE_M), None)
    return crossed is not None and crossed - t_release <= THROUGH_MAX_LAG_S


def read_outcome(samples, t_release) -> Outcome | None:
    """What became of the throw, from the rock colour's sightings per frame.

    ``samples`` is ``[(t, [(x_m, y_m, conf), ...]), ...]`` in time order, in
    house metres from the far tee (+y toward the thrower). None when the
    stone was never followed, or its ending is ambiguous.
    """
    path = _follow(samples, t_release)
    if len(path) < 3:
        return None
    k = _restarted(path)
    if k is not None:
        t, x, y = path[k]
        # Stopped out by the line and pushed on: a hog being cleared away.
        if y >= HOG_NEAR_Y_M:
            return Outcome(HOG, t, x, y, len(path))
        # Stopped in play and moved later -- by a later shot, or a sweeper's
        # foot. Where it stopped is what this throw did.
        path = path[:k + 1]
    for i in range(len(path)):
        if _still_from(path, i) is None:
            continue
        # Still by the run's own test from its first sample, but a stone creeps
        # its last half metre: where it ended is everything from here on, which
        # the stone sitting there dominates, and it stopped on reaching it.
        settled = path[i:]
        x = float(np.median([p[1] for p in settled]))
        y = float(np.median([p[2] for p in settled]))
        t_stop = next(p[0] for p in settled
                      if abs(p[1] - x) <= STILL_X_M and abs(p[2] - y) <= STOPPED_Y_M)
        later = [(t, dets) for t, dets in samples
                 if t_stop + STAY_FROM_S <= t <= t_stop + STAY_TO_S]
        if len(later) < 4:
            return None
        here = sum(1 for _t, dets in later
                   if any(abs(d[0] - x) <= STAY_X_M and abs(d[1] - y) <= STAY_Y_M for d in dets))
        stayed = here >= STAY_SHARE * len(later)
        if y <= C.THROUGH_BACK_Y_M:
            # Out of play behind the house, where a stone that ran through
            # comes to rest -- or where a hog is pushed, too late to be the throw.
            return Outcome(THROUGH, t_stop, x, y, len(path)) if _in_time(path, t_release) else None
        if stayed and y <= IN_PLAY_MAX_Y_M and abs(x) <= C.SIDELINE_ABS_X_M:
            return Outcome(REST, t_stop, x, y, len(path))
        if not stayed and y >= HOG_NEAR_Y_M:
            return Outcome(HOG, t_stop, x, y, len(path))
        return None
    t, x, y = path[-1]
    if y <= THROUGH_MAX_Y_M and _in_time(path, t_release):
        pt, _px, py = path[-2]
        if (py - y) / max(t - pt, 1e-6) >= MOVING_M_S:
            return Outcome(THROUGH, t, x, y, len(path))
    return None


def follow(video, view, color, t_release, *, model=None, decode=None, detect=None) -> Outcome | None:
    """`read_outcome` over the long camera ``view`` facing the destination house.

    None without a side model or a laterally calibrated view, or when the
    camera could not say -- the caller then keeps what the overhead decided.
    """
    if view is None or not getattr(view, "has_lateral", False):
        return None
    if model is None:
        from curling_score.detect import sidemodel
        model = sidemodel.default_model()
        if model is None:
            return None
    if decode is None:
        from curling_score.detect import longview
        decode = longview.decode
    if detect is None:
        from curling_score.detect import sidemodel
        detect = sidemodel.detect_band
    frames, times = decode(video, view.rect, t_release + WINDOW_S[0], t_release + WINDOW_S[1], FPS)
    if not len(frames):
        return None
    lo = int(view.tee_row) - ROWS_ABOVE_TEE
    hi = min(int(view.rect[3]), int(view.hog_row) + ROWS_BELOW_HOG)
    boxes = detect(model, frames, times, lo, hi, color, imgsz=800, conf=CONF)
    samples = []
    for t, per in zip(times, boxes):
        dets = []
        for cx, row, _w, conf in per:
            if row >= hi - EDGE_ROWS:
                continue
            x, y = view.to_house(cx, row)
            dets.append((x, y, conf))
        samples.append((t, dets))
    return read_outcome(samples, t_release)


def entry_track(frames, color, t_release):
    """The overhead's track of a stone of ``color`` entering the house at its
    far edge in time to have been this throw running through, or None.

    ``frames`` are the destination panel's detections. A late entry is a hog
    being pushed down the sheet, not the throw (see the module docstring).
    """
    from curling_score.detect import delivery as D

    lo, hi = t_release + 5.0, t_release + THROUGH_MAX_LAG_S
    window = [(t, d) for t, d in frames if lo - 40.0 <= t <= hi + 40.0]
    best = None
    for tr in D._build_tracks(window):
        if tr.color != color or not lo <= tr.ts[0] <= hi:
            continue
        if tr.ys[0] < ENTRY_MIN_Y_M or tr.ys[0] - min(tr.ys) < ENTRY_MIN_TRAVEL_M:
            continue
        if best is None or tr.ts[0] < best.ts[0]:
            best = tr
    return None if best is None else tuple(zip(best.ts, best.xs, best.ys))


# What a hog pushed down the sheet can be taken for: an arrival that changed
# nothing in the house. One that took stones out or added one is a real throw,
# however late -- Sunday Skips 09/27 S2 end 3 rock 16 came in at draw weight
# (a 16.2 s split) 19.6 s after its release and cleared the house.
PUSHED_AS = ("gap-search", "left-view")


def _ran_in(arrival) -> bool:
    """An arrival that came in at the far edge and ran on, never resting in play
    and changing nothing: what a pushed hog looks like."""
    return (arrival.reason in PUSHED_AS and not arrival.came_to_rest
            and arrival.entry_y_m >= ENTRY_MIN_Y_M and arrival.travel_m >= ENTRY_MIN_TRAVEL_M)


def place(unaccounted, thrown_by, arrivals, frames, *, follow_fn):
    """Settle what the overhead could not, by what the long camera saw.

    ``unaccounted`` are `detect.release.unaccounted`'s deliveries, ``thrown_by``
    maps a release to the arrival it was paired with, ``arrivals`` are the
    house's deliveries and ``frames`` its detections. ``follow_fn(color,
    t_release)`` is `follow` bound to the video and the camera facing the house,
    returning None when it cannot say.

    * A release settled as hogged that the overhead saw enter in time to run
      through (`entry_track`) runs through. Otherwise the camera is asked: one
      it saw stop in play becomes a rest there, one it saw run through runs
      through, and one it saw stop by the line and be moved on -- or could
      not follow -- stays hogged.
    * A release paired with an arrival that came in late and ran on is a hog
      pushed down the sheet, unless the camera saw the throw itself arrive:
      the arrival is dropped and the release is hogged.

    Returns ``(arrivals, thrown_by, unaccounted, changed)``, ``changed``
    counting the releases this placed differently.
    """
    from curling_score.detect import release as R

    out, changed = [], 0
    for d in unaccounted:
        r = getattr(d, "release", None)
        if d.reason != R.REASON or r is None:
            out.append(d)
            continue
        # Seen by the overhead running in, in time to be the throw: that is the
        # stronger evidence, and the camera is not asked. Following a stone
        # past a guard, the camera can lose it for a frame and take the guard
        # (Sunday Skips 09/27 S2 end 6 rock 5).
        track = entry_track(frames, r.color, r.t)
        if track:
            out.append(R.ran_through(r, track[-1][0], track[-1][1], track))
            changed += 1
            continue
        o = follow_fn(r.color, r.t)
        if o is not None and o.kind == REST:
            out.append(R.came_to_rest_at(r, o.t, o.x_m, o.y_m))
            changed += 1
        elif o is not None and o.kind == THROUGH:
            out.append(R.ran_through(r, o.t, o.x_m))
            changed += 1
        else:
            out.append(d)
    pairs, dropped = {}, []
    for r, a in thrown_by.items():
        if a.t_enter - r.t > THROUGH_MAX_LAG_S and _ran_in(a):
            o = follow_fn(r.color, r.t)
            if o is None or o.kind == HOG:
                dropped.append(a)
                out.append(R.as_delivery(r))
                changed += 1
                continue
        pairs[r] = a
    if dropped:
        arrivals = [a for a in arrivals if not any(a is x for x in dropped)]
    return arrivals, pairs, out, changed
