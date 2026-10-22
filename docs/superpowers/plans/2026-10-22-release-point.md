# Release Point Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Read where the thrower's hand left the handle for every rock, store it in the timeline, mark it "Release" on every delivery chart, and colour the rock's dots before/after it there and in burned-in clips.

**Architecture:** While the hog-line and delivery reads decode the long camera facing the thrower, a `LaneKeeper` tapped into `longview.decode` keeps a small grey strip of the delivery lane from every frame (30 fps; the user's choice, 2026-10-22). After the line pass has built each rock's delivery path, `releasetime` cuts rock-centred hand crops from those strips, runs a ResNet-18 hand-on/hand-off classifier, and step-fits the release onto the path (`line.release = [t, y, x]`). The viewer draws a marker there and splits the dot colours at it.

**Tech Stack:** Python 3.12, numpy, OpenCV, torch/torchvision (already in the worker image), pytest; React viewer with plain-ES core modules tested under node via `tests/test_viewer_js.py`.

**Spec:** `docs/superpowers/specs/2026-10-22-release-point-design.md`

## Global Constraints

- No new decode of the long camera; the release read uses only frames `hogtime` and `linetime` already decode.
- Attach-only: the release pass may set `line.release` and nothing else; a failure leaves `release` None and is logged.
- Marker label text is exactly `Release`.
- Dot colours: before `#2d1e6b`, after `#a07a00`, no release read `#1f7a8c` (BGR in clips: `(0x6b,0x1e,0x2d)`, `(0x00,0x7a,0xa0)`, `(0x8c,0x7a,0x1f)`).
- Crop: 0.5 m either side of the rock, 0.55 m above and 0.12 m below its ice point, 160x107 px, grey (OpenCV RGB2GRAY), three frames stacked at -4/30, -2/30, 0 s, upsampled 2x, normalised (x - 0.45) / 0.225.
- Read span: rock from 0.5 m behind the throwing tee to the delivery path's last sample. A step on the read's first `FIRST_UNSEEN = 2` frames, or none at all, is no release.
- Ship gate: at least 15 of 20 freshly marked rocks of `s_0hx8wuVKeMmy4evaI` within 0.5 m, else back to the user.
- No reprocess of hosted charts. No distance in the label; nothing framed as a hog-line call.

## Review Focus

- A rock whose release came from the long camera (`release_source == "side"`) or had none: hogtime's window was arrival-based, so the keeper must keep those windows too or the read silently loses its later frames -- Task 5 pins it.
- Two decodes of the same frames (hogtime's window overlaps read_delivery's) must not double a strip -- Task 1 pins dedup by source frame index.
- A live worker holds up to three streams: strips held per end must stay bounded (about 340 MB for a 16-rock end at 30 fps, ~1 GB for three streams) -- Task 1 pins a 400 MB budget.
- An old timeline (no `line.release`) must draw every dot teal with no marker and no "before/after" legend -- Task 8 pins it.
- The large view's speed line and the delivery dots must split at the same moment -- Task 8 pins one shared colour function.

---

### Task 1: Lane strips from the long camera's decodes

**Files:**
- Create: `src/curling_score/detect/lanekeep.py`
- Modify: `src/curling_score/detect/longview.py` (`decode`, new `keeping`)
- Test: `tests/test_lanekeep.py`

**Interfaces:**
- Produces: `lanekeep.LaneKeeper(view, windows, every=1)`; `.offer(rect, frames, times) -> None`; `.frames_between(t0, t1) -> tuple[np.ndarray, np.ndarray]` (strips `(N, h, w)` uint8, times `(N,)`, time-sorted); `.origin -> tuple[int, int]` (col0, row0 in view pixels); `.nbytes -> int`; `.clear()`. `lanekeep.strip_box(view) -> tuple[int, int, int, int]` (row0, row1, col0, col1, view pixels, clipped to the rect's size). `longview.keeping(keeper)` context manager.
- Constants in `lanekeep`: `FROM_M = -0.5`, `TO_M = 8.4`, `UP_M = 0.6`, `DOWN_M = 0.15`, `ACROSS_M = 1.5`, `SOURCE_FPS = 30.0`, `KEEP_EVERY = 1` (every frame; `every` stays a parameter so a later change to every other frame is one constant).

- [ ] **Step 1: Write the failing tests**

Use the 10/20 S2 left view as the fixture view (`SideView(rect=(0, 0, 810, 1080), tee_row=360.8, hog_row=433.0, centre_col=399.21, lat_px_per_m_at_tee=117.425, centre_line=(394.038, 0.007471))`).

```python
def test_keeps_every_source_frame_inside_a_window_once():
    k = lanekeep.LaneKeeper(VIEW, windows=[(10.0, 11.0)])
    frames, times = synthetic(VIEW, t0=9.5, n=60)        # 30 fps, 9.5 .. 11.47
    k.offer(VIEW.rect, frames, times)
    k.offer(VIEW.rect, frames, times)                      # a second decode of the same frames
    strips, ts = k.frames_between(0, 99)
    assert ts.min() >= 10.0 and ts.max() <= 11.0 and len(ts) == len(set(ts)) == 30

def test_another_cameras_frames_are_not_kept():
    k = lanekeep.LaneKeeper(VIEW, windows=[(0, 99)])
    k.offer((1107, 0, 813, 1080), *synthetic(VIEW, t0=1.0, n=10))
    assert k.frames_between(0, 99)[0].shape[0] == 0

def test_strip_is_grey_of_the_lane_box():   # a pixel value put at (row0, col0) of the frame comes back at [0, 0]

def test_decode_offers_frames_only_while_keeping(monkeypatch):
    # monkeypatch longview._decode_now to return synthetic frames; decode() inside
    # longview.keeping(k) feeds k, outside it does not

def test_a_sixteen_rock_end_stays_under_budget():
    r0, r1, c0, c1 = lanekeep.strip_box(VIEW)
    per_end = 16 * 8.0 * (lanekeep.SOURCE_FPS / lanekeep.KEEP_EVERY) * (r1 - r0) * (c1 - c0)
    assert per_end < 400e6
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/python -m pytest tests/test_lanekeep.py -q`
Expected: FAIL (`No module named ...lanekeep`)

- [ ] **Step 3: Implement `lanekeep.py`, and the tap in `longview.decode`**

`strip_box`: rows from `row_for(FROM_M) - UP_M * lateral_px_per_m(row_for(FROM_M))` to `row_for(TO_M) + DOWN_M * lateral_px_per_m(row_for(TO_M))`, columns `centre_col_at(row) ± ACROSS_M * lateral_px_per_m(row)` taken over both end rows, clipped to `(0, rect[3])`/`(0, rect[2])`. A frame is kept when `round(t * SOURCE_FPS) % every == 0`, `t` lies in any window, and its index is not already stored; the stored strip is `cv2.cvtColor(frame[r0:r1, c0:c1], cv2.COLOR_RGB2GRAY)` (a copy). In `longview`, add a module global `_keeping = None` and `keeping(keeper)` mirroring `reading_ahead`; `decode` hands every non-empty result to `_keeping.offer(rect, frames, times)` on whichever path produced it, then returns it unchanged.

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_lanekeep.py tests/test_longview.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/detect/lanekeep.py src/curling_score/detect/longview.py tests/test_lanekeep.py
git commit -m "lanekeep: keep a grey strip of the delivery lane from the long camera's decodes"
```

---

### Task 2: Hand crops, stacks and the step fit

**Files:**
- Create: `src/curling_score/game/releasetime.py` (pure part)
- Test: `tests/test_releasetime.py`

**Interfaces:**
- Consumes: Task 1's strips/times/origin.
- Produces (module `releasetime`):
  - `rock_at(delivery, o) -> tuple[float, float] | None` -- `(x, y)` interpolated on `[t, y, x]` samples, None outside them.
  - `hand_crops(strips, times, origin, view, delivery, t_release) -> tuple[np.ndarray, np.ndarray, np.ndarray]` -- crops `(N, 107, 160)` uint8, `o` `(N,)`, `yp` `(N,)`, one per strip whose rock lies between `FROM_M` and the path's last sample.
  - `stacks(crops, o) -> np.ndarray` `(N, 3, 107, 160)` float32 in 0..1: per frame the crops nearest `o + s` for `s in STACK_S`, clamped to the sequence (by time, so it also works on a thinned sequence).
  - `step_fit(p) -> int`, `release_at(p, o, delivery) -> tuple[float, float, float] | None` (`(t, y, x)`).
  - Constants: `CROP_ACROSS_M = 0.5`, `CROP_UP_M = 0.55`, `CROP_DOWN_M = 0.12`, `CROP_PX = (160, 107)`, `STACK_S = (-4 / 30, -2 / 30, 0.0)`, `FROM_M = -0.5`, `FIRST_UNSEEN = 2`. `yp = linetime.TEE_Y - y`; image point `view.to_image(-x, yp)` (the camera faces the thrower).

- [ ] **Step 1: Write the failing tests**

```python
def test_the_rock_sits_at_the_same_spot_in_every_crop():
    # strips with one bright 3x3 dot at view.to_image(-x, yp) of each frame's rock, minus origin
    crops, o, yp = releasetime.hand_crops(strips, times, origin, VIEW, DELIVERY, T_REL)
    for c in crops:
        r, col = np.unravel_index(c.argmax(), c.shape)
        assert abs(col - 80) <= 2 and abs(r - 88) <= 2       # 0.5 m across, 0.55 m up at 160 px/m

def test_crops_span_half_a_metre_behind_the_tee_to_the_path_end():
    assert yp.min() >= -0.5 - 0.1 and yp.max() <= TEE_Y - DELIVERY[-1][1] + 1e-6

def test_stacks_take_the_frames_two_and_four_back():
    # o on a 30 fps grid: channel 0 is the crop four frames back, channel 1 two back, channel 2 this one

def test_step_fit_finds_the_switch():
    assert releasetime.step_fit(np.array([0, .1, 0, .9, 1, 1.])) == 3

def test_a_switch_on_the_first_frames_or_never_is_no_release():
    o = np.linspace(0.2, 3.0, 43)
    assert releasetime.release_at(np.r_[[0.0], np.ones(42)], o, DELIVERY) is None
    assert releasetime.release_at(np.zeros(43), o, DELIVERY) is None

def test_release_is_the_rocks_point_on_the_path():
    t, y, x = releasetime.release_at(np.r_[np.zeros(20), np.ones(23)], o, DELIVERY)
    assert t == pytest.approx(o[20]) and (y, x) == pytest.approx(releasetime.rock_at(DELIVERY, o[20]))
```

- [ ] **Step 2: Run them to see them fail** -- `.venv/bin/python -m pytest tests/test_releasetime.py -q`, expected FAIL.

- [ ] **Step 3: Implement the functions in `releasetime.py`**

Crop by one affine warp per strip: scale `z = 160 / (2 * CROP_ACROSS_M * ppm)` with `ppm = view.lateral_px_per_m(view.row_for(yp))`, translated so the rock's point (less `origin`) lands at `(80, 88)`, `cv2.INTER_LINEAR`, border 0. `step_fit` is the spike's: `argmin_k sum(p[:k]) + sum(1 - p[k:])` over `k in 0..N`, via cumulative sums.

- [ ] **Step 4: Run the tests** -- expected PASS.

- [ ] **Step 5: Commit** -- `git commit -m "releasetime: hand crops along the delivery path, frame stacks, the on-to-off step"`

---

### Task 3: The release model loader

**Files:**
- Create: `src/curling_score/detect/releasemodel.py`
- Modify: `src/curling_score/weights.py` (after the spin block)
- Test: `tests/test_releasemodel.py`, `tests/test_weights.py`

**Interfaces:**
- Produces: `releasemodel.build() -> torch.nn.Module` (torchvision `resnet18(weights=None)`, `fc = Linear(512, 1)`); `releasemodel.default_model()` (None when `weights.release_path()` is None; lru-cached load, eval mode, CUDA half precision when available else CPU float); `releasemodel.p_off(model, stacks: np.ndarray) -> np.ndarray` (sigmoid per frame, batches of `BATCH = 128`, upsampled by `INPUT_SCALE = 2.0` bilinear, normalised with `NORM = (0.45, 0.225)`). Weights are a float16 `state_dict` file.
- `weights.RELEASE_NAME = "release1.pt"`, `weights.RELEASE_ENV_VAR = "CURLING_SCORE_RELEASE_WEIGHTS"`, `weights.release_path()` with `spin_path`'s contract.

- [ ] **Step 1: Failing tests**

```python
def test_a_saved_model_loads_and_gives_probabilities(tmp_path):
    m = releasemodel.build(); torch.save({k: v.half() for k, v in m.state_dict().items()}, tmp_path / "r.pt")
    p = releasemodel.p_off(releasemodel._load(str(tmp_path / "r.pt")), np.random.rand(5, 3, 107, 160).astype("float32"))
    assert p.shape == (5,) and ((0 <= p) & (p <= 1)).all()

def test_release_path_none_and_missing(monkeypatch, tmp_path):   # mirrors the spin_path tests
```

- [ ] **Step 2: Run to fail; Step 3: implement; Step 4: run** -- `.venv/bin/python -m pytest tests/test_releasemodel.py tests/test_weights.py -q`.

- [ ] **Step 5: Commit** -- `git commit -m "releasemodel: the hand-on/hand-off classifier, loaded like the spin model"`

---

### Task 4: Training kit in the repo, and `weights/release1.pt`

**Files:**
- Create: `scripts/release/cut.py`, `scripts/release/train.py`, `scripts/release/README.md`
- Create: `weights/release1.pt`; Modify: `.gitignore` (`!weights/release1.pt`, with a comment like spin1's), `datasets/releasemarks/README.md` (how crops are cut now)

**Interfaces:**
- Consumes: Tasks 1-3 (`LaneKeeper(view, windows, every=1)`, `hand_crops`, `stacks`, `step_fit`, `build`).
- Produces: `cut.py MARKS.json... --timelines DIR --videos DIR --out DIR` -- per game an `.npz` of crops, `o`, `yp`, labels (0 before the mark's `o`, 1 from it, -1 within two frames of it; a "held" rock all 0; "can't tell" rocks left out) and meta; it decodes each rock's span with `longview.decode` under `keeping(...)` at 30 fps, i.e. through exactly the production crop code. `train.py DATA.npz... --out DIR [--folds "SL1+SL2+SL3+SL4+SL5,SL1,..."] [--save weights.pt]` -- the spike's `hand_train.py --mode stack --scale 2` (5 epochs, AdamW 3e-4, OneCycle, shift/flip/brightness) on `releasetime.stacks` and `releasemodel.build`, results tagged with their fold, saves float16.
- Source of the trainer: `~/curling-work/body-spike/scripts/hand_train.py` (and its fixed per-fold bookkeeping).

- [ ] **Step 1: Write `cut.py` and `train.py`; check `cut.py` on one rock locally** against the spike: its crop of `s_1TH0` e1 r5 frame at the mark is the same picture as the marking frame's centre (look at both).
- [ ] **Step 2: On the 3070 laptop** (videos in `/data/wdd/scratch/body/videos`, timelines in `/data/wdd/scratch/body/tl`, worker image, repo mounted), cut all 11 marked games.
- [ ] **Step 3: Train held-out folds** `"SL1+SL2+SL3+SL4+SL5,SL1,SL2,SL3,SL4,SL5,T1020"` and `--save`.
Expected: Super League games one at a time ≥ 80% within 0.5 m (the spike's 83% on its own crops). Well below that, stop and report to the user before going on.
- [ ] **Step 4: Keep the crops** on the training box: `/data/curling/release/` on `curling@100.85.240.114` (the recordings expire after 21 days).
- [ ] **Step 5: Commit** scripts, README notes, `.gitignore` and `weights/release1.pt` -- `git commit -m "release1: the hand-on/hand-off model, trained on production-style crops of 217 marked rocks"`

---

### Task 5: The release pass in the pipeline

**Files:**
- Modify: `src/curling_score/game/linetime.py` (`Line` gains `release: tuple | None = None`)
- Modify: `src/curling_score/game/releasetime.py` (`time_releases`)
- Modify: `src/curling_score/analyze.py` (`EndContext.release_model`, `build_one_end`, `load_release_model`, `processing_version` call), `src/curling_score/live/session.py` (`Models.release_model`, `EndContext(...)`, `processing_version`), `src/curling_score/live/lane.py:333`, `src/curling_score/cli.py:622,663`, `src/curling_score/practice/enrich.py` (keeper + release stage), `src/curling_score/version.py` (`processing_version(..., release_weights=None)`, `PIPELINE_VERSION` bump with a dated comment)
- Test: `tests/test_releasetime.py`, `tests/test_version.py`, `tests/test_practice_enrich.py`

**Interfaces:**
- Consumes: Tasks 1-3.
- Produces: `releasetime.time_releases(shots, keeper, view, *, model=None, infer=None) -> int` (rocks given a release; `infer` defaults to `releasemodel.p_off`; a no-op when `model` is None or the view has no lateral fit). `releasetime.keep_windows(shots) -> list[tuple[float, float]]`: `(t - 1.0, t + 7.0)` around each shot's `release.t`, else `hogtime.window_for(shot)` widened by 1 s. `analyze.load_release_model(line_model, progress) -> object | None` (None without a line model). `version.processing_version(..., release_weights=None)` appends `+release-<model_id>` after spin, only when the line suffix is there.

- [ ] **Step 1: Failing tests**

```python
def test_a_rock_with_a_delivery_gets_its_release(stub keeper with strips, infer returning a 0..1 step):
    n = releasetime.time_releases([shot], keeper, VIEW, model=object(), infer=step_infer)
    assert n == 1 and shot.line.release == pytest.approx((t, y, x))

def test_nothing_else_on_the_line_changes():          # dataclasses.replace only touches release
def test_a_failing_read_leaves_no_release_and_the_rest():   # infer raises -> release None, n == 0, logged
def test_rocks_without_a_path_or_release_are_skipped():
def test_a_side_or_missing_release_keeps_hogtimes_own_window():
    # a shot with no release: keep_windows gives hogtime.window_for(shot) widened by 1 s
def test_processing_version_names_the_release_model():
    assert version.processing_version(w, side, broom, line=True, spin_weights=s, release_weights=r).endswith(f"+spin-{model_id(s)}+release-{model_id(r)}")
def test_practice_enrich_runs_the_release_stage_after_the_line():   # stub Stages gains release
```

- [ ] **Step 2: Run to fail.**
- [ ] **Step 3: Implement.** In `build_one_end`, make `keeper = lanekeep.LaneKeeper(hog_view, releasetime.keep_windows(shots))` before `hogtime`; wrap hogtime, sidereleases, brooms and lines in `longview.keeping(keeper)` (inside `reading_ahead`); after `linetime.time_lines`, call `time_releases(shots, keeper, hog_view, model=ctx.release_model)`, `progress(f"    end {end.number}: release {n}/{len(shots)} in {s:.1f} s ({keeper.nbytes / 1e6:.0f} MB kept)")`, `stages.mark("release")`, `keeper.clear()`; spin runs after. Practice `enrich` does the same around its hog..line stages, with a `release` field on `Stages`.
- [ ] **Step 4: Run** `.venv/bin/python -m pytest tests/test_releasetime.py tests/test_version.py tests/test_practice_enrich.py tests/test_analyze_stages.py tests/test_linetime.py -q` -- PASS. (The full suite OOMs on this box; run the affected files.)
- [ ] **Step 5: Commit** -- `git commit -m "releasetime: the release pass, after the line, on the frames the hog and delivery reads kept"`

---

### Task 6: The release in the timeline, practice records and clip records

**Files:**
- Modify: `src/curling_score/timeline.py` (`_line`), `src/curling_score/practice/throws.py` (`throw_record`'s `line`), `src/curling_score/game/clips.py` (`throw_from_rock`)
- Test: `tests/test_timeline.py`, `tests/test_practice_pipeline.py` (or the throws test it already has), `tests/test_clips.py`

**Interfaces:**
- Produces: timeline `line.release = [round(t, 2), round(y, 2), round(x, 3)]`, the key absent when `Line.release` is None (no schema bump). Practice and clip records carry `line.release` the same way.

- [ ] **Step 1: Failing tests** -- `_line(line_with_release)["release"] == [1.23, 30.12, -0.142]`; `"release" not in _line(line_without)`; `throw_from_rock({... "line": {"release": [...]}})["line"]["release"] == [...]`; the practice record likewise.
- [ ] **Step 2-4:** fail, implement, pass (`.venv/bin/python -m pytest tests/test_timeline.py tests/test_clips.py tests/test_practice_pipeline.py -q`).
- [ ] **Step 5: Commit** -- `git commit -m "timeline: line.release, the rock's point on its delivery when the hand let go"`

---

### Task 7: Burned-in clips split their dots at the release

**Files:**
- Modify: `src/curling_score/game/clipburn.py` (`RAMP`/`ramp` replaced)
- Test: `tests/test_clipburn.py`

**Interfaces:**
- Produces: `clipburn.DOT_BEFORE = (0x6b, 0x1e, 0x2d)`, `DOT_AFTER = (0x00, 0x7a, 0xa0)`, `DOT_UNKNOWN = (0x8c, 0x7a, 0x1f)` (BGR); `clipburn.dot_colour(t, release_t) -> tuple` (`release_t` None -> unknown; `t < release_t` -> before; else after). `marks` colours each dot by its path time against `throw["line"]["release"][0]`.

- [ ] **Step 1: Failing tests** -- `dot_colour(0.5, 1.0) == DOT_BEFORE`, `dot_colour(1.0, 1.0) == DOT_AFTER`, `dot_colour(0.5, None) == DOT_UNKNOWN`; `marks(...)` on a throw with a release gives both colours split at it, and on one without gives only `DOT_UNKNOWN`; no reference to `RAMP` remains.
- [ ] **Step 2-4:** fail, implement, pass (`.venv/bin/python -m pytest tests/test_clipburn.py tests/test_game_clips.py -q`).
- [ ] **Step 5: Commit** -- `git commit -m "clipburn: the delivery dots change colour at the release"`

---

### Task 8: Viewer core -- release geometry and two-colour dots

**Files:**
- Modify: `frontend/core/constants.mjs` (`DELIVERY_RAMP` -> `DELIVERY_COLORS = { before: "#2d1e6b", after: "#a07a00", unknown: "#1f7a8c" }`), `frontend/core/delivery.mjs`, `frontend/core/speed.mjs`, `frontend/core/index.mjs`, `tests/js/singleton.mjs`
- Test: `tests/test_viewer_js.py` (replace the `DELIVERY_RAMP`/`rampColor`/`rampSpan` tests)

**Interfaces:**
- Produces (in `core/delivery.mjs`):
  - `deliveryRelease(shot) -> {t, yp, x} | null` from `shot.line.release` (`[t, y, x]`, `yp = TEE_Y - y`; null unless three finite numbers).
  - `dotColor(t, rel) -> string` -- the one colour rule for dots and speed segments.
  - `deliveryGeometry(...)` adds `release: { x, y, tick: {x1, y1, x2, y2}, label: {x, y, anchor, text: "Release"} } | null` (inside the plot only) and `legend: [{color, text}]` (two entries with a release, `[]` without); `dots[i].fill` from `dotColor`; `rampColor`/`rampSpan` removed.
  - `aimFrame` adds `release: {yp, dx} | null`; `deliveryOverlay` adds `rocks[i].release: {x, y} | null`.
- Produces (in `core/speed.mjs`): `speedGeometry(...)` segments coloured by `dotColor`, plus `release: { x, y1, y2 } | null`.

- [ ] **Step 1: Failing tests** (in `tests/test_viewer_js.py`, via `run_js`)

```python
def test_dots_split_at_the_release():          # fills before t_rel all "#2d1e6b", from it all "#a07a00"
def test_a_timeline_without_a_release_draws_every_dot_teal_with_no_marker_or_legend():
    # g.release === null, g.legend.length === 0, every fill "#1f7a8c"
def test_the_marker_sits_on_the_path_at_the_release_in_both_orientations():   # DELIVERYBOX and BIGDELIVERYBOX
def test_a_release_outside_the_box_draws_no_marker():
def test_speed_segments_and_dots_change_colour_at_the_same_moment():
def test_the_speed_chart_has_a_release_line_at_the_dots_distance():
def test_aim_frame_and_overlay_carry_the_release():
def test_a_malformed_release_is_ignored():        # [1, null, 2], "x", [] -> null
```

- [ ] **Step 2: Run to fail** -- `.venv/bin/python -m pytest tests/test_viewer_js.py -q -k "release or teal or speed_segments or aim_frame"`.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `.venv/bin/python -m pytest tests/test_viewer_js.py tests/test_practice_js.py -q` -- PASS.
- [ ] **Step 5: Commit** -- `git commit -m "viewer core: the release on the delivery geometry; dots and speed split at it"`

---

### Task 9: The charts draw it

**Files:**
- Modify: `frontend/viewer/Delivery.jsx` (marker, two-swatch legend, phone caption, `deliveryLabel`), `frontend/viewer/DeliveryDialog.jsx` (caption; `SpeedChart` draws `sg.release`), `frontend/viewer/LineChart.jsx` / its caption in `ChartPanel.jsx` or `App.jsx`, `frontend/site/ShotCharts.jsx` (overlay markers), and the built bundles (`npm run build`, `frontend/.buildstamp.json`)
- Test: `tests/test_viewer_js.py` / `tests/test_practice_js.py` for any copy the core now returns; headless check

**Interfaces:**
- Consumes: Task 8's `g.release`, `g.legend`, `sg.release`, `rocks[i].release`.
- Copy: marker label exactly `Release`. Captions and aria labels drop "dark at the push-off/at rest to gold past the hog line" and "darker earlier"; with a release they say the dots are navy before the release and gold after; without one they say nothing about colour (wording the implementer's).

- [ ] **Step 1: Draw the marker** in `DeliveryChart` (a short line across the path at `g.release.tick` plus the `g.release.label` text, in `PAINT.accent` or a colour distinct from both dot colours and the start ring), the two-swatch `Legend` from `g.legend` (none when empty), the speed chart's release line, and the overlay's small unlabelled markers.
- [ ] **Step 2: Build** -- `cd frontend && npm run build`; check `frontend/.buildstamp.json` changed.
- [ ] **Step 3: Look at it** -- devserve with the 10/20 S2 timeline plus a `line.release` on a few rocks (one past the hog line, one missing); headless phone (390x844, touch) and desktop (1500 wide) via `cdp.mjs`: Delivery tab, desktop card, line chart, large view with its speed line, My shots overlay, practice page. Marker and label readable and inside the plot; old-timeline rock teal with no legend.
- [ ] **Step 4: Run** `.venv/bin/python -m pytest tests/test_viewer_js.py tests/test_practice_js.py -q` -- PASS.
- [ ] **Step 5: Commit** -- `git commit -m "viewer: Release on every delivery chart"`

---

### Task 10: Check it on real games, and the ship gate

**Files:** none in the repo beyond results notes; scripts stay in `scripts/release/`.

- [ ] **Step 1: Plumbing** -- on the laptop, analyze `dmljQzQr1xo` (10/20 S2, `s_1TH0`) with the branch; compare each rock's `line.release` with `datasets/releasemarks/s_1TH0dJNrbHeKLARuj.json`. These rocks were trained on, so expect nearly all within 0.3 m; anything beyond means the production crops differ from `cut.py`'s.
- [ ] **Step 2: A/B** on the Ryzen outside league windows (`run_ab.sh`, main vs branch, two games): only `line.release`, `processing_version` and the pipeline version differ.
- [ ] **Step 3: Cost** -- `liveend_t.py` on the Ryzen: the release pass ≤ 3 s an end and `MB kept` ≤ 400, other passes unchanged; note the worker's peak memory with three live streams.
- [ ] **Step 4: Ship gate** -- cut 20 rocks of `s_0hx8wuVKeMmy4evaI` (10/20 S5 Hamzeh v Yalowicki; two per player + four, seeded) with `scripts/mark_release.py` on the laptop, serve it, the user marks; score the branch's `line.release` against the marks. **≥ 15 of 20 within 0.5 m: go on. Fewer: stop and bring the numbers to the user.** Commit the marks to `datasets/releasemarks/`.

---

### Task 11: Ship

- [ ] **Step 1:** `Dockerfile.worker`: `ARG RELEASE_MODEL=release1.pt`, `COPY weights/${RELEASE_MODEL} /opt/curling/weights/${RELEASE_MODEL}`, `CURLING_SCORE_RELEASE_WEIGHTS=/opt/curling/weights/${RELEASE_MODEL}` beside the spin lines. Commit.
- [ ] **Step 2:** Merge to main (only this branch's commits; the checkout is shared with other sessions).
- [ ] **Step 3:** Deploy per the project's deploy notes: clean worktree, checksum rsync to the Ryzen, `compose build worker`, `up -d` when no stream is live; `deploy-api.sh` for the viewer (hand the command to the user if it is denied). Verify the workers with `compose exec ... python -c` (`releasemodel.default_model()` loads) and the bundles by sha256.
- [ ] **Step 4:** No reprocess unless the user asks. Report what shipped.
