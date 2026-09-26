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
- Minor, unrelated to doubles: on the `/c/` editing link at phone width,
  scrolling the sheet down to bring the End box into view puts it partly
  under the fixed bottom transport bar ("← Replay ✔ Mark charted →"), which
  overlaps the "yellow swapped roles" checkbox label. This is the existing
  fixed-bottom-bar phone layout (present for four-player charts too, just
  never scrolled that far since there is no End box to reveal it) rather than
  anything introduced by the doubles work, and it is not one of the checked
  items, so it is not scored as a failure — just flagged as something a
  later phone-layout pass may want to look at.
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
