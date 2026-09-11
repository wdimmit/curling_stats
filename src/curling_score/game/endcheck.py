"""Sanity checks on a detected end's deliveries.

Curling is rigid about how an end is made up, and those rules are the cheapest
error detector available. Each team throws exactly eight stones and the teams
strictly alternate, so any departure is proof that detection went wrong rather
than a fact about the game:

* fewer than sixteen deliveries -- some were not seen;
* more than eight from one team -- something that was not a delivery was
  counted, most often a stone knocked out of the house by a takeout;
* the same colour twice running -- exactly one delivery is missing between them;
* an unusually long gap between deliveries -- likely more than one.

None of this repairs the end. It says *where* the detection is untrustworthy so
those shots can be flagged rather than silently believed.
"""

from dataclasses import dataclass, field

from curling_score.geometry import constants as C

COLORS = ("red", "yellow")
# Club deliveries land about a minute apart; well past double that means a shot
# went unseen rather than a slow one.
LONG_GAP_S = 150.0


@dataclass
class EndCheck:
    """What the rules say about a detected sequence of deliveries."""

    thrown: dict
    complete: bool
    problems: list = field(default_factory=list)
    missed_after: list = field(default_factory=list)  # indices to insert after
    long_gaps: list = field(default_factory=list)  # (index, seconds)

    @property
    def confidence(self) -> float:
        """How much of the end was actually observed, 0 to 1."""
        seen = sum(self.thrown.values())
        return min(1.0, seen / C.STONES_PER_END)


def check(deliveries, long_gap_s: float = LONG_GAP_S) -> EndCheck:
    """Compare a detected end against what the rules require of one."""
    deliveries = sorted(deliveries, key=lambda d: d.t_enter)
    thrown = {c: sum(1 for d in deliveries if d.color == c) for c in COLORS}

    problems: list[str] = []
    total = len(deliveries)
    if total != C.STONES_PER_END:
        problems.append(
            f"saw {total} deliveries, an end has {C.STONES_PER_END}"
        )
    for color in COLORS:
        if thrown[color] > C.STONES_PER_TEAM_PER_END:
            problems.append(
                f"{color} threw {thrown[color]}, more than the "
                f"{C.STONES_PER_TEAM_PER_END} a team has"
            )

    missed_after = [
        i for i, (a, b) in enumerate(zip(deliveries, deliveries[1:]))
        if a.color == b.color
    ]

    long_gaps = [
        (i, round(b.t_enter - a.t_enter, 1))
        for i, (a, b) in enumerate(zip(deliveries, deliveries[1:]))
        if b.t_enter - a.t_enter > long_gap_s
    ]

    complete = not problems and not missed_after
    return EndCheck(
        thrown=thrown,
        complete=complete,
        problems=problems,
        missed_after=missed_after,
        long_gaps=long_gaps,
    )
