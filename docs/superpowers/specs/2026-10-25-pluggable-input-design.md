# Pluggable input: the club's cameras for league and practice, YouTube kept (design)

Date: 2026-10-25. Status: design, approved by the user (plan review in session). Branch `camera-direct`.
Amended 2026-10-29: the cameras are read over **SRT**, not NDI (see "Capture over SRT"). Where this document
says NDI for the new design, read SRT.

## Context

Today a league game is a YouTube video. The playlist poll finds the live stream, yt-dlp records it on a GPU worker,
and the pipeline reads the club's 1920x1080 composite: two long views either side of a strip holding both house
cameras. Practice already reads the 20 NDI cameras directly (path A, 10/24-25), but it rebuilds a YouTube-style
composite on the Ryzen with an NVENC composer, so the tracker could run unchanged.

The user wants:
- both league games and practice read straight from the four camera feeds per sheet, without building a composite;
- crop and downsample allowed, so the current models need no retraining;
- the input as its own module, so YouTube stays selectable (and another club could use either).

**User decisions (2026-10-25):**
- **Identity.** League games stay keyed to the club's YouTube video. The viewer, deep links, dedupe, dates and the
  playlist trigger are unchanged.
- **No YouTube downloads.** The YouTube stream itself is NOT downloaded. Clips come from the local camera feeds.
- **Retention.** Raw camera files are kept a few days. Overnight, every rock of a camera game gets its clip cut,
  so Clip… still works after the raw files are dropped.

**User decisions (2026-10-29):**
- **SRT replaces NDI everywhere NDI was planned**, practice included. There is no NDI fallback: the NDI library, the
  packet tap and everything built for them go.
- **Practice's capture moves to SRT first, on main** (revised the same day; it replaced "ship everything
  together"). The swap from NDI to SRT capture, with the composer kept, is built and deployed from main
  (`docs/superpowers/specs/2026-10-29-practice-srt-capture-design.md` on main). This branch takes it when it next
  merges main, and builds the rest on SRT. Everything else here still ships together from this branch.

## What exploration established

- **The pipeline's composite coupling is shallow.** Every analysis step already works in each view's own pixels:
  - `PanelCalib`, `SideView` fits, `calibrate`, `hogpaint`, `sidepaint` and the models' inputs.
  - Only the *rects* are composite pixels: `PanelSetup.rect` (`game/profile.py:27`), `SideView.rect`
    (`geometry/sideview.py:91`) and `sideview.locate`.
  - The places that cut a rect out of one file: `ingest/frames.py`, `detect/longview._command` (:507),
    `ingest/proxy.py`, `practice/clips.decode_args`, `game/clipburn`, and `game/scoreboard`/`boardsplit`
    (whole-frame search).
- **No retraining is needed.** `cameras/layouts/sheet{1-5}.json` already holds the fitted per-camera crop/turn/scale
  (`cameras/layout.py:Placement`) that reproduces YouTube's view geometry. Applying each placement to its own camera
  gives the same pixels the models were trained on, minus the composite's re-encode.
- **The scoreboard is the physical wall board**, seen in the H-end long view (`long_h`).
  - The 800 px crop keeps it at native scale.
  - The raw `long_h` frame shows the same or more.
  - Only sheets 2-4 have been confirmed.
- **Capture already exists per camera** (`cameras/capture.py`, ~0.02 cores per camera).
  - `CameraClock` stamps every packet on the recorder's shared zero.
  - Each camera goes to its own NUT FIFO.
  - Only the composer (`cameras/recorder.py:299-323`, ~2.7 cores + NVENC per sheet) merges them.
- **The recorder interface is a duck type** (`path`, `head_s`, `ended`, `whole`, `start/check/stop`, `wall_started`).
  `LiveManager._recorder` (`live/manager.py:87-104`) picks between yt-dlp and cameras by job kind.
- **Some code assumes one file or one frame:**
  - `profile.count_frame` reads both panels from one keyframe.
  - `frames.windows` / `sequence.detect_spans` share one decode across both panels (`one_pass`).
  - `recording.path` / `head_s()` assume one file.
  - Cross-camera timing (`split.long_split`, `release.find_and_pair`, `farfollow`, `linetime`) needs only a shared
    clock. The cameras give one, and with it timing is better than YouTube's ±0.5 s drift.
- **The cameras serve SRT** (measured 2026-10-28/29; scripts and results in `/data/wdd/scratch/camera-direct/` on
  the Ryzen):
  - All 20 cameras (`inventory.CAMERAS`, 192.168.0.231-.250) run an SRT listener on **UDP 1600**. A plain caller
    connects: no stream id, no passphrase.
  - Each serves **H.264 Main, 1920x1080, 60 fps, in MPEG-TS, about 9.5 Mbit/s**. Sheet 4's cameras send H.264 over
    SRT even though they send HEVC over NDI.
  - **The NDI house-camera dropout does not happen on SRT.** 3H-HOUSE over SRT for 90 min (`srt-ab/`): one
    connection, longest gap between frames 0.25 s. An NDI receiver on the same camera at the same time dropped for
    32, 16, 16 and 16 s.
  - **During a league broadcast:** all four sheet 4 cameras over SRT for 2 h (`srt-s4-1029/`, 500 ms latency): one
    connection each, 60.04 fps, longest gap 0.13 s, no SRT packet loss. The club's broadcast and our five live
    YouTube games carried on normally.
  - **Open:** the workers logged 18 H.264 decode errors in the YouTube recordings of sheets 1 and 3 during that
    run. Last night's league logged none. Each was one damaged frame and every end published, but the run did not
    show whether our SRT traffic contributed.
  - **Not yet known:** what a second SRT caller to the same camera gets: a second stream, a refusal, or the first
    caller dropped.
- **Hard limits on the Ryzen:**
  - `enp4s0` links at 100 Mbit/s today, still on 2026-10-29. Over SRT a sheet is ~38 Mbit/s and all 20 cameras
    ~190 Mbit/s, so more than two sheets at once needs the gigabit link the user is bringing up. All five sheets
    are the target.
  - /data has 917 GB free. Raw footage is ~16 GB per sheet-hour.
  - Lane CPU is ~72% decode and bound by memory bandwidth.
  - A consumer NVENC allows ~5 sessions per system, so per-view re-encoding of 20 streams is out.

## Approach

### The `feeds` module (new: `src/curling_score/feeds/`)

A **Feed** is one sheet's input over time. It exposes:
- `views`: four **Views**, keyed by the existing position names `top`, `bottom`, `left`, `right`. The timeline JSON,
  `CAMERA_FOR`, `OTHER_HOUSE` and clips keep these names. Each name maps to a role (`house_h`, `house_a`, `long_a`,
  `long_h`) through the sheet layout.
- `head_s()`: the minimum over its files.
- `ended()`, `wall_t0`, `kind`.
- `to_published_t(t)`: maps feed time to the clock the run publishes on (the YouTube video's t).

A **View** has:
- `path`, the file it is read from;
- `chain`: crop → optional transpose → optional scale, ending at the model-space size;
- `size` (w, h), in view-local pixels;
- `ffmpeg_filter()` for subprocess readers, and the same chain applied in PyAV readers.

PyAV readers apply the chain through PyAV's filter graph, so their pixels match the ffmpeg subprocess readers'
exactly.

There are two feed types:
- **`YouTubeFeed`** (`feeds/youtube.py`): one file for all four views. Each view's chain is a crop of the composite,
  from `geometry/layout.detect_panels` plus `sideview.locate`. This is today's behaviour, moved.
- **`CameraFeed`** (`feeds/cameras.py`): four files, one per camera, with chains taken from
  `cameras/layouts/sheetN.json` placements.

**Calibration moves to view-local coordinates.**
- `PanelSetup.rect` and `SideView.rect` become (0, 0, w, h) of their view.
- `timeline.json`'s `calibration` block gains `feed: {kind, views: {name: {role, size, chain}}}`.
- Readers of old timelines (`game/clips.sideviews_from_calibration`, `clipburn.Panel.from_calibration`,
  `gameclips.clip_calibration`) convert legacy composite rects by their origin.

The consumers to switch from `(path, rect)` to `View`:
- `ingest/frames.py` (`window`, `chunked`, `windows`, `keyframe_sweep`, `sample/spread_keyframes`);
- `detect/longview._command`, plus `lanekeep`'s match-by-rect;
- `detect/sequence`;
- `game/profile.count_frame`: sample both panels on a shared time grid, paired by time, rather than from one
  keyframe;
- `analyze.calibrate_from`: per-view frame lists and plates;
- `ingest/proxy.py`;
- `game/scoreboard` and `boardsplit`: read `views["right"]`;
- `practice/clips`, `game/clips`, `clipburn` (sources become per-view);
- `detect/cache` keys (path + chain);
- `fpframes`: name the camera by view name, not by rect tuple.

### Capture over SRT, and recording without a composer (`cameras/capture.py`, `cameras/recorder.py`)

(Replaces the NDI capture, amended 2026-10-29.)

**One capture process per camera, over SRT.**
- It connects as an SRT caller to `srt://<camera ip>:1600`, the address from `inventory.CAMERAS`, with a 500 ms
  latency (measured clean on sheet 4 during a league, where 200 ms once lost packets on 4H-PTZ). There is no finder
  and no camera name lookup.
- It reads the camera's MPEG-TS through FFmpeg's `srt` protocol, which the worker image's ffmpeg already has. Its
  H.264 packets are copied, never decoded or encoded.
- **The shared clock is unchanged.** `CameraClock` times every packet from its pts (the camera's clock spaces the
  frames) anchored to its arrival on the Ryzen (the cameras' clocks are off and drift). SRT pts are 90 kHz and wrap
  at 2^33 (~26.5 h): a wrap must read as continuous time, not as a clock step.
  - Measured 2026-11-03 (see "Camera sync" in `2026-10-29-practice-srt-capture-design.md` on main):
    - The long cameras' NTP times disagree by 10-90 ms from day to day, but arrival delays hold to 1-2 ms.
    - The arrival anchor is within sd 6.5 ms of the hour-long tie.
    - On the 11/02 sheet 3 league, arrival read 30 ms off between the two PTZs; NTP would have read 47 ms off.
  - No flash test (user: do our best with what we have).
  - Main's SRT capture (`practice-srt`) takes each camera model's own delivery delay off: 0.5 s plus 65 ms (HOUSE),
    62 ms (POV) or 91 ms (PTZ), measured against NDI on the same frames. That puts every camera where NDI put it, and
    the PTZs 27 ms earlier against the houses than raw arrival would. The league path inherits it with the capture.
- **Sync watchdog (league, phase 3).** Each camera run records, in its timeline diagnostics, the readings that need
  no calibration:
  - the long-camera pair's offset, from split-sync `mid` read in both throwing directions;
  - house against long, from clean hits (`hits.py` in `sync-check/` is the prototype).

  Both are logged per run and pooled per sheet over weeks. A per-camera correction constant is added to
  `CameraClock` only when the pooled numbers justify one (about 50 clean hits or more), and only with the user's
  OK. Until then the readings are reported, not applied.
- **Kept from today:** `Holder` (a stalled camera's file holds its last keyframe), `Reconnects` (5 s, doubling to
  60 s, so a stuck capture never hammers a camera), `MAX_AGE_S`, the first-keyframe check (`--first`), and the exits
  (camera silent at start, parent gone).
- **Builder's call:** whether the process drives `ffmpeg` as a subprocess (as `srt_recv.py` in the measurements
  did) or opens the URL in PyAV directly. PyAV's own FFmpeg must have `srt` for the second; check before choosing.

**Recording without a composer.**
- Each capture writes its camera's packets, with their `CameraClock` times, into a growing `live/<id>/<role>.ts`, so
  all four files share the recorder's zero. The NUT FIFOs and the composer go.
- This removes ~2.7 cores and an NVENC session per sheet, and the 0.5-0.8 s composer lag during clip cuts (F2).
- **Timestamps in the files (Claude's call, 2026-10-29):** each packet's pts and dts are rewritten to its
  `CameraClock` time, so readers, `frames.seek_lead` and `head_s` use the shared zero directly, and a reconnect gap
  reads as a gap. The cameras send no B-frames (pts = dts on 2,400 packets checked from `srt-s4-1029/`) and a
  keyframe about every 2.2 s, so one time per packet serves both. A sidecar offset map is not used.
- **One caller per camera (Claude's call):** whatever the phase 0 test finds, a camera held by a recording on this
  box is not opened by anything else on it (a per-camera lock; `cameras-calibrate` and test tools refuse or reuse
  the running file). If the test shows a second caller drops the first, the lock is the only protection, and the
  dev box must not pull SRT while the Ryzen records.
- **Disk (Claude's call):** over SRT a sheet-hour is ~17 GB. `WORKER_CAMERA_DAYS` stays the default, but pruning
  also drops the oldest kept camera games whenever /data falls below a free-space floor (builder picks the
  number, at least one five-sheet night, ~350 GB).
- **`CAMERA_MAX_SHEETS` (Claude's call):** defaults to 2 while `enp4s0` is at 100 Mbit/s; the deploy raises it to
  5 once the link reads 1000.
- The slate guard and the per-session limits are unchanged.
- `CameraRecorder` yields a `CameraFeed`. The lane and session read `feed` in place of `recording.path`.

**NDI goes.** Removed: `cameras/tap.py`, `cameras/ndi_tap.c` and the `ndi-tap` build stage in `Dockerfile.worker`;
`cyndilib` from the image; `inventory.ndi_config`; the NDI-specific parts of `capture.py` (finder, receive pump,
tap reader, re-exec with `tap_env`). Inventory keeps the camera names and addresses. The compose notes on NDI
(`deploy/docker-compose.worker.yml`) are rewritten for SRT; the containers reach the cameras from
`deploy_default` as outgoing UDP, with no `network_mode: host`. **The SRT capture itself and the NDI removal come
from main** (the practice-first decision above). This branch keeps only the composer removal and the per-camera
`.ts` writing.

### League games on cameras

- **Choosing the input.** Each playlist gets an input setting: `youtube` or `cameras`.
  - When the poll creates a live run on a `cameras` playlist, it gives the run `source: "cameras"`, up to
    `CAMERA_MAX_SHEETS` (default 5). Any further sheets fall back to YouTube. This is a safety valve, and it
    replaces `PRACTICE_MAX_CAMERA_WATCHES` for practice too.
  - Only a worker with `WORKER_CAMERAS=1` claims a camera live job.
  - The run keeps the YouTube `video_id`, sheet, league and `played_at` exactly as today.
- **Time mapping without downloading.** At the start, fetch one or two 5 s HLS segments of the live stream at a known
  media sequence (stream t = sequence × 5 s).
  - Match their left view against the camera feed's `long_a` (frame difference / motion correlation). That gives
    `published_t = feed_t + offset`, which the run stores.
  - If the fetch fails, fall back to YouTube's `actualStartTime` and flag the run as approximately aligned.
  - Everything published (timeline t, `youtube_url`, clip times) uses the published clock.
- **Failure fallback.** A camera run that fails (capture lost, link, alignment) falls back as a failed live run does
  today: the poller queues the YouTube VOD once it is archived.
- **Reprocess** (`api.py:3648`, `analyze()`):
  - `analyze()` takes a Feed as well as a URL.
  - Within the raw-retention window, admin reprocess of a camera run reads the kept camera files.
  - After that it needs a YouTube download, which needs the user's OK (memory `no-more-vod-downloads`).

### Retention and overnight clips

- **Keeping the raw files.**
  - `CameraRecorder.whole()` becomes true for a cleanly finished league recording.
  - `manager.finish` moves the four files to `kept/cams/<video_id>/`.
  - A new `WORKER_CAMERA_DAYS` (default 3) prunes them, applied ahead of the YouTube recordings in
    `ingest/prune.py`.
  - Practice recordings are still never kept.
- **The nightly clip pass** runs between midnight and 5 am, when the Ryzen is idle and most of its 32 threads are
  free.
  - For each camera run completed that day, queue the existing shot clip job for every rock: `cs_<run>_<rock>`, the
    current `CLIP_STYLE`, through `gameclips` and `api/admin/clips/backfill` logic.
  - Clip… then finds them ready. That is the same GCS key, offered for `SHOT_CLIP_DAYS` (21).
  - `game/clips.find_video` / `clipworker` learn camera feeds: views from `kept/cams/<id>/`, plus the clips worker's
    cache mounts.
  - Run it as N parallel cutters, from a night-only setting (`CLIPS_NIGHT_WORKERS`, ~6 × 4 CPUs), starting at
    00:00 and stopping new work at 05:00. In the day, Clip… keeps today's single 4-CPU clips service.
  - Load estimate: ~1,300 rocks on a five-sheet two-draw night at ~40 CPU-s each is ~52,000 CPU-s, about 40 min on
    ~24 threads.
  - Rocks not reached by 05:00 carry over to the next night. Raw files are kept for 3 days, so nothing is lost.
  - About 1-2 MB per clip on GCS: ~40 GB over 21 days.

### Practice

- Practice moves to `CameraFeed` (no composer).
- Saved calibrations (`cameras/calstore.py`, pickled composite rects) are converted by rect origin, or redone with
  `cameras-calibrate`.
- `practice/overlay` fractions become fractions of the view.
- Practice clips cut from the raw cameras.

## Branch

All work lives on the branch **`camera-direct`**, in its own worktree `.claude/worktrees/camera-direct`, cut from
main. It stays there until the whole project is done. Nothing merges to main before then. That includes the
phase 1 refactor, even though phase 1 changes no behaviour.
- Deploys for live testing (phases 2-4) go out from that branch through a clean worktree, as the practice-cams
  work did.
- The main checkout is shared with other sessions: stage only this work's hunks (memory
  `concurrent-session-commits`).

## Phases and gates

0. **Feasibility.** Measure only; no product code. Write the spec doc
   `docs/superpowers/specs/2026-10-25-pluggable-input-design.md` from this plan first.
   - **Link.** Once the user has `enp4s0` at 1 Gbit/s, confirm `/sys/class/net/enp4s0/speed` reads 1000. Then run
     a five-sheet SRT capture-only test at night: drops, CPU, disk rate.
   - **SRT, done 2026-10-28/29:** dropout-free on 3H, and four cameras through a league broadcast (see "What
     exploration established"). This covers "Receiver headroom during a broadcast" below for one sheet.
   - **A second SRT caller.** At night, connect two callers to one camera and record what each gets. The design
     assumes one caller per camera; if a second one drops the first, nothing (a clip tool, a test script) may open a
     camera that a recording holds.
   - **Decode-error baseline.** At a league with no SRT pull, count the workers' `error while decoding` lines over
     the draw, to compare with the 18 during the sheet 4 SRT run.
   - **League capture permission (user/club).** Practice was agreed for when nothing is streamed. League capture
     needs the club's OK and a privacy page update.
   - **Receiver headroom during a broadcast.** With the club's production running, one extra receiver per camera
     must not disturb it. Test on 1-2 sheets on a league night.
   - **Decode cost.** On a raw recording of real league play (1-2 sheets, `record.sh`), measure a whole end through
     the pipeline: decode-on-read from four raw files, against today's composite.
     - If it is over budget, compare these in order: NVDEC with cuvid crop/resize for the house cameras;
       capture-time house proxies (libx264 at panel size, the house views only).
     - Pick by ends/hour on the Ryzen.
   - **Alignment.** On the same night, check that segment matching gets the offset within ±1 frame against the
     live yt-dlp recording that still runs.
   - **Scoreboard.** Confirm the board appears in raw `long_h` on all five sheets.
1. **The `feeds` module, with YouTube only.** No change in behaviour.
   - Gate: identical timelines on the harness videos (`ab_rel_ff.py`, the 16-game harness).
2. **The camera recorder without a composer, and practice on `CameraFeed`**, on top of main's SRT capture (merge
   main first).
   - Gate: a recorded practice session replayed both ways from the same raw files (an offline-composed composite
     against per-view) finds the same throws, with rest within a few cm.
   - Gate: latency p90 ≤ 20 s, with calibration agreement.
3. **League games on cameras.** Playlist input setting, capacity fallback, time alignment, published clock, camera
   claim, reprocess from kept files.
   - Gate: one league night on all five sheets (or 1-2 sheets first if the user prefers), checked against the YouTube path on the same games: shots matched,
     splits, board, deep links land on the shot.
4. **Retention and the nightly clip pass.**
   - Gate: every rock of the test night clipped before 5 am, and Clip… serves them after the raw files are pruned.
5. **Later, not in this plan:** camera-only clubs (camera-native ids, schedule-driven start, self-hosted playback),
   an RTSP capture backend, and inventory/layouts as club config. Phases 1-2 leave the seams for these: the Feed
   protocol, and layouts as data.

## Critical files

- **New:** `src/curling_score/feeds/{__init__,base,youtube,cameras}.py`.
- **Removed:** `cameras/tap.py`, `cameras/ndi_tap.c`.
- **Changed:**
  - `ingest/frames.py`, `ingest/proxy.py`, `ingest/prune.py`, `ingest/cache.py`;
  - `detect/longview.py`, `detect/lanekeep.py`, `detect/sequence.py`, `detect/cache.py`;
  - `geometry/sideview.py`, `game/profile.py`, `analyze.py`;
  - `live/session.py`, `live/lane.py`, `live/manager.py`;
  - `cameras/capture.py` (SRT), `cameras/recorder.py`, `cameras/calstore.py`, `cameras/inventory.py`;
  - `Dockerfile.worker` (no tap stage, no cyndilib);
  - `practice/clips.py`, `practice/overlay.py`, `game/clips.py`, `game/clipburn.py`;
  - `game/scoreboard.py`, `game/boardsplit.py`;
  - `service/playlists.py`, `service/records.py` (`Playlist.input`, `Run.source`, the time offset);
  - `service/worker.py`, `service/gameclips.py`, `service/clipworker.py`, `service/api.py` (claim, reprocess, the
    nightly clip trigger in the scheduler);
  - `deploy/docker-compose.worker.yml`, `deploy/cloudrun.yaml`, `service/static/privacy.html`.
- **Reused:**
  - `cameras/layout.Placement`/`filtergraphs` (the chain builder);
  - `cameras/capture.CameraClock`/`Holder`/`Reconnects`;
  - `frames.seek_lead` (the MPEG-TS seek gotcha);
  - `gameclips` shot clip jobs and `clips/backfill`;
  - `prune.prune`, `cache.keep_*`;
  - `practice.end_for_league`.

## Verification

- **SRT capture:** unit tests for the 2^33 pts wrap through `CameraClock`, reconnect timing and the first-keyframe
  exit, using a recorded `.ts` from `srt-s4-1029/` (or a local SRT listener) in place of a camera; then a live
  practice session on the Ryzen at night, all four cameras of a sheet, frame gaps logged as in the measurements.
- **Unit tests:** feeds chains match `layout.filtergraphs` pixel for pixel on test patterns; `count_frame` pairing
  across files; legacy timeline rect conversion; the poller's capacity fallback; prune order. Run the affected
  subsets only; the full suite OOMs here.
- **Phase 1:** a byte-identical timeline A/B on the harness VODs, on the Ryzen in a throwaway container.
- **Phase 2:** the replay from the same raw files, both ways; a live practice session checked by hand against the
  long camera (memory `validating-release-changes`).
- **Phase 3:** a league night side by side with the YouTube path (`scripts/compare_timelines.py`); viewer deep links
  checked on a phone (devserve + cdp).
- **Phase 4:** the nightly pass log, clip counts, and a Clip… request after the prune.
