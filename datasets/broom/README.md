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
