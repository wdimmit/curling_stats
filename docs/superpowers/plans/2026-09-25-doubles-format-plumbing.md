# Doubles: format object and plumbing (phases 1–2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the pipeline a `GameFormat` (fours or doubles) that sets every per-end count and throw rule, and carry the declared format from a submission all the way into `analyze()` and the timeline document. Four-player output does not change.

**Architecture:** New module `game/format.py` holds two frozen formats, FOURS and DOUBLES. Pipeline code takes an explicit `fmt=FOURS` keyword, so every existing caller stays fours untouched. Document-level code (overrides, trim) reads the format from the document's new `format` block, which is written only for non-fours documents. The service copies the path `sheet` already takes: Run → claim → worker → `analyze()`.

**Tech Stack:** Python 3.12, pytest, FastAPI TestClient (service tests). Nothing in the frontend changes in this plan.

**Spec:** `docs/superpowers/specs/2026-09-25-mixed-doubles-design.md` (phases 1 and 2). Read it first.

## Global Constraints

- Four-player timelines are byte-identical apart from `schema_version`, `processing_version` and `source.analysed_at`.
- A document with no `format` block, or with `schema_version` ≤ 6, is fours.
- Format names are exactly `"fours"` and `"doubles"`.
- Doubles labels read `"3rd end, B's second rock"`. The player names are `A` (team rocks 1 & 5) and `B` (team rocks 2–4).
- The playlist poller gets no doubles playlist. The only change to it is that a run's format comes from its title.
- Work in `/home/tcuser/src/curling_score/.claude/worktrees/doubles` on branch `doubles`. Another session shares the main checkout, so never commit from there.
- Run tests as `PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest -q <files>`. Run only the files named; the whole suite OOMs on this box.
- End every commit message with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **An old API or an old worker.** A worker that predates this plan sends no `format` on complete. For a doubles run the API must refuse that upload rather than store a fours reading. For a fours run it must still accept. (Task 6, `test_an_old_worker_cannot_complete_a_doubles_run`.)
2. **A doubles submission of a video already run as fours.** It must not reuse the fours run. (Task 6, `test_a_doubles_request_does_not_reuse_a_fours_run`.)
3. **A title that mentions doubles only in passing**, e.g. "Doubles bonspiel practice - Sheet 2 - Friday Night". The title rule matches the whole word `doubles` anywhere, so this reads as doubles. A form choice overrides it. (Task 5, `test_the_form_beats_the_title`.)
4. **A chart saved before this change, with overrides**, re-read by the new code. It must come out the same as before, since `apply_overrides` now reads the format from the document. (Task 4, `test_a_document_without_a_format_block_reorders_as_fours`.)
5. **Moving a shot in a doubles chart.** `_renumber` must use the doubles table and give no "lead" labels. (Task 4, `test_a_doubles_reorder_uses_the_doubles_table`.)

---

### Task 1: The format object

**Files:**
- Create: `src/curling_score/game/format.py`
- Modify: `src/curling_score/game/rules.py:8-53`
- Test: `tests/test_format.py` (new)

**Interfaces:**
- Produces: `format.GameFormat` with fields `name, stones_per_team, placed_per_team, throw_table, positions, blank_passes_hammer, swappable`; properties `delivered_per_team`, `delivered_per_end`, `max_score_per_end`; methods `throw_info(shot_number, swapped=False) -> rules.ThrowInfo`, `shot_label(end, shot_number, swapped=False) -> str`, `to_json() -> dict`. Module-level: `FOURS`, `DOUBLES`, `FORMATS: dict[str, GameFormat]`, `by_name(name) -> GameFormat` (raises `ValueError`), `of_document(doc) -> GameFormat` (never raises; defaults to FOURS).
- Produces: `rules.throw_info(shot_number, fmt=None)` and `rules.shot_label(end, shot_number, fmt=None)`. `None` means FOURS.

- [ ] **Step 1: Write the failing tests**

`tests/test_format.py`:

```python
import pytest

from curling_score.game import format as F
from curling_score.game import rules


class TestFours:
    @pytest.mark.parametrize("n", range(1, 17))
    def test_matches_the_arithmetic_it_replaces(self, n):
        k = (n + 1) // 2
        t = F.FOURS.throw_info(n)
        assert (t.has_hammer, t.team_stone_number, t.position_slot, t.rock_of_player) \
            == (n % 2 == 0, k, (k + 1) // 2, (k - 1) % 2 + 1)

    def test_counts(self):
        assert (F.FOURS.delivered_per_team, F.FOURS.delivered_per_end,
                F.FOURS.max_score_per_end, F.FOURS.placed_per_team) == (8, 16, 8, 0)

    def test_rules_still_answers_for_fours(self):
        assert rules.throw_info(5) == F.FOURS.throw_info(5)
        assert rules.shot_label(3, 5) == "3rd end, second's first rock"

    def test_a_swap_means_nothing_in_fours(self):
        assert F.FOURS.throw_info(1, swapped=True).position_slot == 1


class TestDoubles:
    @pytest.mark.parametrize("n, hammer, slot, rock", [
        (1, False, 1, 1), (2, True, 1, 1),
        (3, False, 2, 1), (4, True, 2, 1),
        (5, False, 2, 2), (6, True, 2, 2),
        (7, False, 2, 3), (8, True, 2, 3),
        (9, False, 1, 2), (10, True, 1, 2),
    ])
    def test_one_player_throws_first_and_last_the_other_the_three_between(
            self, n, hammer, slot, rock):
        t = F.DOUBLES.throw_info(n)
        assert (t.has_hammer, t.position_slot, t.rock_of_player) == (hammer, slot, rock)

    def test_counts(self):
        assert (F.DOUBLES.delivered_per_team, F.DOUBLES.delivered_per_end,
                F.DOUBLES.max_score_per_end, F.DOUBLES.placed_per_team) == (5, 10, 6, 1)

    @pytest.mark.parametrize("bad", [0, 11])
    def test_rejects_shot_numbers_outside_an_end(self, bad):
        with pytest.raises(ValueError, match="1..10"):
            F.DOUBLES.throw_info(bad)

    def test_a_swap_hands_the_first_and_last_to_the_other_player(self):
        first = F.DOUBLES.throw_info(1, swapped=True)
        assert (first.position_slot, first.rock_of_player) == (2, 1)
        assert F.DOUBLES.throw_info(3, swapped=True).position_slot == 1

    def test_labels(self):
        assert F.DOUBLES.shot_label(3, 7) == "3rd end, B's third rock"
        assert F.DOUBLES.shot_label(1, 9) == "1st end, A's second rock"
        assert F.DOUBLES.shot_label(1, 1, swapped=True) == "1st end, B's first rock"
        assert rules.shot_label(2, 4, fmt=F.DOUBLES) == "2nd end, B's first rock"


class TestLookup:
    def test_by_name(self):
        assert F.by_name("doubles") is F.DOUBLES
        assert F.by_name("fours") is F.FOURS
        with pytest.raises(ValueError, match="quads"):
            F.by_name("quads")

    @pytest.mark.parametrize("doc", [{}, {"format": None}, {"format": {}},
                                     {"format": {"name": "quads"}}, None])
    def test_a_document_that_does_not_say_is_fours(self, doc):
        assert F.of_document(doc) is F.FOURS

    def test_a_document_names_its_format(self):
        assert F.of_document({"format": F.DOUBLES.to_json()}) is F.DOUBLES

    def test_the_block_carries_what_a_reader_needs(self):
        assert F.DOUBLES.to_json() == {
            "name": "doubles", "stones_per_team": 6, "placed_per_team": 1,
            "delivered_per_team": 5, "delivered_per_end": 10,
            "positions": ["A", "B"], "throw_table": [1, 2, 2, 2, 1],
            "blank_passes_hammer": True, "swappable": True,
        }
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest -q tests/test_format.py`
Expected: FAIL. `ImportError: cannot import name 'format' from 'curling_score.game'`.

- [ ] **Step 3: Write `src/curling_score/game/format.py`**

```python
"""What kind of game is being played: how many stones, and who throws which.

Four-player curling and mixed doubles share the sheet, the stones and the
scoring. They differ in how an end is built (WCF R17). Doubles sets one stone
per team on the ice before the end and delivers five. One player throws a
team's first and last stones and the partner the three between. A blank end
hands the placement decision -- and so, in practice, the hammer -- to the team
that threw first, where in fours it keeps the hammer where it was.

Everything that used to read those numbers from ``geometry.constants`` takes
one of these instead. A timeline records which one built it, and a timeline
that does not say is fours, which is what every chart made before doubles
existed is.
"""

from dataclasses import dataclass

from curling_score.game.rules import ThrowInfo, ordinal
from curling_score.geometry import constants as C

_NTH = ("first", "second", "third")


@dataclass(frozen=True)
class GameFormat:
    name: str
    stones_per_team: int          # in play per team per end, placed ones included
    placed_per_team: int          # set on the ice before the end, never delivered
    throw_table: tuple[int, ...]  # a team's k-th delivered stone -> player slot
    positions: tuple[str, ...]    # player slot 1.. -> what a curler calls them
    blank_passes_hammer: bool     # does a blank end move the hammer?
    swappable: bool               # may a team's two players swap roles per end?

    @property
    def delivered_per_team(self) -> int:
        return self.stones_per_team - self.placed_per_team

    @property
    def delivered_per_end(self) -> int:
        return 2 * self.delivered_per_team

    @property
    def max_score_per_end(self) -> int:
        return self.stones_per_team

    def throw_info(self, shot_number: int, swapped: bool = False) -> ThrowInfo:
        """Who throws the end's ``shot_number``-th delivered stone.

        Teams alternate and the hammer team throws the even-numbered stones in
        both formats. ``swapped`` says this team's players traded roles this
        end, which only a two-player team can do: the slot then names the
        person, and ``rock_of_player`` still counts within the role.
        """
        if not 1 <= shot_number <= self.delivered_per_end:
            raise ValueError(
                f"shot_number must be 1..{self.delivered_per_end}, got {shot_number}"
            )
        k = (shot_number + 1) // 2
        role = self.throw_table[k - 1]
        slot = 3 - role if swapped and self.swappable else role
        return ThrowInfo(
            shot_number=shot_number,
            has_hammer=shot_number % 2 == 0,
            team_stone_number=k,
            position_slot=slot,
            rock_of_player=self.throw_table[:k].count(role),
        )

    def shot_label(self, end: int, shot_number: int, swapped: bool = False) -> str:
        """"3rd end, second's first rock" -- or, in doubles, "3rd end, B's third rock"."""
        t = self.throw_info(shot_number, swapped)
        who = self.positions[t.position_slot - 1]
        return f"{ordinal(end)} end, {who}'s {_NTH[t.rock_of_player - 1]} rock"

    def to_json(self) -> dict:
        return {
            "name": self.name,
            "stones_per_team": self.stones_per_team,
            "placed_per_team": self.placed_per_team,
            "delivered_per_team": self.delivered_per_team,
            "delivered_per_end": self.delivered_per_end,
            "positions": list(self.positions),
            "throw_table": list(self.throw_table),
            "blank_passes_hammer": self.blank_passes_hammer,
            "swappable": self.swappable,
        }


FOURS = GameFormat(
    name="fours",
    stones_per_team=C.STONES_PER_TEAM_PER_END,
    placed_per_team=0,
    throw_table=(1, 1, 2, 2, 3, 3, 4, 4),
    positions=tuple(C.POSITION_NAMES[i] for i in sorted(C.POSITION_NAMES)),
    blank_passes_hammer=False,
    swappable=False,
)
DOUBLES = GameFormat(
    name="doubles",
    stones_per_team=6,
    placed_per_team=1,
    throw_table=(1, 2, 2, 2, 1),
    positions=("A", "B"),
    blank_passes_hammer=True,
    swappable=True,
)
FORMATS = {f.name: f for f in (FOURS, DOUBLES)}


def by_name(name: str) -> GameFormat:
    """The format called ``name``. Raises on anything else: a typo in a form
    or a flag must not quietly become fours."""
    try:
        return FORMATS[name]
    except KeyError:
        raise ValueError(
            f"unknown game format {name!r}; expected one of {sorted(FORMATS)}"
        ) from None


def of_document(doc) -> GameFormat:
    """The format a timeline was built for. Never raises: a document that does
    not say, or names a format this code does not know, is read as fours --
    the one every older chart was built for."""
    block = doc.get("format") if isinstance(doc, dict) else None
    name = block.get("name") if isinstance(block, dict) else None
    return FORMATS.get(name, FOURS)
```

- [ ] **Step 4: Make `rules.throw_info` and `rules.shot_label` delegate**

In `src/curling_score/game/rules.py`, replace the `ThrowInfo` field comments and the two functions (lines 8–53) with the following. `ordinal` stays where it is.

```python
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
```

and, after `ordinal`:

```python
def shot_label(end: int, shot_number: int, fmt=None) -> str:
    """Render a shot the way a curler says it: "3rd end, second's first rock"."""
    from curling_score.game.format import FOURS

    return (fmt or FOURS).shot_label(end, shot_number)
```

The import is local because `format` imports `ThrowInfo` and `ordinal` from here.

- [ ] **Step 5: Run the new and existing rule tests**

Run: `PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest -q tests/test_format.py tests/test_rules.py`
Expected: all pass. `test_rules.py`'s existing table, the `throw_info(17)` rejection and the label strings must pass unchanged.

- [ ] **Step 6: Commit**

```bash
git add src/curling_score/game/format.py src/curling_score/game/rules.py tests/test_format.py
git commit -m "format: one table for who throws which rock, fours and doubles

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: A blank end passes the hammer in doubles

**Files:**
- Modify: `src/curling_score/game/rules.py:104-156`
- Test: `tests/test_rules.py` (append a class)

**Interfaces:**
- Produces: `rules.next_hammer(hammer, end_score, blank_passes=False)`, `rules.hammer_chain(first_hammer, end_scores, blank_passes=False)`, `rules.first_hammer_given(hammer, end_number, earlier_scores, blank_passes=False)`. Callers pass `fmt.blank_passes_hammer`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_rules.py`)

```python
BLANK = {"red": 0, "yellow": 0}


class TestDoublesHammer:
    """R17: after a blank the team that threw first decides the placement,
    and takes the house stone -- the hammer. After a score the team that did
    not score decides, exactly as fours hands it the hammer."""

    def test_a_blank_passes_the_hammer(self):
        assert rules.next_hammer("red", BLANK, blank_passes=True) == "yellow"

    def test_a_blank_still_keeps_it_in_fours(self):
        assert rules.next_hammer("red", BLANK) == "red"

    def test_a_score_goes_to_the_team_that_did_not_score(self):
        assert rules.next_hammer("red", {"red": 2, "yellow": 0}, blank_passes=True) == "yellow"
        assert rules.next_hammer("red", {"red": 0, "yellow": 1}, blank_passes=True) == "red"

    def test_the_chain(self):
        got = rules.hammer_chain("red", [BLANK, {"red": 0, "yellow": 1}, BLANK],
                                 blank_passes=True)
        assert got == ["red", "yellow", "red"]

    def test_stepping_back_undoes_the_chain(self):
        earlier = [BLANK, {"red": 0, "yellow": 1}]
        assert rules.first_hammer_given("red", 3, earlier, blank_passes=True) == "red"
        assert rules.hammer_chain("red", earlier, blank_passes=True) == ["red", "yellow"]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest -q tests/test_rules.py -k Doubles`
Expected: FAIL. `TypeError: next_hammer() got an unexpected keyword argument 'blank_passes'`.

- [ ] **Step 3: Implement**

In `rules.py`:

```python
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
```

In `hammer_chain`, add the `blank_passes: bool = False` parameter and call `next_hammer(cur, end, blank_passes)`.

In `first_hammer_given`, add the `blank_passes: bool = False` parameter and change the loop body to:

```python
        scorers = [c for c in COLORS if end.get(c, 0) > 0]
        # Whoever scored gave the hammer away, so before that end the hammer
        # sat with the team that did not score. In doubles a blank moved it
        # too, so stepping back over one moves it back.
        if scorers or blank_passes:
            cur = other_color(cur)
```

Update its docstring's first paragraph so it says a blank passes the hammer unchanged "in fours".

- [ ] **Step 4: Run the rule tests**

Run: `PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest -q tests/test_rules.py tests/test_format.py`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/game/rules.py tests/test_rules.py
git commit -m "rules: in doubles a blank end passes the hammer

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Per-end counts come from the format

**Files:**
- Modify: `src/curling_score/game/secondpass.py:47-70`, `src/curling_score/game/endcheck.py:28-75`, `src/curling_score/game/segment.py:22-30,102-140`, `src/curling_score/game/shots.py:488-546`
- Test: `tests/test_secondpass.py`, `tests/test_endcheck.py`, `tests/test_segment.py`, `tests/test_shots.py` (append)

**Interfaces:**
- Consumes: `format.FOURS`, `format.DOUBLES` (Task 1).
- Produces:
  - `secondpass.gaps_to_search(found, start_s, end_s, per_end=C.STONES_PER_END, per_team=C.STONES_PER_TEAM_PER_END)`
  - `endcheck.check(deliveries, long_gap_s=LONG_GAP_S, per_end=C.STONES_PER_END, per_team=C.STONES_PER_TEAM_PER_END)`; `EndCheck.expected: int` (defaults to 16) drives `confidence`
  - `segment.segment_games(samples, min_end_s=MIN_END_S)`
  - `shots.from_deliveries(deliveries, frames, settle_window_s=..., thrown_by=None, fmt=None)`, where `None` means FOURS

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_secondpass.py`:

```python
class TestDoublesCounts:
    def test_a_team_at_five_is_done_in_doubles(self):
        # Red has thrown its five; the only rock that can be missing is yellow's.
        found = [dv(c, 50 * i) for i, c in enumerate(
            ["red", "yellow", "red", "yellow", "red", "yellow", "red", "red"])]
        gaps = secondpass.gaps_to_search(found, 0.0, 900.0, per_end=10, per_team=5)
        assert [g.expected_color for g in gaps] == ["yellow"]
```

Append to `tests/test_endcheck.py`:

```python
class TestDoublesCounts:
    def test_ten_alternating_rocks_are_a_whole_doubles_end(self):
        r = endcheck.check(alternating(10), per_end=10, per_team=5)
        assert r.complete is True and r.problems == []
        assert r.confidence == 1.0

    def test_six_of_one_colour_is_too_many(self):
        r = endcheck.check(alternating(12), per_end=10, per_team=5)
        assert any("more than the 5" in p for p in r.problems)
```

Append to `tests/test_segment.py`:

```python
class TestTheFloorIsAParameter:
    def test_a_shorter_floor_keeps_a_shorter_end(self):
        # 200 s of play: too short for a sixteen-rock end, long enough for ten.
        samples = profile([(40, 3, 0), (2, 0, 0), (180, 0, 3)])
        four = segment.segment_games(samples)
        two = segment.segment_games(samples, min_end_s=10 * segment.MIN_DELIVERY_GAP_S)
        assert [e.house for g in four for e in g.ends] == ["bottom"]
        assert [e.house for g in two for e in g.ends] == ["top", "bottom"]
```

Append to `tests/test_shots.py`:

```python
class TestDoublesEnds:
    def _dv(self, color, t):
        return TestShotsFromDeliveries()._dv(color, t)

    def _frames(self, at):
        return TestShotsFromDeliveries()._frames(at)

    def _alternating(self, n):
        return [self._dv("yellow" if i % 2 == 0 else "red", 10 + 30 * i) for i in range(n)]

    def test_eight_rocks_in_is_two_short_of_a_doubles_end(self):
        from curling_score.game import format as F

        got = shots.from_deliveries(self._alternating(8), self._frames({0: []}),
                                    fmt=F.DOUBLES)
        assert len(got) == 10
        assert [s.missing for s in got[8:]] == [True, True]

    def test_the_same_eight_are_too_few_to_fill_in_fours(self):
        got = shots.from_deliveries(self._alternating(8), self._frames({0: []}))
        assert len(got) == 8

    def test_a_doubles_end_stops_at_ten(self):
        from curling_score.game import format as F

        got = shots.from_deliveries(self._alternating(12), self._frames({0: []}),
                                    fmt=F.DOUBLES)
        assert len(got) == 10
```

- [ ] **Step 2: Run them to verify they fail**

Run: `PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest -q tests/test_secondpass.py tests/test_endcheck.py tests/test_segment.py tests/test_shots.py -k "Doubles or Floor"`
Expected: FAIL with `unexpected keyword argument` for `per_end`, `min_end_s` and `fmt`.

- [ ] **Step 3: Implement**

`secondpass.py`, `gaps_to_search`:

```python
def gaps_to_search(found, start_s, end_s, per_end: int = C.STONES_PER_END,
                   per_team: int = C.STONES_PER_TEAM_PER_END):
    """Windows where the rules say a delivery must be, with its colour."""
    ...
    # A team that has already thrown its share cannot own a missing delivery.
    exhausted = {c for c in COLORS if thrown[c] >= per_team}
    ...
    if len(ds) < per_end and len(exhausted) == 1:
```

`endcheck.py`: add `expected: int = C.STONES_PER_END` as the last field of `EndCheck`, and make `confidence` return `min(1.0, seen / self.expected)`. Change `check` to:

```python
def check(deliveries, long_gap_s: float = LONG_GAP_S,
          per_end: int = C.STONES_PER_END,
          per_team: int = C.STONES_PER_TEAM_PER_END) -> EndCheck:
    ...
    if total != per_end:
        problems.append(f"saw {total} deliveries, an end has {per_end}")
    for color in COLORS:
        if thrown[color] > per_team:
            problems.append(
                f"{color} threw {thrown[color]}, more than the "
                f"{per_team} a team has"
            )
    ...
    return EndCheck(..., expected=per_end)
```

Pass `expected=per_end` in the existing `EndCheck(...)` constructor call at the bottom of `check`, keeping every other argument.

`segment.py`: `def segment_games(samples, min_end_s: float = MIN_END_S) -> list[GameSegment]:`. Replace the one use of `MIN_END_S` inside it (line 138, `if span(i0, i1) >= MIN_END_S`) with `min_end_s`. Keep the module constant and its comment; the default keeps fours as it is.

`shots.py`, `from_deliveries`:
- add `fmt=None` as the last keyword parameter;
- add `from curling_score.game.format import FOURS` beside the existing local import at the top of the body;
- set `per_end = (fmt or FOURS).delivered_per_end`;
- replace both uses of `C.STONES_PER_END` in the function (the `_fill_short_end(...)` argument and the `if len(out) >= ...` cap) with `per_end`;
- add to the docstring: "``fmt`` sets how many rocks an end holds; four-player unless told."

- [ ] **Step 4: Run the four test files in full**

Run: `PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest -q tests/test_secondpass.py tests/test_endcheck.py tests/test_segment.py tests/test_shots.py`
Expected: all pass, except tests that need the local reference VOD (`test_segment.py::TestOnTheReferenceVod`), which must behave exactly as they do on `main`. If in doubt, run the same command from the main checkout without `PYTHONPATH=src` and compare.

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/game/secondpass.py src/curling_score/game/endcheck.py \
        src/curling_score/game/segment.py src/curling_score/game/shots.py tests/
git commit -m "pipeline: an end's rock counts come from the game format

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: The timeline records its format

**Files:**
- Modify: `src/curling_score/timeline.py:15,88-128,211-215,230-281,324-363,412-460,560-600`
- Test: `tests/test_timeline.py`

**Interfaces:**
- Consumes: `format.FOURS/DOUBLES/of_document` (Task 1); `rules.hammer_chain(..., blank_passes)` and `first_hammer_given(..., blank_passes)` (Task 2).
- Produces:
  - `timeline.SCHEMA_VERSION == 7`
  - `build_end(number, house, start_s, end_s, shots, board_score=None, fmt=None)`
  - `build_game(index, start_s, end_s, ends, board=None, fmt=None)`
  - `build_document(..., window=None, processing_version=None, fmt=None)`, which writes `"format": fmt.to_json()` only when `fmt` is not FOURS
  - `_renumber(shots, end_number, patched, fmt=None)`
  - `apply_overrides` and `trim_to_start` read the format with `format.of_document(document)`

- [ ] **Step 1: Write the failing tests** (append to `tests/test_timeline.py`)

```python
from curling_score.game import format as F


class TestTheFormatIsRecorded:
    def test_a_fours_document_has_no_format_block(self):
        doc = timeline.build_document("v", "u", 1, 10.0, calibration={}, games=[])
        assert "format" not in doc
        assert doc["schema_version"] == 7

    def test_a_doubles_document_says_so(self):
        doc = timeline.build_document("v", "u", 1, 10.0, calibration={}, games=[],
                                      fmt=F.DOUBLES)
        assert doc["format"] == F.DOUBLES.to_json()

    def test_a_doubles_end_expects_ten_and_labels_by_player(self):
        end = timeline.build_end(1, "top", 0.0, 900.0,
                                 [shot(n, "yellow" if n % 2 else "red", [], t=10.0 * n)
                                  for n in range(1, 9)],
                                 fmt=F.DOUBLES)
        assert (end["shots_expected"], end["unplaced_shots"]) == (10, 2)
        third = end["shots"][2]
        assert (third["position"], third["thrower_slot"], third["rock_of_player"]) == ("B", 2, 1)
        assert third["label"] == "1st end, B's first rock"

    def test_a_doubles_game_passes_the_hammer_on_a_blank(self):
        ends = [
            {"number": 1, "score": {"red": 0, "yellow": 0}, "hammer": "red"},
            {"number": 2, "score": {"red": 0, "yellow": 1}, "hammer": "yellow"},
        ]
        game = timeline.build_game(0, 0.0, 900.0, ends, fmt=F.DOUBLES)
        assert game["hammer_consistent"] is True
        assert [e["hammer_expected"] for e in game["ends"]] == ["red", "yellow"]


class TestReorderingByFormat:
    def _doc(self, fmt_block=None):
        doc = TestMovingAShot()._doc()
        if fmt_block is not None:
            doc["format"] = fmt_block
        return doc

    def test_a_document_without_a_format_block_reorders_as_fours(self):
        got = timeline.apply_overrides(self._doc(), {"0.4.5": {"before": 1}})
        assert got["games"][0]["ends"][0]["shots"][2]["position"] == "lead"

    def test_a_doubles_reorder_uses_the_doubles_table(self):
        got = timeline.apply_overrides(self._doc(F.DOUBLES.to_json()),
                                       {"0.4.5": {"before": 1}, "0.4.6": {"before": 1}})
        shots_ = got["games"][0]["ends"][0]["shots"]
        assert [s["position"] for s in shots_] == ["A", "A", "B", "B", "B", "B"]
        assert shots_[2]["label"] == "4th end, B's first rock"

    def test_trimming_a_doubles_document_relabels_with_doubles_names(self):
        doc = {"format": F.DOUBLES.to_json(), "games": [{"index": 0, "ends": [
            {"number": 1, "start_s": 0.0, "end_s": 50.0, "shots_expected": 10,
             "shots": [{"number": 1, "label": "x"}]},
            {"number": 2, "start_s": 100.0, "end_s": 900.0, "shots_expected": 10,
             "shots": [{"number": n, "label": "x"} for n in range(1, 11)]},
        ]}]}
        got = timeline.trim_to_start(doc, 90.0)
        kept = got["games"][0]["ends"]
        assert len(kept) == 1 and kept[0]["number"] == 1
        assert kept[0]["shots"][8]["label"] == "1st end, A's second rock"
```

Also change `test_the_schema_version_says_the_shape_changed` to assert `== 7`, and add to its docstring: "7 adds the document-level `format` block, present only when the game is not four-player; a document without one is fours."

- [ ] **Step 2: Run them to verify they fail**

Run: `PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest -q tests/test_timeline.py`
Expected: the new tests FAIL (unexpected keyword `fmt`, a missing `format` key, schema 6).

- [ ] **Step 3: Implement**

At the top of `timeline.py`, add `from curling_score.game import format as format_mod` and set `SCHEMA_VERSION = 7`.

`build_end`: add a `fmt=None` parameter and `fmt = fmt or format_mod.FOURS` as the first body line. In the shot loop, replace `throw = s.throw` with `throw = fmt.throw_info(s.number)`, and the three derived fields with:

```python
                "thrower_slot": throw.position_slot,
                "position": fmt.positions[throw.position_slot - 1],
                "rock_of_player": throw.rock_of_player,
                "label": fmt.shot_label(number, s.number),
```

Then:

```python
        # Every rock an end holds was thrown. Where the list is shorter than
        # that, the missing ones could not even be placed, and the viewer has
        # to say so rather than present a short end as a whole one.
        "shots_expected": fmt.delivered_per_end,
        "unplaced_shots": max(0, fmt.delivered_per_end - len(shots)),
```

`build_game`: add a `fmt=None` parameter and `blank = (fmt or format_mod.FOURS).blank_passes_hammer`. Pass `blank_passes=blank` to both `rules.hammer_chain(...)` and `rules.first_hammer_given(...)`.

`build_document`: add a `fmt=None` parameter. Build the dict as today into a local `doc`, then:

```python
    fmt = fmt or format_mod.FOURS
    if fmt is not format_mod.FOURS:
        # Written only when it says something: a four-player timeline stays
        # byte-for-byte what it was, and a missing block reads as fours.
        doc["format"] = fmt.to_json()
    return doc
```

`_renumber(shots, end_number, patched, fmt=None)`: set `fmt = fmt or format_mod.FOURS`, use `t = fmt.throw_info(i + 1)`, `s["position"] = fmt.positions[t.position_slot - 1]` and `s["label"] = fmt.shot_label(end_number, i + 1)`.

`apply_overrides`: at the top of the body, `fmt = format_mod.of_document(document)`, and call `_renumber(ordered, end["number"], colour_set, fmt)`.

`trim_to_start`: after `document = deepcopy(document)`, set `fmt = format_mod.of_document(document)`. Use `s["label"] = fmt.shot_label(number, s["number"])`, and call `build_game(game["index"], kept[0]["start_s"], kept[-1]["end_s"], kept, fmt=fmt)`.

Leave the `C.STONES_PER_END` fallbacks in `settle_board_scores` and `trim_to_start` alone. They apply only to ends that lack `shots_expected`, which only schema-≤6 fours documents do.

- [ ] **Step 4: Run the timeline and service tests that read documents**

Run: `PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest -q tests/test_timeline.py tests/test_service_api.py tests/test_viewer_js.py`
Expected: all pass. `test_viewer_js.py` compares JS and Python renumbering for fours and must be unchanged.

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/timeline.py tests/test_timeline.py
git commit -m "timeline: schema 7 records a non-fours format; overrides and trims read it

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: `analyze()` takes a format; titles and the CLI supply one

**Files:**
- Modify: `src/curling_score/ingest/source.py:87-110`, `src/curling_score/analyze.py:178-525`, `src/curling_score/cli.py:48-60,591-617`
- Test: `tests/test_ingest_service.py`, `tests/test_cli_analyze.py`

**Interfaces:**
- Consumes: `format.by_name`, `FOURS` (Task 1); the `fmt` parameters from Tasks 3–4.
- Produces:
  - `source.format_from_title(title) -> str`, returning `"doubles"` or `"fours"`
  - `analyze(..., game_format: str | None = None)`, where `None` means read the title
  - CLI `analyze --format {fours,doubles}`, defaulting to the title

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ingest_service.py`:

```python
class TestFormatFromTitle:
    @pytest.mark.parametrize("title", [
        "3/19 - Sheet 1 - Thursday Mixed Doubles League 2025-2026",
        "Sunday open doubles - Sheet 3 - Open Doubles",
        "DOUBLES - Sheet 2 - Friday",
    ])
    def test_doubles_anywhere_is_doubles(self, title):
        assert source.format_from_title(title) == "doubles"

    @pytest.mark.parametrize("title", [
        "4/30 - Sheet 2 - Spring Skip's Choice League 2026", "", None,
        "Doubleshot Coffee Cup - Sheet 1 - Bonspiel",
    ])
    def test_everything_else_is_fours(self, title):
        assert source.format_from_title(title) == "fours"
```

Append to `tests/test_cli_analyze.py`, using the file's own `_run` helper (it returns `(rc, seen)`, where `seen` holds the kwargs the fake `analyze` received):

```python
class TestFormatFlag:
    def test_the_format_flag_reaches_analyze(self, monkeypatch, tmp_path):
        rc, seen = _run(monkeypatch, tmp_path, ["--format", "doubles"])
        assert rc == 0
        assert seen["game_format"] == "doubles"

    def test_no_flag_leaves_it_to_the_title(self, monkeypatch, tmp_path):
        rc, seen = _run(monkeypatch, tmp_path)
        assert rc == 0
        assert seen["game_format"] is None


def test_the_form_beats_the_title():
    from curling_score import analyze as A
    from curling_score.game import format as F

    assert A.resolve_format("fours", "x - Sheet 1 - Mixed Doubles") is F.FOURS
    assert A.resolve_format(None, "x - Sheet 1 - Mixed Doubles") is F.DOUBLES
    assert A.resolve_format(None, "x - Sheet 1 - League") is F.FOURS
```

- [ ] **Step 2: Run them to verify they fail**

Run: `PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest -q tests/test_ingest_service.py tests/test_cli_analyze.py`
Expected: FAIL. `format_from_title` doesn't exist, `--format` is unrecognised, and `resolve_format` doesn't exist.

- [ ] **Step 3: Implement**

`ingest/source.py`, after `league_from_title`:

```python
_DOUBLES_RE = re.compile(r"\bdoubles\b", re.IGNORECASE)


def format_from_title(title: str | None) -> str:
    """"doubles" when the stream's title says so, else "fours".

    The club names its doubles leagues in the title ("Thursday Mixed Doubles
    League"). Only a default: the submit form and the ``--format`` flag
    override it, and the pipeline checks the ice for the placed stones.
    """
    return "doubles" if _DOUBLES_RE.search(title or "") else "fours"
```

(`re` is already imported for `_SHEET_RE`; check and add the import if not.)

`analyze.py`: add `from curling_score.game import format as format_mod` to the imports, and a helper above `analyze`:

```python
def resolve_format(game_format: str | None, title: str | None):
    """The format to analyse as: the one asked for, else what the title says."""
    return format_mod.by_name(game_format or source.format_from_title(title))
```

In `analyze`'s signature, add `game_format: str | None = None` after `sheet=None`, and document it in the docstring beside `sheet`. Straight after `sheet = ...`, add:

```python
    fmt = resolve_format(game_format, info.title)
    progress(f"{info.title} ({info.duration_s / 3600:.2f} h, sheet {sheet}, {fmt.name})")
```

replacing the existing `progress(f"{info.title} ...")` line.

Then thread `fmt` through:
- `games = segment.segment_games(samples, min_end_s=fmt.delivered_per_end * segment.MIN_DELIVERY_GAP_S)`
- `gaps = secondpass.gaps_to_search(deliveries, end.start_s, end.end_s, per_end=fmt.delivered_per_end, per_team=fmt.delivered_per_team)`
- `audit = endcheck.check(deliveries, per_end=fmt.delivered_per_end, per_team=fmt.delivered_per_team)`
- `kept = fit.fit_end(fit.drop_clearing(...), paired=..., per_end=fmt.delivered_per_end, per_team=fmt.delivered_per_team)`
- `shots = shots_mod.from_deliveries(kept, seq, thrown_by=..., fmt=fmt)`
- `built = timeline.build_end(..., board_score=..., fmt=fmt)`
- the `progress(f"    {len(kept)}/16 deliveries ...")` line becomes `/{fmt.delivered_per_end}`
- the comment "An end holds sixteen deliveries" becomes "An end holds its format's deliveries"
- `timeline.build_game(...)`: find the call in `analyze` (`grep -n build_game src/curling_score/analyze.py`) and pass `fmt=fmt`
- `timeline.build_document(..., fmt=fmt)`

`cli.py`: pass `game_format=args.format` in `_analyze`'s call, and in the `analyze` subparser add:

```python
    p.add_argument("--format", choices=sorted(format_mod.FORMATS), default=None,
                   help="fours or doubles (default: read from the video's title)")
```

with `from curling_score.game import format as format_mod` among the imports.

- [ ] **Step 4: Run the tests**

Run: `PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest -q tests/test_ingest_service.py tests/test_cli_analyze.py tests/test_format.py tests/test_timeline.py`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/ingest/source.py src/curling_score/analyze.py src/curling_score/cli.py tests/
git commit -m "analyze: a game format, from the caller or the title, sets every per-end count

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: The service carries the format from submission to worker and back

**Files:**
- Modify: `src/curling_score/service/records.py:26-46,88-122`, `src/curling_score/service/api.py` (submit ~740-825, claim ~1288, complete ~1385-1417, reprocess ~1500, games list ~586), `src/curling_score/service/dedupe.py:63-74,107-131`, `src/curling_score/service/playlists.py:62-68`, `src/curling_score/service/worker.py:135-209`
- Test: `tests/test_service_api.py`, `tests/test_service_core.py`, `tests/test_worker.py`

**Interfaces:**
- Consumes: `source.format_from_title` (Task 5); `format.FORMATS` / `by_name` (Task 1); `analyze(game_format=...)` (Task 5).
- Produces:
  - `Run.format: str | None` and `Source.format: str | None`, where `None` (records written before this) means fours
  - claim job dict key `"format"`
  - `dedupe.find_reusable_run(runs, processing_version, start_s, game_format="fours")`
  - worker `meta.json` and complete payload key `"format"`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_service_api.py`. The `world` fixture's video is titled "4/30 - Sheet 2 - Spring League"; add a doubles video to the fixture's `yt` inside each test.

```python
DOUBLES_VID = "brMO74e6ZZU"


def add_doubles_video(w):
    w["yt"].add(VideoMeta(DOUBLES_VID,
                          "3/19 - Sheet 1 - Thursday Mixed Doubles League 2025-2026",
                          CLUB, 6001.0, "none", T0))


class TestFormat:
    def test_a_doubles_title_queues_a_doubles_run(self, world):
        add_doubles_video(world)
        assert submit(world, url=f"https://youtu.be/{DOUBLES_VID}").status_code == 201
        (run,) = world["repo"].runs_for_video(DOUBLES_VID)
        assert run.format == "doubles"

    def test_the_form_beats_the_title(self, world):
        add_doubles_video(world)
        submit(world, url=f"https://youtu.be/{DOUBLES_VID}", format="fours")
        (run,) = world["repo"].runs_for_video(DOUBLES_VID)
        assert run.format == "fours"

    def test_an_unknown_format_is_a_400(self, world):
        assert submit(world, format="quads").status_code == 400

    def test_the_claim_carries_the_format(self, world):
        add_doubles_video(world)
        submit(world, url=f"https://youtu.be/{DOUBLES_VID}")
        r = world["client"].post("/api/worker/claim", headers=WORKER,
                                 json={"worker_id": "home", "model_id": "m-abc"})
        assert r.json()["job"]["format"] == "doubles"

    def test_a_doubles_request_does_not_reuse_a_fours_run(self, world):
        submit(world)                                   # fours, from the title
        r = submit(world, format="doubles", ip="5.6.7.8")
        assert r.json()["reused"] is False
        assert sorted(run.format for run in world["repo"].runs_for_video(VID)) \
            == ["doubles", "fours"]

    def test_an_old_worker_cannot_complete_a_doubles_run(self, world):
        add_doubles_video(world)
        submit(world, url=f"https://youtu.be/{DOUBLES_VID}")
        c = world["client"]
        job = c.post("/api/worker/claim", headers=WORKER,
                     json={"worker_id": "home", "model_id": "m-abc"}).json()["job"]
        plan = c.post(f"/api/worker/jobs/{job['id']}/artifacts", headers=WORKER,
                      json={"worker_id": "home",
                            "files": [{"name": "timeline.json", "bytes": 10},
                                      {"name": "meta.json", "bytes": 5}],
                            "detcache": []}).json()
        for up in plan["uploads"]:
            world["store"].put_bytes(up["key"], json.dumps(sample_doc(1)).encode())
        r = c.post(f"/api/worker/jobs/{job['id']}/complete", headers=WORKER,
                   json={"worker_id": "home", "games": [], "detcache_digests": []})
        assert r.status_code == 409
        assert "format" in r.json()["detail"]

    def test_a_fours_run_still_completes_without_a_format(self, world):
        submit(world)
        work_through(world)                             # sends no "format"
        (run,) = world["repo"].runs_for_video(VID)
        assert run.status == "ready"
```

Append to `tests/test_service_core.py`, in `TestReusableRun`:

```python
    def test_a_run_of_another_format_is_not_reused(self):
        r = run(status="ready")
        r.format = "doubles"
        assert dedupe.find_reusable_run([r], "p+m", 3000.0) is None
        assert dedupe.find_reusable_run([r], "p+m", 3000.0, game_format="doubles") is r

    def test_a_run_from_before_formats_is_fours(self):
        r = run(status="ready")          # format left as None
        assert dedupe.find_reusable_run([r], "p+m", 3000.0, game_format="fours") is r
```

Append to `tests/test_worker.py`, in `TestProcessJob`:

```python
    def test_the_format_reaches_the_pipeline_and_comes_back(self, tmp_path):
        api = FakeApi([])
        seen = {}

        def analyze_fn(url, **kw):
            seen.update(kw)
            return {**fake_doc(), "format": {"name": "doubles"}}

        worker.process_job({**JOB, "format": "doubles"}, api, "home", root=tmp_path,
                           weights=None, out_dir=tmp_path / "out",
                           analyze_fn=analyze_fn, fetch_info=fake_info)
        assert seen["game_format"] == "doubles"
        assert json.loads(api.uploads["memory://meta.json"])["format"] == "doubles"
        assert api.completed[0]["format"] == "doubles"

    def test_a_fours_document_reports_fours(self, tmp_path):
        api = FakeApi([])
        worker.process_job(JOB, api, "home", root=tmp_path, weights=None,
                           out_dir=tmp_path / "out",
                           analyze_fn=lambda url, **kw: fake_doc(), fetch_info=fake_info)
        assert api.completed[0]["format"] == "fours"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest -q tests/test_service_api.py tests/test_service_core.py tests/test_worker.py -k "Format or format or another_format or before_formats"`
Expected: FAIL (`Run` has no `format`, the claim has no `"format"`, and so on).

- [ ] **Step 3: Implement**

`records.py`: add `format: str | None = None` to `Run` after `league`, and to `Source` after `league`. Add a one-line comment on each: `# "fours" or "doubles"; None is a record from before formats, read as fours`.

`dedupe.py`:

```python
def find_reusable_run(runs: list[Run], processing_version: str,
                      start_s: float | None, game_format: str = "fours") -> Run | None:
    """An existing run of the current version and format whose window holds ``start_s``.

    The format is part of the key: a doubles reading of a video that was run
    as fours is a different analysis, not a cached one.
    """
    ...
        if r.processing_version == processing_version and r.status in live
        and (r.format or "fours") == game_format
        and r.covers(start_s)
```

In `find_or_create_source`, add `format=run.format` to the `Source(...)` constructor.

`api.py`, submit. After the `sheet` parsing:

```python
        fmt_name = body.get("format")
        if fmt_name in (None, "", "auto"):
            fmt_name = None
        elif fmt_name not in format_mod.FORMATS:
            raise HTTPException(400, f"format must be one of {sorted(format_mod.FORMATS)}")
```

After `meta = validate_submission(...)`:

```python
        fmt_name = fmt_name or source.format_from_title(meta.title)
```

Pass `game_format=fmt_name` to `dedupe.find_reusable_run(...)` and `format=fmt_name` to `Run(...)`. Import `from curling_score.game import format as format_mod` at the top of `api.py`.

Claim: add `"format": run.format or "fours",` to the returned job dict.

Complete: after `run = repo.get_run(job.run_id)` and the `done` early return, add:

```python
        # A worker that predates formats sends none and builds fours whatever
        # it was asked; storing that as a doubles reading would be wrong in
        # every end. Old fours runs are unaffected.
        want = run.format or "fours"
        got = body.get("format") or "fours"
        if got != want:
            raise HTTPException(409, f"this run is {want}, but the worker built "
                                     f"{got} -- update the worker")
```

Reprocess: add `format=base.format if base else None,` to the `Run(...)`.

Games list (`/api/games`, ~line 586): add `"format": s.format or "fours",` to each source row. For the queued-run rows below it, add `"format": run.format or "fours",`.

`playlists.py`: add `format=source.format_from_title(meta.title),` to the `Run(...)`. Import `source` from `curling_score.ingest` if it isn't imported already.

`worker.py`, `process_job`: pass `game_format=job.get("format")` to `analyze_fn(...)`. Compute `fmt_name = (doc.get("format") or {}).get("name", "fours")`, and add `"format": fmt_name` to both `meta` and the `api.complete(...)` payload.

- [ ] **Step 4: Run the service test files in full**

Run: `PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest -q tests/test_service_api.py tests/test_service_core.py tests/test_worker.py tests/test_repo_contract.py tests/test_service_auth.py`
Expected: all pass. `test_repo_contract.py` round-trips records through the repo implementations; the new fields must survive it.

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/service/ tests/test_service_api.py tests/test_service_core.py tests/test_worker.py
git commit -m "service: a run's format travels from the submission to the worker and is checked on return

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Prove four-player output is unchanged, end to end

**Files:**
- Create: `scripts/doubles/compare_docs.py`
- Create: `scripts/doubles/fours_parity.sh`

**Interfaces:**
- Consumes: the whole branch at HEAD, and `main` at the branch point (`git merge-base doubles main`).
- Produces: a pass/fail report committed as `docs/superpowers/plans/2026-09-25-doubles-format-plumbing.parity.txt`.

- [ ] **Step 1: Write `scripts/doubles/compare_docs.py`**

```python
"""Are two timelines the same, apart from what changes on every run?

    python scripts/doubles/compare_docs.py a.json b.json

Prints the first few differing paths and exits 1 if there are any.
"""

import json
import sys

VOLATILE = (("schema_version",), ("processing_version",), ("source", "analysed_at"))


def strip(doc):
    for path in VOLATILE:
        d = doc
        for k in path[:-1]:
            d = d.get(k, {})
        d.pop(path[-1], None)
    return doc


def diff(a, b, path="", out=None, limit=20):
    out = [] if out is None else out
    if len(out) >= limit:
        return out
    if type(a) is not type(b):
        out.append(f"{path}: {a!r} != {b!r}")
    elif isinstance(a, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                out.append(f"{path}.{k}: only in {'b' if k not in a else 'a'}")
            else:
                diff(a[k], b[k], f"{path}.{k}", out, limit)
    elif isinstance(a, list):
        if len(a) != len(b):
            out.append(f"{path}: {len(a)} items != {len(b)}")
        for i, (x, y) in enumerate(zip(a, b)):
            diff(x, y, f"{path}[{i}]", out, limit)
    elif a != b:
        out.append(f"{path}: {a!r} != {b!r}")
    return out


def main(argv):
    a, b = (strip(json.load(open(p))) for p in argv[1:3])
    found = diff(a, b)
    for line in found:
        print(line)
    print("identical" if not found else f"{len(found)}+ differences")
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
```

- [ ] **Step 2: Write `scripts/doubles/fours_parity.sh`**

This runs on the laptop. `run` ships one commit's code to the worker and runs a full `analyze` of three warm-cache fours videos inside the worker image, with the code mounted over the installed package, into `out/<label>/`. `compare` diffs two labels.

```bash
#!/bin/bash
# Four-player parity on the GPU worker.
#   scripts/doubles/fours_parity.sh run LABEL SHA [--no-longview]
#   scripts/doubles/fours_parity.sh compare LABEL_A LABEL_B
set -euo pipefail
HOST=administrator@10.0.0.182
REMOTE=/data/wdd/curling/doubles-parity
VIDS="VXU9xwmugRg hOKZoeJNTpM AEqLTgM25Tc"
case $1 in
run)
  LABEL=$2 SHA=$3 EXTRA=${4:-}
  ssh $HOST "mkdir -p $REMOTE/code-$SHA $REMOTE/out"
  git archive $SHA src scripts/doubles | ssh $HOST "tar -x -C $REMOTE/code-$SHA"
  for vid in $VIDS; do
    ssh $HOST "cd /data/wdd/curling_score/deploy && docker compose -f docker-compose.worker.yml run --rm --no-deps \
      -v $REMOTE/code-$SHA:/code:ro -v $REMOTE/out:/parity -e PYTHONPATH=/code/src worker \
      sh -c 'curling-score analyze https://www.youtube.com/watch?v=$vid --out /parity/$LABEL/$vid $EXTRA > /parity/$LABEL-$vid.log 2>&1'"
    echo "$LABEL $vid done"
  done ;;
compare)
  A=$2 B=$3
  for vid in $VIDS; do
    echo "== $vid"
    ssh $HOST "python3 - $REMOTE/out/$A/$vid/timeline.json $REMOTE/out/$B/$vid/timeline.json" \
      < scripts/doubles/compare_docs.py || true
  done ;;
esac
```

The worker container's default cache root is `/data/cache`, the worker's own warm cache. That's where these videos' detections are. Each `analyze` takes about 4 minutes warm.

- [ ] **Step 3: Measure the noise floor first: base against itself**

```bash
BASE=$(git merge-base doubles main)
bash scripts/doubles/fours_parity.sh run base-a $BASE
bash scripts/doubles/fours_parity.sh run base-b $BASE
bash scripts/doubles/fours_parity.sh compare base-a base-b
```

Expected: `identical` for all three. If the side-view, broom or line models make two runs of the same code differ (GPU nondeterminism), record the differing paths. Then do steps 3 and 4 again with `--no-longview` as the fourth `run` argument (labels `base-nl-a`, `base-nl-b`, `head-nl`); that must come out identical.

- [ ] **Step 4: Base against head**

```bash
bash scripts/doubles/fours_parity.sh run head $(git rev-parse HEAD)
bash scripts/doubles/fours_parity.sh compare base-a head
```

Expected: `identical` for all three videos, or no differing paths beyond those step 3 showed between `base-a` and `base-b`. Any other difference is a bug in Tasks 1–6: fix it before going on.

- [ ] **Step 5: Commit the scripts and the report**

Save the script output to `docs/superpowers/plans/2026-09-25-doubles-format-plumbing.parity.txt`.

```bash
git add scripts/doubles/compare_docs.py scripts/doubles/fours_parity.sh \
        docs/superpowers/plans/2026-09-25-doubles-format-plumbing.parity.txt
git commit -m "doubles: four-player parity check, and its result on three videos

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## What this plan deliberately leaves to later plans

- The placement stage, seeding rock 1's house, and the placement-based hammer (spec phase 3). These wait for the phase 0 findings on how the club places stones.
- The role-swap override and every frontend change (spec phases 4–5).
- The broom (spec phase 6) and the deploy (spec phase 7).
- `misses.py` (CLI only) and `scoreboard.MAX_SCORE_PER_END`, which is looser than doubles' 6 but harmless.
- The spec's `Run.format_source` (form or title) is not stored. Nothing reads it yet, and the status page can show the format alone.
