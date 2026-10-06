# broom — broom heads on the ice, seen by the long camera

Frames of the **destination-facing long camera** (`CAMERA_FOR[end.house]`, the
camera at the throwing end looking at the house being played to), cut at
`t_tee − 1.0` and `t_tee − 0.3` for every shot. That window is the pipeline's:
the skip's target broom is read in the second before the thrower crosses the
tee. Phase 0 of `docs/superpowers/specs/2026-09-23-target-broom-design.md`
found the pad down and still across that window on 45 of 49 shots.

**Labelling rule:** box **every broom head resting on the ice** — the skip's and
anyone else's. A broom held in the air is not boxed. A reviewed frame with no
box says no broom was down. The pass that uses the model picks the target by
position and stillness, so the labels do not try to.

Crops are the full view width, from `tee_row − 130` down past the hog line
(`row_for(6.401) + 15`): the skip's legs and the shaft are above the house, and
a skip calling a guard from in front of the house is still in frame.

The first labelling pass (`wave1`) was cut to only 3 m in front of the tee,
which clipped exactly that case (VXU9 e6 s6, pad ~4 m up-sheet). `wave1b` is the
same 222 frames on the taller crop. Its boxes are wave 1's, restated by
`scripts/broom/recrop_edits.py`: the top row did not move, so every box keeps
its pixels. The original session file stays untouched beside the restated one.
**Train from `wave1b`.** `manifest-<wave>.json` carries each frame's view calibration -- the depth rows
and the lateral scale (`centre_col`, `lat_px_per_m_at_tee`), fitted by the
harvest from 24 frames when the timeline predates lateral calibration -- and its
crop offset. That is what maps a box back to the house: rebuild the `SideView`
from the row and call `to_house` at the box's bottom edge, `crop_top` added.

## Where things are

| what | where |
| --- | --- |
| images, `items.json`, the page | `~/curling-work/broom/<wave>/` (not committed) |
| every saved labelling session | `datasets/broom/edits/` — **commit each one as soon as it is saved** |
| frame manifests | `datasets/broom/manifest-<wave>.json` |
| training trees | the worker, `/data/wdd/curling/broom_trees/` |

## Commands

```bash
# cut a wave (see the script's docstring)
./.venv/bin/python scripts/broom/harvest.py --timeline <timeline.json> \
    --video <video.mp4> --out ~/curling-work/broom/wave1 \
    --manifest datasets/broom/manifest-wave1.json --scope broom:wave1

# label it: SAM behind the page, on 127.0.0.1:8777 (ssh -L 8777:127.0.0.1:8777 to reach it)
./.venv/bin/python scripts/broom/serve.py ~/curling-work/broom/wave1 \
    --weights ~/curling-work/sam/sam2.1_b.pt

# after each sitting
cp ~/curling-work/broom/wave1/edits/*.json datasets/broom/edits/ && git add datasets/broom/edits
```

## Results

Round 1 was VXU9 (`wave1b`), labelled from scratch. Round 2 was AEqL
(`wave2-aeql`) and hOKZ (`wave2-hokz`), corrected from v0's proposals; hOKZ is
the held-out game. The model trained on VXU9 + AEqL scores **127/127 hOKZ
shots within 0.30 m** of the boxed pad (median 0.029 m, max 0.134 m) with 0
wild markers. `weights/broom1.pt` is trained on all three games
(`tree-all`: VXU9 train, AEqL and hOKZ shot-split 85/15). The full table and
caveats are in the spec's "Phase 3 result".

```bash
# score a model on a held-out wave
./.venv/bin/python scripts/broom/eval_heldout.py --weights <best.pt> --video <video.mp4> \
    --manifest datasets/broom/manifest-wave2-hokz.json --edits 'datasets/broom/edits/broom-wave2-hokz-*.json'
```

**Round 3 / broom2 (2026-09-24).** `wave3` is the 274 hosted shots broom1 left
without a broom, from six videos, which were mostly red pads. It was harvested
on the worker with `--without-broom` and pre-labelled by broom1; 163 frames
were reviewed. `weights/broom2.pt` is trained on all four waves. Coverage on
the weak videos rose from 55-67% to 96-99%; the spec's "Round 3 and broom2"
has the checks.

**Round 4 / broom3 (2026-09-30).** `wave4a` (sheets 3 and 5) and `wave4b` (sheets 1,
2 and 4) are every shot of the ten 2026-09-28 Monday games that broom2 left
without a broom or held below 0.7 -- 117 shots, 232 frames, cut on the worker
(`merge_waves.py` joins the per-game harvests), pre-labelled by broom2 and all
reviewed: 224 boxes. The gap it is for: a navy pad held on sheet 5's green
12-foot ring, which broom2 scored 0 while it read the same pad on white ice at
0.7-0.85.

A check model held out sheet 2 and s_0vIn end 6; broom3 (`weights/broom3.pt`,
tree b3all, 157 epochs, val mAP50 0.965) trains on all four rounds. Probed from
pre-cut windows (`~/curling-work/broom/b3eval`) over 19 hosted games:

| model | all 19 games | 9 games in no wave | lost vs broom2 (9 games) | s_0vIn (navy pad) |
| --- | --- | --- | --- | --- |
| broom2 | 94.7% | 94.8% | -- | 83.3% |
| check | 96.6% | 96.0% | 10 | 99.0% |
| broom3 | 97.1% | 96.9% | 5 | 99.0% |

The check model found all five held-out navy shots (broom2 none). About 2% of
markers move 0.3-0.5 m in depth, the box's bottom edge sitting 2-3 px lower on
the same pad; lateral position is unchanged.

**Rounds 5-6 / broom4 (2026-10-06).** `wave5b` is the 10/01 Thursday Morning shots
on sheets 1, 4 and 5 that broom3 left without a broom or held below 0.7: 52 frames,
all reviewed, 51 boxes. (`wave5a`, sheets 2-3, is cut but not reviewed: it is
nearly all one player holding the pad flat on the ice, left for later.) `wave6` is
hard negatives. `scripts/broom/mine.py` read every shot of 16 hosted games
(2026-09-27..10-04) as broomtime does and kept the shots where broom3 held a pad
more than 1 m off the thrown line that was also weak, behind the tee or not
alone, or where boxes under the pipeline's floor sat behind the tee (flag f_1Np1:
a doubles player's red shoes at the back of the house, read as the target four
times). `harvest.py --only --offsets` cut those 15 shots at five moments each: 75
frames, all reviewed; of broom3's 115 proposals the review deleted 63 and added 5,
leaving 27 frames empty.

broom4 (`weights/broom4.pt`, tree b4all, 153 epochs, val mAP50 0.963) is broom3's
recipe on every round but 5a. A check model held out wave 6's iDZ-mFORtG0 (the
f_1Np1 game) and wave 5b's I4qVxXm1tnY. On those 43 frames broom3 scored P 0.44 /
R 0.57 / mAP50 0.28 and the check model 0.94 / 0.74 / 0.72; in that game it held
none of the three shoe targets and kept all 18 real pads. Over the 19 b3eval games
(`probe_b4.py`, `compare_b4.py`):

| model | coverage | shoe-like held pads | picks moved across (>0.3 m in x) / new / lost |
| --- | --- | --- | --- |
| broom3 | 97.4% | 8 | -- |
| broom4 | 97.2% | 2 | 7 / 3 / 6 |

Of the 30 picks that changed across the 19 games and the 16 mined ones, about 12
were wrong targets fixed (shoes, feet, a stone handle) and about 10 new pads
(upright brooms at the feet, kneeling skips); about 7 real pads were lost, all
brooms held upright at a standing player's feet -- wave 5a's gap -- and one new
wrong pick (09/27 S4 e4 r8, a red shoe at 0.35 nearer the tee than the real pad).

