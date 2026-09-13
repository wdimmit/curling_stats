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

Three things are deliberately not guessed, and are counted instead:

* **an end's first stone**, because there is nothing to start its clock from.
  ``EndSegment.start_s`` opens only once a stone has *rested* in the house, so
  it is already after the first rock landed -- it cannot serve, and the break
  between ends is not thinking time anyway.
* **a shot with no release**, since the throwing-end camera loses deliveries.
* **a previous shot that never rested**, including the placeholders inserted to
  keep alternation, which have no time at all.

A total assembled from an unknown fraction of an end is not a total, so
``measured_shots`` and ``unmeasured_shots`` travel with it.
"""

import math
from dataclasses import dataclass, field

from curling_score.game import rules, split

# After the previous stone stops, before the next team's clock starts. Long
# enough to clear the ice and no longer.
GRACE_S = 5.0

# The delivering end's tee line, in the throwing panel's own metres.
TEE_LINE_Y_M = 0.0


@dataclass(frozen=True)
class Thinking:
    """What each team spent, and how much of the play it was read from."""

    by_color: dict
    measured_shots: int = 0
    unmeasured_shots: int = 0
    anomalies: int = 0
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
    """When this shot's stone crossed the delivering end's tee line."""
    r = getattr(shot, "release", None)
    if r is None or not getattr(r, "track", ()):
        return None
    return split.crossing_time(r.track, TEE_LINE_Y_M)


def for_end(shots) -> Thinking:
    """The clock across one end."""
    shots = list(shots)
    by_color = {c: 0.0 for c in rules.COLORS}
    per_shot: list = []
    measured = unmeasured = anomalies = 0

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

    return Thinking(by_color=by_color, measured_shots=measured,
                    unmeasured_shots=unmeasured, anomalies=anomalies,
                    per_shot=per_shot)


def for_game(ends) -> Thinking:
    """The clock across a whole game, from each end's shot list."""
    by_color = {c: 0.0 for c in rules.COLORS}
    measured = unmeasured = anomalies = 0
    per_shot: list = []
    for shots in ends:
        one = for_end(shots)
        for c, v in one.by_color.items():
            by_color[c] += v
        measured += one.measured_shots
        unmeasured += one.unmeasured_shots
        anomalies += one.anomalies
        per_shot.extend(one.per_shot)
    return Thinking(by_color=by_color, measured_shots=measured,
                    unmeasured_shots=unmeasured, anomalies=anomalies,
                    per_shot=per_shot)
