"""Was the rock thrown at the broom?

The stone's line just past the throwing hog line -- read from the side camera
that times that crossing, where the stone has certainly left the hand and has
barely begun to curl -- extended to the skip's broom. Then, from the camera
behind the thrower, where the stone actually went, which confirms the line and
says which way it curled.

Everything here is in the timeline's frame: metres from the destination tee,
+y up-sheet toward the thrower, +x the thrower's right.

Attach-only, like ``hogtime`` and ``broomtime``: it may give a shot a
``line`` and can never add, drop or renumber one. Measured on four games on
2026-09-24 (``~/curling-work/line-spike``): the line fits straight to
0.2-0.4 cm and the camera behind the thrower agrees with it to about 4 cm.

In doubles the partner is usually sweeping rather than holding a broom in the
house, and a rock nobody held a broom for is still measured: its start, line,
path and curl need none. Only the figures that measure against a broom -- the
offset at the hog line, the miss, wide or narrow -- are None, and the line is
pinned instead by where it crosses the destination tee. Four-player games keep
asking for a broom (``without_broom`` is the game format's call).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

from curling_score.geometry import constants as C
from curling_score.harvest import sidepool

log = logging.getLogger(__name__)

TEE_Y = C.TEE_TO_TEE_M                          # the throwing tee
HOG_Y = C.TEE_TO_TEE_M - C.TEE_TO_HOGLINE_M     # the throwing hog line
# Free flight only: past the hog line the stone has certainly been released,
# and within 10 m of the tee it has barely curled.
FIT_PAST_TEE_M = (C.TEE_TO_HOGLINE_M, 10.0)
FIT_MIN_N = 15
FIT_MIN_SPAN_M = 2.5
# The camera behind the thrower checks the line only where the rock has barely
# begun to curl: 5.3-9.3 m past the throwing hog line, where the spike found it
# on the fitted line to about 4 cm (2026-09-24). Nearer the house the rock has
# curled 10-36 cm off its thrown line, and comparing there measures the curl.
CONFIRM_WINDOW_Y = (19.0, 23.0)
CONFIRM_MIN_N = 3
CONFIRM_TOL_M = 0.10
CURL_MIN_M = 0.05
THIN_M = 0.5


@dataclass(frozen=True)
class Fit:
    a: float
    b: float
    n: int
    rms: float

    def x(self, y: float) -> float:
        return self.a + self.b * y


@dataclass(frozen=True)
class Line:
    start: tuple | None          # (x, y): the stone before push-off
    at_hog_x: float
    at_hog_offset: float | None  # at_hog_x less the start-to-broom line there
    at_broom_x: float | None     # None: nobody held a broom (doubles)
    miss: float | None           # at_broom_x - broom x, signed
    curl: str | None             # "left" | "right"
    side: str | None             # "wide" | "narrow"
    confirmed: bool | None
    hog_path: tuple              # ((y, x), ...) thinned, travel order
    path: tuple                  # ((y, x), ...) thinned, travel order
    fit_n: int
    fit_rms: float
    at_tee_x: float | None = None  # the line's x at the destination tee, only without a broom
    delivery: tuple = ()         # ((t, y, x), ...) from the rest to past the hog line; see read_delivery


def fit_line(track) -> Fit | None:
    """A straight x(y) through the free-flight samples, or None."""
    lo, hi = FIT_PAST_TEE_M
    pts = [(y, x) for _t, x, y, yp in track if lo <= yp <= hi]
    if len(pts) < FIT_MIN_N:
        return None
    ys = np.array([p[0] for p in pts]); xs = np.array([p[1] for p in pts])
    if np.ptp(ys) < FIT_MIN_SPAN_M:
        return None
    b, a = np.polyfit(ys, xs, 1)
    return Fit(a=float(a), b=float(b), n=len(pts), rms=float(np.std(xs - (a + b * ys))))


def aim_x(start, broom, y: float) -> float:
    """The start-to-broom line's x at depth ``y``."""
    (sx, sy), (bx, by) = start, broom
    return sx + (bx - sx) * (y - sy) / (by - sy)


def curl_of(fit: Fit, end) -> str | None:
    """Which way curl took the rock: from its thrown line to where it ended."""
    if end is None:
        return None
    d = end[0] - fit.x(end[1])
    if abs(d) < CURL_MIN_M:
        return None
    return "right" if d > 0 else "left"


def side_of(miss: float, curl: str | None) -> str | None:
    """Wide is the side away from the curl; narrow the side it curls toward."""
    if curl is None:
        return None
    toward = 1.0 if curl == "right" else -1.0
    return "wide" if np.sign(miss) == -toward else "narrow"


def confirmed_by(path, fit: Fit) -> bool | None:
    """Does the camera behind the thrower see the rock on the fitted line,
    where the rock has not yet curled away from it? None when it did not see
    the rock there."""
    lo, hi = CONFIRM_WINDOW_Y
    seen = [(y, x) for y, x in path if lo <= y <= hi]
    if len(seen) < CONFIRM_MIN_N:
        return None
    dev = float(np.median([abs(x - fit.x(y)) for y, x in seen]))
    return dev <= CONFIRM_TOL_M


def thin(points, step: float = THIN_M) -> tuple:
    """About one (y, x) per ``step`` metres of travel, the last always kept."""
    if not points:
        return ()
    out = [points[0]]
    for p in points[1:]:
        if abs(p[0] - out[-1][0]) >= step - 1e-9:     # 5 x 0.1 must count as 0.5
            out.append(p)
    if out[-1] != points[-1]:
        out.append(points[-1])
    return tuple(out)


def measure(fit: Fit, track, start, broom, rest=None, path=(), delivery=()) -> Line:
    """Everything the Detail pane says about one rock, from its pieces.

    ``broom`` None is a rock nobody held a broom for (doubles): the offset at
    the hog line, the miss and the side are None, and ``at_tee_x`` pins the
    line in their place."""
    at_hog_x = fit.x(HOG_Y)
    end = rest if rest is not None else ((path[-1][1], path[-1][0]) if path else None)
    curl = curl_of(fit, end)
    if broom is None:
        offset = at_broom_x = miss = side = None
        at_tee_x = fit.x(0.0)
    else:
        bx, by = broom
        offset = None if start is None else at_hog_x - aim_x(start, broom, HOG_Y)
        at_broom_x = fit.x(by)
        miss = at_broom_x - bx
        side = side_of(miss, curl)
        at_tee_x = None
    return Line(start=start, at_hog_x=at_hog_x, at_hog_offset=offset,
                at_broom_x=at_broom_x, miss=miss, curl=curl, side=side,
                confirmed=confirmed_by(list(path), fit),
                hog_path=thin([(y, x) for _t, x, y, _yp in track]),
                path=thin(list(path)), fit_n=fit.n, fit_rms=fit.rms, at_tee_x=at_tee_x,
                delivery=tuple(delivery))


CROP_EDGE_ROWS = 6          # the box is clipped at the band's bottom edge


def to_destination(view, cx: float, edge_row: float):
    """A hog-camera box's bottom-centre as the stone's centre, in the
    destination frame, with its distance past the throwing tee. The camera
    faces the thrower: its image-left is the thrower's right, so x flips."""
    yp = view.metres_at(edge_row) - C.STONE_RADIUS_M
    x_view = view.lateral_x(cx, view.row_for(yp))
    return -x_view, TEE_Y - yp, yp


def relink(samples, key, t_seed, fps: float = 30.0, max_gap: int = 6):
    """The stone's samples frame to frame by continuity, both ways from the
    one nearest ``t_seed`` in column key ``key`` -- not by key, which a stone
    drifting across the sheet leaves."""
    by_t: dict = {}
    for s in samples:
        by_t.setdefault(round(s[0], 4), []).append(s)
    ts = sorted(by_t)
    seeds = [s for s in samples if int(s[1] // 120) == key]
    if not seeds:
        return []
    seed = min(seeds, key=lambda s: abs(s[0] - t_seed))
    chain = [seed]
    for direction in (1, -1):
        cur, i, gap = seed, ts.index(round(seed[0], 4)), 0
        while 0 <= i + direction < len(ts):
            i += direction
            steps = abs(ts[i] - cur[0]) * fps
            ok = [s for s in by_t[ts[i]]
                  if abs(s[1] - cur[1]) <= 8 + 3 * steps
                  and -3 * steps <= (s[2] - cur[2]) * direction <= 8 * steps + 4]
            if not ok:
                gap += 1
                if gap > max_gap:
                    break
                continue
            gap = 0
            cur = min(ok, key=lambda s: abs(s[1] - cur[1]) + abs(s[2] - cur[2]))
            chain.append(cur)
    return sorted(chain)


def hog_track(crossing, view, extra=()):
    """The rock through the throwing hog line as [(t, x, y, y_past_tee)]."""
    # `is None`, not `not crossing`: a refused crossing is falsy (no time) but
    # may still carry the stone's samples -- a big-weight hit over the speed bound.
    if crossing is None or getattr(crossing, "track_key", None) is None \
            or not getattr(crossing, "samples", ()):
        return []
    edge = sidepool.band_crop(view)[1] - CROP_EDGE_ROWS
    samples = [s for s in list(crossing.samples) + list(extra) if s[2] < edge]
    if not samples:
        return []
    t_seed = crossing.t if crossing.t is not None else samples[len(samples) // 2][0]
    return [(t, *to_destination(view, cx, row)) for t, cx, row, _w in
            relink(samples, crossing.track_key, t_seed)]


START_WINDOW_S = (-3.0, -0.2)       # before the release: the stone at rest in front of the hack
START_FPS = 5.0
START_BEHIND_TEE_M = (C.TEE_TO_HACKLINE_M - 1.6, C.TEE_TO_HACKLINE_M + 0.6)
START_MAX_X_M = 0.6
START_MIN_N = 3


def pick_start(boxes, view):
    """The median of the stones sitting in front of the hack, or None."""
    lo, hi = -START_BEHIND_TEE_M[1], -START_BEHIND_TEE_M[0]
    xs, ys = [], []
    for cx, row, _w, _c in boxes:
        yp = view.metres_at(row) - C.STONE_RADIUS_M
        if not lo <= yp <= hi:
            continue
        x_view = view.lateral_x(cx, view.row_for(yp))
        if abs(x_view) > START_MAX_X_M:
            continue
        xs.append(-x_view); ys.append(TEE_Y - yp)
    if len(xs) < START_MIN_N:
        return None
    return float(np.median(xs)), float(np.median(ys))


def find_start(model, video, view, color, t_release, *, decode, detect):
    """Read the stone at rest before the push, behind the throwing tee."""
    frames, times = decode(video, view.rect, t_release + START_WINDOW_S[0],
                           t_release + START_WINDOW_S[1], START_FPS)
    if not len(frames):
        return None
    top = int(view.row_for(-(C.TEE_TO_HACKLINE_M + 1.0))) - 30
    bot = int(view.tee_row) + 10
    return pick_start([b for per in detect(model, frames, times, top, bot, color) for b in per], view)


PATH_FPS = 5.0
PATH_SPLIT_ROW = 640            # the far part at imgsz 800, the near part at 416
PATH_CONF = 0.3
PATH_WIDTH = (0.65, 1.5)        # x 0.291 m x lateral_px_per_m(row)
PATH_GAP_S = 8.0                # the delivery team can hide the rock for 6 s
PATH_SEED_Y = 21.5
PATH_START_GAP_S = 1.0          # a start whose next sighting is over a second
                                 # later is a lone false detection (a sweeper's
                                 # broom, a glint), not the rock
# A stone of the rock's colour seen well down-sheet of it, in a frame the rock
# was seen in, cannot be the rock; seen at one spot twice, 0.4 s apart, it is
# sitting there. The tolerance is this camera re-reading one stone (a placed
# guard at 3.2 m read 3.34-3.63 along, 1.15-1.21 across).
PATH_AHEAD_M = 1.0
PATH_REST_TOL_M = (0.3, 0.12)   # (along, across)
PATH_REST_SEEN_S = 0.4
PATH_LOST_S = 1.5 / PATH_FPS    # the rock missed at least one frame


def path_points(boxes, times, view):
    """Destination-camera boxes as (t, x, y, cx, row, conf) per frame. The rock
    moves away from this camera, so its centre is behind the box bottom."""
    out = []
    for t, frame_boxes in zip(times, boxes):
        pts = []
        for cx, row, w, conf in frame_boxes:
            if not PATH_WIDTH[0] < w / (0.291 * view.lateral_px_per_m(row)) < PATH_WIDTH[1]:
                continue
            y = view.metres_at(row) - C.STONE_RADIUS_M
            pts.append((t, view.lateral_x(cx, view.row_for(y)), y, cx, row, conf))
        out.append(pts)
    return out


def _at_rest(d, ahead) -> bool:
    """Was ``d`` where a stone already sat, seen ahead of the rock?"""
    along, across = PATH_REST_TOL_M
    ts = [t for t, y, x in ahead if abs(d[2] - y) < along and abs(d[1] - x) < across]
    return bool(ts) and max(ts) - min(ts) >= PATH_REST_SEEN_S


def _follow(times, per, is_seed, near, max_gap_s):
    # The gates widen with every frame the rock is hidden (a sweeper, the
    # delivery team), and a stone already at rest in them is still the best
    # match once they reach it; from then on it matches every frame and the
    # path ends on it. So once the rock is lost, what the camera saw sitting
    # ahead of it is not the rock. Only once it is lost: a rock followed
    # frame by frame into a freeze reads within a stone of the one it froze to.
    path, cur, vel, last_t, ahead = [], None, -2.0, None, []
    for t, dets in zip(times, per):
        if cur is None:
            c = [d for d in dets if is_seed(d)]
            if c:
                cur = min(c, key=near); path.append(cur); last_t = t
            continue
        dt = t - last_t
        if dt > max_gap_s:
            break
        ypred = cur[2] + vel * dt
        lost = dt > PATH_LOST_S
        c = [d for d in dets if abs(d[2] - ypred) < 0.6 + 0.5 * dt
             and abs(d[1] - cur[1]) < 0.12 + 0.12 * dt and d[2] <= cur[2] + 0.2
             and not (lost and _at_rest(d, ahead))]
        if not c:
            continue
        nxt = min(c, key=lambda d: abs(d[2] - ypred) + abs(d[1] - cur[1]))
        vel = 0.6 * vel + 0.4 * (nxt[2] - cur[2]) / dt
        cur = nxt; path.append(cur); last_t = t
        ahead += [(t, d[2], d[1]) for d in dets if d[2] < cur[2] - PATH_AHEAD_M]
    return path


def chain(times, per, fit: Fit):
    """The rock's path to rest as [(y, x)], started from the detection nearest
    the fitted line -- first where the rock enters this camera's view, then
    anywhere -- and moved on to the next candidate when a start leads nowhere
    (a sweeper's broom, a resting stone near the line)."""
    near = lambda d: abs(d[1] - fit.x(d[2]))
    rules = (lambda d: abs(d[2] - PATH_SEED_Y) < 2.5 and near(d) < 0.35,
             lambda d: 3.0 < d[2] < 24.0 and near(d) < 0.45)
    best = []
    for rule in rules:
        for i, dets in enumerate(per):
            if not any(rule(d) for d in dets):
                continue
            got = _follow(times[i:], per[i:], rule, near, PATH_GAP_S)
            if len(got) >= 2 and got[1][0] - got[0][0] > PATH_START_GAP_S:
                continue
            if len(got) > len(best):
                best = got
            if len(got) >= 5:
                return [(d[2], d[1]) for d in got]
    return [(d[2], d[1]) for d in best] if len(best) >= 5 else []


def find_path(model, video, view, color, t_hog, t_rest, fit, *, decode, detect):
    """Where the rock went, seen from behind the thrower."""
    if view is None or not view.has_lateral or t_hog is None:
        return []
    t1 = (t_rest if t_rest is not None else t_hog + 24.0) + 1.0
    frames, times = decode(video, view.rect, t_hog + 1.0, t1, PATH_FPS)
    if not len(frames):
        return []
    far = detect(model, frames, times, int(view.tee_row) - 80, PATH_SPLIT_ROW + 20, color,
                 imgsz=800, conf=PATH_CONF)
    near_boxes = detect(model, frames, times, PATH_SPLIT_ROW - 20, view.rect[3], color,
                        imgsz=416, conf=PATH_CONF)
    boxes = [a + b for a, b in zip(far, near_boxes)]
    return chain(times, path_points(boxes, times, view), fit)


EXTEND_WINDOW_S = (6.5, 9.0)        # after the release, past hogtime's window
EXTEND_IF_SHORT_OF_M = 10.0         # ...for a track that stops short of here


def _rest(shot):
    i = getattr(shot, "delivered_stone_index", None)
    stones = getattr(shot, "stones", None) or []
    if i is None or not 0 <= i < len(stones):
        return None
    s = stones[i]
    return float(s.x_m), float(s.y_m)


def time_lines(shots, video, hog_view, dest_view, *, model=None, decode=None, detect=None,
               without_broom=False) -> int:
    """Give each shot its ``line``, in place; return how many it gave one.

    A no-op without a model or a laterally calibrated hog-camera view; a shot
    it cannot measure keeps None. ``without_broom`` measures a rock nobody
    held a broom for too (doubles); otherwise such a rock keeps None.
    Attach-only, like ``hogtime`` and ``broomtime``: a decode or detector
    error on one rock is caught and logged, and costs that rock its line, not
    the game its analysis -- the same reasoning as ``analyze.read_board``.
    """
    if model is None or hog_view is None or not hog_view.has_lateral:
        return 0
    if decode is None:
        from curling_score.detect import longview
        decode = longview.decode
    if detect is None:
        from curling_score.detect import sidemodel
        detect = sidemodel.detect_band
    n = 0
    for shot in shots:
        broom = getattr(shot, "target_broom", None)
        rel = getattr(shot, "release", None)
        crossing = getattr(shot, "hog_crossing", None)
        if getattr(shot, "missing", False) or rel is None or crossing is None:
            continue
        if broom is None and not without_broom:
            continue
        try:
            track = hog_track(crossing, hog_view)
            if track and max(p[3] for p in track) < EXTEND_IF_SHORT_OF_M:
                extra = _extend_for(shot, hog_view, video, rel.t, model, decode, detect)
                track = hog_track(crossing, hog_view, extra)
            fit = fit_line(track)
            if fit is None:
                continue
            # One read from before the push to the band gives the start and
            # the delivery both; only the delivery's assembly may fail alone.
            frames = read_delivery(model, video, hog_view, shot.color, rel.t, track[0][0],
                                   decode=decode, detect=detect)
            try:
                delivery = delivery_path(track, frames, rel.t)
            except Exception:
                log.exception("delivery failed on shot %s; its line keeps none",
                              getattr(shot, "number", "?"))
                delivery = ()
            path = find_path(model, video, dest_view, shot.color, getattr(shot, "t_hog_s", None),
                             getattr(shot, "t_rest_s", None), fit, decode=decode, detect=detect)
            shot.line = measure(fit, track, frames.start,
                                None if broom is None else (broom.x_m, broom.y_m),
                                rest=_rest(shot), path=path, delivery=delivery)
            n += 1
        except Exception:
            log.exception("line pass failed on shot %s; it keeps no line",
                          getattr(shot, "number", "?"))
    return n


def _extend_for(shot, view, video, t_release, model, decode, detect):
    """Detections in the seconds past hogtime's window, as proposer samples."""
    from curling_score.detect import longview, sidemodel
    frames, times = decode(video, view.rect, t_release + EXTEND_WINDOW_S[0],
                           t_release + EXTEND_WINDOW_S[1], 30.0)
    if not len(frames):
        return []
    lo, hi = sidepool.band_crop(view)
    out = []
    for t, boxes in zip(times, detect(model, frames, times, lo, hi, shot.color)):
        for cx, row, w, _c in boxes:
            expect = view.stone_width_at(row, longview.STONE_WIDTH_AT_HOG_PX)
            if expect > 1 and sidemodel.WIDTH_TOL[0] <= w / expect <= sidemodel.WIDTH_TOL[1]:
                out.append((t, cx, row, w))
    return out


# The delivery, close up: the rock from where it sat in front of the hack to
# 1.5 m past the throwing hog line, a sample every tenth of a second, for the
# viewer's delivery chart (schema 8's ``line.delivery``). Most of the sideways
# movement a thrower puts on a rock happens in the hand -- at the push-off and
# in late steering -- where no figure above looks. The band's own 30 fps track
# covers it from hogtime's window on (2 s after the release, 2-3 m past the
# tee); one read at 10 fps from before the push to the band's first sample
# covers the rest, and replaces the start's own read, the start being picked
# from that read's frames on START_FPS's grid.
#
# 10 fps, measured 2026-09-29 on s_0N8Q's 94 rocks against the 30 fps path:
# 0.4 cm p95 across (4.1 cm worst), the biggest sideways move in the slide
# 0.2 cm short at the median, every rock followed back to its rest. 15 fps
# bought nothing past that and would take the live lane to its limit.
DELIVERY_FPS = 10.0
DELIVERY_MAX_AFTER_S = 5.0      # the band's first sample comes 2.0-3.1 s after the release
DELIVERY_TO_M = 4.5             # the read's crop runs past the band's first rows
DELIVERY_END_M = C.TEE_TO_HOGLINE_M + 1.5
DELIVERY_KEEP_M = 8.2
DELIVERY_MAX_X_M = 1.0          # stones parked at the side sit 1.4 m or more out
DELIVERY_LOST_S = 1.2           # unseen this long, stop rather than guess
DELIVERY_V0 = 2.0               # m/s at the band, when its track is too short to say
# Where the rock will be is a straight line through its last few samples, not
# its last one: at the hack's distance one reading can be 0.5 m out along, and
# a speed taken from it runs every prediction after it off (AEqL e1 r1). The
# speed is held to what a rock does here, forward and back over 0.3 s, measured
# on s_0N8Q's 94 rocks: 3.9 m/s and -1.0 at the most.
DELIVERY_FIT_N, DELIVERY_FIT_S = 5, 0.6
DELIVERY_V_M_S = (-1.1, 4.0)
# The follow's gates, (base, per second unseen, cap), across and along: capped,
# because an open-ended gate reaches the stones parked at the side.
DELIVERY_GATE_X = (0.06, 0.40, 0.20)
DELIVERY_GATE_YP = (0.30, 2.5, 0.90)
# A band sample that leads nowhere back (its first is sometimes read short
# where the thrower's body meets the band) gives way to the next, this far in.
DELIVERY_ANCHOR_S = 0.3
# A stone sitting still in front of the hack is one dot, drawn twice: a run of
# samples within this camera re-reading one stone (0.15 m along, 0.015 across
# at that distance) keeps its first and last.
DELIVERY_STILL_M = (0.15, 0.015)
DELIVERY_REST_BEHIND_M = -2.0


@dataclass(frozen=True)
class DeliveryFrames:
    times: tuple                # the read's frame times, video seconds
    dets: tuple                 # per frame, ((x, yp), ...): the rock's colour, near the centre line
    start: tuple | None         # pick_start over the frames on START_FPS's grid


def read_delivery(model, video, view, color, t_release, t_band, *, decode, detect,
                  fps: float = DELIVERY_FPS) -> DeliveryFrames:
    """One read of the hog camera from before the push to ``t_band``, the
    band's first sample: the stone at rest, which is the line's start, and the
    rock's way from there to the band."""
    t0 = t_release + START_WINDOW_S[0]
    t1 = min(t_release + DELIVERY_MAX_AFTER_S, max(t_release, t_band) + 0.1)
    frames, times = decode(video, view.rect, t0, t1, fps)
    if not len(frames):
        return DeliveryFrames((), (), None)
    top = int(view.row_for(-(C.TEE_TO_HACKLINE_M + 1.0))) - 30
    bot = int(view.row_for(DELIVERY_TO_M + C.STONE_RADIUS_M)) + 30
    boxes = detect(model, frames, times, top, bot, color)
    step = max(1, round(fps / START_FPS))
    t_last = t_release + START_WINDOW_S[1] + 1e-6
    start = pick_start([b for i, (t, per) in enumerate(zip(times, boxes))
                        if i % step == 0 and t <= t_last for b in per], view)
    # A box cut by the crop's bottom edge puts the stone short of where it is.
    edge = min(bot, view.rect[3]) - CROP_EDGE_ROWS
    dets = []
    for per in boxes:
        here = []
        for cx, row, _w, _c in per:
            if row >= edge:
                continue
            x, _y, yp = to_destination(view, cx, row)
            if abs(x) <= DELIVERY_MAX_X_M:
                here.append((x, yp))
        dets.append(tuple(here))
    return DeliveryFrames(tuple(times), tuple(dets), start)


def _gate(g, dt: float) -> float:
    base, per_s, cap = g
    return min(base + per_s * dt, cap)


def _band_speed(track) -> float:
    """m/s along the sheet over the band's first samples."""
    head = track[:8]
    if len(head) < 8 or head[-1][0] - head[0][0] <= 0:
        return DELIVERY_V0
    return float(np.polyfit([p[0] for p in head], [p[3] for p in head], 1)[0])


def _predict(seen, t: float, vel: float) -> float:
    """Where the rock was at ``t``, from the samples ``seen`` since the anchor."""
    lo, hi = DELIVERY_V_M_S
    cur_t, _x, cur_yp = seen[-1]
    recent = [p for p in seen[-DELIVERY_FIT_N:] if p[0] - t <= DELIVERY_FIT_S + (cur_t - t)]
    if len(recent) < 3:
        return cur_yp - min(max(vel, lo), hi) * (cur_t - t)
    ts = np.array([p[0] for p in recent]); ys = np.array([p[2] for p in recent])
    b = min(max(float(np.polyfit(ts, ys, 1)[0]), lo), hi)
    return float(ys.mean() + b * (t - ts.mean()))


def _follow_back(frames: DeliveryFrames, anchor, vel: float):
    """The rock from ``anchor`` (t, x, yp) back through the read's earlier
    frames, by where it was and how fast it went, as [(t, x, yp)] in time
    order. ``vel`` is its speed at the anchor, for the first steps back."""
    seen = [anchor]
    for t, dets in reversed(list(zip(frames.times, frames.dets))):
        cur_t, cur_x, _yp = seen[-1]
        if t >= cur_t - 1e-6:
            continue
        dt = cur_t - t
        if dt > DELIVERY_LOST_S:
            break
        pred = _predict(seen, t, vel)
        gx, gy = _gate(DELIVERY_GATE_X, dt), _gate(DELIVERY_GATE_YP, dt)
        ok = [d for d in dets if abs(d[0] - cur_x) <= gx and abs(d[1] - pred) <= gy]
        if not ok:
            continue
        x, yp = min(ok, key=lambda d: 3 * abs(d[0] - cur_x) + abs(d[1] - pred))
        seen.append((t, x, yp))
    return seen[1:][::-1]


def _on_grid(track, t0: float, fps: float):
    """The band's samples nearest the read's grid (t0 + k/fps), as [(t, x, yp)]."""
    best: dict = {}
    for t, x, _y, yp in track:
        k = round((t - t0) * fps)
        off = abs(t - (t0 + k / fps))
        if off <= 0.5 / 30 + 1e-6 and (k not in best or off < best[k][0]):
            best[k] = (off, (t, x, yp))
    return [best[k][1] for k in sorted(best)]


def _to_end(pts):
    """Up to the first sample past DELIVERY_END_M, none past DELIVERY_KEEP_M."""
    out = []
    for p in pts:
        if p[2] > DELIVERY_KEEP_M:
            break
        out.append(p)
        if p[2] > DELIVERY_END_M:
            break
    return out


def _collapse_rest(pts):
    """Each run of samples sitting still in front of the hack as its first and last."""
    along, across = DELIVERY_STILL_M
    out, i = [], 0
    while i < len(pts):
        j = i
        if pts[i][2] < DELIVERY_REST_BEHIND_M:
            while j + 1 < len(pts) and pts[j + 1][2] < DELIVERY_REST_BEHIND_M \
                    and abs(pts[j + 1][2] - pts[i][2]) <= along and abs(pts[j + 1][1] - pts[i][1]) <= across:
                j += 1
        out += [pts[i], pts[j]] if j - i >= 2 else list(pts[i:j + 1])
        i = j + 1
    return out


def delivery_path(track, frames: DeliveryFrames, t_release: float) -> tuple:
    """The rock from its rest to just past DELIVERY_END_M as ((t, y, x), ...),
    t seconds from the release. Anchored on the band, which is certainly the
    thrown rock -- a stone of its colour waiting at the other hack is not --
    and followed back to the rest; the band's own samples on the same grid
    from the anchor on. The first band sample that leads back to the rest is
    the anchor, else the one that leads furthest. () without a track."""
    if not track:
        return ()
    t0 = t_release + START_WINDOW_S[0]
    best = None
    for i, (ta, xa, _ya, ypa) in enumerate(track):
        if ta > track[0][0] + DELIVERY_ANCHOR_S:
            break
        back = _follow_back(frames, (ta, xa, ypa), _band_speed(track[i:]))
        if best is None or len(back) > len(best[1]):
            best = (i, back)
        if back and min(p[2] for p in back) < DELIVERY_REST_BEHIND_M:
            break
    i, back = best
    pts = back + _on_grid(track[i:], t0, DELIVERY_FPS)
    pts = _collapse_rest(_to_end(pts))
    return tuple((t - t_release, TEE_Y - yp, x) for t, x, yp in pts)
