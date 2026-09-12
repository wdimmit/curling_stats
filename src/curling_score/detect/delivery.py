"""Detect thrown stones by their travel down the sheet.

Knowing *when each stone was delivered* is what the end structure hangs on.
Inferring it from how the house changes does not work: between ends the players
push stones back toward the end they are about to throw from, and those staged
rocks look exactly like new arrivals. On the reference VOD that inflated one
end's house from 4 stones to 12 and scored it seven minutes after the last shot.

A delivery has a signature nothing else does. The overhead panel sees the stone
enter at roughly 3.7 m up-sheet and run monotonically to rest near the house --
a traverse of several metres, tracked in every frame. Brooms and sweepers in
team colours do appear near the panel's far edge, but they wander; they never
cross the sheet. Requiring the traverse separates them cleanly.

It also names the thrower directly: the moving stone's own colour, with no
appeal to the alternation rule.
"""

from dataclasses import dataclass

from curling_score.geometry import constants as C

# Sheet metres. Only enough to rule out a stone that never really moved --
# distance is *not* a good filter for whether something was thrown. A guard is
# thrown short and stops in front of the house, so its visible travel inside the
# panel can be under a metre (one measured 0.79 m). Leads throw mostly guards, so
# a large threshold here quietly discards the opening deliveries of every end.
MIN_TRAVEL_M = 0.25
# Travel alone does *not* excuse a track from having to persist. A sweeper in a
# team-coloured jacket runs alongside the delivery, so their phantom track
# enters up-sheet, crosses several metres and decelerates -- every hallmark of a
# throw. What it does not do is stay put afterwards. The only thing that
# genuinely excuses persistence is leaving the playing area, which is what a
# takeout does after it has done its job.
# What separates a delivery from a broom nudge or a mis-detection is that the
# stone is *still sitting there* once the players have cleared off.
#
# Checked a few seconds after the stone settles, not ten. Ten was chosen to give
# the players time to clear off, but at the end of an end they clear the *rocks*
# too -- in end 4 the last stone rested at t=3546.5 and the house was empty by
# t=3557 -- so a ten-second check systematically rejected the one delivery that
# decides the score. Five seconds is still far longer than a broom head or a
# sweeper's jacket lingers, and comfortably short of the ~50 s to the next
# delivery, so the check is not confounded by the next stone arriving.
PERSIST_S = 5.0
PERSIST_TOLERANCE_M = 0.20
# ...judged over a window, since any single frame can be occluded.
PERSIST_WINDOW_S = 3.0
# If we cannot watch at least this long after the stone settles, persistence is
# unconfirmable and the track is not claimed as a delivery.
MIN_CONFIRM_S = 4.0
# A delivery arrives from the delivery end. Every real one in end 4 entered the
# panel at y >= 3.4 m; a stone picked up mid-house is one that was struck, and
# its flight out the back otherwise looks exactly like a shot.
MIN_ENTRY_Y_M = 2.5
# The stone must actually run down-sheet, not drift about.
MIN_NET_TRAVEL_FRACTION = 0.7
# And it must run down-sheet more than across it. A stone is thrown along a
# 45 m sheet 4.75 m wide; inside the panel it covers three to five metres of
# length while curling well under a metre, and even a shooter rolling out
# after a takeout is still going mostly forwards. A blob that moves further
# sideways than onwards is someone at the delivery end stepping aside -- one
# such was accepted in end 1 having crossed 2.0 m of sheet laterally against
# 1.2 m down, and stopped up-sheet of the house at y = +3.29.
MAX_LATERAL_RATIO = 1.0
# A track first seen below the entry gate may still be a delivery the detector
# picked up late, but not if it was never seen above the tee at all: a stone
# has to cross the whole panel to get there, and something first seen behind
# the house is far more likely to have been struck than thrown. One such, first
# seen at y = -0.80 in game 2 end 1, was accepted and then out-competed the
# real delivery 29 s later.
LATE_ENTRY_MIN_Y_M = 0.0
# Matching gate. A stone under sweeping is hidden for up to ~1.2 s at a time, so
# a track must survive gaps of that order or it fragments into pieces too short
# to recognise. The gate is anchored on the track's *predicted* position and
# grows with its own speed rather than a worst-case speed -- a fixed
# worst-case gate widens to metres over a long gap and starts swallowing
# neighbouring stones.
MATCH_BASE_M = 0.30
MATCH_SPEED_SLACK = 0.6   # share of the predicted travel allowed as error
MATCH_MAX_M = 1.2
TRACK_TIMEOUT_S = 2.5
# Until a track has seen two frames it has no velocity to predict with, so its
# gate has to cover the fastest a stone can plausibly move. Takeouts run at
# 1.5-3 m/s, which at 5 fps is 0.3-0.6 m between frames -- far outside the
# steady-state gate. Narrow it again as soon as a velocity exists.
BOOTSTRAP_SPEED_M_S = 3.5
# A stone struck by a takeout goes from still to several metres a second between
# one frame and the next, which snaps its track: the gate is scaled by the
# track's own speed, and that is zero while it sits there. The fragment then
# looks like a stone entering from up-sheet -- and a *guard* sits up-sheet, so a
# struck guard mimics a delivery exactly. These link a fragment back to the
# track it continues, which restores the fact that it was already at rest.
# Linking also has to bridge the gap where a stone *settles*. Players converge
# on the house as the stone stops, so the moment that decides where it ended is
# the one most likely to be hidden -- measured at three seconds in end 1, after
# which the resting stone appeared as a separate track and the delivery was
# discarded as having vanished. The allowance scales with how fast the stone was
# going, and only ever reaches forward: a stone carries on the way it was going.
LINK_MAX_GAP_S = 4.0
LINK_BASE_DIST_M = 0.5
LINK_MAX_DIST_M = 2.5
# How far a track may be linked *backwards* against its direction of travel.
LINK_BACKWARD_TOLERANCE_M = 0.2
# Across a very short gap the stone may have been struck, going from still to
# several metres a second between frames, so the allowance cannot be scaled by
# the speed it *had*. Over longer gaps it can, which keeps the reach tight.
LINK_ACCEL_GAP_S = 0.5
# It must settle: this much movement per second or less, held this long.
REST_SPEED_M_S = 0.10
REST_HOLD_S = 1.0
# Requiring the *delivered* stone to be seen coming to rest is wrong twice over,
# and both cases are common. A collision breaks it: the shooter stops dead while
# the stone it struck carries on the way the shot was going, and since a stone
# that stops abruptly looks exactly like a track dying, the tracker follows the
# struck one. And a takeout whose shooter rolls out never rests at all.
#
# What both leave behind is a house that looks different -- a stone added,
# removed or shifted. A sweeper running alongside the delivery, which is the
# false positive persistence was there to reject, moves nothing at all. So the
# house itself is the second, independent way to confirm a shot.
CHANGE_WINDOW_S = 6.0
# Kept clear of the delivery so neither window catches the stones in flight.
CHANGE_GUARD_S = 1.5
# A stone the tracker lost while it was still moving has not stopped yet, so
# the house cannot show it until it has. How long that takes follows from how
# fast it was going: a stone slows at roughly 0.08 m/s^2 -- a draw crosses the
# far hog line at about 2 m/s and comes to rest some 27 m on, 25 s later --
# and it slows less than that only when it is nearly stopped already. So a
# stone lost at 0.6 m/s needs up to another 7.5 s. Game 3 end 6 of the 5U
# championship lost its opening red exactly so: tracked to +0.63 m at 0.6
# m/s, hidden for 5.7 s as the players closed over it, sitting 2.3 m further
# on when they moved off -- and seen for only 30% of a six-second window that
# opened while it was still rolling, which is short of the 40% that counts as
# settled. The run-out is capped at the least time between two deliveries, so
# a stone that left play can never be read as resting where the next one lands.
STONE_DECEL_M_S2 = 0.08
MAX_RUNOUT_S = 10.0
# Two stones cannot be closer than a diameter, so anything further apart than
# that is a different stone rather than the same one re-measured.
CHANGE_TOLERANCE_M = 2.0 * C.STONE_RADIUS_M
# Present in this share of a window's frames to count as settled there, which
# allows for players walking over it.
CHANGE_MIN_PRESENCE = 0.4
# Too short a window is no evidence either way, and must not be read as "the
# house did not change".
CHANGE_MIN_FRAMES = 5
# How close the shot must have passed to a stone to be answerable for it --
# either as the stone it struck or as its own resting place, which the stone
# necessarily passed through before the tracker lost hold of it. A hair over
# two diameters, since the collision itself falls between frames.
#
# Without this, a changed house vouches for every track of that colour anywhere
# in the window, and the sweeper running alongside in their own team's jacket is
# exactly such a track. Measured: it took end 1 from 9 deliveries to 16 and end
# 2 to eleven yellows, which is three more than a team is given.
CONTACT_M = 0.8
# The side lines lie *outside* the overhead panels: they span roughly
# x = -1.9 to +1.9 m against a side line at 2.233 m. Asking whether the last
# sighting was beyond the side line is therefore a question that can never be
# answered yes, and every shooter that rolled out was discarded as having
# vanished mid-sheet. What can be judged is whether the stone had been running
# outward long enough to be nearly out: a stone rolls out by drifting steadily
# across the sheet for the whole latter part of its run, so by the time it is
# lost it has already covered more ground sideways than it has left to cover.
#
# Extrapolating a velocity forward instead does not work, and was measured
# failing: a couple of seconds of a noisy end-of-track estimate reaches metres,
# so a blob that jittered sideways in its last half second was credited with
# leaving. One such was accepted in end 1 having drifted 0.27 m outward with
# 1.45 m still to go.
#
# The back line needs none of this and must not have it. It falls just inside
# the panel, so a stone going out the back is genuinely seen crossing it.
# Long enough to average out one noisy frame, short enough to be the speed the
# stone had *there* rather than the speed it ended the track with.
VELOCITY_SPAN_S = 0.5
# Two shots this far apart may legitimately account for the same spot in the
# house: the first is cleared out and the second draws to where it had been.
# Closer than this they are rivals for one piece of evidence -- deliveries in
# the reference game are never less than about fifteen seconds apart.
CHANGE_REUSE_S = 30.0


@dataclass(frozen=True)
class Delivery:
    """One thrown stone: when it arrived, and where it stopped."""

    color: str
    t_enter: float
    t_rest: float
    entry_y_m: float
    rest_x_m: float
    rest_y_m: float
    travel_m: float
    came_to_rest: bool = True
    # What made this a delivery rather than something walking past. Worth
    # keeping: the four routes are not equally strong, and a reader asking why
    # a shot is believed should not have to re-derive it.
    #   rest          the stone itself was seen settling and stayed put
    #   house-add     a stone of its colour appeared that was not there before
    #   house-remove  a stone it passed close enough to strike is now gone
    #   left-view     it was still running when it left the panel
    #   late-entry    first seen already inside the house, confirmed by the sheet
    #   house-appear  arrived already stopped; a guard thrown short
    #   gap-search    recovered by a second pass through a gap in the sequence
    reason: str = "rest"
    # The flight itself, as (t, x, y) from where the stone came into view to
    # where it stopped. Kept because a reader wants to see the shot, not just
    # its endpoints, and because how fast it was moving is the only evidence
    # separating a takeout from a draw that happened to land on a stone.
    track: tuple[tuple[float, float, float], ...] = ()

    @property
    def duration_s(self) -> float:
        return self.t_rest - self.t_enter

    def speed_at(self, t_offset_s: float = VELOCITY_SPAN_S) -> float:
        """How fast the stone was moving ``t_offset_s`` into its flight.

        Measured at the top of the panel rather than averaged over the whole
        run, because a stone decelerates the entire way down: the average tells
        you where it stopped, which we already know, while the entry speed is
        what distinguishes the weight it was thrown at.
        """
        if len(self.track) < 2:
            return 0.0
        t0 = self.track[0][0]
        last = self.track[0]
        for sample in self.track[1:]:
            last = sample
            if sample[0] - t0 >= t_offset_s:
                break
        dt = last[0] - t0
        if dt <= 0:
            return 0.0
        dx = last[1] - self.track[0][1]
        dy = last[2] - self.track[0][2]
        return (dx * dx + dy * dy) ** 0.5 / dt


class _Track:
    """One stone followed through motion, with a constant-velocity guess."""

    __slots__ = ("color", "ts", "xs", "ys", "vx", "vy")

    def __init__(self, color, t, x, y):
        self.color = color
        self.ts, self.xs, self.ys = [t], [x], [y]
        self.vx = self.vy = 0.0

    def predict(self, t):
        dt = t - self.ts[-1]
        return self.xs[-1] + self.vx * dt, self.ys[-1] + self.vy * dt

    def add(self, t, x, y):
        dt = t - self.ts[-1]
        if dt > 0:
            # Smooth the velocity so one noisy frame cannot fling the gate open.
            self.vx = 0.5 * self.vx + 0.5 * (x - self.xs[-1]) / dt
            self.vy = 0.5 * self.vy + 0.5 * (y - self.ys[-1]) / dt
        self.ts.append(t)
        self.xs.append(x)
        self.ys.append(y)


def _path(track, end_i: int) -> tuple:
    """The flight as (t, x, y) samples, from first sight to ``end_i``.

    A plain tuple rather than the live ``_Track`` so a Delivery stays frozen
    and trivially serialisable; nothing downstream wants the velocity state.
    """
    return tuple(
        zip(track.ts[: end_i + 1], track.xs[: end_i + 1], track.ys[: end_i + 1])
    )


def _tail(track, i: int):
    """The same track from sample ``i`` on, with its velocity re-derived."""
    if i <= 0:
        return track
    out = _Track(track.color, track.ts[i], track.xs[i], track.ys[i])
    for t, x, y in zip(track.ts[i + 1:], track.xs[i + 1:], track.ys[i + 1:]):
        out.add(t, x, y)
    return out


def _head(track, i: int):
    """The same track up to, and not including, sample ``i``."""
    out = _Track(track.color, track.ts[0], track.xs[0], track.ys[0])
    for t, x, y in zip(track.ts[1:i], track.xs[1:i], track.ys[1:i]):
        out.add(t, x, y)
    return out


def _candidate_tracks(frames):
    """Every track, split where one stone's history was handed to another.

    A track that starts where a stone is still sitting borrowed that stone's
    history; its own motion began where the two parted. The borrowed part is
    not thrown away: it is the parked stone, and if that stone arrived within
    the footage its arrival is a delivery in its own right. Game 3 end 4 of
    the 5U championship lost its opening guard this way. The guard parked at
    (+0.36, +4.22) for four minutes; a later red passed 0.3 m from it while
    the sweepers hid it, the tracker handed the guard's track to the passing
    stone, and the trim that gave the flight back to the shooter discarded the
    guard's arrival with the history it cut. Every shot in the end was then
    credited to the wrong thrower.
    """
    for track in _build_tracks(frames):
        i0 = _trim_borrowed_start(frames, track)
        if i0 > 0:
            yield _head(track, i0)
        tail = _tail(track, i0)
        i1 = _trim_borrowed_end(frames, tail)
        yield tail if i1 >= len(tail.ts) else _head(tail, i1)


def _build_tracks(frames):
    tracks: list[_Track] = []
    live: list[_Track] = []
    for t, dets in frames:
        used = set()
        for track in list(live):
            dt = t - track.ts[-1]
            if dt > TRACK_TIMEOUT_S:
                live.remove(track)
                continue
            px, py = track.predict(t)
            if len(track.ts) < 2:
                gate = MATCH_BASE_M + BOOTSTRAP_SPEED_M_S * dt
            else:
                speed = (track.vx**2 + track.vy**2) ** 0.5
                gate = min(MATCH_BASE_M + MATCH_SPEED_SLACK * speed * dt, MATCH_MAX_M)
            best, best_d = None, gate
            for i, d in enumerate(dets):
                if i in used or d.color != track.color:
                    continue
                dist = ((d.x_m - px) ** 2 + (d.y_m - py) ** 2) ** 0.5
                if dist < best_d:
                    best, best_d = i, dist
            if best is not None:
                used.add(best)
                track.add(t, dets[best].x_m, dets[best].y_m)
        for i, d in enumerate(dets):
            if i in used:
                continue
            track = _Track(d.color, t, d.x_m, d.y_m)
            tracks.append(track)
            live.append(track)
    return _link_continuations(tracks)


def _link_continuations(tracks):
    """Join a track to the one it continues after a break.

    A stone hit hard enough leaves the gate of its own resting track, so it
    reappears as a fresh track already in flight. Rejoining them means the
    combined track still shows the stone at rest at its start, which is what
    distinguishes a struck stone from a thrown one.

    Single forward pass. Tracks are visited in start order and each is offered
    to the most recent compatible tail, so a stone that fragments many times
    chains in linear time -- rescanning from the top after every merge turned
    this into a cubic-time trap on real footage.
    """
    ordered = sorted(tracks, key=lambda t: t.ts[0])
    chains: list[_Track] = []
    for track in ordered:
        best, best_dist = None, None
        for chain in chains:
            if chain.color != track.color:
                continue
            gap = track.ts[0] - chain.ts[-1]
            if not (0 < gap <= LINK_MAX_GAP_S):
                continue
            speed = (chain.vx**2 + chain.vy**2) ** 0.5
            if gap <= LINK_ACCEL_GAP_S:
                speed = max(speed, BOOTSTRAP_SPEED_M_S)
            allowance = min(LINK_BASE_DIST_M + speed * gap, LINK_MAX_DIST_M)
            dist = (
                (track.xs[0] - chain.xs[-1]) ** 2
                + (track.ys[0] - chain.ys[-1]) ** 2
            ) ** 0.5
            if dist > allowance:
                continue
            # A stone continues down-sheet; it never comes back up. Without
            # this, a long allowance would happily annex a stone sitting
            # behind the one that was moving.
            if chain.vy < -1e-6 and track.ys[0] > chain.ys[-1] + LINK_BACKWARD_TOLERANCE_M:
                continue
            if best_dist is None or dist < best_dist:
                best, best_dist = chain, dist
        if best is None:
            chains.append(track)
            continue
        for k in range(len(track.ts)):
            best.add(track.ts[k], track.xs[k], track.ys[k])
    return chains


# A stone cannot be in two places, so if one of this colour is still sitting
# where a track began after the track has moved away, the track's start belongs
# to a different stone and its motion started later.
#
# This happens where the stones waiting to be thrown are parked, because a
# delivery passes within a stone's width of them on its way in. Frame to frame
# the association is genuinely ambiguous; over the end it is not. In game 1 end
# 6 a yellow hit that entered at 5075 was recorded as entering at 4995.6 -- an
# eighty-second error, taken from a parked stone the track had picked up first.
# Sorted by entry time it then fell before the red at 4996.8, an impossible 1.2
# s pair, and the turn order broke badly enough that the rules dropped a real
# red 150 s later.
TRIM_SETTLE_S = 3.0


def _trim_borrowed_start(frames, track):
    """Drop a leading segment that belongs to a stone still sitting there.

    Returns the index the track's own motion starts from.
    """
    x0, y0 = track.xs[0], track.ys[0]
    # Where did this track leave that position for good?
    left = None
    for i in range(len(track.ts)):
        d = ((track.xs[i] - x0) ** 2 + (track.ys[i] - y0) ** 2) ** 0.5
        if d > CHANGE_TOLERANCE_M:
            left = i
            break
    if left is None or left == 0:
        return 0
    # If a stone of this colour is still there afterwards, it never left, so
    # everything up to the departure was that stone and not this one.
    t_after = track.ts[-1] + TRIM_SETTLE_S
    if not _still_there(frames, track.color, x0, y0, t_after, persist_s=0.0):
        return 0
    return left


def _trim_borrowed_end(frames, track) -> int:
    """Drop a trailing segment that belongs to a stone already sitting there.

    Returns the index the track's own account ends at (exclusive). The mirror
    of `_trim_borrowed_start`: a track that finishes on a spot where a stone
    of its colour was sitting *before it got there* did not come to rest, it
    was handed that stone. The case is a shooter passing a parked stone while
    the sweepers hide both. The parked stone's track takes the shooter over,
    which orphans the shooter's first few sightings, and when the parked stone
    shows again a second later it is the one detection inside that orphan's
    widened gate -- so the orphan "comes to rest" exactly where a stone had
    been all along, and reads as a short delivery with the strongest evidence
    there is.
    """
    n = len(track.ts)
    x1, y1 = track.xs[-1], track.ys[-1]
    arrived = n - 1
    while arrived > 0 and (
        (track.xs[arrived - 1] - x1) ** 2 + (track.ys[arrived - 1] - y1) ** 2
    ) ** 0.5 <= CHANGE_TOLERANCE_M:
        arrived -= 1
    if arrived == 0 or track.ts[-1] - track.ts[arrived] < REST_HOLD_S:
        return n  # never moved, or never settled: nothing was borrowed
    if _place_was_empty(frames, track.color, x1, y1, track.ts[arrived],
                        unknown_is_empty=True):
        return n
    return arrived


def _rest_index(track):
    """First index from which the stone stays put for REST_HOLD_S.

    "Stays put for a second" has to be witnessed by a sighting at least a
    second later, so where the track has one it closes the window. Judged on
    the samples inside the second alone, a stone that crept 0.05 m in 0.7 s
    and then, after half a second under the sweepers, showed up 0.2 m further
    on was called at rest from its first sighting -- game 3 end 4's yellow
    guard, first seen with its box clipped against the top of the panel. A
    stone at rest is exactly where it was after the gap; a moving one is not.
    """
    n = len(track.ts)
    for i in range(n):
        j = i
        while j + 1 < n and track.ts[j + 1] - track.ts[i] < REST_HOLD_S:
            j += 1
        if j + 1 < n:
            j += 1  # the first sample REST_HOLD_S or more after i
        elif track.ts[j] - track.ts[i] < REST_HOLD_S * 0.5:
            # The tail of the track, too short to judge. Skip it rather than
            # give up: the stone may still be seen settling later.
            continue
        span = max(
            ((track.xs[k] - track.xs[i]) ** 2 + (track.ys[k] - track.ys[i]) ** 2) ** 0.5
            for k in range(i, j + 1)
        )
        if span <= REST_SPEED_M_S * REST_HOLD_S:
            return i
    return None


def _still_there(frames, color, x, y, t_rest,
                 persist_s: float = PERSIST_S) -> bool:
    """Whether a stone of this colour is still at this spot a moment later.

    A delivered stone stays put; a broom head, a sweeper's glove or a
    mis-detection does not. Judged over a window rather than at one instant,
    because any single frame can be occluded by a player walking past.
    """
    target = t_rest + persist_s
    lo, hi = target - PERSIST_WINDOW_S, target + PERSIST_WINDOW_S
    window = [(t, d) for t, d in frames if lo <= t <= hi]

    # A window of one or two frames passes any presence test trivially, so it
    # is no evidence at all. Fall back to whatever can be seen after the stone
    # settled, and refuse to judge if even that is too short to tell a stone
    # from a broom head.
    if not window or window[-1][0] - window[0][0] < 1.5:
        tail = [(t, d) for t, d in frames if t >= t_rest + 1.0]
        if not tail or tail[-1][0] - t_rest < MIN_CONFIRM_S:
            return False
        window = tail

    hits = 0
    for _, dets in window:
        for d in dets:
            if d.color != color:
                continue
            if ((d.x_m - x) ** 2 + (d.y_m - y) ** 2) ** 0.5 <= PERSIST_TOLERANCE_M:
                hits += 1
                break
    # Present for a good share of the window, allowing for people walking over it.
    return hits >= 0.4 * len(window)


def _settled_stones(frames, t0: float, t1: float):
    """Stones sitting in one place through most of [t0, t1].

    Anything moving through the window smears across several positions and so
    holds none of them for long enough to be reported, which is what keeps
    stones in flight out of the before/after comparison.
    """
    window = [(t, d) for t, d in frames if t0 <= t <= t1]
    if len(window) < CHANGE_MIN_FRAMES:
        return None  # not enough to tell; no evidence either way

    spots: list[list] = []  # [color, x, y, frames_seen]
    for _, dets in window:
        claimed = set()
        for d in dets:
            for i, (color, x, y, _n) in enumerate(spots):
                if color != d.color or i in claimed:
                    continue
                if ((x - d.x_m) ** 2 + (y - d.y_m) ** 2) ** 0.5 <= CHANGE_TOLERANCE_M:
                    spots[i][3] += 1
                    claimed.add(i)
                    break
            else:
                spots.append([d.color, d.x_m, d.y_m, 1])
                claimed.add(len(spots) - 1)
    need = CHANGE_MIN_PRESENCE * len(window)
    return [(c, x, y) for c, x, y, n in spots if n >= need]


def _house_delta(before, after):
    """Which stones appeared and which went away between two settled sets."""
    left = list(before)
    added = []
    for color, x, y in after:
        for i, (bc, bx, by) in enumerate(left):
            if bc == color and ((bx - x) ** 2 + (by - y) ** 2) ** 0.5 <= CHANGE_TOLERANCE_M:
                del left[i]
                break
        else:
            added.append((color, x, y))
    return added, left


def _approach(track, x, y, upto_i) -> float:
    """How close the shot came to a point, up to where it ended."""
    return min(
        ((track.xs[k] - x) ** 2 + (track.ys[k] - y) ** 2) ** 0.5
        for k in range(upto_i + 1)
    )


def _runout_s(track, end_i) -> float:
    """How long a stone lost at sample ``end_i`` may still have been moving."""
    vx, vy = _velocity_at(track, end_i)
    return min((vx * vx + vy * vy) ** 0.5 / STONE_DECEL_M_S2, MAX_RUNOUT_S)


def _house_change(frames, track, end_i, in_motion: bool = False):
    """The house change this shot is answerable for, if any.

    Returns ``(rest, claim)``. ``rest`` is the resting place the change names,
    when it names one. ``claim`` identifies the stone that changed, as
    ``(kind, colour, x, y, approach)`` -- callers use it to make sure one
    change confirms one delivery and not every track that was passing.

    ``in_motion`` says the track ended before the stone stopped, so the
    after-window is held open for as long as the stone could still have been
    running (see ``STONE_DECEL_M_S2``).
    """
    t_in, t_out = track.ts[0], track.ts[end_i]
    before = _settled_stones(frames, t_in - CHANGE_GUARD_S - CHANGE_WINDOW_S,
                             t_in - CHANGE_GUARD_S)
    runout = _runout_s(track, end_i) if in_motion else 0.0
    after = _settled_stones(frames, t_out + CHANGE_GUARD_S,
                            t_out + CHANGE_GUARD_S + CHANGE_WINDOW_S + runout)
    if before is None or after is None:
        return None, None
    added, removed = _house_delta(before, after)


    # A stone of this shot's own colour that was not there before is the stone
    # it threw, wherever along the way the tracker lost hold of it.
    #
    # Deliberately not gated on how close the track passed to it. After a
    # collision the track *is* the struck stone's path -- that is the whole
    # failure being repaired -- so the shooter's resting place lies off it by
    # however far the shooter carried, measured at 1.18 m in end 1. Closeness
    # still decides which rival track is answerable for the stone, below.
    mine = [
        (x, y, _approach(track, x, y, end_i))
        for c, x, y in added if c == track.color
    ]
    if mine:
        x, y, near = min(mine, key=lambda m: m[2])
        return (x, y), ("add", track.color, x, y, near)

    # Nothing of ours settled, so the shooter left the playing area. It is
    # still a shot if something it passed close enough to strike is now gone.
    # Here contact is not optional: nothing moves a stone at a distance.
    hit = [
        (c, x, y, _approach(track, x, y, end_i)) for c, x, y in removed
    ]
    hit = [h for h in hit if h[3] <= CONTACT_M]
    if hit:
        c, x, y, near = min(hit, key=lambda h: h[3])
        return None, ("rm", c, x, y, near)
    return None, None


def _one_per_change(claimed):
    """Keep a single delivery per house change.

    A shot moves one stone, so a stone that appeared or vanished is evidence
    for exactly one delivery. Several tracks can reach for the same evidence --
    the stone itself and the sweepers who followed it down -- and whichever
    passed closest is the one that touched it.
    """
    out = []
    for _kind, _color, x, y, near, delivery in sorted(
        claimed, key=lambda c: (c[4], -c[5].travel_m)
    ):
        for kind2, color2, x2, y2, _n2, kept in out:
            if (kind2, color2) != (_kind, _color):
                continue
            if ((x2 - x) ** 2 + (y2 - y) ** 2) ** 0.5 > CHANGE_TOLERANCE_M:
                continue
            # Same stone, but far enough apart in time to be a later shot
            # drawing to a place an earlier one had been cleared out of.
            if abs(kept.t_enter - delivery.t_enter) >= CHANGE_REUSE_S:
                continue
            break
        else:
            out.append((_kind, _color, x, y, near, delivery))
    return [c[5] for c in out]


def _velocity_at(track, i):
    """How fast the stone was going at sample ``i``.

    Not ``track.vx``/``track.vy``: those are the running estimate left over
    from the *end* of the track. A stone swept along, pausing mid-sheet and
    then carrying on out of play would otherwise be credited at its pause with
    the speed it eventually left at.
    """
    j = i
    while j > 0 and track.ts[i] - track.ts[j - 1] < VELOCITY_SPAN_S:
        j -= 1
    dt = track.ts[i] - track.ts[j]
    if dt <= 0:
        return 0.0, 0.0
    return (track.xs[i] - track.xs[j]) / dt, (track.ys[i] - track.ys[j]) / dt


# How far back to look for the resting place being empty. The six-second
# window is not enough on its own: players stand at the delivery end, right
# over the stones waiting to be thrown, so a parked stone drops out of a short
# window and its reappearance reads as an arrival. One sat at (+0.32, +4.43)
# for 94% of game 1 end 6 and was accepted as a delivery three times. Over half
# a minute a parked stone is seen in most frames however often it is walked
# past, while a place no stone has reached yet stays empty throughout.
EMPTY_LOOKBACK_S = 30.0
# The furthest back any test reaches. Detection has to be given this much
# run-up before an end's first shot or the tests fail for want of history
# rather than on the evidence: game 1 end 2's opening yellow arrives two
# seconds into the end, and whether it was accepted came down to a five-second
# difference in where the segment boundary fell.
REQUIRED_LOOKBACK_S = EMPTY_LOOKBACK_S + CHANGE_WINDOW_S
# It may be hidden this often within the look-back and still count as occupied.
EMPTY_MAX_PRESENCE = 0.2


def _place_was_empty(frames, color, x, y, t0, unknown_is_empty=False) -> bool:
    """Whether no stone of this colour had been sitting here beforehand.

    With too little footage before ``t0`` to tell, the answer is whatever the
    caller can afford to be wrong about: a delivery must not be claimed on no
    evidence, while a track link is refused only on positive evidence.
    """
    lo, hi = t0 - EMPTY_LOOKBACK_S, t0 - CHANGE_GUARD_S
    window = [(t, d) for t, d in frames if lo <= t <= hi]
    if len(window) < CHANGE_MIN_FRAMES:
        return unknown_is_empty  # nothing to go on
    hits = sum(
        1 for _t, dets in window
        if any(d.color == color
               and ((d.x_m - x) ** 2 + (d.y_m - y) ** 2) ** 0.5
               <= CHANGE_TOLERANCE_M
               for d in dets)
    )
    return hits <= EMPTY_MAX_PRESENCE * len(window)


def _appeared_without_replacing(frames, track) -> bool:
    """Whether this colour gained a stone where the track begins.

    The point of asking about the *count* rather than the position is staging.
    Between ends the players push stones back toward the end they will throw
    from, and a stone shoved from one place to another is an arrival at the new
    spot -- but it is also a departure from the old one, so the colour's count
    is unchanged. A stone that was delivered has no matching departure.
    """
    t0 = track.ts[0]
    before = _settled_stones(frames, t0 - CHANGE_GUARD_S - CHANGE_WINDOW_S,
                             t0 - CHANGE_GUARD_S)
    after = _settled_stones(frames, t0 + CHANGE_GUARD_S,
                            t0 + CHANGE_GUARD_S + CHANGE_WINDOW_S)
    if before is None or after is None:
        return False
    was = sum(1 for c, _x, _y in before if c == track.color)
    now = sum(1 for c, _x, _y in after if c == track.color)
    return now > was


def _arrived_at_rest(frames, track, at_i: int = 0):
    """A delivery whose flight was all but over before it came into view.

    Returns where it rests, or None.

    A guard thrown short stops just inside the top of the panel -- one measured
    in game 2 end 3 came to rest at y = +4.08 against a panel edge at +4.60 --
    so there is no traverse to follow and nothing the tracker can recognise.
    It was still thrown, and the only trace it leaves is that the sheet holds
    one more stone of its colour than it did a moment earlier.

    Persistence is judged where the stone *ends up*, which is not where it was
    first seen. A red guard in game 1 end 5 entered at y = +3.95 already almost
    stopped and drifted 0.13 m to +3.82 -- too much movement to look like a
    stone already in play, far too little to pass the travel gate. Checking the
    entry position instead looks for the stone 0.29 m from where it actually
    sits, which is outside the tolerance, so it fails on a stone that stayed in
    view for the next three minutes.

    This is the weakest of the routes and is deliberately the last one tried:
    it is the same evidence a stone pushed back between ends would offer, minus
    the flight that would settle the question.
    """
    if track.ys[0] < MIN_ENTRY_Y_M:
        return None  # a stone cannot arrive from anywhere but up-sheet
    return _arrival_rest(frames, track, at_i)


def _stone_gained_near(frames, color, x, y, t0, t_settled):
    """A settled stone of ``color`` near ``(x, y)`` that is new since ``t0``.

    A guard frozen against a stone of its own colour stops a diameter from it,
    which is exactly the tolerance `_place_was_empty` uses for "the same spot",
    so that test can never pass a freeze -- and the detector often boxes two
    touching stones as one while the arrival settles, drifting the track onto
    the parked stone. Game 3 end 7's ninth rock was lost this way, and the
    rules then dropped the real eighth as one red too many. What the spot did
    gain is a second stone: one more of the colour within a stone's reach after
    than before. Returns where it sits, or None.
    """
    reach = 2.0 * CHANGE_TOLERANCE_M
    before = _settled_stones(frames, t0 - EMPTY_LOOKBACK_S, t0 - CHANGE_GUARD_S)
    after = _settled_stones(frames, t_settled + CHANGE_GUARD_S,
                            t_settled + CHANGE_GUARD_S + CHANGE_WINDOW_S)
    if before is None or after is None:
        return None
    near = lambda stones: [(sx, sy) for c, sx, sy in stones
                           if c == color and ((sx - x) ** 2 + (sy - y) ** 2) ** 0.5 <= reach]
    was, now = near(before), near(after)
    if len(now) <= len(was):
        return None
    # Pair each stone that was there with the after-stone nearest it; the
    # newcomer is whatever is left over.
    left = list(now)
    for bx, by in was:
        if left:
            left.remove(min(left, key=lambda p: (p[0] - bx) ** 2 + (p[1] - by) ** 2))
    return left[0] if left else None


def _arrival_rest(frames, track, at_i: int = 0):
    """Where this track's stone came to rest, if the sheet did not have it before.

    Three things together, and none of them alone: it is still there
    afterwards, the spot did not already hold it -- empty, or holding one
    fewer stone of the colour than it does now -- and the colour's count went
    up rather than a stone merely moving from one place to another. Only a
    delivery adds a stone. Returns the resting place, or None.
    """
    settled = _settled_index(track, at_i)
    x, y, t = track.xs[settled], track.ys[settled], track.ts[settled]
    if not _still_there(frames, track.color, x, y, t):
        return None
    rest = (x, y)
    if not _place_was_empty(frames, track.color, x, y, track.ts[0]):
        rest = _stone_gained_near(frames, track.color, x, y, track.ts[0], t)
        if rest is None:
            return None
    return rest if _appeared_without_replacing(frames, track) else None


def _is_new_stone(frames, track, at_i: int = 0) -> bool:
    return _arrival_rest(frames, track, at_i) is not None


def _settled_index(track, from_i: int = 0) -> int:
    """Where the stone has come to rest, a moment after ``from_i``."""
    limit = track.ts[from_i] + PERSIST_S
    j = from_i
    while j + 1 < len(track.ts) and track.ts[j + 1] <= limit:
        j += 1
    return j


def _left_the_view(track, end_i, x_limit_m: float,
                   y_min_m: float = C.THROUGH_BACK_Y_M) -> bool:
    """Whether the stone was on its way out of sight and not coming back.

    ``y_min_m`` is where the view ends down-sheet. On the club's top camera
    that is the back line itself, and a box clipped by the image edge never
    reports a centre beyond it -- game 3 end 6's last yellow was seen at
    -1.94 m doing 0.75 m/s, three centimetres short of "crossed", and then
    not at all. A stone last seen within its own radius of that edge, still
    running fast enough to have been past the back line before the tracker
    gave up, has left. Both halves matter: a sweeper's blob vanishing mid-house
    at a run is not at the edge, and a stone dying against the edge is not
    running.
    """
    y1 = track.ys[end_i]
    if y1 <= C.THROUGH_BACK_Y_M:
        return True  # seen crossing the back line, which is inside the panel
    vx, vy = _velocity_at(track, end_i)
    at_edge = y1 <= y_min_m + C.STONE_RADIUS_M
    if at_edge and vy < 0 and y1 + vy * TRACK_TIMEOUT_S <= C.THROUGH_BACK_Y_M:
        return True  # running out the back of the view
    x0, x1 = track.xs[0], track.xs[end_i]
    if abs(x1) >= x_limit_m:
        return True
    if abs(vx) < 1e-6 or (vx > 0) != (x1 > 0):
        return False  # not heading out
    return abs(x1) - abs(x0) >= x_limit_m - abs(x1)


def find_deliveries(frames, min_travel_m: float = MIN_TRAVEL_M,
                    view_x_limit_m: float | None = None,
                    view_y_min_m: float | None = None) -> list[Delivery]:
    """Find every thrown stone in a dense sequence of detections.

    ``view_x_limit_m`` and ``view_y_min_m`` say where the camera's view ends,
    so a stone that ran out of the picture can be told from one that vanished
    in play. Absent, the view is taken to end at the side and back lines.
    """
    frames = [(t, list(d)) for t, d in frames]
    if not frames:
        return []
    x_limit = min(C.SIDELINE_ABS_X_M, view_x_limit_m or C.SIDELINE_ABS_X_M)
    y_min = C.THROUGH_BACK_Y_M if view_y_min_m is None else view_y_min_m

    out: list[Delivery] = []
    claimed: list[tuple] = []
    for track in _candidate_tracks(frames):
        if len(track.ts) < 4:
            continue
        late_entry = False
        if track.ys[0] < MIN_ENTRY_Y_M:
            # Normally this is a stone that was struck, not thrown. But the
            # model finds red much further down the sheet than yellow -- in
            # game 1 end 3 a red takeout was first seen at y = +1.69, below
            # this gate, while the sweepers alongside it were picked up at
            # +4.15 -- so the gate cuts real shots of one colour only.
            #
            # A struck stone moves; it does not add one. If the sheet holds a
            # stone of this colour that it did not hold before, in a place
            # nothing of that colour was sitting, then one was delivered.
            if track.ys[0] < LATE_ENTRY_MIN_Y_M:
                continue
            if not _is_new_stone(frames, track, _rest_index(track) or 0):
                continue
            late_entry = True
        rest_i = _rest_index(track)
        if rest_i == 0:
            # Sitting still from the moment we saw it, so there is no flight to
            # judge. Usually that means it was already in play -- but a guard
            # thrown short comes to rest just inside the panel and looks
            # exactly the same, so ask whether the sheet gained a stone.
            rest = _arrived_at_rest(frames, track)
            if rest is not None:
                j = _settled_index(track)
                out.append(
                    Delivery(
                        color=track.color,
                        t_enter=track.ts[0],
                        t_rest=track.ts[j],
                        entry_y_m=track.ys[0],
                        rest_x_m=rest[0],
                        rest_y_m=rest[1],
                        travel_m=track.ys[0] - rest[1],
                        came_to_rest=True,
                        reason="house-appear",
                        track=_path(track, j),
                    )
                )
            continue
        at_rest = rest_i is not None
        # Where the shot ended as far as the tracker could follow it.
        end_i = rest_i if at_rest else len(track.ts) - 1

        # A thrown stone runs one way. A wandering blob covers as much path but
        # ends up where it started, so its net travel is a small share of it.
        travelled = track.ys[0] - track.ys[end_i]
        path = sum(abs(track.ys[k + 1] - track.ys[k]) for k in range(end_i))
        if travelled < min_travel_m:
            # Too little movement to judge as a flight -- but a guard that was
            # already almost stopped when it came into view moves about this
            # much, so ask the sheet whether it gained a stone before giving up.
            rest = _arrived_at_rest(frames, track, end_i) if at_rest else None
            if rest is not None:
                j = _settled_index(track, end_i)
                out.append(
                    Delivery(
                        color=track.color,
                        t_enter=track.ts[0],
                        t_rest=track.ts[j],
                        entry_y_m=track.ys[0],
                        rest_x_m=rest[0],
                        rest_y_m=rest[1],
                        travel_m=track.ys[0] - rest[1],
                        came_to_rest=True,
                        reason="house-appear",
                        track=_path(track, j),
                    )
                )
            continue
        if path <= 0 or travelled / path < MIN_NET_TRAVEL_FRACTION:
            continue
        if abs(track.xs[end_i] - track.xs[0]) > MAX_LATERAL_RATIO * travelled:
            continue

        # Every stone the players leave in play counts, however short the shot,
        # but it has to still be there once the players have cleared off.
        # Distance is no substitute: a sweeper's jacket crosses just as much
        # sheet as the stone it is following.
        rest_x, rest_y = track.xs[end_i], track.ys[end_i]
        settled = at_rest and _still_there(
            frames, track.color, rest_x, rest_y, track.ts[end_i],
        )
        claim, reason = None, ("late-entry" if late_entry else "rest")
        if not settled:
            # Either the tracker came out of a collision holding the wrong
            # stone, or the shooter left the playing area. Ask the house, which
            # a shot changes and a sweeper does not.
            rest, claim = _house_change(frames, track, end_i, in_motion=not at_rest)
            if rest is not None:
                rest_x, rest_y = rest
                settled = True
                reason = "house-add"
            elif claim is not None:
                reason = "house-remove"
            elif _left_the_view(track, end_i, x_limit, y_min):
                reason = "left-view"
            else:
                continue

        # However it was confirmed, a shot whose arrival was never seen is
        # weaker evidence than one watched the whole way down, and the record
        # has to say so. Letting the confirming route overwrite this gave a
        # track first seen behind the tee the same standing as a delivery
        # tracked from the top of the panel, and the fit then had no grounds to
        # choose between them.
        if late_entry:
            reason = "late-entry"
        found = Delivery(
            color=track.color,
            t_enter=track.ts[0],
            t_rest=track.ts[end_i],
            entry_y_m=track.ys[0],
            rest_x_m=rest_x,
            rest_y_m=rest_y,
            travel_m=travelled,
            came_to_rest=settled,
            reason=reason,
            track=_path(track, end_i),
        )
        if claim is None:
            out.append(found)
        else:
            claimed.append((*claim, found))

    out.extend(_one_per_change(claimed))
    return sorted(out, key=lambda d: d.t_enter)
