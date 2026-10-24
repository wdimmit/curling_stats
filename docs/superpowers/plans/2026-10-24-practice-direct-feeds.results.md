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
