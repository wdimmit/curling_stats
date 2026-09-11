"""Find the moments where a delivery was probably missed.

Delivery detection currently recovers about 60% of an end's sixteen stones, and
the limit on improving it is not ideas but ground truth: there is no honest
measure of what is being missed, because the only labels we have were generated
by the detector itself.

The rules narrow the search enormously. Teams alternate, so the same colour
twice running means exactly one delivery is missing, and it must lie between the
first one settling and the second being thrown -- often a window of under a
minute. Sixteen stones land at a fairly even rhythm, so a gap far longer than
the rest of the end holds at least one more. That turns "watch four hours of
curling" into a few dozen short windows to check by eye.
"""

import math
from dataclasses import dataclass

from curling_score.geometry import constants as C

COLORS = ("red", "yellow")

# A gap this many times the typical spacing probably hides a delivery.
GAP_RATIO = 1.8
# Below this many detected deliveries, the rhythm is not worth trusting.
MIN_FOR_RHYTHM = 4
# Which quantile of the observed gaps estimates the true delivery spacing. A gap
# only ever grows when a delivery inside it was missed -- it never shrinks -- so
# the median is dragged upward by the very anomalies we are looking for. The
# lower quartile reflects the gaps that contain nothing.
SPACING_QUANTILE = 0.25
# Ignore slivers: nothing useful to look at in less than this.
MIN_WINDOW_S = 8.0


@dataclass(frozen=True)
class MissCandidate:
    """A window of video where a delivery is likely to have been missed."""

    start_s: float
    end_s: float
    reason: str
    confidence: float
    expected_color: str | None = None

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s


def _other(color):
    return "red" if color == "yellow" else "yellow"


def _gap_confidence(ratio):
    """How strongly a gap of this many normal spacings suggests a missed shot.

    Rises smoothly with the ratio and never quite saturates, so a gap of nine
    normal spacings still outranks one of four instead of both pinning at a cap.
    """
    return 0.40 + 0.52 * (1.0 - math.exp(-0.35 * max(0.0, ratio - 1.0)))


def _typical_spacing(gaps):
    """The gap between consecutive deliveries when none was missed."""
    if not gaps:
        return 0.0
    ordered = sorted(gaps)
    i = min(len(ordered) - 1, int(SPACING_QUANTILE * len(ordered)))
    return ordered[i]


def find_candidates(deliveries, start_s, end_s, gap_ratio: float = GAP_RATIO):
    """Windows worth reviewing, most likely first."""
    ds = sorted(deliveries, key=lambda d: d.t_enter)
    if not ds:
        return [
            MissCandidate(start_s, end_s, "no deliveries detected", 1.0, None)
        ]

    out: list[MissCandidate] = []

    # The teams alternate, so a repeated colour pins a missing delivery to the
    # span between the first settling and the second being thrown.
    for a, b in zip(ds, ds[1:]):
        if a.color != b.color:
            continue
        out.append(
            MissCandidate(
                start_s=a.t_rest,
                end_s=b.t_enter,
                reason="colour repeat",
                confidence=0.95,
                expected_color=_other(a.color),
            )
        )

    # Stones land at a fairly even rhythm; a gap well beyond it hides one.
    gaps = [b.t_enter - a.t_rest for a, b in zip(ds, ds[1:])]
    if len(ds) >= MIN_FOR_RHYTHM and gaps:
        typical = _typical_spacing(gaps)
        for (a, b), gap in zip(zip(ds, ds[1:]), gaps):
            if typical <= 0 or gap < gap_ratio * typical:
                continue
            if a.color != b.color:  # already covered by the repeat rule
                out.append(
                    MissCandidate(
                        start_s=a.t_rest,
                        end_s=b.t_enter,
                        reason="long gap",
                        confidence=_gap_confidence(gap / typical),
                        expected_color=None,
                    )
                )

    # An end has sixteen stones. If the detected ones do not reach the edges of
    # the end, the rest are outside them.
    if len(ds) < C.STONES_PER_END:
        lead = ds[0].t_enter - start_s
        tail = end_s - ds[-1].t_rest
        typical = _typical_spacing(gaps) or (end_s - start_s) / 16.0
        if lead > gap_ratio * max(typical, 1.0):
            out.append(
                MissCandidate(start_s, ds[0].t_enter, "before first delivery",
                              0.6, None)
            )
        if tail > gap_ratio * max(typical, 1.0):
            out.append(
                MissCandidate(ds[-1].t_rest, end_s, "after last delivery",
                              0.6, None)
            )

    out = [c for c in out if c.duration_s >= MIN_WINDOW_S]
    return sorted(out, key=lambda c: (-c.confidence, c.start_s))


# Two deliveries closer than this are almost certainly one event counted twice,
# or a stone scattered by a takeout mistaken for a throw.
MIN_SEPARATION_S = 15.0


@dataclass(frozen=True)
class Suspect:
    """A detected delivery that probably is not one."""

    t_enter: float
    t_rest: float
    color: str
    reason: str
    confidence: float

    @property
    def start_s(self) -> float:
        return self.t_enter - 2.0

    @property
    def end_s(self) -> float:
        return self.t_rest + 4.0


def find_suspects(deliveries, min_separation_s: float = MIN_SEPARATION_S):
    """Detected deliveries worth a second look, least believable first.

    An end holds eight stones per team, so anything beyond that is proof of a
    false positive rather than a fact about the game. Deliveries landing almost
    on top of each other are the other tell: a takeout scatters stones, and a
    scattered stone entering the tracker mid-flight can look like a throw.
    """
    ds = sorted(deliveries, key=lambda d: d.t_enter)
    out: list[Suspect] = []
    seen: set[int] = set()

    for i, (a, b) in enumerate(zip(ds, ds[1:])):
        gap = b.t_enter - a.t_enter
        if gap >= min_separation_s:
            continue
        # Both are shown. One of the pair is spurious, but which is not
        # knowable from timing alone, and guessing would hide the real one.
        for j in (i, i + 1):
            if j in seen:
                continue
            seen.add(j)
            d = ds[j]
            out.append(
                Suspect(d.t_enter, d.t_rest, d.color,
                        f"{gap:.0f}s apart from another delivery", 0.8)
            )

    for color in COLORS:
        same = [(i, d) for i, d in enumerate(ds) if d.color == color]
        excess = len(same) - C.STONES_PER_TEAM_PER_END
        if excess <= 0:
            continue
        # Shortest travel first: those are the least convincing.
        for i, d in sorted(same, key=lambda p: p[1].travel_m)[:excess]:
            if i in seen:
                continue
            seen.add(i)
            out.append(
                Suspect(d.t_enter, d.t_rest, d.color,
                        f"{color} throws {len(same)}, more than the "
                        f"{C.STONES_PER_TEAM_PER_END} a team has", 0.85)
            )

    return sorted(out, key=lambda s: -s.confidence)


# How long after the last stone settles the house is read for scoring.
SCORE_WINDOW_S = 10.0
# Half the stones sitting in the twelve-foot when the end is decided means
# almost nothing was taken out all end. That happens, but far more often it
# means the house was read while stones were being pushed back for the next
# end -- game 2 scored three of its six ends from houses of 7, 8 and 10, and
# came out 8-3 against a board reading 4-4.
CROWDED_HOUSE = 8


@dataclass(frozen=True)
class ScoreWindow:
    """The window an end's score is read from.

    Worth reviewing on its own: it decides the end, and it answers both
    questions at once -- whether the house is as detected, and whether another
    stone was thrown after the one we think was last.
    """

    start_s: float
    end_s: float
    deliveries_seen: int
    later_unchecked_s: float
    color: str | None = None
    # How the delivery that decided the end was confirmed, and how many stones
    # its house held. Both bear directly on whether the score is believable:
    # the weakest routes are also what a person walking through looks like, and
    # a house read during clearing holds stones that were never thrown there.
    evidence: str | None = None
    stones_in_house: int | None = None

    @property
    def reason(self) -> str:
        bits = [f"score read here, from {self.deliveries_seen}/16 deliveries"]
        if self.evidence and self.evidence != "rest":
            bits.append(f"last shot confirmed only by {self.evidence}")
        if self.stones_in_house == 0:
            bits.append("no stone in the house: reads as a blank end")
        elif (self.stones_in_house or 0) >= CROWDED_HOUSE:
            bits.append(f"{self.stones_in_house} stones in the house")
        return "; ".join(bits)

    @property
    def confidence(self) -> float:
        # Less trustworthy the more of the end went unseen, and the more of it
        # runs on afterwards without a delivery being found.
        seen = self.deliveries_seen / C.STONES_PER_END
        tail = min(1.0, self.later_unchecked_s / 300.0)
        conf = 0.9 * seen * (1.0 - 0.5 * tail)
        # An end that reads as blank is the single largest remaining source of
        # wrong scores, so it always wants looking at.
        if self.stones_in_house == 0:
            conf *= 0.5
        elif (self.stones_in_house or 0) >= CROWDED_HOUSE:
            conf *= 0.5
        if self.evidence and self.evidence != "rest":
            conf *= 0.8
        return round(max(0.05, min(0.95, conf)), 3)


def find_score_windows(deliveries, start_s, end_s, scored_at=None,
                       stones_in_house=None):
    """The one window per end that the score is taken from.

    ``scored_at`` is the moment the pipeline actually scored from, which is not
    the last delivery: `shots.scoring_shot` walks back to the last one that left
    stones on the sheet. Passing it keeps the review pointed at the frames that
    decided the end rather than at a trailing phantom.
    """
    ds = sorted(deliveries, key=lambda d: d.t_enter)
    if not ds:
        return []
    at = ds[-1].t_rest if scored_at is None else scored_at
    decided = min(ds, key=lambda d: abs(d.t_rest - at))
    lo = at + 0.5
    return [
        ScoreWindow(
            start_s=lo,
            end_s=min(lo + SCORE_WINDOW_S, max(lo + 2.0, end_s)),
            deliveries_seen=len(ds),
            later_unchecked_s=max(0.0, end_s - at),
            color=decided.color,
            evidence=getattr(decided, "reason", None),
            stones_in_house=stones_in_house,
        )
    ]


# How much either side of a dropped candidate to show, so the rival it lost to
# is on screen next to it.
CONFLICT_PAD_S = 12.0



@dataclass(frozen=True)
class RuleConflict:
    """A candidate delivery the rules would not allow.

    `game/fit.py` keeps the longest run of candidates that obeys the rules --
    sixteen stones, eight a team, thrown strictly in turn -- so anything it
    drops lost a contest with a neighbour. Which of the two was the stone is
    exactly the question a human eye settles in a couple of seconds and the
    detector cannot settle at all.
    """

    start_s: float
    end_s: float
    color: str
    evidence: str | None
    at_s: float
    # The kept delivery of the same colour immediately either side, which is
    # the turn-order clash this candidate lost.
    rival_s: float | None
    # And the nearest kept delivery of *any* colour, which is what says whether
    # this candidate could be a stone at all. Deliveries are never within about
    # fifteen seconds of each other, so a candidate that close to a kept one is
    # a phantom however good its own evidence looks -- the yellow one second
    # after the kept red in game 1 end 4 is the sweeper running alongside it.
    # Judging closeness by same-colour neighbours alone misses exactly that
    # case, and it ranked fourth on the page.
    nearest_kept_s: float | None = None
    nearest_kept_color: str | None = None
    at_limit: bool = False

    @property
    def _is_phantom(self) -> bool:
        return (self.nearest_kept_s is not None
                and self.nearest_kept_s <= MIN_SEPARATION_S)

    @property
    def reason(self) -> str:
        if self._is_phantom:
            other = self.nearest_kept_color or "kept"
            return (f"{self.color} candidate {self.nearest_kept_s:.0f}s from "
                    f"the {other} kept: no two deliveries are that close, so "
                    f"this is not a stone")
        if self.rival_s is not None:
            gap = abs(self.rival_s - self.at_s)
            when = "earlier" if self.rival_s < self.at_s else "later"
            # Two accounts fit and the rules cannot choose between them, so
            # both are worth stating rather than the more interesting one.
            return (f"only one of this {self.color} and the {self.color} kept "
                    f"{gap:.0f}s {when} is allowed: either this is not a "
                    f"stone, or a {_other(self.color)} between them was missed")
        if self.at_limit:
            return f"{self.color} candidate dropped: the team already has eight"
        return (f"{self.color} candidate dropped: keeping it would have broken "
                f"the turn order further along")

    @property
    def confidence(self) -> float:
        weight = WEIGHT_FOR_REVIEW.get(self.evidence or "", 0.5)
        # Twenty-two of thirty-five drops across both games are this close to a
        # kept delivery. They are worth seeing -- they say what the phantoms
        # look like -- but they are not where a missing stone will be found.
        if self._is_phantom:
            return round(weight * 0.3, 3)
        return round(weight, 3)


# Mirrors `fit.WEIGHT_BY_REASON`; kept here so this module needs no import of
# the fitter to rank what the fitter discarded.
WEIGHT_FOR_REVIEW = {
    "rest": 0.95, "house-add": 0.85, "house-remove": 0.75,
    "gap-search": 0.65, "left-view": 0.55,
}


# Routes that accept a delivery without ever seeing it travel. These are the
# ones a stone parked at the delivery end can also satisfy, and did: three
# times in game 1 end 6 alone, on a stone that sat in 94% of the end's frames.
# They are kept because they are the only way to catch a guard thrown short,
# which is a real and common shot, but they are the weakest thing the pipeline
# believes and the least checked by eye.
NO_FLIGHT_REASONS = ("house-appear",)


@dataclass(frozen=True)
class WeakEvidence:
    """A delivery believed without its flight ever having been seen."""

    start_s: float
    end_s: float
    color: str
    evidence: str
    rest_y_m: float
    at_s: float

    @property
    def reason(self) -> str:
        return (f"{self.color} accepted with no flight seen, only because the "
                f"sheet gained a stone at y={self.rest_y_m:+.2f}")

    @property
    def confidence(self) -> float:
        # Higher the further up-sheet it sits, since that is where the stones
        # waiting to be thrown are parked and where this route goes wrong.
        return round(min(0.9, 0.45 + 0.12 * max(0.0, self.rest_y_m - 2.0)), 3)


def find_weak_evidence(deliveries, pad_s: float = CONFLICT_PAD_S):
    """Windows for the deliveries whose flight was never observed."""
    return [
        WeakEvidence(
            start_s=max(0.0, d.t_enter - pad_s),
            end_s=d.t_rest + pad_s,
            color=d.color,
            evidence=d.reason,
            rest_y_m=float(d.rest_y_m),
            at_s=d.t_enter,
        )
        for d in sorted(deliveries, key=lambda d: d.t_enter)
        if getattr(d, "reason", None) in NO_FLIGHT_REASONS
    ]


def find_conflicts(offered, kept, pad_s: float = CONFLICT_PAD_S):
    """Windows around each candidate the rules dropped."""
    keep_ts = {round(d.t_enter, 3) for d in kept}
    kept_sorted = sorted(kept, key=lambda d: d.t_enter)
    thrown = {c: sum(1 for k in kept if k.color == c) for c in COLORS}
    out = []
    for d in sorted(offered, key=lambda d: d.t_enter):
        if round(d.t_enter, 3) in keep_ts:
            continue
        # Only the kept deliveries either side of it can have clashed with it:
        # a shot breaks the turn order against its immediate neighbours.
        before = [k for k in kept_sorted if k.t_enter <= d.t_enter]
        after = [k for k in kept_sorted if k.t_enter > d.t_enter]
        neighbours = [k for k in (before[-1] if before else None,
                                  after[0] if after else None)
                      if k is not None and k.color == d.color]
        rival = (min(neighbours, key=lambda k: abs(k.t_enter - d.t_enter))
                 if neighbours else None)
        closest = (min(kept_sorted, key=lambda k: abs(k.t_enter - d.t_enter))
                   if kept_sorted else None)
        out.append(
            RuleConflict(
                start_s=max(0.0, d.t_enter - pad_s),
                end_s=d.t_rest + pad_s,
                color=d.color,
                evidence=getattr(d, "reason", None),
                at_s=d.t_enter,
                rival_s=rival.t_enter if rival else None,
                nearest_kept_s=(abs(closest.t_enter - d.t_enter)
                                if closest else None),
                nearest_kept_color=closest.color if closest else None,
                at_limit=thrown.get(d.color, 0) >= C.STONES_PER_TEAM_PER_END,
            )
        )
    return out
