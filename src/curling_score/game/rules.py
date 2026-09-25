"""Curling rules as pure logic. No computer vision, no I/O."""

from dataclasses import dataclass

from curling_score.geometry import constants as C


@dataclass(frozen=True)
class ThrowInfo:
    """Who throws a given stone of an end, by delivery slot."""

    shot_number: int  # 1.. across both teams, delivered stones only
    has_hammer: bool  # the hammer team throws the even-numbered stones
    team_stone_number: int  # this team's k-th delivered stone of the end
    position_slot: int  # the player: 1=lead..4=skip in fours, 1=A 2=B in doubles
    rock_of_player: int  # this player's n-th rock of the end


def throw_info(shot_number: int, fmt=None) -> ThrowInfo:
    """Decompose a stone's position in the delivery order.

    Four-player unless ``fmt`` (a :class:`format.GameFormat`) says otherwise;
    the arithmetic lives there, so both formats read from one table.
    """
    from curling_score.game.format import FOURS

    return (fmt or FOURS).throw_info(shot_number)


def ordinal(n: int) -> str:
    """1 -> '1st', 2 -> '2nd', 11 -> '11th'."""
    if n % 100 in (11, 12, 13):
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def shot_label(end: int, shot_number: int, fmt=None) -> str:
    """Render a shot the way a curler says it: "3rd end, second's first rock"."""
    from curling_score.game.format import FOURS

    return (fmt or FOURS).shot_label(end, shot_number)


COLORS = ("red", "yellow")


@dataclass(frozen=True)
class Stone:
    """A stone at rest, in sheet metres relative to the tee of the playing house."""

    color: str
    x: float
    y: float

    @property
    def distance_to_tee(self) -> float:
        return (self.x**2 + self.y**2) ** 0.5

    @property
    def in_house(self) -> bool:
        """True if any part of the stone touches the 12-foot ring (R12(b))."""
        return self.distance_to_tee <= C.IN_HOUSE_MAX_D_M


def score_end(stones) -> dict[str, int]:
    """Points scored by each colour at the completion of an end (R12(b)).

    A team scores one point for each of its own stones in or touching the house
    that is closer to the tee than any opposition stone. No stone in the house
    means a blank end.
    """
    counters = sorted(
        (s for s in stones if s.in_house), key=lambda s: s.distance_to_tee
    )
    score = {c: 0 for c in COLORS}
    if not counters:
        return score
    shot_color = counters[0].color
    for s in counters:
        if s.color != shot_color:
            break
        score[shot_color] += 1
    return score


def other_color(color: str) -> str:
    if color not in COLORS:
        raise ValueError(f"unknown colour {color!r}")
    return COLORS[1] if color == COLORS[0] else COLORS[0]


def next_hammer(hammer: str, end_score: dict[str, int], blank_passes: bool = False) -> str:
    """Which colour holds the hammer in the following end.

    The team that scores delivers first in the next end, so it gives up the
    hammer (R5(a)). A blank end leaves the order unchanged in fours; in doubles
    the team that threw first gets the placement decision (R17) and is expected
    to take the hammer with it, which ``blank_passes`` says.
    """
    scorers = [c for c in COLORS if end_score.get(c, 0) > 0]
    if len(scorers) > 1:
        raise ValueError(f"both teams cannot score in one end: {end_score}")
    if not scorers:
        return other_color(hammer) if blank_passes else hammer
    return other_color(scorers[0])


def running_total(end_scores) -> dict[str, int]:
    """Cumulative score after the given ends, in order."""
    total = {c: 0 for c in COLORS}
    for end in end_scores:
        for c in COLORS:
            total[c] += end.get(c, 0)
    return total


def hammer_chain(first_hammer: str, end_scores, blank_passes: bool = False) -> list[str]:
    """Who holds the hammer in each end, given the first end's hammer.

    Derived from the scores alone, so it does not depend on having seen who
    threw the opening stone of every end -- which is where reading the hammer
    from detected deliveries goes wrong.
    """
    out, cur = [], first_hammer
    for end in end_scores:
        out.append(cur)
        cur = next_hammer(cur, end, blank_passes)
    return out


def first_hammer_given(hammer: str, end_number: int, earlier_scores, blank_passes: bool = False) -> str:
    """Work backwards to the opening hammer from a later, observed one.

    A blank end leaves the hammer unchanged in fours; in doubles it passes it
    along. A scored end swaps it, so the chain is reversible: each step back is
    the same rule applied in reverse.
    """
    if end_number < 1:
        raise ValueError(f"end_number must be 1 or more, got {end_number}")
    cur = hammer
    for end in reversed(list(earlier_scores)[: end_number - 1]):
        scorers = [c for c in COLORS if end.get(c, 0) > 0]
        # Whoever scored gave the hammer away, so before that end the hammer
        # sat with the team that did not score. In doubles a blank moved it
        # too, so stepping back over one moves it back.
        if scorers or blank_passes:
            cur = other_color(cur)
    return cur
