"""Choose which harvested frames become the dataset.

The point of ds11 is breadth, so selection optimises for *difference* rather
than for how much is happening. ds10 is the cautionary case: 6,995 frames taken
two seconds apart inside twenty ends, which is a few hundred distinct house
configurations wearing a big number.

Frames are therefore picked to span three things at once -- how full the house
is, whether a stone is moving, and when in the night it happened -- with the
number wanted from each bin fixed in advance:

* **empty**, so the model is finally shown a frame whose answer is "nothing".
  Every dataset so far skipped these by construction, and the model has never
  once been told what no stone looks like.
* **sparse / medium / busy**, because a nine-stone cluster and a lone guard are
  different problems.
* **motion**, because a stone in flight is what the pipeline misses.

A bin that cannot be filled is *reported*, never quietly padded: a motion quota
met with still frames would turn the flight coverage into a number nobody could
trust.
"""

from dataclasses import dataclass, field

# (name, low, high) on the total stone count. Order is the reporting order.
#
# The edges follow a measurement, not a guess. Over 423 candidates from eight
# videos the distribution is sharply bimodal: 44% hold no stones at all, then a
# broad hump from four to eight, and almost nothing above. An earlier guess put
# "busy" at ten or more, which turned out to be 2.7% of frames -- a quota that
# could never be filled. Seven or more is 15%, and still a crowded house.
COUNT_BINS = (("empty", 0, 0), ("sparse", 1, 3), ("medium", 4, 6), ("busy", 7, 999))

# Per video, 15 frames, weighted by where the detector was measured to be
# weak rather than spread evenly.
#
# The wave-1 pilot reviewed 551 frames by hand and scored ds10_stratified
# against them. Overall it manages mAP50-95 0.952, but the bins spread
# fourfold, and three of the results contradicted the even split this quota
# replaces:
#
#   empty   114 frames, 0 labels,  0.0% error  -- not one correction in 114
#   sparse  112 frames, 224 labels, 11.2%, R 0.908  <- worst, and unforeseen
#   medium  111 frames, 551 labels,  7.1%, R 0.943
#   busy    103 frames, 875 labels,  2.6%, R 0.987  <- already excellent
#   motion  111 frames, 561 labels,  6.1%, P 0.963  <- worst precision
#
# So empty drops to a token 1 (it was 21% of the pilot and taught nothing),
# busy and medium shrink, and sparse and motion take the room. A missed guard
# is a missed delivery and a phantom stone in flight is a phantom delivery;
# neither shows up in an overall mAP of 0.99.
#
# Set against measured supply per video -- empty 24.5, motion 17.2, sparse 5.2,
# medium 5.2, busy 4.1 -- sparse at 5 is the one that bites: 57% of videos can
# fill it and the rest report a shortfall. That is deliberate. Sparse frames are
# the scarcest thing worth having, so the quota sweeps up what exists.
QUOTA = {"empty": 1, "sparse": 5, "medium": 2, "busy": 2, "motion": 5}
# Val takes the same shape. A benchmark that is composed differently from the
# training set measures a different problem.
VAL_QUOTA = dict(QUOTA)

WAVE1_QUOTA = {name: 1 for name in QUOTA}

# Wave 3 asks for throws and nothing else. Every set through ds12 was built
# from ``is_flight``, which measures net *down-sheet* travel and so can only
# ever accept an arrival -- the model has been shown 547 stones coming into the
# house and not one leaving the hack. Two per video over the season's 120 is
# the couple of hundred frames that gap is worth, and mixing the other bins
# back in would spend review time re-teaching what ds11 already covers.
THROW_QUOTA = {"throw": 2}


def quota_for(split: str = "train", wave: int = 2) -> dict:
    """The bin quota for one video."""
    if wave == 1:
        return dict(WAVE1_QUOTA)
    if wave == 3:
        return dict(THROW_QUOTA)
    return dict(VAL_QUOTA if split == "val" else QUOTA)

MAX_PER_CLIP = 3


@dataclass(frozen=True)
class Candidate:
    """One panel crop that could go into the set, with what the model saw."""

    video_id: str
    panel: str
    t_abs: float
    clip_start_s: float
    kind: str  # "grid", "motion" (an arrival) or "throw" (a departure)
    n_red: int
    n_yellow: int
    flight_id: int | None = None
    lighting: str = "lit"
    labels: tuple = field(default_factory=tuple)
    # Normalised box size for this panel, from its own calibration. An empty
    # frame has no label to copy from and is exactly where a reviewer adds the
    # first box; across 120 videos a borrowed median would be wrong nearly
    # everywhere.
    box_wh: tuple = ()
    # For a motion or throw frame, the stems either side. One still cannot
    # tell a moving stone from a red shoe; three can.
    neighbours: tuple = ()

    @property
    def n_stones(self) -> int:
        return self.n_red + self.n_yellow

    @property
    def stem(self) -> str:
        return stem_for(self.video_id, self.panel, self.t_abs)


def stem_for(video_id: str, panel: str, t_abs: float) -> str:
    """The frame's name, and its identity.

    ``labels.parse_name`` splits this back into a sequence and a time, so the
    panel letter has to sit between the id and the number. Hand edits are keyed
    by this string, so it must be a pure function of the manifest -- change how
    it is built and every correction a person made stops matching.
    """
    return f"{video_id}_{panel[0]}_{t_abs:09.2f}".replace(".", "_")


def bin_of(candidate: Candidate) -> str:
    """Which quota this frame counts against.

    Motion and throw win over the count bins: such a frame is wanted because
    the stone is moving, and which count bin it would otherwise land in is
    beside the point.
    """
    if candidate.kind in ("motion", "throw"):
        return candidate.kind
    for name, low, high in COUNT_BINS:
        if low <= candidate.n_stones <= high:
            return name
    return COUNT_BINS[-1][0]


def _spread(pool, k: int, max_per_clip: int):
    """``k`` items from ``pool``, as far apart in time as they can be.

    Farthest-point selection from the two ends inward, so the picks cover the
    whole night rather than clustering wherever the pool happens to be dense.
    Deterministic: no randomness anywhere, ties broken by the frame name.
    """
    ordered = sorted(pool, key=lambda c: (c.t_abs, c.stem))
    if k <= 0 or not ordered:
        return []

    chosen = [ordered[0]]
    if k > 1 and len(ordered) > 1:
        chosen.append(ordered[-1])
    per_clip = {}
    for c in chosen:
        per_clip[c.clip_start_s] = per_clip.get(c.clip_start_s, 0) + 1

    while len(chosen) < k:
        best, best_gap = None, -1.0
        for c in ordered:
            if c in chosen:
                continue
            if per_clip.get(c.clip_start_s, 0) >= max_per_clip:
                continue
            gap = min(abs(c.t_abs - p.t_abs) for p in chosen)
            if gap > best_gap:
                best, best_gap = c, gap
        if best is None:
            break
        chosen.append(best)
        per_clip[best.clip_start_s] = per_clip.get(best.clip_start_s, 0) + 1

    # The two seeds may have overrun a per-clip cap of 1; trim to it.
    kept, seen = [], {}
    for c in sorted(chosen, key=lambda c: (c.t_abs, c.stem)):
        if seen.get(c.clip_start_s, 0) >= max_per_clip:
            continue
        seen[c.clip_start_s] = seen.get(c.clip_start_s, 0) + 1
        kept.append(c)
    return kept[:k]


def select(pool, quota=None, max_per_clip: int = MAX_PER_CLIP, backfill: bool = False):
    """Pick this video's frames. Returns ``(chosen, shortfall)``.

    ``shortfall`` maps a bin to how many of its frames the video could not
    supply, so the real composition of the set is a recorded number rather than
    the one that was intended.

    ``backfill`` tops up a missed *count* bin from whatever else is going,
    which keeps the frame budget whole.

    It draws from neither motion, throw nor empty. The two moving bins because
    a quota met with still frames would make the coverage of the very thing
    they exist for a number nobody could trust. Empty because the pilot measured 114 empty frames and got not one
    correction out of them: topping up a short bin with empties spends review
    time on the one thing shown to teach nothing, and since empties are 44% of
    the pool that is exactly what a naive backfill does.
    """
    quota = dict(quota if quota is not None else QUOTA)
    by_bin: dict[str, list] = {}
    for c in pool:
        by_bin.setdefault(bin_of(c), []).append(c)

    chosen, shortfall = [], {}
    for name, want in quota.items():
        got = _spread(by_bin.get(name, []), want, max_per_clip)
        chosen.extend(got)
        if len(got) < want:
            shortfall[name] = want - len(got)

    filled = 0
    NO_BACKFILL_FROM = ("motion", "throw", "empty")
    if backfill:
        missing = sum(n for b, n in shortfall.items()
                      if b not in ("motion", "throw"))
        if missing:
            taken = set(chosen)
            rest = [c for c in pool
                    if c not in taken and bin_of(c) not in NO_BACKFILL_FROM]
            extra = _spread(rest, missing, max_per_clip)
            chosen.extend(extra)
            filled = len(extra)

    chosen.sort(key=lambda c: (c.t_abs, c.stem))
    # The shortfall is reported whether or not it was made up. Backfilling
    # keeps the frame budget whole, but it does not mean the video supplied a
    # busy house -- and a composition nobody can see is a composition nobody
    # can trust.
    if filled:
        shortfall = dict(shortfall, backfilled=filled)
    return chosen, shortfall
