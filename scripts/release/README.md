# The release model's training kit

`weights/release1.pt` -- the hand-on/hand-off classifier the release pass
reads the release point with (`detect/releasemodel.py`,
`game/releasetime.py`) -- is made from the hand marks in
`datasets/releasemarks` by two scripts:

- `cut.py` cuts every marked rock's crops **through the production crop
  code**: the rock's span of the long camera facing the thrower decoded by
  `longview.decode` at 30 fps under `longview.keeping(LaneKeeper(...))`, then
  `releasetime.hand_crops` along the timeline's delivery path. One
  `<tag>.npz` per game; the layout is in `cut.py`'s docstring.
- `train.py` is the spike's trainer (`~/curling-work/body-spike/scripts/hand_train.py
  --mode stack --scale 2`: 5 epochs, AdamW 3e-4, one-cycle, batch 128, shift /
  mirror / gain-and-bias augmentation) on the production pieces: stacks picked
  as `releasetime.stacks` picks them, `releasemodel.build`'s network started
  from ImageNet, `releasemodel.prepare` as the one input transform, and each
  held-out rock read by `releasemodel.p_off` and `releasetime.release_at`. A
  fold's score is the pipeline's on that game.

Both are importable (`tests/test_release_scripts.py` loads them by path).

## Running them

On the GPU box, in the worker image with the repo mounted (here at `/w`), the
videos as `<video_id>.mp4` and the timelines the marks were made on as
`<source_id>.json`:

    docker run --rm --gpus all --shm-size 8g --entrypoint "" --user "$(id -u):$(id -g)" \
        -v "$REPO":/w -v /data/wdd/scratch:/s -e PYTHONPATH=/w/src -w /w \
        curling-worker:local python -u scripts/release/cut.py datasets/releasemarks/s_*.json \
        --timelines /s/body/tl --videos /s/body/videos --out /s/release/crops

    ... python -u scripts/release/train.py /s/release/crops/*.npz --out /s/release/run1 \
        --init /s/body/weights/resnet18-f37072fd.pth \
        --folds "SL1+SL2+SL3+SL4+SL5,SL1,SL2,SL3,SL4,SL5,T1020" --save /s/release/release1.pt

`--init` is torchvision's ImageNet ResNet-18 `state_dict`
(`resnet18-f37072fd.pth`, the file the spike started from); every weight but
the final layer is taken from it. Each game is named by its marks file's `tag`
(`T1020` for the 10/20 game, which has none). A fold is games joined by `+`,
held out together; with no `--folds`, every game is held out alone.

Each fold's model is scored as it will be served: written as the float16
weights file (`fold-<fold>.pt` in `--out`) and read back by
`releasemodel._load`, so on CUDA in half precision.

`train.py` prints each fold's median |error| along the path, the share of the
rocks read within 0.3 / 0.5 / 1.0 m, the count over 1 m, the red and yellow
medians, and the share of every rock marked off that is read within 0.5 m (a
rock read as no release is a miss there). Each comes as two lines: the rocks
`chosen` at random alone -- the fair sample; the "hard" ones were picked
because a pose read looked wrong -- and every rock. Then the same for the
single-game folds together and for every fold. It writes every held-out rock
(fold, id, game, colour, `chosen`, mark, `mark_yp`, `pred_yp`, `err`, the
per-frame `p`) to `results.json` in `--out`. The spike got 83% within 0.5 m
on Super League games one at a time on its own crops; well short of that on
these, stop and look before saving.

Every rock in an npz carries where it came from (`source_id`, `video_id`,
`camera`) and the crop geometry it was cut with (releasetime's `CROP_PX`,
`CROP_ACROSS_M`, `CROP_UP_M`, `CROP_DOWN_M`, `FROM_M`, `STACK_S`, and the
label margin). `train.py` refuses crops whose geometry releasetime no longer
has: cut them again.

`--save` trains once more on every rock and writes the float16 `state_dict`
`releasemodel._load` reads; copy it to `weights/release1.pt`. Keep the crops
on the training box (`/data/curling/release/` on curling@100.85.240.114): the
recordings they came from expire after 21 days.

## The labels

Per crop, 0 before the mark's `o` (hand on), 1 from it (off), -1 within two
frames of it (2/30 s, either side), left out of training. A `held` rock is 0
throughout; a can't-tell rock (`mark: null`) is left out.

The margin also covers a difference in clocks. `mark_release.py` labelled its
frames `t0 + i/30` from the start of its decode, while ffmpeg's first frame is
the first source frame at or after `t0`, and the `LaneKeeper` keys every frame
by that true source time. So a mark's `o` can sit up to one frame before the
frame the person marked. The margin swallows it: the two frames before the
marked frame and at least one after it are left out, and no frame lands on
the wrong side of the mark.

## Checking a cut by eye

The crops are not the marking tool's picture (160 px/m grey, not 400 px/m
colour), but the hand at the mark should be the same: take a rock's first
crop at or after its `mark_o` from the npz and put it beside the marking
frame's centre (for the 10/20 game,
`~/curling-work/body-spike/mark/s_1TH0/<id>_<frame>.jpg`, `frame` from the
marks file).
