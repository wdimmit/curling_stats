# Shot Detail on the Desktop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put the phone's shot detail (the whole sheet as a strip, and six line
figures) on the desktop viewer, in a new card directly under the video, with
the sheet on its side.

**Architecture:**
- **Pure core.** `frontend/core/line.mjs` gains `stripShapes` (the strip as
  explicit shapes) and `sideways` (those shapes a quarter turn).
  `constants.mjs` gains `DESKBOX`. All of it is tested in node through
  `tests/js/singleton.mjs`.
- **One renderer.** `Detail.jsx`'s `Strip` draws shapes. The phone passes
  upright shapes and gets byte-identical markup. The new `DeskDetail` passes
  turned shapes.
- **Placement.** `App.jsx` mounts `#detailCard` after `#playCard`. CSS grid
  areas put it under the video at every desktop width, and hide it on phones.
- **The list.** The chart panel's detail list drops the long split, renames the
  m/s weight, and counts stones in words.

**Tech Stack:**
- React 18;
- esbuild via `frontend/build.mjs`, with its committed outputs;
- ESLint;
- pytest driving node (`tests/test_viewer_js.py`);
- hand-written `src/curling_score/viewer/style.css`;
- headless Chrome through `scripts/devserve.py` and `scripts/cdp.mjs`.

**Spec:** `docs/superpowers/specs/2026-09-24-desktop-shot-detail-design.md`.
Mockups: https://claude.ai/artifact/LdbRUpSE7PGJ86EHAaUgR7 (artboard A).

## Global Constraints

- **Desktop only.** `#detailCard` is `display: none` at `max-width: 640px`.
  Both phone layouts, and the phone Detail pane's markup, are unchanged.
- **Never reparent the video.** `#video` keeps zero React children.
  `#detailCard` is always mounted, and CSS alone decides whether it shows.
- **Breakpoints stay where they are.**
  - Three columns above 1180 px: `minmax(0,1fr) minmax(290px,360px) 310px`.
  - Two columns at 820–1180 px: `minmax(0,1fr) 320px`.
  - One column at 820 px and below.
- **Grid areas.**
  - Above 1180 px: `"play house chart" "detail house chart"`, rows `auto 1fr`.
  - 820–1180 px: `"play house" "detail house" "chart house"`, rows
    `auto auto 1fr`.
  - 820 px and below: `"play" "detail" "house" "chart"`.
  - 640 px and below: `none`, with each card's `grid-area: auto`.
- **`DESKBOX = { w: 114, h: 660, y0: -2.3, y1: 38.9, half: 2.375 }`.** That is
  ×1.5 across (24 px/m) against 16 px/m along.
- **The turn is `(x, y) → (660 − y, x)`.** The hack is at the left, the
  thrower's left along the top. It is a rotation, not a mirror.
- **The broom marker** is 4 × 10 upright and 10 × 4 on its side.
- **The miss bracket** is 7 px past the broom, on the side away from the
  thrower. On its side, the label is:
  - 11 px bold;
  - end-anchored at the bracket's x − 16;
  - at the bracket's middle + 4, clamped to [11, 111];
  - drawn with a 3 px `PAINT.ice` halo (`paint-order: stroke`).
- **Desktop caption, exactly:** "Sheet from above, thrower at the left · across
  ×1.5 · figures ±4 in · wide = the side away from the curl".
- **An end with no rocks:** the desktop card says exactly "No rocks were
  detected in this end".
- **The list.**
  - `["Entry speed", …]` replaces `["Weight", …]`.
  - No "Long split" row.
  - The house counts read "1 stone in", "2 stones out", "1 stone moved",
    "no change" and "—".
- **Shared checkout.** Another session may work in this tree. Stage explicit
  paths only, and never `git add -A`.
- **Build after every `frontend/` change.** Run `cd frontend && npm run build`
  (eslint, then esbuild). Commit `src/curling_score/viewer/app.js` and
  `frontend/.buildstamp.json`, plus `src/curling_score/service/static/site.js`
  when it changed. `tests/test_frontend_build.py` checks the stamp.
- **Tests.** Run
  `.venv/bin/python -m pytest tests/test_viewer_js.py tests/test_frontend_build.py -q`.
  The full suite is OOM-killed on this box.
- **Commits.** Style `area: sentence`, ending with
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Work on a
  branch, `desktop-shot-detail`, cut from `main`.

## Review Focus

1. **An end with no rocks** (`shot` is `null`). The card must say "No rocks
   were detected in this end". It must not show `lineFigures`' "No broom was
   held still before the release". Pinned in Task 3.
2. **A huge miss, or a broom near the sheet's edge.** The bracket label must
   stay inside the 660 × 114 frame and off the bracket. Pinned in Task 1
   (`test_the_label_stays_inside_the_frame_on_a_huge_miss`).
3. **The phone layouts at 640 px and below.** A `grid-area` naming an area that
   no longer exists pushes a card into the implicit grid, so the cards must get
   `grid-area: auto` there. Pinned in Task 4 (CSS) and Task 5 (390 px check).
4. **The shared renderer changing the phone strip's markup by a character**,
   for example "75.0" against "75", or attribute order. Pinned in Task 3 by a
   before-and-after comparison of the rendered SVG.
5. **Figures overflowing, or a gap under the video, at narrow desktop widths**
   (641–1180 px). Pinned in Task 5: no `.dval` overflows at 1280 or 700 px,
   and the card starts 14 px under the play card at 1000 px.

---

### Task 1: The strip as shapes, and on its side (pure)

**Files:**
- Modify: `frontend/core/constants.mjs` (after `STRIPBOX`, line 42)
- Modify: `frontend/core/line.mjs` (after `stripGeometry`, which ends at line 246)
- Modify: `tests/js/singleton.mjs` (next to `stripGeometry`, line 94)
- Test: `tests/test_viewer_js.py` (new class after `TestStripAndTrack`)

**Interfaces:**
- Consumes: `stripGeometry(shot, box)`, whose output fields are `w, h, rings[{cy, rx, ry, kind}],
  hogs[2], tees[2], backs[2], hack, stones[{cx, cy, color}], broom{x, y}|null,
  rest{x, y}|null, aim|thrown|ext|path: "x,y x,y"|null, start{x, y}|null,
  miss{x1, x2, y, label}|null`.
- Produces:
  - `DESKBOX` (constants).
  - `stripShapes(g) -> Shapes | null`. `Shapes` is `{ w, h, rings[{cx, cy, rx, ry, kind}],
    lines[{x1, y1, x2, y2, kind: "hog"|"tee"|"back"|"hack"|"centre"}],
    stones[{x, y, color}], aim, thrown, ext, path (point strings or null),
    broom{x, y, w, h}|null, rest{x, y}|null, start{x, y}|null,
    miss{x1, y1, x2, y2, label, tx, ty, anchor, size, halo?}|null }`.
  - `sideways(Shapes | null) -> Shapes | null`.

- [ ] **Step 1: Branch**

```bash
cd /home/tcuser/src/curling_score && git status --short | grep -v '^??'   # expect nothing
git checkout -b desktop-shot-detail
```

- [ ] **Step 2: Write the failing tests**

Add to `tests/test_viewer_js.py`, directly after `class TestStripAndTrack`:

```python
class TestTheStripOnItsSide:
    """The desktop draws the phone's strip turned a quarter: hack at the left."""

    SHOT = TestLineFigures().measured()

    def turned(self, s=None):
        return run_js(f"out(sideways(stripShapes(stripGeometry({json.dumps(s or self.SHOT)}, DESKBOX))));")

    def test_upright_shapes_are_the_phone_strip_s_own_numbers(self):
        got = run_js(f"const g = stripGeometry({json.dumps(self.SHOT)}); const s = stripShapes(g);"
                     "out([s.lines[0], s.rings[0], s.broom, s.aim === g.aim, s.lines.length, s.miss.anchor, s.miss.size]);")
        assert got == [{"x1": 0, "y1": 88.7, "x2": 150, "y2": 88.7, "kind": "hog"},
                       {"cx": 75, "cy": 23.4, "rx": 57.8, "ry": 18.6, "kind": "twelve"},
                       {"x": 21, "y": 20.2, "w": 4, "h": 10}, True, 8, "middle", 9]

    def test_the_frame_turns_and_the_hack_is_at_the_left(self):
        s = self.turned()
        assert (s["w"], s["h"]) == (660, 114)
        assert s["start"] == {"x": 13.3, "y": 51.5}
        assert s["aim"].split()[0] == "13.3,51.5"

    def test_the_thrower_s_left_is_the_top_edge(self):
        # The broom is at x = -1.647, the thrower's left: it lands above the centre line.
        s = self.turned()
        centre = s["lines"][-1]
        assert centre == {"x1": 660, "y1": 57, "x2": 0, "y2": 57, "kind": "centre"}
        assert s["broom"]["y"] + s["broom"]["h"] / 2 < 57

    def test_a_ring_s_radii_swap_and_the_broom_lies_along_the_sheet(self):
        s = self.turned()
        assert s["rings"][0] == {"cx": 623.2, "cy": 57, "rx": 29.3, "ry": 43.9, "kind": "twelve"}
        assert s["broom"] == {"x": 615.4, "y": 15.5, "w": 10, "h": 4}

    def test_the_hog_lines_run_across_the_sheet(self):
        assert self.turned()["lines"][0] == {"x1": 520.6, "y1": 0, "x2": 520.6, "y2": 114, "kind": "hog"}

    def test_the_miss_bracket_stands_7_px_past_the_broom_labelled_before_it(self):
        s = self.turned()
        m, b = s["miss"], s["broom"]
        assert m["x1"] == m["x2"] == pytest.approx(b["x"] + b["w"] / 2 + 7, abs=0.15)
        assert (m["label"], m["anchor"], m["size"], m["halo"]) == ("2 ft 4 in", "end", 11, True)
        assert m["tx"] == pytest.approx(m["x1"] - 16, abs=0.05)

    def test_the_label_stays_inside_the_frame_on_a_huge_miss(self):
        big = dict(self.SHOT, target_broom={"x": 1.9, "y": 0.0},
                   line=dict(self.SHOT["line"], at_broom={"x": -1.5, "miss_m": 3.4}))
        m = self.turned(big)["miss"]
        assert m["label"] == "11 ft 2 in"
        assert 11 <= m["ty"] <= 111 and m["tx"] < m["x1"]

    def test_no_line_still_turns_the_sheet(self):
        s = shot(1, "red", "lead", target_broom={"x": 0.5, "y": 0.2}, delivered_stone_index=0,
                 stones=[{"color": "red", "x": 0.3, "y": 1.0}], line=None)
        t = self.turned(s)
        assert t["miss"] is None and t["aim"] is None
        assert t["broom"] is not None and t["rest"] is not None and len(t["rings"]) == 8

    def test_no_shot_draws_nothing(self):
        assert run_js("out(sideways(stripShapes(stripGeometry(null, DESKBOX))));") is None
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_viewer_js.py -k TheStripOnItsSide -q`
Expected: FAIL, with node errors like `stripShapes is not defined`.

- [ ] **Step 4: Add `DESKBOX`**

In `frontend/core/constants.mjs`, directly after the `STRIPBOX` line:

```js
/* The desktop strip before it is turned on its side: ×1.5 across (24 px/m
 * against 16 px/m along), so the whole sheet fits under the video. */
export const DESKBOX = { w: 114, h: 660, y0: -2.3, y1: 38.9, half: 2.375 };
```

- [ ] **Step 5: Add `stripShapes` and `sideways`**

In `frontend/core/line.mjs`, directly after `stripGeometry`'s closing brace:

```js
/* stripGeometry's output as explicit shapes, so one renderer draws the strip
 * upright (the phone) or on its side (the desktop). Numbers and point strings
 * pass through untouched: the phone's markup must not change by a character. */
export function stripShapes(g) {
  if (!g) return null;
  const across = (y, kind) => ({ x1: 0, y1: y, x2: g.w, y2: y, kind });
  return {
    w: g.w, h: g.h,
    rings: g.rings.map(r => ({ cx: g.w / 2, cy: r.cy, rx: r.rx, ry: r.ry, kind: r.kind })),
    lines: [...g.hogs.map(y => across(y, "hog")), ...g.tees.map(y => across(y, "tee")),
            ...g.backs.map(y => across(y, "back")), across(g.hack, "hack"),
            { x1: g.w / 2, y1: 0, x2: g.w / 2, y2: g.h, kind: "centre" }],
    stones: g.stones.map(s => ({ x: s.cx, y: s.cy, color: s.color })),
    aim: g.aim, thrown: g.thrown, ext: g.ext, path: g.path,
    broom: g.broom ? { x: g.broom.x - 2, y: g.broom.y - 5, w: 4, h: 10 } : null,
    rest: g.rest, start: g.start,
    miss: g.miss ? { x1: g.miss.x1, y1: g.miss.y, x2: g.miss.x2, y2: g.miss.y, label: g.miss.label,
                     tx: (g.miss.x1 + g.miss.x2) / 2, ty: g.miss.y - 4, anchor: "middle", size: 9 } : null,
  };
}

const r1 = v => +(+v).toFixed(1);

/* A quarter turn, (x, y) -> (length - y, x): the hack at the left and the
 * thrower's left along the top. A rotation, not a mirror, so wide and narrow
 * still mean what they say. */
export function sideways(s) {
  if (!s) return null;
  const L = s.h, W = s.h, H = s.w;
  const t = (x, y) => [r1(L - y), r1(x)];
  const pt = p => { const [x, y] = t(p.x, p.y); return { ...p, x, y }; };
  const pts = str => str == null ? null
    : str.split(" ").map(q => t(...q.split(",").map(Number)).join(",")).join(" ");
  let broom = null;
  if (s.broom) {
    const [cx, cy] = t(s.broom.x + s.broom.w / 2, s.broom.y + s.broom.h / 2);
    broom = { x: r1(cx - s.broom.h / 2), y: r1(cy - s.broom.w / 2), w: s.broom.h, h: s.broom.w };
  }
  let miss = null;
  if (s.miss) {
    const [x1, y1] = t(s.miss.x1, s.miss.y1), [x2, y2] = t(s.miss.x2, s.miss.y2);
    // The far house is ~30 px from the right edge, so the label cannot go
    // right of the bracket. It goes left of the broom marker (which spans
    // x1-12 .. x1-2), at the bracket's middle, haloed in ice by the renderer.
    miss = { x1, y1, x2, y2, label: s.miss.label, anchor: "end", size: 11, halo: true,
             tx: r1(x1 - 16), ty: r1(Math.min(Math.max((y1 + y2) / 2 + 4, 11), H - 3)) };
  }
  return {
    w: W, h: H,
    rings: s.rings.map(r => { const [cx, cy] = t(r.cx, r.cy); return { ...r, cx, cy, rx: r.ry, ry: r.rx }; }),
    lines: s.lines.map(l => {
      const [x1, y1] = t(l.x1, l.y1), [x2, y2] = t(l.x2, l.y2);
      return { ...l, x1, y1, x2, y2 };
    }),
    stones: s.stones.map(pt),
    aim: pts(s.aim), thrown: pts(s.thrown), ext: pts(s.ext), path: pts(s.path),
    broom, rest: s.rest && pt(s.rest), start: s.start && pt(s.start), miss,
  };
}
```

- [ ] **Step 6: Export them to the tests**

In `tests/js/singleton.mjs`, directly after `export const stripGeometry = core.stripGeometry;`:

```js
export const stripShapes = core.stripShapes;
export const sideways = core.sideways;
```

(`DESKBOX` reaches the tests already, through `export * from "../../frontend/core/constants.mjs"`.)

- [ ] **Step 7: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_viewer_js.py -k "TheStripOnItsSide or StripAndTrack" -q`
Expected: all pass.

- [ ] **Step 8: Build, run the subset, commit**

```bash
cd frontend && npm run build && cd ..
.venv/bin/python -m pytest tests/test_viewer_js.py tests/test_frontend_build.py -q
git add frontend/core/constants.mjs frontend/core/line.mjs tests/js/singleton.mjs tests/test_viewer_js.py \
        src/curling_score/viewer/app.js frontend/.buildstamp.json
git status --short src/curling_score/service/static/site.js   # add it too if it shows M
git commit -m "viewer core: the Detail strip as shapes, and those shapes turned on their side

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: The chart panel's list counts stones and names the entry speed

**Files:**
- Modify: `frontend/core/stats.mjs` (after `splitText`, near line 29)
- Modify: `frontend/viewer/ChartPanel.jsx:3-7` (imports) and `:159-181` (`function Detail`)
- Modify: `tests/js/singleton.mjs` (next to `splitText`, line 78)
- Test: `tests/test_viewer_js.py`

**Interfaces:**
- Produces: `houseDeltaText(d: {removed?, added?, moved?} | null | undefined) -> string`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_viewer_js.py`, after `class TestTheStripOnItsSide`:

```python
class TestTheChartPanelList:
    SRC = Path(__file__).resolve().parents[1] / "frontend/viewer/ChartPanel.jsx"

    def test_the_house_counts_stones_not_inches(self):
        got = run_js('out([houseDeltaText({added: [1]}), houseDeltaText({removed: [1, 2], moved: [3]}),'
                     ' houseDeltaText({added: []}), houseDeltaText(null)]);')
        assert got == ["1 stone in", "2 stones out, 1 stone moved", "no change", "—"]

    def test_the_list_names_the_entry_speed_and_drops_the_long_split(self):
        src = self.SRC.read_text()
        assert '["Entry speed",' in src and '"Long split"' not in src and '["Weight",' not in src
        assert '["House", houseDeltaText(shot?.house_delta)]' in src
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_viewer_js.py -k TheChartPanelList -q`
Expected: FAIL (`houseDeltaText is not defined`, and the source assertions).

- [ ] **Step 3: Add `houseDeltaText`**

In `frontend/core/stats.mjs`, directly after `splitText`:

```js
/* What the house did on this throw, in stones: a bare "1 in" now reads as an
 * inch, since the Detail figures are in feet and inches. */
export function houseDeltaText(d) {
  if (!d) return "—";
  const n = (k, word) => `${k} stone${k === 1 ? "" : "s"} ${word}`;
  return [d.removed?.length && n(d.removed.length, "out"),
          d.added?.length && n(d.added.length, "in"),
          d.moved?.length && n(d.moved.length, "moved")].filter(Boolean).join(", ") || "no change";
}
```

- [ ] **Step 4: Use it in the chart panel**

In `frontend/viewer/ChartPanel.jsx`, change the import's third line from
`splitText, thinkText, typeOf,` to `houseDeltaText, thinkText, typeOf,`. `splitText` has no
other use in this file.

Replace the start of `function Detail({ shot })`, up to and including the
`rows` array, with:

```jsx
function Detail({ shot }) {
  // The long split is the Detail card's Weight figure now, so it is not
  // repeated here; this m/s number is the speed the rock entered the house at.
  const rows = [
    ["Thrower", `${shot?.position ?? "—"}${shot ? ` (rock ${shot.rock_of_player})` : ""}`],
    ["Entry speed", shot?.entry_speed_m_s != null ? `${shot.entry_speed_m_s.toFixed(2)} m/s` : "—"],
    ["Travel", shot?.travel_m != null ? `${shot.travel_m.toFixed(2)} m` : "—"],
    ["Thinking", thinkText(shot)],
    ["House", houseDeltaText(shot?.house_delta)],
    ["Evidence", shot?.reason ?? "—"],
    ["Stones", String(shot?.stones?.length ?? 0)],
  ];
```

The `return (<dl id="detail">…)` below it is unchanged.

- [ ] **Step 5: Export it to the tests**

In `tests/js/singleton.mjs`, directly after `export const splitText = core.splitText;`:

```js
export const houseDeltaText = core.houseDeltaText;
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_viewer_js.py -k TheChartPanelList -q`
Expected: PASS.

- [ ] **Step 7: Build, run the subset, commit**

```bash
cd frontend && npm run build && cd ..
.venv/bin/python -m pytest tests/test_viewer_js.py tests/test_frontend_build.py -q
git add frontend/core/stats.mjs frontend/viewer/ChartPanel.jsx tests/js/singleton.mjs tests/test_viewer_js.py \
        src/curling_score/viewer/app.js frontend/.buildstamp.json
git status --short src/curling_score/service/static/site.js   # add it too if it shows M
git commit -m "viewer: the chart panel's list counts stones and names the entry speed, and the long split moves to Detail

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: One strip renderer, the shared figures, and `DeskDetail`

**Files:**
- Modify: `frontend/viewer/Detail.jsx` (whole file)
- Test: `tests/test_viewer_js.py` (`TestTheDetailPane`, around line 1799)

**Interfaces:**
- Consumes: `stripGeometry`, `stripShapes`, `sideways`, `DESKBOX` and
  `lineFigures`, all from `../core/index.mjs`.
- Produces:
  - `Detail({ shot, doc })`, the phone, unchanged in output.
  - `DeskDetail({ shot, doc })`, the desktop card's content.
  - `Figures({ figures })`.

- [ ] **Step 1: Record the phone strip's markup before touching the renderer**

This is the baseline for Review Focus 4. `$S` is a scratch folder. Use your
session's scratchpad directory; the path below is this planning session's.

```bash
S=/tmp/claude-1000/-home-tcuser-src-curling-score/27d05843-58b6-4652-beee-bff76b38005e/scratchpad/desk-detail && mkdir -p $S
curl -s https://curling.dimmit.net/g/s_0ZIRyB57JW0Q7dOu2/timeline.json -o $S/tl6.json
(cd /home/tcuser/src/curling_score && PYTHONPATH=src .venv/bin/python scripts/devserve.py $S/tl6.json > $S/devserve.log 2>&1 & echo $! > $S/devserve.pid)
(google-chrome --headless=new --disable-gpu --no-sandbox --remote-debugging-port=9240 --user-data-dir=$S/chrome about:blank > $S/chrome.log 2>&1 & echo $! > $S/chrome.pid)
until grep -q "view-only" $S/devserve.log; do sleep 0.5; done; grep -E " edit |view-only|review" $S/devserve.log
```

Write `$S/strip_markup.mjs`:

```js
// Usage: CDP_PORT=9240 node strip_markup.mjs <view-only url> <out.json>
import { attach } from "/home/tcuser/src/curling_score/scripts/cdp.mjs";
import { writeFileSync } from "node:fs";
const [S, OUT] = process.argv.slice(2);
const cdp = await attach("");
const sleep = ms => new Promise(r => setTimeout(r, ms));
await cdp.send("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 2, mobile: true });
await cdp.send("Emulation.setTouchEmulationEnabled", { enabled: true });
const out = {};
// A 6 ft miss, an 8 in miss, a line the hog camera lost, and no broom at all.
for (const h of ["e=1&s=3", "e=3&s=10", "e=1&s=12", "e=4&s=1"]) {
  await cdp.send("Page.navigate", { url: `${S}#tab=detail&${h}` }); await sleep(300);
  await cdp.send("Page.reload", {});   // a same-document hash change is not a fresh open
  for (let i = 0; i < 80; i++) { await sleep(250); if (await cdp.eval("!!document.querySelector('#watch .dstrip')")) break; }
  await sleep(400);
  // #watch: from Task 4 on, the hidden desktop card has a .dstrip too.
  out[h] = await cdp.eval("document.querySelector('#watch .dstrip').outerHTML");
}
writeFileSync(OUT, JSON.stringify(out, null, 1));
console.log(Object.keys(out).map(k => `${k}: ${out[k].length} chars`).join("\n"));
process.exit(0);
```

Run it (the view-only URL is the `view-only` line of `devserve.log`):

```bash
cd $S && CDP_PORT=9240 node strip_markup.mjs "$(grep view-only $S/devserve.log | awk '{print $2}')" $S/strip-before.json
```

Expected: four lines, each a few thousand chars. Leave the server and Chrome
running for Step 6.

- [ ] **Step 2: Update the tests**

In `tests/test_viewer_js.py`, `class TestTheDetailPane`: replace
`test_the_broom_marker_stands_upright` (it pins a JSX expression this task
removes) with the four tests below. The class already has
`SRC = …/frontend/viewer/Detail.jsx`.

```python
    def test_the_broom_marker_is_drawn_from_its_shape(self):
        # Upright 4 x 10 on the phone, 10 x 4 on its side: TestTheStripOnItsSide pins both.
        assert "width={s.broom.w} height={s.broom.h}" in self.SRC.read_text()

    def test_the_desktop_turns_the_strip_and_shares_the_figures(self):
        src = self.SRC.read_text()
        assert "sideways(stripShapes(stripGeometry(shot, DESKBOX)))" in src
        assert "stripShapes(stripGeometry(shot))" in src
        assert src.count("<Figures figures={f.figures} />") == 2
        assert ("Sheet from above, thrower at the left · across ×1.5 · figures ±4 in · "
                "wide = the side away from the curl") in src

    def test_an_empty_end_says_so_rather_than_blaming_the_broom(self):
        assert 'if (!shot) return <p className="dnone">No rocks were detected in this end</p>;' in self.SRC.read_text()

    def test_the_label_halo_is_ice_under_the_text(self):
        assert '{ stroke: PAINT.ice, strokeWidth: 3, paintOrder: "stroke" }' in self.SRC.read_text()
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_viewer_js.py -k TheDetailPane -q`
Expected: the four new tests FAIL. `test_it_draws_the_strip_and_the_six_figures_from_the_core`
and `test_the_caption_is_the_spec_s` still PASS.

- [ ] **Step 4: Rewrite `frontend/viewer/Detail.jsx`**

```jsx
/* Detail: was this rock thrown at the skip's broom? The whole sheet as a strip
 * beside (the phone) or above (the desktop) six figures. Everything it draws
 * comes from core/line.mjs; this file only turns it into markup. */
import { DESKBOX, lineFigures, sideways, stripGeometry, stripShapes } from "../core/index.mjs";
import { PAINT } from "../core/constants.mjs";

const RING = { twelve: PAINT.twelve, eight: PAINT.ice, four: PAINT.four, button: PAINT.ice };
const GOLD = "#a07a00";      // the rock's path: #e8b400 is unreadable on the ice
const MUTED = "#6d6455";
const LINE = { hog: [PAINT.red, 1.2] };
const PAINTED = [PAINT.iceLine, 0.8];   // tee, back, hack and centre lines

/* One renderer for both orientations. `fluid` leaves the size to CSS: the
 * desktop card scales the drawing to its own width through the viewBox. The
 * attribute order below is the phone's markup, character for character. */
function Strip({ shot, shapes: s, label, fluid }) {
  if (!s) return <div className="dstrip dstrip-none" aria-hidden="true" />;
  const own = shot.color === "red" ? PAINT.red : PAINT.yellow;
  return (
    <svg className="dstrip" {...(fluid ? {} : { width: s.w, height: s.h })} viewBox={`0 0 ${s.w} ${s.h}`}
         role="img" aria-label={label}>
      {s.rings.map((r, i) => (
        <ellipse key={i} cx={r.cx} cy={r.cy} rx={r.rx} ry={r.ry} fill={RING[r.kind]} fillOpacity={0.55} />
      ))}
      {s.lines.map((l, i) => {
        const [stroke, width] = LINE[l.kind] ?? PAINTED;
        return <line key={`l${i}`} x1={l.x1} y1={l.y1} x2={l.x2} y2={l.y2} stroke={stroke} strokeWidth={width} />;
      })}
      {s.stones.map((t, i) => (
        <circle key={`s${i}`} cx={t.x} cy={t.y} r={3.2} fill={t.color === "red" ? PAINT.red : PAINT.yellow}
                fillOpacity={0.6} stroke={PAINT.graniteEdge} strokeWidth={0.6} />
      ))}
      {s.aim ? <polyline points={s.aim} fill="none" stroke={MUTED} strokeWidth={1.4} strokeDasharray="4 3" /> : null}
      {s.ext ? <polyline points={s.ext} fill="none" stroke={PAINT.accent} strokeWidth={1.2} strokeDasharray="2 2.5" /> : null}
      {s.thrown ? <polyline points={s.thrown} fill="none" stroke={PAINT.accent} strokeWidth={2.4} /> : null}
      {s.path ? <polyline points={s.path} fill="none" stroke={GOLD} strokeWidth={2.2} strokeLinejoin="round" /> : null}
      {s.miss ? (
        <g>
          <line x1={s.miss.x1} y1={s.miss.y1} x2={s.miss.x2} y2={s.miss.y2} stroke={PAINT.accent} strokeWidth={1} />
          <text x={s.miss.tx} y={s.miss.ty} fontSize={s.miss.size} fontWeight={700}
                textAnchor={s.miss.anchor} fill={PAINT.accent}
                {...(s.miss.halo ? { stroke: PAINT.ice, strokeWidth: 3, paintOrder: "stroke" } : {})}>{s.miss.label}</text>
        </g>
      ) : null}
      {s.broom ? (
        <rect x={s.broom.x} y={s.broom.y} width={s.broom.w} height={s.broom.h} rx={1}
              fill={PAINT.accent} stroke={own} strokeWidth={1}><title>skip&apos;s broom</title></rect>
      ) : null}
      {s.rest ? <circle cx={s.rest.x} cy={s.rest.y} r={4.2} fill={own} stroke={PAINT.accent} strokeWidth={1.2} /> : null}
      {s.start ? <circle cx={s.start.x} cy={s.start.y} r={3.2} fill={PAINT.accent} /> : null}
    </svg>
  );
}

const Check = () => (
  <svg width="13" height="13" viewBox="0 0 24 24" aria-hidden="true"
       style={{ fill: "none", strokeWidth: 2.6 }}><path d="M4 12 L10 18 L20 6" /></svg>
);

/* The six figures. The phone stacks them; the desktop card's CSS lays them
 * out three across. */
export function Figures({ figures }) {
  return (
    <dl className="dfigs">
      {figures.map(x => (
        <div key={x.key} className={x.dim ? "dfig dim" : "dfig"}>
          <dt>{x.label}</dt>
          <dd className="dval">{x.value}</dd>
          <dd className={x.tick ? `dnote ${x.tick}` : "dnote"}>
            {x.tick === "confirmed" ? <Check /> : null}{x.note}
          </dd>
        </div>
      ))}
    </dl>
  );
}

export function Detail({ shot, doc }) {
  const f = lineFigures(shot, doc);
  if (f.predates) return <p className="dnone">{f.reason}</p>;
  return (
    <>
      <div className="dbody">
        <Strip shot={shot} shapes={stripShapes(stripGeometry(shot))}
               label="The sheet from above, thrower at the bottom: the intended line, the thrown line and where the rock went" />
        <Figures figures={f.figures} />
      </div>
      <p className="dcap">Sheet from above, thrower at the bottom · across ×3 · figures ±4 in · wide = the side away from the curl</p>
    </>
  );
}

/* The desktop card under the video: the same strip on its side, the same
 * figures below it. */
export function DeskDetail({ shot, doc }) {
  // An end where detection found nothing has no rock to describe, and
  // lineFigures would blame a broom that was never the problem.
  if (!shot) return <p className="dnone">No rocks were detected in this end</p>;
  const f = lineFigures(shot, doc);
  if (f.predates) return <p className="dnone">{f.reason}</p>;
  return (
    <>
      <Strip shot={shot} shapes={sideways(stripShapes(stripGeometry(shot, DESKBOX)))} fluid
             label="The sheet from above, thrower at the left: the intended line, the thrown line and where the rock went" />
      <Figures figures={f.figures} />
      <p className="dcap">Sheet from above, thrower at the left · across ×1.5 · figures ±4 in · wide = the side away from the curl</p>
    </>
  );
}
```

- [ ] **Step 5: Run the tests, then build**

```bash
.venv/bin/python -m pytest tests/test_viewer_js.py -k "TheDetailPane or TheStripOnItsSide" -q
cd frontend && npm run build && cd ..
```

Expected: PASS, then a clean build. ESLint must pass: `DESKBOX`, `sideways`
and `DeskDetail` are all used or exported.

- [ ] **Step 6: The phone strip's markup is unchanged**

The dev server serves `app.js` from disk, so the rebuilt bundle is live. Record
the markup again and compare:

```bash
cd $S && CDP_PORT=9240 node strip_markup.mjs "$(grep view-only $S/devserve.log | awk '{print $2}')" $S/strip-after.json
cmp $S/strip-before.json $S/strip-after.json && echo "phone strip unchanged"
```

Expected: `phone strip unchanged`. If `cmp` reports a difference, diff the
entries (for example with `python3 -c` and `difflib`) and fix the renderer
until they match. Do not update the baseline.

- [ ] **Step 7: Run the subset and commit**

```bash
cd /home/tcuser/src/curling_score
.venv/bin/python -m pytest tests/test_viewer_js.py tests/test_frontend_build.py -q
git add frontend/viewer/Detail.jsx tests/test_viewer_js.py src/curling_score/viewer/app.js frontend/.buildstamp.json
git status --short src/curling_score/service/static/site.js   # add it too if it shows M
git commit -m "viewer: one strip renderer for both orientations, the shared figures, and the desktop's Detail

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Mount the card, and lay the grid out by area

**Files:**
- Modify: `frontend/viewer/App.jsx`: the import near line 25, and between
  `#playCard`'s closing `</section>` and `<section className="card" id="houseCard"`
  (around line 414)
- Modify: `src/curling_score/viewer/style.css`: replace lines 75–79 (the
  `main` grid and its two width rules), and add the card's rules after them
- Test: `tests/test_viewer_js.py` (new class after `TestTheTabShell`)

**Interfaces:**
- Consumes: `DeskDetail({ shot, doc })` from Task 3.

- [ ] **Step 1: Write the failing tests**

```python
class TestTheDesktopDetailCard:
    APP = Path(__file__).resolve().parents[1] / "frontend/viewer/App.jsx"

    def css(self):
        return (VIEWER / "style.css").read_text()

    def test_the_card_follows_the_play_card(self):
        src = self.APP.read_text()
        i_play, i_card, i_house = (src.index('id="playCard"'), src.index('id="detailCard"'),
                                   src.index('id="houseCard"'))
        assert i_play < i_card < i_house
        assert "<DeskDetail shot={shot} doc={doc} />" in src

    def test_the_grid_is_laid_out_by_area_at_every_width(self):
        css = self.css()
        for needle in ('grid-template-areas: "play house chart" "detail house chart";',
                       'grid-template-areas: "play house" "detail house" "chart house";',
                       'grid-template-areas: "play" "detail" "house" "chart";',
                       "#detailCard { grid-area: detail; }", "#chart { grid-area: chart; }"):
            assert needle in css, needle

    def test_the_phones_get_their_own_grid_back_and_no_card(self):
        assert ("@media (max-width:640px){\n"
                "  main { grid-template-areas: none; }\n"
                "  #playCard, #houseCard, #chart { grid-area: auto; }\n"
                "  #detailCard { display: none; }\n"
                "}") in self.css()

    def test_the_strip_scales_to_the_card_and_the_figures_go_three_across(self):
        css = self.css()
        strip = css[css.index("#detailCard .dstrip {"):css.index("}", css.index("#detailCard .dstrip {"))]
        assert "width: 100%" in strip and "height: auto" in strip
        figs = css[css.index("#detailCard .dfigs {"):css.index("}", css.index("#detailCard .dfigs {"))]
        assert "grid-template-columns: repeat(3, minmax(0, 1fr))" in figs
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_viewer_js.py -k TheDesktopDetailCard -q`
Expected: FAIL (`ValueError: substring not found` on `id="detailCard"`, and the
missing CSS).

- [ ] **Step 3: Mount the card**

In `frontend/viewer/App.jsx`, add to the imports after `import { House } from "./House.jsx";`:

```jsx
import { DeskDetail } from "./Detail.jsx";
```

Then insert this between `#playCard`'s closing `</section>` and the line
`<section className="card" id="houseCard" …>`:

```jsx
        {/* Always mounted: CSS decides who is visible (hidden on phones), the
            same rule that keeps the player from ever being reparented. */}
        <section className="card" id="detailCard" aria-label="Shot detail">
          <DeskDetail shot={shot} doc={doc} />
        </section>

```

- [ ] **Step 4: Lay the grid out by area**

In `src/curling_score/viewer/style.css`, replace lines 75–79:

```css
main { display:grid; gap:14px; padding:14px;
       grid-template-columns:minmax(0,1fr) minmax(290px,360px) 310px;
       align-items:start; }
@media (max-width:1180px){ main { grid-template-columns:minmax(0,1fr) 320px; } }
@media (max-width:820px){ main { grid-template-columns:1fr; } }
```

with:

```css
/* Laid out by area so the shot detail sits right under the video: the house
   and the chart panel span the rows beside it, and the last row is 1fr so a
   tall column grows that row rather than opening a gap under the video. See
   docs/superpowers/specs/2026-09-24-desktop-shot-detail-design.md §1. */
main { display:grid; gap:14px; padding:14px;
       grid-template-columns:minmax(0,1fr) minmax(290px,360px) 310px;
       grid-template-areas: "play house chart" "detail house chart";
       grid-template-rows: auto 1fr;
       align-items:start; }
#playCard { grid-area: play; }
#detailCard { grid-area: detail; }
#houseCard { grid-area: house; }
#chart { grid-area: chart; }
@media (max-width:1180px){ main { grid-template-columns:minmax(0,1fr) 320px;
  grid-template-areas: "play house" "detail house" "chart house";
  grid-template-rows: auto auto 1fr; } }
@media (max-width:820px){ main { grid-template-columns:1fr;
  grid-template-areas: "play" "detail" "house" "chart";
  grid-template-rows: none; } }
/* The phone layouts place these cards themselves, and a grid-area naming an
   area that no longer exists would push a card into the implicit grid, so
   the names go as well as the areas. */
@media (max-width:640px){
  main { grid-template-areas: none; }
  #playCard, #houseCard, #chart { grid-area: auto; }
  #detailCard { display: none; }
}

/* --- shot detail (desktop) ---------------------------------------------- */
/* The phone's Detail rules live inside its media query; these are the
   desktop card's own. The strip scales to the card through its viewBox. */
#detailCard .dstrip { display: block; width: 100%; height: auto; background: var(--ice);
                      border: 1px solid var(--line); border-radius: 4px; }
#detailCard .dstrip-none { aspect-ratio: 660 / 114; }
#detailCard .dfigs { margin: 8px 0 0; display: grid; gap: 0 18px;
                     grid-template-columns: repeat(3, minmax(0, 1fr)); }
#detailCard .dfig { padding: 7px 0; border-bottom: 1px solid var(--line); min-width: 0; }
#detailCard .dfig dt { font-size: 10.5px; letter-spacing: .08em; text-transform: uppercase;
                       color: var(--muted); font-weight: 600; }
#detailCard .dfig .dval { margin: 0; font-family: var(--display); font-size: 19px; font-weight: 700;
                          line-height: 1.15; white-space: nowrap; font-variant-numeric: tabular-nums; }
#detailCard .dfig .dnote { margin: 0; font-size: 11.5px; color: var(--muted);
                           display: flex; align-items: center; gap: 5px; }
#detailCard .dfig.dim .dval { color: var(--muted); }
#detailCard .dnote.confirmed svg { flex: none; stroke: var(--ok); }
#detailCard .dcap { font-size: 11px; color: var(--muted); margin: 6px 0 0; }
#detailCard .dnone { font-size: 14px; color: var(--muted); margin: 0; }
```

- [ ] **Step 5: Run the tests to verify they pass, then build**

```bash
.venv/bin/python -m pytest tests/test_viewer_js.py -k "TheDesktopDetailCard or ThePhoneTabsCss or TheTabShell" -q
cd frontend && npm run build && cd ..
```

Expected: PASS, then a clean build.

- [ ] **Step 6: Run the subset and commit**

```bash
.venv/bin/python -m pytest tests/test_viewer_js.py tests/test_frontend_build.py -q
git add frontend/viewer/App.jsx src/curling_score/viewer/style.css tests/test_viewer_js.py \
        src/curling_score/viewer/app.js frontend/.buildstamp.json
git status --short src/curling_score/service/static/site.js   # add it too if it shows M
git commit -m "viewer: the desktop shows the shot detail under the video, with the sheet on its side

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Check it in a browser at every width

**Files:** none committed. The scripts live in `$S` from Task 3.

- [ ] **Step 1: A schema-5 chart for the "predates" state**

The dev server from Task 3 serves the schema-6 game. Start a second one on a
schema-5 game:

```bash
curl -s https://curling.dimmit.net/g/s_0QFeYeXEGfTNDSTzL/timeline.json -o $S/tl5.json
python3 -c "import json; print(json.load(open('$S/tl5.json')).get('schema_version'))"   # expect 5
(cd /home/tcuser/src/curling_score && PYTHONPATH=src .venv/bin/python scripts/devserve.py $S/tl5.json > $S/devserve5.log 2>&1 & echo $! > $S/devserve5.pid)
until grep -q "view-only" $S/devserve5.log; do sleep 0.5; done
```

- [ ] **Step 2: Write `$S/check_desktop.mjs`**

```js
// Usage: CDP_PORT=9240 node check_desktop.mjs <c url> <s url> <g url> <c url of schema 5>
import { attach } from "/home/tcuser/src/curling_score/scripts/cdp.mjs";
const [C, S, G, C5] = process.argv.slice(2);
const cdp = await attach("");
const sleep = ms => new Promise(r => setTimeout(r, ms));
const fails = [], ok = (name, cond, got) => { console.log(`${cond ? "ok  " : "FAIL"} ${name}`, cond ? "" : JSON.stringify(got)); if (!cond) fails.push(name); };

async function open(url, w, h, mobile = false) {
  await cdp.send("Emulation.setDeviceMetricsOverride", { width: w, height: h, deviceScaleFactor: 1, mobile });
  await cdp.send("Emulation.setTouchEmulationEnabled", { enabled: mobile });
  await cdp.send("Page.navigate", { url }); await sleep(300);
  await cdp.send("Page.reload", {});
  for (let i = 0; i < 80; i++) { await sleep(250); if (await cdp.eval("!!document.querySelector('#detailCard')")) break; }
  await sleep(900);
}
const rects = () => cdp.eval(`(() => { const o = {}; for (const id of ["playCard", "detailCard", "houseCard", "chart"]) {
  const r = document.getElementById(id).getBoundingClientRect(); o[id] = { l: Math.round(r.left), t: Math.round(r.top),
  r: Math.round(r.right), b: Math.round(r.bottom) }; }
  o.overflow = [...document.querySelectorAll('#detailCard .dval')].filter(e => e.scrollWidth > e.clientWidth).map(e => e.textContent);
  o.display = getComputedStyle(document.getElementById('detailCard')).display;
  o.text = document.getElementById('detailCard').innerText;
  o.strip = !!document.querySelector('#detailCard svg.dstrip');
  return o; })()`);

// 1440 x 900, the edit page: under the video and above the fold.
await open(`${C}#e=1&s=3`, 1440, 900);
let r = await rects();
ok("1440: card sits 14 px under the video, same column", Math.abs(r.detailCard.t - r.playCard.b - 14) <= 1 && r.detailCard.l === r.playCard.l, r);
ok("1440: card ends above the fold", r.detailCard.b <= 900, r.detailCard);
ok("1440: strip and six figures", r.strip && /AT THE BROOM|At the broom/i.test(r.text) && /6 ft 1 in narrow/.test(r.text), r.text);
ok("1440: house and chart beside it", r.houseCard.l > r.playCard.r && r.chart.l > r.houseCard.r, r);

// 1280 x 800: record the fold overrun (the spec expects about 10 px).
await open(`${C}#e=1&s=3`, 1280, 800);
r = await rects();
console.log(`info 1280x800: card bottom ${r.detailCard.b} (${r.detailCard.b - 800} px past the fold)`);
ok("1280: no figure overflows", r.overflow.length === 0, r.overflow);

// 1000: two columns, details then chart panel in column 1, house in column 2.
await open(`${C}#e=1&s=3`, 1000, 800);
r = await rects();
ok("1000: card 14 px under the video", Math.abs(r.detailCard.t - r.playCard.b - 14) <= 1, r);
ok("1000: chart panel under the card in column 1", r.chart.l === r.playCard.l && Math.abs(r.chart.t - r.detailCard.b - 14) <= 1, r);
ok("1000: house in column 2", r.houseCard.l > r.playCard.r, r);

// 700: one column, in order.
await open(`${C}#e=1&s=3`, 700, 900);
r = await rects();
ok("700: video, details, house, chart", r.playCard.t < r.detailCard.t && r.detailCard.t < r.houseCard.t && r.houseCard.t < r.chart.t, r);
ok("700: no figure overflows", r.overflow.length === 0, r.overflow);

// The other surfaces at 1440.
for (const [name, url] of [["view-only", S], ["review", G]]) {
  await open(`${url}#e=1&s=3`, 1440, 900);
  r = await rects();
  ok(`${name} 1440: card under the video, above the fold`, Math.abs(r.detailCard.t - r.playCard.b - 14) <= 1 && r.detailCard.b <= 900, r);
}

// The states.
await open(`${C}#e=1&s=12`, 1440, 900);
r = await rects();
ok("a lost line: strip drawn, reason given", r.strip && /The hog-line camera lost this rock/.test(r.text), r.text);
await open(`${C}#e=4&s=1`, 1440, 900);
r = await rects();
ok("no broom: reason given", /No broom was held still before the release/.test(r.text), r.text);
await open(`${C5}#e=1&s=1`, 1440, 900);
r = await rects();
ok("schema 5: one line, no drawing", r.text.trim() === "This chart predates line measurement" && !r.strip, r.text);

// The phone: no card, and the cards are back on auto placement.
await open(`${S}#tab=detail&e=1&s=3`, 390, 844, true);
r = await rects();
const area = await cdp.eval("getComputedStyle(document.getElementById('houseCard')).gridArea");
ok("phone: card hidden", r.display === "none", r.display);
ok("phone: cards on auto placement", /auto/.test(area), area);

console.log(fails.length ? `FAILURES: ${fails.join("; ")}` : "ALL OK");
process.exit(0);
```

- [ ] **Step 3: Run it**

```bash
cd $S && CDP_PORT=9240 node check_desktop.mjs \
  "$(grep ' edit ' $S/devserve.log | awk '{print $2}')" \
  "$(grep view-only $S/devserve.log | awk '{print $2}')" \
  "$(grep review $S/devserve.log | awk '{print $2}')" \
  "$(grep ' edit ' $S/devserve5.log | awk '{print $2}')"
```

Expected: every line `ok`, an `info` line with the 1280 × 800 overrun, and
`ALL OK`.

- A `FAIL` on a gap under the video means a grid-area rule is wrong: re-read
  Task 4 Step 4.
- An overflow `FAIL` names the figure text. Tighten `#detailCard .dfig .dval`'s
  font-size (19 → 17), not the column count.

- [ ] **Step 4: Look at it**

Capture 1440 × 900 and 390 × 844 screenshots (`Page.captureScreenshot` through
`cdp.send`, written with `Buffer.from(data, "base64")`) and look at them. Check
that the card matches artboard A of the mockup, and that the phone looks as it
did before.

- [ ] **Step 5: Stop what you started**

```bash
kill $(cat $S/devserve.pid) $(cat $S/devserve5.pid) $(cat $S/chrome.pid)
```

Kill only these PIDs. Another session may be running its own `devserve.py`.

- [ ] **Step 6: Hand back**

Report the check output, the 1280 × 800 overrun and the screenshots. Merging to
`main` and deploying (`deploy/deploy-api.sh` from a clean worktree) happen only
when the user asks.
