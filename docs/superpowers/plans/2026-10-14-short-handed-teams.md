# Short-handed Teams Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Anyone signed in can mark a game's team as having played with three. Every page of that game then credits that team's rocks 3/3/2 (lead 1st–3rd, second 4th–6th, skip 7th & 8th).

**Architecture:**
- The mark is a list of colours on the game's `Source`, saved by the existing teams endpoint.
- `api.game_doc` writes it into the served document's `format` block and re-credits the rocks. Every chart, `/g/`, My shots and clips read the result.
- The four-player format gains a second throw table, `(1,1,1,2,2,2,4,4)`. Its slot numbers keep their four-player meaning, so the skip is still slot 4.
- The Python crediting (`timeline.py`) and the viewer's mirror (`core/timeline.mjs`) both honour the table. A parity test keeps them identical.

**Tech Stack:** Python 3 / FastAPI / pytest; React 19 viewer built by esbuild through `npm run build`; node for the JS tests (`tests/test_viewer_js.py`).

**Spec:** `docs/superpowers/specs/2026-10-14-short-handed-teams-design.md`

## Global Constraints

- **Worktree:** work only in `/home/tcuser/src/curling_score/.claude/worktrees/short-handed`, on branch `short-handed`.
  - Never write under `/home/tcuser/src/curling_score` outside that worktree; another session shares the main checkout.
- **Python tests:** run them from the worktree root with `/home/tcuser/src/curling_score/.venv/bin/python -m pytest <files> -q`. pytest's `pythonpath = ["src"]` puts the worktree's `src` first.
  - Never run the whole suite; it gets OOM-killed on this box (exit 137).
  - `tests/test_longview.py::TestAgainstHandMarkedCrossings` already fails on main. Deselect it if it is ever pulled in.
- **Frontend:**
  - Build only with `cd frontend && npm run build`, never esbuild directly.
  - The worktree's `frontend/node_modules` is a symlink to `/home/tcuser/src/curling_score/frontend/node_modules`.
  - Commit the built `src/curling_score/viewer/app.js`, `src/curling_score/service/static/site.js` and `frontend/.buildstamp.json` with the sources that produced them.
- **The three-player throw table** is exactly `(1, 1, 1, 2, 2, 2, 4, 4)`. Slot 3 (third) throws nothing for a short team.
- **Copy, verbatim:**
  - checkbox: `Played with three`
  - menu item: `Teams…`
  - report heading suffix: ` · 3 players`
  - play refusal: `that team played with three: lead, second or skip`
  - doubles refusal: `a doubles team is two players`
  - bad body: `short_handed is a list of colours: red, yellow`
  - My shots line: `This team played with three. Pick your rocks again`
  - select placeholder: `Pick your rocks again`
- **Commit messages** end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **Doubles:** never accepts or honours the mark.

## Review Focus

1. **A doubles game whose Source has `short_handed` set anyway** (written straight to the repo, or set before the format was known). Serving must ignore it and credit A/B as before. Test in Task 3.
2. **A rock never seen (a `missing` placeholder) on the short team.** It is still credited by its number, so the lead's third rock stays the lead's. Test in Task 2.
3. **A charter recolouring a rock from the full team to the short team, without moving it.** The thrower must follow the short table in both Python and the viewer. Tests in Task 2 (Python) and Task 4 (parity).
4. **Switching team in "I played…" after picking Third on the full team.** The pick must clear and Save must disable, rather than send slot 3 for the short team. Checked in Task 5's phone check; the server's 422 (Task 3) is the backstop.
5. **A "third" play saved before the team was marked.** It must be kept, credit no rocks, and say "Pick your rocks again" in My shots. Tests in Task 3 (API) and Task 4 (`playText`).

---

### Task 1: The three-player throw table

**Files:**
- Modify: `src/curling_score/game/format.py`
- Test: `tests/test_format.py`

**Interfaces:**
- Produces:
  - `GameFormat.short_throw_table: tuple[int, ...] | None` (FOURS `(1,1,1,2,2,2,4,4)`, DOUBLES `None`).
  - `GameFormat.throw_info(shot_number, swapped=False, short=False) -> ThrowInfo`.
  - `GameFormat.shot_label(end, shot_number, swapped=False, short=False) -> str`.
  - `GameFormat.to_json()` gains `"short_throw_table": [...]` only when the format has one.
  - `format.short_handed_of(doc) -> frozenset[str]`.

- [ ] **Step 1: Make the worktree**

```bash
cd /home/tcuser/src/curling_score
git worktree add -b short-handed .claude/worktrees/short-handed main
cd .claude/worktrees/short-handed
ln -s /home/tcuser/src/curling_score/frontend/node_modules frontend/node_modules
git log --oneline -1   # 2f537255 spec: short-handed teams ...
```

- [ ] **Step 2: Write the failing tests.** Append to `tests/test_format.py`:

```python
class TestShortHanded:
    """A team of three throws its eight rocks 3/3/2: lead, second, skip. Slots
    keep their four-player meaning, so the skip is still slot 4 and slot 3
    throws nothing."""

    @pytest.mark.parametrize("k, slot, rock", [
        (1, 1, 1), (2, 1, 2), (3, 1, 3),
        (4, 2, 1), (5, 2, 2), (6, 2, 3),
        (7, 4, 1), (8, 4, 2),
    ])
    def test_the_team_stones_go_three_three_two(self, k, slot, rock):
        for n in (2 * k - 1, 2 * k):          # either team's k-th rock
            t = F.FOURS.throw_info(n, short=True)
            assert (t.team_stone_number, t.position_slot, t.rock_of_player) == (k, slot, rock)
            assert t.has_hammer == (n % 2 == 0)

    def test_labels(self):
        assert F.FOURS.shot_label(3, 5, short=True) == "3rd end, lead's third rock"
        assert F.FOURS.shot_label(3, 7, short=True) == "3rd end, second's first rock"
        assert F.FOURS.shot_label(3, 13, short=True) == "3rd end, skip's first rock"

    def test_a_full_team_is_unchanged(self):
        for n in range(1, 17):
            assert F.FOURS.throw_info(n, short=False) == F.FOURS.throw_info(n)

    def test_doubles_has_no_team_of_three(self):
        assert F.DOUBLES.short_throw_table is None
        assert F.DOUBLES.throw_info(3, short=True) == F.DOUBLES.throw_info(3)
        assert "short_throw_table" not in F.DOUBLES.to_json()

    def test_the_fours_block_carries_the_table(self):
        assert F.FOURS.to_json()["short_throw_table"] == [1, 1, 1, 2, 2, 2, 4, 4]


class TestShortHandedOf:
    """Which colours played with three, as api.game_doc writes them into the
    document's format block."""

    def test_read_from_the_format_block(self):
        doc = {"format": {**F.FOURS.to_json(), "short_handed": ["red"]}}
        assert F.short_handed_of(doc) == frozenset({"red"})

    @pytest.mark.parametrize("doc", [
        {}, None, [], {"format": None}, {"format": {"name": "fours"}},
        {"format": {"name": "fours", "short_handed": "red"}},
        {"format": {"name": "fours", "short_handed": {"red": True}}},
    ])
    def test_anything_else_is_nobody(self, doc):
        assert F.short_handed_of(doc) == frozenset()

    def test_only_colours_count(self):
        doc = {"format": {"name": "fours", "short_handed": ["red", "blue", 3, None, ["yellow"]]}}
        assert F.short_handed_of(doc) == frozenset({"red"})

    def test_doubles_ignores_it(self):
        doc = {"format": {**F.DOUBLES.to_json(), "short_handed": ["red"]}}
        assert F.short_handed_of(doc) == frozenset()
```

- [ ] **Step 3: Run them and check they fail**

Run: `/home/tcuser/src/curling_score/.venv/bin/python -m pytest tests/test_format.py -q`
Expected: FAIL. You get `TypeError: ... unexpected keyword argument 'short'` and `AttributeError: ... 'short_throw_table'` / `'short_handed_of'`, and the existing classes still pass.

- [ ] **Step 4: Implement.** In `src/curling_score/game/format.py`:

Add the field after `line_without_broom`:

```python
    line_without_broom: bool = False
    # Who throws a team's k-th stone when the team is three players (WCF R4:
    # the first two deliver three each, the last two), in the full team's
    # slots: the skip is still the last slot, and the slot nobody plays
    # throws nothing -- so a play saved as "skip" stays the skip's, and the
    # report's position rows still line up. None where a team cannot be
    # short. Which teams were is not the format's to know: the game's record
    # says (Source.short_handed), and api.game_doc writes it into the
    # document's format block (`short_handed_of`).
    short_throw_table: tuple[int, ...] | None = None
```

Replace `throw_info` and `shot_label`:

```python
    def throw_info(self, shot_number: int, swapped: bool = False,
                   short: bool = False) -> ThrowInfo:
        """Who throws the end's ``shot_number``-th delivered stone.

        Teams alternate and the hammer team throws the even-numbered stones in
        both formats. ``swapped`` says this team's players traded roles this
        end, which only a two-player team can do: the slot then names the
        person, and ``rock_of_player`` still counts within the role.
        ``short`` says this team played with three, which only a format with
        a three-player table honours.
        """
        if not 1 <= shot_number <= self.delivered_per_end:
            raise ValueError(
                f"shot_number must be 1..{self.delivered_per_end}, got {shot_number}"
            )
        k = (shot_number + 1) // 2
        table = self.short_throw_table if short and self.short_throw_table else self.throw_table
        role = table[k - 1]
        slot = 3 - role if swapped and self.swappable else role
        return ThrowInfo(
            shot_number=shot_number,
            has_hammer=shot_number % 2 == 0,
            team_stone_number=k,
            position_slot=slot,
            rock_of_player=table[:k].count(role),
        )

    def shot_label(self, end: int, shot_number: int, swapped: bool = False,
                   short: bool = False) -> str:
        """"3rd end, second's first rock" -- or, in doubles, "3rd end, B's third rock"."""
        t = self.throw_info(shot_number, swapped, short)
        who = self.positions[t.position_slot - 1]
        return f"{ordinal(end)} end, {who}'s {_NTH[t.rock_of_player - 1]} rock"
```

In `to_json`, after `"swappable": self.swappable,` add:

```python
            **({"short_throw_table": list(self.short_throw_table)}
               if self.short_throw_table else {}),
```

In `FOURS = GameFormat(...)`, after `swappable=False,` add `short_throw_table=(1, 1, 1, 2, 2, 2, 4, 4),`.

At the end of the module add:

```python
def short_handed_of(doc) -> frozenset[str]:
    """The colours that played with three, as api.game_doc wrote them into
    the document's format block. Never raises: a format with no three-player
    table, a block that does not say, or one not shaped as expected is
    nobody."""
    block = doc.get("format") if isinstance(doc, dict) else None
    if not isinstance(block, dict) or not of_document(doc).short_throw_table:
        return frozenset()
    said = block.get("short_handed")
    if not isinstance(said, list):
        return frozenset()
    return frozenset(c for c in said if isinstance(c, str) and c in rules.COLORS)
```

- [ ] **Step 5: Run them and check they pass.** Also run the formats' neighbours.

Run: `/home/tcuser/src/curling_score/.venv/bin/python -m pytest tests/test_format.py tests/test_rules.py tests/test_timeline.py tests/test_plays.py -q`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/curling_score/game/format.py tests/test_format.py
git commit -m "format: a three-player throw table for fours (1,1,1,2,2,2,4,4), slots kept

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Crediting a short team in the timeline

**Files:**
- Modify: `src/curling_score/timeline.py` (`_assign_throwers` ~595, `_renumber` ~613, `apply_overrides` ~646, new `assign_short_handed` after `apply_overrides`)
- Test: `tests/test_timeline.py`

**Interfaces:**
- Consumes: `fmt.throw_info(n, swapped=, short=)`, `fmt.shot_label(..., short=)` and `format_mod.short_handed_of(doc)` from Task 1.
- Produces:
  - `timeline.assign_short_handed(document: dict) -> dict`. It edits in place and returns the same dict, reading the colours from `document["format"]["short_handed"]`.
  - `apply_overrides` honours the block when it renumbers, recolours or swaps.

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_timeline.py`; `_fours_doc` and `F` are already defined above:

```python
def _short(doc, colours=("red",)):
    """`doc` with the format block api.game_doc writes for a team of three."""
    doc["format"] = {**F.FOURS.to_json(), "short_handed": list(colours)}
    return doc


def _who(doc, color):
    return [(s["number"], s["position"], s["rock_of_player"])
            for s in doc["games"][0]["ends"][0]["shots"] if s["color"] == color]


THREE = [(1, "lead", 1), (3, "lead", 2), (5, "lead", 3), (7, "second", 1),
         (9, "second", 2), (11, "second", 3), (13, "skip", 1), (15, "skip", 2)]


class TestShortHanded:
    """A team of three, credited 3/3/2 wherever the document says so."""

    def test_the_short_team_is_credited_three_three_two(self):
        doc = timeline.assign_short_handed(_short(_fours_doc()))
        assert _who(doc, "red") == THREE
        shots = doc["games"][0]["ends"][0]["shots"]
        assert shots[4]["label"] == "2nd end, lead's third rock"
        assert (shots[4]["thrower_slot"], shots[12]["thrower_slot"]) == (1, 4)

    def test_the_full_team_is_left_as_it_was(self):
        before = [s for s in _fours_doc()["games"][0]["ends"][0]["shots"] if s["color"] == "yellow"]
        doc = timeline.assign_short_handed(_short(_fours_doc()))
        assert [s for s in doc["games"][0]["ends"][0]["shots"] if s["color"] == "yellow"] == before

    def test_both_teams_can_be_three(self):
        doc = timeline.assign_short_handed(_short(_fours_doc(), ("red", "yellow")))
        assert _who(doc, "red") == THREE
        assert [p for _, p, _ in _who(doc, "yellow")] == ["lead"] * 3 + ["second"] * 3 + ["skip"] * 2

    def test_a_document_that_does_not_say_is_untouched(self):
        assert timeline.assign_short_handed(_fours_doc()) == _fours_doc()

    def test_a_rock_never_seen_is_credited_by_its_number(self):
        doc = _short(_fours_doc())
        doc["games"][0]["ends"][0]["shots"][4]["missing"] = True     # red's 3rd rock
        doc = timeline.assign_short_handed(doc)
        assert doc["games"][0]["ends"][0]["shots"][4]["position"] == "lead"

    def test_a_moved_rock_follows_its_new_number_by_the_short_table(self):
        doc = timeline.assign_short_handed(_short(_fours_doc()))
        shots = timeline.apply_overrides(doc, {"0.2.6": {"before": 5}})["games"][0]["ends"][0]["shots"]
        moved, red5 = shots[4], shots[5]
        assert (moved["id"], moved["color"], moved["position"], moved["rock_of_player"]) \
            == (6, "yellow", "second", 1)
        assert (red5["id"], red5["number"], red5["position"], red5["rock_of_player"]) \
            == (5, 6, "lead", 3)
        assert red5["label"] == "2nd end, lead's third rock"

    def test_a_rock_recoloured_onto_the_short_team_is_credited_by_its_table(self):
        doc = timeline.assign_short_handed(_short(_fours_doc()))
        shots = timeline.apply_overrides(doc, {"0.2.6": {"color": "red"}})["games"][0]["ends"][0]["shots"]
        assert (shots[5]["color"], shots[5]["position"], shots[5]["rock_of_player"]) == ("red", "lead", 3)

    def test_the_leads_report_and_clips_get_all_three_rocks(self):
        from curling_score.service import gameclips
        doc = timeline.assign_short_handed(_short(_fours_doc()))
        assert [r["name"] for r in gameclips.player_rocks(doc, "red", 1)] == ["0.2.1", "0.2.3", "0.2.5"]
        cut = timeline.for_player(doc, "red", 1)["games"][0]["ends"][0]["shots"]
        assert [s["number"] for s in cut if "track" in s] == [1, 3, 5]

    def test_nobody_is_third_on_a_team_of_three(self):
        doc = timeline.assign_short_handed(_short(_fours_doc()))
        cut = timeline.for_player(doc, "red", 3)["games"][0]["ends"][0]["shots"]
        assert [s["number"] for s in cut if "track" in s] == []
```

- [ ] **Step 2: Run them and check they fail**

Run: `/home/tcuser/src/curling_score/.venv/bin/python -m pytest tests/test_timeline.py -q -k "ShortHanded"`
Expected: FAIL with `AttributeError: module 'curling_score.timeline' has no attribute 'assign_short_handed'`.

- [ ] **Step 3: Implement.** In `src/curling_score/timeline.py`:

Replace `_assign_throwers`:

```python
def _assign_throwers(shots, end_number, fmt, swapped, short=frozenset()) -> None:
    """Who threw each rock of an end whose order did not change.

    A role swap moves the thrower fields and the label, never the number, the
    colour or the hammer. So does a team of three (``short``, its colours):
    its rocks go by the format's three-player table.
    """
    for s in shots:
        n = s.get("number")
        if not isinstance(n, int) or not 1 <= n <= fmt.delivered_per_end:
            continue
        swap = bool(swapped.get(s.get("color")))
        few = s.get("color") in short
        t = fmt.throw_info(n, swapped=swap, short=few)
        s["thrower_slot"] = t.position_slot
        s["position"] = fmt.positions[t.position_slot - 1]
        s["rock_of_player"] = t.rock_of_player
        s["label"] = fmt.shot_label(end_number, n, swapped=swap, short=few)
```

In `_renumber`:
- Change the signature to `def _renumber(shots: list, end_number: int, patched: set, fmt=None, swapped=None, short=frozenset()) -> None:`.
- Add to its docstring: `` ``short`` names the teams that played with three. ``
- Replace the three lines from `swap = bool(swapped.get(s["color"]))` through `t = fmt.throw_info(i + 1, swapped=swap)` with:

```python
        swap = bool(swapped.get(s["color"]))
        few = s["color"] in short
        t = fmt.throw_info(i + 1, swapped=swap, short=few)
```

and the label line with:

```python
        s["label"] = fmt.shot_label(end_number, i + 1, swapped=swap, short=few)
```

In `apply_overrides`:
- After `fmt = format_mod.of_document(document)` add `short = format_mod.short_handed_of(document)`.
- Replace the reorder / swap branch with:

```python
            if ordered is not end["shots"]:
                colour_set = {s for s, p in here.items() if "color" in p}
                _renumber(ordered, end["number"], colour_set, fmt, swapped, short)
                end["shots"] = ordered
            elif swapped or short:
                # A team of three is credited by colour, so a rock recoloured
                # onto or off it changes hands even where nothing moved.
                _assign_throwers(end["shots"], end["number"], fmt, swapped, short)
```

After `apply_overrides` add:

```python
def assign_short_handed(document: dict) -> dict:
    """Credit a team of three's rocks 3/3/2 (format.short_throw_table).

    Which teams played with three is not the pipeline's to know: api.game_doc
    writes it into the format block from the game's record, then calls this.
    Edits and returns the document it is given; one that does not say is
    left alone.
    """
    short = format_mod.short_handed_of(document)
    if not short:
        return document
    fmt = format_mod.of_document(document)
    for game in document.get("games", []):
        for end in game.get("ends", []):
            _assign_throwers(end.get("shots") or [], end["number"], fmt, {}, short)
    return document
```

- [ ] **Step 4: Run them and check they pass.** Also run the timeline's other users.

Run: `/home/tcuser/src/curling_score/.venv/bin/python -m pytest tests/test_timeline.py tests/test_format.py tests/test_gameclips.py tests/test_plays.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/timeline.py tests/test_timeline.py
git commit -m "timeline: credit a team of three 3/3/2, through moves and recolours

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Marking a game, and serving it

**Files:**
- Modify: `src/curling_score/service/records.py` (`Source`, after `team_yellow`)
- Modify: `src/curling_score/service/api.py`:
  - `game_doc` ~487
  - `api_set_teams` ~1133
  - `api_save_play` ~1942
  - `plays_list` item ~2084
- Create: `tests/test_short_handed.py` (checked: does not exist on main)
- Modify: `tests/test_repo_contract.py` (`TestSources`)

**Interfaces:**
- Consumes: `format_mod.FOURS`, `FOURS.to_json()`, `FOURS.short_throw_table` (Task 1) and `timeline.assign_short_handed` (Task 2).
- Produces:
  - `Source.short_handed: list[str] | None`.
  - `POST /api/games/{id}/teams` accepts `short_handed: list[str]` and answers `{"ok", "red", "yellow", "short_handed": [...]}`.
  - Served documents of a marked fours game carry `format.short_handed` (a sorted list) and `format.short_throw_table`.
  - `GET /api/me/plays` items carry `short_handed: [...]`.

- [ ] **Step 1: Write the failing API tests.** Create `tests/test_short_handed.py`:

```python
"""A team that played with three (Source.short_handed): marked on the game by
anyone signed in, served into every chart of it, its rocks credited 3/3/2."""

import pytest

from curling_score.game import format as F
from tests.test_plays import a_played_game, get, played_doc, put
from tests.test_service_auth import SARAH, post, w  # noqa: F401  (fixtures)

THREE = [(1, "lead", 1), (3, "lead", 2), (5, "lead", 3), (7, "second", 1),
         (9, "second", 2), (11, "second", 3), (13, "skip", 1), (15, "skip", 2)]
FOUR = [(1, "lead", 1), (3, "lead", 2), (5, "second", 1), (7, "second", 2),
        (9, "third", 1), (11, "third", 2), (13, "skip", 1), (15, "skip", 2)]


def teams(w, src, body, headers=SARAH):  # noqa: F811
    return post(w, f"/api/games/{src}/teams", body, headers)


def red(doc):
    return [(s["number"], s["position"], s["rock_of_player"])
            for s in doc["games"][0]["ends"][0]["shots"] if s["color"] == "red"]


class TestMarkingIt:
    def test_a_signed_in_person_marks_a_team(self, w):  # noqa: F811
        _, _, src = a_played_game(w)
        r = teams(w, src, {"short_handed": ["red"]})
        assert r.status_code == 200, r.text
        assert r.json()["short_handed"] == ["red"]
        assert w["repo"].get_source(src).short_handed == ["red"]

    def test_names_alone_leave_it_as_it_was(self, w):  # noqa: F811
        _, _, src = a_played_game(w)
        teams(w, src, {"short_handed": ["red"]})
        assert teams(w, src, {"red": "Dimmit"}).json()["short_handed"] == ["red"]
        assert w["repo"].get_source(src).short_handed == ["red"]

    def test_an_empty_list_clears_it(self, w):  # noqa: F811
        _, _, src = a_played_game(w)
        teams(w, src, {"short_handed": ["red"]})
        assert teams(w, src, {"short_handed": []}).json()["short_handed"] == []
        assert w["repo"].get_source(src).short_handed is None

    def test_both_teams_can_be_three(self, w):  # noqa: F811
        _, _, src = a_played_game(w)
        r = teams(w, src, {"short_handed": ["yellow", "red"]})
        assert r.json()["short_handed"] == ["red", "yellow"]

    @pytest.mark.parametrize("bad", ["red", ["blue"], ["red", "red"], [1], [["red"]],
                                     None, {"red": True}])
    def test_anything_but_a_list_of_colours_is_refused(self, w, bad):  # noqa: F811
        _, _, src = a_played_game(w)
        r = teams(w, src, {"short_handed": bad})
        assert r.status_code == 422 and r.json()["detail"] == \
            "short_handed is a list of colours: red, yellow"
        assert w["repo"].get_source(src).short_handed is None

    def test_signed_out_it_is_read_only(self, w):  # noqa: F811
        _, _, src = a_played_game(w)
        assert teams(w, src, {"short_handed": ["red"]}, headers=None).status_code == 401

    def test_a_doubles_team_is_two_players(self, w):  # noqa: F811
        _, _, src = a_played_game(w, played_doc(F.DOUBLES))
        r = teams(w, src, {"short_handed": ["red"]})
        assert r.status_code == 422 and r.json()["detail"] == "a doubles team is two players"
        assert teams(w, src, {"short_handed": []}).status_code == 200


class TestServed:
    def test_the_review_link_credits_the_short_team_three_three_two(self, w):  # noqa: F811
        _, _, src = a_played_game(w)
        assert red(get(w, f"/g/{src}/timeline.json").json()) == FOUR
        teams(w, src, {"short_handed": ["red"]})
        doc = get(w, f"/g/{src}/timeline.json").json()
        assert red(doc) == THREE
        assert doc["format"]["short_handed"] == ["red"]
        assert doc["format"]["short_throw_table"] == [1, 1, 1, 2, 2, 2, 4, 4]
        assert doc["format"]["name"] == "fours"
        assert doc["games"][0]["ends"][0]["shots"][4]["label"] == "1st end, lead's third rock"

    def test_a_chart_made_before_gets_it_too(self, w):  # noqa: F811
        slug, share, src = a_played_game(w)
        teams(w, src, {"short_handed": ["red"]})
        assert red(get(w, f"/c/{slug}/timeline.json").json()) == THREE
        assert red(get(w, f"/s/{share}/timeline.json").json()) == THREE

    def test_unmarked_it_is_fours_again(self, w):  # noqa: F811
        _, _, src = a_played_game(w)
        teams(w, src, {"short_handed": ["red"]})
        teams(w, src, {"short_handed": []})
        doc = get(w, f"/g/{src}/timeline.json").json()
        assert red(doc) == FOUR and "short_handed" not in (doc.get("format") or {})

    def test_a_doubles_game_ignores_a_stray_mark(self, w):  # noqa: F811
        _, _, src = a_played_game(w, played_doc(F.DOUBLES))
        before = get(w, f"/g/{src}/timeline.json").json()
        w["repo"].update_source(src, short_handed=["red"])
        assert get(w, f"/g/{src}/timeline.json").json() == before


class TestPlays:
    def test_the_leads_report_gets_all_three_rocks(self, w):  # noqa: F811
        _, _, src = a_played_game(w)
        teams(w, src, {"short_handed": ["red"]})
        assert put(w, src, {"path": f"/g/{src}/", "color": "red", "slot": 1}).status_code == 200
        doc = get(w, f"/api/me/plays/{src}/doc").json()["doc"]
        mine = [s["number"] for s in doc["games"][0]["ends"][0]["shots"]
                if s["color"] == "red" and "track" in s]
        assert mine == [1, 3, 5]

    def test_third_is_refused_on_a_team_of_three(self, w):  # noqa: F811
        _, _, src = a_played_game(w)
        teams(w, src, {"short_handed": ["red"]})
        r = put(w, src, {"path": f"/g/{src}/", "color": "red", "slot": 3})
        assert r.status_code == 422
        assert r.json()["detail"] == "that team played with three: lead, second or skip"
        assert put(w, src, {"path": f"/g/{src}/", "color": "yellow", "slot": 3}).status_code == 200

    def test_a_third_saved_before_is_kept_and_credits_nothing(self, w):  # noqa: F811
        _, _, src = a_played_game(w)
        put(w, src, {"path": f"/g/{src}/", "color": "red", "slot": 3})
        teams(w, src, {"short_handed": ["red"]})
        [item] = get(w, "/api/me/plays").json()["plays"]
        assert (item["slot"], item["short_handed"]) == (3, ["red"])
        doc = get(w, f"/api/me/plays/{src}/doc").json()["doc"]
        assert not any("track" in s for s in doc["games"][0]["ends"][0]["shots"]
                       if s["color"] == "red")

    def test_the_list_says_nobody_when_nobody_was_short(self, w):  # noqa: F811
        _, _, src = a_played_game(w)
        put(w, src, {"path": f"/g/{src}/", "color": "red", "slot": 4})
        assert get(w, "/api/me/plays").json()["plays"][0]["short_handed"] == []
```

In `tests/test_repo_contract.py`, add to `class TestSources`:

```python
    def test_short_handed_round_trips(self, repo):
        repo.put_source(Source(id="s_3", video_id="vidC", game_start_s=0.0, game_end_s=6000.0,
                               current_run_id="r_1", game_index=0, created_at=T0))
        assert repo.get_source("s_3").short_handed is None
        repo.update_source("s_3", short_handed=["red", "yellow"])
        assert repo.get_source("s_3").short_handed == ["red", "yellow"]
        repo.update_source("s_3", short_handed=None)
        assert repo.get_source("s_3").short_handed is None
```

- [ ] **Step 2: Run them and check they fail**

Run: `/home/tcuser/src/curling_score/.venv/bin/python -m pytest tests/test_short_handed.py tests/test_repo_contract.py -q`
Expected: FAIL. There are 422s where 200s are expected, `KeyError: 'short_handed'`, and `AttributeError: 'Source' object has no attribute 'short_handed'`.

- [ ] **Step 3: Add the field.** In `src/curling_score/service/records.py`, directly after `team_yellow: str | None = None` in `Source`:

```python
    # The colours that played with three, if anybody has said. On the game
    # for the same reason as the names, and served into every chart of it:
    # api.game_doc credits that team's rocks 3/3/2 (format.short_throw_table).
    # None is nobody.
    short_handed: list[str] | None = None
```

- [ ] **Step 4: Serve it.** In `api.py` `game_doc`, directly after the `apply_entered_scores` block (`if src is not None: doc = timeline.apply_entered_scores(...)`) and before `game = doc["games"][0] ...`:

```python
        # A team that played with three, if anybody has said: its rocks are
        # credited 3/3/2. On the source for the same reason as the names
        # below, and written into the format block so a renumbering -- the
        # viewer's, or apply_overrides' -- keeps crediting it that way.
        if (src is not None and src.short_handed
                and format_mod.of_document(doc) is format_mod.FOURS):
            doc["format"] = {**format_mod.FOURS.to_json(), **(doc.get("format") or {}),
                             "short_handed": [c for c in ("red", "yellow")
                                              if c in src.short_handed]}
            timeline.assign_short_handed(doc)
```

- [ ] **Step 5: Accept it.** Replace the body of `api_set_teams` from `me = require_user(authorization)` to the `return`:

```python
        me = require_user(authorization)
        names = {}
        for colour in ("red", "yellow"):
            if colour not in body:
                continue
            name = str(body.get(colour) or "").strip()
            if len(name) > 60:
                raise HTTPException(422, "that name is too long for a team")
            names["team_" + colour] = name or None
        # Which teams played with three: a list of colours, empty to clear.
        # Left out, it is left as it was, so naming a team never unmarks it.
        if "short_handed" in body:
            short = body["short_handed"]
            if (not isinstance(short, list)
                    or not all(isinstance(c, str) and c in ("red", "yellow") for c in short)
                    or len(set(short)) != len(short)):
                raise HTTPException(422, "short_handed is a list of colours: red, yellow")
            names["short_handed"] = sorted(short) or None
        if not names:
            raise HTTPException(400, "name red, yellow, or both")
        src = repo.get_source(source_id)
        if src is None:
            raise HTTPException(404, "no such game")
        if names.get("short_handed") and (src.format or "fours") != "fours":
            raise HTTPException(422, "a doubles team is two players")
        repo.update_source(src.id, **names)
        log.info("teams %s set on %s by %s", names, src.id, me.id)
        return {"ok": True, **{c: names.get("team_" + c, getattr(src, "team_" + c))
                               for c in ("red", "yellow")},
                "short_handed": names.get("short_handed", src.short_handed) or []}
```

Also add one line to its docstring, after the first line: `` ``short_handed`` says which teams played with three. ``

- [ ] **Step 6: Guard plays.** In `api_save_play`, directly after the `slot must be 1 to …` check:

```python
        if (fmt.short_throw_table and color in (src.short_handed or [])
                and slot not in fmt.short_throw_table):
            raise HTTPException(422, "that team played with three: lead, second or skip")
```

In `plays_list`, in the `item.update({...})` dict, after the `"teams": {...},` entry:

```python
                "short_handed": (src.short_handed if src else None) or [],
```

- [ ] **Step 7: Run them and check they pass.** Also run the endpoints' neighbours.

Run: `/home/tcuser/src/curling_score/.venv/bin/python -m pytest tests/test_short_handed.py tests/test_repo_contract.py tests/test_plays.py tests/test_service_auth.py tests/test_shared_shots.py tests/test_game_clips_api.py tests/test_thinking_api.py -q`
Expected: all PASS. The Firestore half of `test_repo_contract.py` skips without the emulator; the next step runs it.

- [ ] **Step 8: Firestore contract on the emulator**

```bash
JAVA=/usr/lib/jvm/java-21-openjdk-amd64/bin/java ~/google-cloud-sdk/platform/cloud-firestore-emulator/cloud_firestore_emulator start --host=127.0.0.1 --port=8399 &
# wait until port 8399 answers, then:
FIRESTORE_EMULATOR_HOST=127.0.0.1:8399 /home/tcuser/src/curling_score/.venv/bin/python -m pytest tests/test_repo_contract.py -o addopts="" -q -k Sources
```

Expected: PASS, with the firestore params included (not skipped). Afterwards stop it by the **java** child's PID (find it with `pgrep -af java`; it holds port 8399), not the wrapper's. A leftover java process would keep serving old data to a later run.

- [ ] **Step 9: Commit**

```bash
git add src/curling_score/service/records.py src/curling_score/service/api.py \
        tests/test_short_handed.py tests/test_repo_contract.py
git commit -m "api: mark a team that played with three on the game; served 3/3/2 everywhere

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: The viewer's arithmetic (JS core)

**Files:**
- Modify: `frontend/core/format.mjs`
- Modify: `frontend/core/timeline.mjs` (`assignThrowers`, `renumber`, `layout`)
- Modify: `frontend/core/myshots.mjs` (`positionChoices`, `playText`)
- Modify: `frontend/core/report.mjs` (new `teamHeads`)
- Test: `tests/test_viewer_js.py`
- Build outputs: `src/curling_score/viewer/app.js`, `src/curling_score/service/static/site.js`, `frontend/.buildstamp.json`

**Interfaces:**
- Consumes: the served block shape from Task 3, `{..., short_throw_table: [1,1,1,2,2,2,4,4], short_handed: ["red"]}`, and `timeline.assign_short_handed` (Task 2) for Python-side fixtures.
- Produces (all exported through `core/index.mjs`, which already does `export *`):
  - `shortOf(fmt) -> Set<string>`
  - `throwInfo(n, fmt, swapped, short)`
  - `shotLabel(endNo, n, fmt, swapped, short)`
  - `roleText(fmt, slot, short) -> string`; `""` when that slot throws nothing
  - `assignThrowers(shots, endNo, fmt, swapped, short)`
  - `renumber(shots, endNo, fixed, fmt, swapped, short)`
  - `positionChoices(fmt, short = false)`, which drops slots that throw nothing
  - `playText(play)`, which reads `play.short_handed`
  - `teamHeads(names, fmt) -> {red, yellow}`

- [ ] **Step 1: Write the failing tests.** In `tests/test_viewer_js.py`, add after `class TestRoleSwapParity` (whose helpers `doc`, `setup`, `both` and `FIELDS` are defined above it):

```python
def short_doc(colours=("red",), end_number=2, credited=True):
    """A fours end with the format block api.game_doc writes for a team of
    three -- its rocks credited 3/3/2 as game_doc credits them, or, with
    `credited` off, still as the pipeline wrote them."""
    from curling_score import timeline
    from curling_score.game import format as F
    d = doc(_sliced_shots(16, "fours", end_number), end_number=end_number)
    d["format"] = {**F.FOURS.to_json(), "short_handed": list(colours)}
    return timeline.assign_short_handed(d) if credited else d


THREE_ROWS = [("lead", 1), ("lead", 2), ("lead", 3), ("second", 1),
              ("second", 2), ("second", 3), ("skip", 1), ("skip", 2)]


class TestShortHandedFormat:
    def test_short_of_reads_the_block(self):
        from curling_score.game import format as F
        dbl = {**F.DOUBLES.to_json(), "short_handed": ["red"]}
        got = run_js(setup(short_doc()) +
                     "const f = formatOf(state.doc);"
                     "out([[...shortOf(f)], [...shortOf(formatOf({}))],"
                     " [...shortOf({...f, short_handed: ['red', 'blue', 3]})],"
                     " [...shortOf({...f, short_throw_table: 'x'})],"
                     f" [...shortOf({json.dumps(dbl)})]]);")
        assert got == [["red"], [], ["red"], [], []]

    def test_the_throw_table_for_a_team_of_three(self):
        got = run_js("const F4 = formatOf({});"
                     "out([5, 7, 13].map(n => throwInfo(n, F4, false, true))"
                     ".concat([shotLabel(2, 5, F4, false, true), roleText(F4, 1, true),"
                     " roleText(F4, 2, true), roleText(F4, 3, true), roleText(F4, 4, true),"
                     " roleText(F4, 1)]));")
        assert got[:3] == [{"has_hammer": False, "thrower_slot": 1, "rock_of_player": 3},
                           {"has_hammer": False, "thrower_slot": 2, "rock_of_player": 1},
                           {"has_hammer": False, "thrower_slot": 4, "rock_of_player": 1}]
        assert got[3:] == ["2nd end, lead's third rock", "1st–3rd", "4th–6th", "",
                           "7th & 8th", "1st & 2nd"]

    def test_the_report_heads_say_three_players(self):
        got = run_js(setup(short_doc()) +
                     "out(teamHeads({red: 'Dimmit', yellow: 'Grant'}, formatOf(state.doc)));")
        assert got == {"red": "Dimmit · 3 players", "yellow": "Grant"}


class TestShortHandedParity:
    """The viewer credits a team of three exactly as apply_overrides does."""

    def test_as_served(self):
        py, js = both(short_doc(), {})
        assert js == py
        assert [(r[4], r[5]) for r in js if r[2] == "red"] == THREE_ROWS

    def test_a_block_whose_rocks_are_not_yet_credited(self):
        py, js = both(short_doc(credited=False), {})
        assert js == py
        assert [(r[4], r[5]) for r in js if r[2] == "red"] == THREE_ROWS

    def test_a_moved_rock(self):
        py, js = both(short_doc(), {"0.2.6": {"before": 5}})
        assert js == py
        assert (js[5][0], js[5][1], js[5][4], js[5][5]) == (5, 6, "lead", 3)

    def test_a_rock_recoloured_onto_the_short_team(self):
        py, js = both(short_doc(), {"0.2.6": {"color": "red"}})
        assert js == py
        assert (js[5][2], js[5][4], js[5][5]) == ("red", "lead", 3)

    def test_the_players_slice_matches_the_view(self):
        TestPlayerSliceParity()._check(short_doc(), {}, "red", 1)
```

Add after `class TestPositionChoices`, where `play`, `FOURS_JSON` and `DAY` are defined:

```python
SHORT_JSON = {**FOURS_JSON, "short_throw_table": [1, 1, 1, 2, 2, 2, 4, 4]}


class TestShortHandedPlays:
    def test_choices_for_a_team_of_three(self):
        got = run_js(f"out(positionChoices({json.dumps(SHORT_JSON)}, true));")
        assert [(c["slot"], c["label"], c["role"]) for c in got] == [
            (1, "Lead", "1st–3rd"), (2, "Second", "4th–6th"), (4, "Skip", "7th & 8th")]

    def test_a_play_on_a_team_of_three(self):
        plays = [play(slot=1, short_handed=["red"], format=SHORT_JSON),
                 play(slot=3, short_handed=["red"], format=SHORT_JSON),
                 play(slot=3, short_handed=["yellow"], format=SHORT_JSON),
                 play(slot=4, format=SHORT_JSON)]
        got = run_js(f"out({json.dumps(plays)}.map(playText));")
        assert got == ["Dimmit · Lead (1st–3rd)",
                       "Dimmit · This team played with three. Pick your rocks again",
                       "Dimmit · Third (5th & 6th)",
                       "Dimmit · Skip (7th & 8th)"]
```

- [ ] **Step 2: Run them and check they fail**

Run: `/home/tcuser/src/curling_score/.venv/bin/python -m pytest tests/test_viewer_js.py -q -k "ShortHanded"`
Expected: FAIL. You get `ReferenceError: shortOf is not defined` / `teamHeads is not defined`, and parity mismatches on the recolour and uncredited cases.

- [ ] **Step 3: `frontend/core/format.mjs`**

Add `short_throw_table: [1, 1, 1, 2, 2, 2, 4, 4],` to `FOURS` after `throw_table`.

After `lineWithoutBroom` add:

```js
/* The colours that played with three, as api.game_doc writes them into the
 * format block. Only a format with a three-player table of the right length
 * honours it -- anything else is nobody. Mirrors format.short_handed_of. */
export function shortOf(fmt) {
  const t = fmt?.short_throw_table;
  if (!Array.isArray(t) || t.length !== fmt.throw_table?.length || !t.every(Number.isInteger))
    return new Set();
  const said = Array.isArray(fmt.short_handed) ? fmt.short_handed : [];
  return new Set(said.filter(c => c === "red" || c === "yellow"));
}

/* A team's table: the three-player one when it played with three. */
const tableOf = (fmt, short) =>
  (short && Array.isArray(fmt.short_throw_table) ? fmt.short_throw_table : fmt.throw_table);
```

Replace `throwInfo`, `shotLabel` and `roleText`:

```js
/* Who throws the end's n-th delivered rock. `swapped` says this team's two
 * players traded roles this end: the slot then names the person, and
 * rock_of_player still counts within the role. `short` says this team played
 * with three. */
export function throwInfo(n, fmt = FOURS, swapped = false, short = false) {
  const table = tableOf(fmt, short);
  const k = Math.min((n + 1) >> 1, table.length);   // this team's k-th rock
  const role = table[k - 1];
  let rock = 0;
  for (let i = 0; i < k; i++) if (table[i] === role) rock++;
  const slot = swapped && fmt.swappable ? 3 - role : role;
  return { has_hammer: n % 2 === 0, thrower_slot: slot, rock_of_player: rock };
}

export function shotLabel(endNo, n, fmt = FOURS, swapped = false, short = false) {
  const t = throwInfo(n, fmt, swapped, short);
  return `${ordinalOf(endNo)} end, ${fmt.positions[t.thrower_slot - 1]}'s `
       + `${NTH[t.rock_of_player - 1]} rock`;
}

/* Which of a team's rocks a player's role throws: "1st & 5th", "2nd–4th";
 * "" for the slot a team of three has nobody in. */
export function roleText(fmt, slot, short = false) {
  const ks = tableOf(fmt, short).map((r, i) => (r === slot ? i + 1 : 0)).filter(Boolean);
  if (!ks.length) return "";
  const run = ks.every((k, i) => i === 0 || k === ks[i - 1] + 1);
  if (run && ks.length > 2) return `${NUM[ks[0] - 1]}–${NUM[ks[ks.length - 1] - 1]}`;
  return ks.map(k => NUM[k - 1]).join(" & ");
}
```

- [ ] **Step 4: `frontend/core/timeline.mjs`**

- Change the import to `import { FOURS, formatOf, shortOf, shotLabel, throwInfo } from "./format.mjs";`.
- Replace `assignThrowers` and `renumber`:

```js
/* Who threw each rock of an end whose order did not change. Mirrors
 * timeline._assign_throwers. `short` is the set of colours that played with
 * three (shortOf). */
export function assignThrowers(shots, endNo, fmt, swapped, short = new Set()) {
  for (const s of shots) {
    if (!Number.isInteger(s.number) || s.number < 1 || s.number > fmt.delivered_per_end) continue;
    const swap = !!swapped[s.color];
    const few = short.has(s.color);
    const t = throwInfo(s.number, fmt, swap, few);
    s.thrower_slot = t.thrower_slot;
    s.position = fmt.positions[t.thrower_slot - 1];
    s.rock_of_player = t.rock_of_player;
    s.label = shotLabel(endNo, s.number, fmt, swap, few);
  }
}

export function renumber(shots, endNo, fixed, fmt = FOURS, swapped = {}, short = new Set()) {
  const anchors = [];
  shots.forEach((s, i) => {
    if (!s.color_inferred || fixed.has(identity(s))) anchors.push([i, s.color]);
  });
  shots.forEach((s, i) => {
    s.id = identity(s);
    s.number = i + 1;
    if (s.color_inferred && anchors.length && !fixed.has(s.id)) {
      let best = anchors[0];
      for (const a of anchors) if (Math.abs(a[0] - i) < Math.abs(best[0] - i)) best = a;
      s.color = (i - best[0]) % 2 === 0 ? best[1] : (best[1] === "red" ? "yellow" : "red");
    }
    const swap = !!swapped[s.color];
    const few = short.has(s.color);
    const t = throwInfo(i + 1, fmt, swap, few);
    s.has_hammer = t.has_hammer;
    s.thrower_slot = t.thrower_slot;
    s.position = fmt.positions[t.thrower_slot - 1];
    s.rock_of_player = t.rock_of_player;
    s.label = shotLabel(endNo, i + 1, fmt, swap, few);
  });
}
```

In `layout`:
- After `const swapped = swappedOf(g, e, overrides, fmt);` add `const short = shortOf(fmt);`.
- Change `renumber(shots, e.number, fixed, fmt, swapped);` to `renumber(shots, e.number, fixed, fmt, swapped, short);`.
- Replace the swap branch:

```js
  } else if (Object.keys(swapped).length || short.size) {
    // A team of three is credited by colour, so a recoloured rock changes
    // hands even where nothing moved. Mirrors apply_overrides.
    assignThrowers(shots, e.number, fmt, swapped, short);
  }
```

- [ ] **Step 5: `frontend/core/myshots.mjs`**

Replace `positionChoices` and `playText`:

```js
/* What "I played…" offers: each slot, by name and by the rocks it throws --
 * "Skip" and "7th & 8th", or in doubles "Player B" and "2nd–4th". For a team
 * of three (`short`), only the three who threw: Lead, Second, Skip. */
export const positionChoices = (fmt, short = false) => fmt.positions
  .map((p, i) => ({ slot: i + 1, label: positionLabel(p, fmt), role: roleText(fmt, i + 1, short) }))
  .filter(c => c.role);

/* "Dimmit · Skip (7th & 8th)": a play as the report lists it. A slot its
 * team of three had nobody in -- saved before the team was marked -- says
 * so rather than naming rocks nobody threw. */
export function playText(play) {
  const team = play.teams?.[play.color] || capital(play.color);
  const fmt = play.format;
  const role = roleText(fmt, play.slot, (play.short_handed || []).includes(play.color));
  if (!role) return `${team} · This team played with three. Pick your rocks again`;
  return `${team} · ${positionLabel(fmt.positions[play.slot - 1], fmt)} (${role})`;
}
```

- [ ] **Step 6: `frontend/core/report.mjs`**

- Change the import to `import { FOURS, positionText, shortOf } from "./format.mjs";`.
- After `typedTeamNames` add:

```js
/* The names over the report's two columns: "Dimmit · 3 players" for a team
 * that played with three, so its empty third's row reads as meant. */
export function teamHeads(names, fmt) {
  const short = shortOf(fmt);
  return Object.fromEntries(COLORS.map(c => [c, short.has(c) ? `${names[c]} · 3 players` : names[c]]));
}
```

- [ ] **Step 7: Build, run the tests, and check they pass**

```bash
cd frontend && npm run build && cd ..
/home/tcuser/src/curling_score/.venv/bin/python -m pytest tests/test_viewer_js.py tests/test_frontend_build.py -q
```

Expected: eslint is clean, the build writes both bundles, and all tests PASS, including the existing `TestDoublesFormat`, `TestRoleSwapParity`, `TestPlayerSliceParity`, `TestPositionChoices` and `TestPlayerRocks`.

- [ ] **Step 8: Commit**

```bash
git add frontend/core/format.mjs frontend/core/timeline.mjs frontend/core/myshots.mjs \
        frontend/core/report.mjs tests/test_viewer_js.py \
        src/curling_score/viewer/app.js src/curling_score/service/static/site.js frontend/.buildstamp.json
git commit -m "viewer core: a team of three credited 3/3/2, in step with apply_overrides

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: The dialog, "I played…", the report and My shots

**Files:**
- Modify: `frontend/viewer/App.jsx` (import list ~19; menu item ~634-641)
- Modify: `frontend/viewer/Teams.jsx`
- Modify: `frontend/viewer/Played.jsx`
- Modify: `frontend/viewer/Report.jsx` (import ~15-18; `Report` ~360; `HeadToHead` ~197; `<Matrix>` ~439)
- Modify: `frontend/site/Shots.jsx` (`PlayRow` ~263)
- Modify: `frontend/runtime/teams.mjs` (comment only)
- Modify: `src/curling_score/viewer/style.css` (the `#teamsDialog` block ~662)
- Test: `tests/test_viewer_js.py` (`TestNamingTheTeams`)
- Build outputs, as in Task 4

**Interfaces:**
- Consumes: `shortOf`, `positionChoices(fmt, short)` and `teamHeads` (Task 4); the teams endpoint's `short_handed` (Task 3).
- Produces: the `naming` snapshot `{opened, sourceId, names, short: string[], doubles: boolean}`.

- [ ] **Step 1: Write the failing test.** In `tests/test_viewer_js.py` `class TestNamingTheTeams`:
  - Give `send` a `body` keyword. Change `def send(self, status, reply, *, throws=False):` to `def send(self, status, reply, *, throws=False, body=None):`.
  - Add `body = body or {"red": "Dimmit", "yellow": ""}` as the method's first line.
  - Change the script line to `f"const res = await M.sendTeams('s 1', {json.dumps(body)}, 'tok');\n"`.
  - Then add:

```python
    def test_it_sends_a_team_of_three_with_the_names(self):
        body = {"red": "Dimmit", "yellow": "", "short_handed": ["red"]}
        got = self.send(200, {"ok": True, "short_handed": ["red"]}, body=body)
        assert json.loads(got["asked"][0]["init"]["body"]) == body

    def test_the_menu_says_teams_and_the_dialog_offers_three(self):
        assert ">\n          Teams…\n" in self.src("frontend/viewer/App.jsx")
        teams = self.src("frontend/viewer/Teams.jsx")
        assert "Played with three" in teams and "short_handed" in teams
```

Run: `/home/tcuser/src/curling_score/.venv/bin/python -m pytest tests/test_viewer_js.py -q -k NamingTheTeams`
Expected: the first new test PASSES already, because `sendTeams` passes the body through untouched. The second FAILS.

- [ ] **Step 2: `App.jsx`**
  - Add `shortOf,` to the `../core/index.mjs` import list, next to `teamNames, typedTeamNames,`.
  - Replace the teams button's `onClick` patch and label:

```jsx
                onClick={() => dispatch({ type: "set", patch: { naming: {
                  opened: Date.now(), sourceId: doc.chart.source_id,
                  names: typedTeamNames(view.game),
                  short: [...shortOf(view.format)],
                  doubles: view.format.name === "doubles" } } })}>
          Teams…
```

- [ ] **Step 3: `Teams.jsx`**
  - Header comment: change `"Name teams…": who played this game` to `"Teams…": who played this game, and whether a team played with three`.
  - In `TeamsForm`, after the `yellow` state:

```js
  const [short, setShort] = useState(() => new Set(naming.short || []));
  const three = (c, on) => setShort(s => {
    const next = new Set(s);
    if (on) next.add(c); else next.delete(c);
    return next;
  });
```

  - In `save`, replace the `sendTeams(...)` call's body argument `{ red: red.trim(), yellow: yellow.trim() }` with `body`, defined just above `const who = ...` as:

```js
    // A doubles team is two players: the box is not shown, and nothing is sent.
    const body = { red: red.trim(), yellow: yellow.trim(),
                   ...(naming.doubles ? {} : { short_handed: ["red", "yellow"].filter(c => short.has(c)) }) };
```

  - Change the `tm-why` paragraph to:

```jsx
      <p className="tm-why">Each team's name, by the colour it threw. Every chart and link
        of this game shows them, and so does the game list. A team of three: its lead and
        second threw three rocks each, its skip two.</p>
```

  - After each `.tm-team` div (red, then yellow) add the matching checkbox. For red:

```jsx
      {!naming.doubles && (
        <label className="tm-three">
          <input type="checkbox" checked={short.has("red")}
                 onChange={e => three("red", e.target.checked)} />
          Played with three
        </label>
      )}
```

    The yellow one is the same, with `"yellow"` in both places.

- [ ] **Step 4: `style.css`.** In the teams dialog block (~662), change the heading comment to `/* --- "Teams…" dialog --- */` and add:

```css
#teamsDialog .tm-three { display: flex; align-items: center; gap: 8px; min-height: 44px;
  margin: -8px 0 10px calc(12px + 4.5em + 16px); color: var(--muted); font-weight: 400; }
#teamsDialog .tm-three input { width: 20px; height: 20px; margin: 0;
  accent-color: var(--accent); }
```

(The `.tm-team input` rule does not reach these boxes, because they sit outside `.tm-team`.)

- [ ] **Step 5: `Played.jsx`**
  - Change the core import to `import { positionChoices, shortOf } from "../core/index.mjs";`.
  - In `PlayedForm`, just above `const save = ...`:

```js
  // A team of three offers its three players. A pick the team picked now
  // does not have (Third, from the other team or a save before it was
  // marked) is no pick, so Save waits for one.
  const few = !!color && shortOf(playing.format).has(color);
  const choices = positionChoices(playing.format, few);
  const picked = choices.some(p => p.slot === slot) ? slot : null;
```

  - In `save`: `if (busy || !color || !picked) return;` and send `{ path: location.pathname, color, slot: picked }`.
  - In the JSX:
    - the slots group is `style={{ "--n": choices.length }}`;
    - map `choices.map(p => ...)`;
    - use `className={picked === p.slot ? "on" : undefined}` and `aria-pressed={picked === p.slot}`;
    - the submit button is `disabled={busy || !color || !picked}`.

- [ ] **Step 6: `Report.jsx`**
  - Add `teamHeads` to the core import.
  - In `Report`, after `const names = teamNames(view.game);` add `const heads = teamHeads(names, view.format);`.
  - Change `<HeadToHead h2h={h2h} names={names} />` to `<HeadToHead h2h={h2h} names={names} heads={heads} />`, and change the `<Matrix ... names={names} />` in the per-team map to `names={heads}`.
  - In `HeadToHead({ h2h, names })`, take `heads` too (`function HeadToHead({ h2h, names, heads })`) and pass `names={heads}` to both `<Duel>`s. The `others` sentence keeps `names`.

- [ ] **Step 7: `Shots.jsx` (`PlayRow`).** Replace the `choices` lines with:

```js
  const threeOn = c => (p.short_handed || []).includes(c);
  const choices = ["red", "yellow"].flatMap(c => positionChoices(p.format, threeOn(c)).map(k => ({
    value: `${c}|${k.slot}`, text: `${teams[c]} · ${k.label} (${k.role})` })));
  const current = `${p.color}|${p.slot}`;
```

and the `<select>` with:

```jsx
            <select aria-label="Which rocks were yours" disabled={busy}
                    value={current} onChange={change}>
              {!choices.some(c => c.value === current) &&
                <option value={current} disabled>Pick your rocks again</option>}
              {choices.map(c => <option key={c.value} value={c.value}>{c.text}</option>)}
            </select>
```

- [ ] **Step 8: `runtime/teams.mjs`.** Change the comment above `sendTeams` to:

```js
/* `body` is {red, yellow, short_handed?}: an empty name clears that team's;
 * short_handed, when sent, is the colours that played with three. */
```

- [ ] **Step 9: Build, run the tests, and check they pass**

```bash
cd frontend && npm run build && cd ..
/home/tcuser/src/curling_score/.venv/bin/python -m pytest tests/test_viewer_js.py tests/test_frontend_build.py -q
```

Expected: eslint is clean and all tests PASS.

- [ ] **Step 10: Headless phone check (390×844, touch).** Follow the memory note "headless phone check".
  1. Write a short-handed fixture: `/g/s_1UuJFNsEmVfYZaPkz/timeline.json` (public), saved to the scratchpad, with its `format` block set to `{**FOURS.to_json(), "short_handed": ["red"]}`, passed through `timeline.assign_short_handed`.
  2. Serve it with `PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python scripts/devserve.py <fixture>`.
  3. Drive it with headless Chrome and `scripts/cdp.mjs`. Check:
     - **Detail:** the red rock 5 reads "lead (rock 3)".
     - **Report:** the "By position" head says "<red name> · 3 players"; red's Third row shows "—".
     - **⋯ menu:** it shows "Teams…".
     - **Teams dialog:** it shows two "Played with three" boxes. This needs hosted accounts, so if devserve reports accounts off, check the box markup instead by setting the `naming` state through `eval`. Screenshot it.
     - **"I played…":** pick Yellow → Third, then Red. The Third choice disappears, nothing is pressed, and Save is disabled. This is the "switching team after picking Third" item from Review Focus.
  4. Save the screenshots in the scratchpad.

- [ ] **Step 11: Commit**

```bash
git add frontend/viewer/App.jsx frontend/viewer/Teams.jsx frontend/viewer/Played.jsx \
        frontend/viewer/Report.jsx frontend/site/Shots.jsx frontend/runtime/teams.mjs \
        src/curling_score/viewer/style.css tests/test_viewer_js.py \
        src/curling_score/viewer/app.js src/curling_score/service/static/site.js frontend/.buildstamp.json
git commit -m "viewer: Teams… marks a team of three; I played…, the report and My shots follow

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Verify, merge, deploy (deploy waits for the user)

**Files:** none new.

- [ ] **Step 1: Run every affected test file once more, together**

```bash
/home/tcuser/src/curling_score/.venv/bin/python -m pytest tests/test_format.py tests/test_rules.py \
  tests/test_timeline.py tests/test_gameclips.py tests/test_short_handed.py tests/test_plays.py \
  tests/test_service_auth.py tests/test_service_api.py tests/test_shared_shots.py \
  tests/test_game_clips_api.py tests/test_thinking_api.py tests/test_repo_contract.py \
  tests/test_viewer_js.py tests/test_frontend_build.py -q
```

Expected: all PASS. If `test_service_api.py` is killed with exit 137, split it from the rest; that is the OOM killer, not a failure.

- [ ] **Step 2: Merge into main.** On the main checkout:
  1. Run `git status`, and check that no stray copies of this branch's files are there (memory: concurrent session commits).
  2. If main moved, rebase `short-handed` onto it and re-run Step 1.
  3. Then `git -C /home/tcuser/src/curling_score merge --ff-only short-handed`.

- [ ] **Step 3: Ask the user before deploying.** The deploy is API only: no worker rsync and no reprocess. Before asking, list what else would ship, with `git diff --stat <live api sha>..main -- src/curling_score/service src/curling_score/viewer frontend`. The live sha is the `api:<sha>` image tag from `~/google-cloud-sdk/bin/gcloud run services describe curling-chart --region us-west1 --project curling-stats-508323 --format='value(spec.template.spec.containers[0].image)'`.

- [ ] **Step 4: Deploy (after the user's yes).** Deploy from a clean worktree of main:

```bash
git -C /home/tcuser/src/curling_score worktree add --detach /tmp/claude-1000/-home-tcuser-src-curling-score/01ffeb86-4540-413d-ae2f-96e6f4f5124c/scratchpad/deploy-short main
cp /home/tcuser/src/curling_score/firebase_api_key.json /tmp/claude-1000/-home-tcuser-src-curling-score/01ffeb86-4540-413d-ae2f-96e6f4f5124c/scratchpad/deploy-short/
cd /tmp/claude-1000/-home-tcuser-src-curling-score/01ffeb86-4540-413d-ae2f-96e6f4f5124c/scratchpad/deploy-short
PATH=~/google-cloud-sdk/bin:$PATH PROJECT_ID=curling-stats-508323 REGION=us-west1 \
  PUBLIC_BASE_URL=https://curling.dimmit.net ./deploy/deploy-api.sh
```

  - First run the script's MODEL_ID one-liner and compare it with the live `MODEL_ID`.
  - Check that the output says `accounts: sign-in is live`.
  - If auto mode denies the deploy, hand the user the exact command instead of retrying.

- [ ] **Step 5: Check it live, then mark the example game**
  1. The live `/g/s_1UuJFNsEmVfYZaPkz/app.js` sha256 equals `frontend/.buildstamp.json`'s output hash.
  2. `POST /api/games/s_1UuJFNsEmVfYZaPkz/teams` without a token answers 401.
  3. The user marks the short team through ⋯ Teams…, or tells us which colour.
  4. Then `/g/s_1UuJFNsEmVfYZaPkz/timeline.json` shows that colour's rock 3 as "lead's third rock" and `format.short_handed` set.

- [ ] **Step 6: Memory.** Add a memory file `short-handed-teams.md` and a line in `MEMORY.md`. Record:
  - what shipped (commit, API revision);
  - that the mark lives on `Source.short_handed` and is applied in `game_doc`;
  - that slot 3 throws nothing for a team of three;
  - which colour was marked on s_1UuJ.
