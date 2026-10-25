# Practice on the club's cameras: feasibility results

Plan: `2026-10-24-practice-direct-feeds.md`, Task 1. Run 2026-10-24, 16:20-16:43 UTC, on the Ryzen
(`curling@100.85.240.114`) in throwaway `curling-worker:local` containers. The production containers
were not touched.

## Summary

| Step | Result | Pass? | What later tasks adopt |
|---|---|---|---|
| 1. Packets without libndi's decode | 60.0 packets/s with decode off; CPU 0.02 cores off vs 0.48-0.51 on | **Pass** (≥ 59) | Task 2: **`NDI_TAP_DECODE=0` by default** |
| 2. Bridge networking (`deploy_default`) | Finder saw all 20 sources; 60.0 packets/s | **Pass** | Task 10: **keep the workers on `deploy_default`**; no `network_mode: host` |
| 3. NVENC in a worker-like container | Fails on GPU 0 alone, with any driver capability. Works on GPU 1 alone, and on either GPU when the container has both | **Fail as briefed**; fix found | Task 10: **both GPUs in each worker, pinned with `CUDA_VISIBLE_DEVICES`** (below). No `NVIDIA_DRIVER_CAPABILITIES` |
| 4. Receiver headroom | 1A-HOUSE, 1A-POV, 2A-PTZ: 3 receivers each at 60.0 fps, no lost frames | **Pass** (≥ 55) | No stop. Mind keyframe storms (below) |

## Setup

- **Scratch:** `/data/wdd/scratch/ndi/t1/` on the Ryzen. The shared scratch files (`avshim.c`, `grab.py`,
  `shim/`) were left as they were, because another session was working in that folder.
  - `t1/avshim.c` is `avshim.c` extended for this task, and Task 2 should start from it. Built to
    `t1/shim/libavcodec-ndi.so.61`.
  - `t1/rx.py` is one receiver.
  - `t1/run.py` holds N receivers at once, each in its own process, over the same 30 s window.
  - `t1/step4.sh` runs Step 4.
  - `t1/results.jsonl` and `t1/step4.log` hold the numbers.
- **The stand-in (`t1/avshim.c`):**
  - It writes one numbers-only line per H.264 packet: arrival ns, pts, size, flags and first NAL type. No video
    is kept.
  - It tells the H.264 context by `codec_id` at offset 24 (27 = H.264).
  - With `NDI_TAP_DECODE=0`, `avcodec_send_packet` returns 0 without forwarding, and
    `avcodec_receive_frame` returns `-EAGAIN` (-11), for that context only. AAC on the PTZ cameras still
    forwards.
  - It counts `avcodec_receive_frame` calls and decoded frames, to prove whether decode ran.
  - Build:
    `gcc -O2 -Wall -shared -fPIC -o t1/shim/libavcodec-ndi.so.61 t1/avshim.c -Lstub -Wl,--no-as-needed -lavcodec -ldl -Wl,-soname,libavcodec-ndi.so.61`.
    `--no-as-needed` matters with Ubuntu's gcc: the shim calls nothing in libavcodec directly, so
    without it `NEEDED libavcodec.so.61` is dropped.
- **The receive loop** is `grab.py`'s: `rx.receive(recv_video, 1000)` with `UYVY_BGRA`, bandwidth `highest`.
  - Each frame is dropped with `vf.skip_frames(False)` instead of being copied out and encoded with ffmpeg.
  - A frame must be consumed somehow. Otherwise cyndilib's buffer queue fills, and `receive()` spins on
    `recv_buffers_full`. The first attempt did exactly that: 1.3 cores and 0 frames, so it was discarded.
- **What was measured:**
  - **Packets/s:** packets reaching the stand-in in the 30 s window, by arrival time.
  - **Lost frames:** gaps in the packets' pts.
  - **CPU:** `os.times()` user+system for the whole receiver process (all of libndi's threads) over the
    window.
  - Every receiver was connected 15 s before its window opened.
- **Box state:** both workers were idle, claiming and getting 204s. No league stream was live (Saturday
  ~09:30 PT). Whether the club's own NDI production was connected to any camera cannot be seen from the
  Ryzen.

## Step 1: packets without libndi's decode (host network, 1A-POV, 30 s)

```
cd /data/wdd/scratch/ndi && docker run --rm --network host --user $(id -u):$(id -g) \
  -e HOME=/ndi/home -e NDI_CONFIG_DIR=/ndi/home/.ndi -e PYTHONPATH=/ndi/pylib-cont \
  -e LD_LIBRARY_PATH=/ndi/t1/shim -e NDI_TAP_DECODE=<1|0> \
  -v /data/wdd/scratch/ndi:/ndi -w /ndi --entrypoint python curling-worker:local \
  t1/run.py 1A-POV 1 30 s1-host-dec<1|0>
```

| | Decode on (`NDI_TAP_DECODE=1`) | **Decode off (`=0`)** |
|---|---|---|
| Packets reaching the stand-in | 1800 in 30 s = **60.0/s** (each second 60) | 1800 in 30 s = **60.0/s** (each second 60) |
| pts gaps / keyframes | 0 / 15 | 0 / 15 |
| Bitrate | 12.3 Mbit/s | 12.4 Mbit/s |
| Decoded by libavcodec | every packet (2089 of 2089 in a 20 s check run) | 0 (`receive_frame` called ~once per packet, always `EAGAIN`) |
| Video frames reaching Python | 8-29 fps (three runs) | 0 |
| **CPU of the receiver process** | **0.48 cores** (0.51 and 0.41 in two 20 s check runs) | **0.019 cores** |

**Pass: 60 packets/s with libndi's decode off.** libndi keeps feeding packets when the decoder swallows
them and never returns a frame. That saves ~0.47 cores per camera, ~9-10 cores for 20, which is more than
the spec's 3-4 estimate. **Task 2: `NDI_TAP_DECODE=0` is the default.** Task 10 needs only ~0.5 core in
total for 20 captures.

What this means for later tasks:
- **Task 3:** with decode off, cyndilib's `receive()` never returns a video frame. **"Nothing received for
  `RECONNECT_S`" must be judged from the tap's packets**, not from `receive()`.
- **Task 3:** the first packets libndi passes can be P-slices (NAL type 1) before the first SPS/IDR (NAL
  type 7). The holder's "nothing before the first keyframe" is needed.
- **Task 2:** the tap must also define `avcodec_receive_frame`, not only `avcodec_send_packet`.
- **Decoded frames through Python:** with decode on, libndi decoded all 60 fps, but the Python loop got only
  8-29 fps, below the spec's 45-55. This confirms that decoded frames through Python are not a way to
  capture.

## Step 2: bridge networking (`--network deploy_default`, 1A-POV, 30 s)

The Step 1 command, with `--network deploy_default` in place of `--network host`. That is the workers'
bridge, 172.18.0.0/16, NATed to the Ryzen's 192.168.1.62/23.

| | Decode off | Decode on |
|---|---|---|
| Sources the finder saw | **20**: 1A-HOUSE ... 5H-POV, all of them | 20 |
| Packets/s | **60.0** (each second 60), 0 pts gaps | 60.0 (each second 59-61), 0 pts gaps |
| CPU | 0.022 cores | 0.50 cores |

**Pass.** Discovery uses `ndi-config.v1.json`'s IP list, through `NDI_CONFIG_DIR`, so mDNS is not needed.
The receivers connect out to each camera's TCP 5960 (control) and 5962 (video), and NAT handles that.
**Task 10: the workers stay on `deploy_default`;** no `network_mode: host`.

## Step 3: NVENC in a container started like `deploy-worker-1`

`deploy-worker-1` has `runc` with the device request `{driver: nvidia, device_ids: ["0"], capabilities: [[gpu]]}`,
no `NVIDIA_*` env, `--shm-size 2g`, on `deploy_default`.

```
docker run --rm --network deploy_default --shm-size 2g --gpus '"device=0"' --entrypoint ffmpeg curling-worker:local \
  -f lavfi -i testsrc2=s=1920x1080:r=60 -t 5 -c:v h264_nvenc -b:v 20M -f null -
```

| Container's GPUs | `NVIDIA_DRIVER_CAPABILITIES` | Result |
|---|---|---|
| `device=0` (as `deploy-worker-1`) | unset | **fails**: `OpenEncodeSessionEx failed: unsupported device (2)`, `No capable devices found` |
| `device=0` | `compute,utility,video` (the brief's retry) | **fails**, the same way |
| `device=0` | `all` | fails |
| `--runtime nvidia`, `NVIDIA_VISIBLE_DEVICES=0` | `compute,utility,video` | fails |
| `device=1` (as `deploy-worker2-1`) | unset | **encodes**: 300 frames, 6.6x real time |
| `all`, `ffmpeg -gpu 0` or `-gpu 1` | unset | encodes |
| `all`, `CUDA_DEVICE_ORDER=PCI_BUS_ID`, `CUDA_VISIBLE_DEVICES=0` | unset | encodes, and `nvidia-smi` shows the session on GPU 0 |
| `all`, `CUDA_DEVICE_ORDER=PCI_BUS_ID`, `CUDA_VISIBLE_DEVICES=1` | unset | encodes, with the session on GPU 1 |
| `device=0` plus `--device /dev/nvidia1` | unset | encodes |

**The cause is not the driver capability.**
- Docker here injects GPUs by CDI (`NVIDIA_VISIBLE_DEVICES=void` inside the container). It mounts every
  driver library, `libnvidia-encode.so.1` and `libnvcuvid.so.1` included, whatever the capability.
- CUDA works on `device=0`: torch sees the RTX 4070.
- What fails is NVENC on GPU 0 (PCI 0A:00.0) **when `/dev/nvidia1` is not in the container**. GPU 1 alone
  does not need `/dev/nvidia0`. Driver 610.57.04, open kernel module.

**Task 10 must add this, for both GPU workers:**
- `devices: [{ driver: nvidia, device_ids: ["0", "1"], capabilities: [gpu] }]`;
- `CUDA_DEVICE_ORDER: PCI_BUS_ID`;
- `CUDA_VISIBLE_DEVICES: "0"` on `worker`, `"1"` on `worker2`.

*Superseded in Task 10:* only `worker` holds both GPUs, pinned with `CUDA_VISIBLE_DEVICES: "0"`.
`worker2` keeps card 1 alone, which encodes without card 0, and has no `CUDA_VISIBLE_DEVICES`: its one card
is its CUDA device 0, so a pin of `"1"` there would hide it (`deploy/docker-compose.worker.yml`).

What was checked:
- With that pin, torch sees exactly one GPU, the right one by UUID: `ae8789f2…` (GPU 0) for `"0"`,
  `8c01673e…` (GPU 1) for `"1"`.
- ffmpeg's NVENC picks the pinned GPU without `-gpu`.

What was ruled out, and what was not done:
- Adding only `/dev/nvidia1` to `worker` also fixes NVENC. But CUDA then sees both GPUs, so the pin is
  needed anyway, and the `device_ids` form is the cleaner of the two.
- `NVIDIA_DRIVER_CAPABILITIES` is not needed.
- Nothing was changed in the deploy.

## Step 4: receiver headroom (spec test 5), decode off, 30 s per step

`t1/step4.sh CAMERA IP` holds 1, then 2, then 3 receivers. Each receiver is its own process and its own NDI
connection, and all of them share one window. Through each window the script samples, every 2 s, the
video connections from the Ryzen to the camera. It also lists any other NDI script running on the box.

| Camera | 1 receiver | 2 receivers | 3 receivers | Lost frames | Keyframes / 30 s | Bitrate | Video connections seen |
|---|---|---|---|---|---|---|---|
| 1A-HOUSE (.241) | 60.0 | 60.0, 60.0 | **60.0, 60.0, 60.0** | 0 | 15 | 6.2-6.5 Mbit/s | 1 / 2 / 3 |
| 2A-PTZ (.232) | 60.0 | 60.0, 60.0 | **60.0, 60.0, 60.0** | 0 | 15 | 8.4 Mbit/s | 1 / 2 / 3 |
| 1A-POV (.231), rerun | 60.03 | 60.0, 60.0 | **60.0, 60.0, 60.0** | 0 | 15 | 12.3 Mbit/s | 1 / 2 / 3 at all 15 samples |

The figures are frames per second each receiver got. Every second of every window had 59-61 packets.

**Pass: each kind of camera served 3 receivers at full rate.** The plan's stop condition (a camera that
can't serve 2) did not happen.

**1A-POV's first pass is discarded: another reader was on the camera.**
- That pass ran at 16:36-16:38. Another session on the box was reading 1A-HOUSE and 1A-POV at the same time,
  with `tsprobe.py` under an `LD_PRELOAD` clock shift.
- During the 1- and 2-receiver windows there was one video connection more than ours. Those still got
  60.0 fps.
- During the 3-receiver window, 1A-POV sent **163-171 keyframes in 30 s instead of 15**, at 21 Mbit/s
  instead of 12. Our three receivers got 56.1, 56.3 and 28.8 fps, losing 109-918 frames.
- Once the other session's reads had stopped, the rerun at 16:40-16:43 was clean, with the counts sampled
  all through each window.
- So the drop came from something re-requesting keyframes (a receiver reconnecting or misbehaving), not
  from the number of receivers. The 2-receiver window with the extra reader also had extra keyframes (19).

**Concern for Task 3 and the "don't disturb the broadcast" anti-goal:**
- A receiver that makes a camera re-key degrades **every** receiver of that camera. That would include the
  club's production.
- Task 3's reconnect (after `RECONNECT_S = 5.0` of silence) should back off rather than loop.
- Task 11's load test should count keyframes per camera (15 per 30 s is normal).

## Cleanup

- No video was written. Every file in `t1/` is numbers or code.
- The per-packet stats files in `t1/stats/` were deleted after these tables were made.
- `t1/results.jsonl` (one summary line per receiver per run) and `t1/step4.log` remain.

# Task 11: validation on the box

Plan Task 11, Steps 1-6 and 8. Step 7 (the deploy) was not part of this run. Run 2026-10-24,
20:31-22:45 UTC (13:31-15:45 PT, a Saturday), on the Ryzen (`curling@100.85.240.114`).

## Summary

| Step | Result | Pass? |
|---|---|---|
| 1. Calibrate each sheet when it is empty | No sheet was empty: sheets 1, 2, 3 and 5 had games on them (wheelchair and stick curling) all afternoon. `cameras-calibrate` was run anyway, saving to a scratch root: sheets 1, 2, 3 and 5 calibrated complete. Sheet 4 cannot be read: its four cameras send HEVC | **Not as specified** (sheets in use); CLI works; sheet 4 blocked |
| 2. Picture fidelity | Sheets 1, 2, 3, 5: panel rectangles within 1 px, px/m within 0.21%, tee and hog rows within 2.0 px of YouTube's | **Pass** (4 of 5 sheets; sheet 4 not measurable) |
| 3. Inter-camera lag | Long view minus house panel at the hog line, 17 throws: median **+0.40 s**. Sheet 1: +0.19 to +1.00 s (n 6). Sheet 2: -3.1 to +4.3 s (n 11), on a recording whose camera queues were at their cap (defect D2) | Measured; no better than YouTube's ±0.5 s |
| 4. Accuracy | No YouTube practice stream to compare with. A partial check of one session (sheet 2, 40 min, 30 throws) against the long cameras: 1 throw missed and 1 false throw found in 8 cases examined; the rest could not be settled | **Not passed**: inconclusive; the YouTube path must stay |
| 5. Load | Five sessions could not run: the Ryzen's network link is at **100 Mbit/s** (it should be 1 Gbit/s) and five sheets need ~170 Mbit/s. With 3-4 sheets the link saturated and three sheets failed to start. Two sessions on one worker: CPU 10.2 cores, NVENC 2 sessions at 23%, cameras 60.0 packets/s, **throw latency p90 23.8 s** (A) and 28.6 s (Q), with up to 10 s more hidden in the composer's queues (D2) | **Fail**: gate missed at two sessions; five not runnable |
| 6. Failures | Capture killed: the picture holds, a warning in 2.2 s, recording goes on. All four cameras cut for 30 s: held, back 0.1-0.4 s after the network returned. Tap removed: camera-named `LiveError` in 0.11 s. League: covered by the API test | **Pass** |

**Defects found** (details in "Defects" below):
- **D1:** a camera that goes quiet just after its first keyframe stops the composer from ever starting. The recording fails at `STALL_S` ("composer stalled at 0 s"). This happened 2-3 times.
- **D2:** the composer's input queues fill up and stay full. That delays cameras by up to 10 s, which `head_s` cannot see, and thousands of drop-to-keyframe events followed under two sessions.
- **D3:** the two-session latency is above the gate. The lane runs one watch step at a time: a watch catching up starved the other for minutes, and calibration steps of 14-22 s block every watch on that worker.
- **D4-D9:** smaller ones.

**Environment** (not code), to fix before deploying:
- **E1:** the Ryzen's NIC `enp4s0` is linked at 100 Mbit/s. The kernel says "downshifted": both ends advertise 1000baseT/Full, so it is most likely the cable or the port.
- **E2:** sheet 4's cameras send HEVC.

## Setup

- **Image:** `curling-worker:practice-cams` rebuilt from the branch at `fab1e4da`, image `d94013c86a3d`.
  - The worktree was rsynced to `/data/wdd/scratch/practice-cams-src/` and built with `docker build -f Dockerfile.worker`.
  - The installed `cameras/*.py`, `practice/watch.py` and `live/lane.py` match the branch byte for byte (md5).
- **Containers:** throwaway, with `--network deploy_default --shm-size 2g --user 1000:1000`.
  - A worker-like container had `--gpus '"device=0,1"' -e CUDA_DEVICE_ORDER=PCI_BUS_ID -e CUDA_VISIBLE_DEVICES=0`,
    the new `worker` config. The other had `--gpus '"device=1"'`, like `worker2`.
  - Nothing was run in `/data/wdd/curling_score/deploy`, and `curling-worker:local` was not retagged.
  - The production containers were never restarted: "Up 19 hours" at the start, "Up 21 hours" at the end.
- **Production was idle throughout:**
  - every claim from both workers got a 204;
  - `GET /api/games` listed 184 games, all `ready`, nothing live;
  - a monitor polled the workers' logs every 30 s through every run, and no job was claimed.
- **Scripts and numbers** are in `/data/wdd/scratch/practice-cams-work/t11/`. No video is kept there.
  - `harness.py` runs a sheet's `CameraRecorder` and `PracticeWatch` the way a worker does:
    - recorders start one after another;
    - the models load once (`analyze.load_models`, imgsz 448);
    - each watch is built as `lane.video_sessions().make` builds a camera watch: `since_s` 0, `t0_s` 0,
      `wall_t0` = `wall_started`, a `CalStore` under a scratch root, and a `Clipper` whose upload only
      records the clip's size;
    - the main thread is the lane: one watch step per turn, first sheet first, a 5 s sleep when idle, and a
      60 s back-off after a failed step (`live/lane.py`'s rules);
    - a thread is the manager: `check()` every 20 s;
    - every 5 s it samples CPU per process (`/proc` and the container's cgroup), RSS, each head against the
      wall clock, and the composer's output size and progress line;
    - it publishes to `pub/sheetN/throws.jsonl`. Each throw's latency is publish time minus when its rest
      (or, for a stone that did not arrive, its release + 15 s) was written, by the wall clock.
  - `site/sitecustomize.py` is on `PYTHONPATH` only in these containers. It wraps `tap.read_records` in each
    capture and logs a `TAPCOUNT` line every 10 s: packets, keyframes, Mbit/s and the longest gap. The
    `site-q/` version also logs the capture's FIFO queue depth. Nothing in the packet path changes.
  - The rest: `peek.py`, `longcap.py`, `compare.py`, `events.py`, `sidelag.py`, `crossings.py`,
    `summarize.py` and `netcut.sh`. Logs and numbers are under `runs/`.
- **Who was on the ice:** a look at each sheet's composite at 20:35 (one frame per sheet, viewed once and
  deleted) found games on sheets 1, 2, 3 and 5. They were wheelchair and stick curling, with stones in play.
  Sheet 4 sent nothing (E2). The throws measured below are those games' deliveries, not a practice session.

## Environment findings

**E1: the Ryzen's network link runs at 100 Mbit/s.**
- `ethtool enp4s0` (RTL8125 2.5GbE) gives `Speed: 100Mb/s`. Both the NIC and its link partner advertise
  `1000baseT/Full`.
- The kernel logged `Link is Up - 10Mbps/Full (downshifted)` at the 01:25 UTC boot, then
  `100Mbps/Full (downshifted)` at 01:27. A gigabit link that downshifts usually means a cable or port with
  a bad pair.
- The second port, an Intel I211 (`enp5s0`), is unplugged.
- **Measured per camera** (`TAPCOUNT`, median): HOUSE 5.5-8.3 Mbit/s, PTZ 8.4-8.5, POV 12.3-12.4.
  - A sheet needs 31-38 Mbit/s, so five sheets need about **170 Mbit/s**.
  - Two sheets put 75 Mbit/s on the link (`rx_bytes`).
  - Three to four sheets put **96 Mbit/s** on it, with 14,605 dropped packets on the interface since boot.
- **What a saturated link does to the cameras.** With 3-4 sheets connected, at 20:44-20:48, the cameras
  were in keyframe storms: 12-37 keyframes per 10 s instead of 5, and 1.4-37 packets/s instead of 60.
  - Silences of 5-15 s followed every new connection.
  - Three sheets failed to start: D1, plus "camera 3H-PTZ sent no H.264 1920x1080 within 15 s".
  - Once two sheets were stopped, every camera was back to 60.0/s with 5 keyframes per 10 s within 10 s.
  - A camera that re-keys degrades all of its receivers, so this can also reach the club's own production
    (Task 1).
- **Before any deploy, fix the link,** for example with a new cable or the I211 port, and check
  `cat /sys/class/net/enp4s0/speed` reads 1000. Even at 1 Gbit/s, a share of 170 Mbit/s is worth
  watching.

**E2: sheet 4's cameras send HEVC.**
- A decode-on receiver with Task 1's stand-in (`t1/rx.py`) on 4A-HOUSE, 4A-PTZ, 4H-HOUSE and 4H-PTZ found
  libndi opening `avcodec_find_decoder id=173` (HEVC) for all four. No H.264 packet reached the tap.
- `CameraRecorder(4)` and `cameras-calibrate --sheet 4` fail as designed, in 15 s, with
  `camera 4A-HOUSE sent no H.264 1920x1080 within 15 s`. Their captures reconnect at 5 s first, since the tap
  ignores HEVC.
- Sheet 4 needs the club to set those cameras to H.264, or a tap that handles HEVC.
- Task 4 fitted sheet 4's layout from decoded clips, which work whatever the codec, so it never showed
  this.

## Step 1: calibrate each sheet

- **Not as specified:** no sheet was empty at any point in the run. The sheets were in play from 20:35 until
  at least 21:20 UTC.
- To check that the CLI works on the box, it was run on every sheet anyway, one sheet at a time. It saved
  only to a scratch root, never the workers' cache. Numbers only were kept, and the CLI deleted its own
  recording.

```
docker run --rm --network deploy_default --gpus '"device=1"' --shm-size 2g --user 1000:1000 \
  -v /data/wdd/scratch/practice-cams-work:/w -w /w --entrypoint curling-score curling-worker:practice-cams \
  cameras-calibrate --sheet N --root /w/t11/calroot-cli
```

| Sheet | Took | Top panel | Bottom panel | Left view tee / hog | Right view tee / hog | Saved |
|---|---|---|---|---|---|---|
| 1 | 74 s | (812,10,298,510) 67.4 px/m, 0.53 cm | (812,552,298,517) 75.4 px/m, 1.10 cm | 494.9 / 586.0 | 463.1 / 546.0 | yes |
| 2 | 73 s | (810,10,297,514) 76.4 px/m, 0.94 cm | (810,552,297,517) 77.6 px/m, 0.90 cm | 361.7 / 433.0 | 364.7 / 433.0 | yes |
| 3 | 74 s | (808,10,303,522) 79.0 px/m, 1.11 cm | (808,540,303,531) 77.2 px/m, 1.13 cm | 363.6 / 434.0 | 362.7 / 431.0 | yes |
| 4 | 16 s | - | - | - | - | no: `camera 4A-HOUSE sent no H.264 1920x1080 within 15 s` (E2) |
| 5 | 72 s | (806,10,300,534) 79.5 px/m, 1.05 cm | (806,554,300,516) 75.6 px/m, 0.70 cm | 471.5 / 558.0 | 481.6 / 568.0 | yes |

- The residual is the panel calibration's ring residual.
- Every hog line was found, and every side view was calibrated across.
- The watches also seeded calibrations from these busy sheets (Ruling 12): sheet 2 in run A, sheet 5 in run
  L2B. They agree with the CLI's to 0.1 px/m and 0-1 px.
- **For the user:** run `cameras-calibrate` for real at an empty-sheet time, in both workers, once E1 is
  fixed.

## Step 2: picture fidelity

`compare.py` runs `analyze.calibrate_from` on each sheet's 12 YouTube frames from this season
(`layout-ref/yt/sheetN/`, Task 4) and compares the result with the camera calibration from Step 1.

| Sheet | Panel rect Δ (x,y,w,h), worst | px/m Δ top / bottom | Side rows Δ, worst (tee, hog) | Pass |
|---|---|---|---|---|
| 1 | 1 px (y) | +0.10% / -0.07% | 1.5, 2.0 (left) | yes |
| 2 | 1 px (y, bottom) | +0.16% / +0.21% | 1.3, 1.0 | yes |
| 3 | 1 px (x) | +0.03% / +0.17% | 0.9, 1.0 | yes |
| 5 | 1 px (x) | -0.07% / +0.03% | 1.4, 1.0 | yes |
| 4 | not measurable (E2) | | | - |

- **Pass on all four readable sheets:** rectangles within 4 px, px/m within 3%, and rows within
  `SIDE_AGREE_PX` (3.0).
- Ring centres agree to 0.6 px across and 0-2.2 px along the sheet.
- The camera residuals are 0.53-1.13 cm, against YouTube's 0.41-1.09 cm.
- The watch-seeded calibrations (sheet 2, sheet 5) also pass, with the same margins
  (`runs/compare-watch.jsonl`).

## Step 3: inter-camera lag

- **What was measured:** each throw's stone crossing the arriving house's hog line, in two cameras. That
  house's panel gives the pipeline's own tripwire time, `t_far_s` (`game/fartime.py`). The long camera
  behind the thrower looks at the same line (`hogtime.CAMERA_FOR`).
  - That long view was decoded at 30 fps over `t_far_s` ± 12 s.
  - The production side model (`sidemodel.detect_band`, ds13e) found stones of the throw's colour around the
    view's hog row.
  - The crossing is when the stone's bottom edge passes the hog row, interpolated (`sidelag.py`).
- **Data:** run A's recordings of sheets 1 and 2, 40 minutes each, deleted after measuring. Only arrived
  throws with a `t_far_s` count: 10 on sheet 1 and 16 on sheet 2. The side model found the crossing in 6 and
  11 of them.

| | n | Median | IQR | Range |
|---|---|---|---|---|
| Sheet 1 (top pair: top panel vs left camera) | 6 | **+0.35 s** | +0.23..+0.75 | +0.19..+1.00 |
| Sheet 2 (both pairs) | 11 | **+0.58 s** | -0.20..+1.18 | -3.15..+4.30 |
| Both | 17 | **+0.40 s** | | |

- **Positive means the long view sees the crossing later than the panel.**
- **Checked by eye** on one throw (sheet 2, t_1412.2, red, to the bottom house). Composite frames at 1412.50,
  1412.85, 1413.50 and 1415.04 s, viewed once and deleted:
  - the panel shows the stone on the hog line at ~1412.85;
  - the right camera shows the same stone reaching that line ~1-2 s later;
  - the script measured +2.19 s.
- **Reading:**
  - On sheet 1 the long camera trails the panel by 0.2-1.0 s.
  - Sheet 2's spread of ±3-4 s comes from a composite whose four input queues sat at their 600-packet cap
    for the whole run (D2). Each camera was then up to 10 s behind, and by varying amounts.
  - Either way, the direct feeds are not in better sync than YouTube's picture (±0.5 s with jumps,
    `camera-sync-2026-27`). The spec expected arrival-time stamping to remove most of that.
- **Caveats:**
  - These were wheelchair and stick deliveries, with slow stones and people walking in front of the long
    cameras.
  - The two measures take different points of the stone: the panel tripwire uses the leading edge, the side
    model the box's bottom. That is about 0.1 s at these speeds.
  - The near-hog pair (`panel_delta`, instrumented in the harness) was never available. The throwing panel
    did not time a single departure.

## Step 4: accuracy

- **No both-ways comparison was possible.** No YouTube practice stream was being processed, and YouTube was
  not queried (`youtube-gentle`, `no-more-vod-downloads`). **The YouTube path must stay**, as the plan says.
- **A partial check of one camera session by hand.** The session was sheet 2 in run A: 40 min, 30 throws,
  28 of them arrived.
  - The independent second opinion was `crossings.py`. It runs the production side model at 10 fps over both
    long views for the whole recording, and books every red or yellow stone that crosses a hog line moving
    away from the camera.
  - It found 22 crossings. 10 of them matched an arrived throw of the same house and colour, resting 0-25 s
    later.
  - That leaves 18 throws with no crossing and 12 crossings with no throw. Eight of those were examined by
    eye, in two contact sheets of composite frames, viewed once and deleted:
    - **Throws with no crossing** (4 examined):
      - t_232.9, t_590.6 and t_1607.4 are **real throws**. Each stone stopped short, at or just past the far
        hog line, so the side model never saw a full crossing. The watch's "short" was right for all three.
      - t_2125.6 ("out") is **probably false**: at its time, players were clearing stones from the bottom
        house at the end of an end, and no delivery is visible.
    - **Crossings with no throw** (4 examined):
      - 522.75 (top, red) is **a missed throw**. A red stone crept past the top house's hog line at ~523 s
        and stopped short at ~532 s, and the watch reported nothing between t_464.8 and t_549.2.
      - 846.05, 1651.05 and 1829.25 **could not be settled from the frames**: people in both views, or
        stones near the line already.
- **Other figures for this session** (sheet 2; sheet 1 in brackets):
  - rest rings: "short" 12 (31), "out" 7 (5), 8-ft 4 (1), 12-ft 5 (4), long 0 (2);
  - release found for 13 of 30 (4 of 45);
  - splits for 7 (1);
  - lines for 4 (2);
  - clips: 13 (4) cut and "uploaded", 0.10-0.39 MB each, 1.7-3.3 s to cut. Every other throw had `clip`
    None, meaning no clip possible.
- **Reading:**
  - This is not an acceptance. One miss and one likely false throw in 8 hard cases says the pipeline has
    trouble with these deliveries and stones that stop short. It says nothing about practice slides, which
    this activity did not include.
  - A real practice session (slides from the hack) has to be checked the same way, ideally both ways
    against a YouTube practice stream.
  - The procedure for the user:
    1. When someone practises on a sheet and its YouTube practice stream is live, start a camera session on
       the box with the harness. That is `harness.py --sheets N --watch --keep-rec`, while the API's
       YouTube session runs on the same sheet.
    2. Compare the two throw lists. Check each disagreement on the long view
       (`validating-release-changes`).
    3. Delete the recording.

## Step 5: load

**Five sessions could not run.**
- Five sheets need ~170 Mbit/s on a 100 Mbit/s link (E1), and sheet 4 cannot start (E2).
- What did run:
  - **L1, 20:43:55-20:48:20:**
    - container A had sheets 1, 2 and 3 (the `worker` GPU config, GPU 0); container B had sheets 4 and 5
      (GPU 1); a watch on every sheet that started;
    - sheet 3 failed to start (3H-PTZ silent), sheet 4 failed (HEVC), and sheet 5's composer stalled at 0 s
      (D1);
    - B was restarted with sheets 3 and 5 (L2B), and sheet 3 stalled at 0 s;
    - the link was at 96 Mbit/s and saturated, so B was stopped to protect the cameras.
  - **A, 20:43:57-21:24:23:** sheets 1 and 2 with their two watches in one worker-like container, 40 min.
    From 21:00:11 to 21:24:18 the link was clean at 75 Mbit/s. That window is the steady state below.

**Two sessions, steady state (24 min, 290 samples):**

| | Mean | p90 |
|---|---|---|
| Container CPU, total (cores) | 10.17 | 11.66 |
| Captures, all 8 | 0.23 (0.024-0.033 each) | 0.26 |
| Composer, sheet 1 / sheet 2 | 2.95 / 3.76 | 3.07 / 4.73 |
| Watch process (lane, 2 watches, PyAV decode) | 2.52 | 3.97 |
| Rest (clip cuts, ffprobe and other children) | 0.71 | |
| Host, all 32 threads (vmstat) | 33.4% busy = 10.7 threads | |
| GPU 0: utilisation / NVENC | 12.6% / 22.6% (max 69% / 37%) | |
| NVENC sessions | 2 on GPU 0 (2 and 2 per GPU in L1's four-sheet window) | |
| GPU 0 memory | 7.3 GB (production's 3.7 GB + the harness models) | |
| RSS: watch process / composer / capture | 2.8 GB (peak 3.8) / 0.61-0.62 GB / 70-89 MB | |

| Camera | Packets/s, median (min) | 10-s windows under 59/s | Keyframes per 10 s, median (max) | Mbit/s | Longest gap |
|---|---|---|---|---|---|
| 1A-HOUSE | 60.00 (59.81) | 0 | 5 (6) | 6.67 | 0.15 s |
| 1A-POV | 60.00 (59.79) | 0 | 5 (6) | 12.30 | 0.07 s |
| 1H-HOUSE | 60.00 (59.67) | 0 | 5 (6) | 5.71 | 0.16 s |
| 1H-POV | 60.00 (55.70) | 5 of 145 | 5 (8) | 12.37 | 0.71 s |
| 2A-HOUSE | 60.00 (59.74) | 0 | 5 (6) | 6.23 | 0.09 s |
| 2A-PTZ | 60.00 (59.90) | 0 | 5 (5) | 8.41 | 0.09 s |
| 2H-HOUSE | 60.00 (59.57) | 0 | 5 (6) | 8.30 | 0.13 s |
| 2H-PTZ | 59.99 (44.20) | 50 of 145 | 5 (15) | 8.53 | 1.14 s |

**Composer head and dropped frames:**
- **Head lag:** sheet 1 median 2.09 s (p90 2.36), sheet 2 median 1.03 s (p90 1.25). Both measure
  `wall - wall_started - head_s`.
- **ffmpeg itself** reported `dup=0 drop=0` throughout.
- **The captures dropped packets heavily,** because the composer was not reading (D2). There were 5,796
  "the composer is 600 packets behind; dropping to the next keyframe" events in the run:
  - sheet 2: 1,292 / 1,520 / 1,420 / 1,058 on 2A-HOUSE / 2A-PTZ / 2H-HOUSE / 2H-PTZ, of which 3,525 fell in
    the clean window;
  - sheet 1: 95-148 per camera.
- **Output:** 20.3 Mbit/s (sheet 1) and 11.1 Mbit/s (sheet 2) mean. Recordings were 5.8 GB and 3.3 GB for
  40 min.

**Throw latency** (publish time minus when the rest was written, by the wall clock):

| | n | p50 | p90 | max |
|---|---|---|---|---|
| Sheet 1, all | 45 | 18.2 s | 265 s | 310 s |
| Sheet 2, all | 30 | 15.3 s | 81 s | 158 s |
| Both, rests after the first 600 s | 55 | **17.0 s** | **23.8 s** | 35.8 s |
| Sheet 1 / sheet 2, after 600 s | 33 / 22 | 17.9 / 13.9 s | 22.2 / 28.5 s | |

- **The gate (p90 ≤ 20 s with five sessions) could not be applied as written.** Five sessions could not run.
  - **Two sessions in one worker already miss it, at p90 23.8 s.**
  - Sheet 2's figures also leave out up to 10 s of queueing before its cameras reached the composer (D2).
- **The first 600 s** are the late calibration and the starvation in D3 and D5:
  - sheet 1 began watching only at its 5th calibration try, ~6 min after Start, and published its backlog
    with latencies of 100-310 s;
  - meanwhile sheet 2's latency rose to 78-158 s, because the lane gave every step to sheet 1 while it
    caught up.
- **Run Q** (two sessions again, the watches starting from the CLI's saved calibrations, which both failed
  the check, D6):
  - 11 throws on sheet 2 and none on sheet 1;
  - after calibration, p50 16.3 s and **p90 28.6 s** (n 10);
  - sheet 2's cameras were also ~9.5 s behind in the composite for all of those throws (D2).
  - Container CPU 9.46 cores; composers 2.81 / 3.47.
- The latency of 13.8 s for today's YouTube path was one session on the laptop, measured the same way from
  the recording's head.

**Extrapolation to five sessions** (an estimate, not a measurement):
- About 3.3 cores per composer, 0.03 per capture, and ~1.6 per watch: **about 25 cores** of the 5950X's 32
  threads, before production's own work.
- NVENC needs about 11% of an encoder per session.
- At three watches per worker the lane serialises three sessions, so D3's latency grows.
- The gate needs D2 and D3 fixed first. The plan's optimisation order does not reach them: libndi's decode is
  already off and the watches are already spread over the workers. These are the composer's input queues
  and the lane's scheduling.

**Carried checks:**
- **(a) One capture held for ≥ 1 h:** `longcap.py`, 5H-HOUSE alone into a FIFO read and discarded,
  21:27:12-22:30:12 (63 min, run alongside K, the CLI and Q).
  - **Stability:** 377 windows of 10 s, every one at 59.49 packets/s or more, at most 6 keyframes per window,
    longest gap 0.106 s. 0.021 cores. Exit 0 on SIGTERM.
  - **RSS grew steadily and did not level off:** 61.7 MB at 30 s, 63.0 at 5 min, 66.1 at 20 min, 70.2 at
    40 min, **74.9 MB at 62.5 min**. That is ~0.2 MB a minute, ~12.6 MB an hour (D9).
  - An earlier 9-minute run (20:50-20:59) had RSS 62.9 → 63.8 MB. It was stopped because its 8 Mbit/s pushed
    the link to 86 Mbit/s and 2H-PTZ started to storm.
  - Over A's 40 min, the 8 captures' RSS grew 66-80 → 71-89 MB.
- **(b) Our receiver and a foreign reader on one camera at once:** run K, 21:25:40-21:26:45.
  - The foreign reader was Task 1's `t1/rx.py`, decode on, libavcodec counting packets through the stand-in.
    It ran on 2H-HOUSE for 60 s while our capture read it.
  - **Ours:** 59.97-60.03 packets/s, 5-6 keyframes per 10 s, queue 0-31.
  - **The foreign reader:** 3,838 packets in 63.9 s = **60.08/s**, 32 keyframes (1 per 2 s), 0.28 cores. It
    got 29.5 fps through Python, cyndilib's known limit (Task 1).
  - **Both at full rate.**
- **(c) The cost of a hold** (run K, sheet 2 alone with its watch):
  - **Capture killed** (EOF; the overlay repeats the last frame), from 480 s on:
    - composer 2.65 → **2.28 cores**;
    - output 18.8 → **12.7 Mbit/s** (one panel still);
    - head lag unchanged at 2.0 s.
  - **Camera silent, capture alive** (`Holder` repeating the last keyframe at 60/s): all four cameras were
    cut off for 30 s with `docker network disconnect`:
    - composer 2.65 → **5.64 cores**;
    - each capture 0.02 → **0.99 cores** (3.66 for four);
    - output 18.8 → 4.9 Mbit/s;
    - head lag unchanged.
    - So a hold costs about **1.7 cores per held camera**.
  - **Earlier, in L2B,** 5H-POV was silent under congestion. Sheet 5's composer took 4.0 cores against 2.7
    after, and its capture hit the 600-packet cap 27 times in ~70 s.

## Step 6: failures

**(i) A capture killed mid-recording: pass** (run K, sheet 2).
- `SIGKILL` reached 2H-PTZ's capture at 480 s.
- The manager's next `check()`, 2.2 s later, logged `camera 2H-PTZ's capture exited (-9); its picture holds its
  last frame`.
- The recording went on to 606.7 s, with the head at 1.01x and lag 2.0 s, and the watch kept stepping.
- At stop, the other captures exited 0 and the composer 255 (SIGTERM, as Task 5 said).
- **A camera unplugged mid-session: pass.** All four cameras were cut off at 21:29:33.1 and back at
  21:30:03.3.
  - Each capture logged `no packets for 5.0 s; reconnecting`, then again at 15.1 s.
  - All four logged `packets again` 0.13-0.44 s after the network returned.
  - The composite held still and kept going: no stall, head lag 2.2-2.4 s.
  - The cost was large while it lasted (see (c)).
  - Afterwards the captures' queues held 190-506 packets (up to 8.4 s behind) and took ~70 s to drain, with no
    drops (D2).

**(ii) Start without the tap: pass.** `/opt/ndi` was replaced by an empty tmpfs (`--mount type=tmpfs,destination=/opt/ndi`).
- `tap_env` puts `/opt/ndi` first on `LD_LIBRARY_PATH` whatever the caller sets, so that is the way to take
  the tap away.
- `CameraRecorder(2).start()` raised `LiveError: camera 2A-HOUSE sent no H.264 1920x1080 within 15 s` after
  **0.11 s**, and nothing was left on disk.
- Each capture logged `/opt/ndi/libavcodec-ndi.so.61 is missing: capture runs only in the worker image`, at
  INFO (D7).

**(iii) A league run ends every session: covered by the API test.**
- `tests/test_practice_api.py::TestALeagueEndsPractice::test_league_live_ends_every_session`.
- Run here together with the other league tests (`-k "league_live_ends_every_session or league_closes_practice
  or start_refused_while_a_league or poll_that_finds_no_league or league_stream_already_followed or
  league_closes_youtube"`): **6 passed**.
- It is API-side, so there is no on-box run.

## Defects

None was fixed in this task.

**D1: the composer never starts when a camera goes quiet right after its first keyframe.**
- How it happens:
  - `capture._read` passes packets to the `Holder` only once the FIFO is open (`if self.fifo.opened.is_set()`).
  - The composer opens its four FIFOs one at a time, in order house_a, house_h, long_a, long_h, probing each
    in turn.
  - A camera that sends its first keyframe (copied to `first-<role>.h264`, so the start check passes) and
    then goes quiet leaves its FIFO empty when the composer opens it. The `Holder` has no keyframe to repeat,
    so ffmpeg blocks probing that input.
  - The head stays at 0 until `STALL_S`, and the recording fails: `sheet 5's composer stalled at 0 s`.
- Seen three times:
  - L1B sheet 5: 5H-POV gave its first keyframe at 20:44:13.4, then 40 s of silence; failed at 20:44:56.
  - L2B sheet 3: 3H-PTZ; failed at 20:47:26.
  - Very likely the first look at 20:35 (sheet 2's head stayed at 0.0 for 60 s), whose log was not kept.
- Meanwhile the inputs already opened overflow their 600-packet queues. In production, Start then fails ~30 s
  after the player pressed it.
- The trigger here was the congested link (E1), but any camera that hiccups as it connects does this.
  2H-PTZ also stayed silent for 15 s after connecting at 20:43:57, as the link was filling. That time the
  composer survived, because the camera came back within `STALL_S`.
- Two fixes are possible:
  - prime the `Holder` with the first keyframe, so a quiet camera's FIFO still gets a held picture;
  - or let the composer start without the quiet input.

**D2: the composer's input queues fill, and stay full under load.**
- How it happens:
  - Each input is stamped with the wall clock when ffmpeg *reads* it.
  - Packets waiting in a capture's queue (`BACKLOG` 600, 10 s) are therefore delay that `head_s` and the
    composite's timestamps cannot see.
  - A backlog drains only as fast as the composer can decode faster than real time.
- **Measured in K** (`site-q`, the queue depth every 10 s):
  - **At start**, from the one-at-a-time probing: 424 / 302 / 185 / 83 packets at +10 s on
    house_a / house_h / long_a / long_h. That is 7.1 / 5.0 / 3.1 / 1.4 s, so the four cameras are up to 6 s
    apart in the composite. They drained by +30-40 s.
  - **After the 30 s cut:** 190-506 packets, drained in ~70 s.
- **In run A** (two composers and two watches in one container) the queues never drained.
  - There were 5,796 queue-full events: 5,290 on sheet 2, on every camera, all 40 minutes; 506 on sheet 1.
  - Each one means that camera was ≥ 10 s behind in the composite at that moment, and then dropped up to 2 s
    of frames to the next keyframe.
  - Sheet 2's head lag still read 1.0 s.
  - So the latency figures in Step 5 and the composite itself understate the real delay by up to 10 s on
    that sheet. Step 3's ±4 s on sheet 2 is the same thing, seen between cameras.
- **Reproduced in run Q** (22:30-22:40: A again, sheets 1 and 2 with two watches in one container,
  `site-q`):
  - Both sheets' startup staircases (403-424 packets on house_a at +10 s) drained by +30-40 s.
  - Sheet 1 then stayed at 0.
  - Sheet 2's four queues climbed together from 0 to ~570 packets between 22:31:50 and 22:32:50. That was
    while its watch began calibrating and watching, so the composer read ~50 packets/s a camera instead of 60
    for a minute.
  - They then stayed at the cap for the rest of the run: median 569-579 packets (~9.5 s), with 820
    queue-full events in 8 minutes. Packet rates were 60/s throughout, except 2H-PTZ (26 of 60 windows under
    59/s).
  - Sheet 2's head lag read 1.7-2.1 s the whole time.
  - So a backlog that forms after startup is not drained: at its cap, the composer reads exactly at real time
    and sheds the excess only by dropping.
- Possible fixes:
  - stamp packets by arrival in the capture (the spec's rule: time every frame by its arrival) instead of at
    ffmpeg's read;
  - bound the queue by age, not count;
  - open the inputs in parallel or with a short probe.

**D3: two sessions on one worker miss the latency gate,** at p90 23.8 s (Step 5).
- The lane takes one watch step at a time, and a step that did work returns at once, so the first sheet with
  work always goes first.
- Steps of 14-22 s were logged: calibrations, and 30 s reads with enrichment. Each blocks every other watch
  on that worker.
- After sheet 1's late calibration (D5), sheet 1 caught up on 6 minutes of backlog and sheet 2's throws waited
  78-158 s.
- With three sessions per worker it gets worse.

**D4: the lane counts a failed first step as a stream failure.**
- A camera watch with no saved calibration has `since_s` 0. Its first `step()` therefore calibrates at
  head ≈ 0, before the composer has created `rec.0.ts`.
- PyAV raised `av.error.FileNotFoundError` (L1B, L2B), which the lane counts as one of its 3 failures and backs
  off 60 s. When the file exists but holds less than two keyframes, it costs one of `CALIB_TRIES` and
  `CALIB_RETRY_S` (60 s) instead.
- Either way the first watch starts ≥ 60 s after Start.

**D5: an incomplete calibration is adopted only at the last try.**
- Sheet 1's calibrations from its own busy recording were never complete. The watch adopted the incomplete one
  only at try 5, ~6 minutes after Start, and published that backlog late.
- (The CLI calibrated sheet 1 complete at 21:41, with the sheet quieter.)

**D6: a saved calibration was rejected on a busy sheet, and replaced by a busy-sheet one.**
- At K's Start, sheet 2's saved calibration (seeded by run A's watch 40 minutes earlier) failed the ring check:
  `the top house cannot be read: could not find the four_ft ring`, with players in the house.
- The watch then calibrated from its own recording: 72 s to a complete one, 92 s to watching.
- It replaced the saved file (Ruling 12). This is the risk Ruling 13 names.
- In run Q the same happened to both of the CLI's fresh calibrations (sheets 1 and 2, made 45-50 minutes
  earlier), with the same message for the top house.
- So that is 3 rejections in 3 Starts on sheets in use. Each cost about 90 s to watching, and overwrote the
  saved calibration.

**D7 (minor): a missing tap reports the wrong cause.**
- It surfaces as `sent no H.264 1920x1080 within 15 s` after 0.11 s.
- The real cause (`/opt/ndi/libavcodec-ndi.so.61 is missing`) is logged only at INFO, by the relay.

**D9 (minor): a capture's RSS grows ~12.6 MB an hour.**
- Measured in (a): 61.7 → 74.9 MB in 63 min, linear. The 8 captures in A grew 5-11 MB in 40 min.
- Over a 3 h session (`MAX_S`) that is ~38 MB a capture, ~0.8 GB for 20. Not urgent, but it is growth, not a
  plateau.
- The `TAPCOUNT` wrapper keeps only counters, so it is not the cause.

**D8 (minor): a capture whose camera is silent did not exit within 5 s of SIGTERM.**
- In L1B, 5H-POV was killed (exit -9) by `CameraRecorder.stop()`.
- It was probably inside libndi's `connect_to` or `disconnect`.

## Cleanup

- **Every recording was deleted after measuring:**
  - run A's two, 5.8 GB and 3.3 GB;
  - L1B's, L2B's, K's and Q's;
  - each `peek` and contact-sheet image, after one look;
  - the CLI's own.
- **Nothing remains:** `find t11 -name '*.ts' -o -name '*.h264*' -o -name '*.mp4' -o -name '*.jpg' -o -name '*.png'`
  finds nothing.
- **What remains in `t11/`** is numbers and code:
  - logs, `samples.jsonl`, the published throw documents and clip sizes;
  - `rx/*.stats` (per-packet numbers);
  - `runs/ev/*.npy` (per-frame band motion, numbers);
  - calibration pickles under `calroot-*` (scratch roots, not the workers' caches).
- No `t11-*` container, capture or ffmpeg process is left, and the production containers are untouched.
- **Camera connections made by this task, for the record:**
  - 20 cameras for ~1 min (the looks);
  - sheets 1-5 for 1-5 min in L1/L2B;
  - sheets 1 and 2 for 40 min (A);
  - sheet 2 for 10 min (K);
  - sheets 1 and 2 for 10 min (Q);
  - each sheet for ~75 s (CLI);
  - 5H-HOUSE for 9 min and then 63 min;
  - a second reader on 2H-HOUSE for 64 s;
  - four 4-8 s reads of sheet 4's cameras.

# Task 11 fixes: re-measure

The fixes for D1-D4 and D6-D8 (commits `eebd07cc`..`7a9ad9a4`), measured on the Ryzen on 2026-10-24/25,
22:56-00:57 UTC, under Task 11's rules. D5 and D9 are parked (Ruling 24).

## Setup

- **Image:** `curling-worker:practice-cams`, built from `/data/wdd/scratch/practice-cams-src` after rsyncing
  the worktree. The installed `cameras/*.py`, `practice/watch.py`, `practice/pipeline.py`, `live/lane.py` and
  `ingest/frames.py` matched the branch by md5. The baseline run D0 used the Task 11 image (`fab1e4da`).
- **Containers:** throwaway `t11fix-*`, worker-like (`--gpus "device=0,1"`, `CUDA_DEVICE_ORDER=PCI_BUS_ID`,
  `CUDA_VISIBLE_DEVICES=0`), on `deploy_default`. Production and `curling-worker:local` were not touched.
- **Production was idle throughout:** a monitor read the three production containers' logs every 30 s during
  each run, and every claim got a 204.
- **Tools** are in `/data/wdd/scratch/practice-cams-work/t11fix/`:
  - Task 11's `harness.py`, with per-thread CPU sampling and `--lane turns` (live/lane.py's new rule);
  - `site/sitecustomize.py`, Task 11's TAPCOUNT wrapper, which also logs the queue's largest depth and the age
    of its oldest packet in each 10-s window;
  - `site-quiet/`, the same wrapper that, for the camera named in `T11_QUIET`, throws away every packet after
    the first keyframe;
  - `bench.py` (the composer offline, on synthetic test patterns), `startup.py`, `d8.py`, `threads.py`,
    `qstats.py`, `inputs.py`.
- **Who was on the ice:** D0 had a game on sheet 2. Q1 caught a quiet hour (one throw). A look at sheets
  1, 2, 3 and 5 at 00:20 (one frame each, viewed once and deleted) found groups on all four, so Q2 and Q3 used
  sheets 2 and 5.

## D2 part 3: why a sheet's composer fell behind

**Before (D0, Task 11's code, sheets 1 and 2 with their watches, 15 min):**
- Sheet 2's four queues were at their 600-packet cap from the start and never drained: queue p50 572-581 on all
  four cameras, 6 to 264 drop-to-keyframe events in each 2-minute window.
- Sheet 1's queues stayed near 0.
- Per thread (`/proc/<pid>/task`):
  - sheet 2's filtergraph thread `fc0` and its slice workers took **1.59-1.62 cores in every window**, flat;
  - sheet 1's took 1.35;
  - `top -H` showed sheet 2's main `fc0` thread at **86%** of a core, with its decoders at 30-40% each.
- GPU 0 was not the limit: SM mean 10.5% (max 57%), NVENC mean 21.3% (max 76%).
- So the composer was CPU-bound on one thread, the single filtergraph that crops, turns, scales and overlays all
  four cameras at 60 fps.

**Offline** (`bench.py`: sheet 2's layout, four synthetic 1080p60 H.264 streams read as fast as they come, so
the output rate is the composer's capacity; real time is 60):

| Composer | Flat out (fps) | Paced to real time | Beside 24 busy threads |
|---|---|---|---|
| As it was: one filtergraph, 2 filter and 2 decode threads | 57.6, 60.7, 58.5 | **49.8 fps** (0.83x), 2.68 cores | **44.7, 46.0** |
| `-filter_complex_threads` 4 | 73.8 | | |
| Scale before transpose | 70.7 | | |
| **A filtergraph per camera, plus one that lays them out** | **98.1, 118.3** | **60.3** (keeps up), **2.43 cores** | **69.6, 70.1** |
| The same, with 1 / 4 decode threads | 92.4 (1 filter thread) | 60.3 (1 decode), 2.62 cores | 72.4 / 67.4 |
| The same, filter threads 4 | 114.7 | 60.3 | 70.3 |
| The same, `format=yuv420p` in each camera's graph | 94.3 | 60.3, 2.74 cores | 68.6 |

- **Cause:** with every camera in one graph, FFmpeg runs it on one thread, which could only just keep 60 fps
  even alone. Any other load (the watch's decode and detector, the other sheet's composer) put it below real
  time. Since the composer reads only what its output needs, a backlog that formed never drained.
- **Fix:** each camera's crop, turn and scale is a graph of its own (FFmpeg 7's filtergraph chaining), so it runs
  on its own thread, and the thread caps are unchanged. The picture is the same: the same filters in the same
  order.

## Run Q again

Q1 used sheets 1 and 2 (25 min, 23:46-00:11). Q2 used sheets 2 and 5 (25 min, 00:20-00:46).

| | Task 11 Q (sheets 1+2) | D0 (old code, sheets 1+2) | Q1 (sheets 1+2) | Q2 (sheets 2+5) |
|---|---|---|---|---|
| Queue depth, p50 / max | sheet 2: 569-579 / 600 | sheet 2: 572-581 / 600 | 0 / 34 | 0 / 34 |
| Packet age in queue, worst per 10 s: p99 / max | not measured (≈9.5 s at the cap) | ≈10 s at the cap | ≤ 0.45 / 0.68 s | ≤ 0.70 / 0.77 s |
| Drop episodes (events) | 820 events in 8 min | 1,198 events in 15 min | 1 (4) | 14 (93) |
| Composer CPU, mean (cores) | 2.81 / 3.47 | 2.78 / 3.53 | 2.89 / 2.70 | 2.86 / 2.78 |
| Composer speed | 1.01x | 1.01x | 1.00x | 1.00x |
| Head lag p50 (s) | 1.32 / 1.90 | 2.21 / 1.52 | 0.85 / 0.70 | 0.95 / 0.71 |
| Container CPU, mean (max) | 9.46 | 9.48 (12.8) | 8.60 (10.6) | 9.41 (16.3) |
| GPU 0 SM / NVENC, mean (max) | | 10.5 (57) / 21.3 (76) % | 11.7 (50) / 25.2 (45) % | 12.6 (68) / 23.4 (34) % |
| Throws | 11 | 17 | 1 | **32** |
| Latency p50 / p90, from the head | 16.3 / **28.6** s (after calibration) | 14.7 / 30.2 s | 14.3 s (one throw) | **12.5 / 17.0 s** |
| Latency p50 / p90, by the wall clock | | 16.1 / 31.6 s | 15.0 s | **13.2 / 17.6 s** |

- Task 11's run A, the other two-session run, had p90 23.8 s.
- **Latency:** "from the head" is Task 11's measure, publish time minus when the recording's head reached the
  stone's rest. "By the wall clock" also counts the composer's head lag. Before the fix, neither counted the
  queues (up to 10 s on sheet 2); now a packet waits at most 0.5 s.
- In Q2 the two watches stepped 353 and 353 times (D3's turns). Both saved calibrations agreed at Start, so no
  watch saved or replaced one.
- **Drop episodes:** each was 0.5-0.8 s behind and caught up at once (the next packet was fresh). 13 of Q2's 14
  episodes, and all 11 of Q3's, fell within a practice clip cut: 4 s before its delivery to 1 s after.
  - A cut is FFmpeg decoding the composite and encoding with libx264 at its default threads, a few seconds over
    every core.
  - **Running the cut at `nice -n 10` did not help.** Q3 ran that, on sheets 2 and 5, and was **stopped early**
    at 8 min when the wave was closed: 11 episodes, 87 events, all 11 on a cut of 13.
  - So it is not CPU time the composers lose to a cut, or not only that. Candidates are memory bandwidth (the
    cut decodes 1080p60 flat out) and the watch's own step. **Not fixed**; the niceness change was dropped.
- Capture RSS: 62 MB → 67-72 MB over 25 min, the same growth as D9 (parked).
- Composer RSS: 635-653 MB → 671-709 MB, against 583-602 MB with one graph.

## Startup (D2 part 2)

`startup.py` starts sheet 2's recorder with ffmpeg's log at info, and times each "Input #k" line from the
composer's spawn.

| Input options | Inputs opened at (s) | First output (s) | Cameras behind once read |
|---|---|---|---|
| FFmpeg's own probe (`-f h264` only, D1's keyframe-first FIFO) | 0.43, 0.80, 1.05, 1.35 | 2.4 | 1.2 / 0.9 / 0.8 / 0.6 s: 74 / 59 / 53 / 41 packets dropped |
| `-probesize 500000 -analyzeduration 100000` | 0.12, 0.20, 0.27, 0.33 | 1.4 | 0.45 s at worst |
| **`-probesize 32 -analyzeduration 1` (kept)** | **0.08, 0.12, 0.17, 0.22** | **1.3** | **0.30 s at worst, nothing dropped** |
| `-fpsprobesize 0` | 0.09, 0.14, 0.18, 0.23 | 1.3 | 0.30 s |

- Every input still read as `h264 (Main) ... 1920x1080`.
- **At +10 s in Q1** the queue was **0 on all eight cameras**, against Task 11's 424 / 302 / 185 / 83 and 403-424
  on house_a. In the first 10 s the largest was 12-26 packets, and the oldest packet 0.19-0.39 s.

## D1: a camera quiet after its first keyframe

`site-quiet` on one camera of sheet 2, 45 s each.

| Image | Quiet camera | Result |
|---|---|---|
| Task 11's | 2H-PTZ (long_h, opened last) | `sheet 2's composer stalled at 0 s` at +30 s. The other inputs reached 600 packets |
| Fixed | 2H-PTZ | First output at 1.3 s. The recording ran all 45 s, through reconnects at 5, 15 and 35 s, with no drop |
| Fixed | 2A-HOUSE (house_a, opened first) | First output at 1.3 s. At +15 s **the recording froze**: see "A new defect" below |

## D8: SIGTERM

| Case | Seconds to exit after SIGTERM (exit code) |
|---|---|
| Sheet 2, all four cameras cut off (`netcut.sh`) for 40 s, recorder stopped 18 s into the cut | 0.007-0.016 (-15) on all four |
| Sheet 2, 2A-HOUSE's capture frozen inside libndi (below) | 0.015 (-15); the other three 0.007 |
| Q1 and Q2 stops | sheet stopped in 0.17-0.18 s, all captures -15 (0.36-0.64 s in D0) |

## A new defect: a capture can freeze for good during a reconnect

- **Seen** in the D1 run with 2A-HOUSE quiet, twice. That camera kept sending, and the wrapper hid its packets,
  so the capture reconnected at 5 s and again at 15 s.
- **At the second reconnect the capture froze** (`/proc/<pid>/task/*/wchan`):
  - libndi's `ndir:video` thread was blocked in `pipe_write`, the tap's pipe full;
  - the main thread was in a futex wait inside `receiver.disconnect()`/`connect_to()`, which cyndilib calls
    holding the GIL;
  - so the Python reader that drains the pipe could not run, and neither could the `Holder`'s ticker.
- **What it does to the recording:** the composer starved on that input and stopped. The other three cameras' queues
  grew to the 600-packet count cap, and the recording would have failed at `STALL_S`.
- **When it happens for real:** packets have to be in flight while a reconnect runs, for example a camera that
  comes back just as the capture gives up on it. Task 11's L1B, a capture SIGKILLed after more than 5 s, fits
  that.
- SIGTERM now ends such a capture at once (D8), but nothing unfreezes it.
- **Possible fixes** (not made, out of this wave's scope):
  - a tap pipe large enough for libndi's in-flight packets (`F_SETPIPE_SZ`, 1 MB);
  - or a tap that never blocks on its pipe and marks a gap, so the Holder restarts at a keyframe.

## Cleanup

- **Every recording, clip and frame was deleted:**
  - D0, Q1, Q2 and Q3's `rec*` directories and clip folders;
  - the startup, D1 and D8 recordings;
  - the four looks, after one view;
  - `bench.py`'s synthetic test-pattern inputs.
- A `find` under `t11fix/` for `*.ts`, `*.h264*`, `*.mp4`, `*.jpg`, `*.png` and FIFOs finds nothing.
- What remains is numbers and code: logs, `samples.jsonl`, throw documents, `gpu.log`, and the calibration
  pickles under the scratch roots `calroot-*`.
- No `t11fix-*` container, capture, ffmpeg or monitor process is left. The extra image tag
  `curling-worker:practice-cams-fab1e4da`, made for the D0/D1 baseline, was removed.
- **Camera connections, for the record:**
  - sheets 1 and 2 for 15 min (D0) and 25 min (Q1);
  - sheets 2 and 5 for 25 min (Q2) and 8 min (Q3);
  - each of sheets 1, 2, 3 and 5 for about 10 s (the looks, inputs.py);
  - sheet 2 for about 20-45 s, ten times (startup, D1, D8).

# Open before the YouTube path goes

The final branch review (2026-10-24) found these gates still open. The spec keeps the YouTube practice path
until they close. The user has since decided to remove that path anyway; they stay open as checks of the
camera path.

- **Task 11 Step 1: calibration on an empty sheet.** Every sheet was in play during the run, so no saved
  calibration was made from an empty sheet. Run `cameras-calibrate` at an empty-sheet time, in `worker` and then
  `worker2` (one at a time), for each sheet. Sheet 4 waits on its cameras sending H.264 (E2).
- **Task 11 Step 4: the both-ways accuracy comparison.** One real practice session, read from the cameras and
  from its YouTube practice stream at once: throws found, false throws, rest positions, lines and clips, each
  disagreement checked on the long view. Only a partial check of one camera session was made.
- **Task 11 Step 5: latency.**
  - Three watches on one worker, against the gate (p90 ≤ 20 s). Two met it after the Task 11 fixes (Q2, p90
    17.6 s by the wall clock); three were never run.
  - Five sessions, once the Ryzen's link is fixed (E1: `/sys/class/net/enp4s0/speed` reads 1000) and
    `PRACTICE_MAX_CAMERA_WATCHES` is raised to 5.
- **The throw timing checks step 2 left for a busy session.** Step 2 (`3887782a`..`4e600aef`, Clocks in the
  spec) made every camera frame carry its real time. Its runs were on empty sheets, so these wait:
  - hog-line times and splits on real throws;
  - inter-camera lag on real throws, against Step 3's median of +0.40 s;
  - drift over an hour or more;
  - whether the composite's ~1 s grey lead-in upsets the saved-calibration check on a busy sheet.
