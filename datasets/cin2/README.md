# cin2 — Cincinnati's end cameras

100 frames from Cincinnati Curling Club's two side cameras on `rKiMmtVod0I`, cut to the band
`sidepool.band_crop` gives each view, the rows the side model reads. Each camera sits above its own
hog line and looks back at its own house (`geometry/endcam.py`), so it watches throwers deliver from
its end.

Cincinnati's throwers hold the stone almost to the hog line, which is the bottom of the frame.
ds13e learned Granite's distant long cameras and loses the stone under the hand: the first
end-camera run (`out2`) timed the hog line on 14 of ~120 throws.

- `scripts/cin2/build.py`:
  - reads each placed throw's delivering camera from 1 s before `t_release_s` to 6 s after, with
    ds13e at 0.05;
  - takes the deepest track and one frame from it:
    - `lost` (32): the track ends short of the hog line. The frame sits in the gap before the stone
      would reach the line at its last speed, alternately halfway through the gap and 0.1 s before
      the line;
    - `seen` (68): the track reaches the line. The frame shows the stone a metre short of it.
  - Of 120 throws, 104 gave a frame: 32 lost, 72 seen, cut to 100 evenly over the game. 47 frames
    come from the red camera (`left`), 53 from the blue (`right`).
- At 0.05, ds13e keeps far more of a throw than the game run's crossings did. A box at 0.25 is
  missing from most `seen` frames too, so they are hard frames as well.
- A few frames hold no stone: the deepest "track" there was a shoe or a broom. They stay as
  negatives.
- Pre-labels: ds13e at 0.1, kept to boxes of a stone's width at their row (`sidemodel.WIDTH_TOL`).
  That gives 78 boxes on 66 frames, against 51 on 48 at 0.25.

The set is on the GPU laptop at `/data/wdd/scratch/cincinnati/cin2/set1`. The review is served from
`http://100.80.66.81:8790/` (scope `cin2:train`), rendered with the ds19 branch's review page,
which has the reject-all button.

## Reviewed and trained (2026-10-27)

- **Review** (`edits/`): all 100 frames; 35 rejects; 48 stones added (29 red, 19 yellow). Added boxes are sized by
  their row from `add_box`: 0.255 x 0.20 m at the calibrated lateral scale.
- **Training folder** (`scripts/cin2/make_tree.py`): 91 boxes; 12 frames kept empty; every fifth frame goes to val
  (80 train, 20 val).
- **cin2a:** ds13e's recipe and data (wave2, hack1, broom1, kink1) plus cin2, on the laptop. It stopped at epoch 124.
  Weights: `/data/wdd/curling/runs/cin2a/weights/best.pt` (md5 dfda314e).

| val, mAP50-95 | wave2 | hack1 | broom1 | kink1 | cin2 |
|---|---|---|---|---|---|
| ds13e | .752 | .650 | .792 | .797 | .435 (R .64) |
| cin2a | .739 | .678 | .855 | .809 | .606 (R .94) |

**The game rerun** (`out3`, cin1a overhead + cin2a side; `compare_runs.py`):
- **Hog-line times:** 112 of 128 throws, up from 14.
  - Release to hog line: 2.8-4.9 s.
  - Speed at the hog line: 1.5-2.9 m/s.
- **Caveat:** the frames were taken from this same game, and no other Cincinnati game has been tested.
- **Unchanged:** scores (2/7 exact), far-hog times, splits and lines, all still 0.
