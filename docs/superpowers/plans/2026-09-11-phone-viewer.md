# Phone Layout for the Charting Viewer — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the existing charting viewer usable on a phone through responsive CSS, without a second app and without changing the desktop layout.

**Architecture:** One DOM, one stylesheet, CSS-led. A `max-width: 640px` media query re-lays the existing markup: the video pins at the top, the house is cropped to the scoring area beneath it, and the chart panel becomes a bottom sheet with a peek and an expanded state. Controls that CSS cannot gather into the phone's bottom bar are moved once in `index.html` into containers that render identically on desktop. Nothing is ever reparented by JavaScript.

**Tech Stack:** Plain HTML/CSS/ES2020 in `src/curling_score/viewer/`. No framework, no build step, no new dependencies. Tests are pytest driving `node -e`.

**Spec:** `docs/superpowers/specs/2026-09-11-phone-viewer-design.md`

**Wireframes:** https://claude.ai/code/artifact/db3ba9fa-0e55-46cd-ba53-2ee61c054445 (page "Phone layout")

## Global Constraints

Every task's requirements implicitly include all of these.

- **The video iframe must never be reparented.** `app.js` reuses one player for the whole session and the README promises navigation never reloads it. Moving `#video` or `#player` in the DOM destroys the iframe. Use `position: fixed`/`absolute` to place things, never `appendChild`.
- **The desktop layout must not change at any width it serves today.** The existing `@media (max-width:1180px)` and `@media (max-width:820px)` rules are not to be edited. `houseViewBox("full", …)` must return the string `-2.6 -2.6 5.2 8.6` byte for byte.
- **Phone breakpoint is `@media (max-width: 640px)`.** All new phone rules live in one block at the end of `style.css`.
- **Use `100dvh`, never `100vh`.** The shell is a fixed-height column; `100vh` is wrong under a collapsing mobile browser toolbar.
- **Touch targets are 44 px minimum in phone mode.** The sole exception is `#stoneChip` at 40 px, which is status and not tappable.
- **`tests/test_viewer_js.py` runs `app.js` in node with no DOM.** Only pure functions are unit-testable. Every task must leave the existing suite green: `./.venv/bin/pytest tests/test_viewer_js.py -v`.
- **Colours come from the existing custom properties** in `style.css:1-13`. Do not introduce new colour literals in CSS. (`app.js`'s `PAINT` object stays literal, for the reason stated at `app.js:10-12`.)

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `src/curling_score/viewer/app.js` | Three new pure helpers, three new `state` fields, phone branches in `render()`/`drawHouse()`, new handlers in `boot()` | Modify |
| `src/curling_score/viewer/index.html` | Gather scattered controls into `.transport` and `#menu`; add the phone-only elements | Modify |
| `src/curling_score/viewer/style.css` | One new `@media (max-width: 640px)` block, plus a landscape block | Modify |
| `tests/test_viewer_js.py` | Cover the three pure helpers | Modify |

---

### Task 1: The three pure helpers

Everything testable in this plan lives here. Tasks 2–9 wire these up and have no automated coverage, so this task is where correctness is actually pinned down.

**Files:**
- Modify: `src/curling_score/viewer/app.js` (add functions near `isBlank` at line 179; extend `module.exports` at line 878)
- Test: `tests/test_viewer_js.py`

**Interfaces:**
- Consumes: `isBlank(s)` (`app.js:179`), `R` (`app.js:17`)
- Produces:
  - `houseViewBox(mode, aspect)` → `string`. `mode` is `"full"` or `"crop"`; `aspect` is the rendered box's width ÷ height. Returns an SVG `viewBox` value.
  - `peekMode(shot)` → `"grade" | "order"`.
  - `renumberNotice(before, after)` → `string | null`. Both arguments are merged shot objects.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_viewer_js.py`:

```python
class TestHouseViewBox:
    """The desktop crop is load-bearing: it must not drift by a character."""

    def test_the_full_view_is_the_string_the_desktop_has_always_used(self):
        got = run_js('out(houseViewBox("full", 0.605));')
        assert got == "-2.6 -2.6 5.2 8.6"

    def test_an_unusable_aspect_falls_back_to_the_full_view(self):
        for bad in ("0", "-1", "NaN", "undefined"):
            got = run_js(f'out(houseViewBox("crop", {bad}));')
            assert got == "-2.6 -2.6 5.2 8.6", bad

    def test_the_crop_is_always_as_wide_as_the_sheet(self):
        x, y, w, h = (float(v) for v in
                      run_js('out(houseViewBox("crop", 390/321));').split())
        assert (x, w) == (-2.6, 5.2)

    def test_the_crop_fills_the_box_it_is_given(self):
        x, y, w, h = (float(v) for v in
                      run_js('out(houseViewBox("crop", 390/321));').split())
        assert w / h == pytest.approx(390 / 321, abs=1e-3)

    def test_the_crop_is_centred_on_the_tee_so_the_house_stays_whole(self):
        x, y, w, h = (float(v) for v in
                      run_js('out(houseViewBox("crop", 390/321));').split())
        assert y == pytest.approx(-h / 2, abs=1e-3)
        assert y <= -1.829 and y + h >= 1.829   # the whole 12-foot is visible

    def test_a_box_taller_than_the_sheet_does_not_zoom_past_the_full_view(self):
        x, y, w, h = (float(v) for v in
                      run_js('out(houseViewBox("crop", 0.2));').split())
        assert h == 8.6


class TestPeekMode:
    """A blank's fast path is saying where the rock went, not grading it."""

    def test_a_shot_we_watched_offers_grading(self):
        got = run_js(setup(doc([shot(1, "red", "lead")])) +
                     'out(peekMode(merge(g, e, e.shots[0])));')
        assert got == "grade"

    def test_a_rock_never_seen_offers_the_order_picker(self):
        got = run_js(setup(doc([shot(1, "red", "lead", missing=True)])) +
                     'out(peekMode(merge(g, e, e.shots[0])));')
        assert got == "order"

    def test_an_unreadable_house_offers_the_order_picker(self):
        got = run_js(setup(doc([shot(1, "red", "lead", state_known=False)])) +
                     'out(peekMode(merge(g, e, e.shots[0])));')
        assert got == "order"

    def test_grading_a_blank_without_placing_stones_leaves_it_a_blank(self):
        got = run_js(setup(doc([shot(1, "red", "lead", state_known=False)]),
                           {"0.1.1": {"user_score": 3}}) +
                     'out(peekMode(merge(g, e, e.shots[0])));')
        assert got == "order"

    def test_there_being_no_shot_is_not_a_blank(self):
        got = run_js('out(peekMode(null));')
        assert got == "grade"


class TestRenumberNotice:
    """The phone has no chip strip, so the renumber has to say so itself."""

    def test_a_move_says_the_new_number(self):
        got = run_js('out(renumberNotice({number:9}, {number:3}));')
        assert "rock 3" in got

    def test_a_move_that_settled_the_colour_says_where_the_colour_came_from(self):
        got = run_js('out(renumberNotice({number:9},'
                     ' {number:3, color:"red", color_inferred:true}));')
        assert "rock 3" in got and "red" in got and "alternation" in got

    def test_a_rock_whose_colour_was_seen_does_not_claim_alternation(self):
        got = run_js('out(renumberNotice({number:9},'
                     ' {number:3, color:"red", color_inferred:false}));')
        assert "alternation" not in got

    def test_landing_back_on_the_same_number_announces_nothing(self):
        got = run_js('out(renumberNotice({number:4}, {number:4}));')
        assert got is None

    def test_a_missing_shot_announces_nothing(self):
        assert run_js('out(renumberNotice(null, {number:3}));') is None
        assert run_js('out(renumberNotice({number:3}, null));') is None
```

Add `peekMode`, `houseViewBox` and `renumberNotice` to the destructuring list in `run_js` at `tests/test_viewer_js.py:23-25`, so the block reads:

```python
        "const {state, merge, keyFor, mergedShots, gatherStats, pct, avg,\n"
        "       isBlank, isGraded, typeOf, shotVideoTime, stoneAt,\n"
        "       shotKey, rawShot, houseViewBox, peekMode, renumberNotice} = A;\n"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./.venv/bin/pytest tests/test_viewer_js.py -k "HouseViewBox or PeekMode or RenumberNotice" -v`
Expected: every test FAILS. The node subprocess exits non-zero and the assertion at `test_viewer_js.py:30` reports `TypeError: houseViewBox is not a function`.

- [ ] **Step 3: Write the implementations**

In `app.js`, immediately after `const isGraded = ...` (line 181), insert:

```js
/* The phone cannot show the sheet's whole length beside the video, so it shows
 * the house centred on the tee and lets the guards fall off the bottom.
 * Centring is what keeps the 12-foot whole however short the band gets.
 * `aspect` is the rendered box's width over its height; desktop passes nothing
 * that matters, because "full" is always the view it has always had. */
function houseViewBox(mode, aspect) {
  if (mode !== "crop" || !(aspect > 0)) return "-2.6 -2.6 5.2 8.6";
  const h = Math.min(5.2 / aspect, 8.6);
  const n = x => String(+x.toFixed(3));
  return `-2.6 ${n(-h / 2)} 5.2 ${n(h)}`;
}

/* Nobody can grade a rock nobody saw. A blank's fast path is saying where it
 * was thrown and what was on the ice, so its peek carries different controls. */
const peekMode = s => isBlank(s) ? "order" : "grade";

/* Moving a rock renumbers all sixteen. On desktop the chip strip shows that
 * happening; the phone has no strip, so the change announces itself -- and
 * says where the colour came from, since `renumber` settles a blank's colour
 * from the alternation around it and the charter has no other way to know. */
function renumberNotice(before, after) {
  if (!before || !after || before.number === after.number) return null;
  const why = after.color_inferred ? `, ${after.color} by alternation` : "";
  return `End renumbered — this is now rock ${after.number}${why}`;
}
```

Extend the export list at `app.js:878-881` by adding the three names:

```js
  module.exports = { state, merge, keyFor, shotKey, rawShot, mergedShots, layout, identity, READ_ONLY,
                     gatherStats, pct, avg,
                     isBlank, isGraded, typeOf, shotVideoTime, TYPE, TYPES,
                     GROUPS, POSITIONS, stoneAt, R, LIMIT,
                     houseViewBox, peekMode, renumberNotice };
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `./.venv/bin/pytest tests/test_viewer_js.py -v`
Expected: PASS, including every pre-existing test.

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/viewer/app.js tests/test_viewer_js.py
git commit -m "viewer: the three decisions the phone layout turns on"
```

---

### Task 2: Gather the scattered controls

Pure restructuring. `#prev`, `#replay`, `#markCharted` and `#next` must end up in one container because CSS cannot gather non-siblings into the phone's bottom bar; the header's rarely-used controls must end up in one container for the same reason. **The desktop rendering must be pixel-identical when this task is done** — that is the whole acceptance criterion.

**Files:**
- Modify: `src/curling_score/viewer/index.html:7-19` (header), `:23-40` (play card), `:76-78` (the Mark charted row)
- Modify: `src/curling_score/viewer/style.css` (add `.transport` and `#menu` desktop rules)

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: `.transport` (a container holding exactly `#prev`, `#replay`, `#markCharted`, `#next`, in that DOM order) and `#menu` (a container holding `#game`, `#end`, `#copyLink`, `#shareLink`, `#download`, `#reportBtn`, and the two `.row` blocks of playback settings). Tasks 4–9 position both by CSS only.

- [ ] **Step 1: Restructure the header**

Replace `index.html:7-19` with:

```html
<header>
  <h1>Curling Chart</h1>
  <span class="muted" id="src"></span>
  <span id="stoneChip" hidden></span>
  <span class="grow"></span>
  <button id="blanks" class="pill" title="Jump to the next shot needing charting"></button>
  <span id="save" class="pill">saved</span>
  <button id="menuBtn" title="More">&#8943;</button>
  <div id="menu">
    <select id="game" aria-label="Game"></select>
    <select id="end" aria-label="End"></select>
    <button id="copyLink" title="Copy this page's link">Copy link</button>
    <button id="shareLink" title="Copy a link others can view but not edit" hidden>View-only link</button>
    <button id="download" title="Save overrides.json to disk">&#11015;</button>
    <button id="reportBtn">Report</button>
  </div>
</header>
```

`#stoneChip` is added here rather than in Task 3 so the header's DOM is edited once.

- [ ] **Step 2: Gather the transport controls**

In `index.html`, replace the play card's first `.row` — the one holding
`#prev`, `#next`, `#replay`, `#label` and `#link`. Line numbers shift as soon as
the header edit above lands, so match on content, not position:

```html
    <div class="row transport" id="transport">
      <button id="prev" title="Previous shot (&larr;)">&larr;</button>
      <button id="next" title="Next shot (&rarr;)">&rarr;</button>
      <button id="replay" title="Replay from before the throw (v)">&#8635; Replay</button>
      <button id="markCharted">&#10004; Mark charted (Enter)</button>
      <strong id="label" class="grow"></strong>
      <a id="link" target="_blank" rel="noopener">YouTube &rarr;</a>
    </div>
```

and delete the now-duplicated block in the `<aside>`:

```html
    <div class="row" style="margin-top:10px">
      <button id="markCharted" class="grow">&#10004; Mark charted (Enter)</button>
    </div>
```

Give the two sections ids so later tasks can address them — `<section class="card" id="playCard">` and `<section class="card" id="houseCard">`, and `<aside class="card" id="chart">`.

Move the playback-settings `.row` — the one holding `#autoplay` and `#leadin` — into `#menu`, after `#reportBtn`.

- [ ] **Step 3: Keep the desktop rendering identical**

`#menu` must vanish as a box on desktop so its children sit in the header's flex row exactly as before. Add to `style.css`, before the `@media print` block:

```css
/* --- gathered controls -------------------------------------------------- */
/* `display:contents` lets the menu exist as a container for the phone while
   its children lay out in the header row on desktop, as they always have. */
#menu { display: contents; }
#menuBtn { display: none; }
.transport { margin-top: 10px; }
```

`#markCharted` previously sat in the `<aside>` with `class="grow"`; in `.transport` it is a plain button. The chart panel loses its last row, so check its bottom spacing against `git stash`.

- [ ] **Step 4: Verify the desktop is unchanged and nothing broke**

Run: `./.venv/bin/pytest tests/test_viewer_js.py -v`
Expected: PASS. (The tests never touch the DOM, so this only proves `app.js` still parses — which matters, because `boot()` wires every id above.)

Then serve and compare by eye at 1400 px, 1000 px and 900 px wide:

```bash
./.venv/bin/curling-score serve --out out
```

Confirm: the three-column layout is unchanged; `←`, `→`, `⟲ Replay` and `✔ Mark charted` all work; `Enter` still marks charted; the game and end selects, Copy link, `⬇` and Report all still work from the header; no console errors.

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/viewer/index.html src/curling_score/viewer/style.css
git commit -m "viewer: gather the controls CSS will need to move"
```

---

### Task 3: The phone-only elements

Add the elements the phone layout needs and fill them in `render()`. They stay invisible on desktop, so this task changes nothing a desktop user sees.

**Files:**
- Modify: `src/curling_score/viewer/index.html` (inside `header`, `#houseCard`, `#chart`)
- Modify: `src/curling_score/viewer/app.js` (`render()`, `state`)
- Modify: `src/curling_score/viewer/style.css`

**Interfaces:**
- Consumes: `peekMode(shot)` and `renumberNotice(before, after)` from Task 1; `identity(s)` (`app.js:88`), `isBlank(s)`, `mergedShots(e)`.
- Produces: the ids `#stoneChip`, `#sheetHandle`, `#orderRow`, `#orderRowValue`, `#houseDone`, `#recolour`, `#renumbered`; and `state.sheet`, `state.houseMode`, `state.notice`.

- [ ] **Step 1: Add the elements**

`#stoneChip` already exists from Task 2. In `#houseCard`, after the `<svg id="house">`, add:

```html
    <div class="housebar">
      <button id="recolour" title="Swap the selected stone's colour (c)">&#8644;</button>
      <button id="houseDone">Done</button>
    </div>
```

`#recolour` closes the gap the spec names: `toggleStoneColor()` has been reachable only from the keyboard.

In `#chart`, as the very first child, add:

```html
    <button id="sheetHandle" aria-expanded="false" aria-controls="chart">
      <span class="grip"></span><span class="sr">Expand the chart panel</span>
    </button>
```

As the last child of `#chart`, add:

```html
    <div id="renumbered" role="status" hidden></div>
```

Wrap the existing `#moveBefore` so the phone can front it with a one-line summary. Replace `index.html`'s `#orderBox` contents with:

```html
    <div id="orderBox">
      <h3>Order</h3>
      <button id="orderRow" type="button">
        <span class="lbl">Order</span>
        <span id="orderRowValue">detected order</span>
      </button>
      <select id="moveBefore" title="Where in the end this rock was really thrown"></select>
      <div class="muted" style="font-size:12px;margin-top:4px">
        Detection missed a rock and put its blank in the wrong place? Move the
        blank to where it was thrown; the whole end renumbers.
      </div>
    </div>
```

- [ ] **Step 2: Add the state fields**

In `app.js`, extend the `state` object (line 61-67) with three transient fields. They are deliberately **not** added to `savePrefs`/`restorePrefs` — a sheet left open is not a preference:

```js
  saveTimer:null, saving:false, again:false, reporting:false,
  sheet:"peek", houseMode:"", notice:null,
```

- [ ] **Step 3: Fill the elements in `render()`**

In `app.js`, inside `render()` immediately after the `$("label").textContent = …` line, add:

```js
  // Status for the phone header, where the shot chip strip does not fit.
  const chip = $("stoneChip");
  chip.textContent = s ? (isBlank(s) ? "?" : s.number) : "";
  chip.className = s ? `chip ${s.color} ${isBlank(s) ? "unknown" : ""}` : "chip";
  chip.hidden = !s;
```

After the block that fills `#moveBefore` (`app.js:573-578`), add:

```js
  const movedBefore = others.find(x => s?.before === identity(x));
  $("orderRowValue").textContent =
    movedBefore ? `before #${movedBefore.number} (${movedBefore.color})` : "detected order";
  $("orderRow").disabled = !s || READ_ONLY;

  document.body.dataset.peek = peekMode(s);
  document.body.dataset.sheet = state.sheet;
  document.body.dataset.house = state.houseMode;

  const note = $("renumbered");
  note.textContent = state.notice || "";
  note.hidden = !state.notice;
```

- [ ] **Step 4: Hide all of it on desktop**

Add to `style.css`, after the `#menu` rules from Task 2:

```css
#stoneChip, #sheetHandle, #renumbered, #orderRow, .housebar { display: none; }
.sr { position:absolute; width:1px; height:1px; overflow:hidden; clip-path:inset(50%); }
```

- [ ] **Step 5: Keep the view-only link honest**

`boot()` hides every editing control when `READ_ONLY` (`app.js:897-902`). The
buttons added above are editing controls, so extend that list — otherwise a
shared view-only link offers a Done button, a recolour button and a way into
the house editor:

```js
    for (const id of ["download", "delStone", "markThrown", "resetShot", "markCharted",
                      "orderBox", "orderRow", "recolour", "houseDone", "placeStones"]) {
      const el = $(id); if (el) el.hidden = true;   // placeStones lands in Task 7
    }
```

The guard matters: `$` is `document.getElementById`, and `#placeStones` does
not exist until Task 7, so an unguarded loop would throw on every read-only
load between here and there.

- [ ] **Step 6: Verify the desktop is still unchanged**

Run: `./.venv/bin/pytest tests/test_viewer_js.py -v`
Expected: PASS.

Serve and confirm at 1400 px: the page looks exactly as it did before Task 2, the Order select still reorders an end, and the console is clean. `document.body.dataset` should now carry `peek`, `sheet` and `house`; check in devtools that they update as you move between a normal shot and a blank.

- [ ] **Step 7: Commit**

```bash
git add src/curling_score/viewer/index.html src/curling_score/viewer/app.js src/curling_score/viewer/style.css
git commit -m "viewer: the elements a phone needs, dormant on a desktop"
```

---

### Task 4: The phone shell

The layout itself. After this task the phone is usable, with the sheet stuck in its peek state.

**Files:**
- Modify: `src/curling_score/viewer/index.html:3` (viewport meta)
- Modify: `src/curling_score/viewer/style.css` (new block at the end)

**Interfaces:**
- Consumes: `.transport`, `#menu`, `#playCard`, `#houseCard`, `#chart`, `#stoneChip` from Tasks 2–3.
- Produces: the phone shell's geometry, which Tasks 5–9 vary by `body[data-*]`.

- [ ] **Step 1: Let the page reach under the notch**

Replace `index.html:3` with:

```html
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
```

- [ ] **Step 2: Write the phone block**

Append to `style.css`:

```css
/* --- phone -------------------------------------------------------------- */
/* A fixed-height shell, not a scrolling page: the video pins at the top, the
   house takes what is left, and the chart panel becomes a sheet over both.
   Everything is placed with fixed/absolute positioning because the player
   iframe must never be reparented -- see app.js's one-player invariant. */
@media (max-width: 640px) {
  body { height: 100dvh; overflow: hidden; }

  header { height: 48px; padding: 0 8px 0 12px; flex-wrap: nowrap; gap: 8px; }
  h1, #src, #save { display: none; }
  #menuBtn { display: grid; place-items: center; width: 44px; height: 44px;
             flex: 0 0 44px; padding: 0; border-color: transparent; }
  #menu { display: none; position: fixed; top: 52px; right: 8px; z-index: 7;
          background: var(--panel); border: 1px solid var(--line);
          border-radius: 10px; padding: 8px; gap: 8px;
          box-shadow: 0 6px 24px rgb(0 0 0 / .18); min-width: 220px; }
  body[data-menu="open"] #menu { display: grid; }
  #menu > * { width: 100%; height: 44px; }
  #menu .row { height: auto; }
  #blanks { min-height: 44px; }

  #stoneChip { display: grid; place-items: center; flex: 0 0 40px;
               width: 40px; height: 40px; border-radius: 7px; font-size: 15px;
               font-weight: 650; font-variant-numeric: tabular-nums;
               border: 1px solid transparent; }
  #stoneChip.red { background: var(--red); color: #fff; }
  #stoneChip.yellow { background: var(--yellow); color: #1a1a1a; }
  #stoneChip.unknown { background: var(--panel); color: var(--warn);
                       border: 2px dashed var(--warn); }
  /* an id selector outranks the UA's [hidden] rule, so restate it */
  #stoneChip[hidden] { display: none; }

  main { display: block; padding: 0; margin: 48px 0 0;
         height: calc(100dvh - 48px); }
  main > .card { border: 0; border-radius: 0; padding: 0; background: none; }

  /* the video keeps its own box at the top; the house fills the gap down to
     the sheet, and #chart is lifted out of flow onto the bottom of the screen */
  #playCard .shots, #playCard .row:not(.transport) { display: none; }
  #houseCard { position: absolute; top: calc(48px + 56.25vw); bottom: 256px;
               left: 0; right: 0; overflow: hidden; }
  #house { height: 100%; }
  #houseCard .muted { display: none; }

  #flags { position: fixed; top: calc(48px + 56.25vw); left: 0; right: 0;
           z-index: 3; max-height: 92px; overflow-y: auto; padding: 0 10px; }
  #flags:empty { display: none; }

  #chart { position: fixed; left: 0; right: 0; bottom: 0; z-index: 4;
           height: 256px; border-radius: 16px 16px 0 0; border: 0;
           border-top: 1px solid var(--line);
           box-shadow: 0 -3px 14px rgb(0 0 0 / .08);
           padding: 0 12px calc(84px + env(safe-area-inset-bottom));
           overflow: hidden; }
  #chart h2, #chart h3, #chart dl, #chart details,
  #chart #autoType, #orderBox h3, #orderBox .muted,
  #orderBox #moveBefore { display: none; }

  #sheetHandle { display: block; width: 100%; height: 32px; border: 0;
                 background: none; padding: 0; }
  #sheetHandle .grip { display: block; width: 36px; height: 4px; margin: 0 auto;
                       border-radius: 2px; background: var(--line); }

  /* the transport is its own fixed layer so it can sit visually inside the
     sheet without being its child -- which is what lets it live in #playCard */
  .transport { position: fixed; left: 12px; right: 12px; z-index: 5;
               bottom: calc(24px + env(safe-area-inset-bottom));
               margin: 0; gap: 8px; flex-wrap: nowrap; }
  .transport button { height: 48px; flex: 0 0 56px; padding: 0; }
  .transport #markCharted { flex: 1 1 auto; background: var(--accent);
                            color: #fff; border-color: transparent; }
  .transport #label, .transport #link { display: none; }

  /* touch targets */
  .pick button, .scorebtns button, .swatchbtn, .shot,
  #chart button, #chart select, #chart input { min-height: 44px; }
  .scorebtns button { flex: 1 1 0; width: auto; height: 48px; }
  .swatchbtn { width: 44px; height: 44px; }
  .pick { gap: 8px; }
  .pick button { padding: 0 14px; font-size: 13px; }
}
```

`56.25vw` is the 16:9 video's height at full bleed — it tracks the viewport rather than hardcoding 219 px.

- [ ] **Step 3: Wire the overflow menu**

In `app.js`'s `boot()`, next to the other handlers, add:

```js
  $("menuBtn").onclick = () => {
    const open = document.body.dataset.menu === "open";
    document.body.dataset.menu = open ? "" : "open";
  };
  // Anything else you touch closes it, including a control inside it.
  addEventListener("pointerdown", ev => {
    if (document.body.dataset.menu !== "open") return;
    if (ev.target.closest("#menuBtn")) return;
    if (ev.target.closest("#menu") && ev.target.tagName === "DIV") return;
    document.body.dataset.menu = "";
  }, true);
```

- [ ] **Step 4: Verify**

Run: `./.venv/bin/pytest tests/test_viewer_js.py -v`
Expected: PASS.

Then in a browser's device emulation, at **390 × 844**:

- the video sits under the header and does not scroll away;
- the house fills the gap and its circles are round, not ovals;
- the transport bar sits at the bottom, four controls, none under the home indicator;
- `⋯` opens the menu; Report, Copy link and the game/end selects all work from it;
- the page does not scroll horizontally at any point;
- the player does **not** reload when you press `→` — watch the Network tab.

Repeat at **430 × 932** and **360 × 740**.

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/viewer/index.html src/curling_score/viewer/style.css src/curling_score/viewer/app.js
git commit -m "viewer: a shell that fits a phone"
```

---

### Task 5: The sheet's two states

**Files:**
- Modify: `src/curling_score/viewer/app.js` (`boot()`)
- Modify: `src/curling_score/viewer/style.css` (phone block)

**Interfaces:**
- Consumes: `state.sheet`, `#sheetHandle`, `body[data-sheet]` from Task 3.
- Produces: nothing new; Tasks 7–8 assume `body[data-sheet]` is `peek` or `open`.

The spec floated factoring the sheet and house transitions as pure functions so
they could be unit-tested. This plan does not: each is a single assignment
mirrored to a `data-` attribute, and a pure wrapper around `state.sheet =
"open"` would be scaffolding with no logic under it. `peekMode` is different —
it is a real branch on shot state — and it is tested in Task 1.

- [ ] **Step 1: Toggle the state**

In `boot()`:

```js
  $("sheetHandle").onclick = () => {
    state.sheet = state.sheet === "open" ? "peek" : "open";
    $("sheetHandle").setAttribute("aria-expanded", String(state.sheet === "open"));
    render();
  };
```

- [ ] **Step 2: Give the open state its height and its content**

Inside the `@media (max-width: 640px)` block, add:

```css
  body[data-sheet="open"] #chart {
    height: calc(100dvh - 48px - 56.25vw);
    overflow-y: auto;
    overscroll-behavior: contain;
  }
  /* the full form only exists in the open state */
  body[data-sheet="open"] #chart h3,
  body[data-sheet="open"] #chart #typeList,
  body[data-sheet="open"] #chart #missReason,
  body[data-sheet="open"] #chart #note,
  body[data-sheet="open"] #orderRow { display: block; }
  body[data-sheet="peek"] #chart #typeList,
  body[data-sheet="peek"] #chart #missReason,
  body[data-sheet="peek"] #chart #note { display: none; }

  #orderRow { width: 100%; height: 44px; display: flex; align-items: center;
              gap: 10px; margin-top: 12px; text-align: left; }
```

- [ ] **Step 3: Verify**

Run: `./.venv/bin/pytest tests/test_viewer_js.py -v` — PASS.

At 390 × 844: tapping the grip expands the sheet over the house and leaves the video visible; the full form (type list, miss reason, note, Order row) appears; the transport bar stays pinned at the bottom in both states; tapping the grip again collapses it. The sheet scrolls internally without scrolling the page behind it.

- [ ] **Step 4: Commit**

```bash
git add src/curling_score/viewer/app.js src/curling_score/viewer/style.css
git commit -m "viewer: a sheet that peeks and opens"
```

---

### Task 6: The cropped house, and editing it full-screen

**Files:**
- Modify: `src/curling_score/viewer/app.js` (`drawHouse()`, `boot()`)
- Modify: `src/curling_score/viewer/style.css` (phone block)

**Interfaces:**
- Consumes: `houseViewBox(mode, aspect)` from Task 1; `state.houseMode`, `#houseDone`, `#recolour` from Task 3; `toggleStoneColor()` (`app.js`, used at the `c` key).
- Produces: nothing new.

- [ ] **Step 1: Let the SVG choose its own crop**

Remove the hardcoded attribute from `index.html`, leaving `<svg id="house" aria-label="House diagram"></svg>`, and set it in `drawHouse()` instead. At the top of `drawHouse()` (`app.js`), after `const svg = $("house");`:

```js
  // The desktop and the full-screen editor both want the whole sheet; the
  // phone's house band is too short for it, so it crops to what it can show.
  const box = svg.getBoundingClientRect();
  const cropped = state.houseMode !== "edit" && box.width > 0 &&
                  box.height > 0 && box.height < box.width * 1.3;
  svg.setAttribute("viewBox",
    houseViewBox(cropped ? "crop" : "full", box.width / box.height));
```

- [ ] **Step 2: Enter and leave the editor**

In `boot()`:

```js
  $("house").addEventListener("click", () => {
    if (state.houseMode || READ_ONLY) return;
    if (!matchMedia("(max-width: 640px)").matches) return;
    state.houseMode = "edit"; render();
  }, true);
  $("houseDone").onclick = () => { state.houseMode = ""; render(); };
  $("recolour").onclick = () => { if (state.selStone !== null) toggleStoneColor(); };
```

The listener is registered in the capture phase so entering the mode does not also place a stone on the first tap.

- [ ] **Step 3: Give the editor the screen**

Inside the phone block:

```css
  body[data-house="edit"] #houseCard { top: 48px; bottom: 114px; z-index: 8;
                                       background: var(--bg); }
  body[data-house="edit"] #chart,
  body[data-house="edit"] .transport,
  body[data-house="edit"] #playCard { display: none; }
  body[data-house="edit"] .tools { display: flex; position: fixed; z-index: 9;
    left: 0; right: 0; bottom: calc(58px + env(safe-area-inset-bottom));
    height: 56px; margin: 0; padding: 0 12px; background: var(--panel);
    border-top: 1px solid var(--line); }
  body[data-house="edit"] .housebar { display: flex; gap: 8px; position: fixed;
    z-index: 9; left: 12px; right: 12px;
    bottom: calc(12px + env(safe-area-inset-bottom)); }
  body[data-house="edit"] #houseDone { flex: 1 1 auto; height: 46px;
    background: var(--accent); color: #fff; border-color: transparent; }
  body[data-house="edit"] #recolour { flex: 0 0 56px; height: 46px; }
```

- [ ] **Step 4: Verify**

Run: `./.venv/bin/pytest tests/test_viewer_js.py -v` — PASS, including Task 1's `houseViewBox` tests.

At 390 × 844: the house under the video shows the whole 12-foot, centred, with circles round. Tapping it opens the full-screen editor showing the sheet's full length including the guard zone. In the editor, red/yellow swatches place stones, dragging moves them, dragging off removes them, `⇄` swaps a selected stone's colour, Done returns. At 1400 px, tapping the house does **nothing** and the desktop viewBox is still `-2.6 -2.6 5.2 8.6` — check the element in devtools.

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/viewer/index.html src/curling_score/viewer/app.js src/curling_score/viewer/style.css
git commit -m "viewer: crop the house to the band, and give editing the screen"
```

---

### Task 7: The blank's peek

**Files:**
- Modify: `src/curling_score/viewer/style.css` (phone block)
- Modify: `src/curling_score/viewer/index.html` (`#chart`)

**Interfaces:**
- Consumes: `body[data-peek]` (set by `render()` in Task 3, from `peekMode`).
- Produces: `#placeStones`.

- [ ] **Step 1: Add the way into the house**

In `#chart`, immediately after `#orderRow`, add:

```html
      <button id="placeStones" type="button">Place the stones</button>
```

and in `boot()`:

```js
  $("placeStones").onclick = () => { state.houseMode = "edit"; render(); };
```

- [ ] **Step 2: Swap the peek's contents on a blank**

Inside the phone block:

```css
  /* A rock nobody saw cannot be usefully graded, so its peek asks the two
     questions that can be answered: where was it thrown, and what was on the
     ice. The grade row comes back as soon as the house has been placed. */
  #placeStones { display: none; width: 100%; height: 44px; margin-top: 8px; }
  body[data-peek="order"][data-sheet="peek"] #typeGroups,
  body[data-peek="order"][data-sheet="peek"] .scorebtns { display: none; }
  body[data-peek="order"][data-sheet="peek"] #orderRow,
  body[data-peek="order"][data-sheet="peek"] #placeStones { display: flex; }
  body[data-peek="order"][data-sheet="peek"] #orderRow { border-color: var(--accent); }
```

- [ ] **Step 3: Verify**

Run: `./.venv/bin/pytest tests/test_viewer_js.py -v` — PASS.

At 390 × 844, navigate to a blank (the `6 to chart` pill jumps to one): the peek shows the Order row and "Place the stones" instead of the type chips and grade row; the house is hatched and says `STATE UNKNOWN`. Navigate to a normal shot: the type chips and grade row are back. Place the stones on the blank, mark it charted, and confirm its peek reverts to the grading controls.

- [ ] **Step 4: Commit**

```bash
git add src/curling_score/viewer/index.html src/curling_score/viewer/app.js src/curling_score/viewer/style.css
git commit -m "viewer: a blank's fast path is not grading"
```

---

### Task 8: The renumber announces itself

**Files:**
- Modify: `src/curling_score/viewer/app.js` (`boot()`'s `#moveBefore` handler at `app.js:950-957`, plus `#orderRow`)
- Modify: `src/curling_score/viewer/style.css` (phone block)

**Interfaces:**
- Consumes: `renumberNotice(before, after)` from Task 1; `state.notice`, `#renumbered` from Task 3.
- Produces: nothing new.

- [ ] **Step 1: Open the native picker from the row**

In `boot()`:

```js
  $("orderRow").onclick = () => {
    // The native <select> is a full-width picker on iOS and Android, which is
    // better than anything we would draw; the row is only its label.
    $("moveBefore").showPicker ? $("moveBefore").showPicker() : $("moveBefore").click();
  };
```

- [ ] **Step 2: Announce the renumber**

Replace the body of `$("moveBefore").onchange` (`app.js:950-957`) with:

```js
  $("moveBefore").onchange = ev => {
    // Follow the shot to its new place rather than staying on its old slot.
    const was = shot(), id = identity(was);
    if (ev.target.value === "") unpatchShot("before");
    else patchShot({ before:+ev.target.value });
    const shots = mergedShots(end());
    const at = shots.findIndex(x => identity(x) === id);
    state.notice = renumberNotice(was, at >= 0 ? shots[at] : null);
    if (at >= 0 && at !== state.si) state.si = at;
    render();
    if (state.notice) {
      clearTimeout(state.noticeTimer);
      state.noticeTimer = setTimeout(() => { state.notice = null; render(); }, 5000);
    }
  };
```

and add `noticeTimer:null,` to `state` beside `notice:null`.

- [ ] **Step 3: Style the toast**

Inside the phone block:

```css
  /* Floated over the house rather than placed in the sheet, so nothing
     reflows when it arrives or leaves. */
  #renumbered { display: block; position: fixed; z-index: 6;
                left: 12px; right: 12px; bottom: 268px;
                background: var(--ink); color: var(--bg); font-size: 13px;
                line-height: 1.35; border-radius: 9px; padding: 10px 12px;
                box-shadow: 0 2px 12px rgb(0 0 0 / .22); }
  #renumbered[hidden] { display: none; }
  body[data-sheet="open"] #renumbered { bottom: auto; top: calc(56px + 56.25vw); }
```

`#renumbered` keeps `role="status"` at every width even though it is only drawn on the phone: the desktop chip strip is a purely visual signal, so this is the only announcement a screen-reader user gets that sixteen labels were rewritten.

- [ ] **Step 4: Verify**

Run: `./.venv/bin/pytest tests/test_viewer_js.py -v` — PASS, including Task 1's `renumberNotice` tests.

At 390 × 844, on a blank: tap the Order row, pick "before #3", and confirm the native picker opens, the toast appears saying the new number (and "red by alternation" if the colour was inferred), it disappears after about five seconds, and the header chip shows the new number. Then pick "detected order" while already in detected order and confirm **no** toast appears. At 1400 px, confirm no toast is drawn but the reorder still works.

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/viewer/app.js src/curling_score/viewer/style.css
git commit -m "viewer: say so when the end renumbers"
```

---

### Task 9: Landscape

A phone in landscape is about 390 px tall. The pinned video plus the peek sheet do not fit and cropping cannot save it, so the layout falls back to a plain scrolling stack — which is nearly free, because it is the existing single-column layout plus a fixed transport bar.

**Files:**
- Modify: `src/curling_score/viewer/style.css`

**Interfaces:**
- Consumes: everything from Tasks 2–8.
- Produces: nothing.

- [ ] **Step 1: Undo the shell when there is no height for it**

Append to `style.css`, **after** the phone block so it wins on specificity order:

```css
/* --- short screens ------------------------------------------------------ */
/* Landscape on a phone: too short for a pinned video over a sheet. Fall back
   to one scrolling column with the transport pinned, which clips nothing. */
@media (max-height: 520px) {
  body { height: auto; overflow: auto; }
  main { position: static; display: grid; grid-template-columns: 1fr;
         gap: 14px; padding: 14px;
         margin: 0 0 calc(84px + env(safe-area-inset-bottom)); height: auto; }
  main > .card { border: 1px solid var(--line); border-radius: 10px;
                 padding: 12px; background: var(--panel); }
  #houseCard, #flags, #chart { position: static; height: auto; bottom: auto;
                               top: auto; z-index: auto; }
  #chart { border-radius: 10px; border: 1px solid var(--line);
           padding: 12px; box-shadow: none; overflow: visible; }
  #chart h2, #chart h3, #chart #typeList, #chart #missReason,
  #chart #note, #orderBox #moveBefore, #orderBox h3 { display: block; }
  #sheetHandle, #orderRow, #renumbered, #stoneChip { display: none; }
  #playCard .shots { display: flex; }
  .transport { bottom: calc(12px + env(safe-area-inset-bottom)); }
}
```

The shot chip strip comes back here, so the renumber is visible again and the toast is not needed.

- [ ] **Step 2: Verify**

Run: `./.venv/bin/pytest tests/test_viewer_js.py -v` — PASS.

Rotate the emulator to **844 × 390** and **932 × 430**: the page scrolls as one column, nothing is clipped, the transport bar stays at the bottom, the shot chip strip is back, and the full chart form is visible. Rotate back to portrait and confirm the shell returns intact. Check a 1024 × 500 desktop window too — it will take the landscape rules, which is correct: it has the same problem.

- [ ] **Step 3: Commit**

```bash
git add src/curling_score/viewer/style.css
git commit -m "viewer: landscape gets the stack, because nothing else fits"
```

---

## Final verification

Before calling the work done, run the whole suite and walk one end end-to-end on a phone-sized viewport:

```bash
./.venv/bin/pytest
```

Then at 390 × 844, chart a complete end: navigate every shot with the transport, grade several, expand the sheet and add a note and a miss reason, find a blank via the `n`-equivalent pill, move it with the Order row, confirm the toast, place its stones in the full-screen editor, and mark it charted. Confirm at the end that:

- the player never reloaded (Network tab shows one iframe request for the session);
- `out/overrides.json` holds every edit;
- the desktop layout at 1400 px is indistinguishable from `git show HEAD~9:src/curling_score/viewer/index.html` rendered with the old CSS.
