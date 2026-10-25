# Feeds, phase 1: the input as a module, YouTube only — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for
> tracking.

**Goal:** every analysis read of a video goes through a `Feed` (which file a view lives in) and a `Cut` (how
that view is taken from the file). With today's YouTube composite, the timelines come out byte-identical.

**Architecture:**
- **A `Cut` replaces the bare `(x, y, w, h)` rect.**
  - It is a crop, optionally followed by a quarter turn and a scale. These are `cameras/layout.Placement`'s
    terms.
  - A YouTube cut is only a crop, so every ffmpeg command, every numpy slice and every detection-cache key stays
    exactly what it is today.
- **A `Feed` maps each view name (`top`, `bottom`, `left`, `right`, `board`) to a file.**
  - A YouTube feed maps all five to the one composite.
  - A feed with one file behaves as that path (`__fspath__`/`__str__`). Code not yet moved keeps working, and
    phase 2's multi-file `CameraFeed` will fail loudly anywhere a single path is still assumed.
- **Calibrations carry their cut.** `PanelSetup` and `SideView` gain a `name` and an optional `transform`; their
  `cut` property falls back to a crop of `rect`. Pickles saved before this load unchanged.

**Tech Stack:** Python 3.12, PyAV, ffmpeg subprocesses, numpy, pytest.

**Spec:** `docs/superpowers/specs/2026-10-25-pluggable-input-design.md` (phase 1).

## Global Constraints

- **No behaviour change.** Timelines from `analyze`, `live-replay` and `practice-replay` must be byte-identical to
  main at the branch point (`a2c7fc9c`) on the same inputs.
- **Detection-cache keys stay identical for crop-only cuts** (`detect/cache.py`). Warm caches on the workers must
  keep hitting.
- **The ffmpeg `-vf` strings stay identical for crop-only cuts:** `crop={w}:{h}:{x}:{y}:exact=1,fps={fps}`.
- **View names keep today's position names:** `top`, `bottom`, `left`, `right`, plus `board` for the wall board's
  view. The timeline JSON is unchanged.
- **Rotations are clockwise degrees in `(0, 90, -90)`.** This is `cameras/layout.ROTATIONS`, and FFmpeg's transpose
  1 = clockwise, 2 = counter-clockwise.
- **Scope:** phase 1 does not implement `Cut.apply` for a turned or scaled cut, nor a feed with more than one file
  in a PyAV reader. Both raise `NotImplementedError("phase 2")`.
- **Left for phase 2,** where `CameraFeed` gives them a reason to move:
  - clip cutting (`practice/clips`, `game/clips`, `clipburn`);
  - building the strip proxy (`ingest/proxy.py`);
  - `fpframes` camera naming;
  - the offline `harvest/` and `train/` tools.
- **Branch:** `camera-direct` (worktree `.claude/worktrees/camera-direct`). Nothing merges to main.
- **Running tests:** run tests as
  `PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest <files>`, affected files only. The full
  suite OOMs on this box (exit 137).
- **Comments and naming** follow the surrounding code: explanatory docstrings for the why, short names.

## Review Focus

1. **A calibration pickled before this change** (`cameras/calstore` `sheet<N>.pkl`, `scripts/replay_end` setups)
   still loads, and its `cut` is a crop of its `rect`. Covered in Task 3.
2. **A `Feed` where a path was used as a key:** `str(video)` in `longview._keyframes`, the detection cache's
   file-identity key, and `seek_lead(path)` by suffix. Each gives the same answer as the bare path. Covered in
   Tasks 1, 2 and 4.
3. **The VOD strip proxy:** panels read from `proxies/<id>.<crop>.mp4` with translated rects. Their cut keeps its
   name, and their feed's `top`/`bottom` point at the proxy while `left`/`right`/`board` stay on the original.
   Covered in Task 6.
4. **Odd widths and heights** (side rect width 813): `exact=1` stays in the crop step, and `out_size` is the crop's
   own size. Covered in Task 1.
5. **One decode for both panels** (`frames.windows` / `sequence.detect_spans`): two cuts on the same file still
   decode once. Two files raise rather than silently decoding one of them. Covered in Task 2.

---

### Task 1: `feeds` package: `Cut` and `Feed`

**Files:**
- Create: `src/curling_score/feeds/__init__.py`, `src/curling_score/feeds/cut.py`,
  `src/curling_score/feeds/feed.py`
- Modify: `src/curling_score/cameras/layout.py` (`filtergraphs` builds its per-camera steps with `Cut`)
- Test: `tests/test_feeds.py`, plus the existing `tests/test_cameras_layout.py`, which must still pass unchanged

**Interfaces:**
- Produces, in `curling_score.feeds.cut`:
  ```python
  Rect = tuple[int, int, int, int]
  @dataclass(frozen=True)
  class Cut:
      name: str                          # view name; "" when unknown (a legacy rect)
      crop: Rect                         # x, y, w, h in the source file's pixels
      rotate: int = 0                    # 0 | 90 | -90, clockwise
      size: tuple[int, int] | None = None   # (w, h) after turn + scale; None = no scale
      out_size -> tuple[int, int]        # property
      is_crop -> bool                    # property: rotate == 0 and size is None
      def steps(self) -> str             # ffmpeg filter chain, no fps
      def apply(self, bgr: np.ndarray) -> np.ndarray
      def key(self) -> tuple             # cache-key part: self.crop when is_crop, else (crop, rotate, size)
      @classmethod
      def of(cls, rect_or_cut, name: str = "") -> "Cut"   # a Cut passes through; a 4-tuple becomes a crop
      @classmethod
      def from_placement(cls, name: str, p: "Placement") -> "Cut"
  ```
- Produces, in `curling_score.feeds.feed`:
  ```python
  VIEWS = ("top", "bottom", "left", "right", "board")
  @dataclass(frozen=True)
  class Feed:
      kind: str                          # "youtube" | "cameras"
      paths: Mapping[str, Path]          # view name -> file; every name in VIEWS
      def path_for(self, name: str) -> Path   # "" -> single() or ValueError
      def single(self) -> Path | None    # the one file when all views share it
      def __fspath__(self) -> str        # str(single()), else TypeError naming the files
      def __str__(self) -> str           # same as __fspath__
      def on(self, names: Iterable[str], path) -> "Feed"   # those views moved to another file
  def as_feed(video) -> Feed             # Feed passes through; a path -> kind "youtube", all VIEWS -> it
  ```
- `feeds/__init__.py` re-exports `Cut`, `Feed`, `as_feed` and `VIEWS`.

- [ ] **Step 1: Write the failing tests** in `tests/test_feeds.py`:
  - `test_crop_cut_steps_match_longview`:
    `Cut("left", (0, 0, 813, 1080)).steps() == "crop=813:1080:0:0:exact=1"`; `out_size == (813, 1080)`;
    `is_crop`.
  - `test_turned_cut_steps`:
    `Cut("top", (518, 255, 1319, 814), 90, (298, 510)).steps() ==
    "crop=1319:814:518:255:exact=1,transpose=1,scale=298:510"`; `out_size == (298, 510)`.
  - `test_turned_cut_without_scale_swaps_size`: rotate 90 with `size=None` gives `out_size == (h, w)` of the crop.
  - `test_apply_crop_is_the_slice`: on a random `(1080, 1920, 3)` uint8 array, `apply` equals
    `arr[y:y+h, x:x+w]` and is C-contiguous.
  - `test_apply_turned_raises`: raises `NotImplementedError`.
  - `test_key_of_crop_is_the_rect`: `Cut("", (1, 2, 3, 4)).key() == (1, 2, 3, 4)`.
  - `test_of`: `Cut.of((1, 2, 3, 4), "top") == Cut("top", (1, 2, 3, 4))`; a Cut passes through unchanged.
  - `test_from_placement` uses sheet 1's `house_h` placement: `crop=[518,255,1319,814]`, `rotate=90`,
    `out=[812,10,298,510]`. It checks `size == (298, 510)`.
  - `test_feed_single_path_is_the_path`: `f = as_feed(Path("/v/x.mp4"))`; `os.fspath(f) == str(f) == "/v/x.mp4"`;
    `f.path_for("left") == Path("/v/x.mp4")`; `as_feed(f) is f`.
  - `test_feed_on_moves_views`: `g = f.on(("top", "bottom"), "/p/x.strip.mp4")`;
    `g.path_for("top") == Path("/p/x.strip.mp4")`; `g.path_for("left") == Path("/v/x.mp4")`; `g.single() is None`;
    `os.fspath(g)` raises `TypeError`.
- [ ] **Step 2: Run them and see them fail.**
  Run `PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest tests/test_feeds.py -q`.
  Expected: they fail with `ModuleNotFoundError: curling_score.feeds`.
- [ ] **Step 3: Implement `cut.py` and `feed.py`** as above.
  - `steps()` puts `exact=1` on the crop. Add `transpose=1` for 90 or `transpose=2` for -90, then
    `scale={w}:{h}` when `size` is set.
  - In `layout.filtergraphs`, replace the inline step-building with `Cut.from_placement(p.role, p).steps()`. The
    output must be the same string.
- [ ] **Step 4: Run** `tests/test_feeds.py tests/test_cameras_layout.py`. Expected: all pass.
- [ ] **Step 5: Commit.** `feeds: Cut and Feed, the input's views and files`

### Task 2: frame readers take a `Feed` and a `Cut`

**Files:**
- Modify: `src/curling_score/ingest/frames.py`
  - `window`, `windows`, `chunked`: the `crop` parameter, and `path` may be a `Feed`;
  - `keyframe_sweep`, `sample_keyframes`, `spread_keyframes`, `keyframe_times`, `seek_lead`, `probe`: accept a
    single-file `Feed` through `os.fspath`.
- Test: `tests/test_frames.py`

**Interfaces:**
- Consumes `Cut`, `Feed` and `as_feed` from Task 1.
- Produces:
  - `window(path_or_feed, start_s, end_s, fps, crop: Rect | Cut | None = None)`. A `Cut` reads
    `as_feed(path).path_for(cut.name)` and yields `cut.apply(img)`. A rect or `None` behaves as today.
  - `windows(path_or_feed, specs, end_s)`: specs are `(start_s, fps, Rect | Cut | None)`. All specs must resolve to
    one file, else `NotImplementedError("phase 2: specs span files")`.
  - `chunked(..., crop=Rect | Cut | None)` passes it to `window`.
  - Everything else: `os.fspath(path)` at the top, so a single-file feed is its path.

- [ ] **Step 1: Write the failing tests** in `tests/test_frames.py`, reusing the module's existing synthetic-video
  fixture (or the way it makes one):
  - `test_window_cut_equals_rect`: for `Cut("left", r)` against rect `r`, `window(as_feed(p), ..., crop=Cut(...))`
    yields the same times and arrays as `window(p, ..., crop=r)`.
  - `test_windows_cuts_one_decode`: two cuts on one feed give the same frames as two `window` calls.
  - `test_windows_two_files_raises`: specs whose cuts resolve to different files raise `NotImplementedError`.
  - `test_keyframe_sweep_takes_feed`: the same `(t, frame)` sequence from `as_feed(p)` as from `p`.
  - `test_seek_lead_of_ts_feed`: `seek_lead(as_feed(Path("a.ts"))) == TS_SEEK_LEAD_S`.
- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement.** A small `_source(path, crop) -> (str_path, apply)` helper in `frames.py` resolves the
  file and the per-frame transform. A rect keeps today's slice code path, byte for byte.
- [ ] **Step 4: Run** `tests/test_frames.py`. Expected: all pass.
- [ ] **Step 5: Commit.** `frames: read a view of a feed`

### Task 3: calibrations carry their cut

**Files:**
- Modify:
  - `src/curling_score/game/profile.py`: `PanelSetup` gains `name: str = ""` and `transform: Cut | None = None`,
    plus a `cut` property; `calibrate_panels` sets `name` on each setup. `PanelSetup.crop(frame)` stays.
  - `src/curling_score/geometry/sideview.py`: `SideView` gains the same two fields and property. `solve`'s existing
    `name=` parameter is stored. `lateral_from_strip`, `solve_lateral` and any `dataclasses.replace` must keep
    `name` and `transform`.
  - `src/curling_score/analyze.py` (`_proxy_setups` keeps `name`).
- Test: `tests/test_profile.py`, `tests/test_sideview.py`, `tests/test_cameras_calstore.py`

**Interfaces:**
- Consumes `Cut` (Task 1).
- Produces `PanelSetup.cut -> Cut` and `SideView.cut -> Cut`. Each returns
  `self.transform or Cut(self.name, tuple(self.rect))`.

- [ ] **Step 1: Write the failing tests:**
  - `test_setup_cut_defaults_to_rect`.
  - `test_calibrate_panels_names_setups`: the names are `top`/`bottom`, using the module's existing panel fixture.
  - `test_sideview_solve_keeps_name`.
  - `test_old_pickle_has_a_cut`: a `PanelSetup` pickled with its new attributes deleted from `__dict__` (as
    pickled before this change) loads with `cut == Cut("", rect)`. Same for `SideView`.
  - `test_calstore_roundtrip_keeps_cut`.
- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement.** The defaults are class attributes, so old pickles fall back to them.
- [ ] **Step 4: Run** the three test files plus `tests/test_sideviews.py tests/test_analyze_side.py`. Expected: all
  pass.
- [ ] **Step 5: Commit.** `profile, sideview: a calibration knows its cut`

### Task 4: overhead detection and its cache read through the cut

**Files:**
- Modify: `src/curling_score/detect/sequence.py` (`detect_end`, `detect_span`, `detect_spans`: crop with
  `setup.cut`, path via the feed); `src/curling_score/detect/cache.py` (the key uses `cut.key()` where it used
  `rect`; the file identity comes from `os.fspath` of the resolved file).
- Test: `tests/test_detcache.py`, `tests/test_sequence*.py` if present, else a new `tests/test_sequence_feed.py`.

**Interfaces:**
- Consumes `PanelSetup.cut` (Task 3), and Task 2's readers.
- Produces: the same functions with `path` now `path | Feed`, and unchanged return types.

- [ ] **Step 1: Write the failing tests:**
  - `test_cache_key_crop_cut_equals_rect_key`: for a crop cut the cache key is identical to the key computed today
    with the rect. Pin today's key value from a fixture setup before changing the code.
  - `test_detect_span_feed_equals_path`: with a stub detector that records the crops it is given, the same crops
    for `as_feed(p)` as for `p`.
- [ ] **Step 2: Run them and see them fail** (the feed variant).
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `tests/test_detcache.py` and the sequence tests. Expected: all pass.
- [ ] **Step 5: Commit.** `sequence, detcache: read panels through their cut`

### Task 5: side views decode through the cut

**Files:**
- Modify:
  - `src/curling_score/detect/longview.py`: `_command`, `_run`, `_decode_now`, `_key`, `decode`, `soon`, `Plan`,
    `find_crossing`. Each takes `cut: Rect | Cut` where it took `rect`. The file is
    `as_feed(video).path_for(cut.name)`, the `-vf` is `f"{cut.steps()},fps={fps}"`, and the frame size is
    `cut.out_size`.
  - `src/curling_score/detect/lanekeep.py`: match by `Cut.of(...)` equality, not by the rect tuple.
  - The callers: change `view.rect` to `view.cut` wherever it is passed to `decode`/`Plan`/`soon`.
    - `game/hogtime.py`, `game/sidereleases.py`, `game/farfollow.py`, `game/broomtime.py`, `game/linetime.py`,
      `game/spintime.py`, `game/releasetime.py`;
    - `detect/sidemodel.py`, `detect/broommodel.py`.
    - Where code reads `view.rect[2]`/`[3]` as the decoded frame's size, use `view.cut.out_size`. Where it reads
      `view.rect[0]` to tell a full-width frame from a view-width one (`sidemodel.propose`, `broommodel.find`),
      leave it alone.
- Test: `tests/test_longview.py`, `tests/test_longview_ts.py`, `tests/test_lanekeep.py`

**Interfaces:**
- Consumes `SideView.cut` (Task 3) and `as_feed` (Task 1).
- Produces: `longview.decode(video: Path | Feed, cut: Rect | Cut, t0, t1, fps)`, with frames `(n, h, w, 3)` where
  `(w, h) = Cut.of(cut).out_size`.

- [ ] **Step 1: Write the failing tests:**
  - `test_command_crop_cut_identical`: `_command(p, Cut("left", r), t0, t1, 30)` equals `_command(p, r, t0, t1, 30)`
    as a list, for an odd-width `r = (0, 0, 813, 1080)`.
  - `test_command_feed_resolves_view_file`: for a feed with `left` on `a.ts` and `right` on `b.ts`, the command for
    `Cut("right", ...)` opens `b.ts`.
  - `test_lanekeep_matches_cut`: a decode kept under a crop cut is found by the same view's cut.
- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement.** The `_keyframes` cache keys on `str` of the resolved file, as today.
- [ ] **Step 4: Run** the three test files plus `tests/test_sideframes.py tests/test_analyze_side.py
  tests/test_analyze_split.py`. Expected: all pass.
- [ ] **Step 5: Commit.** `longview: decode a side view through its cut`

### Task 6: the pipelines carry a `Feed`

**Files:**
- Modify:
  - `src/curling_score/analyze.py`: `EndContext.path` / `read_path` become feeds.
    - `analyze()` builds `feed = as_feed(path)`.
    - With the strip proxy: `read_feed = feed.on(("top", "bottom"), proxy_path)` and `read_setups` as today.
    - `board_for_game` / `read_board` / `recording_board_reader` take the feed. Their whole-frame reads use
      `feed.path_for("board")`.
  - `src/curling_score/live/session.py`: the recording's path is wrapped with `as_feed`. Where it builds
    `EndContext`, `path` and `read_path` are that feed.
  - `src/curling_score/practice/pipeline.py`: the same.
  - `src/curling_score/game/profile.py` (`count_frame` / the sweep), `game/scoreboard.py` (`read_cards_at` etc.),
    `game/boardsplit.py`: accept a feed, and read whole frames from `path_for("board")`. Both panels still come
    from one keyframe of their (single) file.
- Test: `tests/test_analyze_stages.py`, `tests/test_analyze_write.py`, `tests/test_live_lane.py`,
  `tests/test_practice_pipeline.py`, `tests/test_profile.py`, `tests/test_scoreboard.py`

**Interfaces:**
- Consumes Tasks 1-5.
- Produces `EndContext.path: Feed` and `EndContext.read_path: Feed`, with the field names unchanged so the diff
  stays small.

- [ ] **Step 1: Write the failing test:** `test_endcontext_paths_are_feeds`, in `tests/test_analyze_stages.py`.
  With the module's fake stages, `analyze` builds an `EndContext` whose `path` and `read_path` are `Feed`s.
  - With a proxy, `read_path.path_for("top")` is the proxy and `read_path.path_for("left")` the original.
  - Without one, `read_path` equals `path`.
- [ ] **Step 2: Run it and see it fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the test files listed above. Expected: all pass, with no test edited other than the new one.
- [ ] **Step 5: Commit.** `analyze, live, practice: the pipelines read a feed`

### Task 7: gate — identical timelines

**Files:**
- Create: `scripts/feeds/ab_identical.sh` (the A/B driver).
- Results go in `docs/superpowers/plans/2026-10-25-feeds-phase1.results.md`.

**What the driver does:**
- On the Ryzen, it builds two throwaway containers from `curling-worker:local`, one with `a2c7fc9c`'s `src` mounted
  and one with this branch's.
- It runs `analyze` on the harness VODs already in the worker cache (memory `worker-cache-eviction`: the 11 harness
  videos), with a copy of the detection cache for each side, and `live-replay` on one kept live recording.
- It diffs the timelines with `cmp`.
- Use the cached video and a locally built `VideoInfo` (no YouTube calls; memory `practice-sessions` gotcha), at
  nice 10.

- [ ] **Step 1: Write the driver.** It takes `BASE_SRC`, `HEAD_SRC` and a list of video ids, writes
  `out/{base,head}/<id>/timeline.json`, and prints one `same`/`DIFF` line per id.
- [ ] **Step 2: Run it on 3 harness videos with warm caches.**
  - Expected: `same` × 3, and the detection cache gets no new entries on the head side (keys unchanged).
- [ ] **Step 3: Run it on all 11, plus one `live-replay`.** Expected: all `same`.
- [ ] **Step 4: Write the results file** with the command, the ids, the outcomes and the wall times.
- [ ] **Step 5: Commit.** `feeds phase 1: identical timelines on the harness`
