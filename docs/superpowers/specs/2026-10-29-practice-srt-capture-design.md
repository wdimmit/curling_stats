# Practice capture over SRT (design)

Date: 2026-10-29, amended 2026-11-03 (camera sync, the 20-camera hour, the second caller). Status: design,
agreed with the user in session. Built on **main**, ahead of the `camera-direct` branch.

## Why

Practice reads the club's 20 cameras over NDI (`cameras/capture.py`, through the libndi packet tap). On NDI the
house cameras drop out for 16-32 s at fixed points of their own clock, about twice an hour. The club's OBS feed
does not show these dropouts. All 20 cameras also serve SRT, and over SRT the dropout does not happen.

The user decided (2026-10-29):
- SRT replaces NDI everywhere NDI was planned, with **no NDI fallback**.
- **Practice moves to SRT first, on main, deployed to production**, so SRT is working before the rest of
  `camera-direct` builds on it. The rest of that project (composer removal, league games on cameras, retention,
  clips) still ships as one, from the branch, which takes this change when it next merges main
  (`docs/superpowers/specs/2026-10-25-pluggable-input-design.md` on `camera-direct`, amended 2026-10-29).

## What was measured

Scripts and results are in `/data/wdd/scratch/camera-direct/` on the Ryzen.
- **Listener:** every camera (`inventory.CAMERAS`, 192.168.0.231-.250) runs an SRT listener on **UDP 1600**.
  A plain caller connects, with no stream id and no passphrase.
- **What it sends:** each camera sends MPEG-TS holding **H.264 Main, 1920x1080, 60 fps, ~9.5 Mbit/s**.
  - There are no B-frames: pts = dts on 2,400 packets checked.
  - A keyframe comes about every 2.2 s.
  - Sheet 4 sends H.264 over SRT too, although it sends HEVC over NDI.
- **3H-HOUSE, 90 min at night (`srt-ab/`):** one SRT connection; the longest gap between frames was 0.25 s.
  An NDI receiver on the same camera at the same time dropped for 32, 16, 16 and 16 s.
- **Sheet 4 during a league broadcast (`srt-s4-1029/`, latency 500 ms):** all four cameras for 2 h.
  - Each held one connection at 60.000 fps by pts (an earlier 60.04 was ffmpeg's start-up burst).
  - The longest gap between frames was 0.13 s, with no SRT packet loss.
  - The broadcast and our live YouTube games were unaffected.
  - At 200 ms latency, 4H-PTZ once lost 8 packets.
- **All 20 cameras at once, 1 h (`srt-all-1101/`, 2026-11-01, gigabit link):**
  - Every camera held one connection at 60.000 fps; the longest gap between packets was 0.174 s.
  - The only losses were six single 2-3 frame skips on house cameras, which the cameras dropped themselves.
  - The link carried ~170-180 Mbit/s. SRT recovered the few network drops.
  - Capture took ~0.8 cores for all 20.
  - The club's broadcast restarted during our streaming without trouble (user, 2026-11-03).
- **A second caller (`caller2-1103/`, 2026-11-03):** on 2A-PTZ and 2A-HOUSE, a second caller joined for 40 s
  and got its own full stream. The first caller was unaffected (60.000 fps, no gaps, no skips).
- **Open:** during the sheet 4 run the workers logged 18 one-frame decode errors in the YouTube recordings of
  sheets 1 and 3. They logged none at the previous league. Whether our traffic contributed is unknown; a no-SRT
  baseline is pending. Practice runs only when no league is on, so this does not gate practice.

## Goals

- Practice sessions get their cameras over SRT, with the same pictures, timing and behaviour as today minus the
  house-camera dropouts.
- NDI leaves the worker: `cyndilib`, the packet tap, its build stage and the NDI config.

**Non-goals:**
- No change to the composer, the composite, the tracker, calibrations, clips or the practice UI.
- No league capture.
- No `feeds` work.

**Anti-goals:**
- Practice must not get less reliable than it is on NDI. A capture that hammers a camera with reconnects is a
  failure even if it records.
- No change to what the club's production sees.

## Design

### `cameras/capture.py`: SRT in, the same FIFO out

The capture keeps its contract with `CameraRecorder`:
- the same command line, except `--camera` still names the camera and its address now comes from
  `inventory.CAMERAS`;
- the same NUT FIFO, with each packet's time;
- the same `--first` keyframe file;
- the same log lines and the same exit codes.

The recorder and composer do not change, except where noted below.

**What changes:**
- **Input.** It connects as an SRT caller to `srt://<ip>:1600` with `latency` 500 ms, demuxes the MPEG-TS, and
  takes the video stream's packets. The packets are Annex-B H.264, as the tap gave, so `_Nut`, `Holder` and
  `--first` take them unchanged.
- **Time.** `CameraClock` is unchanged. Its input is each packet's pts in seconds, with the 90 kHz MPEG-TS pts
  **unwrapped at 2^33** (~26.5 h), so a wrap does not read as a clock step. See "Camera sync" below for why the
  cameras' own wall time is not used.
- **Liveness and reconnects.**
  - Liveness is judged from packets, as today.
  - After `RECONNECT_S` without one, the capture closes the SRT connection and opens a new one, at the same
    doubling gaps up to `RECONNECT_MAX_S`.
  - `Holder` keeps the picture still meanwhile.
  - FFmpeg's SRT `timeout` makes a dead connection end rather than hang.
- **Exits.** These are kept:
  - no keyframe within `FIRST_S` of starting;
  - the FIFO's reader is gone;
  - the parent is gone;
  - SIGTERM ends it at once.

  `FIND_S` and the finder go, since there is no discovery. A camera that does not answer is the
  `FIRST_S` exit.
- **Gone:**
  - the cyndilib receive pump and finder;
  - the tap reader;
  - `_exec_with_tap` and its re-exec;
  - `NDI_CONFIG_DIR`;
  - the GIL notes that came with libndi.
- **How the SRT stream is opened is the builder's call.** It can be opened in PyAV directly, if the image's PyAV
  FFmpeg has the `srt` protocol (check first; PyAV wheels bundle their own FFmpeg). Otherwise it can be read from an
  `ffmpeg -i srt://… -c copy -f mpegts -` subprocess, as the measurement script `srt_recv.py` did. Either way, a
  stuck read must not stop SIGTERM, the parent check or the FIFO writer.

### Elsewhere

- **`cameras/recorder.py`:** keeps its check that every first keyframe is H.264 1920x1080. Any text that says NDI
  becomes SRT.
- **`cameras/inventory.py`:** `ndi_config` goes. Names, addresses and `sheet_cameras` stay.
- **Removed:**
  - `cameras/tap.py` and `cameras/ndi_tap.c`;
  - the `ndi-tap` stage and the `/opt/ndi` copy in `Dockerfile.worker`;
  - the `cyndilib` pin;
  - `tests/test_cameras_tap.py`.
- **`deploy/docker-compose.worker.yml`:** the camera notes say SRT. The containers stay on `deploy_default`;
  SRT is outgoing UDP from there, which worked in the measurements, all of which used `--network host`. The
  builder must confirm the bridge network reaches UDP 1600 before deploying.
- **A second caller is harmless (measured 2026-11-03).** The recorder already holds one capture per camera per
  session. A second SRT caller to a camera (`cameras-calibrate`, a script) gets its own stream and leaves the first
  alone, so no lock is needed. A note in `inventory.py` and the compose file says so. Keep to two of ours per
  house camera, as before.

### Camera sync

The user (2026-11-03): the club's OBS broadcast is no more in sync than anything we do, so there is no flash test.
We do the best we can with what we have. What was measured:
- **The cameras' own clocks.**
  - A house camera's pts runs on its encoder's uptime clock.
  - A long camera's pts is NTP time, but the long cameras disagree with each other by 10-90 ms, and the gap moves
    from day to day. 3H vs 3A PTZ was +31 ms on 11/01 and +77 ms on 11/02, and drifted 13 ms over that evening.
  - So no camera's wall time is used.
- **Arrival.** Each camera's delay from pts to arrival held to 1-2 ms across six reconnects (`reconn-1103/`).
- **What `CameraClock` does with that.** Over the 20-camera hour, today's 10 s arrival anchor put every house camera
  within sd 6.5 ms, p99 20 ms, of a line fitted over the whole hour (the tie). Smoothing the anchor over 1-10 min
  gained only 1-2 ms, so `CameraClock` stays as it is.
- **Checked on play** (11/02 sheet 3 league, `sync-s3/`, split-sync mid read in both directions):
  - On the arrival clock, 3H-PTZ read 30 ms late against 3A-PTZ.
  - On the cameras' NTP clocks it would read 47 ms early.
  - Arrival is the closer of the two.
- **Each camera model's SRT delay, against NDI (measured 2026-11-03, during the build).** The same frames were
  received both ways, paired by clock value, on two cameras of each model. Past the 500 ms latency, SRT delivers
  frames this much later than NDI did:
  - house: 64-66 ms;
  - POV: 62 ms;
  - PTZ: 91 ms.

  The two cameras of each model agreed to 2 ms. The capture takes each model's own delay off
  (`capture.delivery_delay_s`), so every camera sits where NDI put it. That also moves the PTZs 27 ms earlier against
  the house cameras than SRT's arrivals alone would, which is the way the hits pointed: 7 clean hits gave house
  -0.03 ± 0.025 s against a PTZ. The league work on `camera-direct` collects more hits (its sync watchdog).

## Tests and gates

- **Unit tests:**
  - `CameraClock` across a 2^33 wrap;
  - reconnect backoff driven by a stalled SRT source;
  - the `FIRST_S` exit for a camera that never answers;
  - the FIFO carrying packets from an MPEG-TS source, using a short `.ts` cut from `srt-s4-1029/` or a local
    `ffmpeg -f mpegts srt://…?mode=listener`.

  Delete the NDI-only tests, and keep every `Holder`/`_Fifo`/`_Nut`/`CameraClock` test passing unchanged. Run the
  camera test files only (the full suite OOMs here).
- **Gate 1, at night on the Ryzen:**
  - A practice session on one sheet, recorded for an hour through the real recorder and composer from a test
    container.
  - The composite plays smoothly, there are no gaps over 1 s, and no capture reconnects.
  - CPU per capture stays near today's ~0.02 cores.
- **Gate 2, at night:** two sheets at once, the practice cap.
- **Then deploy** to both workers, with a version bump done in the morning (memory `youtube-gentle`). Watch the
  first real practice session.

## Settled after the builder check

- **How many sheets.** `PRACTICE_MAX_CAMERA_WATCHES` (2, `deploy/cloudrun.yaml`) already caps practice at two
  sheets. The link has been gigabit since 2026-10-31 and carried all 20 cameras, so it no longer sets the cap. The
  composer's CPU and NVENC sessions do, until `camera-direct` removes the composer. No new admission check, and
  the cap stays at 2 in this change.
- **Frame times and SRT latency (Claude's call).** SRT delivers each packet about `latency` (500 ms) after the
  camera sent it. Arrival-anchored times would then sit ~0.5 s later than NDI's, and sensor-timing matching
  (`wall_started + t`) and clip cuts use them. `CameraClock`'s anchor subtracts the configured latency, so times
  stay where NDI put them.
  - **Check:** for gate 1, run one NDI receiver beside the SRT capture on one camera for a few minutes, using
    `plain_recv2.py` in scratch (two of ours on that camera, within the limit). Their times for the same frames
    agree to within ~50 ms.
- **Keyframes carry SPS/PPS.** Checked: 26 of 26 keyframes on 4A-PTZ over SRT. So `--first` and `_Nut`'s
  template work from any keyframe.
- **If the bridge network cannot reach UDP 1600 (Claude's call).** Stop and bring it to the user before changing
  the network. Host networking would break the `pot` service name that yt-dlp uses.
- **One caller per camera:** settled by the 2026-11-03 measurement. A second caller gets its own stream, so a
  note is enough (see "Elsewhere").
- **CPU (Claude's call).** Up to ~0.1 core per capture passes (20 captures, ~2 cores). An ffmpeg subprocess is
  acceptable within that.
- **Deploy (Claude's call).** The work ends when gates 1-2 have passed, ready to deploy. The deploy itself waits for the user's go, and workers restart through `gated_restart.sh`. Night
  gates run only after checking that no league recording or practice session is live, as the measurement
  scripts did.
- **Code notes:**
  - `Holder`'s keyframe flag (`tap.KEYFRAME`) moves into `capture.py`.
  - The tap-reader tests are rewritten for the SRT reader, not kept unchanged.
  - The re-exec's parent argument becomes plain `getppid()`.

## Left to the builder

- PyAV or the ffmpeg subprocess.
- Exact constants beyond those named.
- How the reconnect closes and reopens.
- Test fixtures.
