# Doubles Switched Ends Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A doubles player can say, from "I played…", which ends their pair switched order. The game then credits both partners' rocks correctly in those ends, for everyone who views it.

**Architecture:**
- **Stored:** the switched ends live on `Source.switched_ends`, per colour, keyed by end identity.
- **Served:** `game_doc` writes them into each end as `roles_swapped` and re-credits the rocks.
- **Honoured:** Python `apply_overrides` and JS `layout` honour a document-held `roles_swapped`, with any end-level override key layered on top.
- **Saved:** a play save carries the claimed colour's switched ends. A chart's End box saves through its own route. An admin route moves old chart-level swaps onto their games.

**Tech Stack:** Python 3 / FastAPI (`src/curling_score/service/api.py`), Firestore + in-memory repos, React viewer (`frontend/`, built with esbuild into `src/curling_score/viewer/app.js`), pytest + node for JS tests.

**Spec:** `docs/superpowers/specs/2026-10-21-doubles-switched-ends-design.md`

## Global Constraints

- **Doubles only.** Every path checks `fmt.swappable`. A fours document must come out exactly as it does today.
- **Ends are keyed by end identity:** `timeline.end_identity(end)` / `endIdentity(e)`, the number the end was detected with. Show users the displayed `number`.
- **Only `true` counts** in `roles_swapped`. An end-level override key can add a switch, never remove one held on the document.
- **The game's first end as served** (`games[0].ends[0]`) is never switched by any route.
- **Stored shape:** `Source.switched_ends = {"red": {"ends": [4, 5, 6, 7], "by": "<uid>", "at": "<iso>"}}`. A colour with no switched ends has no key. `None` / `{}` means nothing is switched.
- **Copy**, verbatim:
  - "We switched during the game"
  - "Tap each end where you threw the other rocks."
  - slot sub-labels "End {n}: 1st & 5th" and "End {n}: 2nd–4th"
  - "Your partner gets the rocks you did not, in every end: saving marks the game for both of you."
  - that note's prefix "You threw the other rocks in end {list}. " / "…in ends {list}. "
  - End box: "{Team} switched this end: B threw 1st & 5th"
  - End box, signed out: "Sign in to mark switched ends"
- **Work in a git worktree** on branch `switched-ends` (superpowers:using-git-worktrees). Another session shares the main checkout: stage only your own files, never `git add -A`.
- **Build the frontend** with `cd frontend && npm run build`, never esbuild directly, and commit the built output with the sources.
- **Tests:** run the affected files only. The full suite OOMs on this box (exit 137).
- **Firestore:** run the Firestore half of `tests/test_repo_contract.py` on the emulator for any `firestore_repo.py` change. The commands are in Task 1.
- **Never mark a production game as a test.** The example game's players are real.

## Review Focus

1. **A partner saving with the box untouched must not wipe the marks.** Sending no `switched_ends` key leaves them. Pinned in Task 5 (`test_the_partners_save_without_the_key_keeps_them`) and Task 6 (`playBody` untouched).
2. **Saving one team's claim must not touch the other team's marks.** Pinned in Task 1 (`test_two_colours_are_kept_apart`) and Task 5 (`test_only_the_claimed_colour_changes`).
3. **A malformed stored value must not break serving the game.** It switches nothing. Pinned in Task 2 (`test_malformed_marks_switch_nothing`).
4. **A play start set after the marks were saved** must keep them on the same ends by identity, even though the displayed numbers shift. Pinned in Task 4 (`test_the_marks_follow_the_end_not_its_number`).
5. **Clearing a claim leaves the game's marks.** Pinned in Task 5 (`test_clearing_a_play_keeps_the_marks`).

---

### Task 1: Store switched ends on the game

**Files:**
- Modify: `src/curling_score/service/records.py` (`Source`, next to `short_handed`)
- Modify: `src/curling_score/service/repo.py` (Protocol, next to `set_entered_score` at :58; in-memory version next to :313)
- Modify: `src/curling_score/service/firestore_repo.py` (next to `set_entered_score` at :232)
- Test: `tests/test_repo_contract.py` (the class holding `test_an_entered_score_round_trips`, :515)

**Interfaces:**
- Produces:
  - `Source.switched_ends: dict | None = None`.
  - `repo.set_switched_ends(source_id: str, color: str, entry: dict | None) -> Source | None`. `entry` is `{"ends": [int], "by": str, "at": str}`. `None` removes that colour. It returns `None` for an unknown source.

- [ ] **Step 1: Write the failing tests** in the entered-score class, using its `_source(repo)` helper:

```python
def test_switched_ends_round_trip(self, repo):
    self._source(repo)
    assert not repo.get_source("s_1").switched_ends
    entry = {"ends": [4, 5, 6, 7], "by": "uid-sarah", "at": "2026-10-21T12:00:00+00:00"}
    assert repo.set_switched_ends("s_1", "red", entry).switched_ends == {"red": entry}
    assert repo.get_source("s_1").switched_ends == {"red": entry}

def test_two_colours_are_kept_apart(self, repo):
    self._source(repo)
    repo.set_switched_ends("s_1", "red", {"ends": [2], "by": "a", "at": "t"})
    repo.set_switched_ends("s_1", "yellow", {"ends": [3], "by": "b", "at": "t"})
    assert set(repo.get_source("s_1").switched_ends) == {"red", "yellow"}

def test_clearing_one_colour_leaves_the_other(self, repo):
    self._source(repo)
    repo.set_switched_ends("s_1", "red", {"ends": [2], "by": "a", "at": "t"})
    repo.set_switched_ends("s_1", "yellow", {"ends": [3], "by": "b", "at": "t"})
    repo.set_switched_ends("s_1", "red", None)
    assert repo.get_source("s_1").switched_ends == {"yellow": {"ends": [3], "by": "b", "at": "t"}}

def test_switched_ends_on_an_unknown_game_is_none(self, repo):
    assert repo.set_switched_ends("s_nope", "red", {"ends": [2], "by": "a", "at": "t"}) is None
```

- [ ] **Step 2: Run them and see them fail**

Run: `.venv/bin/python -m pytest tests/test_repo_contract.py -q -k "switched or colours"`
Expected: FAIL (`AttributeError: ... set_switched_ends`).

- [ ] **Step 3: Implement**
  - Add the `Source` field, with a comment in the style of `short_handed`'s.
  - Add the Protocol method.
  - In memory: copy the dict and pop or set the colour, under `self._lock`.
  - Firestore: `ref.update({FieldPath("switched_ends", color).to_api_repr(): DELETE_FIELD or dict(entry)})`, as `set_entered_score` does.

- [ ] **Step 4: Run them and see them pass, in memory and on the emulator**

```bash
.venv/bin/python -m pytest tests/test_repo_contract.py -q -k "switched or colours"
JAVA=/usr/lib/jvm/java-21-openjdk-amd64/bin/java ~/google-cloud-sdk/platform/cloud-firestore-emulator/cloud_firestore_emulator start --host=127.0.0.1 --port=8399 &
FIRESTORE_EMULATOR_HOST=127.0.0.1:8399 PYTHONPATH=src .venv/bin/python -m pytest tests/test_repo_contract.py -o addopts="" -q
```

Expected: all pass, with no Firestore skips. Stop the emulator by the **java** PID, not the wrapper's.

- [ ] **Step 5: Commit**: `git add` the four files, then `git commit -m "records: a game's switched ends, per colour, kept apart"`

---

### Task 2: The timeline honours a switch held on the document

**Files:**
- Modify: `src/curling_score/timeline.py`: `_swapped` (:590) and `apply_overrides` (:713). Add `apply_switched_ends` after `assign_short_handed` (:760).
- Test: `tests/test_timeline.py`, a new `class TestSwitchedEnds` after `TestRoleSwap` (:1172). It reuses `_doubles_end`.

**Interfaces:**
- Produces:
  - `apply_switched_ends(document: dict, marks: dict | None) -> dict`. It edits and returns `document`.
    - For a swappable format, each end whose `end_identity` is in `marks[colour]["ends"]` gets `roles_swapped[colour] = True`, merged into any already there, and its shots go through `_assign_throwers(shots, end["number"], fmt, end["roles_swapped"])`.
    - It ignores: colours other than red/yellow; an entry that is not a dict; `ends` that is not a list; members that are not ints, or are bools; identities the game does not have.
  - `_swapped(end: dict, patch, fmt) -> dict`: the union of `end.get("roles_swapped")` and `patch["roles_swapped"]`, true values only. `apply_overrides` passes `end`.
- Consumes: nothing new.

- [ ] **Step 1: Write the failing tests.** Add a helper `_doubles_game(numbers=(1, 2, 3))`: one game with one `_doubles_end` per number (each end's `number` = n). Then:

```python
RED_SWITCHED = ["B", "A", "A", "A", "B"]     # red's rocks 1, 3, 5, 7, 9

def _red(end):
    return [s["position"] for s in end["shots"] if s["color"] == "red"]

def test_a_marked_end_hands_first_and_last_to_b(self):
    doc = timeline.apply_switched_ends(_doubles_game(), {"red": {"ends": [2], "by": "u", "at": "t"}})
    e1, e2, e3 = doc["games"][0]["ends"]
    assert _red(e2) == RED_SWITCHED and e2["roles_swapped"] == {"red": True}
    assert e2["shots"][0]["label"] == "2nd end, B's first rock"
    assert _red(e1) == _red(e3) == ["A", "B", "B", "B", "A"]
    assert "roles_swapped" not in e1 and "roles_swapped" not in e3

def test_marks_follow_the_end_identity_not_the_number(self):
    doc = _doubles_game()
    doc["games"][0]["ends"][1]["id"] = 7            # shown as 2, detected as 7
    timeline.apply_switched_ends(doc, {"red": {"ends": [7]}})
    assert _red(doc["games"][0]["ends"][1]) == RED_SWITCHED

def test_a_fours_document_is_left_alone(self):
    doc = _doubles_end(n=16, fmt=F.FOURS, with_block=False)
    before = copy.deepcopy(doc)
    assert timeline.apply_switched_ends(doc, {"red": {"ends": [3]}}) == before

@pytest.mark.parametrize("marks", [None, {}, {"red": [2]}, {"red": {"ends": "2"}},
                                   {"red": {"ends": ["2", True]}}, {"blue": {"ends": [2]}},
                                   {"red": {"ends": [99]}}])
def test_malformed_marks_switch_nothing(self, marks):
    before = _doubles_game()
    assert timeline.apply_switched_ends(_doubles_game(), marks) == before

def test_a_switch_on_the_document_survives_a_move(self):
    doc = _doubles_end(n=6, end_number=4)
    for s in doc["games"][0]["ends"][0]["shots"][4:]:
        s["color_inferred"] = s["missing"] = True
    timeline.apply_switched_ends(doc, {"red": {"ends": [4]}})
    end = _end(doc, {"0.4.5": {"before": 1}, "0.4.6": {"before": 1}})
    assert [s["position"] for s in end["shots"]] == ["B", "A", "A", "B", "A", "B"]

def test_an_override_adds_but_never_removes(self):
    doc = timeline.apply_switched_ends(_doubles_end(), {"red": {"ends": [3]}})
    end = _end(doc, {"0.3": {"roles_swapped": {"red": False, "yellow": True}}})
    assert end["roles_swapped"] == {"red": True, "yellow": True}
```

- [ ] **Step 2: Run them and see them fail**

Run: `.venv/bin/python -m pytest tests/test_timeline.py -q -k "SwitchedEnds or RoleSwap"`
Expected: the new tests FAIL (`AttributeError: apply_switched_ends`). `TestRoleSwap` still passes.

- [ ] **Step 3: Implement** `apply_switched_ends` and the new `_swapped(end, patch, fmt)`, and pass `end` into `_swapped` in `apply_overrides`.

- [ ] **Step 4: Run** the command from step 2. Expected: all pass, `TestRoleSwap` included.

- [ ] **Step 5: Commit**: `git commit -m "timeline: a switch held on the document, overrides only add to it"`

---

### Task 3: The viewer core honours the document-held switch (JS parity)

**Files:**
- Modify: `frontend/core/timeline.mjs`, `swappedOf` (:44)
- Test: `tests/test_viewer_js.py`, a new `class TestSwitchedEndsParity` after `TestRoleSwapParity` (:3570)
- Build: `src/curling_score/viewer/app.js` (built output)

**Interfaces:**
- Consumes: `timeline.apply_switched_ends` (Task 2).
- Produces: `swappedOf(g, e, overrides, fmt)` returns the union of `e.roles_swapped` and the override's `roles_swapped`, true values only.

- [ ] **Step 1: Write the failing tests.** The helper is `switched_doc()`, which is `timeline.apply_switched_ends(doubles_doc(doubles_shots()), {"red": {"ends": [3]}})`:

```python
def test_as_served(self):
    py, js = both(switched_doc(), {})
    assert js == py and js[0][4] == "B"

def test_with_a_move(self):
    shots = doubles_shots(n=6, end_number=4)
    for s in shots[4:]:
        s["color_inferred"] = s["missing"] = True
        s["state_known"] = False
    d = timeline.apply_switched_ends(doubles_doc(shots, end_number=4), {"red": {"ends": [4]}})
    py, js = both(d, {"0.4.5": {"before": 1}, "0.4.6": {"before": 1}})
    assert js == py and [r[4] for r in js] == ["B", "A", "A", "B", "A", "B"]

def test_a_malformed_document_switch_swaps_nothing(self):
    d = doubles_doc(doubles_shots())
    d["games"][0]["ends"][0]["roles_swapped"] = {"red": 1}
    got = run_js(setup(d) + "out(buildGameView(state.doc, 0, state.overrides).ends[0].swapped);")
    assert got == {}

def test_an_override_adds_to_a_document_switch(self):
    got = run_js(setup(switched_doc(), {"0.3": {"roles_swapped": {"yellow": True}}}) +
                 "out(buildGameView(state.doc, 0, state.overrides).ends[0].swapped);")
    assert got == {"red": True, "yellow": True}

def test_the_players_slice_matches_the_view(self):
    TestPlayerSliceParity()._check(switched_doc(), {}, "red", 2)
```

- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_viewer_js.py -q -k "SwitchedEnds or RoleSwap or PlayerSlice"`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement** the union in `swappedOf`.
- [ ] **Step 4: Run** the command from step 2. Expected: all pass.
- [ ] **Step 5: Build and commit**: `cd frontend && npm run build`, then `git add frontend/core/timeline.mjs tests/test_viewer_js.py src/curling_score/viewer/app.js` plus any other file the build stamp touches, then `git commit -m "viewer: a switch held on the document, as apply_overrides reads it"`

---

### Task 4: Serve the marks in every document of the game

**Files:**
- Modify: `src/curling_score/service/api.py`, `game_doc` (:546): after the short-handed block, before the team names.
- Test: create `tests/test_switched_ends.py`.

**Interfaces:**
- Consumes: `Source.switched_ends` and `repo.set_switched_ends` (Task 1); `timeline.apply_switched_ends` (Task 2).
- Produces:
  - in `game_doc`: `doc = timeline.apply_switched_ends(doc, src.switched_ends)` when `src` is not None;
  - test helpers in `tests/test_switched_ends.py` that Tasks 5 and 8 reuse:
    - `doubles_game_doc(ends=3)`: `played_doc(F.DOUBLES)` with its end copied into ends 1..`ends`, each with its own `number`, `start_s` and `end_s` (each end 900 s after the last), and shot `t_rest_s` shifted to match;
    - `a_doubles_game(w, ends=3) -> (slug, share, src)` via `a_played_game(w, doubles_game_doc(ends))`;
    - `red_by_end(doc) -> list[list[str]]`: red rocks' positions per end;
    - `mark(w, src, color, ends)`: calls `w["repo"].set_switched_ends` directly.
- Unchanged on purpose: game clips. My shots looks clips up by its own rows' rock names (`frontend/core/myshots.mjs:343`), so a clip of a rock that is no longer the player's is never shown. The report asks for the new rocks' clips when it opens (`ask_for_clips`). This settles the spec's clip check; no task is needed.

- [ ] **Step 1: Write the failing tests** (`class TestServed`). Red throws the odd rocks in `played_doc`, so its 1st & 5th are rocks 1 and 9.

```python
PLAIN, SWITCHED = ["A", "B", "B", "B", "A"], ["B", "A", "A", "A", "B"]

def test_the_game_link_credits_a_switched_end(self, w):
    _, _, src = a_doubles_game(w)
    mark(w, src, "red", [2])
    doc = get(w, f"/g/{src}/timeline.json").json()
    assert red_by_end(doc) == [PLAIN, SWITCHED, PLAIN]
    assert doc["games"][0]["ends"][1]["roles_swapped"] == {"red": True}

def test_a_chart_and_its_share_link_get_it(self, w):
    slug, share, src = a_doubles_game(w)
    mark(w, src, "red", [2])
    for path in (f"/c/{slug}/timeline.json", f"/s/{share}/timeline.json"):
        assert red_by_end(get(w, path).json())[1] == SWITCHED

def test_a_fours_game_ignores_a_stray_mark(self, w):
    _, _, src = a_played_game(w)
    before = get(w, f"/g/{src}/timeline.json").json()
    w["repo"].update_source(src, switched_ends={"red": {"ends": [1]}})
    assert get(w, f"/g/{src}/timeline.json").json() == before

def test_each_partners_report_follows_the_switch(self, w):
    _, _, src = a_doubles_game(w)
    mark(w, src, "red", [2])
    put(w, src, {"path": f"/g/{src}/", "color": "red", "slot": 1})
    put(w, src, {"path": f"/g/{src}/", "color": "red", "slot": 2}, headers=ALEX)
    def mine(headers):
        doc = get(w, f"/api/me/plays/{src}/doc", headers).json()["doc"]
        return [[s["number"] for s in e["shots"] if s["color"] == "red" and "track" in s]
                for e in doc["games"][0]["ends"]]
    assert mine(SARAH) == [[1, 9], [3, 5, 7], [1, 9]]
    assert mine(ALEX) == [[3, 5, 7], [1, 9], [3, 5, 7]]

def test_the_marks_follow_the_end_not_its_number(self, w):
    _, _, src = a_doubles_game(w)
    mark(w, src, "red", [3])
    end2 = get(w, f"/g/{src}/timeline.json").json()["games"][0]["ends"][1]
    w["repo"].update_source(src, play_start_s=end2["start_s"])      # end 1 trimmed off
    ends = get(w, f"/g/{src}/timeline.json").json()["games"][0]["ends"]
    assert [e["number"] for e in ends] == [1, 2]
    assert red_by_end({"games": [{"ends": ends}]}) == [PLAIN, SWITCHED]
```

- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_switched_ends.py -q`. Expected: FAIL. Nothing is switched yet.
- [ ] **Step 3: Implement** the one call in `game_doc`. Add a comment saying why it is on the source, in the style of the short-handed comment.
- [ ] **Step 4: Run**:
  - `.venv/bin/python -m pytest tests/test_switched_ends.py tests/test_short_handed.py tests/test_plays.py -q`
  - Expected: all pass.
- [ ] **Step 5: Commit**: `git commit -m "service: serve a game's switched ends into every document of it"`

---

### Task 5: Save the marks, from a play and from a chart's End box

**Files:**
- Modify: `src/curling_score/service/api.py`:
  - `api_save_play` (:2107);
  - a new helper `checked_switched_ends`, beside `play_target` (:2091);
  - a new route after `api_set_teams` (:1233).
- Test: `tests/test_switched_ends.py` (`class TestSavingWithAPlay`, `class TestTheEndBoxRoute`).

**Interfaces:**
- Consumes: Task 4's helpers; `play_target`, `chart_doc`, `game_doc`, `may_edit_chart`, `require_user`, `repo.set_switched_ends`.
- Produces:
  - `checked_switched_ends(doc: dict, value) -> list[int]`: sorted and distinct. It raises `HTTPException(422, …)` with exactly these messages:
    - `"only a doubles game has switched ends"`: the format is not swappable;
    - `"switched ends are a list of distinct end numbers"`: not a list, a bool or non-int member, or duplicates;
    - `"the first end sets who is who: it cannot be switched"`: the first end is included;
    - `"the game has no end {n}"`: an unknown identity.
  - `PUT /api/me/plays/{source_id}` takes an optional `switched_ends: list[int]`.
    - It is validated against the document the saver is looking at: `chart_doc(chart, run, True)` when `path` is a chart, otherwise `game_doc(run, src.game_index, src, src.play_start_s)`. That document is loaded only when the key is present.
    - A list stores `{"ends", "by": me.id, "at": _iso(now())}`; `[]` clears that colour.
    - The response's `play` gains `"switched_ends": [int]`, the stored ends for the claimed colour.
    - Log the change with the user id, as `api_set_teams` logs names.
  - `POST /api/games/{source_id}/switched-ends`, body `{path, color, ends}`. It answers `{"ok": True, "color": c, "ends": [int]}`.
    - `require_user` (401, or 503 with accounts off).
    - `path` is resolved by `play_target`. A `g` link gets 422 `"switched ends are marked on a chart"`. An `s` link gets 403 `"this is a view-only link"`. If `shown.id != source_id`: 409 `"that link is a different game"`.
    - `may_edit_chart(chart, me.id)` False: 403 `"only the person or team who made this chart can change it"`.
    - `color` not red/yellow: 422 `"color must be red or yellow"`.
    - The rate limit is `repo.bump_rate_limit("switch:" + me.id, now(), PLAY_HOUR_LIMIT, PLAY_DAY_LIMIT)`.
    - Log the change with the user id, as `api_set_teams` does.

- [ ] **Step 1: Write the failing tests.** For `TestSavingWithAPlay`, the helper is `play(w, src, extra, headers=SARAH)`, which is `put(w, src, {"path": f"/g/{src}/", "color": "red", "slot": 1, **extra}, headers)`.

```python
def test_a_play_saves_its_teams_switched_ends(self, w):
    _, _, src = a_doubles_game(w)
    r = play(w, src, {"switched_ends": [3, 2]})
    assert r.status_code == 200, r.text
    assert r.json()["play"]["switched_ends"] == [2, 3]
    got = w["repo"].get_source(src).switched_ends["red"]
    assert (got["ends"], got["by"]) == ([2, 3], "uid-sarah")

def test_leaving_the_key_out_leaves_them(self, w): ...      # save [2], save without key -> [2]
def test_an_empty_list_clears_them(self, w): ...            # save [2], save [] -> no "red" key
def test_the_partners_save_without_the_key_keeps_them(self, w): ...   # SARAH [2]; ALEX slot 2, no key -> [2]
def test_only_the_claimed_colour_changes(self, w): ...      # mark yellow [3]; red save [2] -> yellow still [3]
def test_clearing_a_play_keeps_the_marks(self, w): ...      # save [2]; DELETE /api/me/plays/{src} -> still [2]
def test_saved_from_a_chart_checks_the_charts_ends(self, w): ...   # path /c/{slug}/ with [2] -> 200

@pytest.mark.parametrize("bad,detail", [
    ("2", "switched ends are a list of distinct end numbers"),
    ([2, 2], "switched ends are a list of distinct end numbers"),
    ([True], "switched ends are a list of distinct end numbers"),
    ([1], "the first end sets who is who: it cannot be switched"),
    ([9], "the game has no end 9")])
def test_bad_switched_ends_are_refused(self, w, bad, detail):
    _, _, src = a_doubles_game(w)
    r = play(w, src, {"switched_ends": bad})
    assert r.status_code == 422 and r.json()["detail"] == detail
    assert not w["repo"].get_source(src).switched_ends

def test_a_fours_game_has_none(self, w):
    _, _, src = a_played_game(w)
    r = put(w, src, {"path": f"/g/{src}/", "color": "red", "slot": 1, "switched_ends": [2]})
    assert r.status_code == 422 and r.json()["detail"] == "only a doubles game has switched ends"
```

For `TestTheEndBoxRoute`, the helper is `endbox(w, src, path, body, headers=MAKER)`, which posts to `/api/games/{src}/switched-ends`. `a_played_game` submits as `MAKER`, so the chart is the maker's.
- `test_the_charts_maker_marks_an_end`: `{path: /c/{slug}/, color: red, ends: [2]}` returns 200, `ends == [2]`, and `/g/` shows end 2 switched.
- `test_someone_elses_chart_is_refused`: as SARAH, 403 with the exact detail.
- `test_signed_out_is_refused`: with headers None, 401.
- `test_a_view_only_link_is_refused`: path `/s/{share}/`, 403.
- `test_a_game_link_is_refused`: path `/g/{src}/`, 422 `"switched ends are marked on a chart"`.
- `test_the_first_end_is_refused`: `ends: [1]`, 422.

- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_switched_ends.py -q`. Expected: the new classes FAIL.
- [ ] **Step 3: Implement** `checked_switched_ends`, the play-route branch and the End box route, as the Interfaces block specifies.
- [ ] **Step 4: Run** `.venv/bin/python -m pytest tests/test_switched_ends.py tests/test_plays.py tests/test_short_handed.py -q`. Expected: all pass.
- [ ] **Step 5: Commit**: `git commit -m "service: save switched ends with a play, or from a chart's End box"`

---

### Task 6: "I played…" marks the switched ends

**Files:**
- Modify: `frontend/core/myshots.mjs`, with its exports in `frontend/core/index.mjs`
- Modify: `frontend/viewer/Played.jsx`
- Modify: `frontend/viewer/App.jsx`: the `playing` snapshot (:827), and `<PlayedDialog … onSaved={reload} />` (:676)
- Modify: `frontend/runtime/plays.mjs` (the `savePlay` doc comment: the body may carry `switched_ends`)
- Modify: `src/curling_score/viewer/style.css` (the `#playedDialog` block, :612; phone overrides at :1285)
- Test: `tests/test_viewer_js.py` (`class TestSwitchedEndsDialog`)
- Build: `src/curling_score/viewer/app.js`

**Interfaces:**
- Consumes: the served `end.roles_swapped` (Task 4) and the PUT's `switched_ends` (Task 5).
- Produces (core, exported):
  - `endChoices(view) -> {id: number, number: number}[]`: `endIdentity(e)` and `e.number` per end of `view`.
  - `switchedEnds(view, color) -> number[]`: the identities of ends whose **document** end has `roles_swapped?.[color] === true`.
  - `endsText(ns: number[]) -> string`. Maximal runs of three or more become "a–b"; every other end stands alone. Parts are joined with ", ", the last with " & ".
  - `switchNote(numbers: number[]) -> string`: the note line, with the verbatim copy in Global Constraints.
  - `playBody({path, color, slot, touched, on, flips}) -> object`: `{path, color, slot}`, plus `switched_ends` only when `touched`. That is `on ? sorted([...flips]) : []`.

- [ ] **Step 1: Write the failing tests** (run with `run_js`):

```python
def test_ends_text(self):
    got = run_js("out([[4,5,6,7],[2,4],[2,3],[2,4,5,6],[2,3,5],[3]].map(endsText));")
    assert got == ["4–7", "2 & 4", "2 & 3", "2 & 4–6", "2, 3 & 5", "3"]

def test_switch_note(self):
    got = run_js("out([switchNote([]), switchNote([3]), switchNote([4,5,6,7])]);")
    tail = "Your partner gets the rocks you did not, in every end: saving marks the game for both of you."
    assert got == [tail, "You threw the other rocks in end 3. " + tail,
                   "You threw the other rocks in ends 4–7. " + tail]

def test_play_body_sends_switched_ends_only_when_touched(self):
    got = run_js("const b = {path: '/g/x/', color: 'red', slot: 1};"
                 "out([playBody({...b, touched: false, on: true, flips: new Set([3])}),"
                 " playBody({...b, touched: true, on: false, flips: new Set([3])}),"
                 " playBody({...b, touched: true, on: true, flips: new Set([5, 3])})]);")
    assert got == [{"path": "/g/x/", "color": "red", "slot": 1},
                   {"path": "/g/x/", "color": "red", "slot": 1, "switched_ends": []},
                   {"path": "/g/x/", "color": "red", "slot": 1, "switched_ends": [3, 5]}]

def test_the_view_says_which_ends_each_team_switched(self):
    d = timeline.apply_switched_ends(doubles_doc(doubles_shots()), {"yellow": {"ends": [3]}})
    got = run_js(setup(d) + "const v = buildGameView(state.doc, 0, state.overrides);"
                 "out([switchedEnds(v, 'red'), switchedEnds(v, 'yellow'), endChoices(v)]);")
    assert got == [[], [3], [{"id": 3, "number": 3}]]
```

- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_viewer_js.py -q -k SwitchedEndsDialog`. Expected: FAIL (not defined).
- [ ] **Step 3: Implement the core helpers.**
- [ ] **Step 4: Run** the command from step 2. Expected: PASS.
- [ ] **Step 5: Implement the dialog** in `Played.jsx`, laid out and worded as board A (`out/mockups/doubles-ends/project/Main.dc.html`).
  - **When it shows:** only when `playing.format.swappable` and `playing.ends.length > 1`.
  - **The snapshot:** App adds `ends: endChoices(view)` and `switched: {red: switchedEnds(view, "red"), yellow: switchedEnds(view, "yellow")}`.
  - **State:** `on`, `flips` (a Set of identities) and `touched`.
    - Picking a colour, or loading a saved play, sets `on = switched[color].length > 0`, `flips = new Set(switched[color])` and `touched = false`.
    - Ticking the box, or tapping a chip, sets `touched`.
  - **Chips:** "End {number}", and under it `roleText(fmt, flipped ? 3 - slot : slot)`, or "—" before a slot is picked. The first end's chip is `disabled`. A flipped chip has class `flip`.
  - **Slot buttons:** while `on`, their role text is prefixed `End {ends[0].number}: `.
  - **Save** sends `playBody(...)`. On success it calls `onSaved()` (App's `reload`) before showing "Saved.".
  - **CSS:** add rules for `.pl-switch`, `.pl-ends` and `.pl-hint`.
    - `.pl-switch` is the checkbox row, `min-height: 44px`.
    - `.pl-ends` is a grid of `repeat(4, minmax(0, 1fr))` with `gap: 6px`. `.pl-ends button.flip` gets `border-color: var(--warn)` and `box-shadow: inset 0 0 0 1px var(--warn)`, with its `span` in `var(--warn)`. `.pl-ends button:disabled` gets `border-style: dashed`.
    - `.pl-hint` is 13 px in `var(--muted)`.
- [ ] **Step 6: Build and lint**: `cd frontend && npm run build`. Expected: eslint clean, build written.
- [ ] **Step 7: Commit**: `git commit -m "I played…: mark the ends a doubles pair switched"`. Include the sources, CSS, test and the built output.

---

### Task 7: The chart's End box writes the game

**Files:**
- Create: `frontend/runtime/switched.mjs`, exporting `saveSwitchedEnds(sourceId, body, token)`. Its POST is shaped like `sendTeams` in `runtime/teams.mjs`.
- Modify: `frontend/core/timeline.mjs` (or `myshots.mjs`): `nextSwitched(view, ei, color, on) -> number[]`. It takes `switchedEnds(view, color)` with this end's identity added or removed, sorted.
- Modify: `frontend/viewer/App.jsx`, `setSwapped` (:188)
- Modify: `frontend/viewer/ChartPanel.jsx`, `EndBox` (:512)
- Modify: `src/curling_score/viewer/style.css` (the sign-in line)
- Test: `tests/test_viewer_js.py` (`nextSwitched`)
- Build: `src/curling_score/viewer/app.js`

**Interfaces:**
- Consumes: the End box route (Task 5); `switchedEnds` (Task 6); `accountsOn`, `whoIsSignedIn` and `signInNow` (`runtime/auth.mjs`).
- Produces: `setSwapped(color, on)`.
  - **The game route:** when `config.hosted && doc.chart?.source_id` and `await accountsOn()`, it gets a token via `whoIsSignedIn()`.
    - No one signed in: set the End box's `needSignIn` and write nothing.
    - Otherwise POST `{path: location.pathname, color, ends: nextSwitched(...)}`, then `reload()`. The server's error words show in the End box.
  - **Everywhere else** (the local viewer, or accounts off), it keeps today's override write unchanged.

- [ ] **Step 1: Write the failing test**:

```python
def test_next_switched_adds_and_removes_this_end(self):
    d = timeline.apply_switched_ends(doubles_doc(doubles_shots()), {"red": {"ends": [3]}})
    got = run_js(setup(d) + "const v = buildGameView(state.doc, 0, state.overrides);"
                 "out([nextSwitched(v, 0, 'red', false), nextSwitched(v, 0, 'yellow', true)]);")
    assert got == [[], [3]]
```

- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_viewer_js.py -q -k next_switched`. Expected: FAIL.
- [ ] **Step 3: Implement `nextSwitched`, and run step 2's command.** Expected: PASS.
- [ ] **Step 4: Implement the End box.**
  - No checkboxes when `cursor.ei === 0`.
  - Label `` `${name(c)} switched this end: B threw 1st & 5th` ``.
  - With `needSignIn`, show "Sign in to mark switched ends" and a Sign in button that calls `signInNow()`. The person ticks again after signing in.
  - The iPad `compact` sheet behaves the same.
- [ ] **Step 5: Build**: `cd frontend && npm run build`. Expected: clean.
- [ ] **Step 6: Commit**: `git commit -m "End box: a doubles switch is the game's, saved through its route"`

---

### Task 8: Move old chart-level swaps onto their games

**Files:**
- Modify: `src/curling_score/service/api.py`: `POST /api/admin/switched-ends/migrate`, beside `admin_backfill_thinking` (:1152)
- Create: `scripts/switched_ends.py` (shaped like `scripts/users.py`: `ADMIN_TOKEN`, `BASE_URL`)
- Test: `tests/test_switched_ends.py` (`class TestMigration`)

**Interfaces:**
- Consumes:
  - `repo.list_sources(limit=EVERY_SOURCE)`, `repo.runs_for_video`, `repo.charts_for_run`, `repo.all_plays`;
  - `repo.merge_overrides(slug, {}, [keys], {}, now)`;
  - `repo.set_switched_ends`, `chart_doc`, `timeline.end_identity`.
- Produces: `POST /api/admin/switched-ends/migrate?apply=false` (`require_admin`). It answers `{"ok": True, "applied": bool, "moved": [...], "conflicts": [...], "other_runs": [...], "normalised": [...]}`. Each item is `{"source_id", "chart_id", "color", "ends"}`; a `normalised` item also carries `"plays": [user_id]`.
  - **Which charts:** doubles sources with `merged_into is None`. Take the charts on every run of the source's video that have `chart.source_id == src.id`, and the end-level keys in them that hold a `true` colour.
  - **A chart on another run** goes in `other_runs` and is left untouched.
  - **A first-end swap** stores the complement: the served identities other than the first end, minus those swapped. The item goes in `normalised`, with the user ids of `all_plays()` saved from that chart for that colour.
  - **Existing marks for that colour that disagree** put the item in `conflicts`, and nothing changes for that chart and colour.
  - **With `apply=true`:** `set_switched_ends(src.id, colour, {"ends", "by": "migration", "at": _iso(now())})`, then remove the chart's end keys for that colour. If both colours of a key moved, remove the key.
  - **The script:** `python scripts/switched_ends.py [--apply]` prints the four lists, one item a line.

- [ ] **Step 1: Write the failing tests.** Seed overrides with `w["repo"].save_overrides(slug, {...}, None, T0)`, or the repo's equivalent.
  - `test_a_dry_run_changes_nothing`: the chart has `{"0.2": {"roles_swapped": {"red": True}}}`. The response lists it in `moved`; the source's marks and the chart's overrides are unchanged.
  - `test_apply_moves_and_removes`: afterwards the source has `red: [2]` with `by == "migration"`, and the chart's overrides have no `"0.2"`.
  - `test_a_disagreeing_game_is_a_conflict`: the source already has `red: [3]`. The item is in `conflicts`, and nothing changes.
  - `test_a_first_end_swap_is_normalised`: `{"0.1": {"roles_swapped": {"red": True}}}` on a 3-end game, with SARAH's play saved from the chart (`path /c/{slug}/`). The result is `red: [2, 3]`, with `plays == ["uid-sarah"]`.
  - `test_admin_only`: no token, 401 or 403.
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_switched_ends.py -q -k Migration`. Expected: FAIL (404).
- [ ] **Step 3: Implement** the route and the script.
- [ ] **Step 4: Run** the command from step 2. Expected: PASS.
- [ ] **Step 5: Commit**: `git commit -m "admin: move chart-level doubles swaps onto their games"`

---

### Task 9: Check it end to end

**Files:** none committed. The check kit goes in the session scratchpad.

- [ ] **Step 1: Run the affected test files, one command each.**
  - `tests/test_repo_contract.py`, on the emulator as in Task 1.
  - `tests/test_timeline.py`
  - `tests/test_viewer_js.py`
  - `tests/test_switched_ends.py`
  - `tests/test_plays.py`
  - `tests/test_short_handed.py`
  - `tests/test_service_api.py`. `TestCatalogueAndAdmin::test_the_pages_load` already fails on main; deselect it.

  Expected: all pass.
- [ ] **Step 2: Check the build stamp**: `cd frontend && npm run check`. Expected: clean.
- [ ] **Step 3: Browser check at 390×844**, signed in, with nothing on production.
  - **The server:** serve the in-memory app (`tests.test_service_auth._world(FakeVerifier())`) with the example game's document. That is `/c/5eFAHyvb3mSui54QyaJ242/timeline.json`, already fetched to the scratchpad as `dbl.json`, passed through `work_through(w, doc=..., fmt="doubles")`.
  - **The browser:** fake the Firebase SDK through CDP `Fetch`, as in the "Signed-in browser check" memory, and drive it with `scripts/cdp.mjs`, using touch emulation.
  - **As uid-sarah:** open `/g/<src>/`, then ⋯, then I played…. Pick Red and Player A, tick the box, tap ends 4–7 and Save. Screenshot the dialog before saving.
  - **As uid-alex:** reopen I played…. The box is ticked, ends 4–7 are amber, and picking Player B shows "1st & 5th" in ends 4–7.
  - **My shots** for each: Sarah has 2 rocks in each of ends 1–3 and 3 in each of ends 4–7. Alex has the reverse.
  - **As the chart's maker:** open `/c/<slug>/` and untick end 5 in the End box. `/g/` then shows end 5 unswitched.
- [ ] **Step 4: Hand over** with superpowers:finishing-a-development-branch.
  - Deploy only with the user's go-ahead: the API and the frontend, per the deploy memory.
  - Then run `scripts/switched_ends.py`, dry run first, and show the user its lists before `--apply`.
