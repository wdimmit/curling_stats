"""Pick the few firings per game worth a frame.

A noisy game can fire a check hundreds of times; a labelling round wants a
handful that differ from each other. So each check keeps its best firings,
spread over the game's ends and over time, and the game keeps a few of every
check rather than all of one.
"""

from . import CHECKS
from .record import Firing


def _pick_for_check(firings: list[Firing], per_check: int,
                    spacing_s: float) -> list[Firing]:
    """Up to ``per_check`` of one check's firings, best first.

    The first pass takes at most one per end, so a check that fired twice in
    one end and once in another gives frames from both. The second pass fills
    what is left. Either way a pick must be ``spacing_s`` from the others, so
    two picks are not the same moment seen twice.
    """
    ranked = sorted(firings, key=lambda f: (-f.score, f.t))
    picks: list[Firing] = []
    ends: set = set()
    for spread in (True, False):
        for f in ranked:
            if len(picks) >= per_check:
                break
            if f in picks or (spread and f.end in ends):
                continue
            if any(abs(f.t - p.t) < spacing_s for p in picks):
                continue
            picks.append(f)
            ends.add(f.end)
    return picks


def _pick_for_game(firings: list[Firing], per_check: int, per_game: int,
                   spacing_s: float) -> list[Firing]:
    by_check = {
        c: _pick_for_check([f for f in firings if f.check == c],
                           per_check, spacing_s)
        for c in CHECKS}
    # Every check's best first, then every check's second best, so the cap
    # trims the later checks' extras before any check's only frame.
    merged = [picks[rank] for rank in range(per_check)
              for c in CHECKS if rank < len(picks := by_check[c])]
    return merged[:per_game]


def choose(firings: list[Firing], *, per_check: int = 2, per_game: int = 12,
           spacing_s: float = 5.0) -> list[Firing]:
    """The firings to save frames for, ordered by game and time.

    Firings with no box are dropped: without one there is nothing to show.
    """
    games: dict = {}
    for f in firings:
        if f.box is not None:
            games.setdefault(f.game, []).append(f)
    out = [f for g in games.values()
           for f in _pick_for_game(g, per_check, per_game, spacing_s)]
    return sorted(out, key=lambda f: (-1 if f.game is None else f.game, f.t))
