"""Which side-view frames become ds13, and why those.

``harvest/candidates.py`` chooses overhead frames by how full the house is.
This chooses by something else, because the failure being fixed is different:
the classical detector finds 20 of 27 hand-marked crossings, and the seven it
misses are the frames a replacement most needs.

Which is exactly the trap ``datasets/ds11/README.md`` documents -- every label
through ds10 was written by the colour detector, so validation mAP measured
agreement with a known-flawed opinion, and ds3 scored mAP50 0.977 while finding
11 of 17 hand-observed deliveries. Selecting by where this detector fired would
repeat it: the model would come out confidently good at the crossings we can
already time to 0.08 s, and blind in the same places.

So the set is two halves.

*Scene* frames are chosen by where the stone sits along its travel and nothing
else. Whether the detector could read the frame is not consulted, which is what
lets a refused frame into the set on its merits.

*Outcome* frames are chosen by which gate refused the window, weighted four to
one against the successes, with a control group of crossings it did find so the
set holds both.
"""

import dataclasses
from dataclasses import dataclass, field

from curling_score.harvest.candidates import _spread

# How near the paint counts as "on it", in image rows. Near the hog line a
# delivery covers roughly 33 rows a second, so +-15 is about +-0.45 s -- wide
# enough that a 5 fps scan lands inside it, tight enough that the bin means
# what it says.
CROSSING_ROWS = 15.0

POSITIONS = ("approach", "crossing", "past", "occluded", "clear")

# Per view, so both wide cameras are represented by construction rather than by
# luck. 150 each, 300 in all.
#
# ``occluded`` is a colour blob with no granite under it -- a stone with a body
# over it, or a broom pad with nothing beneath. It is the detector's blind spot
# stated as a bin, and it is weighted like a real one. ``clear`` is the band
# with nothing in it at all, kept small: ds11's pilot reviewed 114 empty frames
# and got not one correction out of them.
SCENE_QUOTA = {"approach": 35, "crossing": 40, "past": 25,
               "occluded": 35, "clear": 15}

# By which gate refused the window. 240 refusals against 60 successes.
#
# The successes are a control group, not a target: without them nobody could
# tell a model that learned stones from one that learned hard frames.
# The keys are ``longview.KEYS`` verbatim -- ``ambiguous`` and ``bad_speed``,
# not the ``two_candidates``/``wrong_speed`` this plan first guessed at. They
# are a frozenset in ``detect/longview.py``; the test below pins the match so
# a renamed key cannot silently empty a bin.
OUTCOME_QUOTA = {"ok": 60, "no_candidate": 50, "ambiguous": 50,
                 "never_reached": 50, "unsteady": 45, "bad_speed": 45}

# 600 frames over 120 videos averages five. Eight lets a richer night fill a
# scarce bin without any one night becoming the dataset.
MAX_PER_VIDEO = 8
# Two per clip. A ds11 clip is 24 s and the frames inside one are near
# duplicates of each other; a bin filled from a single clip would be one
# moment wearing a quota's number.
MAX_PER_CLIP = 2


@dataclass(frozen=True)
class SideCandidate:
    """One side-view frame that could go into the set, and what was seen in it."""

    video_id: str
    view: str            # "left" or "right"
    t_abs: float
    clip_start_s: float
    position: str        # one of POSITIONS
    outcome: str         # a longview.KEYS entry for the window this sits in
    color: str           # the colour scan that proposed it, or "" for clear
    crowding: int        # dark spans beside the stone: 0 clear, 2+ crowded
    edge_row: float | None = None
    labels: tuple = field(default_factory=tuple)
    half: str = ""       # filled in by select(): "scene" or "outcome"

    @property
    def stem(self) -> str:
        return stem_for(self.video_id, self.view, self.t_abs)


def stem_for(video_id: str, view: str, t_abs: float) -> str:
    """The frame's name, and its identity.

    Shaped like ``harvest/candidates.stem_for`` so ``train/labels.parse_name``
    splits it the same way; ``l``/``r`` where the overhead set uses ``t``/``b``,
    which is also what keeps the two sets' names from ever colliding.
    """
    return f"{video_id}_{view[0]}_{t_abs:09.2f}".replace(".", "_")


def position_of(edge_row, hog_row) -> str | None:
    """Where a stone sits against the paint, or None if there was no stone."""
    if edge_row is None:
        return None
    if abs(edge_row - hog_row) <= CROSSING_ROWS:
        return "crossing"
    return "approach" if edge_row < hog_row else "past"


def bin_of(candidate: SideCandidate) -> str:
    """Which quota bin a candidate counts against, once ``select`` has placed
    it in a half.

    This is the same string ``select`` uses to key ``SCENE_QUOTA`` /
    ``OUTCOME_QUOTA`` and ``shortfall`` -- kept in one place, as
    ``harvest.candidates.bin_of`` does for the overhead set, so a manifest
    reporting which bin a chosen frame filled cannot drift from what
    ``select`` actually used. Returns ``""`` for a candidate that has not been
    through ``select`` (``half`` still unset).
    """
    if candidate.half == "scene":
        return f"scene:{candidate.view}:{candidate.position}"
    if candidate.half == "outcome":
        return f"outcome:{candidate.outcome}"
    return ""


def _fill(pool, want, chosen, taken_ids, video_count, clip_count, half):
    """Pick up to ``want`` frames from ``pool`` via ``_spread``, honouring the
    running *global* ``MAX_PER_VIDEO`` / ``MAX_PER_CLIP`` caps.

    Both caps span the whole selection, not one bin, so they cannot be
    enforced by a single ``_spread`` call's own per-call bookkeeping -- a clip
    that already gave one frame to the scene half must not give two more to
    the outcome half. So this filters out anything already at a cap before
    asking ``_spread`` for a ranking, then walks the ranking and accepts or
    skips each item against the *live* counts, incrementing as it goes. A
    pass that could not place any of its picks (every candidate ran into a
    cap) stops rather than looping forever; the shortfall this leaves is
    reported by the caller like any other.
    """
    remaining = want
    while remaining > 0:
        avail = [c for c in pool
                 if id(c) not in taken_ids
                 and video_count.get(c.video_id, 0) < MAX_PER_VIDEO
                 and clip_count.get((c.video_id, c.clip_start_s), 0) < MAX_PER_CLIP]
        if not avail:
            break
        picked = _spread(avail, remaining, MAX_PER_CLIP)
        progressed = False
        for c in picked:
            if id(c) in taken_ids:
                continue
            vkey = c.video_id
            ckey = (c.video_id, c.clip_start_s)
            if (video_count.get(vkey, 0) >= MAX_PER_VIDEO
                    or clip_count.get(ckey, 0) >= MAX_PER_CLIP):
                continue
            chosen.append(dataclasses.replace(c, half=half))
            taken_ids.add(id(c))
            video_count[vkey] = video_count.get(vkey, 0) + 1
            clip_count[ckey] = clip_count.get(ckey, 0) + 1
            remaining -= 1
            progressed = True
            if remaining == 0:
                break
        if not progressed:
            break
    return want - remaining


def select(pool, *, scene_quota=None, outcome_quota=None):
    """Pick the 600. Returns ``(chosen, shortfall)``.

    Two passes, in this order:

    1. **scene**, keyed ``f"scene:{view}:{position}"``, quota
       ``SCENE_QUOTA[position]`` per view, drawn from the whole pool
       regardless of ``outcome`` -- the half that does not consult whether
       the detector succeeded.
    2. **outcome**, keyed ``f"outcome:{key}"``, quota ``OUTCOME_QUOTA[key]``,
       drawn from what the first pass did not take.

    Both honour ``MAX_PER_VIDEO`` and ``MAX_PER_CLIP`` across the *whole*
    selection -- a cap that reset per bin would not be a cap, and with 600
    frames over 120 videos it would let one rich night supply a sixth of the
    set.

    ``shortfall`` maps a bin key to how many it could not supply, exactly as
    ``harvest.candidates.select`` does, and nothing is backfilled: a scene bin
    topped up from an easier one, or an outcome bin topped up from a cheaper
    refusal, would make the set's composition a number nobody could trust --
    the same reason that function reports a gap rather than padding it.
    """
    scene_quota = dict(scene_quota if scene_quota is not None else SCENE_QUOTA)
    outcome_quota = dict(outcome_quota if outcome_quota is not None else OUTCOME_QUOTA)

    chosen: list[SideCandidate] = []
    shortfall: dict[str, int] = {}
    taken_ids: set[int] = set()
    video_count: dict[str, int] = {}
    clip_count: dict[tuple, int] = {}

    for view in ("left", "right"):
        for position, want in scene_quota.items():
            key = f"scene:{view}:{position}"
            eligible = [c for c in pool if c.view == view and c.position == position]
            got = _fill(eligible, want, chosen, taken_ids, video_count,
                        clip_count, "scene")
            if got < want:
                shortfall[key] = want - got

    for outcome_key, want in outcome_quota.items():
        key = f"outcome:{outcome_key}"
        eligible = [c for c in pool if c.outcome == outcome_key]
        got = _fill(eligible, want, chosen, taken_ids, video_count,
                    clip_count, "outcome")
        if got < want:
            shortfall[key] = want - got

    chosen.sort(key=lambda c: (c.t_abs, c.stem))
    return chosen, shortfall
