"""Where the two positioned stones of a mixed doubles end were set.

Before every doubles end one stone per team goes on the ice (WCF R17). One
sits in the house on the centre line, just behind the button; the other is a
centre guard. In a power play both are moved out to one side. The team whose
stone is in the house has the hammer.

The club places them in one of two ways, and phase 0 found both
(docs/superpowers/specs/2026-09-25-mixed-doubles-phase0.md). From the second
end on, the two spare stones are pushed in from behind the house, which never
looks like a throw. In the first end every stone is still at the delivery end,
so they are slid or carried the length of the sheet, and the pipeline used to
take them for rocks 1 and 2.

One fact about the game makes this tractable: every delivery of an end comes
after its placement is complete. So this finds the moment the placement pattern
first holds still in the destination house, and nothing that settled before it
is a delivery of this end. That covers the placement stones, pre-game slides
and a stone parked by the boards. The arrangement itself is re-read later, from
the still house just before rock 1, because a power play can be set up in two
steps.

Positions are sheet metres from the tee, +y up-sheet, +x to the right facing
down-sheet. The gates come from 17 ends measured in phase 0 and 23 read by eye
(datasets/doubles/marks/placements.json).
"""

from dataclasses import dataclass, replace

from curling_score.detect import delivery as D

# The house stone's back edge sits at the back of the 4-foot. The club's sits
# about 5 cm deeper than the WCF 0.465.
HOUSE_Y_M = -0.49
HOUSE_TOL_M = 0.25
# Power play: the back edge on the tee line where the 8- and 12-foot meet.
PP_HOUSE_X_M = 1.27
PP_HOUSE_Y_M = 0.17
PP_TOL_M = 0.25
GUARD_Y_MIN_M = 2.8
GUARD_Y_MAX_M = 4.2
GUARD_CENTRE_X_M = 0.35
PP_GUARD_X_M = 0.9
PP_GUARD_TOL_M = 0.3

# The delivery finder's own "settled" test and window.
WINDOW_S = D.CHANGE_WINDOW_S
STEP_S = 1.0
# How long the pattern must hold. A guard pushed up through the house crosses
# the house spot on its way, and must not be taken for a house stone.
HOLD_S = 8.0
# A candidate that settles this soon after the placement holds is one of the
# placement stones. Rock 1 arrives 35-45 s after it (phase 0).
SETTLE_S = 10.0
# The house just before rock 1 is read over this span, ending a second before
# rock 1 comes into view. Nobody stands in a doubles house while a rock is on
# its way (phase 0: no one holding a broom there on nearly every rock).
READ_S = 8.0
READ_GAP_S = 1.0
# Candidate reasons that mean a stone flew down-sheet into view: a throw.
# "house-appear" (arrived already stopped) is how a carried guard shows up.
# Phase 0's guards were pushed from behind or carried, never thrown.
THROWN_REASONS = ("rest", "left-view", "late-entry")


def _cxy(s):
    if isinstance(s, tuple):
        return s
    return (s.color, float(s.x_m), float(s.y_m))


def _near(stone, others) -> bool:
    """Whether a stone of the same colour lies within the finder's tolerance."""
    c, x, y = stone
    return any(oc == c and ((ox - x) ** 2 + (oy - y) ** 2) ** 0.5 <= D.CHANGE_TOLERANCE_M
               for oc, ox, oy in others)


def thrown_from(deliveries) -> list:
    """The (color, x, y) rest positions of the candidates that were thrown."""
    return [(d.color, float(d.rest_x_m), float(d.rest_y_m))
            for d in deliveries if d.reason in THROWN_REASONS]


@dataclass(frozen=True)
class Placement:
    t_s: float                      # when the arrangement first held still
    house: tuple                    # (color, x, y) of the house stone
    guard: tuple | None             # (color, x, y), or None when not seen
    power_play: str | None          # "left"/"right": the house stone's side
    seed: tuple = ()                # the still house just before rock 1
    house_s: float | None = None    # when the house stone first held

    @property
    def hammer(self) -> str:
        return self.house[0]

    @property
    def stones(self) -> list:
        return [self.house] + ([self.guard] if self.guard else [])

    @property
    def complete(self) -> bool:
        return self.guard is not None


def _house_side(x, y):
    """None for a centre-line house stone, "left"/"right" for a power play,
    False for neither."""
    if abs(x) <= HOUSE_TOL_M and abs(y - HOUSE_Y_M) <= HOUSE_TOL_M:
        return None
    if abs(abs(x) - PP_HOUSE_X_M) <= PP_TOL_M and abs(y - PP_HOUSE_Y_M) <= PP_TOL_M:
        return "left" if x < 0 else "right"
    return False


def _guards(x, y, side) -> bool:
    if not GUARD_Y_MIN_M <= y <= GUARD_Y_MAX_M:
        return False
    if side is None:
        return abs(x) <= GUARD_CENTRE_X_M
    return (x < 0) == (side == "left") and abs(abs(x) - PP_GUARD_X_M) <= PP_GUARD_TOL_M


def classify(stones, t_s: float = 0.0, *, thrown=()) -> Placement | None:
    """The placement these settled stones show, or None.

    Exactly one stone on a house spot, centre or power play. The guard is the
    one stone of the other colour on the matching guard spot, or None when it
    is not seen. Anything more ambiguous is not a placement.

    ``thrown`` is the (color, x, y) rest positions of thrown rocks. A stone
    on the guard spot lying on one of its colour is that rock, not the guard,
    and is left out. The house stone is never refused this way: ih59's first
    house stone was slid the length of the sheet.
    """
    cxy = [_cxy(s) for s in stones or ()]
    thrown = [_cxy(s) for s in thrown]
    houses = [((c, x, y), side) for c, x, y in cxy
              if (side := _house_side(x, y)) is not False]
    if len(houses) != 1:
        return None
    (house, side), = houses
    guards = [(c, x, y) for c, x, y in cxy
              if c != house[0] and _guards(x, y, side) and not _near((c, x, y), thrown)]
    if len(guards) > 1:
        return None
    return Placement(t_s=t_s, house=house, guard=guards[0] if guards else None,
                     power_play=side)


def _held(frames, t0: float, t1: float) -> list:
    """Every stone settled in any window from ``t0`` to ``t1``."""
    out, t = [], t0
    while t <= t1:
        out += D._settled_stones(frames, t, t + WINDOW_S) or []
        t += STEP_S
    return out


def _play_began(settled, alone: Placement, before, thrown) -> bool:
    """Whether a stone has settled that the placement cannot account for.

    Not the house stone, not already there when the house stone held, and not
    a stone of the guard's colour on the guard spot, which may yet be the
    guard (unless it lies on a thrown rock's rest position).
    """
    for s in settled or ():
        c, x, y = s
        if _near(s, [alone.house]) or _near(s, before):
            continue
        if c != alone.hammer and _guards(x, y, alone.power_play) and not _near(s, thrown):
            continue
        return True
    return False


def find(frames, t0: float, t1: float, *, thrown=()) -> Placement | None:
    """The first moment in [t0, t1] the placement holds still, or None.

    "Holds" means the same hammer colour and power-play side in every window
    for ``HOLD_S``. A complete placement, with its guard, is what is looked for.
    If the guard is never seen, the first held house stone alone is returned,
    with ``complete`` False. ``house_s`` is when the house stone first held.

    The guard is looked for only until play begins: once the house stone has
    held, a stone that settles anywhere else but the guard spot is a rock, and
    so is any stone lying on a ``thrown`` rest position ((color, x, y), see
    :func:`thrown_from`). Searching on past that let a later rock settle on the
    guard spot and become "the guard", and every real rock before it was then
    dropped as part of the placement.
    """
    thrown = [_cxy(s) for s in thrown]
    frames = [(t, d) for t, d in frames if t0 <= t <= t1 + WINDOW_S]
    found = {True: None, False: None}
    run = {True: None, False: None}
    before = []      # what was already settled while the house stone held
    t = t0
    while t <= t1 and found[True] is None:
        settled = D._settled_stones(frames, t, t + WINDOW_S)
        if found[False] is not None and _play_began(settled, found[False], before, thrown):
            break
        p = classify(settled, t, thrown=thrown) if settled else None
        for complete in (True, False):
            ok = p is not None and (p.complete or not complete)
            key = (p.hammer, p.power_play) if ok else None
            start = run[complete]
            if ok and start is not None and start[1] == key:
                if t - start[0].t_s >= HOLD_S and found[complete] is None:
                    found[complete] = start[0]
                    if not complete:
                        before = _held(frames, start[0].t_s, t)
            elif ok:
                run[complete] = (p, key)
            else:
                run[complete] = None
        t += STEP_S
    got = found[True] or found[False]
    if got is None:
        return None
    return replace(got, house_s=(found[False] or got).t_s)


def read_before(frames, placed: Placement, t_first: float) -> Placement:
    """The placement as it stood just before rock 1 came into view.

    The arrangement is re-read from the still house over the ``READ_S`` ending
    ``READ_GAP_S`` before ``t_first``. A power play set up in two steps shows
    its final shape there. ``seed`` is set to that house, for rock 1's house
    diff. When the re-read shows no placement, the found arrangement stands.

    The re-read asks more of a stone (half the frames over ``READ_S``) than
    :func:`find` does (40% over ``WINDOW_S``), so a guard seen in between drops
    out of it. When the re-read shows the same hammer and side with no guard,
    the found guard is kept: losing it would make rock 1's fill base one
    short, and a leading blank renumbers every shot. A two-step power play
    still switches, because its side differs.
    """
    from curling_score.detect.rest import stones_in_window

    lo, hi = t_first - READ_GAP_S - READ_S, t_first - READ_GAP_S
    window = [(t, d) for t, d in frames if lo <= t <= hi]
    seed = tuple(stones_in_window(window)) if window else ()
    again = classify(seed, placed.t_s) if seed else None
    use = again if again is not None else placed
    guard = use.guard
    if guard is None and (use.hammer, use.power_play) == (placed.hammer, placed.power_play):
        guard = placed.guard
    return Placement(t_s=placed.t_s, house=use.house, guard=guard,
                     power_play=use.power_play, seed=seed, house_s=placed.house_s)


def exclude(deliveries, placed: Placement | None):
    """Split candidates into (kept, dropped) by the placement.

    Dropped: anything that settled before the placement had held for
    ``SETTLE_S``. Those are the placement stones themselves (slid, carried or
    set down) and whatever was slid before the game. By time alone: a later
    hit and stick on a placed stone's spot.
    """
    if placed is None:
        return list(deliveries), []
    cut = placed.t_s + SETTLE_S
    kept = [d for d in deliveries if d.t_rest >= cut]
    dropped = [d for d in deliveries if d.t_rest < cut]
    return kept, dropped


def fill_base(placed: Placement | None, fmt) -> int:
    """How many stones rock 1 found already in the panel's view.

    The placed stones that were seen. When no placement was found in a format
    that places stones, assume all of them, the conservative choice: they are
    on the ice either way, and counting them as thrown rocks invents blanks.
    """
    if placed is not None:
        return len(placed.stones)
    return 2 * fmt.placed_per_team
