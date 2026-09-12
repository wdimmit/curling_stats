"""Turn rest states into an ordered, attributed sequence of shots.

Each transition between settled configurations is one delivery. Usually the
delivered stone is simply the one that appeared, which names the thrower
outright. A takeout is harder: the count can stay level or fall, and no new
stone is visible. There the colour comes from the rule that teams strictly
alternate.

That same alternation is also an error-correcting code. If two consecutive
deliveries read as the same colour, a delivery went unseen between them, and we
insert it rather than let every later shot shift by one -- which would misname
the thrower for the rest of the end.
"""

from dataclasses import dataclass

from curling_score.game import rules
from curling_score.geometry import constants as C


@dataclass
class Shot:
    """One delivery and the house it left behind."""

    number: int  # 1..16 within the end
    color: str
    stones: list
    t_rest_s: float
    color_inferred: bool = False  # colour came from alternation, not a new stone
    missing: bool = False  # never observed; inserted to preserve alternation
    confidence: float = 1.0
    # The delivery this shot was built from, kept so the flight and the route
    # that confirmed it survive into the timeline. None for a placeholder.
    delivery: object = None
    # Whether we believe the house we are showing. False means we could not
    # read it and a person has to fill it in -- which is a different statement
    # from an empty house, and must never be rendered as one.
    state_known: bool = True
    # What this shot did to the sheet: {"added": [...], "removed": [...],
    # "moved": [...]}, each a list of {"color", "x", "y"} (moved also carries
    # "from_x"/"from_y"/"distance_m"). The evidence a shot was a hit.
    house_delta: dict | None = None
    # Which entry in ``stones`` is the stone that was just thrown.
    delivered_stone_index: int | None = None

    @property
    def throw(self) -> rules.ThrowInfo:
        return rules.throw_info(self.number)


def scoring_shot(shots):
    """The shot whose house decides the end.

    The last shot that actually left stones on the sheet, not simply the last
    shot. A trailing candidate is routinely a phantom thrown up by the players
    clearing the house -- stones really are dragged down-sheet and across, which
    is a delivery in every visible respect except that the end is over -- and by
    the time it settles the sheet is bare. Four of game 1's eight ends scored
    from an empty house before this rule existed.

    One function so the review and the timeline cannot disagree about which
    moment decided an end.
    """
    return next((s for s in reversed(list(shots)) if s.stones), None)


# Clearing must shed at least this many stones and end this close to empty.
CLEAR_MIN_DROP = 3
CLEAR_MAX_REMAINING = 2


def trim_clearing(rest_states):
    """Drop the trailing states where the players lift the stones off.

    At the end of an end the sheet is cleared, which is a long and perfectly
    stable configuration -- and a near-empty one. Left in, it is scored as the
    final house (turning real ends into blanks) and its removals are counted as
    deliveries. Clearing is distinguished from a takeout by going nearly to zero
    and never recovering.
    """
    states = list(rest_states)
    if len(states) < 2:
        return states

    counts = [len(s.stones) for s in states]
    # Longest non-increasing suffix.
    i = len(counts) - 1
    while i > 0 and counts[i - 1] >= counts[i]:
        i -= 1

    drop = counts[i] - counts[-1]
    if drop >= CLEAR_MIN_DROP and counts[-1] <= CLEAR_MAX_REMAINING:
        return states[: i + 1] or states[:1]
    return states


def _appeared(before, after) -> list:
    """Stones present after a delivery that were not there before."""
    from curling_score.detect.rest import configurations_match

    leftover = list(before)
    new = []
    for stone in after:
        for i, old in enumerate(leftover):
            if old.color == stone.color and configurations_match([old], [stone]):
                leftover.pop(i)
                break
        else:
            new.append(stone)
    return new


def _observe(rest_states):
    """Per transition: the colour delivered, if it can be seen directly."""
    out = []
    for before, after in zip(rest_states, rest_states[1:]):
        new = _appeared(before.stones, after.stones)
        colors = {s.color for s in new}
        out.append(
            {
                "color": colors.pop() if len(colors) == 1 else None,
                "state": after,
                "confidence": (
                    min((s.confidence for s in new), default=0.6) if new else 0.6
                ),
            }
        )
    return out


def _repair_alternation(observations):
    """Fill in unseen colours and insert deliveries that were never observed."""
    anchors = [(i, o["color"]) for i, o in enumerate(observations) if o["color"]]
    if not anchors:
        return observations

    repaired = []
    prev_color = None
    for obs in observations:
        color = obs["color"]
        if color is None:
            # A takeout: whoever did not throw last must be throwing now.
            color = rules.other_color(prev_color) if prev_color else None
            obs = {**obs, "color": color, "inferred": True}
        elif prev_color is not None and color == prev_color:
            # The same team twice running is impossible: one delivery was
            # missed. Insert it so later throwers stay correctly named.
            repaired.append(
                {
                    "color": rules.other_color(prev_color),
                    "state": None,
                    "confidence": 0.0,
                    "missing": True,
                }
            )
            prev_color = rules.other_color(prev_color)
        repaired.append(obs)
        prev_color = color or prev_color
    return repaired


def infer_shots(rest_states) -> list[Shot]:
    """Reduce settled configurations to the deliveries that produced them."""
    rest_states = trim_clearing(rest_states)
    if len(rest_states) < 2:
        return []

    observations = _repair_alternation(_observe(rest_states))

    out: list[Shot] = []
    for obs in observations:
        if len(out) >= C.STONES_PER_END:
            break
        if not obs.get("color"):
            continue
        state = obs.get("state")
        out.append(
            Shot(
                number=len(out) + 1,
                color=obs["color"],
                stones=list(state.stones) if state else [],
                t_rest_s=state.t_mid if state else float("nan"),
                color_inferred=bool(obs.get("inferred")),
                missing=bool(obs.get("missing")),
                confidence=float(obs.get("confidence", 0.0)),
            )
        )
    return out


def hammer_from_shots(shots) -> str | None:
    """Which colour holds the hammer, read from who threw first.

    The team delivering the first stone of an end does not have last rock, so
    this is observed rather than assumed -- no need to know the coin toss.
    """
    if not shots:
        return None
    return rules.other_color(shots[0].color)


# How long after a stone settles to read the house it left.
SETTLE_WINDOW_S = 12.0

# Two readings of the same resting stone differ by detector jitter and by the
# averaging ``stones_in_window`` does, but not by much; a stone that shifted
# further than this was struck.
MOVED_MIN_M = 0.30
# Beyond this, two same-coloured stones are different stones rather than one
# that moved. A stone driven right out of the house travels much further than
# this, but so does the gap between two unrelated rocks, and calling a removal
# plus an arrival a "move" would invent a collision that did not happen.
ASSOCIATE_MAX_M = 1.50


def _xy(stone):
    """Position of a stone, however it is represented."""
    if isinstance(stone, dict):
        return float(stone["x"]), float(stone["y"])
    return float(stone.x_m), float(stone.y_m)


def _color(stone):
    return stone["color"] if isinstance(stone, dict) else stone.color


def _pos(stone) -> dict:
    x, y = _xy(stone)
    return {"color": _color(stone), "x": x, "y": y}


def house_delta(before, after, moved_min_m: float = MOVED_MIN_M) -> dict:
    """What one delivery did to the house.

    Stones are matched to their nearest same-coloured counterpart, closest pair
    first, so a crowded house does not hand an arrival to the wrong rock the way
    matching in list order would. Whatever is left over on each side genuinely
    came or went.
    """
    before, after = list(before), list(after)
    pairs = sorted(
        (
            (((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5, i, j)
            for i, b in enumerate(before)
            for j, a in enumerate(after)
            if _color(b) == _color(a)
            for (bx, by), (ax, ay) in [(_xy(b), _xy(a))]
        ),
        key=lambda p: p[0],
    )

    took_b: set[int] = set()
    took_a: set[int] = set()
    moved = []
    for dist, i, j in pairs:
        if dist > ASSOCIATE_MAX_M or i in took_b or j in took_a:
            continue
        took_b.add(i)
        took_a.add(j)
        if dist >= moved_min_m:
            fx, fy = _xy(before[i])
            moved.append({**_pos(after[j]), "from_x": fx, "from_y": fy,
                          "distance_m": dist})

    return {
        "added": [_pos(a) for j, a in enumerate(after) if j not in took_a],
        "removed": [_pos(b) for i, b in enumerate(before) if i not in took_b],
        "moved": moved,
    }


def _delivered_index(stones, dv, tol_m: float = 0.45) -> int | None:
    """Which stone in the house is the one just thrown.

    The nearest stone of the delivery's own colour to where it was last seen.
    A shooter that rolled out, or a rest position taken from a house change
    rather than from the stone itself, can leave nothing close enough -- in
    which case we say so rather than pointing at a bystander.
    """
    best, best_d = None, tol_m
    for i, s in enumerate(stones):
        if _color(s) != dv.color:
            continue
        x, y = _xy(s)
        d = ((x - dv.rest_x_m) ** 2 + (y - dv.rest_y_m) ** 2) ** 0.5
        if d <= best_d:
            best, best_d = i, d
    return best


# A gap this many times the end's own median is long enough to hold a delivery
# we never saw. Judged against the end rather than a fixed number of seconds,
# because how long a team takes over a rock varies a lot between ends.
GAP_FACTOR = 1.7
# How many blanks are worth placing. The parity-and-gap argument below is
# decisive when an end is a rock or three short; it is worthless when half the
# end is missing, where every position would be invented. An end shorter than
# that is a detection failure rather than a few unseen rocks, and saying so at
# the end level beats scattering eight meaningless blanks through it.
MAX_FILL = 4


def _fill_short_end(seq, per_end: int, max_fill: int = MAX_FILL,
                    house_sizes=None):
    """Mark the deliveries an end is missing, and where they went.

    ``fit_end`` enforces strict alternation by dropping candidates, so by the
    time we see a sequence it always alternates and never repeats a colour --
    which means a missed delivery shows up only as an end that is *short*, not
    as a break in the pattern. Sixteen rocks are thrown; anything less was
    thrown and not seen.

    Where they go is constrained more tightly than it looks. Inserting a single
    blank between two alternating neighbours would break the alternation, so a
    mid-end insertion has to come in pairs; an odd remainder can only belong at
    the end. On the reference VOD that decides every case on its own: the two
    ends missing one rock can only be missing their last, and the two missing
    three have exactly one gap long enough to hold a pair.

    Timing alone would not have done it -- one end has three gaps at twice its
    median and is only one rock short, because teams stop to confer and to
    measure. The parity rule is what makes the guess honest.

    ``house_sizes`` -- how many stones the sheet held after each seen delivery,
    in order -- is harder evidence still, and is used first. The sheet cannot
    hold more stones than rocks have been thrown, so a house of three after
    what looked like the first delivery means two went by unseen before it.
    That is exactly the case the gap rule cannot reach: a rock missed before
    the first one we saw leaves no gap between deliveries at all, and the
    blanks for it used to be appended to the end of the end, where they named
    the wrong throwers for every shot in between.
    """
    short = per_end - len(seq)
    if not 0 < short <= max_fill:
        return seq
    seq = list(seq)

    if house_sizes:
        sizes = iter(house_sizes)
        p = 0
        while p < len(seq):
            color, dv = seq[p]
            if dv is not None:
                need = next(sizes, 0) - (p + 1)
                if need > 0 and p == 0:
                    # Before the first rock seen there is no neighbour to
                    # alternate against, so any number of blanks fits: the
                    # colours simply run backwards from the first rock's.
                    k = min(need, short)
                    lead = [(color if (k - i) % 2 == 0 else rules.other_color(color), None)
                            for i in range(k)]
                    seq[0:0] = lead
                    short -= k
                    p += k
                elif need > 0:
                    # Mid-end, pairs -- for the same parity reason as below: a
                    # lone blank between two alternating neighbours cannot exist.
                    pairs = min((need + 1) // 2, short // 2)
                    pair = [(color, None), (rules.other_color(color), None)]
                    seq[p:p] = pair * pairs
                    short -= 2 * pairs
                    p += 2 * pairs
            p += 1

    real = [(i, dv) for i, (_c, dv) in enumerate(seq) if dv is not None]
    if short >= 2 and len(real) >= 3:
        gaps = sorted(
            ((b.t_enter - a.t_enter, ib) for (_ia, a), (ib, b) in
             zip(real, real[1:])),
            reverse=True,
        )
        median = sorted(g for g, _ in gaps)[len(gaps) // 2]
        # Biggest gaps first, and apply them back to front so the earlier
        # insertion points keep their meaning.
        chosen = []
        for gap, at in gaps:
            if short < 2 or gap < GAP_FACTOR * median:
                break
            chosen.append(at)
            short -= 2
        for at in sorted(chosen, reverse=True):
            before = seq[at - 1][0]
            first = rules.other_color(before)
            seq[at:at] = [(first, None), (rules.other_color(first), None)]

    # Whatever is left was thrown after the last delivery we saw -- or was
    # never thrown at all, if the end was conceded. The charter can say which.
    for _ in range(short):
        seq.append((rules.other_color(seq[-1][0]) if seq else "red", None))
    return seq


def _with_placeholders(deliveries):
    """Deliveries in order, with a gap marked wherever alternation breaks.

    Yields ``(color, delivery_or_None)``. Two deliveries of the same colour
    running means one between them was never seen; naming it keeps every later
    thrower correct and gives the viewer an explicit blank to chart, which is
    the honest thing to show for a shot we could not read.
    """
    out = []
    prev = None
    for dv in deliveries:
        if prev is not None and dv.color == prev:
            out.append((rules.other_color(prev), None))
        out.append((dv.color, dv))
        prev = dv.color
    return out


def from_deliveries(deliveries, frames, settle_window_s: float = SETTLE_WINDOW_S):
    """Build the shot list from observed deliveries.

    This replaces inferring shots from how the house changed. That approach
    could not tell a thrown stone from one the players pushed back up the sheet
    between ends, so an end's "final" house was read from the staging that
    follows it -- on the reference VOD, seven minutes and eight stones after the
    last shot, scoring an end 5-0 that the club scored 0-1.

    Reading the house in the quiet window after each delivery settles fixes
    that, and the delivered stone names its own thrower, so no colour has to be
    inferred from the alternation rule.
    """
    from curling_score.detect.rest import stones_in_window

    deliveries = sorted(deliveries, key=lambda d: d.t_enter)
    frames = list(frames)
    if not deliveries or not frames:
        return []

    houses: dict[int, list] = {}
    for i, dv in enumerate(deliveries):
        # Read between this stone settling and the next one being thrown.
        start = dv.t_rest
        limit = deliveries[i + 1].t_enter if i + 1 < len(deliveries) else float("inf")
        end = min(start + settle_window_s, limit)
        window = [(t, d) for t, d in frames if start <= t <= end]
        if not window:
            window = [
                (t, d) for t, d in frames if start <= t <= start + settle_window_s
            ]
        houses[i] = stones_in_window(window) if window else []

    out: list[Shot] = []
    previous: list = []
    seen = 0
    plan = _fill_short_end(_with_placeholders(deliveries), C.STONES_PER_END,
                           house_sizes=[len(houses[i]) for i in range(len(deliveries))])
    for color, dv in plan:
        if len(out) >= C.STONES_PER_END:
            break
        if dv is None:
            # A delivery we never saw. Its house is unknown -- not empty -- and
            # the previous house is the last thing we actually observed, so the
            # next shot's delta is still measured against something real.
            out.append(
                Shot(
                    number=len(out) + 1,
                    color=color,
                    stones=[],
                    t_rest_s=float("nan"),
                    color_inferred=True,
                    missing=True,
                    confidence=0.0,
                    state_known=False,
                )
            )
            continue
        stones = houses[seen]
        seen += 1
        out.append(
            Shot(
                number=len(out) + 1,
                color=dv.color,
                stones=stones,
                t_rest_s=dv.t_rest,
                color_inferred=False,
                missing=False,
                confidence=1.0 if dv.came_to_rest else 0.8,
                delivery=dv,
                # An empty reading is normally a failure to see the house --
                # the stone just thrown has to be somewhere. The exception is a
                # stone that ran out of play, which legitimately leaves the
                # house exactly as it found it, empty included.
                state_known=bool(stones) or not dv.came_to_rest,
                house_delta=house_delta(previous, stones),
                delivered_stone_index=_delivered_index(stones, dv),
            )
        )
        previous = stones
    return out
