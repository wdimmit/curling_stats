# Mixed doubles, phase 0: what the footage shows

Findings for the spec's phase 0 gate (`2026-09-25-mixed-doubles-design.md`).
Footage: four games from the Thursday Mixed Doubles playlist, cached on the
worker under `/data/wdd/curling-cache/doubles/` (a sub-root the worker's
pruner never scans). Baselines were run with the unchanged `main` pipeline
(`2026.09.25+ds15a+ds13b+broom2+line`) inside the worker image.

## brMO74e6ZZU (3/19, sheet 1)

**Shape of the stream.** The sheet sits empty for 9 minutes, and play starts
at about 10:00 with no warm-up ends. The game was conceded after 4 ends: the
board shows yellow 2, 3, 2 in ends 1–3, and the sheet is empty from about
52:00. From 1:11:00 a single player slides stones, and segmentation reads that
as a second, 2-end "game".

**How the stones are placed.**
- One player at the destination end pushes two *spare* stones in **from
  behind the house**. Each team has 8 stones and uses 6, so the spares wait at
  the far end.
- The guard travels up-sheet, through the house, to its spot. The house stone
  is set just behind the button.
- Placement takes about 20–25 s (end 2: 1153–1175 s). It overlaps the clearing
  of the previous end, which happens in the other panel.
- Rock 1 is released 20–30 s after the arrangement settles.
- Nothing about placement became a delivery candidate. A stone moving up-sheet
  never qualifies, so the fit saw no phantoms from it.
- **The arrangement can change during placement.** End 4 (a power play) was
  first set up normally, and then the house stone was moved out to the side.
  The placement read must take the arrangement *just before rock 1*, not the
  first stable one.

**Where the stones sit** (read off rock 1's `house_delta`, which "adds" them
because no seed exists yet):

| End | House stone | Guard | Hammer (house colour) | First thrower |
|---|---|---|---|---|
| 1 | yellow (−0.01, −0.51) | red (−0.02, 3.37) | yellow | red ✓ |
| 2 | red (0.02, −0.51) | yellow (0.00, 3.61) | red | yellow ✓ |
| 3 | red (−0.02, −0.53) | yellow (−0.02, 3.36) | red | yellow ✓ |
| 4 (power play) | red (−1.28, +0.16) | yellow (−0.93, 3.56) | red | yellow ✓ |

- The house stone sits about 5 cm deeper than the WCF centre of −0.465.
- The guard was **inside the panel's view** in every end.
- Placement agrees with the hammer in 4 of 4 ends.

**What today's pipeline got wrong.**
- The 16-rock "practice signature" withheld the board's scores for ends 1–3.
- Rock 1's house diff counts both placed stones as added.
- End 4 kept an 11th "shot": a `left-view` stone 12 s after rock 10, while
  the players were clearing. The 16-rock cap left room for it.
- The post-game practice became a second game.
- **No target broom on any of the 45 shots**, so no aim line was measured.

**The broom, checked.** The destination-facing camera 0.5 s before each rock's
tee crossing (41 rocks, `tee_frames.jpg`) shows **nobody holding a broom in
the house** on nearly every rock. The team not throwing waits behind the back
line, standing or on the bench. The partner is at the delivery end, sweeping.
One rock (end 3, rock 2) has someone standing in the house with a broom. So
broom2 returning nothing is right, not a miss.

The consequence goes past the broom. `linetime.time_lines` skips any shot
without a broom (`linetime.py:369`), and the viewer's hack call
(`playerHacks`, from `line.start`) and curl-to-contact both read `line`. As
things stand, a doubles chart has no aim line, no hack call and no curl.
`linetime.measure` could run without a broom: the start, the fitted line, the
path and the curl need none, and only `at_hog.offset_m`, `at_broom` and
`side` do. That is a phase 6 change. Gated to doubles, it leaves four-player
output alone; ungated, it would also give lines to the ~2% of hosted
four-player shots that have no broom.

## n7ifEk4Zfl8 (3/26, sheet 4) and 8J3r5FhFFd4 (4/2, sheet 3)

**Length.** n7if ran 6 ends (the board posts 5) and 8J3r ran 7 (the board
posts 6, including a blank end 1). None of the three games so far reached 8
ends, so these league games look time-limited to one 100-minute slot.

**Placement positions**, across 13 ends. The house stone is at y ≈ −0.49,
x ≈ 0. The centre guard is at y ≈ 3.3–3.5. Every guard was inside the panel.

**Power plays are common.** Each team in both games used one, in ends 4, 5,
5 and 7.
- The house stone sits at x ≈ ±1.22–1.33, y ≈ +0.15–0.20.
- The guard sits on the same side at x ≈ ±0.85–0.93, y ≈ 3.3–3.4.

**Hammer.** Placement agrees with it in every end. That includes 8J3r's blank
end 1, after which the hammer passed to yellow, as R17 says it should.

**Placement can become a phantom delivery.** In n7if's end 1, a player walked
up the centre carrying the stones and set them down (152–184 s), more than 3
minutes before rock 1. The pipeline took the red guard as a `house-appear`
delivery (175 s) and kept it, and the end grew to 16 slots with blanks. Every
end after the first was placed by pushing from behind the house, which
produced no phantoms. So the exclusion step is needed, but only against what
actually settles on the placed stones' spots before rock 1.

**Pre-game and post-end junk.**
- n7if's end 1 also swallowed a stray stone parked at the side of the house
  (72–144 s) and a gap-search find at 74 s.
- n7if's end 6 kept two clearing stones after rock 10 (`house-remove` and
  `house-add`, no release), which gave 11 shots. A 10-rock cap drops them.
- 8J3r was clean: 10 of 10 deliveries offered in every end.

**Board.** 8J3r's six posted scores were all withheld by the 16-rock practice
guard, as were brMO's three. n7if's were not, because its end 1 reached 16
slots.

## ih59IKFUHXk (2/19, sheet 5)

6 ends, of which the board posts 5. Placement agrees with the hammer in every
end, and end 6 is yellow's power play. End 6 kept a clearing stone after
rock 10 (`left-view`, no release).

**End 1 is messy again, and this game shows why.**
- The yellow house stone was **slid the length of the sheet**: a release at
  150 s, arriving at (0.01, −0.50). The red guard arrived at 262 s with no
  release.
- Only after that do ten alternating rocks begin, red first, which matches
  yellow holding the house stone.
- Today's pipeline kept both placed stones as rocks 1–2. The house-size fill
  then inserted three blanks after them, because the placed stones made the
  house look fuller than the rock count allowed (the bug the design predicted).

**Why the first end.** At the start of a game every stone is at the delivery
end, so the first end's placed stones must travel down the sheet, slid (ih59)
or carried (n7if). In later ends the two spare stones per team are already at
the far end and are pushed in from behind (brMO), which never looks like a
delivery.

**Game lengths:** 4 (conceded), 6, 7, 6. No game reached 8 ends.

## Design consequences (for the phase 3 plan)

1. **Every delivery of a doubles end comes after its placement is complete.**
   Find `t_placed`: the first moment the destination house holds a placement
   pattern, one stone of each colour, and stays still for a few seconds. Then
   drop every candidate from before it, arrivals and releases alike. That one
   rule covers slid placement stones (ih59), carried ones (n7if) and pre-game
   junk (n7if's parked stone and 74 s find).
2. **The placement itself is the house just before rock 1.** Take it from the
   still house between `t_placed` and the first kept delivery. A power play
   can be set up in two steps (brMO end 4), so do not freeze the first
   pattern. Gates, from 17 observed ends:
   - house stone: |x| ≤ 0.25, |y + 0.49| ≤ 0.25;
   - power-play house stone: ||x| − 1.27| ≤ 0.25, |y − 0.17| ≤ 0.25;
   - guard: y 2.8–4.2, with |x| ≤ 0.35 for a normal placement, or
     ||x| − 0.9| ≤ 0.3 on the power-play side.
   Seed rock 1's house with the placed stones, and give `_fill_short_end` a
   base of 2.
3. If no pattern is found, fall back to today's behaviour and record
   `placement: null`.
4. Once the counts come from the format, the 10-rock cap and the
   `shots_expected` = 10 practice guard fix the post-end junk (seen in 3 of 4
   games) and the withheld board scores. (Both are in the phases 1–2 plan.)
5. The broom and line consequences stand as written above for brMO.
6. Acceptance data. The four baselines give 23 ends where placement, hammer
   and the board can be checked against each other. 8J3r's 7 ends of clean
   10-of-10 deliveries are the easy check; the four first ends are the hard
   one.
