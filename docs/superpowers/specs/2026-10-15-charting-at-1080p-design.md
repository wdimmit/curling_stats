# Charting at 1080p: the charting bar, one line chart, and the delivery opened large

**Status:** design approved on the canvas on 2026-10-15. The user chose:
- layout C, the "charting bar";
- a click on the delivery opens it large, in a modal, with a speed trace under it. The trace was mocked on real rocks first, then approved: "This looks great".
- then a revision (board "C, revised"):
  - drop "This rock";
  - Thinking time becomes a header button like Report;
  - drop Needs charting and Scoreboard;
  - merge the strip and the delivery into one full-height chart in the side column;
  - move the figures (Weight, Rotation…) under the house.

**Mockups:** [Charting at 1080p](https://claude.ai/artifact/GNFKvxc4XhVa2tZrVB4XgG).
- "C, revised" is the layout.
- The board beside it is the opened view. Its Rock switch shows four rocks from s_0cYt (10/14, sheet 2, Wednesday Womens).
- The boards are the real `/c/` page with the layout injected and a real video frame, at 1920×945.
- The kit (render, inject and screenshot scripts, and the speed analysis) is in `out/mockups/desk1080/`.

## Context
At 1920×945 (a 1080p monitor, browser maximised), the desktop layout puts all of a rock's figures below the fold:
- the video is 1168×657;
- `#detailCard` (strip, delivery, seven figures) starts at y=886;
- the page is 1,733 px tall.

The layout was designed at 1440×900 (`2026-09-24-desktop-shot-detail-design.md`). Column 1 grows with the window, and the strip and the delivery chart scale with it. At 1920 they are drawn about 1.8× their design size, and the video takes the height.

**Success:** on a 1080p screen, charting a rock never needs a scroll. The video (at today's size), the house, grading, the figures and the rock's line are all on screen together. Any delivery opens large with its speed.

## Decisions
- **The new layout applies at 1700 px wide and up.** Below that, every layout is unchanged: `#detailCard`, the chart panel, This rock, Needs charting, Thinking time and Scoreboard all stay as they are. The 1440×900 laptop layout already fits and is not part of this.
  - The video column is about 1170 px inside at 1920, and about 950 px at 1700.
  - The bar needs about 1080 px to keep the shot-type row on one line. So below about 1830 px the subtypes wrap to a third row. The video is narrower there, so the height still fits.
- **Heights:** everything fits with no page scroll at 1920×860 and taller. 860 is a maximised browser with a bookmarks bar and the taskbar showing.
- **All desktop surfaces:** `/c/` and `/s/` get the bar. On `/s/` it shows someone's grading, inert, as the chart panel does today. `/g/` (review) hides it, as today (`body[data-mode="review"] #grading`).
- **Desktop only:** the phone layouts (≤640 px) do not change, and neither does the phone's Delivery tab.
- **No new data:** no pipeline, timeline or schema change. Speed is worked out in the browser from `line.delivery`. It works on schema ≥ 8 charts, whether the path is 10 fps (before 2026.10.12.1) or 30 fps.

## Design

### 1. The layout (≥ 1700 px)
`<main>` is three columns: `minmax(0,1fr) 330px 340px`.

| Column 1 | Column 2 | Column 3 |
|---|---|---|
| video card, then the charting bar | house card, then the figures card | the line chart, full height |

At 1920×945 this measured:
- video 1168×657, today's size;
- the video column ends at 879, the figures card at 919 and the line chart at 915 (the fold is 931 inside the page's padding);
- no page scroll.

**Header.**
- The rock's chip and label ("2nd end, lead's second rock") move up beside the source.
- A **Thinking** button (title "Thinking time, rock by rock") sits just before Report. The header's items have explicit `order` values, so it gets one.
- The header still fits on one line at 1920. That is why "YouTube →" stays in the control row, not the header.

**Video card.**
- The video's width is the smaller of the column and `(100vh − K) × 16/9`, where K covers the header, the control row, the bar and the paddings. The plan measures K. This is what keeps 1920×860 from scrolling.
- One control row under the video: ← | the end's chips | → | ↻ Replay | ✔ Mark charted (Enter) | … | YouTube → | ⚙.
- The gear opens a small popover holding "play on jump" and lead-in, which today take a row of their own. It shows wherever those controls show today.

**The charting bar (`/c/`, and inert on `/s/`)**, under the video, two rows. Everything keeps its id, handler and keyboard behaviour.
1. SHOT TYPE: the groups, a divider, the subtypes, then the auto/confidence note at the right.
2. SCORE 0–4 — | MISS (input) | NOTE (one line) | ORDER (the move-before select).
   - The Order explanation becomes the select's `title`.
   - "Place the stones" stays phone only, as today.

**House card.** As today, except "Click the ice to add a stone…" becomes the house's tooltip. It costs 36 px otherwise.

**Figures card**, under the house:
- The seven figures (`lineFigures`, the `Figures` component) in two columns, values at 18 px.
- A note that doesn't fit its column ends in "…" and shows in full as the figure's `title`.
- Caption: "Figures ±4 in · wide = the side away from the curl".
- **Above the figures, only when they apply:**
  - the format and per-rock warnings (`#flags`), moved here from under the transport row; they are about placing stones, so they sit by the house;
  - for doubles, the end box: hammer and power play, plus the swap checkboxes on `/c/`.
  On a rock with warnings the card grows past the fold. Those rocks are rare, and the warning is the thing to read.
- If a chart predates line measurement, the card says so in one line, as the phone does.

**Dropped at ≥ 1700:**
- **The detail list:** Thrower, Entry speed, Travel, Thinking, House, Evidence, Stones. The user calls it diagnostic.
- **Needs charting:** the header pill "N to chart →" already jumps to the next one.
- **Scoreboard:** the game report shows the score by end.

All three stay in the narrower layouts and on the phone.

**Thinking dialog.** The Thinking button opens a native `<dialog id="thinkingDialog">` holding today's Thinking time content: the chart, the key and the per rock / totals switch. Clicking a bar goes to that rock, as it does today, and closes the dialog. It closes on Esc, ✕ or a backdrop click.

**The line chart (column 3)** is one drawing of the whole sheet, thrower at the bottom, at the column's full height. It replaces both the strip and the delivery chart.
- **Top, the far end:** the strip from 1.5 m past the near hog line (8.05 m past the throwing tee) to the far back line. The geometry is `stripGeometry` with `y1 = TEE_Y − 8.05`, clipped to its box by a nested `<svg>` so nothing needs an id. It shows the house, the far hog, the stones, the broom, the miss label, the thrown line and its extension, and the path to rest. It is labelled back, tee and hog on the left, as the delivery labels its lines.
- **Bottom, the delivery close up:** `deliveryGeometry` upright (`DELIVERYBOX` shape) from the hack to 8.05 m: dots, the aim line, the footholds and inch ticks.
- **Under the close up, faintly: the paint.** This shows how the slide lines up with the sheet.
  - **The near house:** the rings round the throwing tee, at the chart's own stretch, so they are wide, flat ellipses, at 30% opacity.
  - **The club's 4 ft centre stripe:** edges at ±0.6096 m, the same paint the long-camera calibration reads (`sideview.STRIP_HALF_M`). It is drawn only in front of the house, as it is painted: from where it meets the 12-foot, following that ring's curve, out past the hog line. It is a light band with thin edges.
  - **Placement:** clipped to the plot, under the grid, the lines and the dots.
  - The user asked for this on 2026-10-15, with the stripe in front of the house only.
- **Between them, a funnel:** a 13 px band. Two dashed lines and a faint fill join the delivery's across window, at its top edge, to where that window sits on the sheet at the bottom of the top part. A heavier tick marks that span on the sheet.
  - `deliveryGeometry` returns its window (`win: { x0, span }`) so the funnel is not read back off the ticks, as the mock had to.
- **In this chart only:**
  - the delivery's "to the broom" label is dropped, because the aim line carries on into the sheet above;
  - its legend goes in the plot corner with the fewest dots.
- **The split (user, 2026-10-15):** the delivery takes a full half of the drawing's height, so its along-the-ice axis is less squeezed. The funnel is 13 px, and the far end has what is left. At 1920×945 the drawing is 314×760: the far end 367, the funnel 13, the delivery 380. That is across ×5 above and about ×6 below; the delivery window widens for a wide rock, so its stretch varies.
- **Header and caption:** a header row reads "THE LINE" with an Expand button at the right. The caption reads "From above, thrower at the bottom · far end across ×N · the delivery across ×M, a dot every …". A click anywhere on the drawing also opens the large view (`cursor: zoom-in`).
- **A rock with no delivery** (camera did not follow, or schema < 8): the chart is the strip at full height with the reason line under it, and no Expand.

**Constraints the implementation must keep:**
- The video iframe is never reparented (`runtime/player.mjs`).
- Both phone layouts render the same DOM as now. The phone's bottom sheet styles grading through `#chart` (`#chart #typeList`, `body[data-sheet] #chart …`), so grading keeps its place in `#chart` on a phone. Two options for desktop:
  - CSS moves it, for example `#chart { display: contents }` at ≥1700, with the rest of the panel hidden there. Check the `aside` landmark survives in the accessibility tree.
  - React renders it in the bar by media query. Losing at most 150 ms of unsaved typing on a resize across 1700 is acceptable.

  The plan picks one, and a domsnap diff of the phone states proves the phone is unchanged.
- React renders one layout or the other, never both: `#detailCard` and the chart panel only below 1700 px, and the bar, the figures card and the line chart only at 1700 px and up. So every grading control and every id is in the page once. None of the drawings carry element ids either.
- Keyboard shortcuts are unchanged.

### 2. The delivery opened large
A native `<dialog id="deliveryDialog">` opened with `showModal()`, beside the Flag, Played, Teams and Clip dialogs outside `<main>`.
- **The rock it shows:** the one it was opened for, snapshotted on open like the flag dialog. A review link that follows the video does not change it underneath.
- **Closing:** ✕, Esc, or a click on the backdrop.
- **Shortcuts:** global shortcuts already stop while any dialog is open (App.jsx, `dialog[open]`).
- **Width:** `min(1560px, 100vw − 64px)`. The charts are fluid inside it.

Contents, top to bottom:
1. **Header:** DELIVERY, the rock's chip, "label · Shot type", "Esc or click outside to close", and ✕.
2. **The delivery, large.**
   - Box: `deliveryGeometry` with a new `BIGDELIVERYBOX` = `{ w: 1000, h: 250, side: true, from: −4.0, to: 8.05, across: 0.9, minorInches: 2, paint: true, pad: { l: 10, r: 52, t: 18, b: 8 } }`, drawn about 1.5× (1500 px wide at 1920).
   - Ticks: a new box option `minorInches: 2` adds unlabelled ticks every 2 in between the labelled 6 in ticks. The default boxes stay as they are.
   - Paint: a new box option `paint: true` gives the same house and stripe underlay as the line chart. Here the window is narrower across, so usually only the stripe edge nearer the rock shows.
   - Caption: "From above, thrower at the left · across ×N · a dot every …, dark at the push-off to gold past the hog line · ticks every 2 in".
3. **The speed numbers:** "**2.4 m/s** at its fastest, 2.3 m out of the hack · **1.9 m/s** at the hog line · **0.5 m/s** lost between them".
4. **The speed trace:**
   - Box: `SPEEDBOX` = `{ w: 1000, h: 170, pad: { l: 10, r: 52, t: 16, b: 22 } }`. It shares `from`/`to` and the left and right pads with `BIGDELIVERYBOX`, so a point on the ice is at the same x in both charts.
   - Axes: hack, tee and hog lines, labelled under the plot; gridlines every 0.5 m/s, figured every 1 m/s at the right; zero at the bottom.
   - The line: coloured with the same ramp, by time, as the delivery dots.
   - Markers: a dot and label at the peak ("2.4 m/s peak", above). A red dot at the hog line, labelled below it with a halo so it never sits on the line.
   - Caption: "Speed along the same axis, smoothed over a second. Ripples smaller than about 0.2 m/s come from the camera, not the throw."
5. **When speed cannot be read:** too few samples, or none before the hog line. The trace and the numbers become one line: "The speed could not be read for this rock." The hog-line number alone is left out when the path ends before 1 m short of the hog.

### 3. Speed, in `core/delivery.mjs`
`deliverySpeed(shot)` returns `{ series: [{ t, yp, v }], peak: { yp, v }, hog: { v } | null }` or `null`. It is pure and tested in node like the rest of the core.
- **Speed at each sample:** the slope of a straight-line fit of along-position (`yp`) against `t`, over every sample within ±0.5 s (`SPEED_HALF_S`). It needs at least 5 samples in the window.
- **Where the series starts:** at the push. From the fastest sample before the hog line, go back while the speed is above 0.25 m/s. Otherwise a stone nudged into place before the delivery draws as a false start (s_0cYt end 2 rock 9).
- **Peak:** the fastest sample before the hog line, with its `yp`. "Out of the hack" is `yp − HACK_PAST`.
- **Hog:** the slope of one straight-line fit over every sample from 1 m before the hog line to the end of the path. It is not read off the smoothed line.
  - Why: every sheet's path dips at the hog line, and this fit agreed with the pipeline's hog speed (median +0.03 m/s over 172 rocks).
  - `null` with fewer than 6 samples or under 0.5 s there.
- **`speedGeometry(speed, box, shot)`:** the trace in pixels (segments with ramp colours, gridlines, line marks and labels, markers). It follows `deliveryGeometry`: `Delivery.jsx`'s new `SpeedChart` only turns it into markup.

What the measurements say, and why the caption is worded as it is (`out/mockups/desk1080/speed/`):
- **Noise:** along-the-ice position is about 10× noisier than across: 3.3 cm a frame against 0.3 cm (172 rocks, after a straight fit over the last 2.5 m). Per-frame speed is mostly noise, hence the second of smoothing.
- **Ripples:** after the release, the leftover ripples (0.1–0.2 m/s) sit at the same places for every rock on a sheet and differ between sheets (5 sheets compared). They are camera error. Every sheet also dips at the hog line, likely where the read joins the band.
- **What the trace is good for:** the overall shape and the three numbers.
  - The hogged rock (end 2 rock 2) peaked at 2.6 m/s and lost 0.8 m/s before the hog line.
  - The full-weight hit (end 1 rock 4) pushed past the tee line, peaked at 3.0 and kept 2.7.

## Testing
- **Node (core):**
  - `deliverySpeed` on paths from s_0cYt. Peak and hog to ±0.05 m/s: end 2 rock 4 is 2.4 / 1.9, end 2 rock 2 is 2.6 / 1.7, end 1 rock 4 is 3.0 / 2.7. End 2 rock 9's nudge does not start the series.
  - A 10 fps path works.
  - A path ending short of the hog gives `hog: null`. Too few samples gives `null`.
  - `speedGeometry` puts a given `yp` at the same x as `deliveryGeometry(…, BIGDELIVERYBOX)`.
  - `minorInches` adds ticks only where a box asks for them.
  - `paint` only where asked. The house is centred on the throwing tee at the chart's stretch. The stripe's edges sit at ±0.6096 m and run from where they meet the 12-foot out to the chart's far edge, never behind or through the house. `STRIPE_HALF_M` matches `sideview.STRIP_HALF_M`.
- **Node (the line chart's geometry):**
  - The top part's lower edge and the delivery's upper edge are the same place on the ice (8.05 m past the throwing tee).
  - The funnel's top span equals the delivery window mapped onto the strip's across scale.
  - The legend corner avoids the dots.
- **Headless layout check** (a script in the style of `scripts/reportcheck.mjs`, against `devserve.py`):
  - At 1920×945, 1920×860, 1920×1032 and 2560×1305: no vertical or horizontal scroll on `/c/`, `/s/` and `/g/`, and the bar, the figures card and the line chart all end above the fold. Here "above the fold" means with no warnings showing.
  - At 1699×900: today's layout, with `#detailCard`, the chart panel and its sections showing.
  - Phone: domsnap of the phone states identical before and after.
- **The dialogs:**
  - Delivery: a click on the chart, or Expand, opens it; Esc, ✕ and the backdrop close it.
  - Thinking: opens from its button; a bar click goes to the rock and closes it.
  - ←/→ do not move the rock while either is open.
  - Both open on `/s/` and `/g/` too.
  - Check in Chrome and Firefox (`<dialog>` and its backdrop).

## Out of scope (follow-ups)
- The speed trace on the phone's Delivery tab. The core is shared, so it would be the markup only.
- Stepping rocks inside the large view (offered, not chosen).
- Removing the camera ripple: a per-camera along-the-ice correction, plus the hog-line dip in the path.
- **`v_hog_m_s` reads too low on fast rocks.** On 29 of 84 rocks with splits under 11 s (4 games), it is below the rock's hog-to-hog average speed, which a slowing rock cannot be. The delivery path's own hog speed never is. Investigate separately; this design does not use the field.
- **Label collision in the phone's upright delivery chart.** Its legend ("push-off → hog +1.5 m") overprints "to the broom" when the aim line ends top left, as on rocks thrown wide left. The line chart above avoids it in its own copy; the phone's Delivery tab keeps it until a separate fix.
- Laptop widths (below 1700 px), including bringing the Thinking button there.
