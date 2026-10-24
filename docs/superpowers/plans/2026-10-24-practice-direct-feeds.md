# Practice on the Club's Cameras Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Practice sessions read a sheet's four NDI cameras on the Ryzen. The
four feeds are rebuilt into a YouTube-style composite, and the existing practice
tracker runs on it unchanged, so practice works whenever no league is on.

**Architecture:** Each camera's H.264 is lifted out of libndi by a small C
stand-in for `libavcodec-ndi.so.61` (the packet tap). That tap feeds a
per-camera capture process, which writes a FIFO. One FFmpeg per sheet composes
the four FIFOs into a growing 1920x1080p60 MPEG-TS, using a per-sheet layout
fitted to YouTube's picture and NVENC. A `CameraRecorder` exposes that file
through the interface `PracticeWatch` already reads. The API creates camera
watches without a YouTube stream, gates Start (validated or club IP, no league
live), and ends every session when a league feed goes live.

**Tech Stack:** Python 3.12, cyndilib 0.1.3 (bundles libndi 6.3.2), FFmpeg 7.1
(Debian, in `curling-worker:local`), C (gcc against `libavcodec-dev`), PyAV,
OpenCV, FastAPI, Firestore/MemoryRepo, Docker Compose with the NVIDIA runtime.

**Spec:** `docs/superpowers/specs/2026-10-24-practice-direct-feeds-design.md`
(main 8f87dbd7). Read it first. The camera facts are in its "What is known"
section, and in `/data/wdd/scratch/ndi/` on the Ryzen.

## Global Constraints

- Composite: 1920x1080, 60 fps, laid out like YouTube's picture for that sheet.
  Encode with `h264_nvenc` at ≥ 20 Mbit/s.
- libndi needs `libavcodec.so.61` (FFmpeg 7). It runs only inside the worker
  image, never on the host.
- Pins: `cyndilib==0.1.3` (libndi 6.3.2). The tap is compiled against the
  image's own `libavcodec-dev`.
- Time every frame by its arrival at the Ryzen, never by the cameras' clocks.
  The HOUSE cameras' clocks are years off.
- At most one NDI connection per camera, and only while a session on that sheet
  is active. Maintenance reads (calibration, layout fitting, tests) keep only
  numbers, plus the empty-sheet reference frames used for layout fitting.
- Practice recordings are never kept. Clips are uploaded as today.
- Sessions:
  - end after `IDLE_S = 900.0` with no throw (counted from Start when nothing
    has been thrown);
  - `MAX_S = 10800.0` is unchanged;
  - a league `live` run on **any** sheet ends every session with reason
    `"league"` and blocks Start (leagues use all five sheets).
- Start needs a signed-in user with `role` in `("validated", "admin")`, **or** a
  request whose `client_ip(request)` is in `Settings.club_ips`.
- The camera path is behind `PRACTICE_SOURCE=cameras`. `youtube` stays as the
  rollback until one both-ways comparison has passed.
- Latency gate: throw-to-phone p90 ≤ 20 s with five sessions running (today
  13.8 s). Above the gate, optimise in this order: stop libndi's decode, spread
  watches across workers, GPU decode. Never cut sheets or fps without asking the
  user.
- The privacy page changes in the same release as the API.
- Run only the affected tests (`pytest tests/<file> -q`); the whole suite OOMs
  this box. `test_service_api::test_the_pages_load` already fails on main.

## Review Focus

1. **A camera stops mid-session** (a power-cycled PTZ, a dropped TCP
   connection). The composite keeps flowing on that camera's last keyframe, a
   warning is logged, and the session continues. Pinned in Task 3
   (`test_hold_repeats_last_idr_when_packets_stop`).
2. **A league feed goes live while sessions run on several sheets.** Every
   session ends `league` at the next poll, and the next heartbeat tells each
   worker to stop. Pinned in Task 8 (`test_league_live_ends_every_session`).
3. **A forged `X-Forwarded-For` naming the club IP from outside.** It is
   refused, because `client_ip` takes only our proxies' hops. Pinned in Task 7
   (`test_forged_forwarded_for_is_not_the_club`).
4. **The worker restarts mid-session.** The requeued job keeps the earlier
   throws and their clips, and new throws continue the same clock. Pinned in
   Task 9 (`test_resumed_watch_keeps_earlier_throws`).
5. **A PTZ camera re-aimed since the saved calibration.** The Start check
   fails, the watch recalibrates from the session's own frames, and the new
   calibration is saved. Pinned in Task 6
   (`test_saved_calibration_that_disagrees_falls_back_and_is_replaced`).

---

### Task 1: Feasibility on the Ryzen (no product code)

This settles four facts the later tasks depend on. Work in
`/data/wdd/scratch/ndi/` with the scratch tools. Write the results to
`docs/superpowers/plans/2026-10-24-practice-direct-feeds.results.md`.

**Files:**
- Create: `docs/superpowers/plans/2026-10-24-practice-direct-feeds.results.md`

- [ ] **Step 1: Packets without libndi's decode.** Extend scratch `avshim.c` so
  that `NDI_TAP_DECODE=0` makes `avcodec_send_packet` return 0 without
  forwarding, and `avcodec_receive_frame` return `AVERROR(EAGAIN)` (−11) for the
  H.264 context. Run `grab.py`'s receive loop for 30 s on 1A-POV. Record:
  - packets per second reaching the shim;
  - CPU of the process, with decode and without.

  Pass: ≥ 59 packets/s without decode. If it fails, Task 2 sets
  `NDI_TAP_DECODE=1` by default and the budget in Task 10 counts ~4 cores for
  capture.
- [ ] **Step 2: Bridge networking.** Repeat Step 1 in a container on
  `deploy_default`, the workers' bridge network, without `--network host`. Pass
  means the finder sees all 20 sources and packets flow. If it fails, Task 10
  puts the workers on `network_mode: host`.
- [ ] **Step 3: NVENC in a worker container.** Run
  `ffmpeg -f lavfi -i testsrc2=s=1920x1080:r=60 -t 5 -c:v h264_nvenc -b:v 20M -f null -`
  in a container started like `deploy-worker-1` (GPU 0). Pass means it encodes.
  If it fails, the cause is the driver capability. Retry with
  `NVIDIA_DRIVER_CAPABILITIES=compute,utility,video`, and record which setting
  Task 10 must add.
- [ ] **Step 4: Receiver headroom (spec test 5).** On an idle camera of each kind
  (1A-HOUSE, 1A-POV, 2A-PTZ), hold 1, then 2, then 3 cyndilib receivers at once
  for 30 s each. Record the frames per second each receiver got. Pass: 3
  receivers each at ≥ 55 fps. **If any camera can't serve 2 at full rate, stop
  and report to the user before Task 2.**
- [ ] **Step 5: Commit the results file.**

```bash
git add docs/superpowers/plans/2026-10-24-practice-direct-feeds.results.md
git commit -m "practice cameras: feasibility results (decode, network, NVENC, headroom)"
```

### Task 2: The packet tap, built into the worker image

**Files:**
- Create: `src/curling_score/cameras/__init__.py`
- Create: `src/curling_score/cameras/ndi_tap.c`
- Create: `src/curling_score/cameras/tap.py`
- Modify: `Dockerfile.worker`: add a builder stage and `pip install "cyndilib==0.1.3"`
- Test: `tests/test_cameras_tap.py`

**Interfaces:**
- Produces:
  - `/opt/ndi/libavcodec-ndi.so.61` in the image.
  - `tap.TAP_DIR = Path("/opt/ndi")`.
  - `tap.tap_env(fd: int, *, decode: bool) -> dict[str, str]`: the extra
    environment for a capture process. Sets `LD_LIBRARY_PATH` (TAP_DIR first),
    `NDI_TAP_FD`, and `NDI_TAP_DECODE` ("0" or "1").
  - `tap.read_records(f: BinaryIO) -> Iterator[tuple[int, bytes]]`: yields
    `(flags, payload)` from the tap's framing. Each record is a 4-byte
    little-endian length, then 1 flags byte (bit 0 = keyframe), then that many
    payload bytes (Annex-B). It stops cleanly at EOF and raises `ValueError` on
    a truncated record.

- [ ] **Step 1: Write the failing tests:**
  - `test_read_records_splits_frames_and_flags`: two records, keyframe then
    not, give `[(1, b"\x00\x00\x00\x01e..."), (0, b"\x00\x00\x00\x01A...")]`.
  - `test_read_records_truncated_raises`.
  - `test_tap_env_puts_tap_dir_first_and_sets_decode`:
    `tap_env(7, decode=False)["LD_LIBRARY_PATH"].split(":")[0] == "/opt/ndi"`,
    `["NDI_TAP_FD"] == "7"` and `["NDI_TAP_DECODE"] == "0"`.
- [ ] **Step 2: Run `pytest tests/test_cameras_tap.py -q`.** Expected: FAIL
  (module missing).
- [ ] **Step 3: Implement `tap.py`.** Then write `ndi_tap.c`, starting from
  scratch `avshim.c`, with these changes:
  - include `<libavcodec/avcodec.h>` and use `AVPacket` fields, not offsets;
  - wrap `avcodec_alloc_context3` to remember the context whose codec is
    `AV_CODEC_ID_H264`, and tap only `avcodec_send_packet` calls on that
    context;
  - write records to `NDI_TAP_FD` with `write(2)` in the framing above;
  - honour `NDI_TAP_DECODE` (Task 1, Step 1);
  - forward every other symbol to the real library through `DT_NEEDED`
    `libavcodec.so.61`.
- [ ] **Step 4: Add the Dockerfile stage.** `FROM python:3.12-slim AS ndi-tap`,
  then `apt-get install gcc libavcodec-dev`, then
  `gcc -O2 -shared -fPIC -o /out/libavcodec-ndi.so.61 ndi_tap.c -lavcodec -Wl,-soname,libavcodec-ndi.so.61`.
  Copy `/out/libavcodec-ndi.so.61` into the final image at `/opt/ndi/`. Add
  `"cyndilib==0.1.3"` to the second pinned `pip install`, with a comment that
  the tap depends on that libndi.
- [ ] **Step 5: Run `pytest tests/test_cameras_tap.py -q`.** Expected: PASS.
- [ ] **Step 6: Build the image on the Ryzen and check the tap.** Use a clean
  worktree (memory `deploying-curling-chart`). Run
  `docker compose -f docker-compose.worker.yml build worker`, then in a
  throwaway container run `readelf -d /opt/ndi/libavcodec-ndi.so.61`. Expected:
  `NEEDED libavcodec.so.61`, `SONAME libavcodec-ndi.so.61`.
- [ ] **Step 7: Commit.**

```bash
git add src/curling_score/cameras/__init__.py src/curling_score/cameras/ndi_tap.c \
        src/curling_score/cameras/tap.py Dockerfile.worker tests/test_cameras_tap.py
git commit -m "cameras: libndi packet tap built into the worker image"
```

### Task 3: Capture: one process per camera, holding on stalls

**Files:**
- Create: `src/curling_score/cameras/inventory.py`
- Create: `src/curling_score/cameras/capture.py`
- Test: `tests/test_cameras_capture.py`

**Interfaces:**
- Consumes: `tap.tap_env` and `tap.read_records` (Task 2).
- Produces:
  - `inventory.CAMERAS: dict[str, str]`: name → IP for all 20 (spec
    "Inventory").
  - `inventory.sheet_cameras(sheet: int) -> dict[str, str]`: role → camera
    name. The roles are `"house_a"`, `"house_h"`, `"long_a"` and `"long_h"`. The
    long views are POV on sheets 1 and 5 and PTZ on 2-4.
  - `inventory.ndi_config(path: Path) -> None`: writes `ndi-config.v1.json`
    with `networks.ips` = the 20 IPs, comma-joined.
  - `capture.Holder(write: Callable[[bytes], None], *, hold_after_s=0.5, period_s=1/60, clock=time.monotonic)`
    with:
    - `.packet(flags: int, payload: bytes) -> None`: writes it, and remembers
      the last keyframe's payload;
    - `.tick() -> bool`: True if it wrote a held keyframe.
  - `python -m curling_score.cameras.capture --camera NAME --fifo PATH --first PATH`.
    This runs the cyndilib receive pump, a reader thread
    (`read_records` → `Holder.packet`) and a ticker thread (`Holder.tick`). The
    first keyframe is copied to `--first`. On SIGTERM it exits 0. If nothing has
    been received for `RECONNECT_S = 5.0`, it calls `Receiver.reconnect()` while
    the holder keeps the picture still. It sets `NDI_CONFIG_DIR` before
    importing cyndilib.

- [ ] **Step 1: Write the failing tests:**
  - `test_sheet_cameras_long_views_by_sheet`: `sheet_cameras(1)["long_a"] == "1A-POV"`,
    `sheet_cameras(3)["long_h"] == "3H-PTZ"`, and
    `sheet_cameras(5)["house_h"] == "5H-HOUSE"`.
  - `test_ndi_config_lists_all_twenty_ips`.
  - `test_holder_passes_packets_through`.
  - `test_hold_repeats_last_idr_when_packets_stop`. The fake clock advances
    0.6 s after a keyframe and a P-frame. Then `tick()` writes the keyframe's
    payload, and again every `period_s`.
  - `test_hold_writes_nothing_before_first_keyframe`.
  - `test_hold_stops_once_packets_resume`.
- [ ] **Step 2: Run `pytest tests/test_cameras_capture.py -q`.** Expected: FAIL.
- [ ] **Step 3: Implement `inventory.py` and `capture.py`.** In `capture.py`,
  the module-level `main()` is the only part that touches cyndilib, and it is
  not unit-tested.
- [ ] **Step 4: Run `pytest tests/test_cameras_capture.py -q`.** Expected: PASS.
- [ ] **Step 5: Check one real camera on the box.** In the new image (and on
  `network_mode` per Task 1), run capture for 1A-POV into a FIFO with
  `cat fifo > x.h264` for 10 s. Then
  `ffprobe -f h264 x.h264 -show_entries stream=width,height`. Expected:
  `1920`/`1080`, and about 600 frames from
  `-count_frames -show_entries stream=nb_read_frames`.
- [ ] **Step 6: Commit.**

```bash
git add src/curling_score/cameras/inventory.py src/curling_score/cameras/capture.py \
        tests/test_cameras_capture.py
git commit -m "cameras: per-camera capture through the tap, holding the last keyframe on a stall"
```

### Task 4: Per-sheet layout, fitted to YouTube's picture

**Files:**
- Create: `src/curling_score/cameras/layout.py`
- Create: `src/curling_score/cameras/layouts/sheet{1..5}.json` (Step 6)
- Create: `scripts/cameras/fit_layout.py`
- Test: `tests/test_cameras_layout.py`

**Interfaces:**
- Consumes: `inventory.sheet_cameras` (Task 3), and
  `geometry.layout.detect_panels` for the YouTube panels.
- Produces:
  - `layout.Placement(role: str, crop: tuple[int,int,int,int], rotate: int, out: tuple[int,int,int,int])`,
    where `rotate` is 0, 90 (clockwise) or −90.
  - `layout.SheetLayout(sheet: int, placements: tuple[Placement, ...], background: str)`,
    where `background` is the bar grey as `"0xRRGGBB"`.
  - `layout.load(sheet: int) -> SheetLayout`, read from `layouts/sheet{N}.json`.
  - `layout.save(sheet_layout, path)`.
  - `layout.filtergraph(sheet_layout, *, inputs: dict[str, int], t0: float) -> str`.
    Each input is
    `[i:v]setpts=PTS-{t0}/TB,crop=…,transpose=…,scale=w:h[role]`. The base is
    `color=c={background}:s=1920x1080:r=60`, and the four roles are overlaid at
    their `out` positions with `eof_action=repeat:repeatlast=1`. The output label
    is `[out]`.
  - `fit_layout.fit(composite: np.ndarray, raw: dict[str, np.ndarray], sheet: int) -> SheetLayout`.
    It uses ORB features and `cv2.estimateAffinePartial2D` with RANSAC between
    each YouTube region and its camera. It snaps rotation to {0, ±90}, and raises
    if the residual angle exceeds 2° or the scale differs between x and y.

- [ ] **Step 1: Write the failing tests:**
  - `test_filtergraph_sheet_fixture`: a hand-built `SheetLayout` gives an exact
    expected string. Write that string into the test from the format above.
  - `test_layout_json_roundtrip`.
  - `test_placements_tile_inside_frame`: every `out` lies inside 1920x1080, and
    no two `out` rectangles overlap.
  - `test_fit_recovers_synthetic_layout`: build a synthetic composite from
    random-textured "camera" images by a known crop, rotate and scale. `fit`
    returns crops within 2 px and the same rotations.
- [ ] **Step 2: Run `pytest tests/test_cameras_layout.py -q`.** Expected: FAIL.
- [ ] **Step 3: Implement `layout.py` and `fit_layout.py`.**
- [ ] **Step 4: Run `pytest tests/test_cameras_layout.py -q`.** Expected: PASS.
- [ ] **Step 5: Collect reference frames per sheet.**
  - **YouTube side:** a this-season (2026-27) YouTube frame of each sheet from a
    cached league recording on the Ryzen (`/data/wdd/curling-cache`; nothing is
    downloaded). Sheets 2-4 were re-aimed for 2026-27.
  - **Camera side:** one frame from each of the sheet's four cameras with the
    sheet empty. This is a maintenance read.
  - **Bar colour:** read it from the YouTube frame's bars.
- [ ] **Step 6: Fit all five sheets** with `scripts/cameras/fit_layout.py --sheet N …`.
  Write `layouts/sheet{N}.json`, and save a side-by-side check image per sheet
  under `out/cameras/layout-check/` (gitignored).
- [ ] **Step 7: Commit.**

```bash
git add src/curling_score/cameras/layout.py src/curling_score/cameras/layouts \
        scripts/cameras/fit_layout.py tests/test_cameras_layout.py
git commit -m "cameras: per-sheet composite layouts fitted to YouTube's picture"
```

### Task 5: `CameraRecorder`: the composite as a growing recording

**Files:**
- Create: `src/curling_score/cameras/recorder.py`
- Test: `tests/test_cameras_recorder.py`

**Interfaces:**
- Consumes:
  - `inventory.sheet_cameras`, `layout.load` and `layout.filtergraph`
    (Tasks 3-4);
  - `live.recorder.progress_time` and `live.recorder._Part` (reused for the
    composer's progress);
  - `live.session.LiveError`.
- Produces:
  `CameraRecorder(sheet: int, directory: Path, *, popen=subprocess.Popen, clock=time.monotonic, wall=time.time, first_timeout_s=15.0, probe=…)`.
  It has:
  - the attributes `path`, `lookback_got_s = 0.0` and `wall_started`;
  - the methods `start()`, `check()`, `stop()`, `ended()`, `whole()` (always
    False) and `head_s()`.

  Behaviour:
  - `start()` makes the four FIFOs `cam-<role>.h264` and spawns the four
    `capture` processes (Task 3).
  - It waits up to `first_timeout_s` for every `first-<role>.h264`. `probe` runs
    `ffprobe` on each, in a subprocess, never PyAV on this thread.
  - It raises `LiveError("camera <name> sent no H.264 1920x1080 within 15 s")`
    unless all four are 1920x1080 H.264.
  - Only then does it spawn the composer:
    `ffmpeg -copyts` with each input as
    `-thread_queue_size 1024 -use_wallclock_as_timestamps 1 -f h264 -i <fifo>`,
    `-filter_complex <filtergraph(t0=wall_started)> -map [out]`,
    `-c:v h264_nvenc -b:v 20M -maxrate 25M -g 60 -bf 0 -f mpegts rec.0.ts`.
  - The composer's stderr is merged into stdout and read by a `_Part`, as the
    YouTube recorder does, so `head_s()` is its `time=`. That is seconds since
    `wall_started`, because of the `setpts` shift.
  - `check()` sets `ended()` and raises `LiveError` if the composer exits or its
    head stalls for `STALL_S = 30.0`.
  - It caps at `MAX_RECORD_S` like the YouTube recorder.

- [ ] **Step 1: Write the failing tests, with fake popen and probe:**
  - `test_start_spawns_four_captures_then_composer`;
  - `test_start_refuses_a_camera_that_is_not_1080p_h264`, which asserts that
    the `LiveError` message names the camera;
  - `test_composer_args_carry_filtergraph_and_nvenc_at_20M`;
  - `test_lookback_is_zero_and_wall_started_set`;
  - `test_composer_exit_ends_recording_and_raises`;
  - `test_stop_terminates_all_five_processes`.
- [ ] **Step 2: Run `pytest tests/test_cameras_recorder.py -q`.** Expected: FAIL.
- [ ] **Step 3: Implement `recorder.py`.**
- [ ] **Step 4: Run `pytest tests/test_cameras_recorder.py -q`.** Expected: PASS.
- [ ] **Step 5: Run one sheet on the box.** Run a `CameraRecorder(1, …)` for
  120 s in the new image. Check:
  - `ffprobe rec.0.ts` reports 1920x1080 at 60 fps;
  - `analyze.calibrate_from` on 24 keyframes finds both panels and both side
    views;
  - a frame matches the YouTube frame's layout by eye.
- [ ] **Step 6: Commit.**

```bash
git add src/curling_score/cameras/recorder.py tests/test_cameras_recorder.py
git commit -m "cameras: CameraRecorder composes a sheet's four cameras into a growing TS"
```

### Task 6: Saved calibration and the check at Start

**Files:**
- Create: `src/curling_score/cameras/calstore.py`
- Modify: `src/curling_score/practice/watch.py` (`PracticeWatch.__init__`, `_calibrate`)
- Modify: `src/curling_score/practice/pipeline.py`: add `check_saved`
- Modify: `src/curling_score/cli.py`: add `curling-score cameras-calibrate`
- Test: `tests/test_cameras_calstore.py`, `tests/test_practice_watch.py`

**Interfaces:**
- Produces:
  - `calstore.CalStore(root: Path)` with
    `.load(sheet: int) -> live.session.Calibration | None` and
    `.save(sheet: int, cal: Calibration) -> None`. It pickles to
    `root/cameras/cal/sheet{N}.pkl` (PanelSetups are already pickled
    elsewhere). Unreadable files are treated as None, with a warning.
  - `calstore.house_agrees(cal, frames: list[np.ndarray]) -> bool`. On the
    median of `frames`, cropped to each `cal.setups[name].rect`, run
    `calibrate.find_rings`. Both panels must agree:
    - the 12-ft centre within `RING_AGREE_PX = 4.0` of `calib.center_px`;
    - the radius within 3% of `calib.px_per_m * C.R_12FT_M - calib.edge_erosion_px`.

    A `CalibrationError` means False.
  - `PracticePipeline.check_saved(path, until_s: float, cal) -> bool`: reads
    `F.spread_keyframes(path, until_s, count=8)`, then `house_agrees`.
  - `PracticeWatch(..., saved_calibration=None, save_calibration: Callable[[Calibration], None] | None = None)`.
    The first calibration step, once `head ≥ CHECK_MIN_S = 4.0`, calls
    `pipeline.check_saved`:
    - if True, it adopts the saved calibration and goes straight to
      `"watching"`;
    - if False, it runs today's retry loop, and calls `save_calibration` with
      the first **complete** calibration that loop produces.

    Without a saved calibration, the watch behaves exactly as today.
  - CLI `curling-score cameras-calibrate --sheet N --seconds 60 --root DIR`:
    records with `CameraRecorder`, calibrates with
    `VideoPipeline.calibrate(path, head)`, and saves only if complete. This is
    a maintenance read: the recording is deleted after.

- [ ] **Step 1: Write the failing tests:**
  - `test_calstore_roundtrip` and `test_calstore_missing_is_none`;
  - `test_house_agrees_true_on_the_panels_it_was_made_from`, using the synth
    helpers in `tests/synth.py`;
  - `test_house_agrees_false_when_rings_moved_8px`;
  - in `test_practice_watch.py`:
    - `test_saved_calibration_that_agrees_skips_calibrating`: the fake pipeline's
      `calibrate` is never called, and status is `"watching"`;
    - `test_saved_calibration_that_disagrees_falls_back_and_is_replaced`:
      `save_calibration` is called once, with the complete one.
- [ ] **Step 2: Run** `pytest tests/test_cameras_calstore.py tests/test_practice_watch.py -q`.
  Expected: the new tests FAIL.
- [ ] **Step 3: Implement it.**
- [ ] **Step 4: Run the same command.** Expected: PASS, including every existing
  watch test.
- [ ] **Step 5: Commit.**

```bash
git add src/curling_score/cameras/calstore.py src/curling_score/practice/watch.py \
        src/curling_score/practice/pipeline.py src/curling_score/cli.py \
        tests/test_cameras_calstore.py tests/test_practice_watch.py
git commit -m "practice: saved per-sheet calibration, checked at Start, replaced on mismatch"
```

### Task 7: API: camera watches, who may start, claiming

**Files:**
- Modify: `src/curling_score/service/records.py`: add `Run.source: str | None = None`
- Modify: `src/curling_score/service/practice.py`: `start`, `league_on`, `camera_video_id`
- Modify: `src/curling_score/service/api.py`: `Settings`, `practice_start`, `claim`
- Modify: `src/curling_score/service/worker.py`: `ApiClient.claim(..., cameras=False)`
- Modify: `src/curling_score/live/manager.py`: pass `cameras` through
- Test: `tests/test_practice_api.py`, `tests/test_practice.py`

**Interfaces:**
- Produces:
  - `Settings.practice_source: str = "youtube"` (env `PRACTICE_SOURCE`).
  - `Settings.club_ips: frozenset[str] = frozenset()` (env `PRACTICE_CLUB_IPS`,
    comma-separated).
  - `practice.camera_video_id(sheet: int, now: datetime) -> str`, giving
    `f"cams{sheet}-{now:%Y%m%d-%H%M%S}"`.
  - `practice.league_on(repo, now) -> bool`: any run with `kind == "live"` and
    `status` in ("queued", "processing", "live"), created within the last
    `LEAGUE_FRESH_S = 6 * 3600`.
  - `practice.start(repo, youtube, user, sheet, hand, now, processing_version, *, source="youtube")`.
    With `source="cameras"`, a new watch's Run has
    `video_id=camera_video_id(...)`, `source="cameras"` and `kind="practice"`.
    No YouTube call is made.
  - `class practice.LeagueOn(Exception)`.
  - Claim body `"cameras": true`. With `practice_source == "cameras"`,
    `"practice"` is dropped from `kinds` unless the body says `cameras`.
  - The claim response for a camera job adds `"source": "cameras"` and
    `"lookback_s": 0`.
  - The worker sends `cameras=True` when `WORKER_CAMERAS=1`.

- [ ] **Step 1: Write the failing tests** in `test_practice_api.py`, with
  `practice_source="cameras"` and `club_ips={"203.0.113.9"}` in `_world`. A
  validated user is set up with `repo.update_user(..., role="validated")`.
  - `test_camera_start_needs_validated_or_club_ip`: unvalidated Sarah from
    elsewhere gets 403 with detail `"Practice on the club's cameras needs a validated account, or the club's Wi-Fi."`.
    Validated, she gets 201. Unvalidated with
    `X-Forwarded-For: 203.0.113.9`, she gets 201.
  - `test_forged_forwarded_for_is_not_the_club`:
    `X-Forwarded-For: 203.0.113.9, 198.51.100.7` gives 403 with
    `proxy_hops=1`.
  - `test_camera_start_needs_no_youtube_stream`: a sheet with no practice stream
    still gets 201, and its run has `source == "cameras"` and
    `video_id.startswith("cams")`.
  - `test_start_refused_while_a_league_is_live`: a `kind="live"`,
    `status="live"` run exists on sheet 2, and Start on sheet 4 gives 409 with
    detail `"A league game is on; practice opens when it ends."`.
  - `test_second_player_joins_same_camera_watch_under_same_rule`.
  - `test_camera_practice_job_only_to_a_worker_with_cameras`: a claim with
    kinds `["live", "practice"]` and no `cameras` gets 204. With
    `"cameras": true` it gets the job, with `source == "cameras"` and
    `lookback_s == 0`.
- [ ] **Step 2: Run `pytest tests/test_practice_api.py -q`.** Expected: the new
  tests FAIL.
- [ ] **Step 3: Implement it.** The order in `practice_start` is: practice on,
  user, permission (camera mode only), `league_on`, rate limit, start.
- [ ] **Step 4: Run** `pytest tests/test_practice_api.py tests/test_practice.py tests/test_worker.py -q`.
  Expected: PASS.
- [ ] **Step 5: Commit.**

```bash
git add src/curling_score/service/records.py src/curling_score/service/practice.py \
        src/curling_score/service/api.py src/curling_score/service/worker.py \
        src/curling_score/live/manager.py tests/test_practice_api.py tests/test_practice.py
git commit -m "practice: camera watches; validated or club IP to start; camera jobs only to camera workers"
```

### Task 8: Ending: 15 minutes idle, any league ends all, privacy page

**Files:**
- Modify: `src/curling_score/service/practice.py`: `IDLE_S = 900.0`, `end_for_league`
- Modify: `src/curling_score/service/api.py`: `admin_poll` calls `end_for_league` when `result["live"]` is non-empty
- Modify: `src/curling_score/service/static/privacy.html`: the "Game video" section
- Test: `tests/test_practice.py`, `tests/test_practice_api.py`

**Interfaces:**
- Produces: `practice.end_for_league(repo, now) -> int`. It ends every active
  session on every live watch with reason `"league"`, and returns how many were
  ended. The worker's next heartbeat then gets `stop: true` through the existing
  `practice_should_stop`.

- [ ] **Step 1: Write the failing tests:**
  - `test_idle_after_fifteen_minutes`: a throw at T, then `sweep` at T+899 s
    leaves the session active, and at T+900 s ends it `"idle"`.
  - `test_idle_counts_from_start_when_nothing_thrown`.
  - `test_league_live_ends_every_session`: sessions on sheets 1, 3 and 5, then
    `admin_poll` with a league stream going live on sheet 2. Every session ends
    `"league"`, and the next `/progress` for each practice job returns
    `"stop": true`.
  - `test_privacy_page_names_the_cameras`: `/privacy` contains
    `"reads the club's cameras directly"` and
    `"only from when a player starts a session"`.
- [ ] **Step 2: Run `pytest tests/test_practice.py tests/test_practice_api.py -q`.**
  Expected: FAIL.
- [ ] **Step 3: Implement it.** Add one paragraph to privacy.html after the
  "Game video" paragraph. It must say:
  - practice reads the club's cameras directly, with the club's agreement,
    including when nothing is streamed;
  - only from when a player starts a session until it ends;
  - the recording is deleted afterwards, and clips are kept as for games;
  - outside sessions the cameras are read briefly to keep calibration current,
    and only measurements are kept.

  Keep the page's plain style.
- [ ] **Step 4: Run the same tests.** Expected: PASS.
- [ ] **Step 5: Commit.**

```bash
git add src/curling_score/service/practice.py src/curling_score/service/api.py \
        src/curling_score/service/static/privacy.html tests/test_practice.py tests/test_practice_api.py
git commit -m "practice: 15-minute idle, a league feed ends every session, privacy page on the cameras"
```

### Task 9: The worker: camera recordings, Start at zero, resume after restart

**Files:**
- Modify: `src/curling_score/live/manager.py`: `_recorder` returns a `CameraRecorder` for `job["source"] == "cameras"`
- Modify: `src/curling_score/live/lane.py`: `video_sessions(..., cal_root: Path | None = None)` and the practice branch of `make`
- Modify: `src/curling_score/service/worker.py`: pass `cal_root=root` to `lane.video_sessions`
- Modify: `src/curling_score/service/api.py`: the claim adds `resume` for a camera job with an earlier practice.json
- Modify: `src/curling_score/practice/watch.py`: `PracticeWatch(..., prior_throws=())`
- Test: `tests/test_live_lane.py`, `tests/test_practice_watch.py`, `tests/test_practice_api.py`

**Interfaces:**
- Consumes:
  - `CameraRecorder` (Task 5);
  - `CalStore` and `PracticeWatch(saved_calibration=, save_calibration=)`
    (Task 6);
  - the claim `source` and `lookback_s` fields (Task 7).
- Produces:
  - A claim `resume` field. When `attempts > 1` and the run's practice.json
    exists, the job dict gets
    `"resume": {"wall_t0": float, "throws": list[dict]}` from that document.
  - In `lane.make` for a camera job:
    - `since_s` is `recorder.lookback_got_s` whenever it is not None, so 0.0 is
      honoured. This fixes `got or …`.
    - Without `resume`: `wall_t0 = recorder.wall_started`.
    - With `resume`: `wall_t0 = resume["wall_t0"]`,
      `t0_s = recorder.wall_started - resume["wall_t0"]` and
      `prior_throws = resume["throws"]`.
    - `saved_calibration=CalStore(cal_root).load(sheet)` and
      `save_calibration=lambda c: CalStore(cal_root).save(sheet, c)`. Without a
      `cal_root`, both are None.
  - `PracticeWatch(prior_throws=…)` starts `self.throws` with them. Their clips
    are left as they are.

- [ ] **Step 1: Write the failing tests:**
  - `test_camera_job_gets_camera_recorder`, which builds the manager with a fake
    `make_recorder` spy;
  - `test_camera_watch_since_zero` (lane);
  - `test_resumed_watch_keeps_earlier_throws`: prior throw `t_30.0`, then
    `t0_s=600`. The first publish holds `t_30.0`, and a new throw at recording
    t=12 has id `t_612.0`.
  - `test_claim_resume_after_requeue` (API): claim, publish a doc with one
    throw, let the lease expire, requeue and claim again. The job carries
    `resume.throws` of length 1.
- [ ] **Step 2: Run**
  `pytest tests/test_live_lane.py tests/test_practice_watch.py tests/test_practice_api.py -q`.
  Expected: FAIL.
- [ ] **Step 3: Implement it.**
- [ ] **Step 4: Run the same command.** Expected: PASS.
- [ ] **Step 5: Commit.**

```bash
git add src/curling_score/live/manager.py src/curling_score/live/lane.py \
        src/curling_score/service/api.py src/curling_score/practice/watch.py \
        tests/test_live_lane.py tests/test_practice_watch.py tests/test_practice_api.py
git commit -m "practice: worker records camera watches, starts at zero, resumes after a restart"
```

### Task 10: Deploy configuration for the Ryzen

**Files:**
- Modify: `deploy/docker-compose.worker.yml`
- Modify: `deploy/README.md`: practice cameras section
- Test: none; this is config only. Step 3 validates the file.

- [ ] **Step 1: Make the compose changes.**
  - On both Ryzen GPU workers: `WORKER_CAMERAS: "1"` and `WORKER_PRACTICE: "1"`,
    and `LIVE_MAX_STREAMS` such that the two together hold ≥ 5 practice
    streams. Practice never overlaps leagues.
  - Add the NVENC capability fix and the network mode that Task 1 found
    necessary.
  - The laptop stand-in's settings, if it uses this file, keep
    `WORKER_CAMERAS` unset.
- [ ] **Step 2: Write the README section.** Cover:
  - the env settings: API `PRACTICE_SOURCE` and `PRACTICE_CLUB_IPS`; worker
    `WORKER_CAMERAS`;
  - `cameras-calibrate`;
  - re-fitting a sheet's layout;
  - rollback: `PRACTICE_SOURCE=youtube`.
- [ ] **Step 3: Validate the compose file.**
  `docker compose -f deploy/docker-compose.worker.yml config -q`. Expected: no
  output, exit 0.
- [ ] **Step 4: Commit.**

```bash
git add deploy/docker-compose.worker.yml deploy/README.md
git commit -m "deploy: camera practice on the Ryzen workers"
```

### Task 11: Validation on the box, then deploy (user-gated)

Results are appended to the Task 1 results file. Deploying needs the user's
go-ahead. Hand over `deploy-api.sh` if auto mode blocks it (memory
`deploying-curling-chart`).

- [ ] **Step 1: Calibrate each sheet when it is empty** with
  `curling-score cameras-calibrate --sheet N`. Record px/m, the residuals, and
  the side views' tee and hog rows.
- [ ] **Step 2: Picture fidelity (spec test 2).** Compare each sheet's numbers
  against `calibrate_from` on that sheet's this-season YouTube frames. Pass:
  - panel rectangles within 4 px;
  - px/m within 3%;
  - tee and hog rows within `SIDE_AGREE_PX`.
- [ ] **Step 3: Inter-camera lag (spec test 1).** On a real practice session's
  recording, for ≥ 5 throws, measure the hog-line crossing time in the house
  panel against the long view. Report the median and spread.
- [ ] **Step 4: Accuracy (spec test 3).** If a YouTube practice stream is live
  at the same time, run both ways and compare throws found, false throws, rest
  positions, lines and clips. Otherwise, check one real camera session by hand
  against the long camera (`validating-release-changes`). The YouTube path is
  not removed until a both-ways comparison has passed.
- [ ] **Step 5: Load (spec test 4).** Run five sessions at once; empty sheets
  are fine for capture and compose. Record:
  - CPU, the GPUs and their NVENC sessions;
  - each camera's packets/s;
  - throw latency p90 on any sheet with real throws.

  Apply the gate in Global Constraints.
- [ ] **Step 6: Failures (spec test 6).**
  - Kill one capture process mid-session: the picture holds, it is logged, and
    the session continues.
  - Start without the tap: start fails with the camera-named `LiveError`.
  - Fake a league run: sessions end `league`.
- [ ] **Step 7: Deploy, with the user's go-ahead.**
  - Rebuild the workers from a clean worktree.
  - Deploy the API with `PRACTICE_SOURCE=cameras` and
    `PRACTICE_CLUB_IPS=71.212.197.184`.
  - Check `/api/features` and a live Start from a validated account.
- [ ] **Step 8: Commit the results.**

```bash
git add docs/superpowers/plans/2026-10-24-practice-direct-feeds.results.md
git commit -m "practice cameras: validation results"
```
