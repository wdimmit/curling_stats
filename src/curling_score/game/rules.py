"""Curling rules as pure logic. No computer vision, no I/O."""

from dataclasses import dataclass

from curling_score.geometry import constants as C


@dataclass(frozen=True)
class ThrowInfo:
    """Who throws a given stone of an end, by delivery slot."""

    shot_number: int  # 1..16 across both teams
    has_hammer: bool  # the hammer team throws the even-numbered stones
    team_stone_number: int  # 1..8, this team's k-th stone of the end
    position_slot: int  # 1=lead 2=second 3=third 4=skip
    rock_of_player: int  # 1 or 2


def throw_info(shot_number: int) -> ThrowInfo:
    """Decompose a stone's position in the delivery order.

    Teams alternate; the hammer team throws the even-numbered stones, so stone 16
    is the hammer. Each player throws two consecutive stones for their team.
    """
    if not 1 <= shot_number <= C.STONES_PER_END:
        raise ValueError(
            f"shot_number must be 1..{C.STONES_PER_END}, got {shot_number}"
        )
    k = (shot_number + 1) // 2
    return ThrowInfo(
        shot_number=shot_number,
        has_hammer=shot_number % 2 == 0,
        team_stone_number=k,
        position_slot=(k + 1) // 2,
        rock_of_player=(k - 1) % 2 + 1,
    )


def ordinal(n: int) -> str:
    """1 -> '1st', 2 -> '2nd', 11 -> '11th'."""
    if n % 100 in (11, 12, 13):
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def shot_label(end: int, shot_number: int) -> str:
    """Render a shot the way a curler says it: "3rd end, second's first rock"."""
    t = throw_info(shot_number)
    position = C.POSITION_NAMES[t.position_slot]
    nth = "first" if t.rock_of_player == 1 else "second"
    return f"{ordinal(end)} end, {position}'s {nth} rock"


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


def next_hammer(hammer: str, end_score: dict[str, int]) -> str:
    """Which colour holds the hammer in the following end (R5(a)).

    The team that scores delivers first in the next end, so it gives up the
    hammer. A blank end leaves the order — and therefore the hammer — unchanged.
    """
    scorers = [c for c in COLORS if end_score.get(c, 0) > 0]
    if len(scorers) > 1:
        raise ValueError(f"both teams cannot score in one end: {end_score}")
    if not scorers:
        return hammer
    return other_color(scorers[0])


def running_total(end_scores) -> dict[str, int]:
    """Cumulative score after the given ends, in order."""
    total = {c: 0 for c in COLORS}
    for end in end_scores:
        for c in COLORS:
            total[c] += end.get(c, 0)
    return total


def hammer_chain(first_hammer: str, end_scores) -> list[str]:
    """Who holds the hammer in each end, given the first end's hammer.

    Derived from the scores alone, so it does not depend on having seen who
    threw the opening stone of every end -- which is where reading the hammer
    from detected deliveries goes wrong.
    """
    out, cur = [], first_hammer
    for end in end_scores:
        out.append(cur)
        cur = next_hammer(cur, end)
    return out


def first_hammer_given(hammer: str, end_number: int, earlier_scores) -> str:
    """Work backwards to the opening hammer from a later, observed one.

    A blank end passes the hammer along unchanged and a scored end swaps it, so
    the chain is reversible: each step back is the same rule applied in reverse.
    """
    if end_number < 1:
        raise ValueError(f"end_number must be 1 or more, got {end_number}")
    cur = hammer
    for end in reversed(list(earlier_scores)[: end_number - 1]):
        scorers = [c for c in COLORS if end.get(c, 0) > 0]
        if scorers:
            # Whoever scored gave the hammer away, so before that end the
            # hammer sat with the team that did not score.
            cur = other_color(cur)
    return cur
