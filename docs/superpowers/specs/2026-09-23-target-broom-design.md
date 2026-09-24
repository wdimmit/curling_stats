# Target broom: detect where the skip held it, draw it on the house

## Context

The viewer shows the house after each shot, with the delivered stone ringed and
its track, but not what was *asked for*. The skip holds a broom head on the ice
in the destination house as the thrower's target. Drawing it next to where the
stone finished shows the call and the result side by side.

**Decisions from the user:**
- Purpose: **show the call**. A stone-width error is fine, and there are no
  analytics.
- Rollout: **local first, then ship**.
- Viewer: a **broom-head glyph plus a faint dashed link to where the delivered
  stone finished**.
- Detector: a **separate single-class broom model**.
- Camera: the **long camera at the throwing end, looking toward the
  destination house**, not the overhead panel.
- Window: **the second or so before the throwing-end tee crossing**.
- Labels: **SAM click-to-box, as for the long-view rocks (ds13)**.

**Assumptions (correct me):**
- Only the current shot's broom is drawn.
- A shot with no confident broom shows none.
- Estimated tee crossings (`t_tee_estimated`, i.e. `t_enter − 16 s`) are used
  like observed ones (user). A miss there just means no marker.
- Labellers box **every broom head resting on the ice**, and the pass picks the
  target by position and stillness.
- On a draw the marker is the aim line, not the intended rest spot. The tooltip
  says "skip's broom" and nothing more.

**What exploration established**
- **Camera.** The camera that sees the destination house is
  `sideviews[hogtime.CAMERA_FOR[end.house]]` (`dest_camera` in the phase3
  caches). hogtime uses the other camera for the same end. Side views are read
  from the original video `path`, not the proxy, with
  `detect/longview.decode(video, rect, t0, t1, fps)`, which returns RGB, full
  height and view width.
- **Orientation.** The far hog line sits below the far house (nearer means a
  larger row). The view is not mirrored: image-left is the thrower's left (−x).
  That was checked by eye on two frames, and nothing asserts it yet.
- **Geometry today is depth only.** `geometry/sideview.py` `SideView(rect,
  tee_row, hog_row, d_m)` has `row_for(x_m)` and `metres_at(row)`, and no
  lateral mapping anywhere.
  - Scale through the house: about 11 rows/m (1 row ≈ 9 cm), and about 150 px/m
    laterally at the tee (1 px ≈ 7 mm).
  - The 12-ft ring is about 550 px wide × 41 rows deep: back line about tee−19
    rows, front edge about tee+22.
  - A 0.2 m broom head is about 30 px wide.
- **ds13's crop (`sidepool.band_crop`) starts at the far tee row.** It cuts off
  the back half of the house, and its frames come from the other camera after
  release, so none can be reused.
- **Clocks.** Tee crossings are read on the *throwing overhead panel*
  (`thinking.tee_crossing`). The side view's clock can be up to ~0.9 s off, and
  can step mid-video, against the panel it has been measured with
  (`~/curling-work/ds13/sync_verdicts.json`). The pair used here, the
  destination-facing camera against the throwing panel, has never been measured.
  VXU9 is in sync on both measured pairs.
- **SAM tool.** `train/boxedit.py` (page) and `train/segserve.py` (server,
  `ultralytics.SAM`).
  - The server ignores `cls`, and `score()` is stone-shaped: it needs a `geom`
    or it raises `KeyError`, and the page then falls back to a 52×22 box.
  - Only `+red R` and `+yellow Y` exist.
  - `sam2.1_b.pt` is gone from disk and would have to be re-downloaded (~160
    MB).
  - `cs labels --apply` drops boxedit's `boxes` (cli.py L376-392 calls
    `apply_edits` without `boxes=`). ds13 worked around that with a one-off
    script.
- **The pass joins the attach-only passes after the shot list is settled**
  (`analyze.py` ~L363, beside hogtime). It cannot add, drop or renumber a shot.
  The detection cache keys on `rocks.py`, `yolo.py` and `calibrate.py`, none of
  which this touches.
- **The viewer passes unknown per-shot keys straight through.** The only gate is
  the explicit shot dict in `timeline.build_end` (`timeline.py:98-164`).

## Phase 0 — Feasibility look (throwaway, gates everything else)

Scratchpad only, nothing committed. VXU9 is used because both of its measured
clock pairs are in sync.

1. For ~40 shots (from a current timeline's `t_tee_s`; include a handful whose
   tee crossing is estimated, to see whether the 1 s window still catches the
   hold), decode `CAMERA_FOR[end.house]` over `[t_tee−3, t_tee+1]` at 4
   fps. Crop rows `tee_row−160 … hog_row+20` and label each frame with time
   relative to `t_tee`. Read the strips and tabulate:
   - whether the pad is on the ice and still across `[t_tee−1, t_tee]`;
   - whether it is occluded by the thrower, sweepers or raised brooms near the
     camera;
   - the pad's size in px;
   - other broom heads on the ice in front of the back line.
2. **Prototype the lateral mapping** (below) and project the known resting
   stones (from the timeline's overhead `stones` for that end) into the side
   frame as circles. They must land on the stones. That confirms image-left =
   −x and the scale.
3. Repeat step 1 on ~10 AEqL shots, since AEqL's other pairs are 0.2–0.9 s off.
   The broom should still be down across the window.

**Output:** a findings table plus the crop rows and window constants.
**Stop and report** if fewer than ~60% of VXU9 shots show a still pad in the
window, or if the stone overlay misses. Otherwise present the findings and get
a go.

### Phase 0 findings (2026-09-23)

Read by eye from contact sheets of the destination-facing camera at t_tee −2.0,
−1.0, −0.5, 0.0 and +0.5 s. The timelines were the 2026.09.22 ones in
`~/curling-work/ds15/games/timelines/`.

| game | shots | pad on the ice, still across −2.0…+0.5 s | exceptions |
| --- | --- | --- | --- |
| VXU9xwmugRg | 37 (every 3rd shot) | 35 | 1 skip crouched mid-house with the broom across the knees, not on the ice; 1 unclear |
| AEqLTgM25Tc | 12 (every 8th shot) | 10–11 | 1 unclear (dark pad against the skip's shoes) |

- **The window is right.** Wherever a pad was down, it stayed still for the
  whole 2.5 s shown, so `[t_tee − 1.0, t_tee]` sits well inside the hold. That
  held on AEqL too, whose other camera pairs run 0.2–0.9 s out of step. No
  occlusion by the thrower or sweepers was seen, because they are below this
  crop at that moment.
- **Pads** are mostly yellow or orange, with some green, white or black. At
  full resolution one is about 30 px wide and 10 px tall, at the skip's feet,
  with the shaft rising to the hands. Colour is not a usable class signal.
- **Other brooms.** Sometimes a second broom rests on the ice near the
  sideline (VXU9 e5 s4) or is held up in the air. **Tie-break:** among
  clusters that pass `MIN_SEEN`, take the one seen in the most frames, then the
  one nearest the tee.
- **Lateral mapping works from paint alone.** A fixed greenness threshold along
  the tee row fails, because the ice swings −6…+5 and the far-side band is half
  as green. The fix is to find the two green bands, then place each band's
  edges at half its own peak. The 8-ft/12-ft ratio reads 0.625–0.635 against
  0.667 (edge erosion), so the scale uses the summed spans, (12-ft + 8-ft) /
  6.096 m:
  - VXU9 left camera: 148.5 px/m, centre col 390.1;
  - VXU9 right camera: 128.4 px/m, centre col 414.7.
- **Checked against the overhead camera** on VXU9 e2 s13, whose 5 stones were
  confirmed by drawing them on the overhead panel itself. Projected into the
  side view, each lands on its stone within a few px laterally. Its footprint
  lands within 1–3 rows (about 0.1–0.3 m) of the silhouette's lowest row,
  measured by differencing against a clean plate. From this angle a stone's
  body stands about 17 px *above* its footprint, because height is not
  foreshortened the way depth is. So the contact row is the bottom of the box,
  never its centre.
- The existing depth model matches the paint down the centre column to about 1
  row: back 12-ft, back 4-ft, button, front 4-ft and front 12-ft.
- **Crop for the detector:** rows `tee_row − 130 … row_for(6.401) + 15`, full
  view width, down past the hog line. Phase 0's pads all sat between about
  tee−20 and tee+30. Round 1 then found a skip calling a guard from in front of
  the house, with the pad about 4 m up-sheet, below a crop that stopped at 3 m
  (VXU9 e6 s6). The user says this happens occasionally.

## Phase 1 — Lateral calibration of the side view

In `geometry/sideview.py`, leave `solve()` and the tripwire untouched.
- `SideView` gains optional `centre_col: float | None = None` and
  `lat_px_per_m_at_tee: float | None = None`. The defaults keep all existing
  constructions valid.
- New `solve_lateral(plate, view)`. It reads green (G−(R+B)/2) along a few rows
  around `round(tee_row)` across the full width. The outermost crossings are the
  12-ft diameter (3.658 m), so their midpoint gives `centre_col` and their spacing
  gives the scale. It cross-checks the 8-ft pair (ratio 0.667) and refuses
  implausible fits the way `PLAUSIBLE_ROWS` does. It returns a new view via
  `dataclasses.replace`.
- `lateral_px_per_m(row) = lat_at_tee·(row−yh)/(tee_row−yh)`, the same law as
  `stone_width_at`.
- `to_house(col, row) → (x, y)` with `y = metres_at(row)` and `x = (col −
  centre_col)/lateral_px_per_m(row)`. `to_image(x, y)` is its inverse, for
  tests and overlays.
- Hand-rebuilt views move to `dataclasses.replace` so the new fields survive:
  `longview.find_crossing` L364-365, `sidepool._shifted` L96, and
  `harvest/sideviews._view_to_json` / `_view_from_json` L89-100 (plus the JSON
  keys).
- `analyze.py`: call `solve_lateral` right after `sideview.solve` (~L209) and
  publish both fields in the calibration block (~L445-447). A lateral failure
  leaves that view without a broom and never fails the run.

## Phase 2 — Broom dataset, labelled with SAM

1. **Tooling.**
   - `boxedit.render(..., classes=None)`: `None` renders today's page
     byte-for-byte. A broom set passes `[{"name": "broom", "key": "b", "color":
     ...}]`, which generates the arm button, key, `.bx.c0` colour and status
     text.
   - `segserve`: pass `cls` through, and per class either use today's stone
     `score` or a broom rule. The broom rule is the top-scoring SAM mask
     containing the click, sanity-bounded by the perspective width
     (`k·(y1−yh)` scaled to a ~0.2 m head, tolerance about 0.3–2.5×).
   - Fix `cs labels --apply` to pass `boxes=edits.boxes`.
   - SAM weights go in `~/curling-work/sam/sam2.1_b.pt`. This is a ~160 MB
     download, and I'll ask before fetching it.
2. **`scripts/broom/harvest.py`**, modelled on `scripts/ds13/harvest_wave2.py`.
   - Input is the current timelines for VXU9, hOKZ and AEqL (with `t_tee_s`,
     `t_tee_estimated` and the calibration block).
   - For each shot that isn't `missing` it cuts `CAMERA_FOR[house]` at
     `t_tee−1.0` and `t_tee−0.3`, cropped to the Phase 0 rows, with stems from
     `sideframes.stem_for`.
   - It writes `images/` and `items.json` (with `geom` using the crop's
     `row_offset`) under `~/curling-work/broom/<wave>/`, never `/tmp`.
3. **Round 1:** VXU9 (~220 frames), opened empty. The user SAM-clicks every
   broom head on the ice and marks empty frames reviewed.
4. **Round 2:** after v0, AEqL and hOKZ go out with v0's proposals
   (`proposals=True`) for the user to correct.
5. Each export is committed to `datasets/broom/edits/` straight away. Commit
   the manifest too. Keep images in `~/curling-work/broom/tree` and on the
   worker at `/data/wdd/curling/broom_trees/`.

## Phase 3 — Train on the worker (RTX 3070, `administrator@10.0.0.182`)

`scripts/broom/train_on_worker.sh`, adapted from `scripts/ds13/train_on_worker.sh`.
- Settings: yolo11s, imgsz 800, epochs 300, patience 60, batch 8, seed 0,
  mosaic 1.0, close_mosaic 30, scale 0.4, degrees 0, fliplr 0.5 (mirror-safe,
  since the pad has no handedness), flipud 0, hsv 0.015/0.6/0.4.
- **v0:** VXU9 only, used to pre-label round 2.
- **Held-out eval:** train on VXU9 + AEqL and evaluate the *pass* on hOKZ, a
  different event.
- **Bar** (proposed), per shot, against the user's box on the `t_tee−0.3`
  frame, mapped through `to_house`:
  - at least 80% of shots with a broom boxed get a marker within 0.30 m;
  - at most 5% of markers are more than 0.60 m off, or sit on a shot with no
    broom boxed.
- **Ship candidate:** train on all three games and save as `weights/broom1.pt`.

### Phase 3 result (2026-09-23)

Labels, all boxed with SAM by the user and committed in `datasets/broom/edits/`:

| wave | frames | boxes | notes |
| --- | --- | --- | --- |
| VXU9 `wave1b` | 220 of 222 | 231 | round 1, from scratch; the crop reaches the hog line |
| AEqL `wave2-aeql` | 180 | 180 | round 2, corrected from v0's proposals (17 frames changed) |
| hOKZ `wave2-hokz` | 256 | 269 | round 2, corrected from v0 (6 changed); the held-out game |

Models: yolo11s on ds13b's recipe (`scripts/broom/train_on_worker.sh`).

| model | trained on | its val | pass on held-out hOKZ (128 shots) |
| --- | --- | --- | --- |
| v0 | VXU9 | mAP50 0.995 (VXU9 shots) | 127/127 within 0.30 m, 0 wild |
| heldout | VXU9 + AEqL | mAP50 0.995, mAP50-95 0.916 (AEqL shots) | **127/127 within 0.30 m, 0 wild**; error median 0.029 m, p90 0.076, max 0.134; every marker seen in 10/10 frames; the one shot with no pad boxed gets no marker |
| **broom1** (`weights/broom1.pt`) | VXU9 + AEqL + hOKZ | mAP50 0.974, mAP50-95 0.849, P 0.998, R 0.953 (80 AEqL+hOKZ val frames; the misses are second brooms resting by the sideline) | in-sample only: hOKZ 127/127, AEqL 87/88 within 0.30 m, 0 wild -- a no-regression check, not a measure |

**The bar is met**: at least 80% within 0.30 m and at most 5% wild.

Two caveats:
- **Anchoring.** Round-2 truth was corrected from v0's proposals, so it leans
  toward where v0 put the box. The error above is agreement between the pass
  (the median over the window) and the user's box on the `t_tee − 0.3` frame.
- **Mapping error is separate.** Pixel-to-metre mapping adds the ~0.1-0.3 m
  depth error measured in Phase 0.

**Attach-only was verified on VXU9** (`analyze --end 3000 --no-scoreboard`). A
no-broom run and a broom run agree on every field except `target_broom`,
`schema_version` and `processing_version`. A first run's end boundaries moved
by one keyframe (5 s), but a second no-broom run reproduces the broom run's
boundaries exactly, so that drift predates this work. With v0, 54 of 55 real
shots carry a broom (98%).

## Phase 4 — Pipeline pass

**`detect/broommodel.py`** (pattern: `detect/sidemodel.py`)
- `BroomFinder(weights, conf, imgsz=800)`. `find(frames, view)` crops the
  Phase 0 rows at full width, converts RGB to BGR, predicts, and returns boxes
  in view pixels with their confidence.

**`game/broomtime.py`** (pattern: `game/hogtime.py`)
- `TargetBroom(x_m, y_m, seen, confidence)`, a frozen dataclass.
- `window_for(shot)` returns `[t_tee − WINDOW_S, t_tee]`, with `WINDOW_S = 1.0`
  and `t_tee` from `thinking.tee_crossing(shot)`, observed or estimated. It
  returns None only when the shot is `missing` or has no tee crossing at all.
- `pick_target(samples, n_frames)` is pure over `(t, x_m, y_m, conf)`, where
  each box maps through `view.to_house` at its bottom-centre (the pad touches
  the ice).
  1. Drop samples with `|x| > 2.2`, behind the back line (`y < R.back − 0.15`,
     where the opposing skip stands), or past the hog line (`y > 6.401`). A
     guard call puts the pad in front of the house.
  2. Cluster within 0.15 m.
  3. Among clusters seen in at least `MIN_SEEN = 0.5` of the frames, the one
     seen in the most frames wins, with ties going to the one nearest the tee.
     Its position is the median.
  4. Otherwise the result is None.
- `time_target_brooms(shots, video, view, finder, fps=10)` attaches
  `shot.target_broom` in place. It is a no-op when the view has no lateral
  calibration.

**Other files**
- `game/shots.py`: `Shot.target_broom: object = None`, with a comment in the
  file's style.
- `analyze.py`, beside the hogtime call (~L364):
  `broomtime.time_target_brooms(shots, path, sideviews[hogtime.CAMERA_FOR[end.house]], broom_finder)`,
  guarded on `sideviews` and the finder. Build the finder once.
- `weights.py`: add `BROOM_NAME = "broom1.pt"`, `BROOM_ENV_VAR =
  "CURLING_SCORE_BROOM_WEIGHTS"` and `broom_path()`. It is optional like
  `side_path()`.
- `version.py`: bump `PIPELINE_VERSION` with a dated comment.
  `processing_version` gains `+broom-<model_id>` when present.
- `timeline.py`: the shot dict gains `"target_broom": {"x", "y", "seen",
  "confidence"}` or `None`. `SCHEMA_VERSION` 4 → 5. Hand overrides need no
  change (`shot.update(patch)`).

## Phase 5 — Viewer

- `frontend/core/house.mjs`: add a pure `broomMark(shot)`. It returns `{x, y,
  to}` when `shot.target_broom.x` and `.y` are numbers, otherwise null. `to` is
  `shot.stones[shot.delivered_stone_index]` or null. Export it from
  `core/index.mjs` and re-export it in `tests/js/singleton.mjs`.
- `frontend/viewer/House.jsx`: add `Broom({shot})` beside `Track` (L58).
  - It draws a dark pad glyph (a ~0.22 × 0.08 m rounded rect, `PAINT.accent`,
    thin stroke in the thrower's colour).
  - It draws a dashed line of ~0.5 opacity to `to`, plus `<title>` "skip's
    broom".
  - It uses `pointerEvents="none"` and no `sheetClip`.
  - It renders inside the `showTrack` gate, between `Track` and `Stones`
    (L211-212). The Watch layout reuses `<House>`.
- Run `cd frontend && npm run build` and commit `viewer/app.js`,
  `service/static/site.js` and `.buildstamp.json`.

## Tests (run only the affected subsets, since the full suite OOMs here)

- `tests/test_sideview.py`:
  - on a synthetic plate (conftest `side_plate` with the ring drawn),
    `solve_lateral` recovers `centre_col` and the scale;
  - the 12-ft edges map to ±1.829;
  - `to_house` and `to_image` round-trip;
  - an implausible ring is refused.
- `tests/test_broomtime.py`:
  - `pick_target` chooses a still cluster over a stray;
  - a placement behind the back line is refused;
  - fewer than `MIN_SEEN` frames gives None;
  - `window_for` gives `[t−1, t]` for both observed and estimated tee
    crossings, and None for a `missing` shot.
- `tests/test_boxedit.py` and `tests/test_segserve.py`: the existing pinned
  strings are unchanged for the default page. The broom classes emit a `b` arm,
  and `cls` reaches the scoring.
- `tests/test_cli_labels.py`: `--apply` honours `boxes`.
- `tests/test_timeline.py`: `target_broom` is a dict or null, and
  `SCHEMA_VERSION == 5`.
- `tests/test_viewer_js.py`: `broomMark` handles an absent key, null,
  non-numbers, a delivered stone (with a link) and no delivered stone (no link).
  A source assertion checks that `<Broom` sits in the `showTrack` block.
- `tests/test_frontend_build.py`: the stamp and lint pass.

## Verification (local first)

1. Run `analyze` on VXU9 and hOKZ (`~/.cache/curling_score`) and AEqL
   (`~/.cache/curling_replay`). Diff each new timeline against the current one
   with `target_broom`, the new calibration fields and the version strings
   stripped: **it must be identical**.
2. Report per-game broom coverage and hOKZ's held-out numbers against the bar.
   Spot-check ~10 shots by projecting the marker back into the side frame with
   `to_image`.
3. Run `python scripts/devserve.py <timeline>` and check desktop and Watch.
   - The marker and link show, and the track toggle hides both.
   - Stones still drag.
   - A schema-4 timeline renders with no marker and no errors.
4. Run the HawkScan skill against the local service after the viewer change, as
   the session hook requires.
5. **Ship only on a separate explicit go:**
   - commit `weights/broom1.pt`;
   - `Dockerfile.worker` gains `ARG BROOM_MODEL`, a `COPY` and the
     `CURLING_SCORE_BROOM_WEIGHTS` env beside the side model (L65-75);
   - deploy the worker and API from a clean worktree, per the
     deploying-curling-chart and concurrent-session-commits notes;
   - requeue the hosted videos.

## Housekeeping

- After approval, save this design as
  `docs/superpowers/specs/2026-09-23-target-broom-design.md` and commit it,
  staging only this session's hunks because another session shares the
  checkout.
- Commit each phase separately, in the repo's `area: sentence` message style.
