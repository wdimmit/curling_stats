# Cameras by default, phase 1: results of the offline gate (2026-11-14)

Branch `camera-direct` at cb906e03. Run on the Ryzen at 14:40-14:45 PST, with nothing recording and load 0.
- **Container:** a throwaway `curling-worker:local` with `--cpus 4`, the branch's `src` mounted, and the caches
  mounted read-only.
- **Script:** `cut_sample.py` (scratch `/data/wdd/scratch/camera-direct/p1-clips/`) does what the clips service does.
  It builds the job's feed with `gameclips.camera_feed_spec` from the timeline, then calls `find_source` and
  `clip_rocks` with the real `clipburn.cut`.

## 1. From a camera game's YouTube copy (the composite)

- **Game:** 11/10 Tuesday Supper sheet 4, `47svJJgf804` (run `r_03Cd78KraRUbGcC5n`), cut from
  `curling-cache-2/videos/47svJJgf804.mp4`.
- **Source:** `find_source` returned `composite` on sheet 4.
- **Rocks:** 0.1.1, 0.1.2 (to the bottom house) and 0.2.1, 0.2.2 (to the top). All clipped, burned, style 2.
- **Frames:**
  - Each clip opens on the long camera facing the thrower: the A end for end 1, the H end with the scoreboard for
    end 2.
  - Each clip ends on the overhead camera of the target house, upright, with the aim line and broom drawn on the ice.
  - The broken clips cut 11/11 for the same game showed a ceiling corner upside down, and the left camera for the
    right.

## 2. From a camera game's own files

- **Game:** 11/05 Thursday Morning sheet 3 (`league-1105/s3-1`, recorded with the product recorder for the phase 3
  shadow test), with its phase 3 Gate A replay timeline (`p3-gate/s3/timeline.json`). That timeline has view-local
  calibration and the clock `offset_s -908.1566`, aligned to `dbV9wuA45Z0`.
- **Setup:** a kept directory was stood up as `p1-clips/cams/kept/dbV9wuA45Z0/`, holding symlinks to the four `.ts`
  files and a `clock.json` with that clock.
- **Source:** `find_source` returned `cameras`, ahead of the YouTube copy in the cache.
- **Rocks:** 0.1.1, 0.1.2 (bottom), 0.2.1, 0.2.2 (top). All clipped.
- **Frames:**
  - The pieces come from the right cameras: `long_a` / `long_h` facing the thrower, then `house_a` / `house_h` from
    overhead, upright.
  - A one-frame-a-second strip of 0.2.1 shows the slide about 3 s in. The pull-back then follows the stone and its
    sweepers down the sheet, so the clock offset puts the rock where the timeline says.

## Verdict

Both sources PASS. The camera-file path will be seen again on the first camera night after the deploy, on a real
kept directory (trim, `clock.json`, `kept_at`).

Not covered offline:
- the not-ready hand-back on a real trim (unit-tested only);
- a live camera directory found by its `video_id` marker (unit-tested only).
