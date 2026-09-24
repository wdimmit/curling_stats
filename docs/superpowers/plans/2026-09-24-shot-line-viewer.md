# Shot Line — Phone Viewer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the read-only phone layout (Watch mode on `/g/` and `/s/`)
with three bottom tabs: House, Detail and Timing. On House and Detail, a swipe
steps through the rocks. Detail draws each rock's thrown line against the
skip's broom from the schema-6 `line` field.

**Architecture:**
- **Pure core.** All the arithmetic goes in a new pure module,
  `frontend/core/line.mjs`: the figure text, the strip geometry, stepping
  across ends, the URL hash and the swipe decision. It is tested in node
  through `tests/js/singleton.mjs`.
- **The shell.** `Watch.jsx` becomes the tab shell. It renders a pager, the
  active pane (new `Detail.jsx`, the promoted `#houseCard`, or new
  `Timing.jsx`) and the tab bar.
- **Per-rock panes.** House and Detail show one rock.
- **Timing.** Timing takes today's rock rows and end header, for the whole
  game.
- **App.** `App.jsx` gains the tab state, restores the cursor from the hash
  and the session, writes the hash, and gains `actions.step` and
  `actions.setTab`.
- **CSS.** CSS alone places the panes. The video is never reparented.

**Tech Stack:**
- React 18;
- esbuild via `frontend/build.mjs`, with its committed outputs;
- ESLint;
- pytest driving node (`tests/test_viewer_js.py`);
- hand-written `src/curling_score/viewer/style.css`.

**Spec:** `docs/superpowers/specs/2026-09-24-shot-line-detail-design.md`
(sections 1, 2, 3 and 5). The data comes from
`docs/superpowers/plans/2026-09-24-shot-line-pipeline.md`.

## Global Constraints

- **Scope.** Only the read-only phone surfaces (`config.readOnly`, behind
  `PHONE_QUERY = "(max-width: 640px) and (min-height: 521px)"`). The desktop
  and charting phone layouts are unchanged.
- **The video element is never reparented.** `#video` keeps zero React
  children, and CSS decides visibility.
- **Coordinates.** `line` is in house metres from the destination tee, +y
  toward the thrower, +x the thrower's right. Its `path` and `hog_path` are
  `[y, x]` pairs; `shot.track` is `[t, x, y]`.
- **Shared geometry.** The throwing hog line is at y = 28.346, the throwing
  tee at 34.747 and the hack line at 38.405.
- **The At-the-broom figure.**
  - "On the broom" when |miss| < 0.10 m.
  - Otherwise `5·round(|m|·100/5)` cm, labelled wide or narrow; left or right
    when `side` is `null`. Left means miss < 0.
- **Confirmation note text.**
  - `true`: "confirmed from behind the thrower".
  - `null`: "not confirmed: hidden from behind the thrower".
  - `false`: "the camera behind the thrower disagrees", with the value greyed.
- **Reasons**, checked in this order:
  1. "This chart predates line measurement" (schema below 6)
  2. "This rock was never seen" (`missing`)
  3. "No broom was held still before the release" (no `target_broom`)
  4. "The hog-line camera lost this rock" (`line` is `null`)
- **Caption.** "Sheet from above, thrower at the bottom · across ×3 · figures
  ±10 cm · wide = the side away from the curl".
- **Swipe.**
  - Horizontal only: |dx| ≥ 50 px and |dx| > 1.5·|dy|.
  - Ignored when it starts within 20 px of the left edge.
  - Left means the next rock.
  - It crosses ends and seeks the video like a tap.
- **Hash.** `#tab=<house|detail|timing>[&g=N]&e=<end number>&s=<rock number>`.
  `g` appears only when the document has more than one game.
- **Tab memory.** The last tab is remembered in prefs (`tab: "detail"` in
  `DEFAULTS`). A first visit opens on Detail.
- **Checkout.** Another session shares it: stage only this plan's files, with
  explicit paths.
- **Commits.** Style `area: sentence`, ending with
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **Build.** After any `frontend/` change, run `cd frontend && npm run build`
  and commit `src/curling_score/viewer/app.js`,
  `src/curling_score/service/static/site.js` and `frontend/.buildstamp.json`.
  `tests/test_frontend_build.py` checks the stamp.

## One deviation from the spec, deliberate

The spec says swiping mid-play pauses following and shows "▶ Back to rock N".
But a swipe seeks the video to the new rock, as tapping a row does, so
following simply carries on from there. The "Back to rock" chip stays for what
it does today: scrolling the Timing list away from the rock being played.
Confirm this with the user before Task 4.

## Review Focus

1. **A game whose last end is short**, or whose first end is empty. Stepping
   past either must not throw or strand the cursor. Pinned in Task 2: stepping
   off the last rock returns `null`, and an empty end is skipped.
2. **A hash naming a rock that no longer exists** (renumbered, or another
   game). It must be ignored rather than opening on a blank pane. Pinned in
   Task 2 (`cursorFromHash` returns `null`) and Task 3 (the session cursor is
   the fallback).
3. **A schema-5 chart**, which is every hosted chart until they are
   reprocessed. Detail must say it predates the measurement, and House and
   Timing must work exactly as today. Pinned in Task 1 (`predates`) and Task 4
   (House falls back to `shot.track`).
4. **A long vertical scroll in the Detail pane** (a small phone). It must not
   trigger a rock change. Pinned in Task 2 (`swipeStep` needs dx at least 1.5
   × dy), and in Task 7's `touch-action: pan-y`.
5. **Opening the House tab on a phone.** The house must crop to the scoring
   area on first open, not show the full sheet. That is today's bug:
   `ui.watch` was never in `cropDeps`. Pinned in Task 3 by a source assertion
   that `ui.tab` is in `cropDeps`.

---

### Task 1: The Detail figures (pure)

**Files:**
- Create: `frontend/core/line.mjs`
- Modify: `frontend/core/index.mjs` (add `export * from "./line.mjs";`)
- Modify: `tests/js/singleton.mjs`
- Test: `tests/test_viewer_js.py`

**Interfaces:**
- Produces:
  - `LINE_SCHEMA = 6`;
  - `restOf(shot) -> {x, y} | null`;
  - `lineX(shot, y) -> number | null`;
  - `lineReason(shot, doc) -> string | null`;
  - `lineFigures(shot, doc)`, which returns
    `{predates, reason, figures: [{key, label, value, note, tick, dim}]}`.

    The keys are, in order, `broom, hack, hog, weight, curl, rest`, and
    `tick` is one of `"confirmed" | "unseen" | "disagrees" | null`;
  - `houseCaption(shot) -> string | null`.

- [ ] **Step 1: Write the failing tests.** Append this to `tests/test_viewer_js.py`:

```python
class TestLineFigures:
    """The Detail pane's six figures, from schema 6's `line`."""

    DOC6 = {"schema_version": 6}

    def figs(self, s, doc=None):
        return run_js(f"out(lineFigures({json.dumps(s)}, {json.dumps(doc or self.DOC6)}));")

    def measured(self, **line):
        base = {"start": {"x": -0.23, "y": 38.07},
                "at_hog": {"x": -0.757, "offset_m": -0.163},
                "at_broom": {"x": -2.359, "miss_m": -0.712},
                "side": "wide", "curl": "right", "confirmed": True,
                "hog_path": [[28.35, -0.757], [25.0, -0.95]],
                "path": [[20.0, -1.18], [1.35, -1.15]], "fit": {"n": 53, "rms_m": 0.004}}
        base.update(line)
        return shot(11, "yellow", "third", target_broom={"x": -1.647, "y": 0.17},
                    long_split_s=13.79, delivered_stone_index=0,
                    stones=[{"color": "yellow", "x": -1.1529, "y": 1.3486}], line=base)

    def by_key(self, got):
        return {f["key"]: f for f in got["figures"]}

    def test_a_wide_throw_confirmed_from_behind_the_thrower(self):
        f = self.by_key(self.figs(self.measured()))
        assert (f["broom"]["value"], f["broom"]["note"], f["broom"]["tick"]) == (
            "70 cm wide", "confirmed from behind the thrower", "confirmed")
        assert (f["hack"]["value"], f["hack"]["note"]) == ("Left", "stone set 23 cm left of centre")
        assert (f["hog"]["value"], f["hog"]["note"]) == ("15 cm wide", "of the hack-to-broom line")
        assert (f["weight"]["value"], f["curl"]["value"]) == ("13.8 s", "1.1 m")
        assert (f["rest"]["value"], f["rest"]["note"]) == ("12-foot", "1.8 m from the button")

    def test_within_ten_centimetres_is_on_the_broom(self):
        f = self.by_key(self.figs(self.measured(at_broom={"x": -1.6, "miss_m": 0.06})))
        assert f["broom"]["value"] == "On the broom"

    def test_hidden_from_behind_the_thrower_keeps_the_number_and_says_so(self):
        f = self.by_key(self.figs(self.measured(confirmed=None)))
        assert (f["broom"]["value"], f["broom"]["note"], f["broom"]["tick"], f["broom"]["dim"]) == (
            "70 cm wide", "not confirmed: hidden from behind the thrower", "unseen", False)

    def test_a_check_that_disagrees_greys_the_number(self):
        f = self.by_key(self.figs(self.measured(confirmed=False)))
        assert (f["broom"]["note"], f["broom"]["dim"]) == ("the camera behind the thrower disagrees", True)

    def test_no_curl_direction_says_left_or_right(self):
        f = self.by_key(self.figs(self.measured(side=None, curl=None,
                                                at_broom={"x": -1.35, "miss_m": 0.30})))
        assert f["broom"]["value"] == "30 cm right"

    def test_an_older_chart_predates_the_measurement(self):
        got = self.figs(self.measured(), {"schema_version": 5})
        assert (got["predates"], got["reason"]) == (True, "This chart predates line measurement")

    def test_each_reason_for_no_line(self):
        cases = [(shot(1, "red", "lead", missing=True, target_broom={"x": 0, "y": 0}), "This rock was never seen"),
                 (shot(1, "red", "lead"), "No broom was held still before the release"),
                 (shot(1, "red", "lead", target_broom={"x": 0.5, "y": 0.0}, line=None),
                  "The hog-line camera lost this rock")]
        for s, why in cases:
            got = self.figs(s)
            f = self.by_key(got)
            assert (got["predates"], got["reason"], f["broom"]["value"], f["broom"]["note"]) == (False, why, "–", why)

    def test_the_house_caption_says_where_it_stopped(self):
        assert run_js(f"out(houseCaption({json.dumps(self.measured())}));") == (
            "Stopped 1.8 m from the button, in the 12-foot")
```

In `tests/js/singleton.mjs`, add passthroughs beside `broomMark`:

```js
export const lineFigures = core.lineFigures;
export const lineReason = core.lineReason;
export const houseCaption = core.houseCaption;
export const lineX = core.lineX;
export const restOf = core.restOf;
```

- [ ] **Step 2: Run them to make sure they fail.**

Run: `.venv/bin/python -m pytest tests/test_viewer_js.py -k "LineFigures" -x`

Expected: FAIL. Node errors because `core.lineFigures` is undefined.

- [ ] **Step 3: Implement it.** Create `frontend/core/line.mjs`:

```js
/* What the Detail pane says about one rock, from schema 6's `line`: where its
 * thrown line passed the skip's broom, where it sat before the push, and where
 * the camera behind the thrower saw it go. Pure -- core/** may not touch the
 * DOM. See docs/superpowers/specs/2026-09-24-shot-line-detail-design.md. */
import { R } from "./constants.mjs";

export const LINE_SCHEMA = 6;
export const HOG_Y = 34.747 - 6.401;      // the throwing hog line, house metres
export const TEE_Y = 34.747;              // the throwing tee
export const HACK_Y = 34.747 + 3.658;     // the hack line
const ON_M = 0.10;                        // inside the measurement's own error
const HACK_CENTRE_M = 0.08;

const cm5 = m => 5 * Math.round((Math.abs(m) * 100) / 5);

/* Where this rock came to rest, when the house says which stone it was. */
export function restOf(shot) {
  const i = shot?.delivered_stone_index;
  const s = Number.isInteger(i) ? shot?.stones?.[i] : null;
  return s && typeof s.x === "number" && typeof s.y === "number" ? { x: s.x, y: s.y } : null;
}

/* The thrown line's x at depth y, through the two points the timeline gives. */
export function lineX(shot, y) {
  const l = shot?.line, b = shot?.target_broom;
  if (!l || !b || l.at_hog?.x == null || l.at_broom?.x == null) return null;
  const k = (l.at_broom.x - l.at_hog.x) / (b.y - HOG_Y);
  return l.at_hog.x + k * (y - HOG_Y);
}

/* Wide is the side away from the curl, narrow the side it curls toward; with
 * no curl direction there is only left and right. */
function sideWord(v, curl) {
  if (curl === "left" || curl === "right") {
    const toward = curl === "right" ? 1 : -1;
    return Math.sign(v) === -toward ? "wide" : "narrow";
  }
  return v > 0 ? "right" : "left";
}

function zone(p) {
  const d = Math.hypot(p.x, p.y);
  if (d <= R.button + R.stone) return "Button";
  if (d <= R.four + R.stone) return "4-foot";
  if (d <= R.eight + R.stone) return "8-foot";
  if (d <= R.inHouse) return "12-foot";
  return p.y > 0 ? "In front" : "Behind";
}

export function lineReason(shot, doc) {
  if (Number(doc?.schema_version) < LINE_SCHEMA) return "This chart predates line measurement";
  if (shot?.missing) return "This rock was never seen";
  if (!shot?.target_broom) return "No broom was held still before the release";
  if (!shot?.line) return "The hog-line camera lost this rock";
  return null;
}

const fig = (key, label, value, note, extra = {}) =>
  ({ key, label, value, note, tick: null, dim: false, ...extra });

export function lineFigures(shot, doc) {
  const reason = lineReason(shot, doc);
  const predates = reason === "This chart predates line measurement";
  const l = reason ? null : shot.line;
  const weight = typeof shot?.long_split_s === "number"
    ? fig("weight", "Weight", `${shot.long_split_s.toFixed(1)} s`, "hog line to hog line")
    : fig("weight", "Weight", "–", "not timed");
  const rest = restOf(shot);
  const restFig = rest
    ? fig("rest", "Came to rest", zone(rest), `${Math.hypot(rest.x, rest.y).toFixed(1)} m from the button`)
    : fig("rest", "Came to rest", "–", "not matched to a stone");
  if (!l) {
    return { predates, reason, figures: [
      fig("broom", "At the broom", "–", reason),
      fig("hack", "Hack", "–", ""), fig("hog", "At the hog line", "–", ""),
      weight, fig("curl", "Curl", "–", ""), restFig] };
  }
  const miss = l.at_broom.miss_m;
  const tick = l.confirmed === true ? "confirmed" : l.confirmed === false ? "disagrees" : "unseen";
  const tickNote = { confirmed: "confirmed from behind the thrower",
                     unseen: "not confirmed: hidden from behind the thrower",
                     disagrees: "the camera behind the thrower disagrees" }[tick];
  const broom = fig("broom", "At the broom",
                    Math.abs(miss) < ON_M ? "On the broom" : `${cm5(miss)} cm ${sideWord(miss, l.curl)}`,
                    tickNote, { tick, dim: tick === "disagrees" });
  let hack = fig("hack", "Hack", "–", "not seen before the push");
  if (l.start) {
    const x = l.start.x;
    hack = Math.abs(x) <= HACK_CENTRE_M
      ? fig("hack", "Hack", "Centre", "stone set on the centre line")
      : fig("hack", "Hack", x < 0 ? "Left" : "Right",
            `stone set ${Math.round(Math.abs(x) * 100)} cm ${x < 0 ? "left" : "right"} of centre`);
  }
  const off = l.at_hog?.offset_m;
  const hog = off == null ? fig("hog", "At the hog line", "–", "needs the hack")
    : fig("hog", "At the hog line",
          Math.abs(off) < ON_M ? "On the line" : `${cm5(off)} cm ${sideWord(off, l.curl)}`,
          "of the hack-to-broom line");
  const end = rest ?? (l.path?.length ? { x: l.path[l.path.length - 1][1], y: l.path[l.path.length - 1][0] } : null);
  const lx = end ? lineX(shot, end.y) : null;
  const curl = end && lx != null
    ? fig("curl", "Curl", `${Math.abs(end.x - lx).toFixed(1)} m`, "from its line to where it stopped")
    : fig("curl", "Curl", "–", "no rest position");
  return { predates, reason, figures: [broom, hack, hog, weight, curl, restFig] };
}

/* The House tab's caption. */
export function houseCaption(shot) {
  const rest = restOf(shot);
  if (!rest) return null;
  const z = zone(rest);
  const where = z === "Button" ? "on the button" : z === "In front" ? "in front of the house"
    : z === "Behind" ? "behind the tee" : `in the ${z}`;
  return `Stopped ${Math.hypot(rest.x, rest.y).toFixed(1)} m from the button, ${where}`;
}
```

Add `export * from "./line.mjs";` to `frontend/core/index.mjs`.

- [ ] **Step 4: Run the tests to make sure they pass.**

Run: `.venv/bin/python -m pytest tests/test_viewer_js.py -k "LineFigures or SkipsBroom" -x`

Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add frontend/core/line.mjs frontend/core/index.mjs tests/js/singleton.mjs tests/test_viewer_js.py
git commit -m "viewer core: the Detail pane's figures from a rock's line against the broom

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Strip geometry, stepping, swipe, hash, track points (pure)

**Files:**
- Modify: `frontend/core/line.mjs`, `frontend/core/charts.mjs`, `frontend/core/constants.mjs`
- Modify: `tests/js/singleton.mjs`
- Test: `tests/test_viewer_js.py`

**Interfaces:**
- Produces:
  - `STRIPBOX = {w: 150, h: 420, y0: -2.3, y1: 38.9, half: 2.375}` and
    `TIMINGBOX = {w: 640, h: 200, padL: 60, padR: 12, padT: 10, padB: 30}`, in
    constants;
  - `stripGeometry(shot, box = STRIPBOX)`, which returns
    `{w, h, rings, hogs, tees, backs, hack, stones, aim, thrown, ext, path, broom, rest, start, miss}`
    or `null`;
  - `stepRock(view, ei, si, d) -> {ei, si} | null`;
  - `swipeStep(dx, dy, x0) -> 1 | -1 | 0`;
  - `parseHash(hash) -> {tab?, g?, e?, s?}`;
  - `formatHash({tab, g, e, s}) -> string`;
  - `cursorFromHash(parsed, viewOf, gameCount) -> {gi, ei, si} | null`;
  - `trackPoints(shot) -> [[x, y], ...]`;
  - `endSpan(geom, k, BOX) -> {x0, x1, y1, y2} | null`, in charts.

- [ ] **Step 1: Write the failing tests.** Append this to `tests/test_viewer_js.py`:

```python
def two_ends():
    d = doc([shot(i, "red" if i % 2 else "yellow", "lead") for i in range(1, 4)])
    first = d["games"][0]["ends"][0]
    d["games"][0]["ends"].append(dict(first, number=2, shots=[shot(i, "red", "lead") for i in (1, 2)]))
    return d


class TestSteppingAndTheHash:
    def test_a_step_off_the_end_s_last_rock_is_the_next_end_s_first(self):
        got = run_js(setup(two_ends()) + "state.si = 2; out(stepRock(1));")
        assert got == {"ei": 1, "si": 0}

    def test_a_step_back_from_an_end_s_first_is_the_last_before_it(self):
        assert run_js(setup(two_ends()) + "state.ei = 1; state.si = 0; out(stepRock(-1));") == {"ei": 0, "si": 2}

    def test_past_the_game_s_last_rock_there_is_nowhere_to_go(self):
        assert run_js(setup(two_ends()) + "state.ei = 1; state.si = 1; out(stepRock(1));") is None

    def test_an_empty_end_is_stepped_over(self):
        view = {"ends": [{"shots": [1, 2, 3]}, {"shots": []}, {"shots": [1, 2]}]}
        got = run_js(f"out([stepRockIn({json.dumps(view)}, 0, 2, 1), stepRockIn({json.dumps(view)}, 2, 0, -1)]);")
        assert got == [{"ei": 2, "si": 0}, {"ei": 0, "si": 2}]

    def test_the_tab_and_rock_round_trip_through_the_hash(self):
        got = run_js(setup(two_ends()) + (
            'const h = formatHash({tab: "detail", g: 1, e: 2, s: 2});'
            'out([h, cursorFromHash(parseHash(h))]);'))
        assert got == ["#tab=detail&e=2&s=2", {"gi": 0, "ei": 1, "si": 1}]

    def test_a_hash_naming_no_such_rock_or_tab_is_ignored(self):
        got = run_js(setup(two_ends()) + (
            'out([cursorFromHash(parseHash("#tab=timing&e=9&s=1")), parseHash("#tab=bogus&e=x")]);'))
        assert got == [None, {}]


class TestSwipe:
    def step(self, dx, dy, x0=200):
        return run_js(f"out(swipeStep({dx}, {dy}, {x0}));")

    def test_left_is_the_next_rock_and_right_the_one_before(self):
        assert (self.step(-80, 5), self.step(80, 5)) == (1, -1)

    def test_a_vertical_scroll_is_not_a_swipe(self):
        assert (self.step(60, 70), self.step(30, 0)) == (0, 0)

    def test_the_browser_s_back_gesture_edge_is_left_alone(self):
        assert self.step(80, 0, x0=12) == 0


class TestStripAndTrack:
    SHOT = TestLineFigures().measured()

    def test_the_broom_and_the_line_s_end_land_where_the_metres_say(self):
        g = run_js(f"out(stripGeometry({json.dumps(self.SHOT)}));")
        assert g["broom"]["x"] == pytest.approx(75 + (-1.647) * 150 / 4.75, abs=0.05)
        assert g["broom"]["y"] == pytest.approx((0.17 + 2.3) * 420 / 41.2, abs=0.05)
        last_y = float(g["ext"].split()[-1].split(",")[1])
        assert last_y == pytest.approx(g["broom"]["y"], abs=0.1)
        assert g["miss"]["label"] == "70 cm"

    def test_no_line_draws_no_strip(self):
        assert run_js(f"out(stripGeometry({json.dumps(shot(1, 'red', 'lead'))}));") is None

    def test_the_house_draws_the_path_from_behind_the_thrower_when_there_is_one(self):
        assert run_js(f"out(trackPoints({json.dumps(self.SHOT)}));") == [[-1.18, 20.0], [-1.15, 1.35]]

    def test_otherwise_the_panel_s_track_as_before(self):
        s = shot(1, "red", "lead", track=[[1.0, 0.1, 4.0], [2.0, 0.2, 1.0]])
        assert run_js(f"out(trackPoints({json.dumps(s)}));") == [[0.1, 4.0], [0.2, 1.0]]

    def test_the_current_end_s_span_on_the_clock_chart(self):
        geom = {"ticks": [{"x": 100, "y1": 8, "y2": 188}, {"x": 200, "y1": 8, "y2": 188}]}
        got = run_js(f"out([endSpan({json.dumps(geom)}, 0, {{padL: 46}}), "
                     f"endSpan({json.dumps(geom)}, 1, {{padL: 46}}), endSpan({json.dumps(geom)}, 5, {{padL: 46}})]);")
        assert got == [{"x0": 46, "x1": 100, "y1": 8, "y2": 188},
                       {"x0": 100, "x1": 200, "y1": 8, "y2": 188}, None]
```

In `tests/js/singleton.mjs`, add:

```js
export const stepRock = d => core.stepRock(view(), state.ei, state.si, d);
export const stepRockIn = core.stepRock;
export const cursorFromHash = parsed => core.cursorFromHash(
  parsed, gi => core.buildGameView(state.doc, gi, state.overrides), state.doc.games.length);
export const parseHash = core.parseHash;
export const formatHash = core.formatHash;
export const swipeStep = core.swipeStep;
export const stripGeometry = core.stripGeometry;
export const trackPoints = core.trackPoints;
export const endSpan = core.endSpan;
```

- [ ] **Step 2: Run them to make sure they fail.**

Run: `.venv/bin/python -m pytest tests/test_viewer_js.py -k "SteppingAndTheHash or Swipe or StripAndTrack" -x`

Expected: FAIL (the functions are undefined).

- [ ] **Step 3: Implement it.**

In `frontend/core/constants.mjs`, beside `TALLBOX`, add:

```js
/* The Detail pane's sheet strip: the whole sheet, thrower at the bottom, in
 * a 150x420 px box -- about 3x wider across than along. Metres y0..y1 from
 * the destination tee. */
export const STRIPBOX = { w: 150, h: 420, y0: -2.3, y1: 38.9, half: 2.375 };
/* The whole-game clock above Timing's list: short, so the list keeps the room. */
export const TIMINGBOX = { w: 640, h: 200, padL: 60, padR: 12, padT: 10, padB: 30 };
```

Append this to `frontend/core/line.mjs`. Change its import line to
`import { R, STRIPBOX } from "./constants.mjs";`.

```js
const SWIPE_MIN_PX = 50;
const SWIPE_EDGE_PX = 20;          // the browser's own back gesture lives here

/* A drag as a rock step: left for the next, right for the one before. */
export function swipeStep(dx, dy, x0) {
  if (x0 < SWIPE_EDGE_PX) return 0;
  if (Math.abs(dx) < SWIPE_MIN_PX || Math.abs(dx) <= 1.5 * Math.abs(dy)) return 0;
  return dx < 0 ? 1 : -1;
}

/* The rock `d` steps from (ei, si), across ends and over empty ones; null
 * past either end of the game. */
export function stepRock(view, ei, si, d) {
  const ends = view?.ends ?? [];
  let e = ei, s = si + d;
  while (e >= 0 && e < ends.length) {
    const n = ends[e].shots.length;
    if (s >= 0 && s < n) return { ei: e, si: s };
    if (s < 0) { e -= 1; if (e >= 0) s = ends[e].shots.length - 1; }
    else { e += 1; s = 0; }
  }
  return null;
}

const TABS = ["house", "detail", "timing"];

export function parseHash(hash) {
  const out = {};
  for (const part of String(hash || "").replace(/^#/, "").split("&")) {
    const [k, v] = part.split("=");
    if (k === "tab" && TABS.includes(v)) out.tab = v;
    else if ((k === "g" || k === "e" || k === "s") && /^\d+$/.test(v ?? "")) out[k] = Number(v);
  }
  return out;
}

export function formatHash({ tab, g, e, s }) {
  const parts = [`tab=${tab}`];
  if (g && g > 1) parts.push(`g=${g}`);
  parts.push(`e=${e}`, `s=${s}`);
  return `#${parts.join("&")}`;
}

/* A parsed hash to a cursor: game by position (1-based), end and rock by
 * their numbers as the view now has them. null when any of it is gone. */
export function cursorFromHash(parsed, viewOf, gameCount) {
  if (parsed?.e == null || parsed?.s == null) return null;
  const gi = parsed.g ? parsed.g - 1 : 0;
  if (gi < 0 || gi >= gameCount) return null;
  const view = viewOf(gi);
  const ei = view.ends.findIndex(x => x.end?.number === parsed.e);
  if (ei < 0) return null;
  const si = view.ends[ei].shots.findIndex(x => x.number === parsed.s);
  return si < 0 ? null : { gi, ei, si };
}

/* What House draws as the rock's path, [x, y] in house metres: the camera
 * behind the thrower's where there is one, else the overhead panel's. */
export function trackPoints(shot) {
  const p = shot?.line?.path;
  if (Array.isArray(p) && p.length >= 2) return p.map(([y, x]) => [x, y]);
  const t = shot?.track;
  return Array.isArray(t) && t.length >= 2 ? t.map(q => [q[1], q[2]]) : [];
}

/* The Detail strip in pixels, from house metres. null without a line. */
export function stripGeometry(shot, box = STRIPBOX) {
  const l = shot?.line, b = shot?.target_broom;
  if (!l || !b) return null;
  const { w, h, y0, y1, half } = box;
  const kx = w / (2 * half), ky = h / (y1 - y0);
  const px = x => w / 2 + x * kx, py = y => (y - y0) * ky;
  const pt = (x, y) => ({ x: +px(x).toFixed(1), y: +py(y).toFixed(1) });
  const pts = list => list.map(([x, y]) => `${px(x).toFixed(1)},${py(y).toFixed(1)}`).join(" ");
  const rings = [0, TEE_Y].flatMap(ty => [[R.twelve, "twelve"], [R.eight, "eight"], [R.four, "four"], [R.button, "button"]]
    .map(([r, kind]) => ({ cy: +py(ty).toFixed(1), rx: +(r * kx).toFixed(1), ry: +(r * ky).toFixed(1), kind })));
  const own = shot.delivered_stone_index;
  const stones = (shot.stones || []).filter((_, i) => i !== own)
    .map(s => ({ cx: +px(s.x).toFixed(1), cy: +py(s.y).toFixed(1), color: s.color }));
  const hp = (l.hog_path || []).map(([y, x]) => [x, y]);
  const lastY = hp.length ? hp[hp.length - 1][1] : HOG_Y - 3.6;
  const miss = l.at_broom.miss_m;
  const rest = restOf(shot);
  return {
    w, h, rings,
    hogs: [+py(R.hog).toFixed(1), +py(HOG_Y).toFixed(1)],
    tees: [+py(0).toFixed(1), +py(TEE_Y).toFixed(1)],
    backs: [+py(R.back).toFixed(1), +py(TEE_Y - R.back).toFixed(1)],
    hack: +py(HACK_Y).toFixed(1),
    stones,
    aim: l.start ? pts([[l.start.x, l.start.y], [b.x, b.y]]) : null,
    thrown: pts(hp),
    ext: pts([[lineX(shot, lastY), lastY], [lineX(shot, b.y), b.y]]),
    path: l.path?.length >= 2 ? pts(l.path.map(([y, x]) => [x, y])) : null,
    broom: pt(b.x, b.y),
    rest: rest ? pt(rest.x, rest.y) : null,
    start: l.start ? pt(l.start.x, l.start.y) : null,
    miss: Math.abs(miss) < ON_M ? null
      : { x1: +px(b.x).toFixed(1), x2: +px(l.at_broom.x).toFixed(1), y: +(py(b.y) - 7).toFixed(1), label: `${cm5(miss)} cm` },
  };
}
```

In `frontend/core/charts.mjs`, add:

```js
/* The x-span of end `k` on a chart made by chartGeometry, for shading it. */
export function endSpan(geom, k, BOX) {
  const at = geom?.ticks?.[k];
  if (!at) return null;
  const x0 = k ? geom.ticks[k - 1].x : BOX.padL;
  return { x0, x1: at.x, y1: at.y1, y2: at.y2 };
}
```

- [ ] **Step 4: Run the tests to make sure they pass.**

Run: `.venv/bin/python -m pytest tests/test_viewer_js.py -k "SteppingAndTheHash or Swipe or StripAndTrack or LineFigures" -x`

Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add frontend/core/line.mjs frontend/core/charts.mjs frontend/core/constants.mjs tests/js/singleton.mjs tests/test_viewer_js.py
git commit -m "viewer core: the sheet strip, stepping across ends, the swipe and the hash

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: App state: the tab, the cursor from the hash, `step` and `setTab`

**Files:**
- Modify: `frontend/viewer/App.jsx`, `frontend/runtime/prefs.mjs`, `frontend/eslint.config.js`
- Test: `tests/test_viewer_js.py`

**Interfaces:**
- Consumes: from Task 2, `stepRock`, `parseHash`, `formatHash` and
  `cursorFromHash`.
- Produces:
  - `ui.tab: "house" | "detail" | "timing"`;
  - `actions.step(d)`;
  - `actions.setTab(tab)`;
  - `bodyFlags.watch = config.readOnly ? ui.tab : ""`, which gives
    `body[data-watch="house|detail|timing"]`;
  - `useSwipe`, imported from `./Pager.jsx` (Task 4) for `#houseCard`.

- [ ] **Step 1: Write the failing test.** Append this to `tests/test_viewer_js.py`:

```python
class TestTheAppKnowsItsTab:
    APP = Path(__file__).resolve().parents[1] / "frontend/viewer/App.jsx"

    def test_the_tab_and_rock_are_written_to_the_hash(self):
        src = self.APP.read_text()
        assert "history.replaceState(" in src and "formatHash(" in src

    def test_a_link_s_rock_is_restored_before_the_session_s(self):
        src = self.APP.read_text()
        assert "cursorFromHash(" in src and "export function App({ doc, config, cursor })" in src

    def test_the_house_re_measures_its_crop_when_the_tab_changes(self):
        assert "cropDeps={[ui.sheet, ui.houseMode, ui.ei, ui.si, ui.gi, ui.tab]}" in self.APP.read_text()

    def test_the_house_card_swipes_on_the_read_only_surfaces(self):
        assert "{...(config.readOnly ? houseSwipe : {})}" in self.APP.read_text()

    def test_the_last_tab_is_remembered(self):
        prefs = (Path(__file__).resolve().parents[1] / "frontend/runtime/prefs.mjs").read_text()
        assert 'tab: "detail"' in prefs
```

- [ ] **Step 2: Run it to make sure it fails.**

Run: `.venv/bin/python -m pytest tests/test_viewer_js.py -k TheAppKnowsItsTab -x`

Expected: FAIL.

- [ ] **Step 3: Implement it.**

In `frontend/runtime/prefs.mjs`, add `tab: "detail"` to `DEFAULTS`, with this
comment: `// the phone viewer's bottom tab: "house" | "detail" | "timing"`.

In `frontend/eslint.config.js`:
- add `"history"` to the browser-globals list, after `"location"`;
- add it to the `no-restricted-globals` list for `core/**` (the second list),
  so that core stays DOM-free.

In `frontend/viewer/App.jsx`:
1. Add `stepRock, parseHash, formatHash, cursorFromHash` to the core import,
   and `import { useSwipe } from "./Pager.jsx";`.
2. Change the signature to `export function App({ doc, config, cursor })`.
3. Replace the reducer initialiser with:

```js
  const [ui, dispatch] = useReducer(reducer, null, () => {
    const prefs = loadPrefs();
    const hash = config.readOnly ? parseHash(location.hash) : {};
    // A link names its rock; failing that, where this tab last was. A hash or
    // cursor naming a rock the document no longer has is ignored.
    const linked = cursorFromHash(hash, gi => buildGameView(doc, gi, {}), doc.games.length);
    const saved = cursor && doc.games[cursor.gi] ? cursor : null;
    return {
      gi: 0, ei: 0, si: 0, selStone: null, placeColor: "red", openGroup: null,
      sheet: "peek", houseMode: "", menu: undefined, reporting: false, notice: null,
      following: true,
      ...prefs,
      ...(linked ?? saved ?? {}),
      tab: hash.tab ?? prefs.tab,
    };
  });
```

4. In `bodyFlags`, change the `watch` line to `watch: config.readOnly ? ui.tab : "",`.
5. In `actions`:
   - delete `openWatch`;
   - add
     `step: d => { const n = stepRock(view, ui.ei, ui.si, d); if (n) goTo(n.ei, n.si); },`;
   - add `setTab: tab => setPref({ tab }),`;
   - add `view` and `setPref` to the `useMemo` dependency array.
6. After the `saveCursor` effect, add:

```js
  // The read-only surfaces keep their tab and rock in the URL, so a shared link
  // opens where it was sent from. replaceState: stepping rocks is not history.
  useEffect(() => {
    if (!config.readOnly) return;
    const e = view.ends[ui.ei]?.end?.number, s = view.ends[ui.ei]?.shots[ui.si]?.number;
    if (e == null || s == null) return;
    history.replaceState(null, "", formatHash({ tab: ui.tab, g: ui.gi + 1, e, s }));
  }, [config.readOnly, view, ui.tab, ui.gi, ui.ei, ui.si]);
```

7. Define `const houseSwipe = useSwipe(d => actions.step(d));` after
   `actions`.
8. On the `#houseCard` `<section>`, add `{...(config.readOnly ? houseSwipe : {})}`.
9. On `<House ...>`, set
   `cropDeps={[ui.sheet, ui.houseMode, ui.ei, ui.si, ui.gi, ui.tab]}`.

`useSwipe` comes from Task 4. So that this task builds on its own, create
`frontend/viewer/Pager.jsx` now with only the hook:

```jsx
/* The phone viewer's rock pager and the swipe that drives it. */
import { useRef } from "react";
import { swipeStep } from "../core/index.mjs";

/* Pointer handlers that turn a horizontal drag into a rock step. The pane they
 * go on needs `touch-action: pan-y` (or none, as #house has) so the browser
 * hands the horizontal part of the gesture to us. */
export function useSwipe(onStep) {
  const start = useRef(null);
  return {
    onPointerDown: e => { start.current = { x: e.clientX, y: e.clientY }; },
    onPointerUp: e => {
      const s = start.current; start.current = null;
      if (!s) return;
      const d = swipeStep(e.clientX - s.x, e.clientY - s.y, s.x);
      if (d) onStep(d);
    },
    onPointerCancel: () => { start.current = null; },
  };
}
```

- [ ] **Step 4: Run the tests and the lint.**

Run: `.venv/bin/python -m pytest tests/test_viewer_js.py -k "TheAppKnowsItsTab" -x && (cd frontend && npx eslint .)`

Expected: PASS, and ESLint prints no errors. `Watch.jsx` still calls the
now-removed `actions.openWatch` from two buttons. That's harmless until Task 4
replaces those buttons: ESLint cannot see it, and no test clicks them.

- [ ] **Step 5: Commit.**

```bash
git add frontend/viewer/App.jsx frontend/viewer/Pager.jsx frontend/runtime/prefs.mjs frontend/eslint.config.js tests/test_viewer_js.py
git commit -m "viewer: the phone viewer keeps a tab, and its tab and rock in the URL

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: The tab shell, the pager, and House's caption and path

**Files:**
- Modify: `frontend/viewer/Watch.jsx` (rewritten as the shell), `frontend/viewer/Pager.jsx`, `frontend/viewer/House.jsx` (`Track`)
- Test: `tests/test_viewer_js.py`

**Interfaces:**
- Consumes:
  - `actions.step` and `actions.setTab` (Task 3);
  - `houseCaption` and `trackPoints` (Tasks 1 and 2);
  - `Detail` (Task 5) and `Timing` (Task 6).

  Until those two exist, Watch imports stubs. Step 3 creates them.
- Produces:
  - `Pager({row, count, index, end, canPrev, canNext, onStep})`;
  - the shell markup: `#watch > .wpager, .wpane | .whouse | Timing, nav.wtabs`.

- [ ] **Step 1: Write the failing tests.** Append this to `tests/test_viewer_js.py`:

```python
class TestTheTabShell:
    JSX = Path(__file__).resolve().parents[1] / "frontend/viewer"

    def test_three_tabs_and_the_panes_they_show(self):
        src = (self.JSX / "Watch.jsx").read_text()
        for needle in ('className="wtabs"', "actions.setTab(", "<Detail ", "<Timing ", "<Pager "):
            assert needle in src, needle
        assert "openWatch" not in src and "ui.watch" not in src

    def test_the_house_draws_its_path_through_track_points(self):
        src = (self.JSX / "House.jsx").read_text()
        body = src[src.index("function Track("):src.index("function Broom(")]
        assert "trackPoints(shot)" in body and "shot?.track" not in body
```

- [ ] **Step 2: Run them to make sure they fail.**

Run: `.venv/bin/python -m pytest tests/test_viewer_js.py -k TheTabShell -x`

Expected: FAIL.

- [ ] **Step 3: Implement it.**

Append the pager to `frontend/viewer/Pager.jsx`:

```jsx
export function Pager({ row, count, index, end, canPrev, canNext, onStep }) {
  const sub = [row.color, row.position, row.name].filter(Boolean).join(" · ");
  return (
    <div className="wpager">
      <button type="button" aria-label="Previous rock" disabled={!canPrev} onClick={() => onStep(-1)}>‹</button>
      <div className="wpmid">
        <div className="wptop">
          <span className={`wdisc ${row.color}`}>{row.number}</span>
          <span>End {end} · {sub}</span>
        </div>
        <div className="wpdots" aria-hidden="true">
          {Array.from({ length: count }, (_, i) => <i key={i} className={i === index ? "on" : undefined} />)}
        </div>
      </div>
      <button type="button" aria-label="Next rock" disabled={!canNext} onClick={() => onStep(1)}>›</button>
    </div>
  );
}
```

Create temporary stubs so Watch imports cleanly. Tasks 5 and 6 replace them:
- `frontend/viewer/Detail.jsx` with `export function Detail() { return null; }`;
- `frontend/viewer/Timing.jsx` with `export function Timing() { return null; }`.

Rewrite `frontend/viewer/Watch.jsx`. Keep its header comment, but change
"the end as a list of its sixteen rocks" to "three tabs: House, Detail and
Timing". Keep the `useFollow` function exactly as it is. Replace everything
after `useFollow` with:

```jsx
const TABS = [["house", "House"], ["detail", "Detail"], ["timing", "Timing"]];

export function Watch({ view, ui, config, series, think, here, actions }) {
  const rows = useMemo(() => rockRows(view, ui.ei, ui.leadIn), [view, ui.ei, ui.leadIn]);
  useFollow(rows, ui.following !== false, ui.si, actions);
  const swipe = useSwipe(d => actions.step(d));
  const row = rows[ui.si] || null;
  const shot = view.ends[ui.ei]?.shots[ui.si] ?? null;
  const caption = houseCaption(shot);
  return (
    <section id="watch" aria-label="This game's rocks">
      {ui.tab !== "timing" && row ? (
        <Pager row={row} count={rows.length} index={ui.si} end={view.ends[ui.ei]?.end?.number}
               canPrev={!!stepRock(view, ui.ei, ui.si, -1)} canNext={!!stepRock(view, ui.ei, ui.si, 1)}
               onStep={actions.step} />
      ) : null}
      {ui.tab === "detail" ? (
        <div className="wpane" {...swipe}><Detail shot={shot} doc={view.doc} /></div>
      ) : null}
      {/* #houseCard itself is promoted by CSS between the pager and this
          caption: one <House> in the page, so one #house. */}
      {ui.tab === "house" ? (
        <div className="whouse">
          {caption ?? "Dark pad: the skip's broom · ringed: this rock"}
        </div>
      ) : null}
      {ui.tab === "timing" ? (
        <Timing view={view} ui={ui} series={series} think={think} here={here} actions={actions} />
      ) : null}
      <nav className="wtabs" aria-label="Views">
        {TABS.map(([k, label]) => (
          <button key={k} type="button" aria-current={ui.tab === k ? "page" : undefined}
                  onClick={() => actions.setTab(k)}>{label}</button>
        ))}
      </nav>
    </section>
  );
}
```

Update Watch's imports:
- `import { useEffect, useMemo } from "react";`
- `import { houseCaption, rockAt, rockRows, stepRock } from "../core/index.mjs";`
- `import * as player from "../runtime/player.mjs";`
- `import { Detail } from "./Detail.jsx";`
- `import { Pager, useSwipe } from "./Pager.jsx";`
- `import { Timing } from "./Timing.jsx";`

Delete `RockRow`, `EndBar`, `GameSheet`, `ROW_H` and the list, back-chip and
sheet markup. Task 6 re-creates `RockRow`, the end header and the back chip in
`Timing.jsx`, and its code is given in full there.

In `House.jsx`, replace the body of `Track` with:

```jsx
function Track({ shot }) {
  const pts = trackPoints(shot);
  if (pts.length < 2) return null;
  const color = shot.color === "red" ? PAINT.red : PAINT.yellow;
  return (
    <g clipPath="url(#sheetClip)" pointerEvents="none">
      {pts.slice(0, -1).map((p, i) => (
        <line key={i} x1={p[0]} y1={p[1]} x2={pts[i + 1][0]} y2={pts[i + 1][1]}
              stroke={color} strokeWidth={0.045} strokeLinecap="round"
              opacity={(0.12 + 0.78 * (i / Math.max(1, pts.length - 2))).toFixed(3)} />
      ))}
      <circle cx={pts[0][0]} cy={pts[0][1]} r={0.06} fill="none"
              stroke={color} strokeWidth={0.025} opacity={0.5} />
    </g>
  );
}
```

Add `trackPoints` to House.jsx's core import. Keep the comment above `Track`,
and add this line to it: "Where the camera behind the thrower followed the
rock, its path; otherwise the overhead panel's track, as before."

- [ ] **Step 4: Run the tests and the lint.**

Run: `.venv/bin/python -m pytest tests/test_viewer_js.py -k "TheTabShell or TheAppKnowsItsTab or SkipsBroom" -x && (cd frontend && npx eslint .)`

Expected: PASS, with no ESLint errors. The four source tests that read
`Watch.jsx` for the end bar and `ROW_H`
(`test_the_end_bar_names_the_fix_too`,
`test_the_visible_label_itself_names_the_remedy`,
`test_the_list_row_height_matches_what_the_component_scrolls_by`) now fail.
Task 6 moves them to `Timing.jsx`. Deselect them for this run.

- [ ] **Step 5: Commit.**

```bash
git add frontend/viewer/Watch.jsx frontend/viewer/Pager.jsx frontend/viewer/House.jsx frontend/viewer/Detail.jsx frontend/viewer/Timing.jsx tests/test_viewer_js.py
git commit -m "viewer: the phone viewer is three tabs, with a pager that swipes across ends

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: The Detail pane

**Files:**
- Modify: `frontend/viewer/Detail.jsx` (replacing the stub)
- Test: `tests/test_viewer_js.py`

**Interfaces:**
- Consumes: `lineFigures` and `stripGeometry` (Tasks 1 and 2), and `PAINT`.
- Produces: `Detail({ shot, doc })`.

- [ ] **Step 1: Write the failing test.** Append this to `tests/test_viewer_js.py`:

```python
class TestTheDetailPane:
    SRC = Path(__file__).resolve().parents[1] / "frontend/viewer/Detail.jsx"

    def test_it_draws_the_strip_and_the_six_figures_from_the_core(self):
        src = self.SRC.read_text()
        for needle in ("lineFigures(shot, doc)", "stripGeometry(shot)", 'className="dstrip"',
                       'className="dcap"', "f.predates"):
            assert needle in src, needle

    def test_the_caption_is_the_spec_s(self):
        assert ("Sheet from above, thrower at the bottom · across ×3 · figures ±10 cm · "
                "wide = the side away from the curl") in self.SRC.read_text()
```

- [ ] **Step 2: Run it to make sure it fails.**

Run: `.venv/bin/python -m pytest tests/test_viewer_js.py -k TheDetailPane -x`

Expected: FAIL (it is still the stub).

- [ ] **Step 3: Implement it.** Replace `frontend/viewer/Detail.jsx` with:

```jsx
/* Detail: was this rock thrown at the skip's broom? The whole sheet as a strip,
 * thrower at the bottom, beside six figures. Everything it draws comes from
 * core/line.mjs; this file only turns it into markup. */
import { lineFigures, stripGeometry } from "../core/index.mjs";
import { PAINT } from "../core/constants.mjs";

const RING = { twelve: PAINT.twelve, eight: PAINT.ice, four: PAINT.four, button: PAINT.ice };
const GOLD = "#a07a00";      // the rock's path: #e8b400 is unreadable on the ice
const MUTED = "#6d6455";

function Strip({ shot }) {
  const g = stripGeometry(shot);
  if (!g) return <div className="dstrip dstrip-none" aria-hidden="true" />;
  const own = shot.color === "red" ? PAINT.red : PAINT.yellow;
  return (
    <svg className="dstrip" width={g.w} height={g.h} viewBox={`0 0 ${g.w} ${g.h}`} role="img"
         aria-label="The sheet from above, thrower at the bottom: the intended line, the thrown line and where the rock went">
      {g.rings.map((r, i) => (
        <ellipse key={i} cx={g.w / 2} cy={r.cy} rx={r.rx} ry={r.ry} fill={RING[r.kind]} fillOpacity={0.55} />
      ))}
      {g.hogs.map((y, i) => <line key={`h${i}`} x1={0} y1={y} x2={g.w} y2={y} stroke={PAINT.red} strokeWidth={1.2} />)}
      {[...g.tees, ...g.backs, g.hack].map((y, i) => (
        <line key={`l${i}`} x1={0} y1={y} x2={g.w} y2={y} stroke={PAINT.iceLine} strokeWidth={0.8} />
      ))}
      <line x1={g.w / 2} y1={0} x2={g.w / 2} y2={g.h} stroke={PAINT.iceLine} strokeWidth={0.8} />
      {g.stones.map((s, i) => (
        <circle key={`s${i}`} cx={s.cx} cy={s.cy} r={3.2} fill={s.color === "red" ? PAINT.red : PAINT.yellow}
                fillOpacity={0.6} stroke={PAINT.graniteEdge} strokeWidth={0.6} />
      ))}
      {g.aim ? <polyline points={g.aim} fill="none" stroke={MUTED} strokeWidth={1.4} strokeDasharray="4 3" /> : null}
      <polyline points={g.ext} fill="none" stroke={PAINT.accent} strokeWidth={1.2} strokeDasharray="2 2.5" />
      <polyline points={g.thrown} fill="none" stroke={PAINT.accent} strokeWidth={2.4} />
      {g.path ? <polyline points={g.path} fill="none" stroke={GOLD} strokeWidth={2.2} strokeLinejoin="round" /> : null}
      {g.miss ? (
        <g>
          <line x1={g.miss.x1} y1={g.miss.y} x2={g.miss.x2} y2={g.miss.y} stroke={PAINT.accent} strokeWidth={1} />
          <text x={(g.miss.x1 + g.miss.x2) / 2} y={g.miss.y - 4} fontSize={9} fontWeight={700}
                textAnchor="middle" fill={PAINT.accent}>{g.miss.label}</text>
        </g>
      ) : null}
      <rect x={g.broom.x - 5} y={g.broom.y - 2} width={10} height={4} rx={1}
            fill={PAINT.accent} stroke={own} strokeWidth={1}><title>skip&apos;s broom</title></rect>
      {g.rest ? <circle cx={g.rest.x} cy={g.rest.y} r={4.2} fill={own} stroke={PAINT.accent} strokeWidth={1.2} /> : null}
      {g.start ? <circle cx={g.start.x} cy={g.start.y} r={3.2} fill={PAINT.accent} /> : null}
    </svg>
  );
}

const Check = () => (
  <svg width="13" height="13" viewBox="0 0 24 24" aria-hidden="true"
       style={{ fill: "none", strokeWidth: 2.6 }}><path d="M4 12 L10 18 L20 6" /></svg>
);

export function Detail({ shot, doc }) {
  const f = lineFigures(shot, doc);
  if (f.predates) return <p className="dnone">{f.reason}</p>;
  return (
    <>
      <div className="dbody">
        <Strip shot={shot} />
        <dl className="dfigs">
          {f.figures.map(x => (
            <div key={x.key} className={x.dim ? "dfig dim" : "dfig"}>
              <dt>{x.label}</dt>
              <dd className="dval">{x.value}</dd>
              <dd className={x.tick ? `dnote ${x.tick}` : "dnote"}>
                {x.tick === "confirmed" ? <Check /> : null}{x.note}
              </dd>
            </div>
          ))}
        </dl>
      </div>
      <p className="dcap">Sheet from above, thrower at the bottom · across ×3 · figures ±10 cm · wide = the side away from the curl</p>
    </>
  );
}
```

- [ ] **Step 4: Run the tests and the lint.**

Run: `.venv/bin/python -m pytest tests/test_viewer_js.py -k "TheDetailPane or LineFigures or StripAndTrack" -x && (cd frontend && npx eslint .)`

Expected: PASS, and ESLint is clean.

- [ ] **Step 5: Commit.**

```bash
git add frontend/viewer/Detail.jsx tests/test_viewer_js.py
git commit -m "viewer: the Detail pane, the rock's line on the sheet beside its figures

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: The Timing tab, the whole game

**Files:**
- Modify: `frontend/viewer/Timing.jsx` (replacing the stub), `frontend/viewer/Charts.jsx` (`ThinkingChart` gains `shadeEnd`)
- Modify: `tests/test_viewer_js.py`, moving the three `Watch.jsx` source tests to `Timing.jsx`

**Interfaces:**
- Consumes: `rockRows`, `endSummary`, `clockText`, `endSpan` (Task 2), and
  `TIMINGBOX` (Task 2).
- Produces:
  - `Timing({ view, ui, series, think, here, actions })`;
  - `ThinkingChart({ series, at, box, shadeEnd = null })`.

- [ ] **Step 1: Move and write the tests.** In `tests/test_viewer_js.py`:
- In `test_the_end_bar_names_the_fix_too` and
  `test_the_visible_label_itself_names_the_remedy`, change
  `frontend/viewer/Watch.jsx` to `frontend/viewer/Timing.jsx`. Rename the first
  to `test_the_end_header_names_the_fix_too`.
- In `test_the_list_row_height_matches_what_the_component_scrolls_by`, read
  `Timing.jsx` instead of `Watch.jsx`, and update the docstring's first line to
  "Timing.jsx keeps the current row in view with ROW_H".

Then append:

```python
class TestTheTimingTab:
    SRC = Path(__file__).resolve().parents[1] / "frontend/viewer/Timing.jsx"

    def test_every_end_of_the_game_with_its_header(self):
        src = self.SRC.read_text()
        for needle in ("view.ends.map(", "endSummary(view, k)", "rockRows(view, k, ui.leadIn)",
                       'className="tend"', "shadeEnd={ui.ei}", "TIMINGBOX"):
            assert needle in src, needle

    def test_a_tap_stays_on_timing_and_follows_the_video_again(self):
        src = self.SRC.read_text()
        assert "actions.setFollowing(true); actions.goTo(k, i);" in src
        assert "setTab" not in src
```

- [ ] **Step 2: Run them to make sure they fail.**

Run: `.venv/bin/python -m pytest tests/test_viewer_js.py -k "TheTimingTab or end_header_names or remedy or row_height" -x`

Expected: FAIL (the stub).

- [ ] **Step 3: Implement it.**

In `Charts.jsx`:
- change `ThinkingChart` to take `shadeEnd = null`;
- import `endSpan` from core;
- render this right after `<Grid geom={geom} />`:

```jsx
      {shade ? <rect className="endshade" x={n(shade.x0)} y={n(shade.y1)}
                     width={n(shade.x1 - shade.x0)} height={n(shade.y2 - shade.y1)} /> : null}
```

with this line computed just after `if (!geom) return null;`:

```jsx
  const shade = shadeEnd == null ? null : endSpan(geom, shadeEnd, box ?? CHARTBOX);
```

Import `CHARTBOX` from constants if it isn't already. In `style.css`'s global
section, beside `.clockchart .ln`, add
`.clockchart .endshade { fill: var(--accent); fill-opacity: .06; }`.

Replace `frontend/viewer/Timing.jsx` with the following. `RockRow` and the
header's label ternary are the ones deleted from `Watch.jsx` in Task 4,
verbatim, so the moved tests' strings and regex still match.

```jsx
/* Timing: the whole game's clock above every rock, grouped by end. Each row
 * carries its thinking-time bar, so the list *is* the per-rock chart turned
 * on its side -- the one statistic that works on a game nobody has charted. */
import { useEffect, useRef } from "react";
import { clockText, endSummary, rockRows } from "../core/index.mjs";
import { TIMINGBOX } from "../core/constants.mjs";
import { ThinkingChart } from "./Charts.jsx";

// Rows are this tall (.wrow in style.css). The list keeps the current one in
// view by its measured offset, centring it by half a row.
const ROW_H = 56;

function RockRow({ row, current, onPick }) {
  return (
    <button type="button" className="wrow" aria-current={current || undefined}
            onClick={() => onPick(row.i)}>
      <span className={`wdisc ${row.color}`}>{row.number}</span>
      <span className="wmid">
        <span className="whead">
          <span className="wname">{row.name}</span>
          {row.splitText ? (
            <span className="wsplit" title="Long split: hog line to hog line">
              <span aria-hidden="true">·</span>{" "}
              <span className="sr">long split </span>{row.splitText}
            </span>
          ) : null}
        </span>
        <span className="wtrack">
          {row.unmeasured ? null
            : <i className={row.color} style={{ width: `${Math.max(4, row.frac * 100)}%` }} />}
        </span>
      </span>
      <span className="wright">
        {current ? <span className="wnow">▶ playing</span> : null}
        <span className="wsecs">{row.text}{row.estimated && !row.unmeasured ? " est." : ""}</span>
      </span>
    </button>
  );
}

function EndHead({ summary }) {
  const { number, running, hammer, boardReadable, scoresWithheld } = summary;
  return (
    <div className="tend">
      <span className="wen">End {number}</span>
      {hammer ? <span className="wham">{hammer} has hammer</span> : null}
      {running ? (
        <span className="wsc">
          <i className="wdot red" />{running.red} – {running.yellow}<i className="wdot yellow" />
        </span>
      ) : (
        <span className="wsc wsc-none"
              title={scoresWithheld
                ? "The wall board was read, but its scores could not be matched "
                  + "to these ends. Setting the game's start time places them, "
                  + "with no need to read the board again."
                : undefined}>
          {boardReadable === false ? "chart predates board reading"
            : scoresWithheld ? "needs a start time"
            : "not posted"}
        </span>
      )}
    </div>
  );
}

export function Timing({ view, ui, series, think, here, actions }) {
  const listRef = useRef(null);

  /* Keep the current row in view while following. Deliberately not
   * scrollIntoView: that scrolls every scrollable ancestor, and the phone
   * shell is a stack of fixed boxes that must not move. */
  useEffect(() => {
    const el = listRef.current;
    const cur = el?.querySelector(".wrow[aria-current]");
    if (!el || !cur || ui.following === false) return;
    const top = cur.offsetTop;
    if (top < el.scrollTop || top + ROW_H > el.scrollTop + el.clientHeight)
      el.scrollTop = Math.max(0, top - el.clientHeight / 2 + ROW_H / 2);
  }, [ui.ei, ui.si, ui.following]);

  /* A gesture, not a scroll event: the effect above scrolls this same
   * element, and a scroll listener could not tell the two apart. */
  const yield_ = () => { if (ui.following !== false) actions.setFollowing(false); };
  const teams = view.game.teams || {};
  const current = view.ends[ui.ei]?.shots[ui.si];
  return (
    <div className="tpane">
      <div className="tchart">
        <ThinkingChart series={series} at={here} box={TIMINGBOX} shadeEnd={ui.ei} />
        <div className="wtotals">
          <span><i className="wdot red" />{teams.red?.name || "red"} {clockText(series.red)}</span>
          <span><i className="wdot yellow" />{teams.yellow?.name || "yellow"} {clockText(series.yellow)}</span>
        </div>
        <p className="wcaveat">
          Read from {think.measured} of {think.measured + think.unmeasured} rocks
          {think.estimated ? `, ${think.estimated} estimated` : ""}.
        </p>
      </div>
      <div className="tlist" ref={listRef} onWheel={yield_} onTouchMove={yield_}>
        {view.ends.map((e, k) => {
          const summary = endSummary(view, k);
          return (
            <section key={k} aria-label={`End ${e.end?.number ?? k + 1}`}>
              {summary ? <EndHead summary={summary} /> : null}
              {rockRows(view, k, ui.leadIn).map(r => (
                <RockRow key={r.i} row={r} current={k === ui.ei && r.i === ui.si}
                         onPick={i => { actions.setFollowing(true); actions.goTo(k, i); }} />
              ))}
            </section>
          );
        })}
      </div>
      {ui.following === false && current ? (
        <button type="button" className="wback"
                onClick={() => { actions.setFollowing(true); actions.goTo(ui.ei, ui.si); }}>
          ↓ Back to rock {current.number}
        </button>
      ) : null}
    </div>
  );
}
```


- [ ] **Step 4: Run the tests and the lint.**

Run: `.venv/bin/python -m pytest tests/test_viewer_js.py -x && (cd frontend && npx eslint .)`

Expected: PASS for the whole `test_viewer_js.py`. It is a single-file run
under node, so it fits in memory. ESLint is clean.

- [ ] **Step 5: Commit.**

```bash
git add frontend/viewer/Timing.jsx frontend/viewer/Charts.jsx src/curling_score/viewer/style.css tests/test_viewer_js.py
git commit -m "viewer: Timing reads the whole game, every end under its own pinned header

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: The phone CSS for the three tabs

**Files:**
- Modify: `src/curling_score/viewer/style.css` (the watch-mode region inside the `@media (max-width: 640px) and (min-height: 521px)` block)
- Test: `tests/test_viewer_js.py`

- [ ] **Step 1: Write the failing test.** Append this to `tests/test_viewer_js.py`:

```python
class TestThePhoneTabsCss:
    def test_the_tabs_the_pager_and_the_panes_have_rules(self):
        css = (VIEWER / "style.css").read_text()
        for sel in (".wtabs {", ".wpager {", ".wpane {", ".dstrip {", ".tend {", ".tlist {",
                    'body[data-watch="house"] main > #houseCard'):
            assert sel in css, sel

    def test_the_detail_pane_hands_horizontal_drags_to_the_swipe(self):
        css = (VIEWER / "style.css").read_text()
        rule = css[css.index(".wpane {"):css.index("}", css.index(".wpane {"))]
        assert "touch-action: pan-y" in rule

    def test_the_old_sheets_are_gone(self):
        css = (VIEWER / "style.css").read_text()
        for gone in (".wsheethead {", ".wbar {", ".wendbar {"):
            assert gone not in css, gone
```


- [ ] **Step 2: Run it to make sure it fails.**

Run: `.venv/bin/python -m pytest tests/test_viewer_js.py -k ThePhoneTabsCss -x`

Expected: FAIL.

- [ ] **Step 3: Implement it.** In `style.css`, inside the phone block, replace
everything from `.wendbar { height: 44px;` through the `.wcaveat { … }` rule
(just before the block's closing `}`) with the following. Keep the `#watch`
rule above it, and keep the watch-mode gate rules. The `.wrow` family, `.wdot`,
`.wen`, `.wsc`, `.wham`, `.wtotals` and `.wcaveat` are kept as they are, with
the moved-test constraints: `.wrow` height 56px, `.wsplit` `flex: none`, and
`.wname` `min-width: 0`.

```css
  /* Three tabs at the thumb. */
  .wtabs { position: absolute; left: 0; right: 0; bottom: 0; z-index: 9;
           height: calc(64px + env(safe-area-inset-bottom));
           padding-bottom: env(safe-area-inset-bottom);
           display: flex; background: var(--panel);
           border-top: 1px solid var(--line); }
  .wtabs button { flex: 1; border: 0; border-top: 3px solid transparent;
                  background: none; font: inherit; font-size: 13px;
                  color: var(--muted); }
  .wtabs button[aria-current] { color: var(--accent); font-weight: 700;
                                border-top-color: var(--accent); }

  /* The rock you are on, and the way to the next. */
  .wpager { height: 56px; flex: none; display: flex; align-items: center;
            padding: 0 4px; background: var(--panel);
            border-bottom: 1px solid var(--line); }
  .wpager > button { width: 44px; height: 44px; border: 0; background: none;
                     font-size: 22px; color: var(--accent); flex: none; }
  .wpager > button:disabled { color: var(--muted); opacity: .35; }
  .wpmid { flex: 1; min-width: 0; display: flex; flex-direction: column;
           align-items: center; gap: 5px; }
  .wptop { display: flex; align-items: center; gap: 8px; font-size: 13px;
           font-weight: 600; min-width: 0; white-space: nowrap; }
  .wptop .wdisc { width: 22px; height: 22px; font-size: 12px; }
  .wpdots { display: flex; gap: 3px; }
  .wpdots i { width: 5px; height: 5px; border-radius: 3px;
              background: color-mix(in srgb, var(--muted) 45%, transparent); }
  .wpdots i.on { width: 14px; background: var(--accent); }

  /* Detail: the sheet strip beside its figures. pan-y keeps the pane's own
     vertical scroll and hands horizontal drags to the swipe. */
  .wpane { flex: 1; min-height: 0; overflow-y: auto; touch-action: pan-y;
           padding: 12px 16px calc(76px + env(safe-area-inset-bottom)); }
  .dbody { display: flex; gap: 16px; align-items: flex-start; }
  .dstrip { flex: none; display: block; background: var(--ice);
            border: 1px solid var(--line); border-radius: 4px; }
  .dstrip-none { width: 150px; height: 420px; }
  .dfigs { flex: 1; min-width: 0; margin: 0; display: flex; flex-direction: column; }
  .dfig { padding: 9px 0; border-bottom: 1px solid var(--line); }
  .dfig dt { font-size: 10.5px; letter-spacing: .08em; text-transform: uppercase;
             color: var(--muted); font-weight: 600; }
  .dfig .dval { margin: 0; font-family: var(--display); font-size: 22px;
                font-weight: 700; line-height: 1.15; font-variant-numeric: tabular-nums; }
  .dfig .dnote { margin: 0; font-size: 11.5px; color: var(--muted);
                 display: flex; align-items: center; gap: 5px; }
  .dfig.dim .dval { color: var(--muted); }
  .dnote.confirmed svg { flex: none; stroke: var(--ok); }
  .dcap { font-size: 11px; color: var(--muted); margin: 8px 0 0; }
  .dnone { font-size: 14px; color: var(--muted); margin: 24px 16px; text-align: center; }

  /* House: #houseCard promoted between the pager and this caption. */
  .whouse { position: absolute; left: 0; right: 0; z-index: 9; height: 44px;
            bottom: calc(64px + env(safe-area-inset-bottom));
            display: flex; align-items: center; padding: 0 16px;
            font-size: 13px; color: var(--muted); background: var(--panel);
            border-top: 1px solid var(--line); }
  body[data-watch="house"] main > #houseCard { display: block; position: fixed;
    left: 0; right: 0; top: calc(48px + 56.25vw + 56px);
    bottom: calc(108px + env(safe-area-inset-bottom));
    z-index: 8; margin: 0; border: 0; border-radius: 0;
    background: var(--panel); padding: 6px 12px; }
  body[data-watch="house"] #houseCard .tools,
  body[data-watch="house"] #houseCard .housebar,
  body[data-watch="house"] #houseCard .muted { display: none; }

  /* Timing: the whole game's clock pinned above every rock, by end. */
  .tpane { flex: 1; min-height: 0; display: flex; flex-direction: column;
           padding-bottom: calc(64px + env(safe-area-inset-bottom)); }
  .tchart { flex: none; padding: 8px 16px; background: var(--panel);
            border-bottom: 1px solid var(--line); }
  .tchart .wtotals { margin-top: 6px; }
  .tchart .wcaveat { margin-top: 4px; }
  .tlist { flex: 1; overflow-y: auto; overscroll-behavior: contain; }
  .tend { position: sticky; top: 0; z-index: 1; height: 30px; display: flex;
          align-items: center; gap: 12px; padding: 0 16px;
          background: var(--bg); border-bottom: 1px solid var(--line);
          font-size: 13px; }
  .wen { font-weight: 650; }
  .wsc { display: flex; align-items: center; gap: 6px;
         font-variant-numeric: tabular-nums; }
  .wsc-none { opacity: 0.55; font-size: 12px; }
  .wham { font-size: 10px; color: var(--muted); letter-spacing: .05em; }
  .wdot { width: 11px; height: 11px; border-radius: 50%; display: inline-block;
          flex: none; }
  .wdot.red { background: var(--red); } .wdot.yellow { background: var(--yellow); }

  /* (the .wrow family, unchanged -- keep it here exactly as it was) */

  .wback { position: absolute; left: 50%; transform: translateX(-50%);
           bottom: calc(76px + env(safe-area-inset-bottom)); z-index: 6;
           height: 36px; padding: 0 16px; border-radius: 999px;
           border: 1px solid var(--line); background: var(--panel);
           color: var(--accent); font: inherit; font-size: 13px;
           box-shadow: 0 2px 10px rgb(0 0 0 / .14); }
  .wtotals { display: flex; gap: 20px; align-items: center;
             font-size: 14px; font-variant-numeric: tabular-nums; }
  .wtotals span { display: flex; align-items: center; gap: 7px; }
  .wcaveat { font-size: 12px; color: var(--muted); line-height: 1.5; margin: 0; }
```

Move the existing `.wrow` … `.wnow` rules to where the placeholder comment
sits, unchanged. Also delete the `.wlist` rule, since `.tlist` replaces it.
The test `test_the_rock_list_is_not_swept_up_by_the_chart_panel_s_blanket_hide`
only asserts that `.wlist` is absent from the blanket rule, so it still passes.

- [ ] **Step 4: Run the tests.**

Run: `.venv/bin/python -m pytest tests/test_viewer_js.py -x`

Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add src/curling_score/viewer/style.css tests/test_viewer_js.py
git commit -m "viewer css: the phone viewer's tab bar, pager, Detail strip and whole-game Timing

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Build, check on a phone viewport, scan

**Files:**
- Modify (generated): `src/curling_score/viewer/app.js`, `src/curling_score/service/static/site.js`, `frontend/.buildstamp.json`

- [ ] **Step 1: Build.**

Run: `cd frontend && npm run build`

Expected: ESLint is clean and esbuild writes the three outputs.

- [ ] **Step 2: Run the frontend tests.**

Run: `.venv/bin/python -m pytest tests/test_frontend_build.py tests/test_viewer_js.py -x`

Expected: PASS. The stamp matches, and the outputs reference only the allowed
hosts.

- [ ] **Step 3: Look at it on a phone viewport.** Serve a schema-6 timeline
from pipeline plan Task 10, `out/line-vxu9/timeline.json`, in view mode:

Run: `.venv/bin/python scripts/devserve.py out/line-vxu9/timeline.json`

In Chrome's device toolbar, check each of these:
- **At 390×844:**
  - the first visit opens on Detail;
  - the strip and all six figures show;
  - swiping left and right steps through rocks and across the end boundary;
  - the video seeks, and the player does not reload;
  - the hash updates;
  - reloading returns to the same rock and tab.
- **House:**
  - it crops to the scoring area the first time it opens;
  - the path comes from behind the thrower where there is one;
  - the caption shows.
- **Timing:**
  - the whole game scrolls, with each end header pinned;
  - tapping a row seeks and stays on Timing;
  - scrolling away mid-play shows "Back to rock N".
- **Other checks:**
  - a schema-5 timeline (`~/curling-work/ds15/games/timelines/` or any hosted
    chart) shows "This chart predates line measurement" on Detail, with House
    and Timing working as before;
  - 430×932 looks the same;
  - landscape falls back to the scrolling stack;
  - the dark colour scheme looks right;
  - the desktop layout at 1280 is unchanged.

- [ ] **Step 4: Run the HawkScan skill** against the local service. The
session hook requires it for UI changes. Fix any findings.

- [ ] **Step 5: Commit the build outputs.**

```bash
git add src/curling_score/viewer/app.js src/curling_score/service/static/site.js frontend/.buildstamp.json
git commit -m "viewer: build the phone viewer's three tabs

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

Deploying the API and worker, and requeueing the hosted videos, comes after
both plans are done, on an explicit go from the user. It follows the
deploying-curling-chart and concurrent-session-commits notes.
