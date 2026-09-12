# A phone layout for the charting viewer

**Status:** design approved, ready for an implementation plan.
**Revised** after `5ba4858` (rock order) — see "Rock order" below.
**Wireframes:** [Curling Chart on a Phone](https://claude.ai/code/artifact/db3ba9fa-0e55-46cd-ba53-2ee61c054445)
(page "Phone layout"; the rejected candidates are on page "Not chosen").

## Goal

One page that reorganises itself into a usable charting tool on a phone —
responsive CSS, not a second app and not a redirect. The desktop layout is
unchanged at every width it serves today.

The user is reviewing a VOD away from the rink, in long sittings. The phone
must therefore do the whole job, not a subset: all thirteen keyboard shortcuts
need touch equivalents, and a blank shot must be chartable.

## The constraint everything follows from

At 390 pt the content column is ~358 px. The house SVG is
`viewBox="-2.6 -2.6 5.2 8.6"`, so at full width it is 592 px tall; the 16:9
video is 201 px in a padded column, 219 px full-bleed. Together that exceeds
the ~740 px of usable viewport before a single control is drawn. **The video
and the house cannot both be full size.** Every decision below is a
consequence.

A second measurement: a stone is `R.stone = 0.142 m`. At any phone width it
renders 19–21 px across, well under the 44 pt touch minimum. Reaching 44 px
would need ~2.5× zoom, which leaves less than the sheet's width visible. We
accept the imprecision rather than build a gesture layer, because the tracking
usually gets the house right and editing is the rare act.

## Chosen design

Video pinned at the top, house cropped to the scoring area beneath it, grading
in a bottom sheet whose **peek** state holds the whole fast path — shot-type
group, grade, next. Expanding the sheet reveals the full form. Editing the
house is a **full-screen mode** you enter deliberately, and is also where a
`STATE UNKNOWN` blank gets charted.

The ranking is the point: grading is frequent, house editing is rare, so
grading owns the thumb zone and editing is a mode.

The peek's content depends on the shot's state — see "Rock order" below.

Vertical budget at 390×844:

| band | height | notes |
|---|---|---|
| header | 48 | identity, status, and rare actions only |
| video | 219 | full-bleed 16:9, pinned |
| house | 321 | cropped to the scoring area; guards off-screen |
| sheet (peek) | 256 | group chips, grade row, transport bar |

The sheet's rounded top overlaps the house by 16 px, so the visible house band
is 337. The crop is **derived from that height, not hardcoded** — which is why
`houseViewBox()` below is a function rather than three literals.

Expanding the sheet grows it over the house; the video never moves.

## Rock order

`5ba4858` added `#orderBox`: a correction may carry `before: N`, and
`layout()` then reorders the end, renumbers it, recomputes thrower, label and
blank colour, and keeps grades filed under the original number via
`identity(s) = s.id ?? s.number`. It is the newest and least forgiving part of
charting, and the first wireframes predated it. Two things were wrong.

**It did not fit.** The expanded sheet was already at 564 of 577 px. The Order
block adds ~64 more. So: the sheet body scrolls with the transport pinned, and
Order collapses to a **44 px disclosure row** showing its current state
(`detected order`, or `before #3 (red)`) that opens the picker on tap. Rare for
a real shot, so collapsing is right. On desktop `#orderBox` stays expanded
exactly as today.

**A blank's fast path is not grading.** You cannot usefully grade a rock nobody
saw, and `isBlank(s)` already distinguishes them. So the peek is
**shot-state dependent**:

| peek, normal shot | peek, `isBlank(s)` |
|---|---|
| shot-type group chips | the order picker, promoted and expanded |
| grade row `0`–`4` | "Place the stones" → house-edit mode |
| transport bar | transport bar |

That is the flow the commit describes: find the blank, say where it was really
thrown, then say what was on the ice.

**Keep the native `<select>`.** iOS and Android render it as a full-width
picker, which beats any custom dropdown we would write, and `render()` already
fills it with fifteen well-labelled options. It only needs to grow to 44 px.

### The renumber announces itself

On desktop the `#shots` chip strip sits under the video, so a reorder visibly
rewrites sixteen labels. Candidate C deliberately has no strip — the header
stone chip replaced it — so the only feedback is that chip changing from `9`
to `3`. That is very thin for an operation that renumbers the whole end, and
`moveBefore.onchange` also moves `state.si` underneath the user.

**Decided: a transient toast, not a chip strip.** A permanent strip costs
46 px on every screen to explain an occasional action, which is the wrong
trade. Instead `#renumbered` appears after a reorder, says what happened, and
leaves.

- **It floats over the house**, absolutely positioned just above the sheet, so
  it costs no layout at all and nothing reflows when it comes and goes.
- **It carries what the user could not otherwise know.** Moving a blank settles
  its *colour*, because `layout()` recolours blanks by the alternation around
  them. So the text is "End renumbered — this is now **rock 3**, red by
  alternation", not a bare "renumbered".
- **It exists in both layouts but is only visible on phone**, and carries
  `role="status"`. The desktop chip strip is a purely visual signal, so a
  screen-reader user gets nothing from it today; announcing the renumber is
  worth having at every width even though the toast is not drawn there.
- It auto-dismisses after ~5 s. It confirms an action the user just took, so
  auto-dismissal is safe — no decision depends on reading it.

Shown as the fourth artboard on the canvas.

## Architecture

**One DOM, CSS-led.** The phone layout is reached by media query over the
existing markup. Two rules make this non-negotiable rather than a preference:

1. **The video iframe must never be reparented.** `app.js` reuses one player
   for the whole session and the README promises navigation never reloads it.
   Moving `#video` in the DOM destroys and recreates the iframe. So the phone
   layout must be reachable by CSS positioning alone, never by JS reparenting.
2. Duplicating controls into a phone-only bar was rejected: two elements per
   action means two places to keep `disabled`, `hidden` and `.on` in sync, and
   `boot()` wires handlers by id.

### The one structural change to `index.html`

The four transport controls the phone bar needs — `#prev`, `#replay`,
`#markCharted`, `#next` — are currently split across two different sections.
CSS cannot gather them into one bar. So they move into a single container:

```html
<div class="transport" id="transport">
  <button id="prev">…</button>
  <button id="replay">…</button>
  <button id="markCharted">…</button>
  <button id="next">…</button>
</div>
```

On desktop `.transport` renders as today's inline row inside the play card. On
phone it becomes the fixed bottom bar inside the sheet. `#markCharted` moves
out of the chart `<aside>` to get there; nothing else in the DOM order changes,
and no handler in `boot()` changes.

### New elements

- `#stoneChip` in the header — the current stone as a 40 px rounded square in
  the team colour with the rock number, reusing the `.shot` chip vocabulary
  (7 px radius, tabular numerals). **Status, not a target**, which is why it is
  40 px rather than 44. Rendered by `render()`, hidden by CSS on desktop.

  The rule for the header is that *frequent* actions go to the thumb zone — not
  that nothing up there is tappable. Two rare ones stay: the `#blanks` pill (the
  touch equivalent of `n`, used a handful of times a session) and an overflow
  menu holding the game and end selects, `#copyLink`, `#shareLink`,
  `#download`, `#reportBtn`, and the lead-in and autoplay settings. Both cost a
  stretch, which is the right price for how rarely they are used.
- `#sheetHandle` — the drag affordance and the tap target that toggles the
  sheet. Needs a visible close control in the expanded state; swipe-to-dismiss
  alone is unreliable.
- `#houseDone` — leaves the full-screen house mode.
- `#orderRow` — the collapsed disclosure row that fronts `#moveBefore` on
  phone. `#moveBefore` itself is reused, not duplicated; the row is a label
  that reflects its value and forwards taps to it.

### New state

Two transient fields on `state`, neither persisted to prefs:

- `state.sheet` — `"peek" | "open"`, mirrored to `body.dataset.sheet`
- `state.houseMode` — `"" | "edit"`, mirrored to `body.dataset.house`

CSS keys off the dataset attributes exactly as `body[data-mode="view"]`
already does. Entering house-edit mode on a blank shot happens automatically
when the user taps the hatched house.

### The house viewBox becomes a function

Today the viewBox is a literal in `index.html`. Three crops are now needed
(phone peek, phone full-screen edit, desktop), so `drawHouse()` must set it:

```js
// exported, pure, and therefore the one part of this work with real coverage
function houseViewBox(mode) → "minX minY w h"
```

Desktop keeps `-2.6 -2.6 5.2 8.6` byte-for-byte, so nothing changes there.

## Every shortcut's touch equivalent

The Goal claims the phone does the whole job, so here is the proof. `onKey`
handles fourteen bindings:

| key | action | on a phone |
|---|---|---|
| `←` `→` | previous / next shot | transport bar |
| `Enter` | mark charted | transport bar, primary button |
| `v` | replay from before the throw | transport bar |
| `0`–`4` | grade | sheet, peek state |
| `n` | jump to the next blank | `#blanks` pill in the header |
| `p` | play / pause | tap the video — YouTube's own control |
| `r` `y` | stone colour to place | house-edit mode, colour swatches |
| `x` | delete selected stone | house-edit mode |
| `d` | mark selected as delivered | house-edit mode |
| `c` | recolour selected stone | house-edit mode — **no control exists yet** |
| `t` | track overlay | house-edit mode, toggle |
| `Escape` | close the Report | the Report's own close control |

Rock order has no shortcut at all, on any platform — it is mouse- or
touch-only in both layouts, which is consistent.

`c` is the one real gap: `toggleStoneColor()` is reachable only from the
keyboard today, so house-edit mode needs a new button for it. On desktop it can
stay keyboard-only, as now.

## Responsive rules

- **Breakpoint:** `@media (max-width: 640px)` enters phone mode. The existing
  1180 px and 820 px breakpoints are untouched.
- **Height, not `100vh`.** The shell is a fixed-height column, so it must use
  `100dvh`. `100vh` is wrong under a collapsing browser toolbar and would push
  the sheet off-screen.
- **Safe areas.** `index.html`'s viewport meta gains `viewport-fit=cover`, and
  the sheet's bottom padding becomes
  `calc(24px + env(safe-area-inset-bottom))` so the transport bar clears the
  home indicator.
- **Touch targets.** In phone mode `.shot` grows 30 → 44 px, `.swatchbtn`
  26 → 44 px, and `.pick button` and `.scorebtns button` grow to 44/48 px. The
  `#stoneChip` stays 40 px, as above.

### Landscape is the fallback, and that is what Candidate A was for

A phone in landscape is ~390 px tall. The pinned video (219) plus the peek
sheet (256) do not fit, and no amount of cropping saves it. Below
`(max-height: 520px)` the layout falls back to the plain scrolling stack —
one column, sticky transport bar, full-height house. It is worse to use but
it never clips, and it is nearly free because it is the 820 px single-column
layout plus a fixed bar.

## What is testable, and what is not

`tests/test_viewer_js.py` runs `app.js` in node with **no DOM**. Layout
therefore has no automated coverage, and this spec does not pretend otherwise.
What can be tested, and should be written test-first:

- `houseViewBox(mode)` — returns the desktop crop unchanged, and a phone crop
  whose aspect ratio matches the band it fills.
- The sheet/house mode transitions, if factored as pure functions over
  `state` rather than inline DOM writes.
- `peekMode(shot)` → `"grade" | "order"`, the shot-state branch that decides
  which peek a shot gets. Pure, one line, and the thing most likely to be got
  wrong for a shot that is both blank and already graded.
- `renumberNotice(before, after)` → the toast's text, or `null` when nothing
  moved. The `null` case is the one worth a test: picking "detected order" when
  the shot is already in detected order, or a `before` that lands it back where
  it started, must not announce a renumber that did not happen.

`layout()` and the reorder itself are already covered by
`tests/test_viewer_js.py` as of `5ba4858`; this work must not change them.

Everything else is verified by eye at 390×844, 430×932 and in landscape. Two
regressions are worth guarding by hand on every change: the player must not
reload when navigating shots, and a `STATE UNKNOWN` shot must stay hatched
rather than drawing as an empty house.

## Risks

- **The transport move touches desktop markup.** `#markCharted` leaves the
  `<aside>`. Its handler and keyboard binding (`Enter`) are unaffected, but
  the desktop chart panel loses a button from its flow and needs its spacing
  re-checked.
- **`touch-action: none` on `#house`** already exists for dragging. In phone
  mode the house sits inside a fixed shell that does not scroll, so this is
  safe — but if the fallback stack ever puts the house in a scroller, dragging
  and page-scroll will fight.
- **A reorder moves `state.si` under the user.** `moveBefore.onchange`
  follows the shot to its new slot deliberately. On a phone, where the chip
  strip is absent, that is a screen changing for reasons the user cannot see —
  which is the risk the transient confirmation exists to cover.
- **The sheet covers the house while expanded.** A user editing stone
  positions must collapse the sheet first. This is deliberate, and the
  full-screen house mode is the escape hatch.

## Out of scope

- The Report view and the view-only mode on phone. Both work well enough
  today as full-width single-column pages; neither was wireframed.
- Pinch-zoom on the house.
- A live-at-the-rink mode.
