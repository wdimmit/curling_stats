# spin — the stone's handle, seen by the long camera facing the thrower

For reading how fast a thrown stone turns. The camera is hogtime's: the one at
the far end, looking back at the thrower (`CAMERA_FOR[OTHER_HOUSE[end.house]]`).
The stone comes towards it the whole way, from ~35 px wide at the hack to ~95 px
near the bottom of the frame (~26 m past the throwing tee).

The spike that led here (2026-10-10, PHbZ3EKOhMI) found the rotation plain to
see from about 8 m past the tee (the hand is off by then) to the frame's bottom:
7-14 s of travel, typically 0.13-0.25 turns a second. Seen from nearly level,
the handle lying sideways alternates grip-left and grip-right with one end-on
view between, so two sideways views are half a turn apart. Colour and
self-similarity heuristics read only about a quarter of the rocks with
confidence; this set is for a small model that finds the handle instead.

**Labelling rule: two points per frame.**
- **neck** — where the handle comes up out of the top of the stone. It is the
  pivot: it stays put while the stone turns.
- **grip end** — the free end of the handle, the tip the hand holds. It swings
  round.

When the handle points at or away from the camera the two are close together;
both are still placed. A frame where a person cannot tell one end from the other
is flagged `unclear`; one where a broom, foot or hand covers the handle is
flagged `occluded`. Both flags are labels too: they say what not to learn from.

Rotation direction is not in these labels: the curl the pipeline already
publishes (`line.curl`) gives it.

Frames come from `scripts/spin/extract.py`, which follows each rock with the side
model (ds13c) at 30 fps. `scripts/spin/wave.py` takes two frames per rock from
different stretches of the path (8-14, 14-20, 20-27 m past the tee), only past
the hand and inside the frame, and drops frames whose box is off the stone's own
width-for-row line. Each frame comes with a one-second animation of its
neighbours, which is how the two ends tell apart when the stone is small.

Coordinates are the crop's native pixels. `manifest-<wave>.json` gives each
crop's origin in the side view (`origin`, view pixels), the stone's box in view
pixels, the video and the time, so the crop can be cut again from the video.

## Where things are

| what | where |
| --- | --- |
| images, animations, `items.json`, the page | `~/curling-work/spin/<wave>/` (not committed) |
| every saved sitting | `datasets/spin/edits/` — **commit each one as soon as it is saved** |
| frame manifests | `datasets/spin/manifest-<wave>.json` |
| per-rock pickles (extract.py) | the 3070 laptop, `/data/wdd/scratch/spin/out/<video>/` |

## Commands

```sh
# on a GPU box with the videos cached, one game:
docker run --rm --gpus all -v $PWD:/w -v /data/wdd/curling-cache/videos:/v:ro \
  --entrypoint python curling-worker:local /w/extract.py \
  /w/<slug>.json /v/<video>.mp4 /w/out/<video> 2,3,4

# a wave from the pickles:
./.venv/bin/python scripts/spin/wave.py --out ~/curling-work/spin/wave1 \
  --manifest datasets/spin/manifest-wave1.json --scope spin:wave1 \
  --game <video>:<slug>:<pickle dir> [--game ...]

# label it:
./.venv/bin/python scripts/spin/serve.py ~/curling-work/spin/wave1   # http://127.0.0.1:8780/

# after every sitting:
cp ~/curling-work/spin/wave1/edits/*.json datasets/spin/edits/ && git add datasets/spin/edits && git commit
```

Each sitting saves itself to `edits/<scope>-<sitting>.json` as frames are
finished, and the browser keeps the same work in localStorage. Serve over HTTP,
never `file://`.
