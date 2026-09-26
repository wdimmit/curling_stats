# Doubles: the placement stage (phase 3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** In a doubles end, find the two positioned stones, drop everything that happened before they were placed, seed rock 1's house with them, and record the placement, hammer and power play. Validate against the 23 hand-marked ends.

**Architecture:**
- A new pure module, `game/placement.py`, does four things:
  - classifies a set of settled stones as a placement;
  - finds `t_placed`, the first moment the placement pattern holds still;
  - drops every candidate that settled before `t_placed`;
  - re-reads the arrangement from the still house just before rock 1.
- `analyze()` calls it only when the format places stones, so a four-player run takes exactly today's path.
- `shots.from_deliveries` takes the placed stones as rock 1's "before" house. `_fill_short_end` counts them as a base, so they no longer invent blanks.
- The timeline writes a per-end `placement` block and a game-level `power_plays`, plus a format check. These are written only for doubles, apart from a four-player warning that appears only when it fires.

**Tech Stack:** Python 3.12, pytest. No frontend change.

**Spec:** `docs/superpowers/specs/2026-09-25-mixed-doubles-design.md` (phase 3), as refined by the section "Design consequences" in `docs/superpowers/specs/2026-09-25-mixed-doubles-phase0.md`. Read both. Four deliberate refinements of the spec:
- **One block, not separate fields.** The spec's separate end fields (`placed`, `power_play`, `placement`) are one `placement` block here.
- **Exclusion is by time alone.** A later "hit and stick" can come to rest on a placed stone's spot, so position cannot identify a placement stone.
- **No `spread_s`.** The spec's `spread_s` is dropped with the "two stones settling close together in time" gate it measured, which phase 0 removed: ih59's first house stone arrived 96 s before its guard.
- **`power_play` is a side string.** The spec's `power_play: {color, side}` is just the side here: `"left"`, `"right"` or `null`, the house stone's side. Its colour is the hammer, so it is not repeated.

## Global Constraints

- **Four-player output is unchanged.** On the three parity videos, `head --no-longview` must be identical to the stored `base-nl-a`, and the full run must equal one of the stored base outcomes (see `docs/superpowers/plans/2026-09-25-doubles-format-plumbing.parity.txt`).
- **The placement stage runs only when `fmt.placed_per_team > 0`.** A four-player end gets no new keys.
- **Keys added only to doubles documents:** `end.placement`, `end.hammer_source`, `game.power_plays`, `format.check`. A four-player document gains `format_warning` only when the format check says it looks like doubles.
- **Gates** (from phase 0; sheet metres, origin at the tee, +y up-sheet, +x right facing down-sheet):
  - house stone: |x| ≤ 0.25, |y + 0.49| ≤ 0.25;
  - power-play house stone: ||x| − 1.27| ≤ 0.25, |y − 0.17| ≤ 0.25;
  - guard: 2.8 ≤ y ≤ 4.2, with |x| ≤ 0.35 for a centre guard, or on the house stone's side with ||x| − 0.9| ≤ 0.3 in a power play.
- **Power-play side** is the house stone's side: `"left"` if x < 0, else `"right"`.
- The hosted switch `DOUBLES_ENABLED` stays default off. Nothing is deployed.
- Work in `/home/tcuser/src/curling_score/.claude/worktrees/doubles-placement` on branch `doubles-placement`. Another session shares the main checkout.
- Tests: `PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest -q <files>`, named files only; the full suite OOMs.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Stage explicit paths only.

## Review Focus

1. **A placement slid down the sheet with a matching release** (ih59 end 1) must not become rock 1: it settles before `t_placed`. (Task 2, `test_a_slid_placement_stone_is_dropped`.)
2. **A power play set up in two steps.** The house stone goes behind the button first and is then moved out. The recorded placement must be the power play. (Task 1, `test_a_two_step_power_play_reads_as_the_power_play`.)
3. **A guard hidden half the time** by the player placing it must still let `find` fire as soon as it is set, not late enough to swallow rock 1. (Task 1, `test_a_guard_hidden_half_the_time_still_counts`.)
4. **A hit and stick later in the end, on a placed stone's spot,** is a real delivery and is kept. (Task 2, `test_a_later_rock_on_a_placed_spot_is_kept`.)
5. **An end whose placement is never seen** (stream joined late, panel dark) must fall back to today's behaviour: `placement: null`, hammer from the first shot, and a fill base of 2 so the unseen stones still don't invent blanks. (Tasks 2 and 3, `test_no_placement_in_doubles_assumes_two` and `test_a_doubles_end_with_no_placement_says_so`.)

---

### Task 1: Find and read the placement

**Files:**
- Create: `src/curling_score/game/placement.py`
- Test: `tests/test_placement.py` (new)

**Interfaces:**
- Consumes: `curling_score.detect.delivery._settled_stones(frames, t0, t1) -> list[(color, x, y)] | None` and `delivery.CHANGE_WINDOW_S` (6.0); `curling_score.detect.rest.stones_in_window(frames) -> list[Detection]`.
- Produces:
  - `placement.Placement(t_s: float, house: tuple[str, float, float], guard: tuple | None, power_play: str | None, seed: tuple = ())`, with properties `hammer -> str`, `stones -> list[tuple]` and `complete -> bool`.
  - `placement.classify(stones, t_s=0.0) -> Placement | None`. It accepts `(color, x, y)` tuples or objects with `.color/.x_m/.y_m`.
  - `placement.find(frames, t0, t1) -> Placement | None`.
  - `placement.read_before(frames, placed, t_first) -> Placement`.
  - Constants `SETTLE_S = 10.0`, `HOLD_S = 8.0`, `READ_S = 8.0` and `READ_GAP_S = 1.0`.

- [ ] **Step 1: Write the failing tests** (`tests/test_placement.py`)

```python
import pytest

from curling_score.detect.rocks import Detection
from curling_score.game import placement as P


def det(color, x, y):
    return Detection(color=color, x_m=x, y_m=y, x_px=0.0, y_px=0.0,
                     area_px=140.0, confidence=0.9)


def frames(t0, t1, stones, fps=10.0, hide=None):
    """(t, detections) from t0 to t1.

    ``stones`` is [(color, x, y, t_from, t_to)]; ``hide(t, i)`` true drops
    stone ``i`` from the frame at ``t`` (a player standing over it).
    """
    out = []
    n = int(round((t1 - t0) * fps))
    for k in range(n + 1):
        t = round(t0 + k / fps, 3)
        out.append((t, [det(c, x, y) for i, (c, x, y, a, b) in enumerate(stones)
                        if a <= t < b and not (hide and hide(t, i))]))
    return out


class TestClassify:
    def test_a_centre_placement(self):
        p = P.classify([("yellow", 0.01, -0.50), ("red", 0.00, 3.40)])
        assert (p.hammer, p.guard[0], p.power_play, p.complete) == ("yellow", "red", None, True)

    def test_a_power_play_on_the_left(self):
        p = P.classify([("red", -1.28, 0.16), ("yellow", -0.93, 3.56)])
        assert (p.hammer, p.power_play, p.guard[0]) == ("red", "left", "yellow")

    def test_a_power_play_on_the_right(self):
        p = P.classify([("yellow", 1.29, 0.13), ("red", 0.95, 3.41)])
        assert (p.hammer, p.power_play) == ("yellow", "right")

    def test_the_house_stone_alone_is_an_incomplete_placement(self):
        p = P.classify([("red", -0.02, -0.51)])
        assert p.hammer == "red" and p.complete is False and p.guard is None

    def test_a_guard_of_the_house_stones_colour_is_not_its_guard(self):
        p = P.classify([("red", 0.0, -0.5), ("red", 0.0, 3.4)])
        assert p.guard is None

    def test_a_centre_guard_does_not_guard_a_power_play(self):
        p = P.classify([("red", -1.28, 0.16), ("yellow", 0.0, 3.4)])
        assert p.power_play == "left" and p.guard is None

    def test_two_stones_on_house_spots_are_not_a_placement(self):
        assert P.classify([("red", 0.0, -0.5), ("yellow", 1.27, 0.17)]) is None

    def test_nothing_on_a_house_spot_is_not_a_placement(self):
        assert P.classify([("red", 0.5, 1.0), ("yellow", 0.0, 3.4)]) is None

    def test_detections_work_as_well_as_tuples(self):
        p = P.classify([det("yellow", 0.0, -0.49), det("red", 0.0, 3.4)])
        assert p.hammer == "yellow" and p.complete


class TestFind:
    def test_pushed_in_from_behind(self):
        fr = frames(0, 400, [("red", 0.0, -0.5, 100, 400), ("yellow", 0.0, 3.4, 130, 400)])
        p = P.find(fr, 0, 400)
        assert p.hammer == "red" and p.complete
        assert 125 <= p.t_s <= 130

    def test_a_house_stone_slid_long_before_its_guard(self):
        # ih59 end 1: the yellow house stone arrived at 166 s, the red guard at 262 s.
        fr = frames(100, 500, [("yellow", 0.01, -0.5, 166, 500), ("red", 0.01, 3.4, 262, 500)])
        p = P.find(fr, 100, 500)
        assert p.complete and 257 <= p.t_s <= 262

    def test_a_guard_hidden_half_the_time_still_counts(self):
        hide = lambda t, i: i == 1 and int(t) % 2 == 0
        fr = frames(0, 300, [("red", 0.0, -0.5, 50, 300), ("yellow", 0.0, 3.4, 80, 300)], hide=hide)
        p = P.find(fr, 0, 300)
        assert p.complete and p.t_s <= 82

    def test_a_guard_never_seen_gives_the_house_stone_alone(self):
        fr = frames(0, 200, [("red", 0.0, -0.5, 50, 200)])
        p = P.find(fr, 0, 200)
        assert p.hammer == "red" and not p.complete

    def test_a_stone_crossing_the_house_spot_is_not_a_placement(self):
        # A guard pushed up through the house sits on the house spot for 4 s.
        fr = frames(0, 200, [("yellow", 0.0, -0.49, 50, 54)])
        assert P.find(fr, 0, 200) is None

    def test_an_empty_house_has_no_placement(self):
        assert P.find(frames(0, 100, []), 0, 100) is None


class TestReadBefore:
    def test_a_two_step_power_play_reads_as_the_power_play(self):
        stones = [("red", 0.02, -0.5, 100, 150), ("yellow", 0.0, 3.4, 110, 150),
                  ("red", -1.28, 0.16, 152, 400), ("yellow", -0.93, 3.56, 152, 400)]
        fr = frames(0, 400, stones)
        found = P.find(fr, 0, 400)
        assert found.power_play is None           # the first arrangement to hold
        read = P.read_before(fr, found, t_first=200.0)
        assert (read.power_play, read.hammer, read.t_s) == ("left", "red", found.t_s)
        assert len(read.seed) == 2

    def test_an_unreadable_window_keeps_the_found_arrangement(self):
        fr = frames(0, 400, [("red", 0.0, -0.5, 100, 400), ("yellow", 0.0, 3.4, 130, 400)])
        found = P.find(fr, 0, 400)
        read = P.read_before(fr, found, t_first=5000.0)
        assert (read.hammer, read.guard, read.seed) == (found.hammer, found.guard, ())
```

- [ ] **Step 2: Run them to verify they fail**

Run: `PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest -q tests/test_placement.py`
Expected: FAIL. `ImportError: cannot import name 'placement'`.

- [ ] **Step 3: Write `src/curling_score/game/placement.py`**

```python
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

from dataclasses import dataclass

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


def _cxy(s):
    if isinstance(s, tuple):
        return s
    return (s.color, float(s.x_m), float(s.y_m))


@dataclass(frozen=True)
class Placement:
    t_s: float                      # when the arrangement first held still
    house: tuple                    # (color, x, y) of the house stone
    guard: tuple | None             # (color, x, y), or None when not seen
    power_play: str | None          # "left"/"right": the house stone's side
    seed: tuple = ()                # the still house just before rock 1

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


def classify(stones, t_s: float = 0.0) -> Placement | None:
    """The placement these settled stones show, or None.

    Exactly one stone on a house spot, centre or power play. The guard is the
    one stone of the other colour on the matching guard spot, or None when it
    is not seen. Anything more ambiguous is not a placement.
    """
    cxy = [_cxy(s) for s in stones or ()]
    houses = [((c, x, y), side) for c, x, y in cxy
              if (side := _house_side(x, y)) is not False]
    if len(houses) != 1:
        return None
    (house, side), = houses
    guards = [(c, x, y) for c, x, y in cxy if c != house[0] and _guards(x, y, side)]
    if len(guards) > 1:
        return None
    return Placement(t_s=t_s, house=house, guard=guards[0] if guards else None,
                     power_play=side)


def find(frames, t0: float, t1: float) -> Placement | None:
    """The first moment in [t0, t1] the placement holds still, or None.

    "Holds" means the same hammer colour and power-play side in every window
    for ``HOLD_S``. A complete placement, with its guard, is what is looked for.
    If the guard is never seen, the first held house stone alone is returned,
    with ``complete`` False.
    """
    frames = [(t, d) for t, d in frames if t0 <= t <= t1 + WINDOW_S]
    found = {True: None, False: None}
    run = {True: None, False: None}
    t = t0
    while t <= t1 and found[True] is None:
        settled = D._settled_stones(frames, t, t + WINDOW_S)
        p = classify(settled, t) if settled else None
        for complete in (True, False):
            ok = p is not None and (p.complete or not complete)
            key = (p.hammer, p.power_play) if ok else None
            start = run[complete]
            if ok and start is not None and start[1] == key:
                if t - start[0].t_s >= HOLD_S and found[complete] is None:
                    found[complete] = start[0]
            elif ok:
                run[complete] = (p, key)
            else:
                run[complete] = None
        t += STEP_S
    return found[True] or found[False]


def read_before(frames, placed: Placement, t_first: float) -> Placement:
    """The placement as it stood just before rock 1 came into view.

    The arrangement is re-read from the still house over the ``READ_S`` ending
    ``READ_GAP_S`` before ``t_first``. A power play set up in two steps shows
    its final shape there. ``seed`` is set to that house, for rock 1's house
    diff. When the re-read shows no placement, the found arrangement stands.
    """
    from curling_score.detect.rest import stones_in_window

    lo, hi = t_first - READ_GAP_S - READ_S, t_first - READ_GAP_S
    window = [(t, d) for t, d in frames if lo <= t <= hi]
    seed = tuple(stones_in_window(window)) if window else ()
    again = classify(seed, placed.t_s) if seed else None
    use = again if again is not None else placed
    return Placement(t_s=placed.t_s, house=use.house, guard=use.guard,
                     power_play=use.power_play, seed=seed)
```

- [ ] **Step 4: Run the tests**

Run: `PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest -q tests/test_placement.py`
Expected: all pass. If a `find` timing assertion is off by a step, check the arithmetic against `D.CHANGE_MIN_PRESENCE` (0.4 of a 6 s window, i.e. 2.4 s of presence) before touching a constant. The tests' bounds were set from that rule.

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/game/placement.py tests/test_placement.py
git commit -m "placement: find and read the two positioned stones of a doubles end

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Drop what came before the placement; seed rock 1's house

**Files:**
- Modify: `src/curling_score/game/placement.py` (add `exclude` and `fill_base`)
- Modify: `src/curling_score/game/shots.py` (`from_deliveries` at ~491, `_fill_short_end` at ~383)
- Test: `tests/test_placement.py`, `tests/test_shots.py`

**Interfaces:**
- Consumes: `placement.Placement`, `placement.SETTLE_S` (Task 1); `format.FOURS/DOUBLES` (`placed_per_team`).
- Produces:
  - `placement.exclude(deliveries, placed) -> (kept: list, dropped: list)`;
  - `placement.fill_base(placed, fmt) -> int`;
  - `shots.from_deliveries(deliveries, frames, settle_window_s=..., thrown_by=None, fmt=None, before=(), base=0)`;
  - `shots._fill_short_end(seq, per_end, max_fill=MAX_FILL, house_sizes=None, base=0)`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_placement.py`:

```python
from curling_score.detect.delivery import Delivery
from curling_score.game import format as F


def dv(color, t_rest, x=0.3, y=1.0):
    return Delivery(color=color, t_enter=t_rest - 8.0, t_rest=t_rest, entry_y_m=4.0,
                    rest_x_m=x, rest_y_m=y, travel_m=3.0)


PLACED = P.Placement(t_s=200.0, house=("yellow", 0.0, -0.5), guard=("red", 0.0, 3.4),
                     power_play=None)


class TestExclude:
    def test_nothing_is_dropped_without_a_placement(self):
        ds = [dv("red", 100.0), dv("yellow", 300.0)]
        assert P.exclude(ds, None) == (ds, [])

    def test_a_slid_placement_stone_is_dropped(self):
        slid = dv("yellow", 166.0, x=0.01, y=-0.5)      # ih59 end 1's house stone
        guard = dv("red", 205.0, x=0.0, y=3.4)          # settles as the pattern completes
        rock1 = dv("red", 260.0, x=0.2, y=2.9)
        kept, dropped = P.exclude([slid, guard, rock1], PLACED)
        assert kept == [rock1] and dropped == [slid, guard]

    def test_a_pre_game_slide_is_dropped(self):
        stray = dv("red", 120.0, x=1.8, y=-0.76)
        assert P.exclude([stray], PLACED) == ([], [stray])

    def test_a_later_rock_on_a_placed_spot_is_kept(self):
        # A hit and stick on the house stone, well after rock 1.
        stick = dv("red", 900.0, x=0.0, y=-0.5)
        assert P.exclude([stick], PLACED) == ([stick], [])


class TestFillBase:
    def test_a_complete_placement_is_two_stones(self):
        assert P.fill_base(PLACED, F.DOUBLES) == 2

    def test_an_unseen_guard_is_not_counted(self):
        alone = P.Placement(t_s=1.0, house=("red", 0.0, -0.5), guard=None, power_play=None)
        assert P.fill_base(alone, F.DOUBLES) == 1

    def test_no_placement_in_doubles_assumes_two(self):
        assert P.fill_base(None, F.DOUBLES) == 2

    def test_fours_places_nothing(self):
        assert P.fill_base(None, F.FOURS) == 0
```

Append to `tests/test_shots.py`:

```python
class TestPlacedStones:
    """Doubles: two stones sit in the house before rock 1 is thrown."""

    PLACED = [det("yellow", 0.0, -0.5), det("red", 0.0, 3.4)]

    def _dv(self, color, t, x, y):
        from curling_score.detect.delivery import Delivery

        return Delivery(color=color, t_enter=t, t_rest=t + 8.0, entry_y_m=4.0,
                        rest_x_m=x, rest_y_m=y, travel_m=3.0)

    def _frames(self, at):
        return TestShotsFromDeliveries()._frames(at)

    def _end(self, n, first=0):
        """n rocks, red first, from rock index ``first``; each house read holds
        the placed stones plus every rock thrown so far, seen or not."""
        rocks = [det("red" if i % 2 == 0 else "yellow", -1.0 + 0.2 * i, 1.0) for i in range(first + n)]
        dvs = [self._dv(r.color, 10 + 18 * i, r.x_m, r.y_m) for i, r in enumerate(rocks) if i >= first]
        at = {0: list(self.PLACED)}
        for i in range(first + n):
            at[10 + 18 * i + 8] = list(self.PLACED) + rocks[:i + 1]
        return dvs, self._frames(at)

    def test_rock_1_adds_only_itself(self):
        from curling_score.game import format as F

        dvs, fr = self._end(3)
        got = shots.from_deliveries(dvs, fr, fmt=F.DOUBLES, before=self.PLACED, base=2)
        added = got[0].house_delta["added"]
        assert len(added) == 1 and added[0]["color"] == "red"
        assert got[0].stones[got[0].delivered_stone_index].x_m == pytest.approx(-1.0)

    def test_the_placed_stones_add_no_leading_blanks(self):
        from curling_score.game import format as F

        dvs, fr = self._end(8)
        got = shots.from_deliveries(dvs, fr, fmt=F.DOUBLES, before=self.PLACED, base=2)
        assert len(got) == 10
        assert [s.missing for s in got[:2]] == [False, False]
        assert [s.missing for s in got[8:]] == [True, True]

    def test_without_the_base_they_would_invent_blanks(self):
        from curling_score.game import format as F

        dvs, fr = self._end(8)
        got = shots.from_deliveries(dvs, fr, fmt=F.DOUBLES)
        assert [s.missing for s in got[:2]] == [True, True]

    def test_a_missed_rock_1_still_gets_its_blank(self):
        from curling_score.game import format as F

        dvs, fr = self._end(7, first=1)      # rock 1 thrown, never seen
        got = shots.from_deliveries(dvs, fr, fmt=F.DOUBLES, before=self.PLACED, base=2)
        assert got[0].missing is True and got[1].missing is False
        assert got[0].color == "red" and got[1].color == "yellow"
```

(`det` and `pytest` are already imported at the top of `tests/test_shots.py`.)

- [ ] **Step 2: Run them to verify they fail**

Run: `PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest -q tests/test_placement.py tests/test_shots.py -k "Exclude or FillBase or PlacedStones"`
Expected: FAIL. `exclude`/`fill_base` don't exist, and `from_deliveries` has no `before`/`base`. `test_without_the_base_they_would_invent_blanks` may already pass, because it documents today's behaviour.

- [ ] **Step 3: Implement**

Append to `placement.py`:

```python
def exclude(deliveries, placed: Placement | None):
    """Split candidates into (kept, dropped) by the placement.

    Dropped: anything that settled before the placement had held for
    ``SETTLE_S``. Those are the placement stones themselves (slid, carried or
    set down) and whatever was slid before the game. By time alone: a later
    hit and stick can come to rest right on a placed stone's spot.
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
```

In `shots.py`, `_fill_short_end`: add `base: int = 0` as the last parameter. In its house-sizes loop, change `need = next(sizes, 0) - (p + 1)` to:

```python
                need = next(sizes, 0) - base - (p + 1)
```

Extend its docstring's house-sizes paragraph with: "``base`` counts stones that were on the sheet before any rock was thrown, which doubles places there."

In `from_deliveries`: add `before=(), base: int = 0` after `fmt=None`, and add to the docstring: "``before`` is the house rock 1 was thrown into (doubles' placed stones), and ``base`` how many of those the house reads count." Change `previous: list = []` to `previous: list = list(before)`, and pass `base=base` in the `_fill_short_end(...)` call.

- [ ] **Step 4: Run the tests**

Run: `PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest -q tests/test_placement.py tests/test_shots.py`
Expected: all pass, including every pre-existing `test_shots.py` test (the defaults keep fours as it was).

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/game/placement.py src/curling_score/game/shots.py tests/test_placement.py tests/test_shots.py
git commit -m "placement, shots: nothing before the placement is a delivery; rock 1 is diffed against the placed stones

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: The timeline records the placement, the power plays and the format check

**Files:**
- Modify: `src/curling_score/timeline.py` (`build_end` ~88, `build_game` ~235, `build_document` ~333; new `format_check`)
- Test: `tests/test_timeline.py`

**Interfaces:**
- Consumes: `placement.Placement` (Task 1); `format.FOURS/DOUBLES` (`placed_per_team`, `name`, `to_json`).
- Produces:
  - `timeline.build_end(..., fmt=None, placement=None)`. For a format that places stones it adds `end["placement"]` (dict or None) and `end["hammer_source"]` (`"placement"` or `"first_shot"`), and sets `end["hammer"]` from the placement when there is one.
  - `timeline.build_game(...)` adds, for such a format, `game["power_plays"] = {"red": [end numbers], "yellow": [...]}` and `game["power_play_problems"]` (a list of strings, empty when the rules were kept). A team may use one power play per game, and none in an extra end (end 9 or later of an 8-end game).
  - `timeline.format_check(games, fmt) -> dict` with keys `ends`, `median_offered`, `looks_like`, plus `placement_found` in doubles.
  - `timeline.build_document(..., fmt=None, check=None)`. A doubles document gets `format.check`; a fours document gets `format_warning` only when `check["looks_like"] != "fours"`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_timeline.py`)

```python
from curling_score.game import placement as PL


PLACED = PL.Placement(t_s=100.0, house=("yellow", 0.01, -0.5), guard=("red", 0.0, 3.4),
                      power_play=None)


class TestPlacementInTheTimeline:
    def _shots(self, n=4, first="red"):
        other = "yellow" if first == "red" else "red"
        return [shot(i, first if i % 2 else other, [], t=100.0 + 10 * i) for i in range(1, n + 1)]

    def test_a_doubles_end_records_its_placement_and_hammer(self):
        end = timeline.build_end(1, "top", 0.0, 900.0, self._shots(), fmt=F.DOUBLES,
                                 placement=PLACED)
        p = end["placement"]
        assert (p["hammer"], p["guard"]["color"], p["power_play"], p["complete"]) == \
               ("yellow", "red", None, True)
        assert (end["hammer"], end["hammer_source"]) == ("yellow", "placement")
        assert p["agrees_with_shots"] is True

    def test_a_first_shot_of_the_wrong_colour_disagrees(self):
        end = timeline.build_end(1, "top", 0.0, 900.0, self._shots(first="yellow"),
                                 fmt=F.DOUBLES, placement=PLACED)
        assert end["placement"]["agrees_with_shots"] is False
        assert end["hammer"] == "yellow"          # the placement, not the first shot

    def test_a_doubles_end_with_no_placement_says_so(self):
        end = timeline.build_end(1, "top", 0.0, 900.0, self._shots(), fmt=F.DOUBLES)
        assert end["placement"] is None
        assert (end["hammer"], end["hammer_source"]) == ("yellow", "first_shot")

    def test_a_fours_end_gains_no_keys(self):
        end = timeline.build_end(1, "top", 0.0, 900.0, self._shots())
        assert "placement" not in end and "hammer_source" not in end

    def test_power_plays_are_counted_per_team(self):
        pp = PL.Placement(t_s=1.0, house=("red", -1.28, 0.16), guard=("yellow", -0.93, 3.56),
                          power_play="left")
        ends = [timeline.build_end(n, "top", 0.0, 1.0, self._shots(), fmt=F.DOUBLES,
                                   placement=pp if n == 4 else PLACED) for n in range(1, 5)]
        game = timeline.build_game(0, 0.0, 1.0, ends, fmt=F.DOUBLES)
        assert game["power_plays"] == {"red": [4], "yellow": []}
        assert game["power_play_problems"] == []

    def test_two_power_plays_by_one_team_are_a_problem(self):
        pp = PL.Placement(t_s=1.0, house=("red", -1.28, 0.16), guard=("yellow", -0.93, 3.56),
                          power_play="left")
        ends = [timeline.build_end(n, "top", 0.0, 1.0, self._shots(), fmt=F.DOUBLES,
                                   placement=pp if n in (2, 9) else PLACED) for n in range(1, 10)]
        game = timeline.build_game(0, 0.0, 1.0, ends, fmt=F.DOUBLES)
        assert game["power_plays"]["red"] == [2, 9]
        assert len(game["power_play_problems"]) == 2   # a second one, and one in an extra end

    def test_a_fours_game_has_no_power_plays(self):
        game = timeline.build_game(0, 0.0, 1.0, [timeline.build_end(1, "top", 0.0, 1.0, [])])
        assert "power_plays" not in game and "power_play_problems" not in game


def _game(ends):
    return {"index": 0, "ends": ends}


class TestFormatCheck:
    def test_doubles_with_placements_looks_like_doubles(self):
        ends = [{"placement": {"hammer": "red"}, "deliveries_seen": 10, "thrown": {"red": 5, "yellow": 5}, "shots": []}] * 3
        c = timeline.format_check([_game(ends)], F.DOUBLES)
        assert (c["looks_like"], c["placement_found"], c["ends"]) == ("doubles", 3, 3)

    def test_doubles_with_no_placements_looks_like_fours(self):
        ends = [{"placement": None, "deliveries_seen": 16, "thrown": {"red": 8, "yellow": 8}, "shots": []}] * 3
        assert timeline.format_check([_game(ends)], F.DOUBLES)["looks_like"] == "fours"

    def test_fours_with_short_ends_looks_like_doubles(self):
        ends = [{"deliveries_seen": 10, "thrown": {"red": 5, "yellow": 5}, "shots": []}] * 4
        assert timeline.format_check([_game(ends)], F.FOURS)["looks_like"] == "doubles"

    def test_fours_with_full_ends_looks_like_fours(self):
        ends = [{"deliveries_seen": 16, "thrown": {"red": 8, "yellow": 8}, "shots": []}] * 4
        assert timeline.format_check([_game(ends)], F.FOURS)["looks_like"] == "fours"

    def test_a_doubles_document_carries_its_check(self):
        check = {"ends": 3, "median_offered": 10, "looks_like": "doubles", "placement_found": 3}
        doc = timeline.build_document("v", "u", 1, 10.0, calibration={}, games=[],
                                      fmt=F.DOUBLES, check=check)
        assert doc["format"]["check"] == check and "format_warning" not in doc

    def test_a_fours_document_is_warned_only_when_it_looks_like_doubles(self):
        quiet = timeline.build_document("v", "u", 1, 10.0, calibration={}, games=[],
                                        check={"ends": 4, "median_offered": 16, "looks_like": "fours"})
        assert "format_warning" not in quiet and "format" not in quiet
        loud = timeline.build_document("v", "u", 1, 10.0, calibration={}, games=[],
                                       check={"ends": 4, "median_offered": 10, "looks_like": "doubles"})
        assert "doubles" in loud["format_warning"] and "format" not in loud
```

(`F`, `shot` and `timeline` are already imported in `tests/test_timeline.py`.)

- [ ] **Step 2: Run them to verify they fail**

Run: `PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest -q tests/test_timeline.py -k "Placement or FormatCheck"`
Expected: FAIL (unexpected keyword `placement`/`check`; no `format_check`).

- [ ] **Step 3: Implement** (in `timeline.py`)

Add a helper above `build_end`:

```python
def _placement(placed, shots) -> dict | None:
    """A `placement.Placement` for output, or None when none was found."""
    if placed is None:
        return None
    pos = lambda s: None if s is None else {
        "color": s[0], "x": round(float(s[1]), 3), "y": round(float(s[2]), 3)}
    first = shots[0] if shots else None
    agrees = None
    if first is not None and not first.missing and not first.color_inferred:
        # The guard's team throws first, so a first rock of the hammer's colour
        # contradicts the placement read (or the shot list).
        agrees = first.color != placed.hammer
    return {"t_s": round(float(placed.t_s), 2), "hammer": placed.hammer,
            "house": pos(placed.house), "guard": pos(placed.guard),
            "power_play": placed.power_play, "complete": placed.complete,
            "agrees_with_shots": agrees}
```

In `build_end`, add `placement=None` after `fmt=None`, and document it: "``placement`` is doubles' positioned stones (`placement.Placement`); it names the hammer." Keep the returned dict as it is, but bind it to `out` instead of returning it directly. Then:

```python
    if fmt.placed_per_team:
        # The house stone's team has the hammer: observed directly, where
        # reading it from the first rock depends on having seen rock 1.
        out["placement"] = _placement(placement, shots)
        if placement is not None:
            out["hammer"] = placement.hammer
        out["hammer_source"] = "first_shot" if placement is None else "placement"
    return out
```

In `build_game`, bind the returned dict to `out` in the same way, then before returning:

```python
    if (fmt or format_mod.FOURS).placed_per_team:
        plays = {c: [] for c in rules.COLORS}
        for end in ends:
            p = end.get("placement") or {}
            if p.get("power_play"):
                plays[p["hammer"]].append(end["number"])
        out["power_plays"] = plays
        # R17: one power play per team per game, and none in an extra end.
        problems = []
        for c, used in plays.items():
            if len(used) > 1:
                problems.append(f"{c} used {len(used)} power plays (ends {used}); a team has one")
            late = [n for n in used if n > SCHEDULED_ENDS]
            if late:
                problems.append(f"{c} used a power play in an extra end ({late})")
        out["power_play_problems"] = problems
    return out
```

and near the other module constants:

```python
# A doubles game is scheduled for eight ends (R17); an end after that is extra.
SCHEDULED_ENDS = 8
```

Add, after `build_game`:

```python
# The format check (it flags, never overrides). A doubles analysis whose
# placement shows in fewer than this share of ends, or whose ends offer
# fourteen or more rocks, looks like fours.
DOUBLES_MIN_PLACED = 0.4
FOURS_MIN_OFFERED = 14
# A fours analysis looks like doubles when its ends offer twelve or fewer
# rocks and three in four offer six or fewer a side.
DOUBLES_MAX_OFFERED = 12
DOUBLES_MAX_A_SIDE = 6
DOUBLES_SHARE = 0.75


def format_check(games, fmt) -> dict:
    """Whether the ends look like the format they were analysed as."""
    ends = [e for g in games for e in g.get("ends", [])]
    offered = sorted(int(e.get("deliveries_seen", len(e.get("shots") or []))) for e in ends)
    median = offered[len(offered) // 2] if offered else 0
    out = {"ends": len(ends), "median_offered": median}
    if fmt.placed_per_team:
        found = sum(1 for e in ends if e.get("placement"))
        doubles = bool(ends) and found / len(ends) >= DOUBLES_MIN_PLACED \
            and median < FOURS_MIN_OFFERED
        return {**out, "placement_found": found,
                "looks_like": "doubles" if doubles else "fours"}
    small = sum(1 for e in ends
                if max((e.get("thrown") or {}).values(), default=99) <= DOUBLES_MAX_A_SIDE)
    doubles = bool(ends) and median <= DOUBLES_MAX_OFFERED \
        and small / len(ends) >= DOUBLES_SHARE
    return {**out, "looks_like": "doubles" if doubles else "fours"}
```

In `build_document`, add `check=None` after `fmt=None`, and replace the format-block lines with:

```python
    fmt = fmt or format_mod.FOURS
    if fmt is not format_mod.FOURS:
        # Written only when it says something: a four-player timeline stays
        # byte-for-byte what it was, and a missing block reads as fours.
        doc["format"] = fmt.to_json()
        if check is not None:
            doc["format"]["check"] = check
    elif check is not None and check.get("looks_like") != fmt.name:
        doc["format_warning"] = (
            f"analysed as {fmt.name}, but the ends look like "
            f"{check['looks_like']}: a median of {check['median_offered']} rocks "
            f"offered across {check['ends']} ends")
    return doc
```

- [ ] **Step 4: Run the tests**

Run: `PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest -q tests/test_timeline.py tests/test_service_api.py tests/test_viewer_js.py`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/timeline.py tests/test_timeline.py
git commit -m "timeline: a doubles end records its placement and hammer; games their power plays; documents a format check

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Wire the stage into `analyze()`, and write the acceptance check

**Files:**
- Modify: `src/curling_score/analyze.py` (the per-end loop, ~358-470; `run_up_from`'s docstring; the `build_document` call)
- Create: `scripts/doubles/placement_acceptance.py`
- Test: `tests/test_placement_acceptance.py` (new)

**Interfaces:**
- Consumes: from Tasks 1–2, `placement.find/read_before/exclude/fill_base/SETTLE_S`; `shots.from_deliveries(..., before=, base=)`; from Task 3, `timeline.build_end(..., placement=)`, `timeline.format_check`, `timeline.build_document(..., check=)`.
- Produces:
  - `scripts/doubles/placement_acceptance.py`, with `compare(marks: dict, docs: list[dict]) -> dict` and a CLI: `python scripts/doubles/placement_acceptance.py datasets/doubles/marks/placements.json tl1.json [tl2.json ...]`. It prints a per-end table and totals, and exits 1 on any hammer or power-play mismatch.
  - Each doubles end in analyze output gains `placement.candidates_dropped`.

- [ ] **Step 1: Write the failing test** (`tests/test_placement_acceptance.py`)

```python
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "placement_acceptance",
    Path(__file__).resolve().parents[1] / "scripts/doubles/placement_acceptance.py")
PA = importlib.util.module_from_spec(spec)
spec.loader.exec_module(PA)


MARKS = {"games": {"vid1": {"ends": [
    {"end": 1, "house_stone": "yellow", "guard_stone": "red", "first_thrower": "red", "power_play": None},
    {"end": 2, "house_stone": "red", "guard_stone": "yellow", "first_thrower": "yellow",
     "power_play": {"color": "red", "side": "left"}},
]}}}


def end(n, hammer, pp, first, shots=10):
    return {"number": n, "placement": {"hammer": hammer, "power_play": pp},
            "shots": [{"color": first, "missing": False, "color_inferred": False}]
                     + [{"color": "x", "missing": False, "color_inferred": False}] * (shots - 1)}


def doc(ends):
    return {"source": {"video_id": "vid1"}, "games": [{"index": 0, "ends": ends}]}


def test_a_perfect_read_scores_full_marks():
    got = PA.compare(MARKS, [doc([end(1, "yellow", None, "red"), end(2, "red", "left", "yellow")])])
    assert (got["ends"], got["hammer_ok"], got["power_play_ok"], got["first_ok"], got["ten_shots"]) \
        == (2, 2, 2, 2, 2)
    assert got["mismatches"] == []


def test_a_wrong_hammer_and_a_missed_power_play_are_reported():
    got = PA.compare(MARKS, [doc([end(1, "red", None, "red"), end(2, "red", None, "yellow", shots=11)])])
    assert (got["hammer_ok"], got["power_play_ok"], got["ten_shots"]) == (1, 1, 1)
    assert {m["end"] for m in got["mismatches"]} == {1, 2}


def test_an_end_with_no_placement_counts_as_a_miss():
    e = end(1, "yellow", None, "red")
    e["placement"] = None
    got = PA.compare(MARKS, [doc([e])])
    assert got["hammer_ok"] == 0 and got["mismatches"][0]["end"] == 1


def test_unmarked_videos_and_ends_are_ignored():
    got = PA.compare(MARKS, [{"source": {"video_id": "other"}, "games": [{"index": 0, "ends": [end(1, "red", None, "red")]}]}])
    assert got["ends"] == 0
```

- [ ] **Step 2: Run it to verify it fails**

Run: `PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest -q tests/test_placement_acceptance.py`
Expected: FAIL (the script does not exist).

- [ ] **Step 3: Write `scripts/doubles/placement_acceptance.py`**

```python
"""How a doubles timeline's placement reads compare with the hand marks.

    python scripts/doubles/placement_acceptance.py \\
        datasets/doubles/marks/placements.json tl1.json [tl2.json ...]

Per marked end:
- the hammer (the house stone's colour);
- the power-play side;
- rock 1's colour against the first thrower in the video, when rock 1 was
  seen;
- whether the end kept exactly ten shots.

Exits 1 on any hammer or power-play mismatch, which the placement stage exists
to get right.
"""

import json
import sys


def compare(marks: dict, docs: list) -> dict:
    out = {"ends": 0, "hammer_ok": 0, "power_play_ok": 0, "first_ok": 0,
           "first_seen": 0, "ten_shots": 0, "mismatches": [], "rows": []}
    for doc in docs:
        vid = doc["source"]["video_id"]
        want = {e["end"]: e for e in marks["games"].get(vid, {}).get("ends", [])}
        if not want or not doc.get("games"):
            continue
        for e in doc["games"][0]["ends"]:
            m = want.get(e["number"])
            if m is None:
                continue
            out["ends"] += 1
            p = e.get("placement") or {}
            want_pp = (m.get("power_play") or {}).get("side")
            hammer_ok = p.get("hammer") == m["house_stone"]
            pp_ok = bool(p) and p.get("power_play") == want_pp
            first = (e.get("shots") or [None])[0]
            seen = first is not None and not first["missing"] and not first["color_inferred"]
            first_ok = seen and first["color"] == m["first_thrower"]
            ten = len(e.get("shots") or []) == 10
            out["hammer_ok"] += hammer_ok
            out["power_play_ok"] += pp_ok
            out["first_seen"] += seen
            out["first_ok"] += first_ok
            out["ten_shots"] += ten
            row = {"video": vid, "end": e["number"], "hammer": p.get("hammer"),
                   "want_hammer": m["house_stone"], "power_play": p.get("power_play"),
                   "want_power_play": want_pp, "first": first["color"] if seen else None,
                   "want_first": m["first_thrower"], "shots": len(e.get("shots") or [])}
            out["rows"].append(row)
            if not (hammer_ok and pp_ok):
                out["mismatches"].append(row)
    return out


def main(argv) -> int:
    marks = json.load(open(argv[1]))
    got = compare(marks, [json.load(open(p)) for p in argv[2:]])
    for r in got["rows"]:
        flag = "" if r not in got["mismatches"] else "   <-- MISMATCH"
        print(f"{r['video']} e{r['end']}: hammer {r['hammer']}/{r['want_hammer']} "
              f"pp {r['power_play']}/{r['want_power_play']} "
              f"first {r['first']}/{r['want_first']} shots {r['shots']}{flag}")
    n = got["ends"]
    print(f"\n{n} marked ends: hammer {got['hammer_ok']}/{n}, power play "
          f"{got['power_play_ok']}/{n}, rock 1 colour {got['first_ok']}/{got['first_seen']} "
          f"where seen, ten shots {got['ten_shots']}/{n}")
    return 1 if got["mismatches"] else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
```

- [ ] **Step 4: Run the test**

Run: `PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest -q tests/test_placement_acceptance.py`
Expected: PASS.

- [ ] **Step 5: Wire the stage into `analyze()`**

In `src/curling_score/analyze.py`, add `placement` to the `curling_score.game` imports. In the per-end loop:

1. Right after `seq = sequence.detect_end(...)`:

```python
            # Doubles: every delivery of the end comes after its placement is
            # complete, so find that moment before anything is counted.
            placed = (placement.find(seq, from_s, end.end_s)
                      if fmt.placed_per_team else None)
```

2. Replace the `deliveries = [...]` block's result handling. Keep the comprehension, then add:

```python
            deliveries, before_placement = placement.exclude(deliveries, placed)
```

3. Change the second pass so its search starts after the placement, and so what it recovers is excluded the same way:

```python
            gap_from = (end.start_s if placed is None
                        else max(end.start_s, placed.t_s + placement.SETTLE_S))
            gaps = secondpass.gaps_to_search(
                deliveries, gap_from, end.end_s,
                per_end=fmt.delivered_per_end, per_team=fmt.delivered_per_team)
            recovered, recovered_early = placement.exclude(
                secondpass.search(seq, gaps, deliveries), placed)
```

4. In the `release.find_and_pair(...)` call, change `since=from_s` to:

```python
                since=from_s if placed is None else max(from_s, placed.t_s),
```

5. After `kept = fit.fit_end(...)` and `dropped = ...`:

```python
            # The arrangement as it stood when rock 1 was on its way: a power
            # play set up in two steps shows its final shape here.
            if placed is not None and kept:
                placed = placement.read_before(seq, placed, kept[0].t_enter)
```

6. In the `shots_mod.from_deliveries(...)` call, add:

```python
                before=placed.seed if placed is not None else (),
                base=placement.fill_base(placed, fmt),
```

7. In the `timeline.build_end(...)` call, add `placement=placed,`. Straight after it:

```python
            if built.get("placement") is not None:
                built["placement"]["candidates_dropped"] = (
                    len(before_placement) + len(recovered_early))
```

8. Name the placement in the per-end progress line. Just before the `progress(...)` call, compute:

```python
            p = built.get("placement")
            placed_note = "" if not p else (
                f"placement {p['hammer']}"
                + (f" power play {p['power_play']}" if p["power_play"] else "") + ", ")
```

Then put `+ placed_note` into the `progress(...)` argument, just before the parenthesised `(f"board says ..." if ... else ...)` part.

9. Before the `timeline.build_document(...)` call at the end of `analyze` (it passes `games=out_games`), compute `check = timeline.format_check(out_games, fmt)`, and pass `check=check`.

10. In `run_up_from`'s docstring, change "this panel holds nothing but this end's first stones" to "this panel holds nothing but this end's stones: in doubles its two placed stones, then its first deliveries".

Then check that nothing in the four-player path changed: every new branch tests `placed is None`, `fmt.placed_per_team`, or a `placement` key that fours never writes. The one fours-visible addition is `format_warning`, written only when the check fires.

Run: `PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -c "import curling_score.analyze"` (it must import cleanly), then `PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest -q tests/test_placement.py tests/test_placement_acceptance.py tests/test_shots.py tests/test_timeline.py tests/test_cli_analyze.py tests/test_worker.py`.
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/curling_score/analyze.py scripts/doubles/placement_acceptance.py tests/test_placement_acceptance.py
git commit -m "analyze: doubles ends drop what came before the placement and diff rock 1 against it; acceptance check

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Acceptance on the four doubles games, and four-player parity, on the worker

**Files:**
- Create: `docs/superpowers/plans/2026-09-25-doubles-placement.acceptance.txt`

**Interfaces:**
- Consumes: the branch HEAD; `scripts/doubles/fours_parity.sh` (on main); `scripts/doubles/placement_acceptance.py` (Task 4); `datasets/doubles/marks/placements.json`.
- Produces: the committed acceptance report.

**Worker facts:**
- The worker is `administrator@10.0.0.182`. Use `ssh -o BatchMode=yes`.
- The live worker runs through `docker compose -f docker-compose.worker.yml` in `/data/wdd/curling_score/deploy`. Never stop, restart or rebuild it, and never modify `/data/wdd/curling_score`.
- The doubles games are cached under the container path `/data/cache/doubles` (host `/data/wdd/curling-cache/doubles`), with proxies and detections from the baselines.
- Files the container writes are root-owned. Read them with `docker compose -f docker-compose.worker.yml exec -T worker cat <container path>`, or chmod them in a throwaway container.
- Launch every long run with the Bash tool's `run_in_background` and wait for the completion notice. Do not poll with sleep loops.

- [ ] **Step 1: Ship HEAD's code to the worker**

```bash
SHA=$(git rev-parse HEAD)
ssh -o BatchMode=yes administrator@10.0.0.182 "mkdir -p /data/wdd/curling/doubles-parity/code-$SHA"
git archive $SHA src | ssh -o BatchMode=yes administrator@10.0.0.182 "tar -x -C /data/wdd/curling/doubles-parity/code-$SHA"
```

- [ ] **Step 2: Analyse the four doubles games with HEAD** (about 15–20 minutes each, one after another)

For each `VID` in `brMO74e6ZZU n7ifEk4Zfl8 8J3r5FhFFd4 ih59IKFUHXk`:

```bash
ssh -o BatchMode=yes administrator@10.0.0.182 "cd /data/wdd/curling_score/deploy && docker compose -f docker-compose.worker.yml run --rm --no-deps -v /data/wdd/curling/doubles-parity/code-$SHA:/code:ro -e PYTHONPATH=/code/src worker sh -c 'curling-score -v analyze https://www.youtube.com/watch?v=$VID --format doubles --cache-root /data/cache/doubles --out /data/cache/doubles/out/p3-$VID > /data/cache/doubles/out/p3-$VID.log 2>&1'"
```

Then copy each timeline to the laptop scratchpad:

```bash
ssh ... "cd /data/wdd/curling_score/deploy && docker compose -f docker-compose.worker.yml exec -T worker cat /data/cache/doubles/out/p3-$VID/timeline.json" > <scratchpad>/p3-$VID.json
```

- [ ] **Step 3: Run the acceptance check**

```bash
python3 scripts/doubles/placement_acceptance.py datasets/doubles/marks/placements.json <scratchpad>/p3-*.json
```

Targets:
- hammer 23/23;
- power play 23/23;
- rock 1's colour right wherever rock 1 was seen;
- ten shots in every marked end except any where the board or video shows a rock was genuinely not thrown.

Also record, per game:
- the per-end "N/10 deliveries" lines from each log;
- `placement.candidates_dropped` per end;
- whether the board scores are kept (`scoreboard.scores_withheld` is null);
- `format.check`;
- how many rock-1 house diffs add a stone of the colour that did not throw (the target is 0).

- [ ] **Step 4: Four-player parity**

The base outputs from the phases 1–2 check are still on the worker, under `/data/wdd/curling/doubles-parity/out/{base-a,base-b,base-nl-a,base-nl-b}`.

```bash
bash scripts/doubles/fours_parity.sh run p3-head-nl $SHA --no-longview
bash scripts/doubles/fours_parity.sh compare base-nl-a p3-head-nl      # must be identical on all three
bash scripts/doubles/fours_parity.sh run p3-head $SHA
bash scripts/doubles/fours_parity.sh compare base-a p3-head
bash scripts/doubles/fours_parity.sh compare base-b p3-head             # one of the two must be identical per video
```

Before each compare, make the new output readable: `ssh ... "docker run --rm -v /data/wdd/curling/doubles-parity/out:/mnt --entrypoint chmod curling-worker:local -R a+rX /mnt"`. Also check that no fours timeline gained a `format_warning` key.

- [ ] **Step 5: Write and commit the report**

Write `docs/superpowers/plans/2026-09-25-doubles-placement.acceptance.txt` with:
- the acceptance table and totals, verbatim;
- the per-game facts from step 3;
- the parity results, verbatim.

If any target is missed, do not change pipeline code. Report DONE_WITH_CONCERNS with the specifics; the controller decides.

```bash
git add docs/superpowers/plans/2026-09-25-doubles-placement.acceptance.txt
git commit -m "doubles: placement stage acceptance on four games, and four-player parity

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Left to later plans

- The role-swap override and every viewer and site change (spec phases 4–5): labels, the report, the swap toggle, the power-play badge, and drawing the placed stones on rock 1.
- The broomless thrown line, **doubles only** (the user's decision on 2026-09-25), which gives doubles an aim line, a hack call and curl (spec phase 6).
- Reading a guard from the long camera when it is above the panel. Phase 0 never saw that happen.
- The 16-game four-player harness before any deploy (spec phase 7).
- Re-anchoring the exclusion to a two-step power play's final spots. The cut is still `t_s + SETTLE_S` from the first arrangement to hold, not from when the stones reached their power-play spots.
