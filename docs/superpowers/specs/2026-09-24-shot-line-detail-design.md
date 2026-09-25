# Shot line detail: was the rock thrown at the broom?

**Status:** design approved section by section, 2026-09-24, and ready for an
implementation plan.

**Mockups:** [Shot Detail on a Phone](https://claude.ai/artifact/Bz5Pa6Ke4Q3gwUZ6FjfZVF).
The first artboard is the chosen Detail pane. B and C are the options not
chosen. The second row shows the House tab, the Timing tab, and every state of
the Detail figures.

## Context

Every shot now carries `target_broom` (broom1 and broom2), and the side cameras
detect rocks (ds13b). A spike on 2026-09-24 asked whether those two together
can say whether a rock was thrown at the broom. The answer is yes, per shot, to
about ±10 cm.

**How the line is measured**
- **Stone column.** The hog-crossing side camera already sees the stone from
  the throwing tee to about 12 m past it. `sidemodel.propose` computes each
  box's column and throws it away. The spike keeps it.
- **Fit.** A straight line is fitted to the stone past the throwing hog line,
  where it has certainly been released. That covers 6.4 to 10 m past the
  throwing tee. The fit is straight to 0.2–0.4 cm RMS.
- **Miss.** The line is extended to the broom's depth. The miss is its
  distance from the broom there.
- **Start point.** The stone's position before push-off gives the hack. It is
  steady to 1–2 cm, and each player's is consistent from shot to shot.
- **Independent check.** The camera behind the thrower tracks the same stone
  from 6–9 m past the hog line. It agrees with the fitted line to a median of
  3.5–4.4 cm, against 8–14 cm from the hack-to-broom line. That was measured
  over 69 checked shots.
- **Calibration.** The lateral mapping holds to better than 1% at every depth.
  That was checked against painted lines 0.615 m either side of the centre line
  and against stone widths. Positions must be measured from the painted centre
  line, not the ring fit: on VXU9's left camera the ring fit is 3 cm off.

**What it found**

| Game | Shots | Line passes the broom, median | Outside / inside |
|---|---|---|---|
| VXU9 | 45 | 16 cm outside | 23 / 2 beyond 15 cm |
| hOKZ | 111 | 33 cm outside | 81 / 7 beyond 15 cm |
| AEqL game 2, end 3 | 16 | 39 cm outside | 15 / 1 |
| hOKZ chart s_0QFe, end 3 | 16 | 33 cm outside | 15 / 1 |

Curl bends the path from release onward. That makes the straight line after
the hog line a conservative estimate of how wide a rock was thrown. Scripts and
results are in `~/curling-work/line-spike/`.

**Cost.** Timed on the worker over a whole game (hOKZ chart, 126 stones), in a
throwaway container of the production image:

| Piece | Per game | Per stone |
|---|---|---|
| Today's hog-line pass, for comparison | 134 s | 1.07 s |
| Extending that pass to 9 s after release, same decode | +62 s | 0.49 s |
| Start point | +34 s | 0.27 s |
| Path from behind the thrower | +199 s | 1.58 s |

In total that is about 5 minutes on top of today's 14–23 minutes per game.

**Decisions from the user**
- **Detail pane.** Option A, the line on the sheet, without the headline
  verdict.
- **Tabs.** Three tabs at the bottom: House, Detail and Timing. Timing reads
  the whole game.
- **Swiping.** House and Detail swipe left and right through rocks.
- **Path pass.** The path from behind the thrower runs on every game.
- **Order.** Local replays first, then ship.

## Scope

**In scope**
- The read-only phone surfaces, `/g/` and `/s/`: today's Watch mode, behind
  `PHONE_QUERY`.
- The pipeline pass that produces the data.

**Unchanged**
- The desktop layout.
- The charting phone layout (the bottom sheet with grading).

## 1. Detail pane (approved)

The artboard "A · The line on the sheet".

**Pager** (56 px)
- ‹ and › buttons, 44 px each.
- The rock chip, in the team colour, with the rock number.
- A line of text: "Yellow · third · draw".
- 16 position dots.
- Swiping anywhere on the pane does the same as the buttons.

**Sheet strip** (left, 150 × 420 px)
- The whole sheet from above, thrower at the bottom, the same way round as
  the video.
- Stretched about 3× across the sheet so that the lines separate.
- Drawn in `PAINT` colours. It shows:
  - where the stone sat before push-off (a dot);
  - the intended line, from the rock's hack to the broom (muted, dashed);
  - the thrown line: solid ink where the hog camera measured it, dotted from
    there to the broom's depth;
  - the stone's path from the camera behind the thrower (gold, `#a07a00`,
    which is legible on the ice where `#e8b400` is not);
  - the broom pad, in ink with a thrower-colour stroke;
  - where the rock stopped;
  - the other stones in play, faded;
  - a small bracket labelled with the miss at the broom.

**Figures** (right, top to bottom)
1. **At the broom:** "2 ft 4 in wide", with "✓ confirmed from behind the thrower"
   under it.
2. **Hack:** "Left", with "stone set 9 in left of centre".
3. **At the hog line:** "8 in wide", with "of the hack-to-broom line".
4. **Weight:** the long split, "13.8 s", with "hog line to hog line".
5. **Curl:** "3 ft 9 in", with "from its line to where it stopped". After a
   hit, "from its line to where it hit a stone" (amended 2026-09-25: see
   below).
6. **Came to rest:** "12-foot", with "1.8 m from the button".

**The hack and the two line figures (amended 2026-09-25).**
- **The hack is always Left or Right.** There is no centre hack, and
  "Centre" is gone.
  - A player throws from one hack all game, so the viewer calls it once per
    player (team colour and throwing position) per game, in `buildGameView`.
    That is after the charter's corrections.
  - The call is the sign of a turn-balanced median of where the player's
    stones sat. The turn moves the stone: a rock that curls right is set
    5–10 cm further to the thrower's left than one that curls left, on
    either hack. So the median of each turn counts once. Dead centre is the
    left hack.
  - On the replays, 23 of 24 players had every stone on one side. The two
    hOKZ skips are on the right. AEqL2's red third sets the stone on the
    centre line; the call is Left by 3 mm, which the user confirmed.
- **The note** still gives this rock's own stone:
  - "stone set 3 in left of centre", or "stone set on the centre line";
  - "as on this player's other rocks" when this rock's start was not seen,
    including a rock with no line.
- **The hack-to-broom line** runs from the hack's foothold centre, 0.152 m
  off the centre line on the hack line, to the broom. WCF R1: each inside
  edge is 76 mm from the centre line, and a hack is at most 152 mm wide.
- **Both line figures read the same two lines at two depths:** the thrown line
  and the hack-to-broom line.
  - At the hog line is their gap at the hog line.
  - At the broom is their gap at the broom's depth. The hack-to-broom line
    ends at the pad, so this is the thrown line's miss of the pad, whatever the
    line's back end. It did not change.
- **Replays:** the hog-line median moved from 8–12 cm wide (from the stone) to
  11–13 cm (from the hack). "On the line" went from 40–50% of rocks to
  31–34%.

**Curl stops at a hit (amended 2026-09-25).** Curl is measured to where the
rock stopped, or to where it hit something, whichever came first. The user's
example: s_0NOnuMHZoSp23r6n4 end 1 rock 14 is a hit on the broom that rolled
0.9 m after impact. It read "2 ft 11 in" and now reads "5 in".
- **A hit** is the first point where the rock comes within two stone radii
  plus 10 cm (0.384 m) of a stone its throw disturbed. Those stones are
  `house_delta`'s removed stones, and its moved stones at their `from`
  positions.
- **Where the curl is read:** the rock's last sample before that point.
  - The overhead `track` is tried first: it samples ten times a second, from
    the camera that placed the stones.
  - Then `line.path` from behind the thrower.
  - The nearer of the two to the stone wins.
- **A rock already touching when first seen** reads "–", with "hit a stone
  before it was seen".
- **A rock that never comes near a disturbed stone** counts as not having hit
  one, and is measured to rest as before. The house diff also moves stones a
  rock never touched: detection dropouts, and stones knocked on.
- **The turn after a hit** (wide or narrow in At the broom and At the hog line)
  is read at the same point. The pipeline's `curl` field takes it from the
  rest position, which a hit-and-roll can flip. Without a hit, the viewer keeps
  the pipeline's `curl`.
- **The hack call's turn balance** uses the same turns (`turnOf`). On the
  replays and s_0NOn this changed no player's call. AEqL2's red third moved
  from −0.3 to −1.0 cm: still Left, and a little further from the line.
- **Replays and s_0NOn:**
  - 135 of 326 lined rocks are hits;
  - their median curl moved from 20.5–29.5 in to 17–23.5 in;
  - 25 At-the-broom words changed, all 12 in s_0NOn toward "wide";
  - no figure changed on a rock without a hit.

**Caption:** "Sheet from above, thrower at the bottom · across ×3 · figures ±4
in". It also defines "wide = the side away from the curl" once. There is no
glossary.

## 2. Shell and navigation (approved)

**Layout**, top to bottom:
- header, 48 px;
- the pinned video, 219 px;
- the pane;
- the tab bar, 64 px including `env(safe-area-inset-bottom)`.

The video is never reparented. That is the same rule as today
(`runtime/player.mjs`), and CSS alone decides what shows.

**Tabs**
- The tabs are House · Detail · Timing.
- Today's per-end rock list moves into Timing.
- House and Detail each show one rock.

**One cursor, `(ui.ei, ui.si)`, shared by all three tabs**
- Swiping, ‹ ›, and tapping a Timing row all move the cursor and seek the
  video there, as tapping a Watch row does today.
- Swiping crosses ends: after rock 16 comes the next end's rock 1.
- Tapping a Timing row keeps you on Timing.

**Following**
- While the video plays, House and Detail follow the rock being thrown. That is
  today's `useFollow`.
- Swiping away mid-play pauses following and shows "▶ Back to rock N", as the
  list does today.
- A swipe starts at least 20 px in from the left edge, so it never fights the
  browser's own back gesture.

**Tab and rock in the URL**
- The tab and the rock are in the URL (`#tab=detail&e=3&s=11`), so a shared
  link opens in the same place.
- The last tab used is remembered in `runtime/prefs.mjs`.
- A first visit opens on Detail.

## 3. House and Timing tabs (approved)

**House**
- The pager, as on Detail.
- The existing read-only `House` component, cropped to the scoring area with
  room for guards. It shows the house after this rock, the rock ringed, and the
  skip's broom pad with its dashed link to the rock (already shipped).
- **The rock's path.** Where `line.path` exists, the house draws that path.
  Otherwise it falls back to today's `track`. `track` comes from the overhead
  panel, which is squeezed toward the centre line near its far edge and can
  start with a glitched point (VXU9 e1 s8).
- A caption says where the rock stopped, with a one-line legend.
- **Ghosts of hit stones (amended 2026-09-25).** Every house view (desktop
  card, phone band, watch House tab) draws where the stones this rock
  disturbed sat before it.
  - The data is `house_delta`: its moved stones at their `from` positions, and
    its removed stones.
  - Each ghost is a dashed ring with a faint core in the stone's colour.
  - A moved stone also gets a dashed link to where it went. A stone knocked out
    of view gets no link.
  - Ghosts show and hide with the track toggle, together with the path and the
    broom. They don't take pointer events.
  - `house_delta` is not recomputed when the house is edited by hand, so an
    entry the current stones contradict is dropped (`ghostStones`, 0.30 m, the
    pipeline's `MOVED_MIN_M`). That covers a move whose stone is no longer
    where it went, and a removal whose stone is still there.
  - Knock-ons and detection dropouts are in the diff too, so they get ghosts.
  - The watch caption adds "dashed: where hit stones sat" when any are drawn.
- There is no gesture conflict. These surfaces are read-only, so stones are not
  draggable and a horizontal swipe is free.

**Timing**
- **Top.** The existing `ThinkingChart` for the whole game, with team totals.
  The current end is shaded and the current rock marked.
- **Below.** Every rock of the game in one scrolling list, grouped by end. Each
  end's header ("End 3 · Good has hammer · Good scores 1") stays pinned while
  you scroll through that end.
- **Rows.** Today's `RockRow`: rock chip, position, long split, thinking bar
  and seconds. The current rock is highlighted and kept in view while
  following.
- **What goes.** The per-end ◀ ▶ `EndBar` and the separate "◷ Whole game"
  sheet.
- Timing does not swipe: its vertical scroll owns the gesture.

## 4. Data: the `line` field (approved)

Each shot gets `line`, an object or `null`. This raises the timeline to schema
6. Coordinates are in the timeline's frame: metres from the destination tee,
+y up-sheet toward the thrower, +x the thrower's right.

```json
"line": {
  "start":    {"x": -0.232, "y": 38.07},
  "at_hog":   {"x": -0.757, "offset_m": -0.163},
  "at_broom": {"x": -2.359, "miss_m": -0.712},
  "side": "wide",
  "curl": "right",
  "confirmed": true,
  "hog_path": [[28.35, -0.757], "…"],
  "path":     [[20.01, -1.180], "…"],
  "fit": {"n": 53, "rms_m": 0.004}
}
```

**The fields**
- **`start`.** The stone before push-off.
- **`at_hog`.**
  - `x` is the fitted line at the throwing hog line, y = 28.346.
  - `offset_m` is `x` minus the start-to-broom line at that depth.
    Since 2026-09-25 the viewer does not show it. The hog-line figure is
    measured from the hack-to-broom line instead (§1). The field stays in the
    data, so nothing needs reprocessing.
- **`at_broom`.**
  - `x` is the fitted line at `target_broom.y`.
  - `miss_m` is `x − target_broom.x`, signed.
- **`curl`.** The direction curl moved the rock: the sign of
  `x_rest − fit(y_rest)`. It is `null` without a rest position or a path.
  Since 2026-09-25, after a hit the viewer reads the turn from just before the
  hit instead (§1).
- **`side`.**
  - `"wide"` when the miss is on the side away from the curl, which is
    `sign(miss) == −sign(curl)`. `"narrow"` otherwise.
  - `null` when `curl` is `null`, and the viewer then says left or right.
- **`confirmed`.**
  - `true` when the path from behind the thrower was seen at least 3 times
    between 19 and 23 m out (5.3–9.3 m past the throwing hog line, before the
    rock curls away from its line) and lies within 10 cm of the fitted line
    there (median). Amended 2026-09-24 from "seen from 12 m out, first 4 m":
    replaying VXU9, that rule compared paths first seen 14–18 m out, where the
    rock had already curled 10–36 cm, and called 18 of 52 rocks disagreements.
  - `false` when it was seen there but disagrees.
  - `null` when it was not seen there.
- **`hog_path` and `path`.** `[y, x]` pairs thinned to about one per 0.5 m,
  roughly 60 points in all, a few hundred bytes per shot.
- **Precision.** Rounding and the "On the broom" rule belong to the viewer, so
  the data keeps full precision.

**How it is produced**, as attach-only passes beside `hogtime` and
`broomtime`. None of them can add, drop or renumber a shot.

1. **Painted centre line (`geometry/sideview.py`).**
   - `solve_centre_line(plate, view)` fits `col = a + b·row` to the painted
     centre line. It takes the luminance dip nearest a guide seeded at the ring
     centre on each row, then fits with outliers over 1.5 px rejected. The
     spike's `robust_centre.py` did this on sheets whose lines a centre-ice
     logo interrupts.
   - `to_house` and `to_image` use it when present, and the ring centre
     otherwise.
   - It is published in the calibration block.
   - This also corrects `target_broom.x` by up to 3 cm on cameras whose ring
     fit was off.
2. **Keep the column, and join split tracks
   (`detect/sidemodel.py`, `detect/longview.py`).**
   - The proposer keeps each box's `cx` beside `(t, edge_row, body_px)`.
     `crossing_from_tracks` ignores it.
   - **Separate fix, own commit and test.** When no single track straddles the
     hog row, `crossing_from_tracks` retries with each 120-px column bin joined
     to its neighbour. A stone whose column crosses a bin boundary exactly at
     the hog row is split into two tracks, neither of which straddles the line.
     Today it reads as "never reached the line" and loses its split (AEqL game
     2, e3 s3). This fix changes some splits on its own.
3. **`game/linetime.py`**, per shot with a release and a hog crossing:
   - **Window.** Use hogtime's window. Extend it to 9 s after release only when
     the track ends short of 10 m past the tee (slow guards and draws).
   - **Relink.** Chain the crossing track frame to frame by continuity, not by
     bin.
   - **Map.** Take each box's bottom-centre to the stone's centre footprint
     (one radius further from the camera). Read x from the painted centre line.
     Convert to the destination frame: `x = −x′`, `y = 34.747 − y′`.
   - **Crop edge.** Drop samples whose box bottom is within 6 rows of the band
     crop's bottom edge. That is where the box is clipped and the "hook"
     artefact comes from.
   - **Fit.** Fit x(y) over 6.401–10.0 m past the throwing tee. It needs at
     least 15 samples spanning at least 2.5 m.
   - **No speed gate.** Apply no speed gate. The 3.2 m/s bound times crossings,
     and big-weight hits exceed it.
   - **Start point.** Run ds13b on the hog camera over `t_release − 3.0` to
     `−0.2` s at 5 fps, on the rows behind the tee. Take the median x of boxes
     within 0.6 m of centre, 2.1–4.3 m behind the tee.
4. **Path from behind the thrower**, in the same module:
   - **Detection.** ds13b on the destination camera over `t_hog + 1` to
     `t_rest + 1` s at 5 fps, on two crops: `tee_row − 80` to row 660 at imgsz
     800, and row 620 to the bottom at 416.
   - **Chaining.** Chained with gates that widen with the gap, allowing gaps up
     to 8 s. The delivery team hides the stone for up to 6 s at a time.
   - **Start of the chain.** It starts at the detection nearest the fitted
     line. It moves to the next candidate when a start leads nowhere, such as a
     sweeper's broom or a resting stone near the line.
5. **Versioning.**
   - `timeline.build_end` publishes `line`, and `SCHEMA_VERSION` goes 5 → 6.
   - `PIPELINE_VERSION` is bumped with a dated comment.
   - `processing_version` gains `+line`.
   - A shot the passes cannot measure gets `line: null` and never fails the
     run.

## 5. States and wording (approved)

The artboard "Detail: every state of the verdict".

**At the broom**
- Within 10 cm: "On the broom", with no number.
- Otherwise: in feet and inches to the nearest inch (changed from 5 cm steps on
  2026-09-24), as wide or narrow; left or right when `side` is `null`. The hack,
  the hog line and the curl use the same feet and inches.

**The confirmation line under it**
- `confirmed: true`: ✓ "confirmed from behind the thrower".
- `confirmed: null`: "not confirmed: hidden from behind the thrower". The
  number still shows.
- `confirmed: false`: "the camera behind the thrower disagrees", and the number
  is greyed.

**When `line` is `null`**
- The pane shows what exists: broom, rest, the path if there is one, the
  player's hack if any of their starts was seen, and the weight.
- The two line figures read "–", with the reason underneath:
  - "The hog-line camera lost this rock"
  - "No broom was held still before the release"
  - "This rock was never seen"

**Missing and blank rocks** stay in the pager, and the pane says why there is
nothing to show.

**Older charts** (schema below 6) keep the Detail tab with one line in place of
the pane: "This chart predates line measurement". House and Timing are
unaffected.

## 6. Testing and rollout (approved)

**Pipeline**, pure functions written test-first:
- the centre-line fit on a synthetic plate, including a gap where a logo
  interrupts the line;
- the line fit and the derived `at_hog`, `at_broom`, `side` and `curl`;
- the adjacent-bin join, with a regression built from e3 s3's split track;
- the window-extension rule;
- each `line: null` case;
- the crop-edge sample filter.

**Pipeline checks on the local games** (VXU9 and hOKZ in `~/.cache/curling_score`,
AEqL in `~/.cache/curling_replay`, all with video):
- `line` on at least 90% of rocks that have a broom;
- medians consistent with the spike: VXU9 16, hOKZ 33, AEqL game 2 end 3 39 cm
  outside;
- the confirmed rate reported per game;
- apart from `line`, the corrected `target_broom.x`, the calibration fields,
  any splits the bin-join fix recovers and the version strings, the timeline
  diffs identical to today's.

**Viewer**, `tests/test_viewer_js.py`, pure functions:
- `lineFigures(shot)`: the 10 cm threshold, 5 cm rounding, wide/narrow and
  left/right, the three confirmation states, each `null` reason and the
  pre-schema-6 case;
- `stepRock(view, ei, si, d)` across end boundaries and at the first and last
  rock;
- the tab and cursor round-tripping through the URL hash;
- `stripMap(x, y)`, the strip's metres-to-pixels mapping.

Layout is checked by eye at 390×844, 430×932 and in landscape, which falls back
to the scrolling stack. Two regressions to guard by hand:
- The player never reloads when you swipe.
- A blank rock stays hatched on House.

**Rollout**
1. Local replays first.
2. Deploy the worker and API from a clean worktree, per the
   deploying-curling-chart notes. Stage only this work's hunks, because another
   session shares the checkout.
3. Requeue the hosted videos, at about 5 minutes extra per game.

## Risks

- **The outside bias is unvalidated by a person.**
  - The spike consistently measured a median 16–39 cm outside, and two cameras
    agree.
  - Nobody has confirmed a sample by eye against what players felt. Review
    `eyecheck_vxu9.png` and the per-shot renders before release.
  - The figures claim ±10 cm and no more.
- **Early curl on slow draws.**
  - On keen ice the heading eases within the fit window (4RrN e1 s4).
  - For "was it thrown at the broom" this errs conservative: the thrown line is
    at least as wide as reported.
  - Heading ratios from these shots should not be shown as a statistic.
- **Unconfirmed shots.** About a third to a half of long draws and guards are
  hidden from behind the thrower all the way (hOKZ end 3: 4 of 16). The pane
  says so rather than hiding the number.
- **The bin-join fix moves splits.** It is a production change to the hog
  timing, so it ships in its own commit and is replayed on its own first.
- **Swipe against browser gestures.** The 20 px edge exclusion and
  horizontal-only swipe handling (vertical scroll still works) must be tried on
  iOS Safari and Android Chrome.
- **Cost.** About 5 minutes per game, or +21–35%. That matters for the
  live-mode capacity sum in the worker-throughput notes. The path pass is the
  large term, and can be made conditional later if the worker falls behind.

## Out of scope

- The desktop layout and the charting phone layout.
- Stills from behind the thrower (option B). That is a possible later "tap to
  see it from behind the thrower".
- Player or team accuracy across games (option C's "every rock this end" strip
  is a candidate follow-up).
- Detecting rotation. Curl direction comes from the path.
- A statistic for heading ratios.
