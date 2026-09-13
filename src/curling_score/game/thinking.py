"""Thinking time, as a timed game keeps it.

Each team's clock runs while it is deciding, and stops once the stone is on its
way. World Curling stops it when the delivered stone crosses the tee line at
the delivering end, and that happens to be the one moment of a delivery this
footage can be relied on to show: ``release.find_releases`` only accepts a
track that entered within ``ENTRY_MARGIN_M`` of a back edge near -2.0 m and
climbed ``MIN_TRAVEL_M`` = 3.0 m, so an accepted release has always crossed
y = 0 -- by 0.92 m in the worst case the gate allows.

The clock starts when the previous stone comes to rest, plus a grace period for
the players to clear the ice. That grace is not in the rulebook; it stands in
for the part of the interval nobody is thinking in, and without it every team
is charged for the walk down the sheet.

Two things are deliberately not guessed, and are counted instead:

* **an end's first stone**, because there is nothing to start its clock from.
  ``EndSegment.start_s`` opens only once a stone has *rested* in the house, so
  it is already after the first rock landed -- it cannot serve, and the break
  between ends is not thinking time anyway.
* **a previous shot that never rested**, including the placeholders inserted to
  keep alternation, which have no time at all.

A shot with no release used to be the third, and was by far the largest: on
game ``AEqLTgM25Tc`` it cost 26 of the 32 unmeasured intervals. It no longer
is, because the clock does not need a release -- see :func:`time_shots`.

A total assembled from an unknown fraction of an end is not a total, so
``measured_shots``, ``unmeasured_shots`` and ``estimated_shots`` travel with
it.
"""

import math
from dataclasses import dataclass, field

from curling_score.detect import delivery as D, release as R
from curling_score.game import rules, split

# After the previous stone stops, before the next team's clock starts. Long
# enough to clear the ice and no longer.
GRACE_S = 5.0

# The delivering end's tee line, in the throwing panel's own metres.
TEE_LINE_Y_M = 0.0

# Two questions, two sets of gates. ``release.find_releases`` decides whether a
# rock was thrown at all, and an unpaired release of its becomes a delivery
# candidate that can change the shot list -- so it is strict, and right to be.
# The clock asks something much weaker about a rock we have already watched
# arrive: not "was this a throw" but "when did this throw cross the tee line".
# Anchored to a shot that already exists, a track can be admitted on far less,
# and refusing it buys nothing.
#
# Measured on game AEqLTgM25Tc, where the strict rule timed 80 of 112 shots:
# these gates recover 17 more, and on all 80 the strict rule had already timed
# they pick the same track and the same crossing, to 0.00 s. The three
# thresholds are set where that game's refusals actually sat -- travel down to
# 2.16 m, speed to 0.82 m/s, entry to 1.08 m -- with a little room below each.
TRACK_MIN_TRAVEL_M = 1.5
TRACK_MIN_SPEED_M_S = 0.5
TRACK_ENTRY_MARGIN_M = 1.2
# A track that dies below the tee line is carried the rest of the way at the
# speed it was climbing. Two shots in that game ended 0.16 and 0.54 m short,
# which at 2.3 m/s is a tenth of a second against an interval of twenty.
TRACK_TEE_GAP_M = 1.0

# Where the throwing camera saw nothing at all, the clock is stopped this long
# before the rock arrived. An approximate interval beats none: a shot with no
# number does not merely go unreported, it silently shortens the team total
# that is reported.
#
# Not a guess. Across the five charted games, 503 throws were seen crossing
# the tee *and* arriving, and the lag between runs a median 16.3 s and a mean
# 15.9, p10 11.8 to p90 19.0 -- and the five game medians sit between 15.9 and
# 17.1, so it is a property of the sheet rather than of a team. Sixteen
# seconds is the round number between median and mean; nine intervals in ten
# are within five seconds of it. Every shot timed this way says so in
# ``Shot.tee_estimated``, and ``estimated_shots`` travels with every total.
ASSUMED_TEE_TO_ARRIVAL_S = 16.0


@dataclass(frozen=True)
class _Sighting:
    """A track climbing out of the thrower's house, judged only for its time."""

    color: str
    t_start: float
    t_tee: float
    entry_m: float   # how far above the back edge of the view it was first seen


def _sightings(frames, view_y_min_m: float) -> list[_Sighting]:
    """Every up-sheet track on the thrower's panel that reaches the tee line."""
    out: list[_Sighting] = []
    for track in D._build_tracks(frames):
        if len(track.ts) < 4:
            continue
        entry = track.ys[0] - view_y_min_m
        if entry > TRACK_ENTRY_MARGIN_M:
            continue  # started well up the panel: not something out of the hack
        up = track.ys[-1] - track.ys[0]
        duration = track.ts[-1] - track.ts[0]
        if up < TRACK_MIN_TRAVEL_M or duration <= 0:
            continue
        speed = up / duration
        if not TRACK_MIN_SPEED_M_S <= speed <= R.MAX_SPEED_M_S:
            continue
        t_tee = split.crossing_time(tuple(zip(track.ts, track.xs, track.ys)),
                                    TEE_LINE_Y_M)
        if t_tee is None:
            gap = TEE_LINE_Y_M - track.ys[-1]
            if not 0.0 < gap <= TRACK_TEE_GAP_M:
                continue  # lost too far below the line, or never below it
            t_tee = track.ts[-1] + gap / speed
        out.append(_Sighting(track.color, track.ts[0], t_tee, entry))
    return out


def time_shots(shots, frames, view_y_min_m: float) -> None:
    """Give every shot a tee crossing, in place.

    ``frames`` are the *thrower's* panel, the same ones
    ``release.find_and_pair`` reads. Shots that already carry a release are
    left alone -- that pairing is the better evidence, and the long split
    depends on it. For the rest, the one track of the right colour climbing
    out of the house in the lag window is taken as this rock on its way, and
    where there is no track at all the crossing is assumed from the arrival.

    This runs after the rules have settled the shot list, and can only attach a
    time to a rock already in it. Nothing here can add, drop or renumber a
    shot; that remains ``release.find_releases``' stricter business.
    """
    seen = _sightings(frames, view_y_min_m)
    for shot in shots:
        if getattr(shot, "release", None) is not None or shot.missing:
            continue
        delivery = getattr(shot, "delivery", None)
        if delivery is None:
            continue
        fits = [s for s in seen if s.color == shot.color
                and R.MIN_LAG_S <= delivery.t_enter - s.t_start <= R.MAX_LAG_S]
        if fits:
            # More than one is one delivery seen twice -- the rock and the
            # slider, or a track broken and remade a moment later. The one
            # that entered closest to the back edge came out of the hack.
            shot.tee_s = min(fits, key=lambda s: s.entry_m).t_tee
        else:
            shot.tee_s = delivery.t_enter - ASSUMED_TEE_TO_ARRIVAL_S
            shot.tee_estimated = True


@dataclass(frozen=True)
class Thinking:
    """What each team spent, and how much of the play it was read from."""

    by_color: dict
    measured_shots: int = 0
    unmeasured_shots: int = 0
    anomalies: int = 0
    # Of ``measured_shots``, how many rest on an assumed tee crossing rather
    # than a seen one.
    estimated_shots: int = 0
    # Per shot, in order: the seconds charged, or None where it could not be
    # read. Lets a viewer show the interval beside the shot it belongs to.
    per_shot: list = field(default_factory=list)

    @property
    def total_s(self) -> float:
        return sum(self.by_color.values())


def _rested(shot) -> float | None:
    """When this shot's stone stopped, or None if it never did."""
    if shot is None or getattr(shot, "missing", False):
        return None
    t = getattr(shot, "t_rest_s", None)
    if t is None:
        return None
    t = float(t)
    return None if math.isnan(t) else t


def tee_crossing(shot) -> float | None:
    """When this shot's stone crossed the delivering end's tee line.

    Read off the paired release where there is one, and otherwise off
    whatever :func:`time_shots` could establish.
    """
    r = getattr(shot, "release", None)
    if r is not None and getattr(r, "track", ()):
        return split.crossing_time(r.track, TEE_LINE_Y_M)
    return getattr(shot, "tee_s", None)


def for_end(shots) -> Thinking:
    """The clock across one end."""
    shots = list(shots)
    by_color = {c: 0.0 for c in rules.COLORS}
    per_shot: list = []
    measured = unmeasured = anomalies = estimated = 0

    for i, shot in enumerate(shots):
        started = _rested(shots[i - 1]) if i else None
        stopped = tee_crossing(shot) if i else None
        if started is None or stopped is None:
            per_shot.append(None)
            unmeasured += 1
            continue
        seconds = stopped - (started + GRACE_S)
        if seconds < 0:
            # The next stone was on its way before the last one had settled and
            # been cleared. That is a timing fault upstream, not a team that
            # thought for a negative time: charge nothing and say so.
            anomalies += 1
            seconds = 0.0
        color = getattr(shot, "color", None)
        if color in by_color:
            by_color[color] += seconds
        per_shot.append(seconds)
        measured += 1
        estimated += bool(getattr(shot, "tee_estimated", False))

    return Thinking(by_color=by_color, measured_shots=measured,
                    unmeasured_shots=unmeasured, anomalies=anomalies,
                    estimated_shots=estimated, per_shot=per_shot)


def for_game(ends) -> Thinking:
    """The clock across a whole game, from each end's shot list."""
    by_color = {c: 0.0 for c in rules.COLORS}
    measured = unmeasured = anomalies = estimated = 0
    per_shot: list = []
    for shots in ends:
        one = for_end(shots)
        for c, v in one.by_color.items():
            by_color[c] += v
        measured += one.measured_shots
        unmeasured += one.unmeasured_shots
        anomalies += one.anomalies
        estimated += one.estimated_shots
        per_shot.extend(one.per_shot)
    return Thinking(by_color=by_color, measured_shots=measured,
                    unmeasured_shots=unmeasured, anomalies=anomalies,
                    estimated_shots=estimated, per_shot=per_shot)
