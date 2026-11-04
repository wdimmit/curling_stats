# Practice capture over SRT Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Practice's per-camera capture reads each camera over SRT instead of NDI, keeps its FIFO contract with
`CameraRecorder`, and NDI leaves the worker image.

**Architecture:**
- `cameras/capture.py` keeps one process per camera.
- That process runs an `ffmpeg` SRT caller as a child, copying MPEG-TS to its stdout, and demuxes it with PyAV.
  The image's PyAV has no `srt` protocol: checked 2026-11-03, `ProtocolNotFoundError` in `curling-worker:local`.
- Each packet goes through the existing `CameraClock` → `Holder` → `_Fifo`/`_Nut` path, unchanged.
- A stall kills the child and starts a new one on the `Reconnects` schedule.
- The tap, cyndilib and the NDI config are deleted.

**Tech Stack:** Python 3.12, PyAV 18.1.0 (demux only), the image's `ffmpeg` (has `srt`), pytest.

**Spec:** `docs/superpowers/specs/2026-10-29-practice-srt-capture-design.md` (amended 2026-11-03). The spec and this
plan are uncommitted in the main checkout `/home/tcuser/src/curling_score`; read them there.

## Global Constraints

- Work in a worktree `.claude/worktrees/practice-srt` on a new branch `practice-srt`, cut from `main`. The main
  checkout is shared with other sessions: never commit there. Main takes the branch only with the user's go, before
  deploy.
- **No NDI fallback.** SRT only.
- **The SRT connection:**
  - caller URL `srt://<inventory.CAMERAS[name]>:1600?mode=caller&latency=500000&timeout=5000000`;
  - latency 500 ms;
  - the camera's address comes from `inventory.CAMERAS`.
- **Frame times are kept where NDI put them:** the capture's session clock and its `CameraClock` both subtract the
  500 ms latency.
- **Unchanged:** `CameraClock`, `Holder` (apart from where its keyframe flag lives), `_Fifo`, `_Nut` and
  `Reconnects`, and every one of their existing tests.
- **Constants:** `RECONNECT_S` = 5, `RECONNECT_MAX_S` = 60, `FIRST_S` = 15, `MAX_AGE_S` = 5. `FIND_S` and
  `FIND_POLL_S` go.
- **Exits** (all exit 1): no keyframe within `FIRST_S` of starting; the FIFO's reader is gone; the parent is gone.
  SIGTERM ends the capture at once, by the signal's default action.
- **A capture never outlives its session, and neither does its `ffmpeg`.**
- **CPU:** up to ~0.1 core per capture.
- **Tests:** run only the camera test files (`tests/test_cameras_*.py`). The full suite OOMs on this box.
- **On the club's network:**
  - at most two of ours per house camera;
  - night gates start only when no league or practice is live: no `rec.*.ts` under
    `/data/wdd/curling-cache*/live` modified in the last 2 min, and no `curling_score.cameras.capture` process
    (`pgrep -f`);
  - **times in reports are Seattle time.**
- **No deploy in this plan.** The work ends ready to deploy. The deploy waits for the user's go and goes through
  `gated_restart.sh`, with a version bump done in the morning.

## Review Focus

1. **A hard-down camera:** its `ffmpeg` exits at once, every time. Expected: new connections follow the
   `Reconnects` schedule, never a tight respawn loop. Test in Task 2.
2. **A capture killed outright** (SIGTERM, SIGKILL, or `os._exit` after a stop). Expected: its `ffmpeg` is gone
   within a second, so no SRT caller is left pulling a camera. Test in Task 2.
3. **`ffmpeg` writing a lot to stderr.** Expected: stderr is drained, so `ffmpeg` never blocks and packets keep
   coming. Test in Task 2.
4. **A reconnect while the old connection's reader is still draining.** Expected: the old reader's packets stop
   reaching the `Holder` once a new connection exists. Test in Task 2.
5. **pts across the 2^33 wrap, and the backwards pts jump after every reconnect** (ffmpeg rebases each new input
   to ~1.4 s). Expected: the wrap reads as continuous time; the reconnect re-anchors (`CameraClock`'s step). Tests
   in Task 1.

---

### Task 1: The SRT reader in `capture.py`

**Files:**
- Modify: `src/curling_score/cameras/capture.py`
- Test: `tests/test_cameras_capture.py`

**Interfaces:**
- Produces:
  - `capture.KEYFRAME: int = 0x01`. `Holder.packet` tests `flags & KEYFRAME` instead of `tap.KEYFRAME`. Every test
    using `tap.KEYFRAME` switches to `capture.KEYFRAME`.
  - `capture.SRT_PORT = 1600`, `capture.SRT_LATENCY_S = 0.5`, `capture.SRT_TIMEOUT_S = 5.0`, and
    `capture.PTS_WRAP = 1 << 33`.
  - `capture.srt_url(camera: str) -> str`: the URL in Global Constraints, with latency and timeout in µs.
  - `capture.ffmpeg_command(camera: str) -> list[str]`: `["ffmpeg", "-nostdin", "-hide_banner", "-loglevel",
    "warning", "-i", srt_url(camera), "-map", "0:v:0", "-c", "copy", "-f", "mpegts", "-"]`.
  - `class capture.PtsUnwrap` with `__call__(self, pts: int) -> int`.
    - It takes `pts % PTS_WRAP` first, because PyAV's demuxer may already have unwrapped.
    - A value more than `PTS_WRAP // 2` below the last counts as one wrap forward.
    - It returns 90 kHz ticks that never wrap.
  - `_Capture._read_stream(self, stream: BinaryIO, generation: int) -> int`.
    - It demuxes MPEG-TS from `stream`, takes the video stream's packets with a size, and returns how many it
      passed on.
    - For each packet:
      - `self.reconnects.heard()`;
      - the first keyframe goes to `_save_first`;
      - the time is `self.times.time(unwrapped_ticks / 90000, self.now())`;
      - then `self.holder.packet(KEYFRAME if key else 0, bytes(packet), at)`.
    - It stops, without passing the packet, once `generation != self.generation`.
    - It ends quietly at EOF or on `av.error.FFmpegError`, with one warning line naming the error.
  - `_Capture.now: Callable[[], float]`, default `time.monotonic`. It is the arrival clock; tests replace it.
  - `_Capture.generation: int`, starting at 0. Task 2 increments it per connection.
  - **Latency:** `_Capture.__init__` builds `session()` as `time.monotonic() - t0 - SRT_LATENCY_S` and
    `CameraClock(t0 + SRT_LATENCY_S)`.
- Leave the tap reader (`_read`) and `run()` in place for now. Task 2 replaces them.

- [ ] **Step 1: Write the failing tests** (in `tests/test_cameras_capture.py`, beside the tap-reader tests)

```python
def mpegts(packets, pts0=0):
    """The h264() packets muxed as MPEG-TS, 1/60 s apart from pts0 (90 kHz), as ffmpeg sends them."""
    # PyAV: av.open(BytesIO, "w", format="mpegts"); stream from a raw h264 template of packets[0];
    # time_base 1/90000; pts = dts = pts0 + i * 1500.

def test_srt_url_names_the_cameras_address_port_latency_and_timeout():
    assert capture.srt_url("3H-HOUSE") == ("srt://192.168.0.248:1600?mode=caller"
                                           "&latency=500000&timeout=5000000")

def test_pts_unwrap_carries_on_across_the_33_bit_wrap():
    u = capture.PtsUnwrap()
    w = capture.PTS_WRAP
    assert [u(p) for p in (w - 3000, w - 1500, 0, 1500)] == [w - 3000, w - 1500, w, w + 1500]

def test_pts_unwrap_takes_already_unwrapped_pts_as_they_are():
    u = capture.PtsUnwrap()
    w = capture.PTS_WRAP
    assert [u(p) for p in (w - 1500, w, w + 1500)] == [w - 1500, w, w + 1500]

def test_the_srt_reader_times_packets_by_the_cameras_clock_less_the_latency(tmp_path):
    """Packets 1/60 s apart by pts, arriving bunched: timed as the camera spaced them, anchored to the
    quickest arrival, 0.5 s (SRT_LATENCY_S) before it, in seconds since --t0."""
    # cap = capture_reading(tmp_path, ..., t0); cap.now replays arrivals t0 + 5.5 + i/60 + late_i
    # with late = (0, 0.013, 0.001, ...); cap.fifo.opened.set(); cap._read_stream(BytesIO(mpegts(...)), 0)
    # assert the queued times == approx([5.0 + i / 60 ...], abs=1e-6)

def test_the_srt_reader_keeps_time_across_a_pts_wrap(tmp_path):
    # mpegts(h264(12), pts0=PTS_WRAP - 6 * 1500); arrivals evenly 1/60 apart
    # assert the queued times' differences are all approx(1/60, abs=1e-6)

def test_the_srt_reader_saves_the_first_keyframe_and_holds_it_before_the_fifo_opens(tmp_path):
    # as test_the_tap_reader_gives_the_holder_packets_before_the_fifo_opens, through _read_stream:
    # (tmp_path / "first").read_bytes() == the first keyframe's bytes; nothing queued; after opened.set(),
    # holder.tick() is True and queues that keyframe

def test_an_old_connections_reader_stops_once_a_new_one_exists(tmp_path):
    # cap.generation = 1; cap._read_stream(BytesIO(mpegts(h264(12))), 0) == 0; nothing queued, no first file
```

`capture_reading` loses its `tap_fd` argument. It builds `_Capture` from the parser's arguments plus
`args.parent = os.getppid()`.

- [ ] **Step 2: Run them to see them fail**

Run: `pytest tests/test_cameras_capture.py -k "srt or pts_unwrap or old_connection" -v`
Expected: FAIL (`AttributeError: ... has no attribute 'srt_url'`, and so on).

- [ ] **Step 3: Implement the Interfaces above in `capture.py`.** The docstring's **Time** paragraph says:
  - the pts is the camera's 90 kHz MPEG-TS clock, unwrapped;
  - times sit `SRT_LATENCY_S` before arrival.

  It also cites the spec's "Camera sync" section for why no camera's wall time is used.

- [ ] **Step 4: Run every camera test**

Run: `pytest tests/test_cameras_capture.py tests/test_cameras_recorder.py tests/test_cameras_tap.py -q`
Expected: all pass. The old tap-reader tests still pass, since `_read` is still there.

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/cameras/capture.py tests/test_cameras_capture.py
git commit -m "capture: read a camera's MPEG-TS over SRT through CameraClock, Holder and the FIFO"
```

### Task 2: The capture runs an `ffmpeg` SRT caller and reconnects it

**Files:**
- Modify: `src/curling_score/cameras/capture.py` (`_Capture.run`, `main`, `_parser`, the module docstring)
- Test: `tests/test_cameras_capture.py`

**Interfaces:**
- Consumes (from Task 1): `ffmpeg_command`, `_read_stream(stream, generation)`, `generation`, `KEYFRAME`.
- Produces:
  - `_Capture.run() -> int`:
    - SIGTERM goes back to its default action.
    - It starts the FIFO writer and the ticker threads.
    - Then it loops every `PUMP_S` (0.1 s) until `exit_code` is set:
      - Connect at once on entry. Later, connect when `self.reconnects.due()`. That covers a stalled stream and an
        `ffmpeg` that has exited.
      - The `FIRST_S` exit, counted from entry.
      - The parent check.
    - Before returning, it kills the current `ffmpeg`.
  - `_Capture._connect(self) -> None`, always on the main thread:
    - It kills and reaps the previous `ffmpeg`, if any, and increments `self.generation`.
    - It starts `ffmpeg_command(self.camera)` with `stdin=DEVNULL`, `stdout=PIPE`, `stderr=PIPE`, and
      `preexec_fn=_die_with_parent`.
    - It starts two daemon threads: one runs `_read_stream(proc.stdout, generation)`; the other relays each stderr
      line as a warning, `ffmpeg: <line>`.
    - It logs `connecting to <url>` at info; after the first connection, `no packets for X s; reconnecting` at
      warning.
  - `capture._die_with_parent() -> None`: `prctl(PR_SET_PDEATHSIG, SIGKILL)` through `ctypes.CDLL(None,
    use_errno=True)`.
    - This works because `_connect` only ever runs on the main thread, which lives until `os._exit`. The
      docstring's note on `getppid()` explains why the thread matters.
  - `main(argv)`:
    - No re-exec.
    - If `shutil.which("ffmpeg")` is None, exit with the message `ffmpeg is not installed: capture runs only in the
      worker image`.
    - `args.parent = os.getppid()` at start.
    - Then `_Capture(args).run()`, ending in `os._exit` as today.
  - `_parser()` drops `--ndi-dir`, `--tap-fd` and `--parent`. `--camera`, `--fifo`, `--first` and `--t0` are
    unchanged, so `recorder._spawn_capture` does not change.
- Removed from `capture.py`:
  - `_read`, `_find`, `_check_stall`'s NDI body, `_exec_with_tap` and `_TAP_LIB`;
  - `FIND_S`, `FIND_POLL_S` and the cyndilib imports;
  - `from curling_score.cameras import tap`;
  - the GIL and libndi paragraphs of the docstring. Rewrite **Threads**, **Stalls** and **Exits** for the
    `ffmpeg` child.
- Tests: write the fake `ffmpeg` to `tmp_path` and monkeypatch `capture.ffmpeg_command` to return
  `[sys.executable, str(fake), *args]`. The fake can:
  - send an `.ts` file (`mpegts()` from Task 1);
  - send it, then sleep;
  - exit at once;
  - write a lot to stderr.

  Each fake appends `"<pid> <time.monotonic()>\n"` to a `spawns` file. Shrink `RECONNECT_S`, `RECONNECT_MAX_S` and
  `FIRST_S` with monkeypatch. Stop `run()` from a `threading.Timer` calling `cap.stop(0, "test over")`.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_camera_that_never_answers_ends_the_capture_after_first_s(...):
    # fake sleeps; FIRST_S = 0.3 -> run() == 1 within 2 s; caplog has "no keyframe within"

def test_a_stalled_stream_reconnects_at_doubling_gaps(...):
    # fake sends mpegts(h264(12)) then sleeps; RECONNECT_S = 0.2, RECONNECT_MAX_S = 0.8; stop after 3.5 s
    # spawns >= 4; gaps between spawns non-decreasing and each <= 0.8 + 0.3; every pid but the last is dead

def test_an_ffmpeg_that_exits_at_once_is_not_restarted_in_a_tight_loop(...):
    # fake exits 1 at once; RECONNECT_S = 0.2, RECONNECT_MAX_S = 0.8; stop after 2.0 s -> spawns <= 5

def test_packets_from_an_mpegts_source_reach_the_fifo_with_their_times(...):
    # fake sends mpegts(h264(12, gop=6)); a thread opens and reads the FIFO; after stop, demux_nut(read)
    # starts at a keyframe, its packets' bytes equal the source's in order, times 1/60 apart (abs 1e-6)

def test_ffmpegs_stderr_is_drained(...):
    # fake writes 1 MB to stderr, then sends the .ts -> first keyframe saved within 2 s

def test_sigterm_ends_a_capture_and_its_ffmpeg_at_once(tmp_path):
    # child: python -c script that monkeypatches ffmpeg_command to a fake writing its pid and sleeping,
    # then calls capture.main([...]); wait for the pid file; SIGTERM the child
    # child exits within 1.0 s; the fake's pid is gone within 1.0 s after (os.kill(pid, 0) raises)

def test_sigkill_takes_ffmpeg_down_too(tmp_path):
    # as above with SIGKILL

def test_without_ffmpeg_a_capture_exits_naming_it(monkeypatch, tmp_path):
    # PATH=str(tmp_path) -> main([...]) raises SystemExit whose code starts with "ffmpeg is not installed"
```

Delete these tests:
- `test_without_the_tap_a_capture_exits_naming_it`;
- `test_sigterm_ends_a_capture_stuck_in_libndi_at_once` and its `_STUCK` script;
- the `Finder` fake;
- the two `test_the_tap_reader_*` tests, which Task 1's SRT reader tests replace.

- [ ] **Step 2: Run them to see them fail**

Run: `pytest tests/test_cameras_capture.py -v`
Expected: the new tests FAIL. Every `Holder`/`_Fifo`/`_Nut`/`CameraClock`/`Reconnects` test passes.

- [ ] **Step 3: Implement the Interfaces above.**

- [ ] **Step 4: Run every camera test**

Run: `pytest tests/test_cameras_capture.py tests/test_cameras_recorder.py -q`
Expected: all pass, and `tests/test_cameras_capture.py` no longer imports `tap`.

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/cameras/capture.py tests/test_cameras_capture.py
git commit -m "capture: an ffmpeg SRT caller per camera, reconnected on the Reconnects schedule"
```

### Task 3: NDI leaves the code, the image and the notes

**Files:**
- Delete: `src/curling_score/cameras/tap.py`, `src/curling_score/cameras/ndi_tap.c`, `tests/test_cameras_tap.py`
- Modify:
  - `src/curling_score/cameras/inventory.py`: drop `ndi_config`, `CONFIG_NAME`, and the `json`/`os`/`Path`
    imports if unused.
  - `src/curling_score/cameras/__init__.py` (docstring)
  - `src/curling_score/cameras/recorder.py` (docstring and comments only)
  - `Dockerfile.worker`
  - `deploy/docker-compose.worker.yml` (comments)
  - `tests/test_cameras_capture.py`: drop `test_ndi_config_lists_all_twenty_ips`.
  - `tests/test_cameras_recorder.py`: the dying capture's message.

**Interfaces:**
- Consumes: nothing new. After this task nothing imports `tap` or `cyndilib`. `grep -rn "cyndilib\|cameras import.*tap\|ndi_config\|NDI_" src tests` prints nothing.

- [ ] **Step 1: Update the recorder test first.**
  - `test_a_capture_that_dies_at_start_gives_its_reason` uses the message `"ffmpeg is not installed: capture runs
    only in the worker image\n"`, and its match follows it.
  - In `test_a_capture_that_dies_at_start_names_its_error_not_its_chatter`, replace the "looking for … / not in
    the finder within 15 s" lines with `"connecting to srt://192.168.0.241:1600?…"` and `"stopping: no keyframe
    within 15 s"`, and update its assertion to match.

  Run: `pytest tests/test_cameras_recorder.py -q`. Expected: PASS. The recorder relays whatever text a capture
  prints, so only the expected strings change.
- [ ] **Step 2: Delete the files, and edit `inventory.py` and the capture test.**

  `inventory.py`'s docstring:
  - The cameras are reached over SRT on UDP 1600, at the addresses below.
  - A second caller gets its own stream and leaves the first alone (measured 2026-11-03).
  - Keep to two of ours per house camera.
- [ ] **Step 3: `Dockerfile.worker`.**
  - Remove the `ndi-tap` stage (lines 1-15), its `COPY --from=ndi-tap` and comment (lines 128-132), `cyndilib==0.1.3`
    and the cyndilib comment paragraph in the pip step.
  - Note beside the ffmpeg install that practice's capture needs the image's ffmpeg to have `srt`.
- [ ] **Step 4: The comment text.**
  - `deploy/docker-compose.worker.yml`'s `WORKER_CAMERAS` note: the box reads the club's 20 cameras over SRT (UDP
    1600, outgoing, from `deploy_default`).
  - `cameras/__init__.py`: practice on the club's cameras over SRT; each camera's H.264 is copied, never decoded,
    and a sheet's four are composed into one recording.
  - In `recorder.py`, "tap reader" becomes "SRT reader", and "(no tap, no such camera)" becomes "(no ffmpeg, a
    camera that never answers)". Line 74 drops `capture.FIND_S`.
- [ ] **Step 5: Run the camera tests and the grep**

  Run: `pytest tests/test_cameras_capture.py tests/test_cameras_recorder.py tests/test_cameras_layout.py tests/test_cameras_calstore.py tests/test_cli_cameras_calibrate.py -q && ! grep -rn "cyndilib\|cameras import.*tap\|ndi_config\|NDI_TAP" src tests Dockerfile.worker`
  Expected: all pass, and the grep prints nothing.
- [ ] **Step 6: Commit**

```bash
git add -A src/curling_score/cameras tests/test_cameras_capture.py tests/test_cameras_recorder.py tests/test_cameras_tap.py Dockerfile.worker deploy/docker-compose.worker.yml
git commit -m "cameras: NDI goes (tap, cyndilib, ndi_config); practice reads the cameras over SRT"
```

### Task 4: The image, the bridge network and one camera, on the Ryzen

Operational; no product code changes unless a step fails. Ryzen: `curling@100.85.240.114`. Scratch:
`/data/wdd/scratch/practice-srt/`.

- [ ] **Step 1: Build a test image from the branch.**
  - rsync the worktree, without `.git` and data, to `/data/wdd/scratch/practice-srt/src`.
  - There, run `docker build -f Dockerfile.worker -t curling-worker:srt-test .`.
  - Expected: the build succeeds, and `docker run --rm --entrypoint python curling-worker:srt-test -c "import
    cyndilib"` fails with `ModuleNotFoundError`.
- [ ] **Step 2: Check that the bridge reaches UDP 1600.**
  - Run `docker run --rm --network deploy_default --entrypoint ffmpeg curling-worker:srt-test -nostdin
    -loglevel error -t 3 -i "srt://192.168.0.232:1600?mode=caller&latency=500000&timeout=5000000" -c copy -f
    null -`.
  - Expected: exit 0.
  - **If it fails, stop and bring it to the user.** Do not change the network: host networking would break the
    `pot` service name.
- [ ] **Step 3: Run one capture for 60 s on `deploy_default`.**
  - In the test image, a script makes a FIFO, starts `python -m curling_score.cameras.capture --camera 2A-PTZ
    --fifo cam.nut --first first.h264 --t0 <time.monotonic()>`, and reads the FIFO with `ffmpeg -f nut -i cam.nut
    -c copy -t 60 out.nut`.
  - Sample the capture's CPU (with its `ffmpeg` child) from `/proc/<pid>/stat` at the start and the end.
  - Expected:
    - `first.h264` probes as H.264 1920x1080;
    - `out.nut` holds ~3,600 frames whose times are 1/60 s apart (no step > 0.1 s);
    - the log has no `reconnecting`;
    - CPU ≤ 0.1 core;
    - after `kill -TERM`, no `ffmpeg` is left (`pgrep -f srt://`).
- [ ] **Step 4: Check the latency constant against NDI, on a house camera of a sheet no session uses.**
  - For 5 min, run `srt_recv.py` (the capture's own pipeline, logging pts and arrivals) beside the old image's
    NDI receiver `plain_recv2.py` on that one camera. That is two of ours, within the limit.
  - Pair frames by NDI timecode = SRT pts (equal on house cameras, measured 10/29). If `plain_recv2.py` does not
    log the timecode, add it in scratch.
  - Expected: the median of (SRT arrival − 0.5 s) − (NDI arrival) is within ±50 ms. Report the number.
- [ ] **Step 5: Write `docs/superpowers/plans/2026-11-03-practice-srt-capture.results.md`, with the numbers from
  Steps 1-4, and commit it on the branch.**

### Task 5: Gate 1, one sheet for an hour at night

- [ ] **Step 1: The gate script `/data/wdd/scratch/practice-srt/gate.py`**, run in `curling-worker:srt-test` on
  `deploy_default`:
  - `CameraRecorder(sheet, dir).start()`, then `check()` every second for 3600 s, then `stop()`. Keep the
    recorder's log, and the composite at `recorder.path`.
  - Every 10 s it samples each capture's CPU, with its `ffmpeg` child, from `/proc`.
  - It waits for the Global Constraints' quiet check: after 23:00 PST, and gives up at 01:00 PST.
- [ ] **Step 2: Run it on sheet 2.**
- [ ] **Step 3: Check the result:**
  - `ffprobe` the composite's frame times: no gap > 1 s;
  - no `reconnecting` in any capture's log;
  - no `LiveError`;
  - median CPU per capture ≤ 0.1 core;
  - an eyeball check of three frames (start, middle, end) shows all four views live.
- [ ] **Step 4: Add the numbers to the results file, and commit.**

### Task 6: Gate 2, two sheets at once at night

- [ ] **Step 1: Run `gate.py` for sheets 3 and 4 together, for 1 h, on the same quiet rule.**
- [ ] **Step 2: Apply Task 5's checks to both.** Also, CPU for all eight captures together stays ≤ 0.8 cores.
- [ ] **Step 3: Add the numbers to the results file, and commit.** Then tell the user that the branch is ready to
  deploy and what the deploy needs:
  - main takes `practice-srt`;
  - a morning version bump;
  - both workers rebuilt through `gated_restart.sh`;
  - watching the first real practice session.
