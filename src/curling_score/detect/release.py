"""Deliveries seen leaving the thrower's own house.

The two overhead cameras look at the houses, and a stone that is hogged --
released too slowly to reach the far hog line, and taken out of play -- never
comes into either house's view. But every delivery starts in the thrower's own
house: the slide begins in the hack behind it and the stone is released before
the near hog line, so the stone crosses that house's overhead panel from the
back edge to the top at about 2 m/s on its way out. The panel already has a
trained detector and a calibration. Watching it for stones *leaving* gives a
count of throws that does not depend on what became of them, and a throw with
no arrival in the far house is a hogged rock. Measured on the club's feed:
releases enter within 0.1 m of the back edge of the view at 1.5-2.1 m/s and
travel 4.5-6.7 m before the tracker loses them among the sweepers; a red-
jacketed sweeper running beside a release is also picked up moving up-sheet,
but starts mid-panel, which is what the entry test refuses.

A release is timed from the stone's first sighting at the back edge, which is
the start of the slide rather than the hand, some 8 m before the hog line. The
club puts hand-release to arrival at 6 s at the fastest and usually 10-15 s;
measured from the slide it was 11-24 s across three ends, so the window runs
to 30 s. Generous at the top on purpose: a slow draw is exactly the stone most
likely to be hogged, and the one that would take longest if it were not.

A release with no arrival is not yet a hogged rock. The house camera loses
arrivals too, and a throw whose arrival went unseen looks the same from the
thrower's end. The house settles it: if the far sheet gained a stone of the
colour, or lost one of either, in the half-minute after the throw, the rock
arrived and did that; if the house did not change, it never got there.
"""

from __future__ import annotations

from dataclasses import dataclass

from curling_score.detect import delivery as D
from curling_score.geometry import constants as C

# Half the rate the houses are watched at: a stone leaving at 2 m/s still
# moves only 0.4 m between samples, well inside the tracker's bootstrap gate.
RELEASE_FPS = 5.0
# Stage 1 of a throw: the stone leaves the hack and crosses this line. One foot
# behind the tee, in sheet metres -- the tee is y = 0 by calibration, so it is
# the same number on every panel.
#
# This replaces a travel minimum of 3.0 m, which from a back edge at y = -2.01
# (top panel) or -2.21 (bottom) finished at y = +0.79 to +0.99, about a metre
# PAST the T-line. That metre is the one the sweepers take away: measured over
# AEqLTgM25Tc, the 27 rocks with no release have a median top-of-track of
# y = 1.02 m against 3.48 m for the rocks that produce one. The gate asked for
# exactly the evidence that stops being available, and 25 of those 27 rocks
# were refused within a metre of passing.
#
# Coverage is flat at 88 of 90 anywhere between the tee and 2.75 ft behind it,
# because no track that exists fails to REACH the line -- every failure is a
# track acquired above it already. What moves is clearance: worst-case margin
# is 0.170 m at the tee, 0.475 m here, 0.489 m at 1.25 ft, and 0.006 m at 3 ft.
# 1.25 ft is the optimum and this is within 3% of it on a round number.
STAGE1_Y_M = -0.3048
# Fewest samples a track may have and still be a throw. Three, not four,
# because e2 s12 on AEqLTgM25Tc is a real delivery the panel caught exactly
# three times -- y -2.16 -> +0.57 at 2.73 m/s, on the centre line -- and four
# costs that rock and no other.
MIN_SAMPLES = 3
MIN_SPEED_M_S = 1.0
MAX_SPEED_M_S = 4.5
# How far off the centre line a delivery may run, as a fraction of the panel's
# half-width. A thrower starts in the hack, which is on the centre line, and
# measured over all 88 deliveries of AEqLTgM25Tc that the panel follows past
# the tee, the widest ran |x| = 0.32 m. Everything else this panel offers runs
# wider -- stones parked at the edges between ends, and the delivering player's
# own arm and shoulder as they slide up-sheet -- a median of 1.10 m over the
# 265 other tracks in that game.
#
# 0.35 of the half-width is 0.65 m on the club's bottom panel and 0.76 m on its
# top, so it keeps all 88 with twice the margin of the widest and culls 66% of
# the rest.
#
# 0.50 WAS TRIED FIRST AND IS NOT ENOUGH. It culls stones parked at the edges
# (those sit at 1.2-1.6 m) but not the thrower, who reads at x = 0.78-0.85
# during the slide, inside a 0.93 m bound -- and `_build_tracks` then joins
# those boxes to the real delivery, so the track runs on past where the stone
# actually went. Frames in datasets/ds11/hardneg.
#
# It does NOT catch a box on the thrower's trailing arm, which sits directly
# behind the stone on the centre line (x = +0.24 against the stone's +0.25).
# Nothing lateral can; that one needs the detector retrained.
CENTRE_FRACTION = 0.35
# Two tracks closer than this laterally, overlapping or touching in time, are
# one stone the detector fragmented rather than two objects. Measured on
# AEqLTgM25Tc: the one pair that is genuinely two objects (e6 s6) sits 0.60 m
# apart, and the one pair that is one stone (e2 s8) sits 0.02 m apart. There is
# nothing in between, so the threshold is not delicate.
MERGE_LATERAL_M = 0.3
# Two tracks of one stone agree about where it is. Where they hold samples at
# the same instant, disagreement beyond this means two objects sharing a lane,
# and joining those destroys the better of them: `_join` gives every collision
# to the longer track, so a long interferer erases a short delivery outright.
#
# The one genuine fragment pair measured -- e1 s1's two boxes at the same
# frame, y = -0.87 and y = -1.19 -- disagrees by 0.32 m, a little over a
# stone's 0.284 m diameter. The erasure case disagrees by up to 2.10 m. 0.5
# sits between, with about 1.6x margin on the real pair.
MERGE_AGREE_M = 0.5
# Release to arrival in the far house.
MIN_LAG_S = 6.0
MAX_LAG_S = 30.0
# Two throws are never this close together (see ``fit.MIN_SEPARATION_S``).
MIN_SEPARATION_S = 10.0
REASON = "hogged"
# Reasons for a throw the house camera did not follow but the house confirms.
REASON_ADD = "release-add"        # a stone of its colour appeared: it arrived unseen
REASON_REMOVE = "release-remove"  # a stone went missing: it hit and rolled out
# How long after the throw to read the far house for what it did.
SETTLE_S = 36.0


@dataclass(frozen=True)
class Release:
    """A stone seen leaving the thrower's house."""

    color: str
    t: float           # first sighting, at the back edge of the view
    y_exit_m: float    # how far up the panel it was followed
    speed_m_s: float
    # The climb itself, as ``(t, x, y)``. A release is a line crossing waiting
    # to be timed -- the near tee line for thinking time, the hog line for the
    # long split -- and neither can be read from the endpoints alone.
    track: tuple[tuple[float, float, float], ...] = ()


def on_centre_line(x_m: float, view_x_limit_m: float | None) -> bool:
    """Whether a detection is near enough the centre line to be a delivery.

    ``view_x_limit_m`` of None means no bound, which is what a caller with no
    panel to hand gets -- a test building frames by hand, and the pre-existing
    callers that never had a lateral limit to pass. Every production caller
    passes ``PanelSetup.view_x_limit_m``; see CENTRE_FRACTION.
    """
    if view_x_limit_m is None:
        return True
    return abs(x_m) <= view_x_limit_m * CENTRE_FRACTION


def _mean_x(track) -> float:
    return sum(track.xs) / len(track.xs)


def _join(a, b):
    """One track from two, preferring the better-sampled one where they collide.

    Where both tracks hold a sample at the same instant they disagree about
    where the stone was, and the better-sampled track is the one to believe: on
    e1 s1 the two boxes were y = -0.87 and y = -1.19, and interpolating that
    stone's neighbours puts it at -0.85.
    """
    long_, short_ = (a, b) if len(a.ts) >= len(b.ts) else (b, a)
    by_t = {round(t, 3): (x, y)
            for t, x, y in zip(short_.ts, short_.xs, short_.ys)}
    by_t.update({round(t, 3): (x, y)
                 for t, x, y in zip(long_.ts, long_.xs, long_.ys)})
    ts = sorted(by_t)
    out = D._Track(a.color, ts[0], *by_t[ts[0]])
    for t in ts[1:]:
        out.add(t, *by_t[t])
    return out


def _agree(a, b) -> bool:
    """Whether two tracks describe the same stone where both hold a sample.

    Compared on ``round(t, 3)``, the same key `_join` uses to detect a
    collision. Any instant where the two disagree by more than
    ``MERGE_AGREE_M`` is two objects sharing a lane, not one stone in pieces.
    """
    by_t_a = {round(t, 3): y for t, y in zip(a.ts, a.ys)}
    by_t_b = {round(t, 3): y for t, y in zip(b.ts, b.ys)}
    for t, ya in by_t_a.items():
        yb = by_t_b.get(t)
        if yb is not None and abs(ya - yb) > MERGE_AGREE_M:
            return False
    return True


def _plausible(track) -> bool:
    """Whether a track's samples describe motion a stone could make.

    A join fabricates a jump if it grafts a second track on where the first
    left off: two fragments of one stone were never far apart, so a step no
    real stone could take marks the join as wrong, not the stone as fast.
    """
    pairs = zip(zip(track.ts, track.ys), zip(track.ts[1:], track.ys[1:]))
    for (t0, y0), (t1, y1) in pairs:
        if t1 <= t0:
            continue
        if abs((y1 - y0) / (t1 - t0)) > MAX_SPEED_M_S:
            return False
    return True


def _merge_fragments(tracks):
    """Join tracks that are one stone the detector split.

    A duplicate box in a single frame is enough to split a delivery, and each
    half on its own can fail stage 1 from opposite directions -- one stops
    short of the line, the other is first seen above it.

    Restarts after every join so a stone broken into three pieces collapses to
    one. There are a handful of tracks in a release window, so the quadratic
    scan costs nothing worth avoiding.
    """
    out = sorted(tracks, key=lambda tr: tr.ts[0])
    joined = True
    while joined:
        joined = False
        for i in range(len(out)):
            for j in range(i + 1, len(out)):
                a, b = out[i], out[j]
                if a.color != b.color:
                    continue
                if max(a.ts[0], b.ts[0]) > min(a.ts[-1], b.ts[-1]):
                    continue                    # a gap between them in time
                if abs(_mean_x(a) - _mean_x(b)) > MERGE_LATERAL_M:
                    continue                    # too far apart to be one stone
                if not _agree(a, b):
                    continue                    # disagree where both hold a sample
                candidate = _join(a, b)
                if not _plausible(candidate):
                    continue                    # the join implies an impossible speed
                out[i] = candidate
                del out[j]
                joined = True
                break
            if joined:
                break
    return out


def find_releases(frames, view_y_min_m: float,
                  view_x_limit_m: float | None = None) -> list[Release]:
    """Every stone seen leaving the hack and crossing the stage-1 line.

    Stage 1 of a throw, and nothing more: it does not ask how far the stone
    then travelled, because the overhead panel loses about 30% of deliveries
    within a metre of the T-line and the hog line is stage 2's job, watched
    from a camera that can actually see it.

    Detections off the centre line are dropped BEFORE tracking, not after.
    Dropping whole tracks afterwards does not work: a box on the delivering
    player is picked up by ``_build_tracks`` and joined to the stone's own
    track, so by the time there is a track to judge, the bad samples are
    already inside a good one.

    THROWING SIDE ONLY. The arriving house must never be bounded this way -- a
    stone comes to rest wherever it is played, and these two panels swap roles
    every end, so the crop that is a delivery lane in one end is a house in the
    next.
    """
    frames = [(t, [d for d in dets if on_centre_line(d.x_m, view_x_limit_m)])
              for t, dets in frames]
    if not frames:
        return []
    if view_y_min_m >= STAGE1_Y_M:
        raise ValueError(
            f"this panel sees down to y={view_y_min_m:.2f} m, which is above "
            f"the stage-1 line at {STAGE1_Y_M:.4f} m: it cannot watch a stone "
            f"leave the hack, so no throw here could ever be confirmed")
    out: list[Release] = []
    for track in _merge_fragments(D._build_tracks(frames)):
        if len(track.ts) < MIN_SAMPLES:
            continue
        if track.ys[0] >= STAGE1_Y_M:
            continue  # first seen above the line: not watched leaving the hack
        if track.ys[-1] < STAGE1_Y_M:
            continue  # never reached it
        up = track.ys[-1] - track.ys[0]
        dur = track.ts[-1] - track.ts[0]
        if dur <= 0:
            continue
        speed = up / dur
        if not MIN_SPEED_M_S <= speed <= MAX_SPEED_M_S:
            continue
        out.append(Release(
            track.color, track.ts[0], track.ys[-1], speed,
            track=tuple(zip(track.ts, track.xs, track.ys)),
        ))
    out.sort(key=lambda r: r.t)
    # One throw at a time: of two sightings inside the separation, keep the
    # better-sampled one, which is the stone rather than whatever crossed
    # beside it.
    #
    # This used to keep the one followed FURTHEST, which worked while a release
    # had to climb three metres and stopped working when it did not. On e6 s6
    # the real delivery carries eight samples and reaches y = +1.82 while the
    # thing 0.60 m beside it carries two and reaches +2.97, so distance now
    # picks the wrong one and sample count picks the right one.
    kept: list[Release] = []
    for r in out:
        if kept and r.t - kept[-1].t < MIN_SEPARATION_S:
            if len(r.track) > len(kept[-1].track):
                kept[-1] = r
            continue
        kept.append(r)
    return kept


def pair(releases, deliveries):
    """Match each release to the arrival it became. Returns ``(matched, unmatched)``.

    ``matched`` maps a release to its delivery; ``unmatched`` lists releases
    with no arrival of their colour inside the lag window -- hogged rocks, or
    deliveries the house camera lost entirely.
    """
    arrivals = sorted(deliveries, key=lambda d: d.t_enter)
    taken: set[int] = set()
    matched, unmatched = {}, []
    for r in sorted(releases, key=lambda r: r.t):
        hit = None
        for i, d in enumerate(arrivals):
            if i in taken or d.color != r.color:
                continue
            lag = d.t_enter - r.t
            if lag > MAX_LAG_S:
                break
            if lag >= MIN_LAG_S:
                hit = i
                break
        if hit is None:
            unmatched.append(r)
        else:
            taken.add(hit)
            matched[r] = arrivals[hit]
    return matched, unmatched


def as_delivery(release: Release) -> D.Delivery:
    """A hogged rock as a delivery: thrown, timed, and never in the house.

    Its time is the release, which is also where a viewer wants the video to
    start. It "rests" beyond the hog line, which is where the rules say a
    hogged stone is taken from, so the house read after it is unchanged.
    """
    return D.Delivery(
        color=release.color,
        t_enter=release.t,
        t_rest=release.t + MIN_LAG_S,
        entry_y_m=C.HOGGED_Y_M,
        rest_x_m=0.0,
        rest_y_m=C.HOGGED_Y_M,
        travel_m=0.0,
        came_to_rest=False,
        reason=REASON,
    )


def settle(release: Release, frames) -> D.Delivery:
    """What became of a throw the house camera did not follow, read from the house.

    ``frames`` are the target house's detections. Compare the settled stones
    just before the throw with those once the stone would have stopped: a
    stone of the release's colour that appeared is the rock, arrived unseen;
    a stone that vanished was struck by it as it ran through; nothing changed
    means it never reached the house -- hogged.
    """
    t = release.t
    before = D._settled_stones(frames, t - D.CHANGE_WINDOW_S, t)
    after = D._settled_stones(frames, t + SETTLE_S, t + SETTLE_S + D.CHANGE_WINDOW_S)
    rock = as_delivery(release)
    if before is None or after is None:
        return rock
    added, removed = D._house_delta(before, after)
    mine = [(x, y) for c, x, y in added if c == release.color]
    if mine:
        x, y = mine[0]
        return D.Delivery(color=release.color, t_enter=t, t_rest=t + SETTLE_S,
                          entry_y_m=C.HOGGED_Y_M, rest_x_m=x, rest_y_m=y,
                          travel_m=C.HOGGED_Y_M - y, came_to_rest=True, reason=REASON_ADD)
    if removed:
        return D.Delivery(color=release.color, t_enter=t, t_rest=t + SETTLE_S,
                          entry_y_m=C.HOGGED_Y_M, rest_x_m=0.0, rest_y_m=C.THROUGH_BACK_Y_M,
                          travel_m=C.HOGGED_Y_M - C.THROUGH_BACK_Y_M, came_to_rest=False,
                          reason=REASON_REMOVE)
    return rock


def find_and_pair(frames, view_y_min_m: float, deliveries, house_frames=(),
                  since: float | None = None,
                  view_x_limit_m: float | None = None):
    """The whole throwing-end pass: find, pair, and settle what did not arrive.

    Returns ``(releases, matched, unaccounted)``. Three callers need exactly
    this sequence -- ``analyze``, ``cli review`` and ``scripts/replay_end`` --
    and ran it as two calls that each paired independently, so the matching
    was computed twice and could drift between them.

    ``since`` drops releases from before an end's run-up, which every caller
    did by hand between the two calls.
    """
    releases = find_releases(frames, view_y_min_m, view_x_limit_m)
    if since is not None:
        releases = [r for r in releases if r.t >= since]
    pairing = pair(releases, deliveries)
    matched, _ = pairing
    return releases, matched, unaccounted(releases, deliveries, house_frames,
                                          pairing=pairing)


def unaccounted(releases, deliveries, frames=(), pairing=None) -> list[D.Delivery]:
    """The releases with no arrival, as deliveries the rules can place.

    With the target house's ``frames`` each is settled by what the house did;
    without them every one is taken as hogged.

    A release whose window holds an arrival of the *other* colour that no
    release of its own accounts for is the same throw with the handle colour
    misread from the far end -- game 4 end 2's "red" at 1616 was the yellow
    that arrived 18 s later. The next real delivery is a full interval away,
    so an unclaimed arrival that soon can only be this one. Such a release is
    explained, and adds nothing.
    """
    matched, unmatched = pair(releases, deliveries) if pairing is None else pairing
    claimed = set(id(d) for d in matched.values())
    frames = [(t, list(d)) for t, d in frames]
    out = []
    for r in unmatched:
        misread = any(
            id(d) not in claimed and d.color != r.color
            and MIN_LAG_S <= d.t_enter - r.t <= MAX_LAG_S
            for d in deliveries
        )
        if misread:
            continue
        out.append(settle(r, frames) if frames else as_delivery(r))
    return out


hogged = unaccounted
