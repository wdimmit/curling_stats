# ds15 — arriving stones the overhead detector found late

234 frames of the destination panel, one or two per arriving rock, taken where
ds11a's track began past the far hog line: 75 rocks whose crossing had to be
reached back for, and 47 first seen too far past the paint to reach for at all.
Three locally cached games: AEqLTgM25Tc (ds11 train), VXU9xwmugRg, hOKZoeJNTpM.
Each frame sits at the estimated crossing, or midway from there to ds11a's first
sighting. They were pre-labelled by ds11a at conf 0.05 and reviewed by hand
(87 boxes added, 92 rejected, one frame removed). The target stone is labelled
in 210 of the 234 frames.

| file | what it is |
|---|---|
| `manifest.json` | every rock, its estimate and its frames (built by `scripts/ds15/build_farhog.py`) |
| `edits/*.json` | the hand review |
| `excluded.json` | frames removed by the reviewer |

The images rebuild from the cached videos with the builder. Training uses
`scripts/ds15/make_folds.py` and `train_folds.sh` (ds11a's recipe, verbatim),
and scoring uses `eval_heldout.py`.

## Result, 2026-09-23 — leave one game out

Each fold trains on ds11 train plus the other two games' ds15 frames, and is
scored only on the game it never saw.

**The late stone, at the pipeline's confidence (0.30, imgsz 448), on held-out frames**

| game | targets | ds11a | fold | predictions matching no label (ds11a → fold) |
|---|---|---|---|---|
| AEqLTgM25Tc | 58 | 12 | 38 | 12 → 14 |
| VXU9xwmugRg | 56 | 21 | 51 | 1 → 1 |
| hOKZoeJNTpM | 96 | 38 | 83 | 0 → 5 |
| all | 210 | 71 (34%) | 172 (82%) | 13 → 20 |

**Benchmarks do not move.** On ds11 val, mAP50 is 0.993 for every model. On ds12 (a second league, never trained on), mAP50 and mAP50-95 are:

| model | ds12 mAP50 | ds12 mAP50-95 |
|---|---|---|
| ds11a | 0.992 | 0.944 |
| folds | 0.988–0.994 | 0.940–0.941 |
| `ds15-all` | 0.988 | 0.939 |

**Whole-game replays**, each game with the fold model that never saw it, on the
current pipeline:

| | observed | reached for | none | splits |
|---|---|---|---|---|
| ds11a | 207 | 75 | 47 | 275 / 329 |
| fold | 252 | 59 | 18 | 300 / 329 |

Scored against the 62 hand-marked destination crossings, with rocks matched by
time rather than shot number:

| | observed crossings | within 0.10 s | worst | reached for | none |
|---|---|---|---|---|---|
| ds11a | 51, median 0.048 s | 41 | 0.393 s | 8, median 0.051 s | 3 |
| fold | 54, median 0.059 s | 47 | 0.174 s | 8, median 0.107 s | 0 |

**The shot list moved in three of 22 ends.**
- AEqL end 1 went from 10 shots to 11, and end 5 from 16 to 15.
- hOKZ end 3 lost its first shot (the yellow arriving at 1961 s). It gained a spurious one at 2764.7 s, with no release and out of colour order.

A different detector changes more than the far edge: it changes releases, pairings and shot numbering. That has to be resolved before any of these models replaces ds11a.

On VXU9, 12 rocks moved from observed to reached-for with reach ≤ 0.02 u and
the same first sighting. The fold model places the stone's centre a hair
nearer the house, which is harmless.

## Result, 2026-09-23: sixteen games, after the rule fixes

Every game on the hosted service was replayed three ways and compared end by end, with rocks matched by arrival or release time, never by shot number (`scripts/ds15/compare_shotlists.py`). That is 108 ends, 16 games, with 13 of the games never seen in training.
- **Published:** the published run, ds11a on the old rules.
- **ds11a on the new rules.**
- **`ds15-all` on the new rules.**

The shot-list faults a better detector exposed were in the rules, not the model. Five changes fixed them:

1. `fit.drop_clearing` drops stones moved while the house is cleared after the last shot. A stone first seen at the far edge is exempt.
2. A rest point must still hold `REST_CONFIRM_S` later. At the far edge a crawling stone passes the speed test.
3. A one-sample track's gate is capped (`BOOTSTRAP_MAX_M`), so a one-frame phantom can no longer claim an arrival.
4. An arrival paired with its release wins a tie in `fit_end`.
5. (2) also fixed AEqL end 5, where a false rest at a collision refused the whole track.

**Published vs ds11a on the new rules: 8 of 108 ends differ, and all 8 are improvements.**
- In six, a rock paired with its release replaces an unpaired account of it.
- One drops a clearing stone.
- One recovers a missing red (_OGo end 6, 15 → 16).
- In -f3R end 1, the new rules restore the end's first shot (paired, 170.3 s) that a clearing stone (967.5 s) had displaced.

**ds11a vs ds15-all on the new rules: 3 of 108 ends differ.**
- -f3R end 4: ds15-all is better (paired).
- VXU9 game 2 end 3: ds11a is better; ds15-all never produced the paired arrival.
- AEqL end 2: neutral, the same rock first seen 3.7 s apart.

**The far hog line on the 13 held-out games (1,389 rocks):**

| | observed | reached for | none | splits |
|---|---|---|---|---|
| ds11a | 967 | 258 | 164 | 1,166 (84%) |
| ds15-all | 1,222 | 125 | 42 | 1,282 (92%) |
