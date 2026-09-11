"""Split a stream into games and ends from a whole-video activity profile.

Curling alternates ends between the two houses, and the club streams one sheet
for four hours at a stretch. Both facts fall straight out of a cheap keyframe
sweep of stone counts per panel:

* the **active house** is whichever panel holds stones, and it switches on every
  end boundary;
* a **game** ends when both panels sit empty for a long stretch -- the sheet is
  cleared and reset -- or when the lights go out over that sheet.

Measured on the reference VOD, this recovers 7 alternating ends for the first
game and 6 for the second, agreeing with the wall scoreboard at two independent
timestamps. Deliberately does not use the scoreboard: it is often updated late,
so it validates but never times.
"""

from dataclasses import dataclass, field

from curling_score.geometry import constants as C

# Deliveries are never closer together than about fifteen seconds -- the
# players have to clear the house and set up between stones -- so sixteen of
# them cannot fit into less than this. Ends actually run about fifteen minutes,
# but a floor that follows from the rules is better than a generous guess: the
# old 120 s admitted a 120 s segment at the end of game 1 that was the players
# pushing the rocks back after the game had finished, and it was then analysed
# as an end and given a score.
MIN_DELIVERY_GAP_S = 15.0
MIN_END_S = C.STONES_PER_END * MIN_DELIVERY_GAP_S
# Both houses empty for longer than this means the sheet was reset.
GAME_GAP_S = 240.0
# A player standing over the stones hides them briefly; smooth that away.
SMOOTH_SAMPLES = 5


@dataclass(frozen=True)
class Sample:
    """One keyframe's worth of activity."""

    t: float
    top_stones: int
    bottom_stones: int
    top_playable: bool = True
    bottom_playable: bool = True


@dataclass
class EndSegment:
    number: int
    house: str  # "top" or "bottom"
    start_s: float
    end_s: float


@dataclass
class GameSegment:
    index: int
    start_s: float
    end_s: float
    ends: list[EndSegment] = field(default_factory=list)


def _active_house(samples) -> list[str | None]:
    """Which house is in play at each sample, or None when nothing is."""
    out = []
    for s in samples:
        top = s.top_stones if s.top_playable else 0
        bottom = s.bottom_stones if s.bottom_playable else 0
        if top == 0 and bottom == 0:
            out.append(None)
        else:
            out.append("top" if top > bottom else "bottom")
    return out


def _smooth(active: list[str | None]) -> list[str | None]:
    """Fill short gaps and drop short blips using a windowed majority vote."""
    n = len(active)
    out: list[str | None] = list(active)
    half = SMOOTH_SAMPLES // 2
    for i in range(n):
        lo, hi = max(0, i - half), min(n, i + half + 1)
        window = [a for a in active[lo:hi] if a is not None]
        if not window:
            out[i] = None
            continue
        out[i] = max(set(window), key=window.count)
    return out


def _runs(active, samples):
    """Contiguous stretches of a single active house, as (house, i0, i1)."""
    out, start = [], 0
    for i in range(1, len(active) + 1):
        if i == len(active) or active[i] != active[start]:
            out.append((active[start], start, i))
            start = i
    return out


def segment_games(samples) -> list[GameSegment]:
    """Group an activity profile into games, each holding its ends in order."""
    samples = list(samples)
    if not samples:
        return []

    active = _smooth(_active_house(samples))
    runs = _runs(active, samples)

    def span(i0, i1):
        return samples[i1 - 1].t - samples[i0].t

    # Split into games wherever the sheet stayed empty long enough.
    games_runs: list[list] = [[]]
    for house, i0, i1 in runs:
        if house is None:
            if span(i0, i1) >= GAME_GAP_S:
                if games_runs[-1]:
                    games_runs.append([])
            continue
        games_runs[-1].append((house, i0, i1))

    games: list[GameSegment] = []
    for run_group in games_runs:
        # Merge consecutive runs of the same house: ends always alternate, so a
        # repeat means a brief dropout, not a new end.
        merged: list[list] = []
        for house, i0, i1 in run_group:
            if merged and merged[-1][0] == house:
                merged[-1][2] = i1
            else:
                merged.append([house, i0, i1])

        ends = [
            EndSegment(number=0, house=h, start_s=samples[i0].t, end_s=samples[i1 - 1].t)
            for h, i0, i1 in merged
            if span(i0, i1) >= MIN_END_S
        ]
        if not ends:
            continue
        for n, end in enumerate(ends, start=1):
            end.number = n
        games.append(
            GameSegment(
                index=len(games),
                start_s=ends[0].start_s,
                end_s=ends[-1].end_s,
                ends=ends,
            )
        )
    return games
