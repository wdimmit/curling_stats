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

Crops are the full view width, from `tee_row − 130` down to `row_for(3.0) + 15`,
so the skip's legs and the shaft are above the house and 3 m of ice in front of
the tee is below it. `manifest-<wave>.json` carries each frame's view calibration -- the depth rows
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
