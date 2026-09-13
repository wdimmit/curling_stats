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
# First seen this close to the back edge of the view, or it did not come from
# the hack.
ENTRY_MARGIN_M = 0.6
# A release crosses most of the panel before the sweepers close over it.
MIN_TRAVEL_M = 3.0
MIN_SPEED_M_S = 1.0
MAX_SPEED_M_S = 4.5
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


def find_releases(frames, view_y_min_m: float) -> list[Release]:
    """Every stone that left the panel up-sheet the way a delivery does."""
    frames = [(t, list(d)) for t, d in frames]
    if not frames:
        return []
    out: list[Release] = []
    for track in D._build_tracks(frames):
        if len(track.ts) < 4:
            continue
        if track.ys[0] > view_y_min_m + ENTRY_MARGIN_M:
            continue  # started mid-panel: a sweeper, or a stone already in play
        up = track.ys[-1] - track.ys[0]
        dur = track.ts[-1] - track.ts[0]
        if up < MIN_TRAVEL_M or dur <= 0:
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
    # one followed further, which is the stone rather than the broom beside it.
    kept: list[Release] = []
    for r in out:
        if kept and r.t - kept[-1].t < MIN_SEPARATION_S:
            if r.y_exit_m > kept[-1].y_exit_m:
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
                  since: float | None = None):
    """The whole throwing-end pass: find, pair, and settle what did not arrive.

    Returns ``(releases, matched, unaccounted)``. Three callers need exactly
    this sequence -- ``analyze``, ``cli review`` and ``scripts/replay_end`` --
    and ran it as two calls that each paired independently, so the matching
    was computed twice and could drift between them.

    ``since`` drops releases from before an end's run-up, which every caller
    did by hand between the two calls.
    """
    releases = find_releases(frames, view_y_min_m)
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
