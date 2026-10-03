# Correcting splits for the composite's camera offsets

**Status:** design approved in chat on 2026-10-02, in six sections. Nothing is
built. The measurement it rests on was a one-off study of the 2026-10-01
Thursday Mens games. Its scripts and per-end tables are in
`~/curling-work/sync-1001` (`probe.py`, `probe2.py`, `analyse.py`,
`combine.py`, `served_pairs.py`).

## Context

The club's video is a composite of four feeds per sheet: the two overhead house
panels (top, bottom) and the two long cameras (left, right). The compositor
does not keep them in step. Every time in the pipeline is the composite's
clock, so any number made by subtracting a time read in one feed from a time
read in another carries the feeds' offset.

Naming used below, for an end played to house `H`:

| Feed | Where | In code |
|---|---|---|
| arriving panel | over `H` | `setup` / `seq` in `analyze.build_one_end` |
| throwing panel | over the other house | `far` / `far_seq` |
| hog camera | at `H`'s end, facing the thrower; times the throwing hog line | `sideviews[CAMERA_FOR[OTHER_HOUSE[H]]]` |
| camera behind the thrower | at the throwing end, facing `H` | `sideviews[CAMERA_FOR[H]]` |

A split (`game/split.py:long_split`) is the arriving panel's far hog crossing
minus the hog camera's throwing hog crossing, so it carries
(arriving panel − hog camera). `scripts/ds13/sync_report.py` explains why that
pair could not be measured. The only cross-feed check was `panel_delta`
(hog camera − throwing panel), and "four sources and two equations" do not give
the split's pair.

**What changed.** Both long cameras see the middle of the sheet: about 12–23 m
from either tee on the old framing, more on the re-aimed sheets 2–4. Timing a
stone through that stretch in both gives left − right. Timing the far hog line
in the arriving panel and in the camera behind the thrower gives that pair.
With the existing throwing-end pair, that is a chain through all four feeds in
every end.

**What it found on 2026-10-01** (Mens, sheets 1, 2, 5; lags relative to the
left camera, + = shows an instant later, ±0.1 s):

| Sheet | Right | Top panel | Bottom panel | Events |
|---|---|---|---|---|
| 1 | 0.00 | +0.40 | −0.05 | steady all night |
| 2 | +0.25 | +0.50 | +0.05 | steady all night |
| 5, 7 pm | −0.15 | 0.00 → −0.45 | −0.25 | top panel jumped at ~950–1020 s (end 2) |
| 5, 9 pm | +0.30 | 0.00 | +0.15 | left camera jumped 0.43 s between games |

So splits were off by up to ±0.45 s, with the sign depending on throwing
direction. Sheet 1 splits toward the top house read ~0.4 s slow. In sheet 5's
9 pm game they read +0.2 s toward the bottom house and −0.3 s toward the top.

Independent check: in sheet 5's published splits, the top−bottom direction gap
moved by −0.88 s between games, against ~−0.5 s predicted. On sheets 1 and 2
(no change found) it moved by ±0.04 s.

A survey of every cross-feed use in the pipeline found that splits are the only
published number where the offset lands in full. Thinking time carries
(throwing panel − arriving panel), but the sign flips each end and the total
mostly cancels. The broom window and the hog-camera window have slack for
0.5 s. The delivery chart's time axis is only used within one delivery.

## Goal

Published splits are corrected for the offsets, live and on reprocess. The raw
split and the measured offsets stay in the timeline.

**Success:**
- On the three cached 2026-10-01 Mens recordings (0SWB4g3SJoE, 32Dkqsf2t2M,
  dea75KXakFc), each end's correction matches the study within ~0.05 s.
- Sheet 5's top−bottom gap in corrected splits stops jumping between games.
- Shot lists are identical rock by rock. Only split fields, the new sync blocks,
  and any `draw_through`/`flashed` flip near 12.5 s change.
- Corrected far crossings agree with the new hand marks after the fitted
  offset.
- The live lane's p90 per-end time stays under its real-time budget.

**Out of scope** (possible follow-ups):
- Thinking time.
- The broom window.
- `line.delivery`'s time axis.
- The raw hog-camera clock that lost-rock releases publish.
- Making the side views' depth calibration exact. This would shrink the
  mid-sheet bias below; the design does not depend on it.
- Viewer changes. It shows `long_split_s` as now.

## Design

### 1. Measurements

For every non-missing shot with a throwing-end hog crossing (`t_hog_s`), three
timed readings:

1. **Arriving panel, far hog line.** `FarCrossing.t`, the split's own end. Only
   observed crossings are used (`far_reach` ≤ 0.05).
2. **Camera behind the thrower, far hog line.** Fit the box bottom (the stone's
   trailing edge, `metres_at(row)`) against time over 5.5–8.5 m from the
   playing tee: a RANSAC line, then a quadratic through its inliers. Let
   `t_trail` be when that edge reaches the paint's near edge (`hog_row`, i.e.
   6.401 m in the view's calibration), and `v` the speed there. The crossing
   is `t_trail + c − 0.291 / v`: the leading edge touching the paint, the
   arriving tripwire's convention. `c` is fitted from hand marks (section 5);
   it is 0 until then.
3. **Mid-sheet, both long cameras.**
   - **Positions:** the hog camera's stone centre is
     `y = TEE_TO_TEE − (metres_at(row) − R)`
     (`linetime.to_destination`'s convention). The camera behind the thrower's
     is `y = metres_at(row) − R` (`path_points`').
   - **Fits:** each track is fitted as in (2) over y ∈ [13.0, 21.75] m from the
     playing tee. That stretch is centred on the sheet's midpoint, so the two
     throwing directions measure at the same physical spots.
   - **The reading:** `mid` = the median of t_hog-cam(y) − t_behind(y) over
     levels every 0.25 m in the span both tracks cover. It needs at least 5
     levels and a span of 2 m or more. The stone's speed at the span's middle
     is kept as `v_mid`.

**Reads.**
- **Splitting `find_path`.** `linetime.find_path`'s decode-and-detect becomes a
  read step of its own (`read_destination`). It runs for every eligible shot,
  before the aim-line pass. Today the read happens only for shots that also got
  a broom and an aim-line fit.
- **What the read covers.** The window, fps and bands are unchanged: from
  `t_hog` + 1 s to (`t_rest` or `t_hog` + 24 s) + 1 s, 5 fps, rows
  tee − 80..660 at imgsz 800 and 620..bottom at imgsz 416, both colours,
  conf 0.3.
- **One decode for both cameras.** `detect/longview.decode_views` decodes once
  and crops both long cameras from each frame: hstack in the filter graph,
  split in numpy. The hog camera's rows come from its calibration:
  `row_for(13.0) − 40` to `row_for(21.75) + 20`, clipped to the frame. They are
  detected at imgsz 800 above row 660 and 416 below, for the shot's colour.
- **Timestamps kept.** The detections, with timestamps, are stored on the
  shot. `find_path`'s chain then reuses them instead of decoding, and is handed
  timed points. Its output is unchanged.

**Diagnostic.** The camera-only split: (2) − the hog camera's crossing − the
end's left−right offset. It is kept per shot, not published as the split.

### 2. Estimation

Per shot, with `far` = (1) − (2) and `mid` from (3):

    role(v) = R0 + S · (1/v − 1/1.6)
    e       = far − (mid − role(v_mid))          # arriving panel − hog camera

`e` is the shot's split error. `role` is the mid-sheet position bias. Its sign
follows which physical camera is the hog camera, so it cancels between ends
thrown in opposite directions.

- **S (speed term), per recording.** A pooled within-end regression of `mid` on
  `1/v_mid`, over ends with 3 or more readings and readings within 0.15 s of
  their end's median.
- **R0 (role term at 1.6 m/s), per recording.** For each pair of adjacent ends
  (always opposite directions), take the mean of the two ends' median `mid`
  values adjusted to 1.6 m/s. R0 is the median over pairs, so a pair that
  straddles a jump is outvoted.
- **Defaults** until a pair exists:
  - Old framing: R0 0.20 s, S 0.22 m.
  - Re-aimed: R0 0.56 s, S 0.36 m.
  - Re-aimed means both views' `tee_row` < 430. These are from 2026-10-01;
    revisit as more nights are measured.
- **Per-shot correction.** The running median of `e` over a 5-shot window in
  throw order within the end. Readings more than 0.3 s from the window's median
  are dropped and the median retaken. A shot with no reading takes its window's
  value.
- **Thin ends.** An end with fewer than 3 usable readings borrows the median `e`
  of the nearest end played to the same house, marked `borrowed`. With none,
  its splits stay raw, marked `uncorrected`.
- **Confidence.** Each end records the MAD and count of its `e` values. Over
  0.08 s MAD it is marked `low_confidence`, and still corrected.
- **Applying it.** Corrected split = raw − e. For example, on sheet 1 an end
  played to the top house has e ≈ +0.4 s, so a raw 15.3 s split becomes 14.9 s.

### 3. Outputs and code

**Timeline, per shot:**
- `long_split_s`: corrected. Everything downstream (viewer, stats,
  `splits_measured`, the thinking report) reads this, unchanged.
- `long_split_raw_s`: the split as measured today.
- `long_split_sync_s`: the correction applied (raw − corrected).
- `long_split_sync`: `measured` | `borrowed` | `default` | `uncorrected`.
  `default` means the role term was a framing default.
- `sync`: `{far_s, mid_s, v_mid_m_s, cam_split_s}`, each nullable.
- `long_split_panel_delta_s`: unchanged.

**Per end:** `sync`: `{split_error_s, mad_s, n, source, low_confidence}`.

**Per document:** `sync`: `{role_s, speed_term_m, pairs, default}`.
`default` is null, `"old"` or `"reaimed"`.

**Classification.** The `draw_through`/`flashed` rule (`classify.py`,
`SPLIT_HIT_MAX_S`) is re-run on corrected splits in the final pass.

**Versions.** `PIPELINE_VERSION` bumps. The schema stays 8, since fields are only
added. Existing charts keep raw splits until reprocessed.

**Code:**
- `game/sync.py` (new, pure):
  - `measure_shots(shots, hog_view, dest_view)` writes the per-shot readings.
    It runs after `fartime` in `build_one_end`.
  - `correct(document)` fits S and R0 and rewrites the split fields and sync
    blocks from the stored raw fields. It is idempotent and reads no video.
- `detect/longview.py`: `decode_views`.
- `game/linetime.py`: `read_destination` plus the existing chain, fed timed
  points.
- `analyze.py`: the read step before the aim-line pass, then `measure_shots`
  after `fartime`.
- `timeline.py`: the fields. `build_document` calls `sync.correct`, so
  `analyze` and `LiveSession.document()` both get it.
- `scripts/mark_hog.py`: the receding mode (section 5).
- The study scripts move into `scripts/sync/`, so the comparison can be rerun.

### 4. Live

`LiveSession.document()` rebuilds the whole document from its stored ends on
every publish, and `build_document` applies `sync.correct`. So:

- **End 1** is corrected with the framing default (`default`).
- **When end 2 lands,** the next publish fits R0 and S from the pair and
  recomputes end 1 from its stored raw fields. There is no new read and no
  special republish path.
- **A calibration rebuild** of an end re-measures its readings with the new
  calibration, and the next publish uses them.
- **Load:** each end gains one crop from a decode that already happens, plus
  ~6–8 GPU-seconds. The lane is bound by one CPU thread and the GPU is ~70%
  idle, but this is an estimate; see section 6.
- **Logging:** the worker logs `end N: split sync −0.31 s (n 12, mad 0.03)` per
  end, so a live night can be checked from the log.

### 5. Hand-mark calibration (before shipping)

From behind, a receding stone hides the ice ahead of it: it is 11 cm tall and
seen at ~5°. A person can see its trailing edge clear the paint's near edge,
and that is the detector's box bottom, so marks and model judge the same edge.

- **Rocks:** ~40 on the cached recordings of sheets 1, 2 and 5. Both throwing
  directions, draws and hits, ~7 per recording per direction.
- **Two marks per rock:**
  - **Receding:** the frame the stone's bottom clears the far hog line's paint,
    in the camera behind the thrower.
  - **Arriving:** the frame its leading edge touches the hog line in the
    arriving panel, the convention of `datasets/hogmarks/hOKZoeJNTpM-receiving.json`.
- **Frames:** cut on the worker with its own ffmpeg (no VOD download), rsynced
  back, served over HTTP.
- **Tool:** `scripts/mark_hog.py` gains a receding mode. It crops around the
  view's `hog_row`, with the stone arriving from below, and does not draw the
  line.
- **Storage:** marks go to `datasets/hogmarks/<vid>-receding.json` and are
  committed as soon as they are written.
- **Fits:**
  - `c` = median(hand − model) on the receding marks, checked separately for
    old and re-aimed framing.
  - The arriving marks give the panel tripwire's residual on this season's
    framing (validated so far only on 2025-26 panels).
  - Per rock, they give a hand-made arriving panel − camera offset: the arriving
    mark − (the receding mark − 0.291/v). That checks reading (1) − (2) directly.

### 6. Testing and rollout

**Unit tests (no video):**
- `sync` on synthetic readings, covering:
  - known offsets and a role term
  - a mid-end jump
  - an outlier
  - a thin end
  - a one-direction recording
  - no side views
  - `correct` run twice
- `decode_views` against two `longview.decode` calls on a fixture clip.
- The linetime tests unchanged.
- Run affected subsets only (the full suite OOMs on this box). Deselect
  `TestAgainstHandMarkedCrossings`, which fails on main already.

**Worker A/B** (`run_ab.sh`, three cached recordings, ~10 min a run):
- shot lists identical (`ab_diff.py`)
- per-end corrections against the study
- sheet 5's direction gap
- far crossings against the hand marks

**Live:**
- `live-replay` one recording before and after.
- Compare per-end build time and the live document's corrections with the VOD
  run, including end 1's default being replaced at end 2.
- A five-stream rehearsal only if one stream grows by more than ~5% per end.

**Rollout, each step on the user's go-ahead:**
1. The marking tool's receding mode, frames, the marking round, fit `c`.
2. The build, on a branch in a worktree (another session shares the checkout).
   Then the A/B and the live replay.
3. Merge, then deploy the worker and API.
4. Reprocessing hosted charts is a separate decision. Cached videos reprocess
   in ~4 min each; others need VOD downloads, which need a fresh ask.

## Risks

- **Direction symmetry.** The role term assumes the mid-sheet bias is the same
  in both directions at the same spot. The study's per-end results agreed
  across directions within each game, to ±0.02 s on sheets 1 and 5 and ±0.05 s
  on sheet 2. A recording thrown one way only, such as a one-end fragment, only
  ever gets the default.
- **Live recalibration** can move a side view mid-recording and shift the role
  term. The adjacent-pair median absorbs one such change.
- **A jump between an end's first shots** is followed after 2–3 shots. The
  shots before that carry part of the jump.
- **Doubles** ends (10 rocks) will borrow more often.
- **Re-aimed views** carry a 2.5× larger mid-sheet bias (0.56 s at 1.6 m/s). It
  cancels the same way, but the per-end scatter was ±0.05 s against ±0.02 s.
