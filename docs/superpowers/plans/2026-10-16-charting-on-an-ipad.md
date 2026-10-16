# Charting on an iPad Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** On a portrait touch screen 700–1199 px wide (every iPad in portrait), the viewer charts a rock with no scroll and no hover:
- the video across the top;
- the rock chips;
- the house beside the seven figures;
- a touch-sized charting bar along the bottom, with Miss, Note and Order in small sheets and a Next rock button.

Tapping the house opens it large under the video.

**Architecture:**
- **Gate:** a third layout gate, `TABLET_QUERY`, joins `PHONE_QUERY` and `WIDE_QUERY`. It is one string shared by JS (`runtime/tablet.mjs`, read with `useSyncExternalStore`) and the stylesheet (one `@media` block).
- **Components:** the tablet reuses the wide layout's components: the charting bar, the figures card, the header's rock name and Thinking button, and both large dialogs. `const desk = wide || tablet` in App.jsx picks them; the line chart stays wide-only.
- **New pieces:**
  - small pure helpers in `frontend/core/tablet.mjs`;
  - a `Sheet` dialog component;
  - a tablet branch for the chips row, the house card's head and the header;
  - CSS.
- **Everything else** renders the same DOM as today.

**Tech Stack:**
- React 19 + esbuild in `frontend/`, built only with `npm run build`;
- plain ES modules in `frontend/core`, tested in node through `tests/test_viewer_js.py`;
- headless Chrome through `scripts/cdp.mjs` with touch emulation;
- `scripts/domsnap.mjs` for the before/after DOM diff.

**Spec:** `docs/superpowers/specs/2026-10-16-charting-on-an-ipad-design.md`.
- **Mockups:** the canvas https://claude.ai/artifact/7XofqtMe3if5htn3J5fhF8 (board "P · Portrait"; "A · the house opened large" for the editor's look).
- **The mock's CSS** is the reference for sizes. It lives in the main checkout at `/home/tcuser/src/curling_score/out/mockups/ipad/mock/touch.js` (touch sizes), `P.js` (the grid) and `H.js` (the open house), and is read-only there.
- **The mock's runner** is `out/mockups/ipad/mock/run.mjs`. It forces the wide branch and lifts the wide CSS; the real build replaces both.

## Global Constraints

- **Where to work:**
  - only in the worktree `.claude/worktrees/ipad-portrait`, on branch `ipad-portrait`;
  - another session commits to the main checkout: never edit, stage, build or commit there;
  - reading the mock kit and the timelines named below from the main checkout is fine.
- **The gate:** `TABLET_QUERY = "(any-pointer: coarse) and (orientation: portrait) and (min-width: 700px) and (max-width: 1199px)"`, byte for byte in `constants.mjs` and in the stylesheet's `@media`.
- **Unchanged everywhere else:**
  - phones (`PHONE_QUERY`);
  - 641–699 px;
  - every landscape window, including a touch one at 1180×820;
  - any window without touch, including 820×1080;
  - the wide layout (≥1700).

  Their DOM must not change: domsnap's 17 states are identical, and `scripts/deskcheck.mjs` still says `deskcheck: all good`.
- **Fit:** no page scroll at 820×1080, 744×1050, 834×1110 and 1024×1290, with touch, on `/c/`, `/s/` and `/g/`. Every card ends above the fold, the cards never overlap, and the video keeps 16:9.
- **Floors:**
  - the video is never narrower than 560 px; below that the page may scroll;
  - every visible control is at least 40 px tall and 36 px wide (the target is 44; the chips are allowed down to 36 wide).
- **Surfaces:**
  - `/c/` charts.
  - `/s/` shows the bar inert. Miss and Note open read-only; there is no Order, Swapped roles, Charted or ⤢.
  - `/g/` hides the bar (`body[data-mode="review"] #grading`, an existing rule).
- **Data:** no pipeline, timeline or schema change.
- **The player:** the video iframe is never reparented (`runtime/player.mjs`).
- **Keys:** keyboard shortcuts are unchanged. Global shortcuts stay off while any `dialog[open]` (App.jsx already does this), so the sheets need nothing more.
- **Ids:** each id is in the page once. No element ids inside drawings.
- **Copy** is verbatim:
  - `Next rock →`, `✔ Charted`, `Line & speed`, `Done`;
  - `Tap the house to open it large and move stones.`, `Drag a stone · tap the ice to add one`;
  - `Miss…`, `Note…`, `Order…`, `Swapped roles…`; a set value reads `Miss: wide`, `Swapped: Red`;
  - `Tap outside to close`.
- **Build:** only `cd frontend && npm run build` (eslint + build stamp). Commit `src/curling_score/viewer/app.js`, `src/curling_score/service/static/site.js` (if it changed) and `frontend/.buildstamp.json` with the sources that changed them.
- **pytest:** by file or `-k` subset (the full suite OOMs this box), from the worktree root: `PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest <file> -q [-k …]`.
- **Processes:** start your own devserve and Chrome, and stop them by PID. Never by name pattern: another session runs its own.
- **Commits** end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

**Timelines for the checks** (read-only; devserve serves one per run):
- **Fours:** `~/curling-work/flags-1015/tl-s_0cYt.json`, 5 ends × 16 rocks.
  - `#e=2&s=4`: the board rock.
  - `#e=3&s=3`: no delivery, "The hog-line camera lost this rock".
  - `#e=1&s=16`: last of end 1. `#e=5&s=16`: the game's last.
- **Blanks:** `/home/tcuser/src/curling_score/out/mockups/desk1080/build-check/tl-blanks.json`. End 2 rock 5 is never seen; end 2 rock 6 is a seen blank. They are the only blanks.
- **Doubles:** `~/curling-work/nightly-review/tl/s_0ToNiuWlb3vM1V6vU.json`, `#e=2&s=3`.

## Review Focus

1. **Safari's toolbar hiding or showing** resizes the viewport mid-edit. The open house and an open sheet must stay open: the resize handler must not take that for leaving a layout. Test: Task 2 `house` (a height change while open), made to pass in Task 4.
2. **Turning the iPad while a note is typed:** what was typed survives the layout swap (the 150 ms debounce has committed), and the rock is not left busy. Test: Task 2 `rotate`, made to pass in Task 3.
3. **A rock with no delivery** (`#e=3&s=3`): no Line & speed cell and no empty button. The other seven figures are laid out as usual. Test: Task 2 `fit` (asserts `#lineOpen` absent there), Task 3.
4. **The ⋯ dropdown's lead-in box:** tapping the number box to type must not close the dropdown. Test: Task 2 `menu`, Task 3.
5. **✔ Charted on the last blank:** it must close the editor, not "jump" to the rock it is on. Test: Task 1 `nextBlankOther` (node), and Task 2 `blanks`, made to pass in Task 4.

---

### Task 1: Worktree, base snapshot, the gate and the core helpers

**Files:**
- Create: `frontend/runtime/tablet.mjs`
- Create: `frontend/core/tablet.mjs`
- Modify: `frontend/core/constants.mjs` (`TABLET_QUERY`, `TABLETDELIVERYBOX`, `TABLETSPEEDBOX`)
- Modify: `frontend/core/index.mjs` (`export * from "./tablet.mjs";`)
- Modify: `src/curling_score/viewer/style.css` (an empty tablet block, so the gate test can find it)
- Test: `tests/test_viewer_js.py` (new classes `TestTheTabletLayoutGate`, `TestSheetButtonText`, `TestNextBlankOther`, `TestEndLineText`, `TestTheTabletBoxes`)

**Interfaces:**
- Produces:
  - `TABLET_QUERY` (string above);
  - `TABLETDELIVERYBOX = { ...BIGDELIVERYBOX, w: 480, h: 160 }` and `TABLETSPEEDBOX = { ...SPEEDBOX, w: 480, h: 115 }`. At 480 units a 688 px drawing (an iPad mini's dialog) scales by 1.43, so the charts' 8-unit labels draw at 11.5 px;
  - `runtime/tablet.mjs`: `subscribe(cb) -> unsubscribe` and `isTablet() -> boolean`, the same shape as `runtime/wide.mjs`;
  - `core/tablet.mjs`:
    - `sheetButtonText(label: string, value: string | null, setLabel = label, max = 16) -> string`;
    - `nextBlankOther(items, ei, si) -> item | null`: the next blank after (ei, si), wrapping round, never (ei, si) itself;
    - `endLineText(summary, endNumber, name) -> string`, where `summary` is `endSummary(view, ei)` (or null) and `name` is `color -> team name`;
    - `swapText(swapped, name) -> string | null`: the names of the swapped colours, red first, joined with ", ", or null;
  - `out/ipad-check/domsnap-base.json`, the DOM baseline Tasks 3 and 6 diff against.

- [ ] **Step 1: Create the worktree, move the spec and plan in, link node_modules**

```bash
cd /home/tcuser/src/curling_score
git worktree add .claude/worktrees/ipad-portrait -b ipad-portrait main
cd .claude/worktrees/ipad-portrait
mkdir -p docs/superpowers/plans out/ipad-check
mv /home/tcuser/src/curling_score/docs/superpowers/specs/2026-10-16-charting-on-an-ipad-design.md docs/superpowers/specs/
mv /home/tcuser/src/curling_score/docs/superpowers/plans/2026-10-16-charting-on-an-ipad.md docs/superpowers/plans/
ln -s /home/tcuser/src/curling_score/frontend/node_modules frontend/node_modules
git add docs/superpowers/specs/2026-10-16-charting-on-an-ipad-design.md docs/superpowers/plans/2026-10-16-charting-on-an-ipad.md
git commit -m "docs: charting on an iPad, spec and plan

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

Expected: one commit on `ipad-portrait`. If the `frontend/node_modules` symlink shows as untracked, leave it; never add it.

- [ ] **Step 2: Snapshot today's DOM**

From the worktree root, in separate shells:

```bash
PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python scripts/devserve.py ~/curling-work/flags-1015/tl-s_0cYt.json
google-chrome --headless=new --disable-gpu --no-sandbox --hide-scrollbars --remote-debugging-port=9371 --user-data-dir=$(mktemp -d) about:blank
CDP_PORT=9371 node scripts/domsnap.mjs EDIT_URL VIEW_URL REVIEW_URL --out out/ipad-check/domsnap-base.json
```

Pass the edit, view-only and review URLs devserve prints, with no hash. Expected: the file is written with all 17 states `reached`. Note the PIDs; stop both with `kill <pid>` when done.

- [ ] **Step 3: Write the failing tests**

In `tests/test_viewer_js.py`, after `TestTheWideLayoutGate`:

```python
class TestTheTabletLayoutGate:
    def test_tablet_query_is_a_media_query_byte_for_byte(self):
        css = (VIEWER / "style.css").read_text()
        queries = [q.strip() for q in re.findall(r"@media ([^{]+)\{", css)]
        want = run_js("out(TABLET_QUERY);")
        assert want == ("(any-pointer: coarse) and (orientation: portrait) "
                        "and (min-width: 700px) and (max-width: 1199px)")
        assert want in queries, "TABLET_QUERY is not one of the stylesheet's media queries"


class TestSheetButtonText:
    def test_nothing_set_asks(self):
        assert run_js('out([sheetButtonText("Miss", null), sheetButtonText("Miss", "")]);') == ["Miss…", "Miss…"]

    def test_a_value_is_shown(self):
        assert run_js('out(sheetButtonText("Miss", "wide"));') == "Miss: wide"

    def test_a_long_value_is_cut(self):
        got = run_js('out(sheetButtonText("Note", "came up two feet short of the tee"));')
        assert got == "Note: came up two feet…"

    def test_the_set_label_can_differ(self):
        assert run_js('out([sheetButtonText("Swapped roles", null, "Swapped"), '
                      'sheetButtonText("Swapped roles", "Red", "Swapped")]);') == ["Swapped roles…", "Swapped: Red"]


class TestNextBlankOther:
    ITEMS = [{"ei": 0, "si": 2}, {"ei": 1, "si": 0}]

    def go(self, items, ei, si):
        return run_js(f"out(nextBlankOther({json.dumps(items)}, {ei}, {si}));")

    def test_the_next_one(self):
        assert self.go(self.ITEMS, 0, 2) == {"ei": 1, "si": 0}

    def test_it_wraps(self):
        assert self.go(self.ITEMS, 1, 0) == {"ei": 0, "si": 2}

    def test_never_the_rock_it_is_on(self):
        assert self.go([{"ei": 0, "si": 2}], 0, 2) is None

    def test_none_left(self):
        assert self.go([], 0, 0) is None


class TestEndLineText:
    NAME = "c => ({ red: 'Red', yellow: 'Yellow' })[c]"

    def line(self, summary, n=3):
        return run_js(f"out(endLineText({json.dumps(summary)}, {n}, {self.NAME}));")

    def test_hammer_and_power_play(self):
        s = {"hammer": "red", "hammerSource": "throws", "powerPlay": {"color": "red", "side": "left"}}
        assert self.line(s) == "End 3 · Red has hammer · power play, red, left"

    def test_hammer_from_the_placement(self):
        assert self.line({"hammer": "yellow", "hammerSource": "placement", "powerPlay": None}) \
            == "End 3 · Yellow has hammer (from the placement)"

    def test_no_summary(self):
        assert self.line(None, 1) == "End 1"

    def test_the_swapped_names(self):
        assert run_js(f"out([swapText({{red: true, yellow: false}}, {self.NAME}), "
                      f"swapText({{yellow: true, red: true}}, {self.NAME}), swapText({{}}, {self.NAME})]);") \
            == ["Red", "Red, Yellow", None]


class TestTheTabletBoxes:
    def test_they_are_the_big_boxes_narrowed(self):
        big, d = run_js("out([BIGDELIVERYBOX, TABLETDELIVERYBOX]);")
        assert d == {**big, "w": 480, "h": 160}
        sp, s = run_js("out([SPEEDBOX, TABLETSPEEDBOX]);")
        assert s == {**sp, "w": 480, "h": 115}

    def test_the_speed_and_the_delivery_share_their_x(self):
        r = json.dumps(ROCKS["e2r4"])
        g, sg = run_js(f"const s = {r}; out([deliveryGeometry(s, TABLETDELIVERYBOX), "
                       "speedGeometry(deliverySpeed(s), TABLETSPEEDBOX, s)]);")
        gx = {l["kind"]: l["x1"] for l in g["lines"] if l["kind"] != "centre"}
        sx = {l["kind"]: l["x"] for l in sg["lines"]}
        for kind in ("hack", "tee", "hog"):
            assert sx[kind] == pytest.approx(gx[kind], abs=0.1), kind
```

`sheetButtonText`'s cut: if the value is longer than `max`, take `value.slice(0, max + 1)` and cut it at its last space, then add "…". If it has no space, cut at `max`. The test's value cuts to "came up two feet" (16).

- [ ] **Step 4: Run them to see them fail**

Run: `PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest tests/test_viewer_js.py -q -k "TabletLayoutGate or SheetButtonText or NextBlankOther or EndLineText or TabletBoxes"`
Expected: FAIL. The names are not defined, and `TABLET_QUERY` is not in the stylesheet.

- [ ] **Step 5: Implement**

**`constants.mjs`:**
- `TABLET_QUERY` goes after `WIDE_QUERY`, with a comment in the style of `WIDE_QUERY`'s: the portrait iPad layout, spec 2026-10-16; one string for React and the stylesheet.
- The two boxes go after `SPEEDBOX`.

**`core/tablet.mjs`:**
- It holds the four functions, each with a one-line comment saying where it is used.
- It imports nothing from the DOM.
- `endLineText` builds its text from `summary`:
  - `End ${n}`;
  - then ` · ${name(hammer)} has hammer`, plus ` (from the placement)` when `hammerSource === "placement"` (the same rule as `EndBox`);
  - then ` · power play, ${color}, ${side}` when `powerPlay`.

**`runtime/tablet.mjs`:** a copy of `runtime/wide.mjs` on `TABLET_QUERY`, with its own header comment.

**`style.css`:**
- Directly after the wide block's closing brace, add `@media (any-pointer: coarse) and (orientation: portrait) and (min-width: 700px) and (max-width: 1199px) {` with a comment line, and close it with `}`.
- Task 3 fills it.

- [ ] **Step 6: Run the tests to see them pass**

Run the Step 4 command. Expected: PASS. Then run `-k "WideLayout or BigDelivery"`; expected PASS, unchanged.

- [ ] **Step 7: Build and commit**

```bash
cd frontend && npm run build && cd ..
git add frontend/core/constants.mjs frontend/core/tablet.mjs frontend/core/index.mjs frontend/runtime/tablet.mjs \
        src/curling_score/viewer/style.css tests/test_viewer_js.py src/curling_score/viewer/app.js frontend/.buildstamp.json
git commit -m "viewer: the tablet gate and its helpers

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

(`runtime/tablet.mjs` is not imported yet, so the bundle may not change. Commit it only if `git status` shows it changed.)

---

### Task 2: `scripts/tabletcheck.mjs`, written to fail first

**Files:**
- Create: `scripts/tabletcheck.mjs`

**Interfaces:**
- Consumes: the ids and classes that Tasks 3–5 create:
  - `#nextRock`, `#houseOpen`, `#markCharted` (on the house card), `#lineOpen`;
  - `#missBtn`, `#noteBtn`, `#orderBtn`, `#swapBtn`;
  - `#missSheet`, `#noteSheet`, `#orderSheet`, `#swapSheet`;
  - `.reasons button` (the miss reasons);
  - `body[data-house="edit"]`, `#menuBtn`, `body[data-menu="open"]`, `#leadin`;
  - `#deliveryDialog`, `#thinkingDialog`, `.dd-esc`.
- Produces: `CDP_PORT=… node scripts/tabletcheck.mjs EDIT VIEW REVIEW [--only=…] [--shots=DIR] [--blanks=EDIT_URL] [--doubles=EDIT_URL]`.
  - It prints `FAIL <what>` for each failure and `tabletcheck: all good` at the end; it exits 1 on any failure.
  - `--blanks` and `--doubles` are the edit URLs of separate devserve runs on the blanks and doubles timelines, without a hash. The script adds the hash.

Model it on `scripts/deskcheck.mjs`: the same `check(name, fn)`, `waitFor`, `box`, `page`, `isOpen` and `--shots` helpers.
- **Emulation:** every page opens with `Emulation.setDeviceMetricsOverride({width, height, deviceScaleFactor: 1, mobile: true})` and `Emulation.setTouchEmulationEnabled({enabled: true, maxTouchPoints: 5})`.
- **On exit:** it always resets both (touch off, `Emulation.clearDeviceMetricsOverride`). The same Chrome then serves deskcheck and domsnap.
- **Taps:** a tap is an `Input.dispatchTouchEvent` touchStart/touchEnd pair at the element's centre. Synthesized tap gestures give no click in headless Chrome (see `headless-phone-check`).
- **Typing:** `Input.insertText`.

`SIZES = [[820, 1080], [744, 1050], [834, 1110], [1024, 1290]]`. The sections, by name:

- **fit:** each size × the three surfaces at `#e=2&s=4`, and also `/c/` at `#e=3&s=3`.
  - No vertical or horizontal scroll.
  - `#video`, `#transport`, `#houseCard`, `#figCard` and (not on `/g/`) `#grading` all end at or above `innerHeight`, and no two of their boxes overlap.
  - The video's width/height is 16/9 ± 2%, and its width is ≥ 560.
  - At `#e=3&s=3`, `#lineOpen` is absent. At `#e=2&s=4`, it is present on all three surfaces.
  - Print the house's drawn size at each size, as deskcheck does.
- **targets:** at 820×1080 and 744×1050 on `/c/`. Every visible `button, select, input, textarea, a, .shot, label.chk` outside a closed `dialog` must be ≥ 40 tall and ≥ 36 wide. The `#video` subtree is skipped. Name the offenders by id or text.
- **menu:** on `/c/` at 820×1080:
  - tap `#menuBtn`, and `body[data-menu="open"]`;
  - `#game`, `#copyLink`, `#flagBtn` and `#link` are visible;
  - `#end`, `#thinkBtn` and `#reportBtn` are visible and lie in `header`'s box;
  - tap `#leadin`, and the dropdown is still open;
  - tap `#menuBtn` again, and it closes.
- **next:** on `/c/` at `#e=1&s=16`:
  - tap `#nextRock`, and the hash says `e=2&s=1`;
  - at `#e=5&s=16`, `#nextRock` is disabled.
- **sheets:** on `/c/` at `#e=2&s=4`:
  - Tap `#missBtn`, and `#missSheet[open]` with its top ≤ 120 px.
  - Dispatch `ArrowRight`, and the hash is unchanged.
  - Tap the first `.reasons button`. Close with its `Done`. After 300 ms, `#missBtn`'s text starts with `Miss: `.
  - Tap `#noteBtn`, tap `#note`, insert `ok`, close, wait 300 ms: `#noteBtn` reads `Note: ok`.
  - Tap `#orderBtn`, and `#orderSheet #moveBefore` is visible.
  - On `/s/`: `#missBtn` opens a sheet whose `#missReason` is read-only, and `#orderBtn` is absent.
- **rotate:** on `/c/` at 820×1080:
  - open `#noteSheet`, insert `turned`, wait 300 ms;
  - switch to 1080×820, still with touch;
  - wait for `#chart`. `#note`'s value is `turned`, and `body` has no sheet open.
- **house:** on `/c/` at 820×1080:
  - Tap `#house`, and `body[data-house="edit"]`.
  - The open `#house` is at least 1.8× the card's height, measured before the tap.
  - `#pickRed` and `#houseDone` are visible.
  - Set the height to 1010 (Safari's bar), wait 400 ms, and it is still open.
  - Tap `#houseDone`, and it closes.
  - On `/s/`: tapping `#house` does nothing, and `#houseOpen` and `#markCharted` are absent.
- **blanks** (needs `--blanks`): `/c/` at `#e=2&s=5`.
  - fit as above: no overlap, video ≥ 560.
  - Tap `#house`, then `#markCharted`. The hash says `e=2&s=6`, and the editor is still open.
  - Tap `#markCharted` again. The editor closes, and the hash stays `e=2&s=6`.
- **doubles** (needs `--doubles`): `/c/` and `/s/` at `#e=2&s=3`.
  - fit as above.
  - `#endBox` is at most 56 px tall.
  - On `/c/`, `#swapBtn` opens `#swapSheet`, with two checkboxes.
- **dialogs:** on `/c/` at 820×1080 and 744×1050:
  - Tap `#lineOpen`, and `#deliveryDialog[open]`.
  - Every `text` in its `svg`s is rendered at ≥ 11 px: the font-size attribute × the svg's width / its viewBox width.
  - `.dd-esc` reads `Tap outside to close`.
  - A tap at (8, innerHeight − 8), on the backdrop, closes it.
  - Tap `#thinkBtn`, and `#thinkingDialog[open]`. Tap its first bar (found as deskcheck's `thinking` section finds it), and it closes and the hash changes.
- **unchanged:** `#figCard` absent, and `#detailCard` and `#chart` present, at:
  - 1180×820 with touch;
  - 820×1080 without touch (`mobile: false`, touch off);
  - 660×1000 with touch.

- [ ] **Step 1: Write the script**

Keep each section's assertions exactly as listed. A section that throws records one `FAIL <section>: <message>` and the run goes on.

- [ ] **Step 2: Run it against today's build to see it fail**

Start devserve on the fours timeline and Chrome on port 9371, as in Task 1. Then run `CDP_PORT=9371 node scripts/tabletcheck.mjs EDIT VIEW REVIEW`.

Expected: `unchanged` passes. `fit`, `targets`, `menu`, `next`, `sheets`, `rotate`, `house` and `dialogs` fail on missing elements and on scroll. Paste the FAIL lines into the commit message body.

- [ ] **Step 3: Commit**

```bash
git add scripts/tabletcheck.mjs
git commit -m "scripts: tabletcheck, the portrait iPad layout's checks (failing until the layout lands)

<the FAIL lines>

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: The portrait page

**Files:**
- Create: `frontend/viewer/Sheet.jsx`
- Modify:
  - `frontend/viewer/App.jsx`
  - `frontend/viewer/ChartPanel.jsx` (`GradingBar`)
  - `frontend/viewer/Side.jsx` (`FiguresCard`)
  - `frontend/viewer/Detail.jsx` (`Figures` takes `children`)
  - `frontend/viewer/House.jsx` (a tap opens the editor on the tablet too)
  - `src/curling_score/viewer/style.css`
- Test: `tests/test_viewer_js.py` (`TestTheWideLayoutKeepsToItsBranch` generalised; new `TestTheTabletBranch`)

**Interfaces:**
- Consumes (Task 1): `TABLET_QUERY`, `runtime/tablet.mjs`, `sheetButtonText`, `stepRock` (already in `core/line.mjs`, already tested: it crosses ends and gives null after the last rock).
- Produces:
  - `Sheet({ id, title, open, onClose, children })` in `Sheet.jsx`: a native `<dialog>` opened with `showModal()` when `open`. It closes on Esc, on a backdrop tap (`e.target === e.currentTarget`, as `DeliveryDialog`) and on a `Done` button (`<form method="dialog">`). Content goes in a `.sheet-body` wrapper, so only the backdrop closes it. Its `onClose` is the dialog's `close` event.
  - `GradingBar` gains props `sheets: boolean` and `next: ReactNode | null`.
  - `FiguresCard` gains `onLine: (() => void) | null`.
  - App: `const tablet = useSyncExternalStore(tabletStore.subscribe, tabletStore.isTablet); const desk = wide || tablet;`.
  - Ids: `#nextRock`, `#houseOpen`, `#lineOpen`, `#missBtn`, `#noteBtn`, `#orderBtn`, `#missSheet`, `#noteSheet` and `#orderSheet`; the miss reasons sit in `#missSheet .reasons`.

- [ ] **Step 1: Write the failing source pins**

**Generalise `TestTheWideLayoutKeepsToItsBranch.side()`.** It returns the flag and the side of the nearest enclosing `{flag ? … : …}`, where flag is `wide`, `desk` or `tablet`: for example `"desk"`, `"!desk"` or `"tablet"`. Nested ternaries must resolve to the innermost one.

**The first test becomes:**

```python
    def test_each_piece_is_in_its_layout_s_branch(self):
        app = (self.ROOT / "frontend/viewer/App.jsx").read_text()
        for needle, want in (("<GradingBar ", "desk"), ("<FiguresCard ", "desk"), ("<LineCard ", "wide"),
                             ("<DeliveryDialog ", "desk"), ("<ThinkingDialog ", "desk"),
                             ("<ChartPanel ", "!desk"), ('id="detailCard"', "!desk"),
                             ('id="houseOpen"', "tablet"), ('id="nextRock"', "tablet")):
            assert app.count(needle) == 1, needle
            assert self.side(app, needle) == want, needle
        assert self.side(app, "<FlagDialog ") is None
```

**Add:**

```python
class TestTheTabletBranch:
    ROOT = Path(__file__).resolve().parents[1]

    def test_the_resize_handler_knows_the_tablet_has_an_editor(self):
        app = (self.ROOT / "frontend/viewer/App.jsx").read_text()
        assert "if (!phone() && !tabletNow() && (ui.houseMode || ui.sheet !== \"peek\"))" in app

    def test_a_tap_opens_the_editor_on_the_tablet(self):
        src = (self.ROOT / "frontend/viewer/House.jsx").read_text()
        assert "if ((phone() || tabletNow()) && houseMode !== \"edit\")" in src

    def test_the_bar_s_sheets_keep_the_typed_fields(self):
        src = (self.ROOT / "frontend/viewer/ChartPanel.jsx").read_text()
        bar = src[src.index("export function GradingBar("):src.index("function Detail(")]
        for needle in ('id="missSheet"', 'id="noteSheet"', 'id="orderSheet"', "{...miss}", "{...note}",
                       "miss.onChange({ target: { value: r } })"):
            assert needle in bar, needle
```

The resize pin belongs to Task 4's behaviour. It is written here so Task 3's branch test file has it, and Task 3 makes it pass, because the tablet's editor already exists after this task.

- [ ] **Step 2: Run them to see them fail**

Run: `… -m pytest tests/test_viewer_js.py -q -k "KeepsToItsBranch or TabletBranch"`. Expected: FAIL.

- [ ] **Step 3: `Sheet.jsx`, `Figures` children, `FiguresCard`'s line cell**

**`Sheet.jsx`:** the component above, with a header comment.
- The sheet's head reads its `title` (an `h2`) and a `Done` button.
- It has an `aria-labelledby` pointing at the `h2`, whose id is `${id}Title`.

**`Figures({ figures, children })`:** renders `children` as the last item inside the `<dl>`.

**`FiguresCard`:**
- When `onLine` is given and the rock has a delivery, it passes as that child:

  ```jsx
  <div className="dfig dline"><dt className="sr">The line</dt><dd><button type="button" id="lineOpen" onClick={onLine}>…</button></dd></div>
  ```

  The button holds the mock's trend icon (`touch.js`'s inline svg, `aria-hidden`) and `Line & speed`.
- "Has a delivery" is `!lineChartGeometry(shot, doc, LINEBOX).why`, the same test `LineCard` uses to offer Expand.
- With `onLine`, the card leaves out its `p.dcap` caption.

- [ ] **Step 4: `GradingBar` with sheets**

With `sheets` false, nothing changes: same markup, byte for byte.

With `sheets` true, the second `.gbar-row` holds:
1. the Score pair;
2. `<button id="missBtn">` with `sheetButtonText("Miss", miss.value)`;
3. `<button id="noteBtn">` with `sheetButtonText("Note", note.value)`;
4. when `!readOnly`, `<button id="orderBtn">` with `sheetButtonText("Order", before ? `#${before.number}` : null)`. `before` is found as `Grading` finds it;
5. a `.mk-grow` spacer;
6. `{next}`.

**The three sheets,** each a `Sheet`, rendered after the rows and before `#renumbered`:
- **`#missSheet`, title "Miss reason":**
  - a `div.reasons` with one button per `MISS_REASONS` entry, each calling `miss.onChange({ target: { value: r } })` (disabled when `readOnly`);
  - then the `#missReason` input with `{...miss}` and `readOnly={readOnly}`, and `<MissList />`.
- **`#noteSheet`, title "Note":** the `#note` textarea, `rows={4}`, with `{...note}` and `readOnly={readOnly}`.
- **`#orderSheet`, title "Order"** (only when `!readOnly`): `<MoveBefore …/>`, then the Order explanation as a `.muted` paragraph. That is the same text as `Grading`'s.

Which sheet is open is local state: `useState(null)`, `"miss" | "note" | "order"`. Opening one closes any other; `onClose` sets null. The inline `.gbar-pair`s for Miss, Note and Order are not rendered with `sheets`.

- [ ] **Step 5: App.jsx and House.jsx**

**Module level:** `const tabletNow = () => matchMedia(TABLET_QUERY).matches;` beside `phone`. Import `TABLET_QUERY`, `stepRock` (already imported) and `* as tabletStore`.

**State:** `tablet` and `desk`, as in the Interfaces. The "narrowing unmounts" effect becomes `if (!desk && wideOpen)`.

**The resize handler:** `if (!phone() && !tabletNow() && (ui.houseMode || ui.sheet !== "peek"))`.

**`#playCard`:** the `{wide ? (…) : (…)}` becomes `{desk ? (…) : (…)}`.
- The narrow branch is unchanged.
- The desk branch is:
  - `{tablet ? <div className="row transport" id="transport">{shotChips}{replayBtn}</div> : <today's wide transport row>}`;
  - then `{tablet ? playback : null}`;
  - then `<GradingBar … sheets={tablet} next={tablet ? nextRockBtn : null} />`.

  The tablet's `#transport` holds neither `#markCharted` nor the arrows.
- `nextRockBtn`:

  ```jsx
  <button id="nextRock" type="button" disabled={!stepRock(view, ui.ei, ui.si, 1)} onClick={() => actions.step(1)}>Next rock →</button>
  ```

**`#detailCard`:** mounted under `!desk`.

**The house card:**
- In a `{tablet ? (…) : null}` placed first inside the section, a `div.hc-head` holding:
  - `markBtn` (moved here on the tablet; see below);
  - `<span className="hc-hint">Drag a stone · tap the ice to add one</span>`, shown by CSS only in the editor;
  - `<button id="houseOpen" type="button" hidden={config.readOnly} aria-label="Open the house large" onClick={actions.openHouseEditor}>` with the mock's expand svg.
- After `.housebar`:
  - on the tablet, `<p className="hc-tap" hidden={config.readOnly}>Tap the house to open it large and move stones.</p>`;
  - otherwise today's hint `div`, now under `!desk`.
- The `title` spread stays `wide`.
- **`markBtn`** is defined once. Its label is `✔ Charted` on the tablet and `✔ Mark charted (Enter)` otherwise. It is rendered in the wide transport row when `wide`, and in `.hc-head` when `tablet`. Its handler is unchanged in this task.

**The side cards:**
- `{desk ? <>…</> : <ChartPanel…/>}`.
- `FiguresCard` gets `onLine={tablet ? openDelivery : null}`.
- `openDelivery` is the `onExpand` body, lifted out so that `LineCard` and `FiguresCard` share it.
- `LineCard` sits inside `{wide ? … : null}`.

**The dialogs:** `DeliveryDialog` and `ThinkingDialog` render under `desk`.

**Header:**
- It takes `desk`, `tablet` and `ytLink`.
- `#rockName` and `#thinkBtn` go from `wide` to `desk`.
- `endSel`, `thinkBtn` and `reportBtn` are built once:
  - when `tablet`, they are rendered just before `#menuBtn`, outside `#menu`, and `{ytLink}` is rendered last inside `#menu`;
  - otherwise they stay where they are, so the DOM is unchanged.
- The view-only track toggle (spec §4) is a `label.chk` with `#trackMenu`, inside `#menu`, when `tablet && config.readOnly`. It is wired to `ui.showTrack` / `setPref` through two more props.

**House.jsx:**
- `if ((phone() || tabletNow()) && houseMode !== "edit")`.
- `tabletNow` is defined there the same way.

- [ ] **Step 6: The stylesheet**

**First, a move with no effect.** Move the wide block's rules that name only elements rendered in the desk layouts out of it, into a new unscoped section just before it, headed `/* --- the desk layouts' parts (wide and tablet) --- */`:
- `.gbar*`;
- `#figCard*`;
- `#rockName .chip*`, `#rockName .rn-label`;
- `#playGear`, `#playPrefs*`;
- `#lineCard*`.

Rules on shared elements stay in the wide block: `main`, `body`, `#video`, `header…`, `header > #rockName`, `#houseCard`, `#house` and `#playCard .transport…`. This changes no computed style anywhere: those elements exist only in the desk layouts. Step 9's domsnap and deskcheck prove it.

**Then fill the tablet block,** ported from the mock (`touch.js` and `P.js`), adapted to the real DOM and these values:

| Element | Rules |
|---|---|
| `main` | grid, columns `minmax(0,1fr) minmax(0,1fr)`; areas `"video video" "tp tp" "house figs" "bar bar"`; rows `minmax(315px, calc((100vw - 28px) * 9 / 16)) auto minmax(auto, 1fr) auto`; `gap: 14px; padding: 14px; box-sizing: border-box; height: calc(100dvh - 56px)` |
| `#playCard` | `display: contents` |
| `#video` | area `video`; `height: 100%; width: auto; max-width: 100%; aspect-ratio: 16/9; justify-self: center` |
| header | `height: 56px; flex-wrap: nowrap`; `#src` and the non-failing `#save` hidden; `#menuBtn` 44×44, shown, last; `#rockName` gives way first (as wide) |
| dropdown | `body[data-menu="open"] #menu`: fixed under the header at the right, `width: 300px`, a column of 44 px items; `#playback` as a fixed panel above it. It is the phone's pattern (fixed offsets); `#playback` is a sibling, so tapping its box doesn't close the menu |
| chips | `height: 44px`; `width: clamp(36px, calc((100vw - 160px) / 16), 40px)`; `.graded::after` 6 px; `#replay` 44 tall |
| bar (`#grading`) | its own card in area `bar`; type buttons 44 tall, the groups' `min-width: 70px`; the subtypes `flex: 1 0 100%`; `#autoType` beside the groups at the right; score buttons `50px × 48px`, `font-size: 20px`; sheet buttons 44 tall, `max-width: 180px` with ellipsis; `#nextRock` 48 tall, filled (`--ink` on `--panel`) |
| house card | area `house`, `position: relative`, `contain: size; min-height: 320px` (its drawing must not size the row), a flex column with `#house` `flex: 1 1 0; min-height: 0`; `.tools` and `.housebar` hidden (outside the editor); `.hc-head` overlaid at the top (`#markCharted` left, `#houseOpen` right, each 44); `.hc-hint` hidden; `.hc-tap` 12.5 px muted |
| figures | area `figs`; `.dnote` `white-space: normal`, never clipped; `.dline` centred; `#lineOpen` 44 tall, full cell width |
| everything | `button, select, a, .shot { touch-action: manipulation; }` |
| sheets | `dialog.sheet`: `margin: 70px auto auto`, `width: min(560px, calc(100vw - 32px))`; `.reasons` a wrap of 44 px buttons; textarea at least 120 px tall |

The sheet rules are not tablet-only: a `Sheet` is only ever rendered on the tablet. Put them beside the other dialogs' rules.

**How the rows share the height** (spec §5: the video gives way first):
- The grid first gives the house row its 320 px floor, or the figures card's height if that is more (warnings), and the video row its 315 px floor (560 wide).
- Free space then grows the video row, up to the column's full 16:9 height.
- The house row takes what is left.
- At 820×1080 that is a 412 px video (732 wide) and a 320 px house row: the board.
- When the floors don't fit, the grid overflows `main` and the page scrolls. The cards never overlap.

- [ ] **Step 7: Build, run the pins and the node subset**

Run `cd frontend && npm run build`. Expected: no eslint errors.

Then run `… -k "KeepsToItsBranch or TabletBranch or WideLayout or TheDeliveryDialog or TheThinkingDialog"`. Expected: PASS.

- [ ] **Step 8: Run tabletcheck**

On fresh devserve and Chrome (fours timeline): `CDP_PORT=9371 node scripts/tabletcheck.mjs EDIT VIEW REVIEW --only=fit,targets,menu,next,sheets,rotate,unchanged --shots=out/ipad-check/t3`.

Expected: those sections pass. `house` already opens the editor, but the editor's look is Task 4. If the 820×1080 video is more than 20 px off the board's 732×412, check the bar's height first.

Look at `out/ipad-check/t3/*.png` against the "P · Portrait" board.

- [ ] **Step 9: Prove the rest is unchanged**

Run `CDP_PORT=9371 node scripts/domsnap.mjs EDIT VIEW REVIEW --out out/ipad-check/domsnap-t3.json`, then `node scripts/domsnap.mjs --diff out/ipad-check/domsnap-base.json out/ipad-check/domsnap-t3.json`.

Expected: 17 identical. Use a fresh devserve, with no hash, as the base was taken.

Then run deskcheck the way its header describes, at `#e=2&s=4`. Expected: `deskcheck: all good`.

- [ ] **Step 10: Commit**

```bash
git add frontend/viewer/Sheet.jsx frontend/viewer/App.jsx frontend/viewer/ChartPanel.jsx frontend/viewer/Side.jsx \
        frontend/viewer/Detail.jsx frontend/viewer/House.jsx src/curling_score/viewer/style.css tests/test_viewer_js.py \
        src/curling_score/viewer/app.js frontend/.buildstamp.json
git commit -m "viewer: the portrait iPad layout -- the page, the bar and its sheets

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: The house opened large

**Files:**
- Modify: `frontend/viewer/App.jsx` (the tablet `markBtn` handler)
- Modify: `src/curling_score/viewer/style.css` (the tablet block: `body[data-house="edit"]`)

**Interfaces:**
- Consumes:
  - `nextBlankOther` (Task 1);
  - `.hc-head`, `.hc-hint`, `.tools`, `.housebar` and `#houseDone` (Task 3);
  - `houseMode: "edit"` and the phone's editor state.

- [ ] **Step 1: Run the house and blanks checks to see them fail**

Start a second devserve on the blanks timeline. Then run `CDP_PORT=9371 node scripts/tabletcheck.mjs EDIT VIEW REVIEW --only=house,blanks --blanks=BLANKS_EDIT_URL`.

Expected: FAIL. The editor's house is not 1.8× larger, and `#markCharted` on the last blank does not close the editor.

- [ ] **Step 2: The editor's layout**

In the tablet block, `body[data-house="edit"] #houseCard`:
- placed over the rows under the video: `grid-row: 2 / 5; grid-column: 1 / 3; z-index: 2`. No fixed positioning, so nothing has to know the video's height;
- `body[data-house="edit"] :is(#transport, #figCard, #grading) { visibility: hidden; }`. Hidden, not removed, so the rows keep their sizes and the video does not change size when the house opens;
- the card itself a grid, `grid-template-columns: auto minmax(0,1fr)`, areas `"svg head" "svg tools" "svg bar"`, rows `auto 1fr auto`;
- a solid `--panel` background and a shadow, as the board's.

Inside it:
- **`#house`:** area `svg`, `height: 100%`, `width: auto`; its viewBox's aspect sets the width.
- **`.hc-head`:** area `head`, a column: `#markCharted` first, then `.hc-hint` (shown, 13 px muted). `#houseOpen` is hidden.
- **`.tools`:** area `tools`, shown, a wrap of 44 px controls.
- **`.housebar`:** area `bar`, shown, `#recolour` 56 wide and `#houseDone` filling the rest, both 48 tall.
- **`.hc-tap`:** hidden.

The video stays visible and is never covered. The chips row and the bar are covered.

- [ ] **Step 3: ✔ Charted on the tablet**

On the tablet only (`tablet` true), `markBtn`'s `onClick`:
1. `patch({ state_known: true })`;
2. `const it = nextBlankOther(queue, ui.ei, ui.si)`;
3. if `it`, `goTo(it.ei, it.si)`. The reducer's `goTo` keeps `houseMode`, so the editor stays open on the rock it jumps to;
4. else if `ui.houseMode === "edit"`, dispatch `houseMode: ""`.

The wide handler and the Enter key are unchanged. They keep `nextBlankAfter`, as on the desktop today.

- [ ] **Step 4: Build and run**

Run `npm run build`. Then `tabletcheck --only=house,blanks,fit --blanks=…`. Expected: PASS.

Look at the editor's screenshot against the "A · the house opened large" board. Here it opens under the video instead, with the tools in a column at the right.

- [ ] **Step 5: Commit**

```bash
git add frontend/viewer/App.jsx src/curling_score/viewer/style.css src/curling_score/viewer/app.js frontend/.buildstamp.json
git commit -m "viewer: the iPad's house opened large under the video; Charted closes it on the last blank

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Doubles, and the large dialogs on the tablet

**Files:**
- Modify: `frontend/viewer/ChartPanel.jsx` (`EndBox` takes `compact`)
- Modify: `frontend/viewer/DeliveryDialog.jsx` (`box`, `speedBox`, `hint` props)
- Modify: `frontend/viewer/App.jsx` (passes them)
- Modify: `src/curling_score/viewer/style.css`
- Test: `tests/test_viewer_js.py` (the `TestTheDeliveryDialog` pins)

**Interfaces:**
- Consumes:
  - `endLineText`, `swapText`, `sheetButtonText` and the boxes (Task 1);
  - `Sheet` (Task 3).
- Produces:
  - `EndBox({ view, cursor, config, actions, compact })`;
  - `DeliveryDialog({ delivering, onClose, box = BIGDELIVERYBOX, speedBox = SPEEDBOX, hint = "Esc or click outside to close" })`;
  - `#swapBtn` and `#swapSheet`.

- [ ] **Step 1: Update the dialog pins to fail**

In `TestTheDeliveryDialog.test_its_words_are_the_spec_s`, replace the three needles:
- `"Esc or click outside to close"`;
- `"deliveryGeometry(shot, BIGDELIVERYBOX)"`;
- `"speedGeometry(speed, SPEEDBOX, shot)"`.

The new needles are:
- `'hint = "Esc or click outside to close"'`;
- `"box = BIGDELIVERYBOX"`;
- `"speedBox = SPEEDBOX"`;
- `"deliveryGeometry(shot, box)"`;
- `"speedGeometry(speed, speedBox, shot)"`.

Add a needle for App.jsx: `'hint="Tap outside to close"'`. Run `-k TheDeliveryDialog`. Expected: FAIL.

- [ ] **Step 2: The dialog takes its boxes**

**`DeliveryDialog`:** passes `box`, `speedBox` and `hint` to `Large`, which uses them in place of the constants and the `.dd-esc` text.

**App:** passes, when `tablet`:
- `box={TABLETDELIVERYBOX}`;
- `speedBox={TABLETSPEEDBOX}`;
- `hint="Tap outside to close"`.

**The tablet block:**
- `#deliveryDialog { width: calc(100vw - 24px); }`;
- `.dd-body { padding: 14px 16px 16px; }`;
- `.dd-close { width: 44px; height: 44px; }`;
- the Thinking dialog's close button and per rock / totals switch at 44 px.

- [ ] **Step 3: The compact end box**

With `compact`, `EndBox` renders `#endBox` holding:
- `<p className="endline">{endLineText(endSummary(view, cursor.ei), at?.end.number, name)}</p>`;
- when `!config.readOnly`:
  - `<button id="swapBtn" type="button">{sheetButtonText("Swapped roles", swapText(at?.swapped || {}, name), "Swapped")}</button>`;
  - and `<Sheet id="swapSheet" title="Swapped roles">` with today's two checkbox labels, unchanged. Whether it is open is `EndBox`'s local state.

Without `compact`, it is unchanged.

App passes `compact={tablet}`.

The tablet block: `#figCard #endBox { display: flex; align-items: center; gap: 8px; margin: 0 0 8px; }`, with `.endline` taking the rest and ellipsising. The full end facts are in the swap sheet's title context; on `/s/` the line alone is fine.

- [ ] **Step 4: Build and run**

Run `npm run build`, then `-k "TheDeliveryDialog or TheThinkingDialog or KeepsToItsBranch"`. Expected: PASS.

Start a third devserve on the doubles timeline. Then run `tabletcheck --only=doubles,dialogs,fit --doubles=DOUBLES_EDIT_URL`. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/viewer/ChartPanel.jsx frontend/viewer/DeliveryDialog.jsx frontend/viewer/App.jsx \
        src/curling_score/viewer/style.css tests/test_viewer_js.py src/curling_score/viewer/app.js frontend/.buildstamp.json
git commit -m "viewer: the iPad's doubles end box in one line, and the large delivery drawn for its width

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Whole-branch verification

**Files:** none new. Fixes go to whichever file a check names, each with its own commit.

- [ ] **Step 1: The node tests**

Run, as separate commands:
- `tests/test_viewer_js.py`, in its `-k` thirds if it OOMs;
- `tests/test_frontend_build.py`.

Expected: PASS. Deselect `TestAgainstHandMarkedCrossings` if it is collected: it fails on main already.

- [ ] **Step 2: tabletcheck, all sections**

Use fresh devserves for the fours, blanks and doubles timelines, and run: `tabletcheck EDIT VIEW REVIEW --blanks=… --doubles=… --shots=out/ipad-check/final`.

Expected: `tabletcheck: all good`. Save the output to `out/ipad-check/tabletcheck-final.txt`.

- [ ] **Step 3: Nothing else moved**

- domsnap against `domsnap-base.json`, on a fresh devserve with no hash: 17 identical.
- `deskcheck` on the fours, with `--blank` on the blanks timeline's `#e=2&s=5` and `#e=2&s=6`: `deskcheck: all good`.

Save both outputs to `out/ipad-check/`.

- [ ] **Step 4: Screenshots for the user's iPad check**

From `out/ipad-check/final/`, keep:
- `/c/` at 820×1080 and 744×1050;
- the open house;
- the Miss sheet;
- the delivery dialog;
- a doubles rock.

List what only a real iPad can show, from the spec's Testing section:
- `100dvh` with Safari's bars;
- YouTube's first play;
- the keyboard over a sheet;
- a backdrop tap;
- turning mid-rock.

- [ ] **Step 5: Report**

Write `out/ipad-check/final-report.md`:
- each check, its command and its result;
- every deviation from this plan, and why;
- the processes started and stopped.

Commit nothing in `out/` (it is gitignored).
