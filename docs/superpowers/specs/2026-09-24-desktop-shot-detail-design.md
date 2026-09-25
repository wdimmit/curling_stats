# Shot detail on the desktop

**Status:** design approved section by section, 2026-09-24, and ready for an
implementation plan. Two changes since approval are marked: the two-column
layout (§1) and where the miss label sits (§2).

**Mockups:** [Shot Detail on the Desktop](https://claude.ai/artifact/LdbRUpSE7PGJ86EHAaUgR7).
Artboard A is the chosen design. B (a Detail tab on the house card) and C (the
strip beside the house, the figures under the video) were not chosen. All three
show the edit page at 1440 × 900 on end 1 rock 3 of s_0ZIRyB57JW0Q7dOu2.

## Context

The phone viewer's Detail pane shows whether a rock was thrown at the skip's
broom: the whole sheet as a strip, and six figures (see
`2026-09-24-shot-line-detail-design.md`). The desktop has none of it. Desktop
is for everyone equally: the charter on `/c/` and viewers on `/s/` and `/g/`.

At 1440 × 900 the space under the video (column 1, below the shot chips) is
about 700 × 280 px and empty on every surface. The house and chart columns are
full.

**Success:** step through rocks on a laptop and see, at a glance and without
scrolling, whether each one was thrown at the broom, next to the video and the
house.

## Scope

**In scope**
- A shot-detail card on the desktop layout of `/c/`, `/s/` and `/g/`.
- Three label changes in the chart panel's detail list (§2).

**Unchanged**
- Both phone layouts, and the phone Detail pane's drawing and figures.
- The house card, the chart panel's grading, and every keyboard shortcut.
- The pipeline and the timeline format. No new data.

## 1. Where it goes (approved)

- A new card, `#detailCard`, a direct child of `<main>` immediately after
  `#playCard`, on every surface. It is always mounted. CSS decides where it
  shows, the same rule the player follows (it must never be reparented).
- `<main>` gets named grid areas. Each card takes its area: `#playCard` play,
  `#detailCard` detail, `#houseCard` house, `#chart` chart.
  - **Above 1180 px (three columns, unchanged widths):**
    `"play house chart" "detail house chart"`, rows `auto 1fr`. The house and
    the chart panel run the full height of their columns.
  - **820–1180 px (two columns):** `"play house" "detail house" "chart house"`,
    rows `auto auto 1fr`. The details sit under the video, the chart panel under
    the details, and the house runs down column 2.
    **Changed since approval:** §1 was approved with the chart panel under the
    house. At these widths the house card is usually taller than the video card
    (at 900 px by about 160 px), so the details could not start until below the
    house, leaving a gap under the video. Keeping the chart panel in column 1,
    where it sits today, avoids it.
  - **820 px and below (one column):** `"play" "detail" "house" "chart"`.
  - **640 px and below (the phone layouts):** `grid-template-areas: none`, so
    both phone layouts see exactly the grid they see today, and `#detailCard`
    is `display: none`.
- **Fold.** Measured headless at 1440 × 900 and 1280 × 800 on end 1 rock 3 of
  s_0ZIRyB57JW0Q7dOu2 (`/c/`, schema 6): at 1440 × 900 the card box ends about
  885 px down, 15 px above the fold, on a confirmed rock, and about 900 px
  down on an unconfirmed one, whose broom note wraps to a second line. Either
  way the card's content through the caption (`#detailCard .dcap`) stays on
  screen -- 872 px and 887 px respectively -- and only the card's own bottom
  padding/border can fall past 900 px. At 1280 × 800 it ends about 877 px
  down, 77 px below the fold, with the strip and the first figure row still
  above it. The video is not capped to force any of this. A real 1440 × 900
  laptop's browser viewport is shorter than 900 px once its own chrome (the
  tab strip, the address bar) is subtracted, so these numbers are a
  conservative floor, not what a laptop actually shows.
- A chart from before line measurement (schema below 6) gets a one-line card,
  "This chart predates line measurement", as the phone does. It never shows an
  empty drawing.

## 2. The strip and the figures (approved)

**The strip**
- The sheet lies on its side: the hack at the left, the far house at the
  right. The thrower's left is the top edge.
- It draws everything the phone strip draws: the rings, both hog lines, the
  tee, back and hack lines, the centre line, the other stones, the dashed aim
  line, the thrown line and its dotted extension, the rock's gold path, the
  broom, where the rock started and where it stopped.
- **×1.5 across**, where the phone uses ×3. `DESKBOX = { w: 114, h: 660, y0:
  -2.3, y1: 38.9, half: 2.375 }` is the box before the turn: 24 px/m across
  and 16 px/m along. The SVG's viewBox is 660 × 114 at `width: 100%`, so its
  height is about a sixth of the card's width (118 px at 1440, 92 px at 1280).
- **The broom marker lies along the sheet**, so it is 10 wide × 4 tall here:
  the same marker as the phone's upright 4 × 10, turned with the sheet.
- **The miss bracket** runs across the sheet (up and down on screen), 7 px past
  the broom on the far side, as on the phone. Its label ("6 ft 1 in") is 11 px
  bold, so it stays readable once the drawing scales down. It sits left of the
  broom marker, end-anchored 16 px before the bracket at the bracket's middle
  (kept 11–111 px down the frame), with a 3 px ice halo so it reads over the
  lines it crosses.
  **Changed while planning:** approved as "4 px to the bracket's right". The far
  house is only about 30 px from the drawing's right edge, so a label there runs
  off it on every rock. A prototype on four real rocks (end 1 rocks 1 and 3,
  end 2 rock 6, end 3 rock 10) showed the left placement readable at every miss
  from 8 in to 6 ft 1 in.
- **Caption:** "Sheet from above, thrower at the left · across ×1.5 · figures
  ±4 in · wide = the side away from the curl".

**The figures**
- The same six, in the same words and the same feet and inches as the phone
  (`lineFigures`): At the broom, Hack, At the hog line in the first row;
  Weight, Curl, Came to rest in the second. The confirmation keeps its green
  check.
- A rock with no line: the strip still draws the sheet, the stones, the broom
  and where the rock stopped. The figures show "–" with the reason, as on the
  phone.
- **Amends §Unchanged, for this one note.** The Weight figure's note reads
  "hog line to hog line, estimated" rather than plain "hog line to hog line"
  when the split is an estimate -- `long_split_extrapolated_m > 0.05` or
  `long_split_far_reach_u > 0`, the same condition `splitText` already used
  for the chart panel's "(est.)"/"(x.x m est.)" suffix (`isSplitEstimated` in
  `stats.mjs`, shared by both). This is true on the phone Detail pane and the
  desktop card alike: an estimated split reading as measured was Ruling 10 in
  the final review, not a desktop-only fix.
- **Narrow-width value step.** `#detailCard` is a `container-type:
  inline-size` container; below a card width of 500 px, `.dfig .dval` steps
  from 19 px down to 15 px. The three `.dfigs` columns are `minmax(0,1fr)`,
  so their width comes from the card's own width, not the viewport's, and
  `<main>`'s grid-template-columns change at 1180 px and 820 px resets it --
  the card is narrowest just past each of those breakpoints (measured ~440 px
  at 1181 px wide, ~444 px at 821 px wide) rather than at either viewport
  extreme. There a 19 px "6 ft 1 in narrow" already overflows its ~126 px
  column, and 16 px (tried first) still overflows a 17-character value like
  "1 ft 10 in narrow" by 3-4 px; 15 px clears it with 4-5 px to spare. 500 px
  sits between the card widths that still overflow at 19 px (up to ~487 px)
  and the ones that already clear it (from ~502 px), so it catches both
  narrow points while leaving 1280 px and 1440 px wide viewports (card ~554
  px, ~699 px) at the full 19 px.

**The chart panel's list**
- Shown on the desktop and, on the phone, only in the short-screen layout
  (`(max-width: 640px) and (max-height: 520px)`); the tall phone layout
  (`(max-width: 640px) and (min-height: 521px)`, `PHONE_QUERY`) hides
  `#chart dl` outright, so the list is not something the two surfaces share.
- "Long split" goes: the Weight figure shows it now.
- "Weight" (m/s) becomes "Entry speed".
- "House: 1 in" becomes "1 stone in", because "in" now reads as inches. The
  other counts follow suit: "2 stones out", "1 stone moved".

## 3. How it is built (approved)

- **`frontend/core/line.mjs`**, all pure and tested in node:
  - `stripShapes(g)` turns `stripGeometry`'s output into explicit shapes: rings
    with `cx, cy, rx, ry, kind`; every line as a segment with a `kind` (hog,
    tee, back, hack, centre); the aim, thrown, extension and path lines as
    point lists; the stones; the broom as a rect `{x, y, w, h}`; the rest and
    start points; the miss as a segment plus a label anchor `{tx, ty, anchor}`.
  - `sideways(shapes)` turns shapes a quarter turn: every point `(x, y)` goes to
    `(H − y, x)`, where `H` is the strip's length. The frame's width and height
    swap, as do each ring's radii and the broom's `w` and `h`. The miss label
    moves left of the broom marker, end-anchored, with a halo (§2).
  - `DESKBOX` joins `STRIPBOX` in `constants.mjs`.
- **`frontend/viewer/Detail.jsx`**
  - `Strip` draws shapes and nothing else, so the phone and the desktop share
    one renderer. The lines the phone strip draws across itself (hog, tee,
    back, hack) come from the shapes instead of from `g.w`.
  - `Figures` is split out of `Detail`. The card's CSS gives it three columns;
    it takes no layout prop.
  - `Detail` (the phone) keeps its markup and draws the same marks at the same
    coordinates.
  - New `DeskDetail`: the turned strip, the figures and the caption, or one
    line instead: the reason when the chart predates line measurement, or "No
    rocks were detected in this end" when there is no rock at all (the phone's
    reasons would otherwise blame the broom).
- **`frontend/viewer/App.jsx`** mounts
  `<section className="card" id="detailCard"><DeskDetail shot={shot} doc={doc} /></section>`
  immediately after `#playCard`.
- **`src/curling_score/viewer/style.css`**: the grid areas at each width, the
  phone reset and hide, and the desktop figure grid.
- **`frontend/viewer/ChartPanel.jsx`**: the list's three changes.

## 4. Testing (approved)

**Node (`tests/test_viewer_js.py`)**
- `sideways`: the hack lands at the left (small x), the thrower's left at the
  top, a ring's `rx`/`ry` swap, the broom rect is 10 × 4, the miss bracket is
  vertical and 7 px past the broom, and its label stays inside the frame on a
  huge miss.
- `stripShapes` on the phone box reproduces today's coordinates. The existing
  `stripGeometry` tests pass unchanged.
- The new list labels, including the singular and plural stone counts.

**Source and CSS assertions**, in the suite's usual style
- `#detailCard` follows `#playCard` in `App.jsx`.
- The areas exist at each width.
- The card is hidden, and the areas are reset, at 640 px and below.

**Headless Chrome** (`scripts/devserve.py` plus `scripts/cdp.mjs`, on a
schema-6 timeline)
- At 1440 × 900 the card's bottom is at most 900.
- At 1280 × 800, record how far past 800 it runs.
- At 1000 px, the card starts within 14 px of the play card's bottom, and the
  chart panel is under it in column 1.
- At 700 px, the order is video, details, house, chart.
- At 390 × 844 (phone, touch on), the card is not visible, and the phone
  strip's SVG markup is unchanged from before the change.
- A rock with no line, and a schema-5 chart, show their reasons.

## Risks

- **Two orientations side by side.** The sheet lies sideways, while the house
  next to it stands thrower-at-bottom. Accepted when choosing A.
- **A thin fold margin.** Anything that grows the play card pushes the card
  below the fold: the warning rows in `#flags` (a blank or unseen rock, an
  inferred colour, the first end's warm-up notice). These are exceptional
  rocks, and the page still scrolls.
- **Phone regressions.** The phone layouts are position-fixed and select on
  `main > …`. The reset to `grid-template-areas: none` and the hide at 640 px
  and below are what keep them as they are. Both are pinned by tests and the
  390 px check.

## Out of scope

- A desktop Timing view, or changes to the report.
- Clicking or hovering the strip.
- Changes to the house card or the phone layouts.
