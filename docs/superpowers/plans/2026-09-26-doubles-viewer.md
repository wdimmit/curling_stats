# Doubles: the role-swap override and the viewer (phases 4–5) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a doubles chart read and chart correctly. That means doubles labels and players, per-player stats and a hack call that follow the person, the placement's hammer and power play on the end, a per-end "swapped roles" control, a Format choice on the submit form and a Doubles tag in the catalogue. A four-player chart looks and behaves exactly as it does today.

**Architecture:**
- The timeline already carries a `format` block, with its throw table and position names, on doubles documents.
- A new framework-free module, `frontend/core/format.mjs`, reads that block (falling back to four-player) and owns the throw arithmetic that `timeline.mjs` used to hard-code.
- A per-end override keyed `"<game>.<end identity>"` with `{"roles_swapped": {"red": true}}` swaps a team's two players for that end. Python's `timeline.apply_overrides` and the JS `layout()` apply it the same way, and a test holds them to the same answers.
- The React components read positions, labels and the end's placement from the view instead of from four-player constants.

**Tech Stack:**
- Python 3.12 and pytest.
- React 19 bundled by esbuild through `npm run build`. It lints first, and writes `src/curling_score/viewer/app.js`, `src/curling_score/service/static/site.js` and `frontend/.buildstamp.json`.
- Node, for `tests/test_viewer_js.py`.
- Headless Chrome, for the final visual check.

**Spec:** `docs/superpowers/specs/2026-09-25-mixed-doubles-design.md` (phases 4 and 5). Read it. Deliberate refinements:
- **Where the format warning shows:** on the chart page rather than the status page, because a finished run sends people straight to the chart.
- **Doubles placement stones:** they need no new drawing. The pipeline already puts them in rock 1's house and its diff, so ghosts work unchanged. Task 6 verifies this.
- **The JS colour-fix prefix is corrected** to use the end's identity rather than its number, as Python does. That is a parity fix, and it changes only trimmed charts that also moved a rock.

## Global Constraints

- **A four-player chart is unchanged.** Every label, position, number, colour, stat and hack call for a document with no `format` block is what it is today, and every existing test in `tests/test_viewer_js.py` passes unedited.
- Python and JS apply overrides identically; the parity tests compare `id, number, color, label, position, rock_of_player, has_hammer, thrower_slot`.
- **Doubles wording:**
  - labels `"3rd end, B's second rock"`;
  - thrower row `"Player B (rock 2 of 3)"`;
  - report headings `"A · 1st & 5th"` and `"B · 2nd–4th"`.
- **Swap override:**
  - key `"<game index>.<end identity>"`, with patch `{"roles_swapped": {"red": <bool>, "yellow": <bool>}}`;
  - honoured only when the format is swappable (doubles);
  - ignored otherwise, including on four-player documents.
- In doubles, `thrower_slot` is the **person** (1 = A, 2 = B) once swaps are applied. That is what makes stats and the hack call follow the person.
- `frontend/core/` must not touch `document`, `window` or `fetch`. The lint enforces it, and the Python suite imports core under bare node.
- Build only with `cd frontend && npm run build`, and commit the built bundles and `frontend/.buildstamp.json` with the sources. `tests/test_frontend_build.py` checks they are fresh. `frontend/node_modules` in this worktree is a symlink to the main checkout's; never commit it (it is gitignored).
- The hosted switch `DOUBLES_ENABLED` is unchanged. The submit form offers Format only when the server says doubles is on.
- Work in `/home/tcuser/src/curling_score/.claude/worktrees/doubles-viewer` on branch `doubles-viewer`. Another session shares the main checkout.
- Python tests: `PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest -q <files>`, named files only; the full suite OOMs.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Stage explicit paths only.

## Review Focus

1. **A swap and a moved rock in the same end.** Numbers must follow the move and positions must follow the swap, identically in Python and JS. (Task 2, `test_js_and_python_agree_on_a_swap_with_a_move`.)
2. **Turning a swap back off** (`{"red": false}`) must give back the detected roles and labels exactly. (Task 1, `test_unswapping_restores_the_detected_roles`; Task 2, `test_js_and_python_agree_when_a_swap_is_turned_off`.)
3. **A four-player document carrying a stray end key** must ignore it in both implementations. (Task 1, `test_a_fours_document_ignores_an_end_key`; Task 2, `test_js_ignores_an_end_key_on_a_fours_chart`.)
4. **A doubles block that is malformed** (no `throw_table`, or no `positions`) must fall back to four-player rather than crash the viewer. (Task 2, `test_a_malformed_format_block_reads_as_fours`.)
5. **A rock whose position is not in the format's list** (an old hand-edited chart) must still count in the report, not vanish from it. (Task 3, `test_an_unknown_position_still_counts`.)

---

### Task 1: Python applies the role-swap override

**Files:**
- Modify: `src/curling_score/timeline.py` (`_renumber` ~530-560, `apply_overrides` ~565-595; new `_swapped` and `_assign_throwers`)
- Modify: `src/curling_score/service/api.py` (the chart row's `"shots_charted"`, ~line 1024)
- Test: `tests/test_timeline.py`, `tests/test_service_api.py`

**Interfaces:**
- Consumes: `format.GameFormat.throw_info(n, swapped=False)`, `shot_label(end, n, swapped=False)`, `positions` and `swappable`; `format.of_document(doc)`; `rules.COLORS`.
- Produces:
  - `timeline.apply_overrides(document, overrides)` honours two-part keys `"g.e"` with `{"roles_swapped": {...}}`, and records `end["roles_swapped"] = {colour: True, ...}` when any team swapped;
  - `timeline._renumber(shots, end_number, patched, fmt=None, swapped=None)`;
  - `timeline._assign_throwers(shots, end_number, fmt, swapped)`;
  - `timeline._swapped(patch, fmt) -> dict[str, bool]`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_timeline.py`; `F` and `timeline` are already imported)

```python
def _doubles_end(n=10, end_number=3, fmt=F.DOUBLES, with_block=True):
    """A doubles end as the pipeline writes it: red throws first."""
    def s(num):
        t = fmt.throw_info(num)
        return {"number": num, "color": "red" if num % 2 else "yellow",
                "color_inferred": False, "missing": False,
                "thrower_slot": t.position_slot,
                "position": fmt.positions[t.position_slot - 1],
                "rock_of_player": t.rock_of_player, "has_hammer": t.has_hammer,
                "label": fmt.shot_label(end_number, num)}
    doc = {"games": [{"index": 0, "ends": [
        {"number": end_number, "shots": [s(i) for i in range(1, n + 1)]}]}]}
    if with_block:
        doc["format"] = fmt.to_json()
    return doc


def _end(doc, ov):
    return timeline.apply_overrides(doc, ov)["games"][0]["ends"][0]


class TestRoleSwap:
    def test_a_swap_hands_red_first_and_last_to_b(self):
        end = _end(_doubles_end(), {"0.3": {"roles_swapped": {"red": True}}})
        red = [(s["number"], s["position"], s["thrower_slot"], s["rock_of_player"])
               for s in end["shots"] if s["color"] == "red"]
        assert red == [(1, "B", 2, 1), (3, "A", 1, 1), (5, "A", 1, 2),
                       (7, "A", 1, 3), (9, "B", 2, 2)]
        assert end["shots"][0]["label"] == "3rd end, B's first rock"
        assert [s["position"] for s in end["shots"] if s["color"] == "yellow"] == \
               ["A", "B", "B", "B", "A"]

    def test_the_end_records_who_swapped(self):
        end = _end(_doubles_end(), {"0.3": {"roles_swapped": {"red": True, "yellow": False}}})
        assert end["roles_swapped"] == {"red": True}

    def test_unswapping_restores_the_detected_roles(self):
        before = _doubles_end()["games"][0]["ends"][0]["shots"]
        end = _end(_doubles_end(), {"0.3": {"roles_swapped": {"red": False}}})
        fields = ("position", "thrower_slot", "rock_of_player", "label")
        assert [[s[f] for f in fields] for s in end["shots"]] == \
               [[s[f] for f in fields] for s in before]
        assert "roles_swapped" not in end

    def test_a_swap_and_a_move_together(self):
        doc = _doubles_end(n=6, end_number=4)
        for s in doc["games"][0]["ends"][0]["shots"][4:]:
            s["color_inferred"] = s["missing"] = True
        end = _end(doc, {"0.4.5": {"before": 1}, "0.4.6": {"before": 1},
                         "0.4": {"roles_swapped": {"red": True}}})
        assert [s["id"] for s in end["shots"]] == [5, 6, 1, 2, 3, 4]
        assert [s["color"] for s in end["shots"]] == ["red", "yellow"] * 3
        assert [s["position"] for s in end["shots"]] == ["B", "A", "A", "B", "A", "B"]
        assert end["shots"][0]["label"] == "4th end, B's first rock"

    def test_a_fours_document_ignores_an_end_key(self):
        doc = _doubles_end(n=16, fmt=F.FOURS, with_block=False)
        end = _end(doc, {"0.3": {"roles_swapped": {"red": True}}})
        assert end["shots"][0]["position"] == "lead"
        assert "roles_swapped" not in end

    def test_a_malformed_end_patch_is_ignored(self):
        end = _end(_doubles_end(), {"0.3": {"roles_swapped": "red"}})
        assert end["shots"][0]["position"] == "A" and "roles_swapped" not in end
```

Append to `tests/test_service_api.py` (the helpers `world`, `submit`, `work_through` and `signed_in` exist there). Charting a game needs a chart owned by the signed-in user, so follow the pattern of the existing signed-in chart tests in that file:

```python
def test_a_role_swap_is_not_counted_as_a_charted_shot(world):
    headers = signed_in(world)
    slug = submit(world, ip="9.9.9.9").json()["slug"]
    world["client"].post(f"/c/{slug}/overrides.json", headers=headers,
                         json={"0.1.1": {"user_score": 3},
                               "0.1": {"roles_swapped": {"red": True}}})
    rows = world["client"].get("/api/me/charts", headers=headers).json()
    row = next(r for r in (rows.get("charts") or rows) if r["slug"] == slug)
    assert row["shots_charted"] == 1
```

If the submission or the overrides POST needs a different call shape for a signed-in owner in this test file (headers on `submit`, an `X-Edit-Key`, a version field), use exactly what the neighbouring signed-in chart tests use. The assertion is what matters: two override keys, one charted shot.

- [ ] **Step 2: Run them to verify they fail**

Run: `PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest -q tests/test_timeline.py -k RoleSwap tests/test_service_api.py -k role_swap`
Expected: FAIL. Positions stay "A", there is no `roles_swapped` key, and `shots_charted` is 2.

- [ ] **Step 3: Implement** (in `timeline.py`)

Add above `_renumber`:

```python
def _swapped(patch, fmt) -> dict:
    """Which teams swapped roles this end, from an end-level override.

    Only a format whose two players can trade roles honours it; anything else
    -- a four-player chart, a patch that is not the expected shape -- is no
    swap at all. Only true values count, so turning a swap off is the same as
    never having set it.
    """
    if not fmt.swappable or not isinstance(patch, dict):
        return {}
    wanted = patch.get("roles_swapped")
    if not isinstance(wanted, dict):
        return {}
    return {c: True for c in rules.COLORS if wanted.get(c) is True}


def _assign_throwers(shots, end_number, fmt, swapped) -> None:
    """Who threw each rock of an end whose order did not change.

    A role swap moves the thrower fields and the label, never the number, the
    colour or the hammer.
    """
    for s in shots:
        n = s.get("number")
        if not isinstance(n, int) or not 1 <= n <= fmt.delivered_per_end:
            continue
        swap = bool(swapped.get(s.get("color")))
        t = fmt.throw_info(n, swapped=swap)
        s["thrower_slot"] = t.position_slot
        s["position"] = fmt.positions[t.position_slot - 1]
        s["rock_of_player"] = t.rock_of_player
        s["label"] = fmt.shot_label(end_number, n, swapped=swap)
```

Change `_renumber` to take `swapped=None` and to settle each shot's colour **before** its thrower. The swap depends on the colour, and for four-player the order makes no difference because `throw_info` does not read the colour when nothing is swapped.

```python
def _renumber(shots: list, end_number: int, patched: set, fmt=None, swapped=None) -> None:
    ...docstring as now, plus: "``swapped`` names the teams that traded roles
    this end (doubles)."...
    fmt = fmt or format_mod.FOURS
    swapped = swapped or {}
    anchors = [(i, s["color"]) for i, s in enumerate(shots)
               if not s.get("color_inferred") or shot_identity(s) in patched]
    for i, s in enumerate(shots):
        s["id"] = shot_identity(s)
        s["number"] = i + 1
        if s.get("color_inferred") and anchors and shot_identity(s) not in patched:
            j, color = min(anchors, key=lambda a: abs(a[0] - i))
            s["color"] = color if (i - j) % 2 == 0 else rules.other_color(color)
        swap = bool(swapped.get(s["color"]))
        t = fmt.throw_info(i + 1, swapped=swap)
        s["has_hammer"] = t.has_hammer
        s["thrower_slot"] = t.position_slot
        s["position"] = fmt.positions[t.position_slot - 1]
        s["rock_of_player"] = t.rock_of_player
        s["label"] = fmt.shot_label(end_number, i + 1, swapped=swap)
```

In `apply_overrides`, parse two-part keys as end patches, and apply the swap after the shot patches:

```python
    fmt = format_mod.of_document(document)
    patches: dict[tuple[int, int], dict[int, dict]] = {}
    end_patches: dict[tuple[int, int], dict] = {}
    for key, patch in (overrides or {}).items():
        try:
            parts = [int(part) for part in key.split(".")]
        except ValueError:
            continue
        if len(parts) == 3:
            patches.setdefault((parts[0], parts[1]), {})[parts[2]] = patch
        elif len(parts) == 2:
            end_patches[(parts[0], parts[1])] = patch
    for game in document.get("games", []):
        for end in game["ends"]:
            ident = (game["index"], end_identity(end))
            here = patches.get(ident, {})
            for shot in end["shots"]:
                patch = here.get(shot_identity(shot))
                if patch is not None:
                    shot.update(patch)
                    shot["corrected"] = True
            swapped = _swapped(end_patches.get(ident), fmt)
            ordered = _reorder(end["shots"])
            if ordered is not end["shots"]:
                colour_set = {s for s, p in here.items() if "color" in p}
                _renumber(ordered, end["number"], colour_set, fmt, swapped)
                end["shots"] = ordered
            elif swapped:
                _assign_throwers(end["shots"], end["number"], fmt, swapped)
            if swapped:
                end["roles_swapped"] = swapped
    return document
```

Update `apply_overrides`' docstring: "Overrides are keyed `"<game>.<end>.<shot>"` for a shot, or `"<game>.<end>"` for the end (doubles' `roles_swapped`)…"

In `api.py`'s chart row (~line 1024), count only shot keys:

```python
            # Shot corrections only: an end-level key (a doubles role swap)
            # is not a charted shot.
            "shots_charted": sum(1 for k in chart.overrides if k.count(".") == 2),
```

- [ ] **Step 4: Run the tests**

Run: `PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest -q tests/test_timeline.py tests/test_service_api.py tests/test_viewer_js.py`
Expected: all pass. That includes `test_viewer_js.py`'s existing fours parity tests (`TestMovingAShot`), which prove the reordered `_renumber` still agrees with the unchanged JS for fours.

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/timeline.py src/curling_score/service/api.py tests/test_timeline.py tests/test_service_api.py
git commit -m "timeline: a doubles end-level override swaps a team's two players for that end

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: The JS core reads the format and applies the swap

**Files:**
- Create: `frontend/core/format.mjs`
- Modify: `frontend/core/timeline.mjs` (`throwInfo`, `renumber`, `layout`, `buildGameView`; new `assignThrowers`, `endKey`)
- Modify: `frontend/core/index.mjs` (export format.mjs)
- Modify: `tests/js/singleton.mjs` (its `layout` passes the document's format)
- Test: `tests/test_viewer_js.py`

**Interfaces:**
- Consumes: the document's `format` block (`name`, `positions`, `throw_table`, `swappable`, `delivered_per_end`); Task 1's Python for parity.
- Produces (all exported through `frontend/core/index.mjs`):
  - `FOURS` — the four-player format object;
  - `formatOf(doc)` — returns FOURS unless `doc.format` has array `positions` and `throw_table`;
  - `throwInfo(n, fmt = FOURS, swapped = false)` — returns `{has_hammer, thrower_slot, rock_of_player}`;
  - `shotLabel(endNo, n, fmt = FOURS, swapped = false)`;
  - `roleText(fmt, slot)` — e.g. `"1st & 5th"`, `"2nd–4th"`;
  - `throwerText(shot, fmt)`;
  - `endKey(g, e)` — `"<game index>.<end identity>"`;
  - `swappedOf(g, e, overrides, fmt)` — `{red?: true, yellow?: true}`;
  - `renumber(shots, endNo, fixed, fmt = FOURS, swapped = {})`;
  - `assignThrowers(shots, endNo, fmt, swapped)`;
  - `layout(g, e, overrides, fmt = FOURS)` — returns `{shots, raws, swapped}`;
  - `buildGameView(doc, gi, overrides)` — returns `{doc, gi, game, ends, format}`, where each `ends[i]` is `{end, shots, raws, swapped}`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_viewer_js.py`, which has `run_js`, `doc`, `setup` and the `TestMovingAShot` pattern)

```python
def doubles_doc(shots, end_number=3):
    from curling_score.game import format as F
    d = doc(shots, end_number=end_number)
    d["schema_version"] = 7
    d["format"] = F.DOUBLES.to_json()
    return d


def doubles_shots(n=10, end_number=3):
    from curling_score.game import format as F
    out = []
    for num in range(1, n + 1):
        t = F.DOUBLES.throw_info(num)
        out.append({"number": num, "color": "red" if num % 2 else "yellow",
                    "color_inferred": False, "missing": False, "state_known": True,
                    "position": F.DOUBLES.positions[t.position_slot - 1],
                    "thrower_slot": t.position_slot, "rock_of_player": t.rock_of_player,
                    "has_hammer": t.has_hammer,
                    "label": F.DOUBLES.shot_label(end_number, num),
                    "shot_type": "draw", "stones": []})
    return out


FIELDS = ("id", "number", "color", "label", "position", "rock_of_player",
          "has_hammer", "thrower_slot")


def both(document, overrides):
    import copy
    from curling_score import timeline
    py = timeline.apply_overrides(copy.deepcopy(document), overrides)
    py = [[s.get(f) for f in FIELDS] for s in py["games"][0]["ends"][0]["shots"]]
    js = run_js(setup(document, overrides) +
                f"out(mergedShots(e).map(s => {list(FIELDS)}.map(f => s[f] ?? null)));")
    return py, js


class TestDoublesFormat:
    def test_the_throw_table_comes_from_the_document(self):
        got = run_js(setup(doubles_doc(doubles_shots())) +
                     "const f = formatOf(state.doc);"
                     "out([1,3,9].map(n => throwInfo(n, f)).concat([shotLabel(3, 7, f), roleText(f, 1), roleText(f, 2)]));")
        assert got[:3] == [{"has_hammer": False, "thrower_slot": 1, "rock_of_player": 1},
                           {"has_hammer": False, "thrower_slot": 2, "rock_of_player": 1},
                           {"has_hammer": False, "thrower_slot": 1, "rock_of_player": 2}]
        assert got[3:] == ["3rd end, B's third rock", "1st & 5th", "2nd–4th"]

    def test_fours_is_what_it_always_was(self):
        got = run_js(setup(doc([])) +
                     "const f = formatOf(state.doc);"
                     "out([f.name, throwInfo(13), throwInfo(13, f), shotLabel(2, 16, f)]);")
        assert got[0] == "fours"
        assert got[1] == got[2] == {"has_hammer": False, "thrower_slot": 4, "rock_of_player": 1}
        assert got[3] == "2nd end, skip's second rock"

    def test_a_malformed_format_block_reads_as_fours(self):
        d = doubles_doc(doubles_shots())
        del d["format"]["throw_table"]
        assert run_js(setup(d) + "out(formatOf(state.doc).name);") == "fours"

    def test_the_thrower_row_says_player_and_rock_of_n(self):
        got = run_js(setup(doubles_doc(doubles_shots())) +
                     "const f = formatOf(state.doc);"
                     "out([throwerText(mergedShots(e)[4], f), throwerText(mergedShots(e)[8], f)]);")
        assert got == ["Player B (rock 2 of 3)", "Player A (rock 2 of 2)"]

    def test_fours_thrower_text_is_unchanged(self):
        got = run_js(setup(doc([{"number": 3, "color": "red", "position": "second",
                                 "rock_of_player": 1, "stones": []}])) +
                     "out(throwerText(mergedShots(e)[0], formatOf(state.doc)));")
        assert got == "second (rock 1)"


class TestRoleSwapParity:
    def test_js_and_python_agree_on_a_swap(self):
        py, js = both(doubles_doc(doubles_shots()), {"0.3": {"roles_swapped": {"red": True}}})
        assert js == py
        assert js[0][4] == "B"

    def test_js_and_python_agree_when_a_swap_is_turned_off(self):
        py, js = both(doubles_doc(doubles_shots()), {"0.3": {"roles_swapped": {"red": False}}})
        assert js == py
        assert js[0][4] == "A"

    def test_js_and_python_agree_on_a_swap_with_a_move(self):
        shots = doubles_shots(n=6, end_number=4)
        for s in shots[4:]:
            s["color_inferred"] = s["missing"] = True
            s["state_known"] = False
        py, js = both(doubles_doc(shots, end_number=4),
                      {"0.4.5": {"before": 1}, "0.4.6": {"before": 1},
                       "0.4": {"roles_swapped": {"red": True}}})
        assert js == py
        assert [row[4] for row in js] == ["B", "A", "A", "B", "A", "B"]

    def test_js_ignores_an_end_key_on_a_fours_chart(self):
        shots = [{"number": n, "color": "red" if n % 2 else "yellow", "position": "lead",
                  "rock_of_player": 1, "stones": []} for n in (1, 2)]
        got = run_js(setup(doc(shots, end_number=3), {"0.3": {"roles_swapped": {"red": True}}}) +
                     "out(mergedShots(e).map(s => s.position));")
        assert got == ["lead", "lead"]

    def test_the_view_carries_the_format_and_the_swap(self):
        got = run_js(setup(doubles_doc(doubles_shots()), {"0.3": {"roles_swapped": {"yellow": True}}}) +
                     "const v = buildGameView(state.doc, 0, state.overrides);"
                     "out([v.format.name, v.ends[0].swapped, endKey(v.game, v.ends[0].end)]);")
        assert got == ["doubles", {"yellow": True}, "0.3"]

    def test_the_hack_call_follows_the_person_across_a_swap(self):
        shots = doubles_shots()
        for s in shots:
            s["line"] = {"start": {"x": 0.15 if s["thrower_slot"] == 1 else -0.15}, "curl": "left"}
        got = run_js(setup(doubles_doc(shots), {"0.3": {"roles_swapped": {"red": True}}}) +
                     "const v = buildGameView(state.doc, 0, state.overrides);"
                     "out(v.ends[0].shots.filter(s => s.color === 'red').map(s => [s.position, s.hack && s.hack.side]));")
        # The set positions were written by the detected role: rocks 1 and 9 at
        # +0.15 (right of centre), rocks 3, 5 and 7 at -0.15 (left). After the
        # swap, 1 and 9 are B's and 3, 5, 7 are A's, so each person's hack is
        # read from their own rocks: B right, A left.
        assert got == [["B", "right"], ["A", "left"], ["A", "left"], ["A", "left"], ["B", "right"]]
```

(`playerHacks` groups rocks by `color|thrower_slot` and calls a group "left" when its median x is ≤ 0. With no turn information, `turnOf` gives nothing and the plain median is used. If `turnOf` needs other fields, read it in `frontend/core/line.mjs`; the assertion above must hold as written.)

- [ ] **Step 2: Run them to verify they fail**

Run: `PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest -q tests/test_viewer_js.py -k "DoublesFormat or RoleSwapParity"`
Expected: FAIL (`formatOf is not defined`, and so on).

- [ ] **Step 3: Write `frontend/core/format.mjs`**

```js
/* What kind of game a chart is: how many rocks, and who throws which.
 *
 * Mirrors curling_score.game.format. A doubles timeline carries its whole
 * format block -- throw table and position names included -- so this never
 * has to know doubles by name: it reads the table it is given, and a document
 * without one (every chart made before doubles existed) is four-player.
 */

export const FOURS = Object.freeze({
  name: "fours", stones_per_team: 8, placed_per_team: 0,
  delivered_per_team: 8, delivered_per_end: 16,
  positions: ["lead", "second", "third", "skip"],
  throw_table: [1, 1, 2, 2, 3, 3, 4, 4],
  blank_passes_hammer: false, swappable: false,
});

const NTH = ["first", "second", "third", "fourth", "fifth"];
const NUM = ["1st", "2nd", "3rd", "4th", "5th", "6th", "7th", "8th"];

/* The format a document was analysed as. A block that lacks what the throw
 * arithmetic needs is not trusted: four-player, the shape every older chart
 * has, rather than a crash. */
export function formatOf(doc) {
  const f = doc?.format;
  if (f && Array.isArray(f.positions) && Array.isArray(f.throw_table)
      && f.throw_table.length && f.throw_table.every(Number.isInteger)) return f;
  return FOURS;
}

/* Who throws the end's n-th delivered rock. `swapped` says this team's two
 * players traded roles this end: the slot then names the person, and
 * rock_of_player still counts within the role. */
export function throwInfo(n, fmt = FOURS, swapped = false) {
  const table = fmt.throw_table;
  const k = Math.min((n + 1) >> 1, table.length);   // this team's k-th rock
  const role = table[k - 1];
  let rock = 0;
  for (let i = 0; i < k; i++) if (table[i] === role) rock++;
  const slot = swapped && fmt.swappable ? 3 - role : role;
  return { has_hammer: n % 2 === 0, thrower_slot: slot, rock_of_player: rock };
}

export const ordinalOf = n => n + (n % 100 >= 11 && n % 100 <= 13 ? "th"
  : { 1: "st", 2: "nd", 3: "rd" }[n % 10] || "th");

export function shotLabel(endNo, n, fmt = FOURS, swapped = false) {
  const t = throwInfo(n, fmt, swapped);
  return `${ordinalOf(endNo)} end, ${fmt.positions[t.thrower_slot - 1]}'s `
       + `${NTH[t.rock_of_player - 1]} rock`;
}

/* Which of a team's rocks a player's role throws: "1st & 5th", "2nd–4th". */
export function roleText(fmt, slot) {
  const ks = fmt.throw_table.map((r, i) => (r === slot ? i + 1 : 0)).filter(Boolean);
  if (!ks.length) return "";
  const run = ks.every((k, i) => i === 0 || k === ks[i - 1] + 1);
  if (run && ks.length > 2) return `${NUM[ks[0] - 1]}–${NUM[ks[ks.length - 1] - 1]}`;
  return ks.map(k => NUM[k - 1]).join(" & ");
}

/* The Detail row: "second (rock 1)" in fours, as it always read; "Player B
 * (rock 2 of 3)" where a team is two players. */
export function throwerText(shot, fmt = FOURS) {
  if (!shot) return "—";
  if (!fmt.swappable) return `${shot.position ?? "—"} (rock ${shot.rock_of_player})`;
  const k = Math.min(((shot.number || 1) + 1) >> 1, fmt.throw_table.length);
  const role = fmt.throw_table[k - 1];
  const of = fmt.throw_table.filter(r => r === role).length;
  return `Player ${shot.position ?? "?"} (rock ${shot.rock_of_player} of ${of})`;
}
```

- [ ] **Step 4: Change `frontend/core/timeline.mjs`**

- Replace the local `throwInfo` and `ordinal` with imports from `./format.mjs`: `import { FOURS, formatOf, ordinalOf, shotLabel, throwInfo } from "./format.mjs";`.
- Keep exporting `ordinal` for existing callers: `export const ordinal = ordinalOf;`.
- Keep re-exporting `throwInfo`: the tests and other modules import it from here. Add `export { throwInfo } from "./format.mjs";`, or keep a named re-export. Make sure `index.mjs` does not export `throwInfo` twice (see Step 5).
- `POSITIONS` is no longer needed in this file.

Add:

```js
/* The key of an end-level correction: a doubles team swapping roles. */
export const endKey = (g, e) => `${g.index}.${endIdentity(e)}`;

/* Which teams swapped roles this end. Only a format whose two players can
 * trade roles honours it, and only true counts -- turning a swap off is the
 * same as never having set it. Mirrors timeline._swapped. */
export function swappedOf(g, e, overrides, fmt = FOURS) {
  if (!fmt.swappable) return {};
  const want = overrides[endKey(g, e)]?.roles_swapped;
  if (!want || typeof want !== "object") return {};
  const out = {};
  for (const c of ["red", "yellow"]) if (want[c] === true) out[c] = true;
  return out;
}

/* Who threw each rock of an end whose order did not change. Mirrors
 * timeline._assign_throwers. */
export function assignThrowers(shots, endNo, fmt, swapped) {
  for (const s of shots) {
    if (!Number.isInteger(s.number) || s.number < 1 || s.number > fmt.delivered_per_end) continue;
    const swap = !!swapped[s.color];
    const t = throwInfo(s.number, fmt, swap);
    s.thrower_slot = t.thrower_slot;
    s.position = fmt.positions[t.thrower_slot - 1];
    s.rock_of_player = t.rock_of_player;
    s.label = shotLabel(endNo, s.number, fmt, swap);
  }
}
```

Rewrite `renumber` so the colour is settled before the thrower, exactly as Task 1's Python does:

```js
export function renumber(shots, endNo, fixed, fmt = FOURS, swapped = {}) {
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
    const t = throwInfo(i + 1, fmt, swap);
    s.has_hammer = t.has_hammer;
    s.thrower_slot = t.thrower_slot;
    s.position = fmt.positions[t.thrower_slot - 1];
    s.rock_of_player = t.rock_of_player;
    s.label = shotLabel(endNo, i + 1, fmt, swap);
  });
}
```

In `layout(g, e, overrides)`, add a `fmt = FOURS` parameter and:
- compute `const swapped = swappedOf(g, e, overrides, fmt);`;
- build the colour-fix prefix from the end's identity, `` const prefix = `${g.index}.${endIdentity(e)}.`; ``, which matches Python's `end_identity`;
- call `renumber(shots, e.number, fixed, fmt, swapped)`;
- add an `else if (Object.keys(swapped).length) assignThrowers(shots, e.number, fmt, swapped);` branch for an end with no moves;
- return `{ shots, raws, swapped }`.

In `buildGameView`:
- compute `const format = formatOf(doc);`;
- call `layout(game, e, overrides, format)`;
- store `swapped` on each entry: `{ end: e, shots, raws, swapped }`;
- return `{ doc, gi, game, ends, format }`.

The hack loop stays as it is: `thrower_slot` is the person now.

- [ ] **Step 5: Export and adapt**

In `frontend/core/index.mjs`, add `export * from "./format.mjs";`.
- If `timeline.mjs` also re-exports `throwInfo`, drop that re-export (export it from format.mjs only). Otherwise the star exports clash.
- Grep the repo for `throwInfo(` and `ordinal(` callers (`grep -rn "throwInfo\|ordinal(" frontend/ tests/`) and keep each one working. The fours defaults make that automatic.

In `tests/js/singleton.mjs`, change the adapter's layout so doubles fixtures go through the same code path as the app:

```js
export const layout = e => core.layout(game(), e, state.overrides, core.formatOf(state.doc));
```

- [ ] **Step 6: Run the tests and the lint**

Run: `PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest -q tests/test_viewer_js.py tests/test_timeline.py`
Then: `cd frontend && npx eslint core` (the build runs the full lint in Task 4).
Expected: all pass, including every pre-existing `test_viewer_js.py` test, which must pass unedited.

- [ ] **Step 7: Commit**

(No rebuild yet: the viewer components change in Task 4, and the build is done there once. `tests/test_frontend_build.py` will fail until then. That is expected; do not run it in this task.)

```bash
git add frontend/core/format.mjs frontend/core/timeline.mjs frontend/core/index.mjs tests/js/singleton.mjs tests/test_viewer_js.py
git commit -m "viewer core: the throw table comes from the chart's format, and a doubles role swap follows the person

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Stats, the end summary and the format warning know the format

**Files:**
- Modify: `frontend/core/stats.mjs` (`gatherStats`)
- Modify: `frontend/core/watch.mjs` (`endSummary`)
- Create: nothing (add `formatWarning` to `frontend/core/format.mjs`)
- Test: `tests/test_viewer_js.py`

**Interfaces:**
- Consumes: `buildGameView(...)` → `view.format`, `view.ends[i].{end, shots, swapped}` (Task 2); `FOURS`, `roleText` (Task 2).
- Produces:
  - `gatherStats(view)` returns `{red: {<position>: bucket, ...}, yellow: {...}}`. Positions come from `view.format.positions`, in order, followed by any unknown position seen, so nothing is dropped;
  - `endSummary(view, ei)` gains `powerPlay: {color, side} | null`, `hammerSource: string | null` and `swapped: {red?, yellow?}`;
  - `formatWarning(doc) -> string | null` in format.mjs.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_viewer_js.py`)

```python
class TestDoublesStatsAndSummary:
    def test_doubles_stats_have_a_bucket_per_player(self):
        got = run_js(setup(doubles_doc(doubles_shots())) +
                     "const v = buildGameView(state.doc, 0, state.overrides);"
                     "const s = gatherStats(v);"
                     "out([Object.keys(s.red), s.red.A.thrown, s.red.B.thrown]);")
        assert got == [["A", "B"], 2, 3]

    def test_a_swap_moves_rocks_between_players(self):
        got = run_js(setup(doubles_doc(doubles_shots()), {"0.3": {"roles_swapped": {"red": True}}}) +
                     "const s = gatherStats(buildGameView(state.doc, 0, state.overrides));"
                     "out([s.red.A.thrown, s.red.B.thrown, s.yellow.A.thrown]);")
        assert got == [3, 2, 2]

    def test_an_unknown_position_still_counts(self):
        shots = [{"number": 1, "color": "red", "position": "alternate",
                  "rock_of_player": 1, "stones": []}]
        got = run_js(setup(doc(shots)) +
                     "const s = gatherStats(buildGameView(state.doc, 0, state.overrides));"
                     "out([Object.keys(s.red), s.red.alternate.thrown]);")
        assert got == [["lead", "second", "third", "skip", "alternate"], 1]

    def test_fours_stats_keys_are_unchanged(self):
        got = run_js(setup(doc([])) +
                     "out(Object.keys(gatherStats(buildGameView(state.doc, 0, {})).yellow));")
        assert got == ["lead", "second", "third", "skip"]

    def test_the_end_summary_names_the_power_play(self):
        d = doubles_doc(doubles_shots())
        e = d["games"][0]["ends"][0]
        e["hammer"] = "yellow"
        e["hammer_source"] = "placement"
        e["placement"] = {"hammer": "yellow", "power_play": "left", "complete": True}
        got = run_js(setup(d, {"0.3": {"roles_swapped": {"red": True}}}) +
                     "const v = buildGameView(state.doc, 0, state.overrides);"
                     "const s = endSummary(v, 0);"
                     "out([s.hammer, s.powerPlay, s.hammerSource, s.swapped]);")
        assert got == ["yellow", {"color": "yellow", "side": "left"}, "placement", {"red": True}]

    def test_a_fours_end_summary_has_no_power_play(self):
        got = run_js(setup(doc([])) +
                     "const s = endSummary(buildGameView(state.doc, 0, {}), 0);"
                     "out([s.powerPlay, s.hammerSource]);")
        assert got == [None, None]

    def test_the_format_warning(self):
        fours = doc([])
        fours["format_warning"] = "analysed as fours, but the ends look like doubles"
        d = doubles_doc(doubles_shots())
        d["format"]["check"] = {"looks_like": "fours", "ends": 6, "median_offered": 15}
        quiet = doubles_doc(doubles_shots())
        quiet["format"]["check"] = {"looks_like": "doubles", "ends": 6, "median_offered": 10}
        got = run_js(f"out([formatWarning({json.dumps(fours)}), formatWarning({json.dumps(d)}),"
                     f" formatWarning({json.dumps(quiet)}), formatWarning({json.dumps(doc([]))})]);")
        assert got[0] == "analysed as fours, but the ends look like doubles"
        assert "doubles" in got[1] and "four-player" in got[1]
        assert got[2] is None and got[3] is None
```

(`json` is already imported at the top of `tests/test_viewer_js.py`.)

- [ ] **Step 2: Run them to verify they fail**

Run: `PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest -q tests/test_viewer_js.py -k DoublesStatsAndSummary`
Expected: FAIL (a missing `B` bucket, no `powerPlay`, no `formatWarning`).

- [ ] **Step 3: Implement**

In `stats.mjs`, change `gatherStats`. Drop the `POSITIONS` import if nothing else in the file uses it, and import `FOURS` from `./format.mjs`.

```js
export function gatherStats(view) {
  const positions = (view.format || FOURS).positions;
  const out = {};
  const bucket = (c, p) => (out[c][p] ||= { thrown: 0, graded: 0, sum: 0, types: {} });
  for (const c of ["red", "yellow"]) {
    out[c] = {};
    for (const p of positions) bucket(c, p);
  }
  for (const { shots } of view.ends)
    for (const s of shots) {
      if (!out[s.color] || !s.position) continue;
      // A position the format does not list -- an old chart edited by hand --
      // still counts, in its own bucket, rather than vanishing from the report.
      const b = bucket(s.color, s.position);
      const id = typeOf(s);
      const row = (b.types[id] ||= { thrown: 0, graded: 0, sum: 0 });
      b.thrown++; row.thrown++;
      if (isGraded(s)) {
        b.graded++; b.sum += s.user_score;
        row.graded++; row.sum += s.user_score;
      }
    }
  return out;
}
```

In `watch.mjs`, `endSummary`: add to the returned object:

```js
    // Doubles: the placement names the hammer and any power play (the house
    // stone's team calls it), and a team may have swapped roles this end.
    hammerSource: end.hammer_source || null,
    powerPlay: end.placement?.power_play
      ? { color: end.placement.hammer, side: end.placement.power_play } : null,
    swapped: view.ends[ei]?.swapped || {},
```

Append to `format.mjs`:

```js
/* A sentence for the chart page when the ends do not look like the format
 * they were analysed as -- the pipeline flags it, never overrides it. */
export function formatWarning(doc) {
  if (doc?.format_warning) return String(doc.format_warning);
  const check = doc?.format?.check;
  if (check && check.looks_like && check.looks_like !== "unknown"
      && check.looks_like !== doc.format.name)
    return `This game was analysed as doubles, but its ends look like a `
         + `four-player game (a median of ${check.median_offered} rocks offered `
         + `across ${check.ends} ends). If it is, resubmit it as four-player.`;
  return null;
}
```

- [ ] **Step 4: Run the tests**

Run: `PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest -q tests/test_viewer_js.py`
Expected: all pass, including every pre-existing report and summary test.

- [ ] **Step 5: Commit**

```bash
git add frontend/core/stats.mjs frontend/core/watch.mjs frontend/core/format.mjs tests/test_viewer_js.py
git commit -m "viewer core: doubles stats per player, the end's power play, and the format warning

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: The viewer shows doubles, and lets a charter mark a swap

**Files:**
- Modify: `frontend/viewer/Report.jsx` (positions and headings from the format)
- Modify: `frontend/viewer/ChartPanel.jsx` (the Detail thrower row; a new "End" box with the swap toggles)
- Modify: `frontend/viewer/Timing.jsx` (`EndHead`: the power-play badge)
- Modify: `frontend/viewer/App.jsx` (the `setSwapped` action; the format warning; pass `view` where needed)
- Modify: `src/curling_score/viewer/style.css` (a `.wpp` badge style, and `#endBox` spacing)
- Build: `cd frontend && npm run build` (updates `src/curling_score/viewer/app.js`, `src/curling_score/service/static/site.js` and `frontend/.buildstamp.json`)
- Test: `tests/test_frontend_build.py`, `tests/test_viewer_js.py`

**Interfaces:**
- Consumes: from Tasks 2–3, `view.format`, `view.ends[i].swapped`, `endKey(g, e)`, `throwerText(shot, fmt)`, `roleText(fmt, slot)`, `gatherStats` (keys = positions), `endSummary(...).powerPlay/.swapped` and `formatWarning(doc)`; `edit.patch` (overrides.mjs) and `store.apply(next, key)` (runtime/overridesStore.mjs).
- Produces: UI only.

- [ ] **Step 1: Report** (`Report.jsx`)

Stop importing `POSITIONS`, and import `roleText` from `../core/index.mjs`. `TeamCard` gets a `format` prop and iterates `Object.keys(stats)` (Task 3 puts the format's positions first). Its per-player heading is:

```jsx
<h3>{format.swappable ? `${p} · ${roleText(format, format.positions.indexOf(p) + 1)}` : p}</h3>
```

Fours headings (`lead`…) are unchanged; doubles reads `A · 1st & 5th`, `B · 2nd–4th`. For a position the format does not list, `indexOf` is −1, so show just `p` (guard: `format.positions.includes(p)`). In `Report`, the thrown and graded totals sum over `Object.keys(stats[c])`, and `<TeamCard ... format={view.format} />`.

- [ ] **Step 2: Detail thrower row** (`ChartPanel.jsx`)

`Detail` takes `format` (pass `view.format` from `ChartPanel`) and uses `["Thrower", throwerText(shot, format)]`, with `throwerText` imported from `../core/index.mjs`. For fours this is exactly today's text (Task 2's `test_fours_thrower_text_is_unchanged`).

- [ ] **Step 3: The end's swap toggles** (`ChartPanel.jsx` and `App.jsx`)

In `App.jsx`, next to `patch`, add an action that edits the end-level key. It uses the same store call a shot edit uses, so saving, versions and the read-only guard all behave identically:

```jsx
  const setSwapped = useCallback((color, on) => {
    const at = view.ends[ui.ei];
    if (config.readOnly || !at || !view.format.swappable) return;
    const key = endKey(view.game, at.end);
    const cur = store.getOverrides()[key]?.roles_swapped || {};
    store.apply(edit.patch(store.getOverrides(), key,
                           { roles_swapped: { ...cur, [color]: on } }), key);
  }, [config.readOnly, view, ui.ei]);
```

Put it on the `actions` object (`setSwapped`), and import `endKey` with the other core imports at the top of App.jsx.

In `ChartPanel`, render an "End" box after `<Detail …/>`, only for a swappable format and not read-only:

```jsx
      {view.format.swappable && !config.readOnly ? (
        <div id="endBox">
          <h3>End {view.ends[cursor.ei]?.end.number}</h3>
          {["red", "yellow"].map(c => (
            <label key={c} className="chk">
              <input type="checkbox" checked={!!view.ends[cursor.ei]?.swapped?.[c]}
                     onChange={e => actions.setSwapped(c, e.target.checked)} />
              {` ${view.game.teams[c]?.name || c} swapped roles (player B threw first and last)`}
            </label>
          ))}
        </div>
      ) : null}
```

`cursor` in `ChartPanel` is the prop it already receives. It carries `ei`; check its shape in App.jsx, where it is passed as `cursor={{ ei: ui.ei, … }}`, and read the end index from wherever it lives.

- [ ] **Step 4: The power-play badge** (`Timing.jsx`)

In `EndHead`, destructure `powerPlay` from `summary` and render it after the hammer:

```jsx
      {powerPlay ? <span className="wpp">power play · {powerPlay.color}, {powerPlay.side}</span> : null}
```

In `src/curling_score/viewer/style.css`, add next to the `.wham` rule (if there is none, next to `.tend`):

```css
.wpp { font-size:12px; padding:1px 7px; border-radius:999px; background:var(--warnbg); color:var(--warn); white-space:nowrap; }
#endBox { margin-top:12px; }
#endBox label { display:block; margin:4px 0; }
```

- [ ] **Step 5: The format warning** (`App.jsx`)

Import `formatWarning`. At the top of `<main>`, before the play card:

```jsx
        {formatWarning(doc) ? <div className="warn" id="formatWarning">{formatWarning(doc)}</div> : null}
```

- [ ] **Step 6: Build, lint and test**

Run: `cd frontend && npm run build` (lint first; it refuses to bundle on a lint error). Then:
`PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest -q tests/test_frontend_build.py tests/test_viewer_js.py`
Expected: all pass.

- [ ] **Step 7: Commit** (sources and built output together)

```bash
git add frontend/viewer/Report.jsx frontend/viewer/ChartPanel.jsx frontend/viewer/Timing.jsx frontend/viewer/App.jsx \
        src/curling_score/viewer/style.css src/curling_score/viewer/app.js \
        src/curling_score/service/static/site.js frontend/.buildstamp.json
git commit -m "viewer: doubles players, the power-play badge, the swap toggles and the format warning

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: The site offers doubles, when it is on, and tags doubles games

**Files:**
- Modify: `src/curling_score/service/api.py` (new `GET /api/features`)
- Modify: `frontend/site/Submit.jsx` (the Format select)
- Modify: `frontend/site/Games.jsx` (a "doubles" tag)
- Modify: `src/curling_score/service/static/site.css` (`.pill.doubles`)
- Build: `cd frontend && npm run build`
- Test: `tests/test_service_api.py`, `tests/test_frontend_build.py`

**Interfaces:**
- Consumes: `Settings.doubles_enabled` (on main); the `format` field `/api/games` already returns per row.
- Produces: `GET /api/features` → `{"doubles": bool}`, with `Cache-Control: public, max-age=300`.

- [ ] **Step 1: Write the failing test** (append to `tests/test_service_api.py`)

```python
def test_the_site_learns_whether_doubles_is_on(world):
    r = world["client"].get("/api/features")
    assert r.status_code == 200 and r.json() == {"doubles": False}
    world["settings"].doubles_enabled = True
    assert world["client"].get("/api/features").json() == {"doubles": True}
```

- [ ] **Step 2: Run it to verify it fails**

Run: `PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest -q tests/test_service_api.py -k learns_whether_doubles`
Expected: FAIL (404).

- [ ] **Step 3: Implement**

In `api.py`, next to `/api/auth/config`:

```python
    @app.get("/api/features")
    def features():
        """What the site pages may offer. Doubles stays off until the viewer and
        pipeline are ready for it, and the submit form only shows the choice
        when it is on."""
        return JSONResponse({"doubles": bool(settings.doubles_enabled)},
                            headers={"Cache-Control": "public, max-age=300"})
```

In `Submit.jsx`:
- Import `useEffect`, and add `const [format, setFormat] = useState("");` and `const [doubles, setDoubles] = useState(false);`.
- Fetch the features once on mount: `useEffect(() => { fetch("/api/features").then(r => r.json()).then(f => setDoubles(!!f.doubles)).catch(() => {}); }, []);`.
- In `submit`, add `if (format) body.format = format;`.
- Next to the Sheet field, render the select only when `doubles` is true:

```jsx
              {doubles ? (
                <div style={{ width: 150 }}>
                  <label htmlFor="format">Game</label>
                  <select id="format" value={format} onChange={e => setFormat(e.target.value)}>
                    <option value="">from title</option>
                    <option value="fours">4-player</option>
                    <option value="doubles">Doubles</option>
                  </select>
                </div>
              ) : null}
```

In `Games.jsx`'s row, inside the League cell's display (in `LeagueCell`, next to the league text, in both the read-only and editable branches), add
`{game.format === "doubles" ? <> <span className="pill doubles">doubles</span></> : null}`.
Keep the league editing behaviour unchanged.

In `site.css`, next to the other `.pill` rules:
`.pill.doubles { background:var(--okbg); color:var(--ok); border-color:transparent; margin-left:6px; }`

- [ ] **Step 4: Build and test**

Run: `cd frontend && npm run build`, then `PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest -q tests/test_service_api.py tests/test_frontend_build.py tests/test_service_auth.py`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/service/api.py frontend/site/Submit.jsx frontend/site/Games.jsx \
        src/curling_score/service/static/site.css src/curling_score/service/static/site.js \
        src/curling_score/viewer/app.js frontend/.buildstamp.json tests/test_service_api.py
git commit -m "site: the submit form offers doubles when it is on, and the catalogue tags doubles games

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

(Include `src/curling_score/viewer/app.js` only if the build changed it. `git status` shows it.)

---

### Task 6: Look at it: a doubles chart and a four-player chart in headless Chrome

**Files:**
- Create: `docs/superpowers/plans/2026-09-26-doubles-viewer.check.md` (what was checked, and the findings)

**Interfaces:**
- Consumes: the built viewer from Tasks 4–5; `scripts/devserve.py`; `scripts/cdp.mjs` (`attach()`, `send()`, `eval()`); a doubles timeline at `/tmp/claude-1000/-home-tcuser-src-curling-score/8ad4ca23-f106-41d7-964f-3701cc57b355/scratchpad/p3r/p3r-8J3r5FhFFd4.json` (from the phase 3 acceptance run; copy it to your own scratch dir first); a four-player timeline (`out/timeline.json` in the main checkout, `/home/tcuser/src/curling_score/out/timeline.json`, read-only).
- Produces: the check note and screenshots (screenshots stay in scratch; the note lists what each showed).

- [ ] **Step 1: Serve the doubles chart**

`PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python scripts/devserve.py <copy of p3r-8J3r5FhFFd4.json>` (run in the background). It prints the `/c/` editing link, plus the `/s/` and `/g/` links.

- [ ] **Step 2: Drive headless Chrome**

Start `google-chrome --headless=new --remote-debugging-port=9222 --user-data-dir=<scratch>/chrome about:blank`, in the background. Use `scripts/cdp.mjs` to open each link and screenshot it (`Page.captureScreenshot`) at desktop (1400×900) and at phone size (390×844 with `mobile: true`, plus `Emulation.setTouchEmulationEnabled`). Check, and record in the note:
- Rock labels read "B's second rock" style, and the Detail thrower row reads "Player B (rock 2 of 3)".
- An end with a power play (8J3r ends 5 and 7) shows the "power play · red, left" badge in its end head; other ends show none.
- In the `/c/` editing link, the End box shows two swap checkboxes. Tick "red swapped roles" in one end, then check that red's rock 1 now reads "B's first rock", and that the report (Report tab or button) moves red's counts between A and B. Untick it and check it all returns.
- Rock 1's house shows the two placed stones.
- The report headings read "A · 1st & 5th" and "B · 2nd–4th".
- No format warning shows on this chart (its check says doubles).

Then serve the four-player timeline and check: labels "second's first rock", the thrower row "second (rock 1)", report headings lead/second/third/skip, no End box, no power-play badge, no warning.

- [ ] **Step 3: Write the note and commit it**

Put the findings in `docs/superpowers/plans/2026-09-26-doubles-viewer.check.md`: what was checked, pass or fail per item, and anything odd. Take screenshots with `Read` to look at them yourself; do not commit them. If something fails, do not fix it here: report DONE_WITH_CONCERNS with the specifics.

```bash
git add docs/superpowers/plans/2026-09-26-doubles-viewer.check.md
git commit -m "viewer: headless check of a doubles chart and a four-player chart

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Left to later plans

- The broomless thrown line for doubles (spec phase 6), which is what gives doubles an aim line, a hack call and curl. Until then a doubles chart's hack call is empty: `line` is null without a broom.
- The deploy (spec phase 7): the 16-game four-player harness, both deploys, and turning `DOUBLES_ENABLED` on.
- A guard never seen (phase 3 residual I1).
