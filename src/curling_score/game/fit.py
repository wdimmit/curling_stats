"""Choose the deliveries an end is allowed to contain.

Detection cannot help over-counting. Every phantom seen in this footage has
been a person: sweepers wear their team's colours, run alongside the stone they
are sweeping and enter the panel from the delivery end, so they satisfy every
test that looks at one track in isolation. Clearing the house at the end of an
end is worse still -- stones really are dragged down-sheet and across, which is
a delivery in every visible respect except that the end is already over.

The rules settle it from outside the pixels. An end holds sixteen deliveries,
eight per team, thrown strictly in turn. A candidate list that breaks those
rules is provably wrong, and the repair is to keep the largest run that obeys
them -- which is a longest-alternating-subsequence problem, not a threshold.

Alternation alone does most of the work. Game 1 end 1 offered
``Y R Y R Y R Y R Y R Y R Y Y R Y``: the doubled yellow can only be one stone,
and the one it drops is the candidate that sat 2.2 s before a red, which no two
deliveries ever are.
"""

from __future__ import annotations

from curling_score.geometry import constants as C

# How much a candidate's evidence is worth when two selections are the same
# length. These rank the routes in `Delivery.reason` by how hard they are to
# fake: a stone seen settling and still sitting there afterwards is the
# strongest, and one merely still moving when it left the panel the weakest,
# since that is also what a person walking out of shot looks like.
WEIGHT_BY_REASON = {
    "rest": 1.0,
    "house-add": 0.9,
    "house-remove": 0.8,
    # Seen only after it was already well down the sheet, so most of its
    # flight is missing -- but what stands in its place is the strongest
    # evidence any route asks for: the stone is still there afterwards, nothing
    # of its colour had been sitting in that spot, and the colour's count went
    # up. That is more than a gap search asks, which only takes the longest
    # track in a window the rules said must hold a delivery.
    #
    # Ranked below it at first, and it cost the real shot: game 1 end 3's red
    # at 2414.9 was dropped in favour of a sweeper 1.4 s earlier that the gap
    # search had turned up.
    "late-entry": 0.75,
    "gap-search": 0.7,
    "left-view": 0.6,
    # Weakest of all: a stone that was simply there in its first sighting,
    # with no flight seen at all. It is the only route that can catch a guard
    # thrown short, and also the only one a stone pushed back between ends
    # could satisfy, so the rules should prefer any other account.
    "house-appear": 0.45,
    # A throw seen leaving the far house with no arrival seen here. Real, but
    # placed only by inference: its time is the start of the slide and its
    # rest, if any, is read off the house. Anything actually seen arriving in
    # this house outranks it -- when a release went unpaired because its
    # arrival came late or its handle colour was misread, the arrival is the
    # record and the release must lose the tie, not replace it.
    "release-add": 0.4,
    "release-remove": 0.35,
    "hogged": 0.3,
}
DEFAULT_WEIGHT = 0.5


# Two candidates closer together than this are not two stones. Alternation
# does not cover it: a phantom of the *opposite* colour alternates perfectly
# and stays inside both teams' eight. Game 2 end 1 ends with a yellow at 8508.9
# and a red 6.4 s later that is the players starting to clear -- and because it
# comes last, the end is scored from it, reading eight stones in the house
# mid-clear-up.
#
# Ten seconds, chosen by measurement rather than by argument. Swept against the
# wall board and the observations in `validation/`, the phantoms confirmed by
# eye sit 1.0 to 6.4 s from their neighbour while real deliveries are 40 s and
# more apart, so there is room between them. Fifteen seconds looked like the
# natural floor -- it is what an end's length is derived from in
# `segment.MIN_DELIVERY_GAP_S` -- and it cost a matching end and a point of
# final score by cutting real deliveries. That floor is used to reject a
# segment far too short to be an end, where being generous is free; this one
# discards individual stones, where it is not.
MIN_SEPARATION_S = 10.0

# Two candidates of the same colour cannot both be kept as neighbours -- unless
# a rock of the other colour was thrown between them and never seen: hogged,
# lost under the sweepers, or anything else that leaves no trace in the house.
# Dropping one of the two was the only option, and it discarded a real
# delivery every time (game 3 end 7's takeout at 6174). The time between them
# says which case it is: two rocks thrown in turn are one interval apart, and
# a rock missed between them makes it two. Judged against the end's own
# median interval, because how long a team takes over a rock varies a lot.
MISSED_GAP_FACTOR = 1.6


def weight(dv) -> float:
    return WEIGHT_BY_REASON.get(getattr(dv, "reason", ""), DEFAULT_WEIGHT)


def missed_gap_s(items, factor: float = MISSED_GAP_FACTOR,
                 min_separation_s: float = MIN_SEPARATION_S) -> float:
    """The shortest gap between two same-colour candidates that can hold a
    missed rock of the other colour, judged against this end's own rhythm."""
    ts = sorted(d.t_enter for d in items)
    gaps = sorted(b - a for a, b in zip(ts, ts[1:]) if b - a >= min_separation_s)
    if not gaps:
        return float("inf")
    return factor * gaps[len(gaps) // 2]


def fit_end(deliveries, per_end: int = C.STONES_PER_END,
            per_team: int = C.STONES_PER_TEAM_PER_END,
            min_separation_s: float = MIN_SEPARATION_S):
    """The longest run of candidates that the rules permit, in time order.

    Ties on length are broken by evidence, so where the rules leave a choice
    the better-attested candidate survives. Two candidates of the same colour
    may follow one another only when the time between them has room for the
    other team's unseen rock, which is then counted against that team's eight
    and the end's sixteen; :func:`shots.from_deliveries` shows it as a blank.
    """
    items = sorted(deliveries, key=lambda d: d.t_enter)
    if not items:
        return []
    gap_for_missed = missed_gap_s(items, min_separation_s=min_separation_s)

    # State is the last candidate kept -- which fixes both whose turn it is and
    # when they threw -- plus how many each team has thrown, unseen rocks
    # included. Keying on the index rather than just the colour is what lets
    # the spacing rule apply: two paths reaching the same colour and counts can
    # have got there at different times, and only the time says whether
    # another stone can follow. The value is (real candidates kept, evidence,
    # chain): the unseen rocks fill slots but are not the thing being maximised.
    best: dict[tuple[int, int, int], tuple[int, float, tuple]] = {
        (-1, 0, 0): (0, 0.0, ())
    }
    for i, dv in enumerate(items):
        for (last_i, nr, ny), (count, wsum, chain) in list(best.items()):
            thrown = {"red": nr, "yellow": ny}
            if last_i >= 0:
                prev = items[last_i]
                if dv.t_enter - prev.t_enter < min_separation_s:
                    continue  # no two stones are thrown that close together
                if dv.color == prev.color:
                    if dv.t_enter - prev.t_enter < gap_for_missed:
                        continue  # a team cannot throw twice in a row
                    # Room for the other team's rock between them: an unseen one.
                    other = "yellow" if dv.color == "red" else "red"
                    thrown[other] += 1
            thrown[dv.color] += 1
            if (thrown["red"] > per_team or thrown["yellow"] > per_team
                    or thrown["red"] + thrown["yellow"] > per_end):
                continue
            key = (i, thrown["red"], thrown["yellow"])
            cand = (count + 1, wsum + weight(dv), chain + (i,))
            have = best.get(key)
            if have is None or (cand[0], cand[1]) > (have[0], have[1]):
                best[key] = cand

    _count, _wsum, chain = max(best.values(), key=lambda v: (v[0], v[1]))
    return [items[i] for i in chain]
