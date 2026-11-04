# Practice capture over SRT: results

Plan: `docs/superpowers/plans/2026-11-03-practice-srt-capture.md`, on branch `practice-srt`. Times are Seattle
time (PST). Scratch on the Ryzen: `/data/wdd/scratch/practice-srt/` (gates) and `/data/wdd/scratch/camera-direct/`
(measurements).

## Task 4: the image, the bridge and one camera (2026-11-03)

- **Image.** `curling-worker:srt-test` built from the branch. `import cyndilib` fails (`ModuleNotFoundError`), and
  `/opt/ndi` is gone.
- **Bridge network.** From `deploy_default`, `ffmpeg` pulled 3 s from `srt://192.168.0.232:1600` and exited 0. So the
  workers stay off host networking.
- **One capture, 60 s (2A-PTZ), the product code:**
  - the first keyframe probes as H.264 1920x1080, and an MPEG-TS access unit delimiter in front of it is fine;
  - 3,604 frames in 60 s, the largest step 0.022 s, no reconnects;
  - 0.036 cores for the capture with its `ffmpeg`;
  - after SIGTERM no `ffmpeg` was left (2A-HOUSE, 20 s).
- **Start-up burst (found in Task 2, measured on 2A-PTZ).** By default `ffmpeg` and the demuxer each probed a new
  connection for seconds and then sent its first ~4 s at once: the worst packet arrived 3.6 s late, so it could only
  be timed by arrival. With `PROBE` (probesize 32768, analyzeduration 0, fpsprobesize 0) on both, the first packet
  came in 0.7 s and none was more than 0.07 s late.
- **Against NDI (Step 4).** Each camera was received over NDI (`plain_recv2.py`, libndi decoding) and over SRT at the
  same time for 5 min. Frames were paired by clock value (NDI timecode = SRT pts), with 8,400-15,300 shared per
  camera. (SRT arrival − 0.5 s) − (NDI arrival), median per 10 s:

  | Camera | Model | Median | Range |
  |---|---|---|---|
  | 2H-HOUSE | HOUSE | +64.0 ms | +62.9..+65.8 |
  | 3A-HOUSE | HOUSE | +65.5 ms | +64.8..+67.2 |
  | 1A-POV | POV | +61.5 ms | +59.7..+62.7 |
  | 5H-POV | POV | +61.6 ms | +60.0..+63.2 |
  | 2H-PTZ | PTZ | +90.8 ms | +90.0..+92.8 |
  | 3H-PTZ | PTZ | +91.1 ms | +85.3..+93.0 |

  - A flat 0.5 s missed the plan's ±50 ms, and by a different amount for each camera model.
  - The capture now takes off `delivery_delay_s` = 0.5 s + 65 ms (HOUSE), 62 ms (POV) or 91 ms (PTZ) (commit
    ed5763d1). That puts every camera where NDI put it, to about 2 ms.
  - It also moves the PTZs 27 ms earlier against the house cameras than SRT's arrivals alone would. That is the way
    the 10/30 hits pointed: house −0.03 ± 0.025 s against a PTZ.
- **Smoke run of the gate script** (sheet 2, 60 s, real recorder and composer):
  - 60.00 fps, the largest gap 0.017 s;
  - the first keyframes from all four cameras within 0.73 s;
  - 0.035-0.042 cores per capture;
  - no reconnects.

A daytime attempt (11/03 13:24 PST: sheets 2, 3 and 4 at once) was stopped after 2.5 min. A member's practice on
sheet 4 (the deployed NDI code) was already running, and four composers on one box all fell 8-10 s behind. That
session lost ~2 min 10 s (13:24:44-13:26:55). The captures were not the cause: the composers are what caps camera
practice at 2 sheets. Its files are in `aborted-1103/`.

## Task 5: gate 1, sheet 2 for an hour (11/03 23:36 - 11/04 00:36 PST)

**Passed.**

- 216,089 frames in 3,601 s: 60.00 fps; the largest gap 0.017 s, no gap over 1 s.
- Median CPU per capture with its `ffmpeg`: 0.032-0.043 cores (max 0.049); all four together 0.156.
- No reconnects, no `LiveError`, and no composer drops.

## Task 6: gate 2, sheets 3 and 4 for an hour (11/04 00:41 - 01:41 PST)

**Passed.**

- Sheet 3: 216,125 frames in 3,602 s; sheet 4: 216,065 in 3,601 s. Both 60.00 fps, the largest gap 0.017 s, none
  over 1 s.
- Median CPU per capture: 0.033-0.046 cores (max 0.056); all eight together 0.327, against the limit of 0.8.
- No reconnects, no `LiveError`, no composer drops.
- Three frames a sheet (at 300, 1800 and 3500 s) look right. There are no decode artifacts, and the wall clock in
  both long views reads the time of night. The ice lights were off, so most house panels are black.
