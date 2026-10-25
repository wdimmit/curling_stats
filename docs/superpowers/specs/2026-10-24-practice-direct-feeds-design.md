# Practice on the club's cameras (direct feeds, path A)

Date: 2026-10-24. Status: design, reviewed; the user's answers folded in.

*Amended 2026-10-25 by the user: YouTube practice removed; no rollback, no
both-ways comparison.* "Remove the YouTube practice feature. It isn't viable
for other reasons and we don't need to do an a/b comparison to it." Practice
reads only the cameras, and `PRACTICE_SOURCE` is gone. The places below that
keep YouTube for a rollback or compare against it say so where they stand.

## Why

Practice sessions (`/practice`, spec `2026-10-09-practice-sessions-design.md`)
today read a sheet's YouTube practice stream. That stream exists only when the
club broadcasts practice ice. Much of it is booked at irregular times and never
streamed, so a player who taps Start on an unstreamed sheet gets nothing.

The Ryzen worker sits on the club's network with the 20 NDI cameras that
YouTube's picture is made from. Reading them directly makes practice available
whenever a sheet is free of leagues. It also gives better input: each camera
sends its own H.264 at 6-10 Mbit/s, about 35 Mbit/s per sheet. YouTube's whole
composite is 0.6-1.5 Mbit/s (Qzh8 and VXU9 averages).

**Path A**, the user's choice: on the Ryzen, rebuild a picture laid out like
YouTube's from a sheet's four cameras, and run the existing practice tracker on
it unchanged. This keeps the models and calibration on the geometry they were
built for and gets the compression gain. It does not get the resolution gain
(path B, below).

## Goals

- A validated member, or anyone signed in on the club's network, can start
  practice on any of the five sheets at any time no league feed is live.
  Today's flow follows: throws on the phone, clips with the line overlay,
  summary, history.
- All five sheets can run sessions at the same time.
- Throws found, rest positions and lines are at least as good as from the
  YouTube stream of the same throws.

## Non-goals

- **Path B:** feeding each camera to the tracker separately, at full house
  resolution, with retrained models. This is a later project. Clips may be the
  first piece moved; the clip code already expects one file per camera.
- RTSP from the cameras, or NDI's Advanced SDK. Either may replace the packet
  tap later; nothing else should depend on which one is used.
- Using the direct feeds for league games.
- Audio. The PTZ cameras also send AAC; it is dropped.
- Keeping recordings. As today, practice recordings are deleted when the watch
  ends; only clips are uploaded.

## Anti-goals (failure even if it "works")

- **Recording NDI's "Video decoder not found" card.** libndi shows it when it
  cannot load FFmpeg 7. It encodes to a valid video that the tracker would read
  as an empty sheet, and it happened on the first attempt. This must be
  impossible to miss.
- **Disturbing the club's broadcast.** Each camera serves a limited number of
  NDI receivers. Take at most one connection per camera, only while a session
  on that sheet is active.
- **Reading cameras when no session is active.** Video of people on the ice is
  read only between a player's Start and the session's end. The exception is
  maintenance reads, my call: calibration refreshes, fitting each sheet's
  layout, and the tests below.
  - They keep only derived numbers (calibrations, layout fits, measurements),
    never video or frames.
  - The one exception is the layout fitting's reference frames, taken of an
    empty sheet.
  - The privacy page says so.
- **A session that never ends,** or that keeps running into a league game.

## What the user decided

1. **Start:** the player taps Start on their phone. The session covers throws
   from Start on. There is no need to see throws from before Start.
2. **End,** whichever comes first:
   - 15 minutes after the last rock is thrown;
   - when a league feed starts on any sheet, within the standard 3-minute
     playlist poll. **Leagues always use all five sheets**, so practice and
     league games never overlap. A live league feed anywhere ends practice
     everywhere and blocks Start;
   - the player taps Stop, as today.
3. **Path A,** as above.
4. **All five sheets at once.**
5. **Capture with the packet tap for now** (described below).
6. **The club knows and agrees** that practice uses its cameras when nothing is
   being streamed.
7. **Who may start:** an account with the `validated` role (or `admin`), or a
   signed-in request from the club's IP address.

## What is known about the cameras (measured 2026-10-23/24)

All of this is in the memory note `ndi-cameras-at-club`. The scratch tools are
in `/data/wdd/scratch/ndi/` on the Ryzen: `grab.py`, `clocks.py`, `avshim.c`
and the venv.

- **Inventory:** 20 AIDA NDI HX cameras at 192.168.0.231-.250 on the Ryzen's
  /23. Names are `<sheet><end>-<kind>`:
  - `HOUSE` (fisheye overhead) on every sheet;
  - `POV` long views on sheets 1 and 5;
  - `PTZ` long views on sheets 2-4.

  Each end, A and H, has one long view and one house camera.
- **Encoding:** H.264 Main@4.2, 1920x1080p60, I+P only, keyframe every 120
  frames.
  - POV/PTZ: ~10 Mbit/s, full range, BT.709.
  - HOUSE: ~6 Mbit/s, untagged.
- **Discovery:** libndi's finder needs avahi-daemon, which is not running.
  `ndi-config.v1.json` with `networks.ips` set to the 20 IPs works without it.
  In a container, use `NDI_CONFIG_DIR`.
- **Decoding:** libndi (6.3.2, bundled in the `cyndilib` 0.1.3 wheel) decodes
  HX by dlopen-ing `libavcodec-ndi.so.61`, then `libavcodec.so.61` (FFmpeg 7).
  - The host's Ubuntu 24.04 has FFmpeg 6.
  - The worker image `curling-worker:local` (Debian 13, FFmpeg 7.1) has the
    right library. Run it there.
- **Clocks:**
  - The 10 HOUSE cameras' clocks are unset. They read ~3.4-3.6 years behind and
    disagree with each other by up to 8 h.
  - POV/PTZ cameras are within ~50 ms of the Ryzen's NTP clock.
  - NDI's per-frame `timestamp` is within 40 ms of arrival on all 20.
  - **Time everything by arrival at the Ryzen, with the camera's own clock
    allowed to set the spacing between frames, anchored to arrival.** Whether
    the cameras' pictures lag one another is not measured yet.
  - *Amended 2026-10-25 by the user.* This read "by arrival at the Ryzen,
    never by the cameras' clocks", because the HOUSE cameras' clocks are years
    off. They still are, and every camera's clock drifts ~15 ms an hour against
    NDI's timestamp, so a camera's clock sets only the spacing: a frame's time
    is its pts plus the least (arrival − pts) over the last 10 s. Timed by
    arrival alone, a composer that fell behind bent the times (up to 0.8 s
    during a clip cut); the packets carry no NDI timestamp without decoding.
- **Python receive rate:** a cyndilib receive loop kept ~45-55 of 60 fps from
  one camera, whatever was done with the frames. Decoded frames through Python
  will not scale to 20 streams; hence the packet tap.

## How YouTube's picture is laid out

These observations come from one frame of sheet 1 (Qzh8, 1920x1080). **Verify
them on every sheet.**

- **Left ~800 px:** a crop of the A-end long camera (1A-POV), at roughly native
  scale.
- **Right ~800 px:** a crop of the H-end long camera (1H-POV), at roughly native
  scale.
- **Centre strip:** about 300 px wide, between flat grey letterbox bars, and
  split by a third grey bar into two house panels of roughly 300x516.
  - Each panel is that house camera's fisheye, turned 90° and scaled to about
    0.37x. A 12-foot ring is ~240 px across, against ~640 px raw.
  - The top panel appears to be the H-end house turned clockwise; the bottom
    panel the A-end house. Its logos match 1H-HOUSE.
- **A wall clock is burned in at the bottom left.** Nothing in the pipeline
  reads it.

Everything downstream depends on this layout:
- `geometry/layout.py` finds the panels by the grey bars' temporal stillness.
- `analyze.calibrate_from` calibrates the panels and solves the side views
  either side of the strip.
- `detect.sequence.detect_spans` crops panels by rectangle.
- `practice/clips.py` crops the long views.
- The overhead, side, broom and release models were trained on these crops.

## Design

### 1. Capture: one receiver per camera, with the packet tap

For each of a sheet's four cameras, a small receiver runs in the worker image.
It connects with cyndilib and preloads the libavcodec stand-in (`avshim.c`).
The stand-in is named `libavcodec-ndi.so.61` and placed on `LD_LIBRARY_PATH`.
It forwards everything to the real libavcodec. On the way, it hands each H.264
packet that libndi passes to `avcodec_send_packet` to the capture process,
stamped with its arrival time and carrying its pts (the camera's clock).

Requirements:
- **Video only.** Tell the H.264 context from the AAC one; libndi asks for both
  on PTZ cameras. The scratch version told them apart by start codes; track the
  context created for `AV_CODEC_ID_H264` instead.
- **Pin the versions the tap depends on:** cyndilib 0.1.3 / libndi 6.3.2, and
  FFmpeg 7's `AVPacket` layout (pts @8, data @24, size @32, flags @40).
- **Fail loudly at startup** unless the first keyframe parses as H.264 1920x1080
  from a real camera. That stops the "decoder not found" card from being
  recorded.
- **Stop libndi's own decode if possible.** We never need its decoded frames,
  and decoding 20 streams twice wastes ~3-4 cores. Measure whether libndi still
  delivers packets when the stand-in swallows them and returns EAGAIN. If it
  does not, let the decode run and budget for it.

The builder decides the process structure: one process per camera, or one per
sheet. Process isolation per camera is the scratch tools' pattern, and a stuck
camera then costs only itself.

### 2. Compose: one picture per sheet, laid out like YouTube's

One FFmpeg process per sheet takes the four packet streams and writes a growing
1920x1080p60 MPEG-TS. Its layout matches YouTube's for that sheet:
- crop the long views;
- turn, crop and scale the house cameras into the strip;
- draw static grey bars.

It encodes on a 4070's NVENC at a high bitrate (≥20 Mbit/s). The CPU is the
Ryzen's constraint, and the two cards have encoder sessions to spare.

- **Match YouTube per sheet, closely.** Fit each sheet's crop, rotation and
  scale from a recent YouTube frame of that sheet against the raw cameras, and
  keep them in a per-sheet layout file. The models and constants were tuned on
  that geometry. Keep panel size and position within a few pixels, and the long
  views' crop within a few percent.
  - Panel sizes differ by sheet (strips 294-302 px).
  - The 2026-27 season re-aimed sheets 2-4's long cameras
    (`side-camera-framing-2026-27`). Fit from this season's frames.
- **Frame timing:** at each 60 Hz output tick, use each camera's newest frame by
  its time (arrival, spaced by the camera's clock; see Clocks), as a vision
  mixer would. If a camera stops sending, hold its last
  frame and log it. Do not end the recording, and do not lose the other three.

### 3. The recorder the watch reads

Add a `CameraRecorder` with the interface the manager, lane and watch already
use:
- `path`, `head_s()`, `ended()`;
- `start` / `check` / `stop`;
- `wall_started` for the wall clock at t=0;
- `lookback_got_s = 0`.

Nothing in `practice/` should need to know the source changed.
`live/recorder.py`'s yt-dlp recorder stays for league games.

### 4. Calibration without a lookback

Today the watch calibrates from up to 20 minutes of stream from before Start: a
mostly empty sheet. With direct feeds there is no footage before Start, and at
Start the sheet has players on it.

My call:
- **Saved calibration.** Keep a saved calibration per sheet, made from the
  rebuilt picture while the sheet is empty. Store it on the Ryzen, the only
  machine that sees the cameras.
- **Check at Start.** Check it against the first frames. A ring fit on both
  house panels must agree within tolerance. This is a new check:
  `Calibration.agrees_with` compares only the side views' tee and hog rows, and
  only between complete calibrations. If it agrees, use it.
- **Fallback.** If not (a PTZ moved, say), recalibrate from the session's own
  frames as the watch's retry loop already does, and log it.
- **Refreshing.** The builder decides when to refresh: on a schedule at a quiet
  hour, at the end of each session, or both. Workers may stop between midnight
  and 7am when idle.

### 5. API: starting, claiming, ending

- **Starting.** `service/practice.start` no longer needs a live YouTube practice
  stream. A sheet can start unless a league `live` run is active on any sheet.
  The watch, run and job are created as today.
- **Who may start.** Start needs a signed-in user who either:
  - has `role` `validated` or `admin` (`scripts/users.py`, memory `user-roles`);
  - or is calling from the club's public IP.

  Take the client IP the same way as the existing request-IP code (the flags
  work fixed its X-Forwarded-For handling). The club IP seen in the logs is
  71.212.197.184; make it a setting, not a constant. Anyone else gets a refusal
  the page explains. Joining an existing watch is a Start, and follows the
  same rule. Viewing one's own sessions and history is unchanged. The run has no YouTube
  `video_id`; give it a camera source the builder chooses, and check every
  consumer of `Run.video_id` for practice runs (pages, clip links, dedupe).
- **Claiming.** Only a worker that can see the cameras may claim a camera
  practice job: a new worker setting such as `WORKER_CAMERAS=1`, set on the
  Ryzen only. The laptop stand-in must never claim one.
- **Ending sessions:**
  - **Idle:** `IDLE_S` 1200 → **900**: 15 minutes after the last throw, or after
    Start when nothing has been thrown (my call).
  - **Length:** `MAX_S` stays at 3 h (my call).
  - **League:** when `playlists.poll` creates a `live` run on any sheet, end
    every active practice session with reason `league` and stop every watch.
    Leagues always use all five sheets (user).
    - **Timing:** the standard poll cadence is fine (user). That is 180 s inside
      a playlist's schedule window, plus up to a minute for the worker's next
      heartbeat.
    - **Undetected leagues:** a league game that is never streamed is not
      detected. Its session ends at idle or `MAX_S`. That is a known limit, and
      the page should make Stop easy to find.
  - **Stop:** unchanged.
- **The YouTube practice path:** direct feeds replace it. My call: keep it
  behind a setting for rollback until a week of real sessions has gone well,
  then remove it.
  - *Amended 2026-10-25 by the user:* removed now, with no rollback. Closing
    practice is `PRACTICE_ENABLED=0`.

### 6. Five sheets at once

- **Stream slots:** the Ryzen's workers run `LIVE_MAX_STREAMS: "3"`.
  `worker` sets `WORKER_PRACTICE=1`, and `worker2` inherits it through
  `extends`. Five concurrent watches need the slots and lane capacity. The
  builder decides how: spread across workers, or raise the limit. League games
  never run at the same time, because leagues use every sheet.
- **Rough budget (estimates, to be measured):**
  - capture: ~1 core for 20 streams if libndi's decode can be stopped, ~4 cores
    if not;
  - five NVENC encodes;
  - five practice watches decoding their composites at 60 fps and detecting at
    10 fps.
- **Target,** my call. Report throw-to-phone latency p90 with five sessions
  running against today's 13.8 s.
  - **Gate:** p90 ≤ 20 s. Above that, optimise before shipping. In order: stop
    libndi's decode, then spread watches across workers, then decode the
    composite on the GPU.
  - Do not cut sheets or frame rate without asking.

### 6b. Known code points

These were found in review and are the builder's to fix.

- **`lane.py` treats a zero lookback as missing.** It reads
  `got or job.get("lookback_s") or LOOKBACK_S`, so `lookback_got_s = 0` becomes
  1200 s. A camera recording must give `since_s = 0`.
- **A worker restart loses throws.** With no stream history to resume from,
  the requeued watch's first publish would overwrite the throws so far. Merge
  by wall time; the 10/10 notes already flagged this as a known limit.

### 7. Privacy page

`service/static/privacy.html` currently says: "The site reads games from the
club's public YouTube streams". Change that section in the same release.
Practice reads the club's cameras directly, with the club's agreement:
- including when nothing is being streamed;
- only from a player's Start until the session ends;
- the recording is deleted afterwards;
- clips are kept as before;
- outside sessions, the cameras are read briefly to keep the calibration
  current, keeping only measurements.

## Testing

1. **Inter-camera lag.** Measure whether the house and long cameras' pictures
   lag one another, by arrival time. Use an event seen by two cameras, such as a
   stone crossing the hog line in both a house panel and a long view. YouTube's
   picture has drifting offsets of up to 0.5 s (`camera-sync-2026-27`), and the
   tracker copes with that, but the number belongs in the results.
2. **Picture fidelity, per sheet.** Calibrate the rebuilt picture and compare it
   with the YouTube picture of the same sheet:
   - panel rectangles, px/m and ring residuals;
   - side-view tee and hog rows.
3. **Accuracy.** Record a real practice session both ways at once and compare:
   - throws found, false throws;
   - rest positions;
   - lines and clips.

   The YouTube side requires a YouTube practice stream to be live, or a
   download, which needs the user's OK (`no-more-vod-downloads`).

   If no stream coincides, my call is that acceptance is a real session on the
   rebuilt picture, checked by hand against the long camera, as releases are
   validated (`validating-release-changes`), together with test 2's fidelity
   numbers. The YouTube path is not removed until one both-ways comparison has
   been made.

   *Amended 2026-10-25 by the user:* the both-ways comparison is dropped, and
   the YouTube path removed without it. Acceptance is the hand check against
   the long camera, with test 2's numbers.
4. **Load.** Run five sheets at once and record:
   - CPU, GPU and NVENC sessions;
   - dropped frames per camera;
   - throw latency p90.

   Real activity on five sheets is unlikely during a test, so a session on an
   empty sheet still exercises capture and compose.
5. **Camera receiver headroom.** On an idle camera of each kind (HOUSE, POV,
   PTZ), connect a second and third receiver and confirm all of them get full
   frame rate. If a camera has no room beyond the club's production plus one
   receiver, stop and come back to the user before building further. The
   session-ending rules would then have to anticipate leagues, not react to
   them.
6. **Failures:**
   - a camera unplugged mid-session (held frame, logged, session continues);
   - the stand-in absent (startup fails loudly);
   - a league feed starting (sessions end with `league`).

## Left to the builder

- Process structure for capture and compose, and the format between them.
- Encoder settings within the bitrate floor.
- How each sheet's layout is fitted, and the file it lives in.
- The calibration refresh schedule.
- The run's camera source id.
- How the five watches are spread over the Ryzen's workers.
- The test harness.
