# Task 6 check: a doubles chart and a four-player chart in headless Chrome

Ran on branch `doubles-viewer` (5eff0ee at the time of the check), serving the
built viewer with `scripts/devserve.py` and driving `google-chrome
--headless=new` (port 9222) with `scripts/cdp.mjs`. Desktop viewport
1400×900; phone viewport 390×844 with `mobile: true` and
`Emulation.setTouchEmulationEnabled`. Screenshots (`Page.captureScreenshot`)
were written to a scratch dir and inspected with `Read`; they are not
committed.

## Inputs

- Doubles timeline: copied from
  `.../scratchpad/p3r/p3r-8J3r5FhFFd4.json` to
  `.../scratchpad/viewer-check/doubles-timeline.json`. `format.name` =
  `doubles`, `format.check.looks_like` = `doubles` (so no warning is
  expected), `power_plays` = `{red: [5], yellow: [7]}`.
- Four-player timeline: copied from the main checkout's `out/timeline.json`
  (read-only source, never touched) to
  `.../scratchpad/viewer-check/fourplayer-timeline.json`.

## Links checked

Doubles (`devserve` on `http://127.0.0.1:53415`):
- edit `/c/42t7LcZwA7RSmNXndVCjOs/`
- view-only (read-only link) `/s/3pF1f1TNLJA1qsUMGMp35K/`

Four-player (`devserve` on `http://127.0.0.1:37717`):
- edit `/c/5A0fJ1EFa6tkFdAqbN5jqa/`
- view-only `/s/0qDhRqkBcK7ipXrH3plhZg/` (badge/no-badge sanity only)

## Doubles chart — desktop (1400×900)

| # | Item | Result | What was seen |
|---|------|--------|---------------|
| 1 | Rock labels read "B's second rock" style | PASS | `/c/` and `/s/`, end 1 shot 5: `#label` and each `.shot` `title` read `"1st end, B's second rock"`. Full sequence for one team across an end: A's first, B's first, B's second, B's third, A's second — matches the throw table (1,2,2,2,1). |
| 2 | Detail thrower row reads "Player B (rock 2 of 3)" | PASS | `/c/` and `/s/`, end 1 shot 5: `#detail` "Thrower" row = `"Player B (rock 2 of 3)"` exactly. |
| 3 | Power-play badge on ends 5 (red) and 7 (yellow); no badge elsewhere | PASS | End 5: `.wpp` = `"power play · red, left"`. End 7: `.wpp` = `"power play · yellow, right"`. Ends 1, 2 (and every other end in the Timing list): no `.wpp` element. Checked on `/c/` and `/s/`, desktop and phone (Timing tab), see screenshots below. |
| 4 | `/c/` End box shows two swap checkboxes; ticking "red swapped roles" makes red's rock 1 read "B's first rock"; report moves red's counts between A and B; unticking reverts | PASS | Before: `redRock1 title = "1st end, A's first rock"`, checkboxes both unchecked, report red card: A=14 thrown, B=21 thrown (yellow unchanged at 14/21 throughout). After ticking red: `redRock1 title = "1st end, B's first rock"`, yellow's rock 1 unaffected, report red card: A=15, B=20 (one shot moved). After unticking: back to `"A's first rock"`, checkbox false, (report not re-checked after untick but the label revert confirms the swap state reverted). |
| 5 | `/s/` (read-only) shows no swap checkboxes | PASS | `#endBox input[type=checkbox]` count = 0 on `/s/`; same End box otherwise shows the hammer line and badge. |
| 6 | Rock 1's house shows the two placed stones | PASS | At `/c/` `#e=1&s=1` (fresh open, first delivered shot), `#house .stone` had 3 groups — red, yellow and the neutral/granite body of the just-delivered stone — i.e. the two placed rocks (one per team) plus the just-thrown one. Detail agreed: "Stones: 3", "House: 1 stone in, 1 stone moved". Screenshot shows a red stone near the 12 o'clock rings and a second stone near the button, consistent with two placed rocks. |
| 7 | Report headings read "A · 1st & 5th" and "B · 2nd–4th" | PASS | Both team cards (`/c/` and `/s/`) render `<h3>` "A · 1ST & 5TH" and "B · 2ND-4TH" (CSS-uppercased; underlying text matches `roleText()`'s "1st & 5th" / "2nd–4th"). |
| 8 | No format warning | PASS | `#formatWarning` absent throughout (`format.check.looks_like` = `doubles`, matching `format.name`). |

Screenshots (scratch, not committed):
`doubles-c-desktop-e1s1.png`, `doubles-c-desktop-e1s5-Bsecond.png`,
`doubles-c-desktop-e5-powerplay-red.png`, `doubles-c-desktop-e7-powerplay-yellow.png`,
`doubles-c-desktop-e1s1-house-closeup.png`, `doubles-c-desktop-report-before-swap.png`,
`doubles-c-desktop-e1-after-swap.png`, `doubles-c-desktop-report-after-swap.png`,
`doubles-c-desktop-e1-after-unswap.png`, `doubles-c-desktop-report-headings.png`,
`doubles-s-desktop-e1s1.png`, `doubles-s-desktop-e5-powerplay.png`.

## Doubles chart — phone (390×844)

| # | Item | Result | What was seen |
|---|------|--------|---------------|
| 1 | End box + badge present on `/c/` at phone width | PASS | `#e=5&s=1`: `.wpp` = `"power play · red, left"`, 2 swap checkboxes present, labels correct. Screenshot `doubles-c-phone-e5-endbox.png`. |
| 2 | Phone watch view's end head shows the badge (`/s/`, Timing tab) | PASS | `#tab=timing&e=5&s=1`: the End 5 `.tend` row reads `"End 5 · red has hammer · power play · red, left · 2–8"` (badge rendered as a pill after the hammer line, before the score). End 7's row shows `"power play · yellow, right"`. Every other end's `.tend` has no `.wpp`. Screenshot `doubles-s-phone-timing-e5.png`. |
| 3 | Read-only phone: no swap checkboxes, labels/detail still correct | PASS | House tab and Detail tab (`#tab=house`, `#tab=detail`) checked at `#e=1&s=5` / `#e=5&s=1`: Detail row read `"Player B (rock 2 of 3)"`; no swap checkboxes anywhere in `/s/`. |

Screenshots: `doubles-c-phone-e5-powerplay-top.png`, `doubles-c-phone-e5-endbox.png`,
`doubles-s-phone-timing-e5.png`, `doubles-s-phone-house-e5.png`, `doubles-s-phone-detail-e1s5.png`.

## Four-player chart — desktop (1400×900) and phone (390×844)

| # | Item | Result | What was seen |
|---|------|--------|---------------|
| 1 | Labels read "second's first rock" | PASS | End 1 shot 5 (the throw-table index for a fours team's 3rd overall delivered rock is "second's first rock", *not* shot 2 — table `[1,1,2,2,3,3,4,4]` means k=1,2 are both "lead"): `#label` = `"1st end, second's first rock"`. |
| 2 | Thrower row reads "second (rock 1)" | PASS | `#detail` "Thrower" = `"second (rock 1)"` exactly (lower-case position, no "Player" prefix, no swappable "of N" suffix — as `throwerText()` gives for a non-swappable format). |
| 3 | Report headings lead/second/third/skip | PASS | Both team cards show plain `<h3>` "LEAD", "SECOND", "THIRD", "SKIP" (no role text / no swap dot). |
| 4 | No End box | PASS | `document.getElementById('endBox')` is `null` throughout (`format.swappable` is false for `FOURS`, so `ChartPanel` never mounts it). |
| 5 | No power-play badge | PASS | `document.querySelectorAll('.wpp').length === 0` on `/c/` (desktop, phone) and on `/s/` phone Timing tab, across all 7 ends. |
| 6 | No format warning | PASS | `#formatWarning` absent (this timeline carries no `format` block at all, so `formatOf()` falls back to `FOURS` and `formatWarning()` finds no `check` to compare against). |

Screenshots: `fours-c-desktop-e1s1.png`, `fours-c-desktop-e1s2.png`,
`fours-c-desktop-e1s5-second-first.png`, `fours-c-desktop-report.png`,
`fours-c-phone-e1s5.png`, `fours-s-phone-timing.png`.

## Anything odd

- Nothing on the checklist failed. All 20 item/mode combinations above passed.
- The overall shot number that lands on a given labelled rock is not what a
  naive per-team count would suggest, for either format: for doubles, end 1
  shot 5 (not shot 3) is "B's second rock", because shot numbers interleave
  both colours (shots 1,2 are both team's rock 1; shots 3,4 both team's rock
  2; etc.). For fours, end 1 shot 5 (not shot 2) is "second's first rock",
  because the fours throw table pairs k=1,2 to "lead" before k=3,4 pairs to
  "second". This is correct behaviour (matches `frontend/core/format.mjs`
  exactly, checked by reading the source) — noted here only because it would
  be easy for a future check to pick the wrong shot number and see a
  false failure.
- Corrected after the final review: on the `/c/` editing link at phone
  width, the End box sat partly under the fixed bottom transport bar
  ("← Replay ✔ Mark charted →") and overlapped the "yellow swapped roles"
  label. This was first written up here as a pre-existing quirk of the
  fixed-bar layout, present for four-player charts too. It was not: it was
  the new End box itself, rendering into the default peek sheet's 84 px of
  bottom padding -- the space the transport bar sits over -- so a tap beside
  ←, Replay, Mark charted or → landed on a swap label and saved a swap. A
  four-player chart has nothing in that space. Fixed by F2, which hides the
  End box in the peek sheet as `#typeList`, `#missReason` and `#note` are
  hidden. The open sheet still shows it -- and at its default scroll the
  End box still reaches under the bar; see "Final-review fixes" below,
  item 3.
- The hidden (display:none) `#endBox` inside `ChartPanel` is still present in
  the DOM under `/s/` and `/g/`-style read-only phone layouts even though the
  visible surface is `#watch`; a DOM query for `.wpp` on a read-only phone
  page therefore returns one extra (invisible) match beyond the one visible
  in the Timing tab's `EndHead`. Not a bug — CSS hides the whole
  `main > *:not(#playCard)` subtree in that mode — but worth knowing so a
  future count-based check doesn't misread it as a duplicate badge.

## Cleanup

Both `devserve.py` instances and the headless Chrome (port 9222, user-data-dir
under the scratch `viewer-check` dir) started for this check were killed
after the check. No other sessions' processes were touched (verified by `ps`
before killing: only PIDs whose command line referenced this task's own
scratch paths were matched).

## Final-review fixes

Re-checked after the final-review fix wave (F1-F6; head dd6dd2c at the time),
with the same tools: `scripts/devserve.py` from the `doubles-viewer`
worktree, and `/opt/google/chrome/chrome --headless=new` on port 9333 with a
fresh `--user-data-dir` per chart, driven by `scripts/cdp.mjs`. Desktop
1400×900; phone 390×844 with `mobile: true` and
`Emulation.setTouchEmulationEnabled`. Screenshots stayed in the scratch dir.

Inputs (copies in `.../scratchpad/v1/`):
- `doubles.json` -- the unmodified doubles timeline (`p3r-8J3r5FhFFd4.json`).
- `doubles-warn.json` -- (a): that copy with `format.check.looks_like = "fours"`.
- `fours.json` -- the main checkout's `out/timeline.json`, unmodified (2 games).
- `fours-warn.json` -- (b): that copy with a top-level
  `"format_warning": "analysed as fours, but the ends look like doubles: …"`.

Every check used the `/c/` edit link each devserve printed.

| # | Check | Result | Measured |
|---|-------|--------|----------|
| 1a | Desktop, (a): `#formatWarning` exists, top < 900 | PASS | top 526.6, bottom 579.6 (it was at y≈1099-1152 before F1). Parent `#flags`, and its first child. Text: "This game was analysed as doubles, but its ends look like a four-player game (a median of 10 rocks offered across 7 ends). If it is, resubmit it as four-player." |
| 1b | Desktop, (b): same | PASS | top 535.0, bottom 568.5. Parent `#flags`, first child. Text is the document's `format_warning` verbatim. Video 81-445.5 and house card 68-729.4, the same as the four-player chart with no warning. |
| 2a | Phone `/c/`, (a): `#video.bottom <= #houseCard.top` | PASS | video 48-267.4, house card top 267.4 (0 px apart, no overlap). The warning is in the `#flags` overlay at 276.4-348.9, over the top 81.5 px of the house card. |
| 2b | Phone `/c/`, (b): same | PASS | video bottom 267.4, house card top 267.4. Warning at 276.4-309.9, in the `#flags` overlay. |
| 3 | Phone `/c/`, unmodified doubles chart, default peek | PASS | `body[data-sheet]` = `peek`, and `#endBox` has computed `display: none`. Transport bar 772-820 (x 12-378). None of 29 `elementFromPoint` probes hit the End box: 21 points 2, 8 and 16 px below the bar at x = 4, 30, 100, 195, 290, 360 and 386; the bar's left and right sides and two of its corners; the three gaps between buttons; and 4 px above the bar. Each hit `#chart` or `#transport`. |
| 3 | Phone, the sheet opened (`#sheetHandle`): End box present | PASS | `data-sheet` = `open`, `#endBox` `display: block`, two swap labels. |
| 3 | Phone, open sheet: the End box's labels end above the transport bar | **FAIL at the default scroll**; PASS scrolled to the end | Just after opening (`#chart.scrollTop` 0; scrollHeight 657, clientHeight 576), the labels span 727.9-780.4 (red) and 784.4-836.9 (yellow). The bar's top is at 772, so the labels end 64.9 px below it. 15 of 28 probes hit the yellow swap label: every one 2, 8 or 16 px below the bar at x = 30-360. Scrolled to the end (`scrollTop` 81), the labels span 646.9-699.4 and 703.4-755.9, 16.1 px above the bar. For comparison, the four-player open sheet does not scroll (scrollHeight 576 = clientHeight 576). Its lowest control is `#placeStones`, which ends at 604.4, and no probe near the bar hit anything but `#chart`/`#transport`. So F2 fixed the peek, but in the open sheet the End box's 135 px are what make it overflow, and at the default scroll the yellow swap label sits under and just below the bar. Reported as found, not tuned. |
| 4 | Four-player, no warning: no warning, no End box | PASS | Desktop and phone: `#formatWarning` absent, `#endBox` absent (`getElementById` null). `#flags` is empty: 0 px tall on the desktop, `display: none` on the phone. |

Also checked:
- The short-screen query (`max-width: 640px` and `max-height: 520px`, tried
  at 600×400 and 390×500) has no sheet: `#chart` is `position: static`, and
  the End box sits in the scrolling page (top 2272.6 of 2601 px, and 1887.7
  of 2216). `data-sheet` still says `peek` there, but the peek rules are
  scoped to the tall-phone query, so they do nothing. F2 needs no rule for it.
- All four `devserve.py` instances and each headless Chrome were killed by
  PID after use. `ps` showed none left, and nothing else was touched.
