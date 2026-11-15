# Camera-direct phase 2: recording without a composer, practice on the camera feed (implementation plan)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** a practice session records each of its sheet's four cameras into a file of its own, with no composer. The
watch reads each view from its own file, and finds the same throws as from the composite.

**Architecture:**
- **Capture.** Each capture writes its camera's packets as MPEG-TS into `<role>.ts`, with pts set to its
  `CameraClock` time. The files share the recorder's zero, so every reader's `t` is the recorder's time.
- **Recorder.** `CameraRecorder.path` becomes a `Feed` of kind `"cameras"` over those files. Each view is read from
  its own file through its `Cut`. Turned and scaled cuts go through a PyAV filter graph, the same chain as the
  FFmpeg readers use.
- **Calibration.** It still happens in composite pixels. A composite frame is assembled in numpy from the four
  views, with the sheet layout's placements as the panels. The result is then moved into each view's own pixels.

**Tech Stack:** Python 3, PyAV (demux, mux, filter graph), FFmpeg CLI, pytest.

**Spec:** `docs/superpowers/specs/2026-10-25-pluggable-input-design.md`: "Capture over SRT, and recording without a
composer", "Practice", "Phases and gates" item 2. Phase 1's hand-on list:
`docs/superpowers/plans/2026-10-25-feeds-phase1.results.md` § "Carried into phase 2".

## Global Constraints

- Everything is on branch `camera-direct`, in `.claude/worktrees/camera-direct`. Nothing merges to main.
- Run test subsets only. The full suite OOMs on the dev box (exit 137). Use
  `/home/tcuser/src/curling_score/.venv/bin/python -m pytest -q -p no:cacheprovider <files>`.
- Quote times in Seattle time (PST).
- Ryzen runs (`curling@100.85.240.114`):
  - never during a league (from 17:00 PST on league nights);
  - never while a practice is live. Check first with `pgrep -f curling_score.cameras.capture` and for `rec.*.ts`
    or `*.ts` written in the last 2 min under `/data/wdd/curling-cache*/live`;
  - camera composers total at most 2. This plan runs none against live cameras.
- At most 2 of our receivers per house camera.
- No new VOD downloads.
- No deploy to the workers without the user's go. Restarts go through `gated_restart.sh`, with the version bump in
  the morning.
- The view names stay `top`, `bottom`, `left`, `right`, `board`. Their camera roles, from the sheet layouts (house_h
  is above house_a, and long_a is left of long_h, on all five sheets): `top`→`house_h`, `bottom`→`house_a`,
  `left`→`long_a`, `right`→`long_h`, `board`→`long_h`.
- The league path (YouTube feeds, `count_frame`, `profile.panels_file`, the board, fpframes) is untouched. It is
  phase 3's.

## Review Focus

1. **A camera whose capture exits mid-session.** The recording goes on: `head_s` follows the cameras still running.
   That view's reads stop at its last frame, and the watch keeps publishing the other houses' throws. It does not
   crash or stall. (Task 4, `test_head_follows_the_cameras_still_running`; Task 2, `test_a_view_past_its_files_end_gives_what_there_is`.)
2. **Silent misalignment between views.** A packet written at recorder time t must read back at t through
   `frames.window` on its own file, whatever moment the camera's first packet arrived. (Task 3,
   `test_a_packet_reads_back_at_its_recorder_time`.)
3. **A saved calibration from the composite era applied to camera views.** Composite rects on per-view frames would
   misplace every stone. A saved calibration with no transforms is never used on a camera sheet. (Task 6,
   `test_a_composite_era_calibration_is_not_loaded`.)
4. **Reading at the head.** Whatever `head_s` reports is already readable on disk, so a watch reading up to it
   never misses a frame that was written. (Task 3, `test_a_written_packet_is_readable_at_once`.)
5. **A capture that cannot write (disk full).** It exits 1 and says so. A thread does not keep logging forever.
   (Task 3, `test_a_write_error_ends_the_capture`.)

---

### Task 1: Camera feeds and their cuts; feed hardening

**Files:**
- Create: `src/curling_score/feeds/cameras.py`
- Modify: `src/curling_score/feeds/feed.py`, `src/curling_score/feeds/cut.py`, `src/curling_score/feeds/__init__.py`
- Test: `tests/test_feeds.py`

**Interfaces:**
- Produces:
  - `Feed(kind, paths, sheet: int | None = None)`. `paths` is a `MappingProxyType`, and `sheet` is part of the
    hash.
  - `Feed.path_for(name)` raises `ValueError` naming the view for an unknown name.
  - `str(feed)` never raises: a feed with several files reads `"cameras feed: a.ts, b.ts, ..."`. `os.fspath` still
    raises for it.
  - `Cut(...)` coerces `crop` to a tuple of ints, and `size` to a tuple or None.
  - `feeds.cameras`:
    - `VIEW_ROLE: dict[str, str]` (the mapping in Global Constraints);
    - `camera_feed(directory: Path, sheet: int) -> Feed`, kind `"cameras"`, each view at
      `directory / f"{VIEW_ROLE[view]}.ts"`;
    - `view_cut(name: str, p: Placement) -> Cut`, crop-only (`Cut(name, p.crop)`) when `p.rotate == 0` and
      `p.out[2:] == p.crop[2:]`, else `Cut.from_placement(name, p)`;
    - `view_cuts(sheet_layout) -> dict[str, Cut]` for `top`, `bottom`, `left` and `right`;
    - `placement_for(sheet_layout, name) -> Placement`.

- [ ] **Step 1: Write the failing tests** in `tests/test_feeds.py`:
  - `test_camera_feed_puts_each_view_in_its_cameras_file`: `camera_feed(Path("d"), 2).path_for("top") == Path("d/house_h.ts")`. Likewise
    `bottom` → `house_a.ts`, `left` → `long_a.ts`, and `right` and `board` → `long_h.ts`. `kind == "cameras"` and
    `sheet == 2`.
  - `test_view_roles_match_every_sheets_layout`: for sheets 1-5, `layout.load(s)`:
    - house_h's `out[1]` < house_a's `out[1]`;
    - long_a's `out[0]` < long_h's `out[0]`.
  - `test_long_views_are_crops_and_houses_are_turned`, for every sheet:
    - `view_cuts(...)["left"].is_crop` and `["right"].is_crop`;
    - `["top"].rotate == 90` and `["top"].out_size == tuple(placement.out[2:])`.
  - `test_a_scaled_long_view_is_not_a_crop`: a `Placement("long_a", (0, 0, 800, 1080), 0, (0, 0, 400, 540))` gives
    a cut that is not a crop, with `out_size == (400, 540)`.
  - `test_feed_str_names_its_files_and_never_raises`: `"cameras feed" in str(camera_feed(...))`, and `os.fspath` of
    it raises `TypeError`.
  - `test_path_for_an_unknown_view_is_a_value_error`: `pytest.raises(ValueError, match="no such view: tpo")`.
  - `test_feed_paths_are_read_only`: assigning to `feed.paths["top"]` raises `TypeError`.
  - `test_cut_coerces_its_crop`: `Cut("x", [1, 2, 3, 4]).crop == (1, 2, 3, 4)`, and it hashes.
- [ ] **Step 2: Run them.** Expected: FAIL (no `feeds.cameras`, no `sheet`, and the KeyError and str behaviours).
- [ ] **Step 3: Implement** the Produces list. `Feed.__eq__` compares `kind`, `sheet` and `paths` (the dataclass
  compares a MappingProxyType as a mapping). Keep `as_feed` returning a YouTube feed with `sheet=None`.
- [ ] **Step 4: Run `tests/test_feeds.py`, then the phase 1 readers:** `tests/test_longview.py tests/test_profile.py
  tests/test_sequence*.py tests/test_frames*.py`. Expected: PASS.
- [ ] **Step 5: Commit:** `feeds: camera feeds and their cuts; feed hardening from phase 1's reviews`.

### Task 2: Reading views through turned and scaled cuts, across files

**Files:**
- Modify: `src/curling_score/feeds/cut.py`, `src/curling_score/ingest/frames.py`
- Test: `tests/test_feeds.py`, `tests/test_frames.py`

**Interfaces:**
- Produces:
  - `Cut.frames()` returns a callable `av.VideoFrame -> np.ndarray` (BGR). It holds one PyAV filter graph, `buffer →
    <steps()> → format=bgr24 → buffersink`, built on its first frame, and serves one pass over a file. A crop-only
    cut keeps the phase 1 path: convert to BGR (`frames._converter`), then `apply`. Its bytes are unchanged.
  - The frame readers take a turned or scaled `Cut` wherever they take a crop: `window`, `chunked`, `windows`, and
    `keyframe_sweep(path, ..., cut=None)` / `spread_keyframes(path, ..., cut=None)`. With a `Cut`, the file comes
    from the feed (`path_for(cut.name)`). The `NotImplementedError("phase 2")` raises go.
  - `windows(path, specs, end_s)` with specs in several files decodes each file once, one file after another. A
    second thread is not used: PyAV in a second thread has deadlocked here before. Each spec still gets exactly what
    `window` would give it. Specs in one file keep the single decode.
- Consumes: Task 1's `view_cut`, `camera_feed`.

- [ ] **Step 1: Write the failing tests:**
  - `test_a_turned_cut_matches_ffmpegs_pixels`:
    - Make a 2 s 1920x1080 `testsrc2` H.264 MPEG-TS in `tmp_path` with the ffmpeg CLI.
    - Take sheet 2's `top` cut. Decode frame 0 through `Cut.frames()`. Separately, decode it with `ffmpeg -i f.ts
      -frames:v 1 -vf "<steps()>,format=bgr24" -f rawvideo -`.
    - Assert `np.array_equal`. If PyAV's bundled FFmpeg and the CLI's differ by a level or two, measure it and record
      a ruling with the max and mean difference; do not loosen the test silently.
  - `test_a_crop_cut_reads_as_before`: a crop-only cut through `window` gives the same bytes as the phase 1 rect
    path.
  - `test_windows_reads_two_files`: two `testsrc2` files of different colours (`-vf hue=h=90` on one) as a feed's
    `top` and `bottom`. One `windows` call with a spec on each gives each spec the frames `window` gives it, by `t`
    and by bytes.
  - `test_a_view_past_its_files_end_gives_what_there_is`: `windows` to `end_s` past one file's end ends that spec at
    its last frame. The other spec still gets its frames, and nothing raises.
  - `test_keyframe_sweep_through_a_cut`: every frame it yields has shape `(h, w, 3)` with `(w, h) == cut.out_size`.
- [ ] **Step 2: Run them.** Expected: FAIL with `NotImplementedError("phase 2")`.
- [ ] **Step 3: Implement** the Produces list. In `_source`, return `(file, frame_fn)`, where `frame_fn` maps an
  `av.VideoFrame` to BGR: `Cut.frames()` for a cut, `_converter()` plus the slice for a rect, and `_converter()`
  alone for a whole frame. Build it once per pass. In `windows`, group the specs by file, keeping their indices.
- [ ] **Step 4: Run** `tests/test_feeds.py tests/test_frames*.py tests/test_sequence*.py tests/test_longview*.py`.
  Expected: PASS.
- [ ] **Step 5: Commit:** `frames: views through turned and scaled cuts, and from several files`.

### Task 3: Each capture writes its camera's file

**Files:**
- Modify: `src/curling_score/cameras/capture.py` (the module docstring's Time, Threads and Exits sections too)
- Test: `tests/test_cameras_capture.py`

**Interfaces:**
- Produces:
  - **Command line:** `python -m curling_score.cameras.capture --camera NAME --out DIR/<role>.ts --first
    DIR/first-<role>.h264 --t0 T`. `--fifo` goes.
  - **`_TsFile(path)`:** `write(data: bytes, at: float, key: bool) -> bool`, and a `head: float` attribute holding
    the time of the last packet written.
    - Its MPEG-TS muxer runs over the opened file with `{"mpegts_copyts": "1", "flush_packets": "1"}`, and the
      stream copies the first keyframe's codec parameters (`add_stream_from_template`, as `_Nut` did).
    - The time base is 1/90000, with `pts = dts = max(round(at * 90000), last + 1)`.
    - **The first packet goes at pts 0**, whatever its `at`. It is a keyframe (`Holder`), and holding the camera's
      first picture from the recording's zero is what makes every camera's file start at the same `t`. Write that
      reason in the class docstring.
    - It writes synchronously under the `Holder`'s lock. A write to the page cache does not block; a write error
      propagates.
  - **Head lines:** every `HEAD_EVERY_S = 0.5` the main loop prints `@head <seconds, 3 decimals>` on stdout, flushed,
    once `head` has moved.
  - **Gone:** `_Nut`, `_Fifo`, `BACKLOG`, `MAX_AGE_S`, the FIFO-reader exit and the fifo-writer thread. `Holder`
    (the ticker), `Reconnects`, `CameraClock`, `PtsUnwrap`, `FIRST_S`, the first-keyframe copy and the parent check
    stay.
  - **Exits:** a write `OSError` logs `capture <camera>: cannot write <path>: <error>` at ERROR and exits 1.

- [ ] **Step 1: Write the failing tests** (keep the existing helpers `h264`, `camera_h264`, `mpegts` and
  `fake_ffmpeg`):
  - `test_a_packet_reads_back_at_its_recorder_time`:
    - Write a keyframe at `at=0.613`, then 59 frames at `0.613 + i / 60`.
    - `frames.stream_start_s(path) == 0.0`, the first frame reads at `t == 0.0`, and frame 30 at
      `pytest.approx(0.613 + 30 / 60, abs=1 / 90000)`.
  - `test_a_written_packet_is_readable_at_once`: after each `write` returns, a fresh `av.open(path)` demuxes every
    packet written so far.
  - `test_times_never_go_back`: a packet whose `at` is before the last is written 1/90000 s after it.
  - `test_a_write_error_ends_the_capture`: with a `_TsFile` whose file object raises `OSError(28, "No space left")`,
    the capture exits 1. Its stdout has the ERROR line naming the path, and no ffmpeg child is left (the `/proc`
    check the existing tests use).
  - `test_head_lines_follow_what_was_written`: run a capture against the fake ffmpeg's `send` mode. Its stdout has
    `@head` lines, increasing, and the last within 0.6 s of the last packet's time.
  - Rewrite the FIFO tests (`test_packets_from_an_mpegts_source_reach_the_fifo_with_their_times` and the rest):
    - they read `<role>.ts` and assert the same times;
    - delete the ones that test only the FIFO (late drops, a reader going away). Each such deletion is ledgered.
- [ ] **Step 2: Run** `tests/test_cameras_capture.py`. Expected: the new tests FAIL (no `_TsFile`, no `--out`).
- [ ] **Step 3: Implement** the Produces list.
- [ ] **Step 4: Run** `tests/test_cameras_capture.py`. Expected: PASS.
- [ ] **Step 5: Commit:** `capture: write the camera's own MPEG-TS on the recording's clock; no FIFO`.

### Task 4: The recorder without a composer

**Files:**
- Modify: `src/curling_score/cameras/recorder.py` (docstring rewritten), `src/curling_score/cameras/layout.py` (the
  docstring says the composer is now only the offline tool's)
- Test: `tests/test_cameras_recorder.py`

**Interfaces:**
- Consumes: Task 1's `camera_feed`; Task 3's command line and `@head` lines.
- Produces:
  - **`CameraRecorder.path`** is `camera_feed(self.dir, sheet)`. It is a `Feed`, never one camera's file.
  - **`head_s()`** is the minimum `@head` over the captures still running, taken from their relays. It is 0.0 until
    every camera has reported a head. With none running, it is the last minimum.
  - **`check()`:**
    - `_end` (LiveError) when every capture has exited, or when the head has not moved for `STALL_S`.
    - The cap (`max_s`, `WALL_MARGIN_S`) is unchanged.
    - A capture that exits alone is logged at WARNING once (`camera <name>'s capture exited (<code>); its view stops
      at <t> s`), and the recording goes on.
  - **`start()`** spawns the captures with `--out`, checks the first keyframes exactly as now, and spawns nothing
    else. `stop()` stops the captures.
  - **Gone:** `_Tail`, `composite_s`, `PROBE`, `DECODE_THREADS`, `FILTER_THREADS`, `COMPOSER_EXIT_S`, `TAIL_*`,
    `_spawn_composer`, the FIFOs.
  - **`layout.filtergraphs` stays**, unused by the product. Task 8's offline composer uses it.
  - **The relay:** a line matching `^@head (\d+\.\d+)$` sets that role's head and is not logged.

- [ ] **Step 1: Write the failing tests:**
  - `test_path_is_the_camera_feed`: `rec.path == camera_feed(dir, 2)`, and `os.fspath(rec.path)` raises.
  - `test_head_is_the_slowest_camera`: with the fake popen's captures printing `@head 4.000`, `@head 3.500`,
    `@head 5.000` and `@head 4.200`, `head_s() == 3.5`.
  - `test_head_follows_the_cameras_still_running`: when the 3.5 camera exits, the next `check()` logs one WARNING,
    raises nothing, and `head_s() == 4.0`.
  - `test_every_camera_gone_ends_the_recording`: LiveError `every camera of sheet 2 has stopped`.
  - `test_a_head_that_stops_moving_is_a_stall`: with clock control, `STALL_S` without head movement gives LiveError
    `sheet 2's recording stalled at 4 s`.
  - `test_no_composer_is_started`: the fake popen saw exactly four commands, all `-m curling_score.cameras.capture`,
    each with `--out <dir>/<role>.ts`.
  - Update the start tests (first keyframes, probe refusal, cleanup after a failed start) to the new command line,
    and delete the composer ones. Each deletion is ledgered.
- [ ] **Step 2: Run** `tests/test_cameras_recorder.py`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement** the Produces list.
- [ ] **Step 4: Run** `tests/test_cameras_recorder.py tests/test_cameras_capture.py tests/test_live_recorder.py
  tests/test_service_live.py`. Expected: PASS.
- [ ] **Step 5: Commit:** `recorder: each camera into its own file, no composer`.

### Task 5: Calibrating a camera feed

**Files:**
- Modify: `src/curling_score/feeds/cameras.py`, `src/curling_score/analyze.py` (`calibrate_from`),
  `src/curling_score/live/session.py` (`VideoPipeline.calibrate`)
- Test: `tests/test_feeds.py`, `tests/test_analyze_stages.py`

**Interfaces:**
- Consumes: Tasks 1-2.
- Produces:
  - **`feeds.cameras.view_keyframes(feed, cuts, until_s, count, *, start_s=None) -> dict[str, list[np.ndarray]]`.**
    For each view, `frames.spread_keyframes` on its file through its cut, with the same `count`, `start_s` and
    `until_s`. The lists are trimmed to the shortest.
  - **`feeds.cameras.compose(views: dict[str, np.ndarray], sheet_layout) -> np.ndarray`.** A 1920x1080 BGR frame of
    the layout's background grey, with each view pasted at its placement's `out` x, y. A view whose shape is not its
    `out` size raises ValueError.
  - **`analyze.calibrate_from(frames, *, skip_longview, progress, panels=None, side_rects=None)`.**
    - Given `panels` (a `layout.PanelLayout`), `detect_panels` is skipped.
    - Given `side_rects`, `sideview.locate` is skipped.
    - Without either, the behaviour is byte-identical to now.
  - **`feeds.cameras.localize(setups, sideviews, cuts) -> (setups, sideviews)`.**
    - Each `PanelSetup` / `SideView` is replaced by `dataclasses.replace(x, rect=(0, 0, *cut.out_size), name=n,
      transform=cut)`.
    - It raises ValueError when an input's rect is not its placement's `out` (it must have been calibrated with
      Task 5's panels).
  - **`feeds.cameras.calibrate(feed, until_s, *, skip_longview, progress) -> (panels, setups, sideviews)`.**
    - It loads `layout.load(feed.sheet)` and takes `view_keyframes(feed, view_cuts, until_s, analyze.CALIB_FRAMES)`.
    - It composes each set of frames.
    - It calls `calibrate_from` with `panels = PanelLayout(top=out(house_h), bottom=out(house_a))` and
      `side_rects = {"left": out(long_a), "right": out(long_h)}`.
    - It returns `panels` as composed, and `setups` / `sideviews` localized.
  - **`VideoPipeline.calibrate(path, until_s)`** takes this route when `as_feed(path).kind == "cameras"`. The
    YouTube route is unchanged.

- [ ] **Step 1: Write the failing tests:**
  - `test_compose_places_each_view_at_its_out`: with sheet 2's layout and four solid-colour views of the out sizes,
    each colour appears exactly in its `out` rectangle, and the background grey elsewhere.
  - `test_compose_refuses_a_wrong_sized_view`.
  - `test_calibrate_from_with_given_panels_skips_detection`: monkeypatch `layout.detect_panels` and
    `sideview.locate` to raise. With `panels` and `side_rects` given, `calibrate_panels` and `sideview.solve` see
    exactly those rects.
  - `test_localize_moves_setups_into_their_views`:
    - `rect == (0, 0, 297, 514)` and `transform == view_cuts(sheet2)["top"]`;
    - `calib` is unchanged;
    - `rect == (0, 0, 800, 1080)` with a crop-only transform for `left`;
    - a setup whose rect is not its `out` raises.
  - `test_pipeline_calibrates_a_camera_feed_through_the_layout`:
    - monkeypatch `feeds.cameras.view_keyframes` to return per-view frames cut from a real composite frame. Use
      `tests/data` if one is there; otherwise compose synthetic views.
    - Monkeypatch `analyze.calibrate_from` to capture its `panels` and `side_rects`.
    - Assert they are sheet 2's outs, and that the returned setups are localized.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement** the Produces list.
- [ ] **Step 4: Run** `tests/test_feeds.py tests/test_analyze_stages.py tests/test_live_session.py
  tests/test_profile.py tests/test_sideview*.py`. Expected: PASS.
- [ ] **Step 5: Commit:** `calibrate a camera feed: composed from its views, kept in each view's pixels`.

### Task 6: Saved calibrations and their check, on camera views

**Files:**
- Modify: `src/curling_score/cameras/calstore.py`, `src/curling_score/practice/pipeline.py` (`check_saved`)
- Test: `tests/test_cameras_calstore.py`, `tests/test_practice_pipeline.py`

**Interfaces:**
- Consumes: Task 5's `view_keyframes`, `view_cuts`.
- Produces:
  - **`CalStore.load(sheet)`** returns None for a calibration whose setups have no `transform`, a composite-era
    one, and logs once at WARNING: `saved calibration for sheet N is from the composite; a session will calibrate
    from the cameras and save a new one (or run cameras-calibrate)`. `save` is unchanged.
  - **`calstore.check_house(cal, frames)`** also takes `frames` as `dict[name, list[ndarray]]`, each view's own
    frames. It then reads `profile.panel_median(frames[name], setup.rect, name)`. A list keeps today's behaviour.
  - **`PracticePipeline.check_saved(path, from_s, until_s, cal)`.** For a camera feed it passes
    `view_keyframes(feed, {n: s.cut for n, s in cal.setups.items()}, until_s, CHECK_FRAMES, start_s=from_s or
    None)`.

- [ ] **Step 1: Write the failing tests:**
  - `test_a_composite_era_calibration_is_not_loaded`: save a `Calibration` whose setups have no transform. `load`
    returns None and logs the WARNING once.
  - `test_a_camera_calibration_round_trips`: a localized calibration saves and loads equal.
  - `test_check_house_reads_each_views_own_frames`: with a dict of per-view frames, each holding a drawn 12-ft
    ring at the calibration's centre and radius, the result is `AGREES`. Moving one view's ring 3x RING_AGREE_PX
    gives `DISAGREES`.
  - `test_check_saved_on_a_camera_feed_reads_per_view_keyframes`: monkeypatched `view_keyframes` is called with the
    setups' cuts and `CHECK_FRAMES`.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement** the Produces list.
- [ ] **Step 4: Run** `tests/test_cameras_calstore.py tests/test_practice*.py tests/test_cli_cameras_calibrate.py`.
  Expected: PASS.
- [ ] **Step 5: Commit:** `calstore: camera calibrations, checked on each view's own frames`.

### Task 7: Practice clips and overlays from the camera files

**Files:**
- Modify: `src/curling_score/practice/clips.py`, `src/curling_score/practice/overlay.py`
- Test: `tests/test_practice_clips.py`, `tests/test_practice_overlay.py`

**Interfaces:**
- Consumes: Tasks 1-2.
- Produces:
  - **`Clipper.make`** builds `views = {name: (feed.path_for(name), sv.cut) for name, sv in
    cal.sideviews.items()}`, with `feed = as_feed(self.recording.path)`.
  - **`decode_args`** takes each view's `(source, cut)`. A piece's filter is `trim=...,setpts=PTS-STARTPTS,
    <cut.steps()>,scale=W:H,setsar=1,fps=F`. A `None` cut means the whole frame, as `None` did before. A bare
    4-tuple is still accepted, as `Cut.of(rect)`.
  - **Sizes from the cut:** `exit_time` uses `view.cut.out_size[1]` and `overlay.to_view` uses
    `view.cut.out_size`, in place of `view.rect[2:]`.
- [ ] **Step 1: Write the failing tests:**
  - `test_clip_pieces_come_from_each_views_file`: a camera feed and two side views. The ffmpeg args name
    `long_a.ts` and `long_h.ts` as inputs. Each piece's graph holds its cut's `steps()`.
  - `test_overlay_fractions_use_the_cut_size`: a side view whose rect would give other fractions than its
    `cut.out_size`. The overlay uses the cut's size.
  - The existing composite tests: a crop's `steps()` differs from the old box (`exact=1`). Update their expected
    strings and ledger it.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement** the Produces list.
- [ ] **Step 4: Run** `tests/test_practice_clips.py tests/test_practice_overlay.py tests/test_practice*.py
  tests/test_game_clips.py tests/test_gameclips.py`. Expected: PASS.
- [ ] **Step 5: Commit:** `practice clips and overlays: each view from its own camera's file`.

### Task 8: Replaying camera files, and the gate's tools

**Files:**
- Modify: `src/curling_score/live/replay.py`, `src/curling_score/cli.py` (`practice-replay`)
- Create: `scripts/cameras/retime_srt.py`, `scripts/cameras/compose_offline.py`, `scripts/cameras/compare_replays.py`
- Test: `tests/test_live_replay.py`, `tests/test_cli_practice_replay.py`, `tests/test_cameras_scripts.py`

**Interfaces:**
- Consumes: Task 1's `camera_feed`; Task 3's `_TsFile`; `capture.CameraClock`, `capture.delivery_delay_s` and
  `capture.PtsUnwrap`; `layout.filtergraphs`.
- Produces:
  - **`ReplayRecording(..., copyts: bool = False)`.** With `copyts` its ffmpeg gets `-copyts -mpegts_copyts 1`, so
    the output keeps the source's times.
  - **`replay.CameraReplay(source_dir, out_dir, sheet, speed=1.0)`.**
    - It runs four `ReplayRecording(source_dir/<role>.ts, out_dir/<role>.ts, speed, copyts=True)`, from the start
      of each file.
    - `path = camera_feed(out_dir, sheet)`; `head_s()` is the minimum; `ended()` means all have ended; `failed()`
      means any has failed; `t0_s = 0.0`; `check()`; `stop()`.
  - **`practice-replay`** takes either `video` or `--cameras DIR --sheet N`, not both. With `--cameras`, `--from` and
    `--to` default to 0 and the end, and the replay is a `CameraReplay`.
  - **`scripts/cameras/retime_srt.py SRC_DIR OUT_DIR --sheet N --start S --end E`.** It writes what the new capture
    would have written, had Start been pressed at `S`.
    - **The clock.** S and E are seconds after the earliest arrival in SRC_DIR's `.pts` logs. Each `.pts` line is
      `pts_s, arrival_monotonic, wall, raw_pts`.
    - **Timing.** For each of the sheet's four cameras (`inventory.sheet_cameras(N)`):
      - pair each packet of `<camera>.ts` with its `.pts` line by raw pts;
      - time it with `CameraClock(t0 + delivery_delay_s(camera))`, `t0` = earliest arrival + S, fed `(pts_s,
        arrival)` as the capture feeds it.
    - **Writing.** It starts at that camera's first keyframe arriving at or after `t0`, and writes through
      `_TsFile` to `OUT_DIR/<role>.ts` until E.
    - **The wall time.** It prints the wall time of S, in PST.
  - **`scripts/cameras/compose_offline.py DIR OUT.ts --sheet N`.** It runs the old composer's command over the four
    files in place of FIFOs: `-copyts`, `layout.filtergraphs`, `h264_nvenc` at the same rate settings. Copy the
    arguments from Task 4's deleted `_spawn_composer` (git history) into the script.
  - **`scripts/cameras/compare_replays.py A_DIR B_DIR`.**
    - It pairs the two runs' `throws.jsonl` by house and colour, then `t_rest_s` (or `t_release_s` for a throw
      that never arrived) within 1.0 s.
    - It prints the unmatched throws on each side, and per pair the rest distance in cm and the split difference in
      ms.
    - It prints the camera run's latency p50/p90 (`latency_s`).
    - It exits 1 when the gate (Task 9) fails.
- [ ] **Step 1: Write the failing tests:**
  - `test_camera_replay_keeps_four_files_on_one_clock`: two-second `_TsFile` writes for four roles, each starting at
    pts 0 with packets at `i / 60`. After `CameraReplay` at speed 50 ends, each output file reads frame 30 at
    `t == 0.5`.
  - `test_camera_replay_head_is_the_slowest`: with stubbed recordings at heads 1.0, 2.0, 0.5 and 3.0, `head_s()`
    is 0.5.
  - `test_practice_replay_takes_cameras_or_a_video`: the parser rejects both and neither. With `--cameras` it
    builds a `CameraReplay`.
  - `test_retime_times_packets_as_the_capture_would`:
    - From a synthetic `.ts` and a `.pts` log with a steady 0.6 s delivery, retimed with `--start 0`, the first
      packet is at 0.0 (the zero rule) and packet k at `pts_s(k) - pts_s(first) + first_arrival - t0 -
      delivery_delay_s` within 1 ms.
    - A packet before `--start` is not written.
  - `test_compare_pairs_and_reports`: two small throws.jsonl files, with one missing throw on one side and a rest
    3 cm off. The report lists the unmatched throw, says `3.0 cm`, and the exit is 1 (unmatched).
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement** the Produces list.
- [ ] **Step 4: Run** `tests/test_live_replay.py tests/test_cli_practice_replay.py tests/test_cameras_scripts.py`.
  Expected: PASS.
- [ ] **Step 5: Commit:** `practice-replay from camera files; retime, offline compose and compare for the gate`.

### Task 9: The gate on the Ryzen, offline

No product code. The results go in `docs/superpowers/plans/2026-11-04-camera-direct-phase2.results.md`.

- [ ] **Step 1: Before the gate runs,** run the camera subset once more, together:
  `tests/test_feeds.py tests/test_frames*.py tests/test_cameras_*.py tests/test_practice*.py tests/test_live_*.py
  tests/test_cli_practice_replay.py tests/test_cameras_scripts.py tests/test_analyze_stages.py`. Expected: PASS.
- [ ] **Step 2: Build the image** `curling-worker:cd-p2` from this branch on the Ryzen. Rsync the worktree to
  `/data/wdd/scratch/camera-direct/p2/src`, without excluding the layouts' JSON, then `docker build`. Expected: the
  build log ends in success.
- [ ] **Step 3: Pick the span and retime it.**
  - Choose 45-60 min of `srt-s3-1102/` (11/02 sheet 3 league, 19:21-23:00 PST) with ends played toward both houses.
  - Run `retime_srt.py` into `p2/cams/` in the container.
  - Expected: four files. `ffprobe` gives each a start of 0.000 and the same duration within 1 s.
- [ ] **Step 4: Compose the same files:** `compose_offline.py p2/cams p2/composite.ts --sheet 3`. Expected: a
  1920x1080 60 fps file of the same duration. Look at 3 frames: the four views sit where YouTube put them.
- [ ] **Step 5: Replay both ways, one after the other, at speed 1, at night.** No league, no live practice (Global
  Constraints).
  - `practice-replay --cameras p2/cams --sheet 3 --out p2/run-cams`;
  - `practice-replay p2/composite.ts --from 0 --to <dur> --out p2/run-comp`.
  - Expected: both finish, and each `throws.jsonl` has throws.
- [ ] **Step 6: Compare:** `compare_replays.py p2/run-comp p2/run-cams`. The gate (spec, phase 2):
  - the same throws on both sides, no unmatched;
  - rest within 5 cm on every arrived pair, median ≤ 2 cm;
  - camera run latency p90 ≤ 20 s;
  - the calibrations agree. Both runs print their panel px/m and the side views' rows; record them side by side.
    House px/m within 1%.

  If a throw is unmatched or a rest is off by more, find which side is right against the long camera (memory
  `validating-release-changes`) before ruling. A difference the per-view path gets right is not a failure; record
  it.
- [ ] **Step 7: Write the results file** with the numbers and the three frames. Commit with the scratch paths:
  `camera-direct phase 2 gate: per-view against composite, 11/02 sheet 3`.

### Task 10: Hand-off for the live check (stop for the user)

- [ ] **Step 1:** Stop here and report to the user:
  - **The gate's numbers.**
  - **That the live check needs a deploy of this branch to both workers.** It carries phase 1 for league games too,
    which was byte-identical on the harness.
  - **The deploy's steps:**
    - a morning version bump;
    - `gated_restart.sh`;
    - `cameras-calibrate` on each sheet at night, since the composite-era saved calibrations are no longer used;
    - the first practice session watched and checked by hand against the long camera.
  - **That practice's two-sheet cap can rise once the composers are gone.** It needs the user's say, after the first
    live sessions.

  Nothing in this task runs without the user's go.
