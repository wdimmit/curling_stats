# Target broom, phases 3–5: train the broom model, attach a broom to each shot, draw it

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Train a single-class broom-head detector on the destination-facing
long camera, attach the skip's target broom to every shot in the timeline, and
draw it on the viewer's house beside the stone it asked for.

**Architecture:**
- **Phase 3.** A YOLO tree is built from the labelled waves. v0 trains on VXU9,
  on the worker's GPU. v0 pre-labels AEqL and hOKZ, and the user corrects
  them. The pass is then evaluated held-out on hOKZ, and the shipped
  `weights/broom1.pt` trains on all three games.
- **Phase 4.** `detect/broommodel.BroomFinder` reads the crop the labels were
  made on. `game/broomtime` picks the pad held still in `[t_tee − 1, t_tee]`
  and attaches `Shot.target_broom`. `timeline.build_end` publishes it.
- **Phase 5.** `core/house.mjs broomMark` and `House.jsx <Broom>` draw a pad
  glyph and a dashed link to the delivered stone.

**Tech Stack:** Python 3, ultralytics YOLO11s, numpy, OpenCV, ffmpeg, React
(esbuild), node for the core tests, pytest.

**Spec:** `docs/superpowers/specs/2026-09-23-target-broom-design.md`, Phases
3–5 and the Phase 0 findings. The follow-on plan is
`2026-09-23-target-broom-phases-1-2.md` (done). Round-1 labels are in
`datasets/broom/edits/`: train from `wave1b`.

## Global Constraints

- **The detection cache must not be invalidated.** Do not edit
  `detect/rocks.py`, `detect/yolo.py` or `geometry/calibrate.py`.
- **The pass attaches data only.** It never adds, drops or renumbers a shot. A
  timeline built with the broom model must equal one built without it, except
  for `target_broom`, `schema_version` and `processing_version`.
- **Anything the viewer can't draw is `null`, never a guess.** That covers a
  missing broom, an old timeline, no lateral calibration and no model.
- **House axes:** +y up-sheet toward the thrower, +x the thrower's right, which
  is image right in this camera. A pad's ground point is its box's
  **bottom-centre**.
- **Crop:** full view width, rows `tee_row − 130 … row_for(6.401) + 15`
  (`harvest.crop_rows`). Training and inference must use the same crop.
- **Window:** `[t_tee − 1.0, t_tee]` (user's decision), with `t_tee` from
  `thinking.tee_crossing`, observed or estimated.
- **No `PIPELINE_VERSION` bump.** `processing_version` gains `+broom-<id>`
  when the broom model is present, as the side model does. A bump on shared
  `main` would stale every hosted timeline at the next deploy without adding
  brooms there. `timeline.SCHEMA_VERSION` goes 4 → 5.
- **Shipping is a separate go.** That means the Dockerfile, the worker and API
  deploys, and the requeue. This plan ends at local verification.
- **Frames and runs never go in `/tmp`.** Trees are
  `~/curling-work/broom/tree-*`, and on the worker
  `/data/wdd/curling/broom_trees/<name>`, with runs in
  `/data/wdd/curling/broom_runs/<name>`. Commit every labelling session to
  `datasets/broom/edits/` as soon as it is saved.
- **Never build a tree with `--drop-unreviewed`.** Boxed frames are
  human-placed, but almost none are marked reviewed.
- **Test runs:** the full suite OOMs here, so run the named files. Stage only
  this plan's paths. Commits follow `area: sentence` and end with
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

- **A timeline from before schema 5 has no `target_broom` key.** The viewer
  must draw nothing and not throw. The Python side must not assume the key
  either (Task 6 tests).
- **Two pads down, the skip's and another held still near the sideline** (VXU9
  e5 s4). The tie-break goes to the one nearest the tee (Task 4 test).
- **A pad that lifts partway through the window** (a clock offset between the
  camera and the panel, up to ~0.9 s). A cluster seen in under half the frames
  gives no marker, never a moving average (Task 4 test).
- **A view that failed lateral calibration**, or no side views at all. The pass
  is a no-op there, and `analyze` must not raise (Task 5 tests).
- **A broom beyond the full house view** (a guard call near the hog line;
  `houseViewBox` stops at y = 6.0). It is drawn, and simply falls outside the
  box. Nothing is clamped to the ice (checked in Task 6).

---

### Task 1: build a training tree from labelled waves

**Files:**
- Create: `scripts/broom/make_tree.py`
- Test: `tests/test_broom_tree.py`

**Interfaces:**
- Produces: `make_tree.labelled(items, boxes) -> list[(item, rows)]`,
  `make_tree.role_for(row, role, val_fraction) -> "train" | "val"`, and
  `make_tree.write_tree(out, entries, names=("broom_head",))`.
- Tree layout:
  - `images/{train,val}/<stem>.jpg`;
  - `labels/{train,val}/<stem>.txt` as `0 cx cy w h`, empty for a reviewed
    negative;
  - `data.yaml` (`path`, `train`, `val`, `names`).

- [ ] **Step 1: Write the failing tests**

```python
"""scripts/broom/make_tree.py: labelled waves become a YOLO tree."""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "broom_tree", Path(__file__).parents[1] / "scripts" / "broom" / "make_tree.py")
tree = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tree)

ITEMS = [{"stem": "a", "image": "images/a.jpg"}, {"stem": "b", "image": "images/b.jpg"},
         {"stem": "c", "image": "images/c.jpg"}]


class TestLabelled:
    def test_a_frame_nobody_touched_is_left_out(self):
        got = tree.labelled(ITEMS, {"a": [[0, .5, .5, .1, .1]], "b": []})
        assert [it["stem"] for it, _ in got] == ["a", "b"]

    def test_a_frame_restated_to_nothing_is_a_negative_not_a_gap(self):
        got = dict((it["stem"], rows) for it, rows in
                   tree.labelled(ITEMS, {"b": []}))
        assert got == {"b": []}


class TestRoleFor:
    def test_both_frames_of_one_shot_go_the_same_way(self):
        a = {"video_id": "v", "end": 3, "shot": 7, "offset": -1.0}
        b = {**a, "offset": -0.3}
        for frac in (0.1, 0.5, 0.9):
            assert tree.role_for(a, "split", frac) == tree.role_for(b, "split", frac)

    def test_a_split_sends_roughly_the_fraction_asked_to_val(self):
        rows = [{"video_id": "v", "end": e, "shot": s} for e in range(1, 9)
                for s in range(1, 17)]
        val = sum(tree.role_for(r, "split", 0.15) == "val" for r in rows)
        assert 8 <= val <= 32                       # 15% of 128 is 19

    def test_an_explicit_role_wins(self):
        r = {"video_id": "v", "end": 1, "shot": 1}
        assert tree.role_for(r, "train", 0.5) == "train"
        assert tree.role_for(r, "val", 0.5) == "val"


class TestWriteTree:
    def test_it_writes_images_labels_and_yaml(self, tmp_path):
        src = tmp_path / "wave"
        (src / "images").mkdir(parents=True)
        (src / "images" / "a.jpg").write_bytes(b"jpg")
        (src / "images" / "b.jpg").write_bytes(b"jpg")
        out = tmp_path / "tree"
        tree.write_tree(out, [(src / "images/a.jpg", "a", [[0, .5, .25, .1, .05]], "train"),
                              (src / "images/b.jpg", "b", [], "val")])
        assert (out / "labels/train/a.txt").read_text().split() == \
            ["0", "0.500000", "0.250000", "0.100000", "0.050000"]
        assert (out / "labels/val/b.txt").read_text() == ""
        assert (out / "images/val/b.jpg").exists()
        y = (out / "data.yaml").read_text()
        assert f"path: {out}" in y and "0: broom_head" in y
```

- [ ] **Step 2: Run to see it fail**

Run: `./.venv/bin/pytest tests/test_broom_tree.py -q`
Expected: collection error, because `scripts/broom/make_tree.py` doesn't exist.

- [ ] **Step 3: Implement `scripts/broom/make_tree.py`**

```python
"""Build a YOLO tree from labelled broom waves.

Each --wave names a role, a wave directory (images/ + items.json), that wave's
manifest and a glob of its saved sessions. Sessions are merged with
`labels.merge_edits`, so a later sitting restates a frame an earlier one
boxed. A frame no session names is left out. A frame restated to no boxes is
a reviewed negative and is kept, with an empty label file.

    ./.venv/bin/python scripts/broom/make_tree.py --out ~/curling-work/broom/tree-v0 \\
        --wave split ~/curling-work/broom/wave1b datasets/broom/manifest-wave1b.json \\
                     'datasets/broom/edits/broom-wave1b-*.json' --val-fraction 0.15

Role `split` sends whole shots (both frames) to val by a stable hash, so two
looks at one placement never straddle train and val.
"""
import argparse
import glob
import hashlib
import json
import shutil
import sys
from pathlib import Path


def labelled(items, boxes: dict):
    return [(it, boxes[it["stem"]]) for it in items if it["stem"] in boxes]


def role_for(row: dict, role: str, val_fraction: float) -> str:
    if role in ("train", "val"):
        return role
    key = f"{row['video_id']}:{row['end']}:{row['shot']}".encode()
    u = int(hashlib.sha256(key).hexdigest()[:8], 16) / 0xFFFFFFFF
    return "val" if u < val_fraction else "train"


def write_tree(out, entries, names=("broom_head",)):
    out = Path(out)
    for split in ("train", "val"):
        (out / "images" / split).mkdir(parents=True, exist_ok=True)
        (out / "labels" / split).mkdir(parents=True, exist_ok=True)
    for image, stem, rows, split in entries:
        shutil.copy(image, out / "images" / split / f"{stem}.jpg")
        (out / "labels" / split / f"{stem}.txt").write_text("".join(
            f"{int(r[0])} {r[1]:.6f} {r[2]:.6f} {r[3]:.6f} {r[4]:.6f}\n" for r in rows))
    (out / "data.yaml").write_text(
        f"path: {out}\ntrain: images/train\nval: images/val\nnames:\n"
        + "".join(f"  {i}: {n}\n" for i, n in enumerate(names)))


def main() -> int:
    from curling_score.train import labels

    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--wave", nargs=4, action="append", required=True,
                    metavar=("ROLE", "DIR", "MANIFEST", "EDITS_GLOB"))
    ap.add_argument("--val-fraction", type=float, default=0.15)
    args = ap.parse_args()

    entries, tally = [], {"train": [0, 0], "val": [0, 0]}
    for role, wdir, manifest, pattern in args.wave:
        wdir = Path(wdir).expanduser()
        files = sorted(glob.glob(pattern))
        if not files:
            raise SystemExit(f"no sessions match {pattern}")
        edits = labels.merge_edits(*(json.loads(Path(f).read_text()) for f in files))
        rows = {r["stem"]: r for r in json.loads(Path(manifest).read_text())}
        items = json.loads((wdir / "items.json").read_text())
        for it, boxes in labelled(items, edits.boxes):
            split = role_for(rows[it["stem"]], role, args.val_fraction)
            entries.append((wdir / it["image"], it["stem"], boxes, split))
            tally[split][0] += 1
            tally[split][1] += len(boxes)
    write_tree(Path(args.out).expanduser(), entries)
    for split, (n, b) in tally.items():
        print(f"{split}: {n} frames, {b} boxes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the tests, then build tree-v0**

Run: `./.venv/bin/pytest tests/test_broom_tree.py -q`. Expected: pass.

Then run the command from the docstring with `--out ~/curling-work/broom/tree-v0`.
Expected: about 188 train and about 32 val frames, 231 boxes in total. The two
e3 s16 frames are absent.

- [ ] **Step 5: Commit**

```bash
git add scripts/broom/make_tree.py tests/test_broom_tree.py
git commit -m "broom: build a YOLO tree from labelled waves, whole shots kept together

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: train v0 on the worker

**Files:**
- Create: `scripts/broom/train_on_worker.sh`

- [ ] **Step 1: Write the script**

Copy `scripts/ds13/train_on_worker.sh`, with these changes:
- `REMOTE=/data/wdd/curling/broom_trees/"$NAME"`;
- `project='/data/wdd/curling/broom_runs'`;
- log `/tmp/broom_train_$NAME.log` on the worker (a log, not data);
- the echoed weights path is `/data/wdd/curling/broom_runs/$NAME/weights/best.pt`.

The training arguments are identical to ds13b's: yolo11s, imgsz 800, patience
60, batch 8, seed 0, mosaic 1.0, close_mosaic 30, scale 0.4, degrees 0, fliplr
0.5, flipud 0, hsv 0.015/0.6/0.4. The header comment explains that brooms reuse
ds13b's recipe because it is the same camera at the same scale.

- [ ] **Step 2: Start v0 and commit the script**

```bash
chmod +x scripts/broom/train_on_worker.sh
scripts/broom/train_on_worker.sh ~/curling-work/broom/tree-v0 v0 300
git add scripts/broom/train_on_worker.sh
git commit -m "broom: train on the worker with ds13b's recipe

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

Expected: `data.yaml` is echoed with the remote path, and training starts in
the background. Poll with `ssh administrator@10.0.0.182 "tail -3
/tmp/broom_train_v0.log"`. Don't wait on it: go straight on to Task 3.

When it finishes, copy the weights:

```bash
mkdir -p ~/curling-work/broom/weights
scp administrator@10.0.0.182:/data/wdd/curling/broom_runs/v0/weights/best.pt \
    ~/curling-work/broom/weights/v0.pt
```

Record the final val mAP50 from the log in the ledger.

---

### Task 3: `detect/broommodel.py`, the model on the crop it was trained on

**Files:**
- Create: `src/curling_score/detect/broommodel.py`
- Modify: `scripts/broom/harvest.py`. `crop_rows` moves to the library;
  harvest re-exports it, so `harvest.crop_rows` still works.
- Test: `tests/test_broommodel.py`

**Interfaces:**
- Produces:
  - `broommodel.crop_rows(view) -> (top, bot)`;
  - `broommodel.ABOVE_TEE_ROWS`, `PAST_Y_M`, `BELOW_PAD`;
  - `broommodel.Pad(col, row, conf, x0, y0, x1, y1)`, frozen, in view pixels,
    where `col, row` is the box's bottom-centre;
  - `broommodel.find(model, frames, view, conf=CONF_MIN) -> list[list[Pad]]`,
    one list per frame;
  - `broommodel.CONF_MIN = 0.25`;
  - `broommodel.default_model() -> model | None`, the YOLO from
    `weights.broom_path()`, loaded lazily and cached.

- [ ] **Step 1: Write the failing tests** (fake model; no GPU or ultralytics)

```python
"""The broom detector reads the crop the labels were made on."""
from types import SimpleNamespace

import numpy as np
import pytest

from curling_score.detect import broommodel
from curling_score.geometry.sideview import SideView

VIEW = SideView(rect=(0, 0, 810, 1080), tee_row=430.0, hog_row=520.0,
                centre_col=390.0, lat_px_per_m_at_tee=148.0)


class FakeModel:
    """Returns one box per crop, in CROP pixels, and records what it was fed."""
    def __init__(self, box=(380.0, 100.0, 410.0, 125.0), conf=0.9):
        self.box, self.conf, self.fed = box, conf, []

    def predict(self, crops, imgsz, conf, verbose):
        self.fed.append((len(crops), crops[0].shape, imgsz, conf))
        t = lambda a: SimpleNamespace(cpu=lambda: SimpleNamespace(numpy=lambda: np.array(a)))
        boxes = SimpleNamespace(xyxy=t([self.box]), conf=t([self.conf]), cls=t([0]))
        return [SimpleNamespace(boxes=boxes) for _ in crops]


class TestCropRows:
    def test_it_runs_from_the_skip_s_legs_to_past_the_hog_line(self):
        top, bot = broommodel.crop_rows(VIEW)
        assert top == 300 and bot > VIEW.hog_row


class TestFind:
    def test_boxes_come_back_in_view_rows_at_their_foot(self):
        frames = [np.zeros((1080, 810, 3), np.uint8)] * 3
        got = broommodel.find(FakeModel(), frames, VIEW)
        top, _ = broommodel.crop_rows(VIEW)
        assert len(got) == 3
        p = got[0][0]
        assert (p.col, p.row) == (395.0, 125.0 + top)     # bottom-centre, lifted
        assert p.conf == pytest.approx(0.9)

    def test_the_model_sees_the_crop_in_bgr_at_800(self):
        m = FakeModel()
        frame = np.zeros((1080, 810, 3), np.uint8)
        frame[..., 0] = 200                                 # red in RGB
        broommodel.find(m, [frame], VIEW)
        n, shape, imgsz, _ = m.fed[0]
        top, bot = broommodel.crop_rows(VIEW)
        assert shape == (bot - top, 810, 3) and imgsz == 800

    def test_a_weak_box_is_dropped(self):
        got = broommodel.find(FakeModel(conf=0.1), [np.zeros((1080, 810, 3), np.uint8)], VIEW)
        assert got == [[]]

    def test_no_frames_no_calls(self):
        m = FakeModel()
        assert broommodel.find(m, [], VIEW) == [] and m.fed == []
```

- [ ] **Step 2: Run to see it fail**

Run: `./.venv/bin/pytest tests/test_broommodel.py -q`
Expected: `ModuleNotFoundError: curling_score.detect.broommodel`.

- [ ] **Step 3: Implement**

```python
"""Find broom heads on the ice in the destination-facing long camera.

The model was trained on crops of full view width from the skip's legs down
past the hog line (datasets/broom/README.md), so it is shown exactly that
crop: `crop_rows` is the one definition, shared with the harvest that cut the
labels. Boxes come back as their bottom-centre in VIEW pixels, because a pad
lies on the ice and `SideView.to_house` maps a point on the ice.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass

import numpy as np

from curling_score.geometry import constants as C

ABOVE_TEE_ROWS = 130            # the skip's legs and the shaft, above the house
# Down to the hog line, and a margin. A skip calling a guard crouches in front
# of the house: on VXU9 e6 s6 the pad sat ~4 m up-sheet, below a crop that
# stopped at 3 m (wave 1, 2026-09-23).
PAST_Y_M, BELOW_PAD = C.TEE_TO_HOGLINE_M, 15
# Below this the model is guessing. The pass wants a pad held still across
# half the window, so one weak false box cannot become a marker on its own;
# the floor only keeps the arrays small.
CONF_MIN = 0.25
BATCH = 16                      # crops per predict call, as sidemodel


@dataclass(frozen=True)
class Pad:
    col: float
    row: float
    conf: float
    x0: float
    y0: float
    x1: float
    y1: float


def crop_rows(view) -> tuple[int, int]:
    """The rows cut: the skip's legs and the shaft above the house, down past
    the hog line; clipped to the frame."""
    top = max(0, int(view.tee_row - ABOVE_TEE_ROWS))
    bot = min(view.rect[3], int(view.row_for(PAST_Y_M) + BELOW_PAD))
    return top, bot


def find(model, frames, view, conf: float = CONF_MIN) -> list[list[Pad]]:
    """Every pad the model finds, per frame, in view pixels."""
    top, bot = crop_rows(view)
    x, _y, w, _h = view.rect
    crops = []
    for frame in frames:
        arr = np.asarray(frame)
        win = arr if arr.shape[1] == w else arr[:, x:x + w]
        crops.append(np.ascontiguousarray(win[top:bot, :, ::-1]))   # RGB -> BGR
    out = []
    for i in range(0, len(crops), BATCH):
        for res in model.predict(crops[i:i + BATCH], imgsz=800, conf=conf,
                                 verbose=False):
            pads = []
            if res.boxes is not None:
                for b, cf in zip(res.boxes.xyxy.cpu().numpy(),
                                 res.boxes.conf.cpu().numpy()):
                    if float(cf) < conf:
                        continue
                    x0, y0, x1, y1 = (float(v) for v in b)
                    pads.append(Pad((x0 + x1) / 2, y1 + top, float(cf),
                                    x0, y0 + top, x1, y1 + top))
            out.append(pads)
    return out


@functools.lru_cache(maxsize=2)
def _load(path: str):
    from ultralytics import YOLO

    return YOLO(path)


def default_model():
    """The broom detector, or None when none is configured: no brooms, not an error."""
    from curling_score import weights as weights_mod

    path = weights_mod.broom_path()
    return None if path is None else _load(str(path))
```

In `scripts/broom/harvest.py`:
- replace the local `ABOVE_TEE_ROWS`, `PAST_Y_M`, `BELOW_PAD` and `crop_rows`
  definitions with
  `from curling_score.detect.broommodel import ABOVE_TEE_ROWS, crop_rows  # noqa: F401`;
- drop the now-unused `constants` import if nothing else uses it.

`default_model` needs `weights.broom_path`, which Task 5 adds. Add it now if
this task runs first:

```python
BROOM_NAME = "broom1.pt"
BROOM_ENV_VAR = "CURLING_SCORE_BROOM_WEIGHTS"


def broom_path():
    """The broom-head detector, or None: no model means no brooms, not an error.

    Resolved like :func:`side_path`: `none` or unset-and-absent gives None, and
    a path that is set but missing raises.
    """
    chosen = os.environ.get(BROOM_ENV_VAR)
    if chosen:
        if chosen.strip().lower() in ("none", ""):
            return None
        path = Path(chosen)
        if not path.is_file():
            raise FileNotFoundError(f"{BROOM_ENV_VAR}={chosen} does not exist")
        return path
    for path in _candidates(BROOM_NAME):
        if path.is_file():
            return path
    return None
```

Task 5's tests cover `broom_path`.

- [ ] **Step 4: Run**

Run: `./.venv/bin/pytest tests/test_broommodel.py tests/test_broom_harvest.py -q`
Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/detect/broommodel.py src/curling_score/weights.py \
        scripts/broom/harvest.py tests/test_broommodel.py
git commit -m "broommodel: find broom heads on the crop the labels were made on

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: `game/broomtime.py`, the pad held still before the tee crossing

**Files:**
- Create: `src/curling_score/game/broomtime.py`
- Test: `tests/test_broomtime.py`

**Interfaces:**
- Consumes: `broommodel.find`, `broommodel.Pad`, `thinking.tee_crossing`,
  `longview.decode`, and `SideView.to_house` / `has_lateral`.
- Produces:
  - `broomtime.TargetBroom(x_m, y_m, seen, confidence)`, frozen;
  - `broomtime.WINDOW_S = 1.0`, `FPS = 10`, `MIN_SEEN = 0.5`,
    `CLUSTER_M = 0.15`;
  - `broomtime.window_for(shot) -> (t0, t1) | None`;
  - `broomtime.pick_target(samples, n_frames) -> TargetBroom | None`, where
    `samples` is a list of `(frame_index, x_m, y_m, conf)`;
  - `broomtime.time_target_brooms(shots, video, view, *, model=None,
    decode=None) -> None`, which sets `shot.target_broom` in place.

- [ ] **Step 1: Write the failing tests**

```python
"""The skip's target broom: the pad held still in the second before the tee."""
from types import SimpleNamespace

import numpy as np
import pytest

from curling_score.game import broomtime
from curling_score.geometry.sideview import SideView


def s(i, x, y, conf=0.8):
    return (i, x, y, conf)


class TestPickTarget:
    def test_a_pad_held_still_is_the_target(self):
        got = broomtime.pick_target([s(i, 0.60, -0.20) for i in range(10)], 10)
        assert (got.x_m, got.y_m) == pytest.approx((0.60, -0.20))
        assert got.seen == pytest.approx(1.0)

    def test_a_stray_does_not_outvote_a_held_pad(self):
        held = [s(i, 0.6, -0.2) for i in range(8)]
        stray = [s(i, -1.4, 1.0, 0.95) for i in range(3)]
        got = broomtime.pick_target(held + stray, 10)
        assert got.x_m == pytest.approx(0.6)

    def test_a_pad_down_for_under_half_the_window_is_no_call(self):
        """A clock offset can put the lift inside the window; a half-seen pad
        is not averaged into a marker somewhere it never was."""
        assert broomtime.pick_target([s(i, 0.6, -0.2) for i in range(4)], 10) is None

    def test_two_pads_held_still_go_to_the_one_nearest_the_tee(self):
        skip = [s(i, 0.5, 0.3) for i in range(10)]
        sideline = [s(i, 2.1, 0.9) for i in range(10)]     # VXU9 e5 s4
        got = broomtime.pick_target(sideline + skip, 10)
        assert got.x_m == pytest.approx(0.5)

    def test_behind_the_back_line_is_the_other_skip(self):
        assert broomtime.pick_target([s(i, 0.2, -2.1) for i in range(10)], 10) is None

    def test_past_the_hog_line_or_off_the_sheet_is_refused(self):
        assert broomtime.pick_target([s(i, 0.0, 6.8) for i in range(10)], 10) is None
        assert broomtime.pick_target([s(i, 2.4, 0.0) for i in range(10)], 10) is None

    def test_a_guard_call_in_front_of_the_house_is_kept(self):
        got = broomtime.pick_target([s(i, 0.1, 4.0) for i in range(10)], 10)
        assert got.y_m == pytest.approx(4.0)

    def test_the_median_ignores_one_wobble(self):
        pts = [s(i, 0.60, -0.20) for i in range(9)] + [s(9, 0.72, -0.10)]
        got = broomtime.pick_target(pts, 10)
        assert (got.x_m, got.y_m) == pytest.approx((0.60, -0.20))

    def test_nothing_seen_is_no_call(self):
        assert broomtime.pick_target([], 10) is None


class TestWindowFor:
    def test_the_second_before_an_observed_tee_crossing(self):
        shot = SimpleNamespace(missing=False, release=None, tee_s=100.0,
                               tee_estimated=False)
        assert broomtime.window_for(shot) == pytest.approx((99.0, 100.0))

    def test_an_estimated_crossing_is_used_too(self):
        shot = SimpleNamespace(missing=False, release=None, tee_s=50.0,
                               tee_estimated=True)
        assert broomtime.window_for(shot) == pytest.approx((49.0, 50.0))

    def test_a_placeholder_or_an_untimed_shot_has_none(self):
        assert broomtime.window_for(SimpleNamespace(missing=True, release=None,
                                                    tee_s=10.0)) is None
        assert broomtime.window_for(SimpleNamespace(missing=False, release=None,
                                                    tee_s=None)) is None


VIEW = SideView(rect=(0, 0, 810, 1080), tee_row=430.0, hog_row=520.0,
                centre_col=390.0, lat_px_per_m_at_tee=148.0)


class TestTimeTargetBrooms:
    def _shot(self, **kw):
        base = dict(missing=False, release=None, tee_s=100.0, tee_estimated=False,
                    target_broom=None)
        base.update(kw)
        return SimpleNamespace(**base)

    def _run(self, shots, view=VIEW, col=390.0 + 0.5 * 148.0, row=430.0):
        from curling_score.detect.broommodel import Pad
        seen = []

        def decode(video, rect, t0, t1, fps):
            seen.append((t0, t1, fps))
            return [np.zeros((1080, 810, 3), np.uint8)] * 10, [t0 + i / fps for i in range(10)]

        class M:
            pass
        import curling_score.detect.broommodel as bm
        real = bm.find
        bm.find = lambda model, frames, view, conf=bm.CONF_MIN: [
            [Pad(col, row, 0.9, col - 15, row - 10, col + 15, row)] for _ in frames]
        try:
            broomtime.time_target_brooms(shots, "v.mp4", view, model=M(), decode=decode)
        finally:
            bm.find = real
        return seen

    def test_it_attaches_the_broom_in_house_metres(self):
        shot = self._shot()
        seen = self._run([shot])
        assert seen == [(pytest.approx(99.0), pytest.approx(100.0), broomtime.FPS)]
        assert (shot.target_broom.x_m, shot.target_broom.y_m) == pytest.approx((0.5, 0.0), abs=1e-6)

    def test_a_placeholder_is_left_alone(self):
        shot = self._shot(missing=True)
        assert self._run([shot]) == [] and shot.target_broom is None

    def test_no_lateral_calibration_is_a_no_op(self):
        shot = self._shot()
        flat = SideView(rect=(0, 0, 810, 1080), tee_row=430.0, hog_row=520.0)
        assert self._run([shot], view=flat) == [] and shot.target_broom is None

    def test_no_model_is_a_no_op(self):
        shot = self._shot()
        broomtime.time_target_brooms([shot], "v.mp4", VIEW, model=None,
                                     decode=lambda *a: ([], []))
        assert shot.target_broom is None
```

- [ ] **Step 2: Run to see it fail**

Run: `./.venv/bin/pytest tests/test_broomtime.py -q`
Expected: `ModuleNotFoundError: curling_score.game.broomtime`.

- [ ] **Step 3: Implement**

```python
"""The skip's target broom, read in the second before the thrower crosses the tee.

The skip holds the pad on the ice as the aim for the whole delivery. Phase 0
found it down and still across t_tee -2.0..+0.5 s on 45 of 49 shots, so the
second before the tee crossing (the user's window) sits well inside the hold,
even with the camera and the panel up to ~0.9 s out of step. What counts is a
pad held STILL: a cluster seen in at least half the frames. A pad that moves
or lifts gives no marker, never an average of where it went. Two held pads --
the skip's and one resting by the sideline -- go to the one nearest the tee.

Like `hogtime` and `fartime`, this only attaches data to shots that exist.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from curling_score.detect import broommodel
from curling_score.game import thinking
from curling_score.geometry import constants as C

WINDOW_S = 1.0
FPS = 10
MIN_SEEN = 0.5
CLUSTER_M = 0.15
X_MAX_M = 2.2                   # inside the sheet's 2.375 half-width
BEHIND_M = 0.15                 # past the back line is the other skip's ground


@dataclass(frozen=True)
class TargetBroom:
    x_m: float
    y_m: float
    seen: float                 # fraction of the window's frames it was in
    confidence: float           # median detector confidence over those frames


def window_for(shot):
    if getattr(shot, "missing", False):
        return None
    t = thinking.tee_crossing(shot)
    return None if t is None else (t - WINDOW_S, t)


def pick_target(samples, n_frames: int) -> TargetBroom | None:
    keep = [p for p in samples
            if abs(p[1]) <= X_MAX_M
            and -C.R_12FT_M - BEHIND_M <= p[2] <= C.TEE_TO_HOGLINE_M]
    clusters: list[list] = []
    for p in sorted(keep, key=lambda q: -q[3]):
        for c in clusters:
            cx = float(np.median([q[1] for q in c]))
            cy = float(np.median([q[2] for q in c]))
            if math.hypot(p[1] - cx, p[2] - cy) <= CLUSTER_M:
                c.append(p)
                break
        else:
            clusters.append([p])
    best = None
    for c in clusters:
        seen = len({q[0] for q in c}) / max(1, n_frames)
        if seen < MIN_SEEN:
            continue
        x, y = float(np.median([q[1] for q in c])), float(np.median([q[2] for q in c]))
        rank = (-len({q[0] for q in c}), math.hypot(x, y))
        if best is None or rank < best[0]:
            best = (rank, TargetBroom(x, y, seen, float(np.median([q[3] for q in c]))))
    return None if best is None else best[1]


def time_target_brooms(shots, video, view, *, model=None, decode=None) -> None:
    """Give every shot its target broom, in place. No-op without a lateral
    calibration or a model; a shot with no held pad keeps None."""
    if model is None or view is None or not view.has_lateral:
        return
    if decode is None:
        from curling_score.detect import longview
        decode = longview.decode
    for shot in shots:
        win = window_for(shot)
        if win is None:
            continue
        frames, _times = decode(video, view.rect, win[0], win[1], FPS)
        if not len(frames):
            continue
        samples = []
        for i, pads in enumerate(broommodel.find(model, frames, view)):
            for p in pads:
                x, y = view.to_house(p.col, p.row)
                samples.append((i, x, y, p.conf))
        shot.target_broom = pick_target(samples, len(frames))
```

- [ ] **Step 4: Run**

Run: `./.venv/bin/pytest tests/test_broomtime.py -q`
Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/game/broomtime.py tests/test_broomtime.py
git commit -m "broomtime: the pad held still before the tee crossing is the skip's call

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: wire the pass into the pipeline

**Files:**
- Modify: `src/curling_score/game/shots.py` (`Shot`), `src/curling_score/weights.py`
  (`broom_path` if not already added), `src/curling_score/version.py`
  (`processing_version`), `src/curling_score/analyze.py` (the model plus the
  call beside hogtime ~L390, and `processing_version` ~L484)
- Test: `tests/test_weights.py`, `tests/test_version.py`,
  `tests/test_broomtime.py` (a `Shot` default)

**Interfaces:**
- Produces:
  - `Shot.target_broom: object = None`;
  - `weights.broom_path()`;
  - `version.processing_version(weights, side_weights=None, broom_weights=None)`,
    which appends `+broom-<model_id>` only when `broom_weights` is given.
  - `analyze` calls `broomtime.time_target_brooms(shots, path,
    sideviews[hogtime.CAMERA_FOR[end.house]], model=broom_model)` when
    `sideviews` and `broom_model` are set.

- [ ] **Step 1: Write the failing tests**

`tests/test_version.py`: add a class copying the side-model class's fixture
style (a tmp weights file):

```python
class TestTheBroomModelIsPartOfTheIdentity:
    def _w(self, tmp_path, name, body=b"x"):
        p = tmp_path / name
        p.write_bytes(body)
        return p

    def test_a_broom_model_changes_the_version(self, tmp_path):
        w, side = self._w(tmp_path, "ds15a.pt"), self._w(tmp_path, "ds13b.pt", b"s")
        broom = self._w(tmp_path, "broom1.pt", b"b")
        with_b = version.processing_version(w, side, broom)
        assert with_b.startswith(version.processing_version(w, side))
        assert "+broom-broom1-" in with_b

    def test_no_broom_model_keeps_the_old_identity(self, tmp_path):
        w, side = self._w(tmp_path, "ds15a.pt"), self._w(tmp_path, "ds13b.pt", b"s")
        assert version.processing_version(w, side, None) == version.processing_version(w, side)
```

Check the file's existing imports and helpers, and reuse them if a
weights-file helper already exists.

`tests/test_weights.py`:

```python
class TestBroomPath:
    def test_absent_is_no_brooms_not_an_error(self, monkeypatch):
        monkeypatch.delenv(weights.BROOM_ENV_VAR, raising=False)
        monkeypatch.setattr(weights, "_candidates", lambda name=None: iter(()))
        assert weights.broom_path() is None

    def test_none_switches_it_off(self, monkeypatch):
        monkeypatch.setenv(weights.BROOM_ENV_VAR, "none")
        assert weights.broom_path() is None

    def test_a_set_but_missing_path_raises(self, monkeypatch, tmp_path):
        monkeypatch.setenv(weights.BROOM_ENV_VAR, str(tmp_path / "nope.pt"))
        with pytest.raises(FileNotFoundError):
            weights.broom_path()
```

`tests/test_broomtime.py`:

```python
class TestShotField:
    def test_a_shot_starts_with_no_broom(self):
        from curling_score.game.shots import Shot
        assert Shot(number=1, color="red", stones=[], t_rest_s=0.0).target_broom is None
```

- [ ] **Step 2: Run to see them fail**

Run: `./.venv/bin/pytest tests/test_version.py tests/test_weights.py tests/test_broomtime.py -q`
Expected: failures on `processing_version()`'s argument count, missing
`BROOM_ENV_VAR` (unless Task 3 added it), and `Shot` having no `target_broom`.

- [ ] **Step 3: Implement**

`shots.py`, after `far_crossing`:

```python
    # Where the skip held the target broom in the second before this rock
    # crossed the throwing end's tee -- a `broomtime.TargetBroom` in house
    # metres -- or None when no pad was held still, or there is no model.
    target_broom: object = None
```

`version.py`: add `broom_weights=None` to the signature and append after the
side part:

```python
    out = base if side_weights is None else f"{base}+side-{model_id(side_weights)}"
    return out if broom_weights is None else f"{out}+broom-{model_id(broom_weights)}"
```

Mention the broom model in the docstring: folded in only when present, so a
timeline made without it keeps its identity.

`analyze.py`:
- beside the detector setup (~L256), add `broom_model = None if skip_longview
  else broommodel.default_model()`, with a progress line when set;
- after the hogtime call (inside `if sideviews is not None:`), add:

```python
                # The skip's target broom, from the camera that sees the
                # destination house -- the OTHER camera from hogtime's.
                broomtime.time_target_brooms(
                    shots, path, sideviews[hogtime.CAMERA_FOR[end.house]],
                    model=broom_model)
```

- change the `processing_version` call to
  `version.processing_version(weights, weights_mod.side_path(), weights_mod.broom_path())`;
- import `broomtime` and `broommodel` beside the existing `hogtime` and
  `sidemodel` imports.

- [ ] **Step 4: Run**

Run: `./.venv/bin/pytest tests/test_version.py tests/test_weights.py tests/test_broomtime.py tests/test_analyze_side.py tests/test_analyze_write.py tests/test_cli_analyze.py -q`
Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/game/shots.py src/curling_score/weights.py src/curling_score/version.py \
        src/curling_score/analyze.py tests/test_version.py tests/test_weights.py tests/test_broomtime.py
git commit -m "analyze: attach the skip's target broom, and name the broom model in the version

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: publish `target_broom` in the timeline (schema 5)

**Files:**
- Modify: `src/curling_score/timeline.py` (`SCHEMA_VERSION`, the shot dict
  in `build_end`)
- Test: `tests/test_timeline.py`

**Interfaces:**
- Produces: `shot["target_broom"]`. Its value is `{"x": float4, "y": float4,
  "seen": float2, "confidence": float3}` or `None`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_timeline.py`:
- change `test_the_schema_version_says_the_shape_changed` to assert `== 5`,
  with a docstring line: "5 adds `target_broom` to every shot."
- add the class below. Reuse this file's existing shot-building helper for a
  real `Shot`; look at how `test_a_placeholder_shot_has_no_timings` calls
  `build_end` and mirror it.

```python
class TestTargetBroom:
    def _end(self, broom):
        from curling_score.game.broomtime import TargetBroom
        from curling_score.game.shots import Shot
        s = Shot(number=1, color="red", stones=[], t_rest_s=10.0)
        s.target_broom = None if broom is None else TargetBroom(*broom)
        return timeline.build_end(number=1, house="top", start_s=0.0,
                                  end_s=900.0, shots=[s])

    def test_a_held_broom_is_published_in_house_metres(self):
        shot = self._end((0.61234, -0.20456, 0.9, 0.8123))["shots"][0]
        assert shot["target_broom"] == {"x": 0.6123, "y": -0.2046,
                                        "seen": 0.9, "confidence": 0.812}

    def test_no_broom_is_null_not_absent(self):
        assert self._end(None)["shots"][0]["target_broom"] is None
```

If `build_end` needs more arguments than shown, copy them from the placeholder
test.

- [ ] **Step 2: Run to see it fail**

Run: `./.venv/bin/pytest tests/test_timeline.py -q -k "TargetBroom or schema_version"`
Expected: FAIL. `KeyError: 'target_broom'`, and the schema is 4.

- [ ] **Step 3: Implement**

`SCHEMA_VERSION = 5`. In the shot dict, after `"stones": ...`:

```python
                # Where the skip held the broom before the rock crossed the
                # tee, in house metres; null when no pad was held still, or the
                # timeline was made without a broom model.
                "target_broom": _broom(getattr(s, "target_broom", None)),
```

Add this helper beside `_stone`:

```python
def _broom(b):
    if b is None:
        return None
    return {"x": round(float(b.x_m), 4), "y": round(float(b.y_m), 4),
            "seen": round(float(b.seen), 2), "confidence": round(float(b.confidence), 3)}
```

- [ ] **Step 4: Run**

Run: `./.venv/bin/pytest tests/test_timeline.py tests/test_analyze_write.py -q`
Expected: pass. If some other test pins 4, update it with a note of why.

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/timeline.py tests/test_timeline.py
git commit -m "timeline: every shot carries its target broom, schema 5

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: the viewer draws the broom and its link to the stone

**Files:**
- Modify: `frontend/core/house.mjs` (`broomMark`), `tests/js/singleton.mjs`
  (re-export), `frontend/viewer/House.jsx` (`Broom`)
- Build output: `src/curling_score/viewer/app.js`,
  `src/curling_score/service/static/site.js` (if it changes),
  `frontend/.buildstamp.json`
- Test: `tests/test_viewer_js.py`

**Interfaces:**
- Produces: `broomMark(shot) -> {x, y, to: {x, y} | null} | null`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_viewer_js.py`)

```python
class TestTheSkipsBroom:
    """Drawn only from numbers the timeline actually has: an old chart, or a
    shot with no held pad, draws nothing rather than a pad at the tee."""

    def mark(self, s):
        return run_js(f"out(broomMark({json.dumps(s)}));")

    def test_a_broom_links_to_the_stone_this_shot_left(self):
        s = shot(3, "red", "lead", target_broom={"x": 0.6, "y": -0.2},
                 stones=[{"color": "yellow", "x": 1.0, "y": 1.0},
                         {"color": "red", "x": 0.3, "y": 0.4}],
                 delivered_stone_index=1)
        assert self.mark(s) == {"x": 0.6, "y": -0.2, "to": {"x": 0.3, "y": 0.4}}

    def test_no_delivered_stone_is_a_broom_with_no_link(self):
        s = shot(3, "red", "lead", target_broom={"x": 0.6, "y": -0.2})
        assert self.mark(s) == {"x": 0.6, "y": -0.2, "to": None}

    def test_an_old_chart_or_a_null_broom_draws_nothing(self):
        assert self.mark(shot(3, "red", "lead")) is None
        assert self.mark(shot(3, "red", "lead", target_broom=None)) is None

    def test_a_malformed_broom_draws_nothing(self):
        assert self.mark(shot(3, "red", "lead", target_broom={"x": "0.6", "y": 1})) is None

    def test_the_house_draws_it_with_the_track_and_under_the_stones(self):
        src = (Path(__file__).resolve().parents[1] / "frontend/viewer/House.jsx").read_text()
        i_broom, i_stones = src.index("<Broom shot={shot}"), src.index("<Stones shot={shot}")
        assert "showTrack && <Broom" in src and i_broom < i_stones
```

- [ ] **Step 2: Run to see it fail**

Run: `./.venv/bin/pytest tests/test_viewer_js.py -q -k TheSkipsBroom`
Expected: FAIL. `broomMark is not defined`, and `<Broom` is not found in the
source.

- [ ] **Step 3: Implement**

`frontend/core/house.mjs`:

```js
/* Where the skip's broom was held, and the stone this shot left, or null. The
 * field is absent on every chart from before schema 5 and null on a shot with
 * no pad held still; both draw nothing rather than a pad somewhere invented. */
export function broomMark(shot) {
  const b = shot?.target_broom;
  if (!b || typeof b.x !== "number" || typeof b.y !== "number") return null;
  const i = shot.delivered_stone_index;
  const st = Number.isInteger(i) ? shot.stones?.[i] : null;
  const to = st && typeof st.x === "number" && typeof st.y === "number"
    ? { x: st.x, y: st.y } : null;
  return { x: b.x, y: b.y, to };
}
```

`tests/js/singleton.mjs`: add `export const broomMark = core.broomMark;` beside
`stoneAt`.

`frontend/viewer/House.jsx`:
- import `broomMark` wherever `stoneAt` / `onSheet` are imported from core;
- add after `Track`:

```jsx
/* The skip's broom, where it was held in the second before the thrower crossed
 * the tee, and a faint line to where the delivered stone finished -- the call
 * and the result side by side. On a draw the pad is the aim, not the intended
 * rest spot, so the tooltip claims no more than that. Not clipped to the ice: a
 * pad just behind the back line is still worth showing. */
function Broom({ shot }) {
  const m = broomMark(shot);
  if (!m) return null;
  const color = shot.color === "red" ? PAINT.red : PAINT.yellow;
  return (
    <g className="broom">
      {m.to && (
        <line x1={m.x} y1={m.y} x2={m.to.x} y2={m.to.y} stroke={PAINT.accent}
              strokeWidth={0.02} strokeDasharray="0.08 0.06" opacity={0.5}
              pointerEvents="none" />
      )}
      <rect x={m.x - 0.11} y={m.y - 0.04} width={0.22} height={0.08} rx={0.025}
            fill={PAINT.accent} stroke={color} strokeWidth={0.02}>
        <title>skip's broom</title>
      </rect>
    </g>
  );
}
```

- in the SVG, change `{showTrack && <Track shot={shot} />}` to:

```jsx
      {showTrack && <Track shot={shot} />}
      {showTrack && <Broom shot={shot} />}
```

  The pad keeps pointer events so its tooltip shows. A click on it is still
  "not a stone" to `onPointerDown` (`closest(".stone")` is null), so editing
  behaves exactly as if it were ice.

- build: `cd frontend && npm run build`.

- [ ] **Step 4: Run**

Run: `./.venv/bin/pytest tests/test_viewer_js.py tests/test_frontend_build.py -q`
Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/core/house.mjs frontend/viewer/House.jsx tests/js/singleton.mjs \
        tests/test_viewer_js.py src/curling_score/viewer/app.js frontend/.buildstamp.json
git add src/curling_score/service/static/site.js 2>/dev/null || true
git commit -m "viewer: the house shows the skip's broom and a line to the stone it asked for

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: round 2, pre-labelled by v0 (the user labels)

**Files:**
- Modify: `scripts/broom/harvest.py` (add `--weights`: pre-label each crop
  with `broommodel.find` at conf 0.05, and render with `proposals=True`)
- Test: `tests/test_broom_harvest.py` (the proposals conversion)

**Interfaces:**
- Produces: `harvest.proposals(pads, top, width, height) -> list[[0, cx, cy, w, h]]`,
  crop-normalised.

- [ ] **Step 1: Write the failing test**

```python
class TestProposals:
    def test_a_pad_in_view_pixels_becomes_a_normalised_crop_box(self):
        from curling_score.detect.broommodel import Pad
        p = Pad(col=400.0, row=450.0, conf=0.9, x0=385.0, y0=430.0, x1=415.0, y1=450.0)
        got = harvest.proposals([p], top=300, width=810, height=235)
        assert got == [[0, round(400 / 810, 6), round(140 / 235, 6),
                        round(30 / 810, 6), round(20 / 235, 6)]]
```

- [ ] **Step 2: Run to see it fail**

Run: `./.venv/bin/pytest tests/test_broom_harvest.py -q -k Proposals`
Expected: `AttributeError: ... 'proposals'`.

- [ ] **Step 3: Implement**

```python
def proposals(pads, top: int, width: int, height: int) -> list:
    """v0's boxes as the editor's rows, normalised to the crop."""
    return [[0, round((p.x0 + p.x1) / 2 / width, 6),
             round(((p.y0 + p.y1) / 2 - top) / height, 6),
             round((p.x1 - p.x0) / width, 6), round((p.y1 - p.y0) / height, 6)]
            for p in pads]
```

In `main`:
- add `ap.add_argument("--weights")`;
- when it is given, load it once (`from ultralytics import YOLO; model =
  YOLO(args.weights)`);
- after decoding each frame, compute `pads = broommodel.find(model, [frames[0]],
  view, conf=0.05)[0]` and set `"boxes": proposals(pads, top, crop.shape[1],
  crop.shape[0])`;
- render with `proposals=bool(args.weights)`.

- [ ] **Step 4: Run, harvest both games, serve**

Run: `./.venv/bin/pytest tests/test_broom_harvest.py -q`. Expected: pass.

Then run the harvest once per game, one wave directory each:

```bash
./.venv/bin/python scripts/broom/harvest.py \
    --timeline ~/curling-work/ds15/games/timelines/AEqLTgM25Tc.json \
    --video ~/.cache/curling_replay/videos/AEqLTgM25Tc.mp4 \
    --out ~/curling-work/broom/wave2-aeql --manifest datasets/broom/manifest-wave2-aeql.json \
    --scope broom:wave2-aeql --weights ~/curling-work/broom/weights/v0.pt
./.venv/bin/python scripts/broom/harvest.py \
    --timeline ~/curling-work/ds15/games/timelines/hOKZoeJNTpM.json \
    --video ~/.cache/curling_score/videos/hOKZoeJNTpM.mp4 \
    --out ~/curling-work/broom/wave2-hokz --manifest datasets/broom/manifest-wave2-hokz.json \
    --scope broom:wave2-hokz --weights ~/curling-work/broom/weights/v0.pt
```

Serve them with `scripts/broom/serve.py`: AEqL on 8777, hOKZ on 8778 (pass
`--port 8778`). Tell the user:
- boxes arrive pre-drawn;
- delete wrong ones, add missed pads with B+click;
- press space on frames that are right, including empty ones;
- Save to server.

Commit the two manifests and the code:

```bash
git add scripts/broom/harvest.py tests/test_broom_harvest.py \
        datasets/broom/manifest-wave2-aeql.json datasets/broom/manifest-wave2-hokz.json
git commit -m "broom: round 2, AEqL and hOKZ pre-labelled by v0

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

When the user reports saved sessions, copy each `edits/*.json` into
`datasets/broom/edits/` and commit it straight away.

---

### Task 9: held-out evaluation on hOKZ, then the shipped model

**Files:**
- Create: `scripts/broom/eval_heldout.py`
- Test: `tests/test_broom_eval.py` (the scoring)

**Interfaces:**
- Consumes: the manifest rows (with lateral), merged edits, `broommodel.find`,
  `broomtime.pick_target`, `longview.decode`, and `SideView.to_house`.
- Produces: `eval_heldout.truth(row, boxes) -> list[(x, y)]`, the labelled pads
  in house metres at their bottom-centre. Also `eval_heldout.score(results) ->
  dict`, where `results` is a list of `(truth_points, marker_or_None)`.

- [ ] **Step 1: Write the failing tests**

```python
"""scripts/broom/eval_heldout.py: the bar is per shot, against the user's boxes."""
import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "broom_eval", Path(__file__).parents[1] / "scripts" / "broom" / "eval_heldout.py")
ev = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ev)

ROW = {"rect": [0, 0, 810, 1080], "tee_row": 430.0, "hog_row": 520.0,
       "centre_col": 390.0, "lat_px_per_m_at_tee": 148.0, "crop_top": 300,
       "width": 810, "height": 235}


class TestTruth:
    def test_a_box_s_foot_on_the_tee_row_at_the_centre_is_the_tee(self):
        cy_foot = (430.0 - 300) / 235
        box = [0, 390 / 810, cy_foot - 0.02, 0.03, 0.04]    # bottom = cy + h/2 = foot
        (x, y), = ev.truth(ROW, [box])
        assert (x, y) == pytest.approx((0.0, 0.0), abs=0.01)


class TestScore:
    def test_the_bar_counts_hits_within_a_stone_width(self):
        results = [([(0.0, 0.0)], (0.1, 0.1)),        # hit (0.14 m)
                   ([(0.0, 0.0)], (0.5, 0.0)),        # miss (0.5 m), not wild
                   ([(0.0, 0.0)], None),              # no call
                   ([], (1.0, 1.0)),                  # a call where no pad was boxed
                   ([(0.0, 0.0)], (0.9, 0.0))]        # wild (0.9 m)
        got = ev.score(results)
        assert got["with_pad"] == 4 and got["hits"] == 1
        assert got["markers"] == 4 and got["wild"] == 2   # >0.60 m off, or no pad
```

- [ ] **Step 2: Run to see it fail**

Run: `./.venv/bin/pytest tests/test_broom_eval.py -q`
Expected: collection error (file missing).

- [ ] **Step 3: Implement `scripts/broom/eval_heldout.py`**

```python
"""Score the broom pass on a held-out game against the user's boxes.

For each shot, the truth is every pad the user boxed on the shot's t_tee-0.3
frame, at its foot, in house metres. The pass decodes [t_tee-1, t_tee] at
broomtime.FPS, runs the model and pick_target. The spec's bar:
  - at least 80% of shots with a pad boxed get a marker within 0.30 m of one;
  - at most 5% of markers are more than 0.60 m from every boxed pad, or sit on
    a shot with none boxed.

    ./.venv/bin/python scripts/broom/eval_heldout.py --weights <best.pt> \\
        --video ~/.cache/curling_score/videos/hOKZoeJNTpM.mp4 \\
        --manifest datasets/broom/manifest-wave2-hokz.json \\
        --edits 'datasets/broom/edits/broom-wave2-hokz-*.json'
"""
import argparse
import glob
import json
import math
import sys
from pathlib import Path

HIT_M, WILD_M = 0.30, 0.60


def _view(row):
    from curling_score.geometry.sideview import SideView
    return SideView(rect=tuple(row["rect"]), tee_row=row["tee_row"],
                    hog_row=row["hog_row"], centre_col=row["centre_col"],
                    lat_px_per_m_at_tee=row["lat_px_per_m_at_tee"])


def truth(row, boxes):
    v = _view(row)
    return [v.to_house(b[1] * row["width"],
                       row["crop_top"] + (b[2] + b[4] / 2) * row["height"])
            for b in boxes]


def score(results):
    with_pad = hits = markers = wild = 0
    for pts, m in results:
        near = min((math.hypot(m[0] - x, m[1] - y) for x, y in pts),
                   default=math.inf) if m is not None else math.inf
        if pts:
            with_pad += 1
            hits += near <= HIT_M
        if m is not None:
            markers += 1
            wild += near > WILD_M
    return {"with_pad": with_pad, "hits": hits, "markers": markers, "wild": wild,
            "hit_rate": hits / with_pad if with_pad else None,
            "wild_rate": wild / markers if markers else None}


def main() -> int:
    from ultralytics import YOLO

    from curling_score.detect import broommodel, longview
    from curling_score.game import broomtime
    from curling_score.train import labels

    ap = argparse.ArgumentParser()
    for a in ("--weights", "--video", "--manifest", "--edits"):
        ap.add_argument(a, required=True)
    args = ap.parse_args()
    model = YOLO(args.weights)
    edits = labels.merge_edits(*(json.loads(Path(f).read_text())
                                 for f in sorted(glob.glob(args.edits))))
    rows = [r for r in json.loads(Path(args.manifest).read_text())
            if r["offset"] == -0.3 and r["stem"] in edits.boxes]
    results = []
    for r in rows:
        v = _view(r)
        frames, _ = longview.decode(Path(args.video).expanduser(), v.rect,
                                    r["t_tee"] - broomtime.WINDOW_S, r["t_tee"],
                                    broomtime.FPS)
        samples = [(i, *v.to_house(p.col, p.row), p.conf)
                   for i, pads in enumerate(broommodel.find(model, frames, v))
                   for p in pads]
        m = broomtime.pick_target(samples, len(frames))
        results.append((truth(r, edits.boxes[r["stem"]]),
                        None if m is None else (m.x_m, m.y_m)))
    print(json.dumps(score(results), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Train held-out, evaluate, train the ship model**

Run `./.venv/bin/pytest tests/test_broom_eval.py -q`. Expected: pass.

Then, once round-2 labels are committed:

```bash
./.venv/bin/python scripts/broom/make_tree.py --out ~/curling-work/broom/tree-heldout \
  --wave train ~/curling-work/broom/wave1b datasets/broom/manifest-wave1b.json 'datasets/broom/edits/broom-wave1b-*.json' \
  --wave split ~/curling-work/broom/wave2-aeql datasets/broom/manifest-wave2-aeql.json 'datasets/broom/edits/broom-wave2-aeql-*.json' \
  --val-fraction 0.2
scripts/broom/train_on_worker.sh ~/curling-work/broom/tree-heldout heldout 300
# when done: scp best.pt -> ~/curling-work/broom/weights/heldout.pt, then:
./.venv/bin/python scripts/broom/eval_heldout.py --weights ~/curling-work/broom/weights/heldout.pt \
  --video ~/.cache/curling_score/videos/hOKZoeJNTpM.mp4 \
  --manifest datasets/broom/manifest-wave2-hokz.json --edits 'datasets/broom/edits/broom-wave2-hokz-*.json'
```

Record the result against the bar in the spec (a "Phase 3 result" section) and
in `datasets/broom/README.md`. Whatever the numbers, train the ship model on
all three games (`tree-all`: VXU9 train, AEqL split, hOKZ split), copy it to
`weights/broom1.pt`, and commit it with the eval script, the tests and the
docs:

```bash
git add scripts/broom/eval_heldout.py tests/test_broom_eval.py weights/broom1.pt \
        docs/superpowers/specs/2026-09-23-target-broom-design.md datasets/broom/README.md
git commit -m "broom: held out on hOKZ, the pass scores <hit>% within 0.30 m; broom1 trained on all three

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

If the bar is missed, still commit the numbers, and say plainly in the final
report which part missed.

---

### Task 10: local end-to-end verification

- [ ] **Step 1:** Run
  `CURLING_SCORE_BROOM_WEIGHTS=none ./.venv/bin/curling-score -v analyze <VXU9 url> --out ~/curling-work/broom/verify/vxu9-none`
  and the same without the env var into `.../vxu9-broom`. Use the cached video;
  no new downloads. Diff the two `timeline.json` files with `target_broom`,
  `schema_version` and `processing_version` removed. **They must be
  identical.** Check `analyze --help` first for the exact flags.
- [ ] **Step 2:** Report broom coverage, the share of non-missing shots with a
  marker, for VXU9, and for hOKZ if time allows. Spot-check about 5 markers by
  projecting them into the side frame with `view.to_image` and viewing the
  image.
- [ ] **Step 3:** Run `python scripts/devserve.py ~/curling-work/broom/verify/vxu9-broom/timeline.json`.
  Screenshot the house on a few shots in the desktop and phone layouts (the
  `run` skill), and check:
  - the pad and the link show, and the track toggle (`t`) hides both;
  - a schema-4 timeline (`~/curling-work/ds15/games/timelines/VXU9xwmugRg.json`)
    shows no pad and no console errors.
- [ ] **Step 4:** HawkScan: run the skill if `HAWK_API_KEY` is set. Otherwise
  say it was not run and why.

## After this plan

Shipping needs a separate go from the user:
- `Dockerfile.worker` gains `ARG BROOM_MODEL` plus `COPY` and
  `ENV CURLING_SCORE_BROOM_WEIGHTS`, and `tests/test_weights.py`'s worker-image
  class gains the broom model;
- deploy the worker and API from a clean worktree;
- requeue the hosted videos.
