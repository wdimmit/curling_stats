"""The handle-point model: where a stone's handle neck and grip end are.

A YOLO-pose model with one class (the stone) and two keypoints -- the neck,
where the handle rises out of the stone and which stays put as it turns, and
the grip end, which swings round. `game/spintime.py` reads a rock's rotation
from how the grip end swings across the neck. Loaded lazily, like
`sidemodel.default_finder`, so importing this needs no GPU or weights.
"""
from __future__ import annotations

import functools

import numpy as np

from curling_score.detect import longview

IMGSZ = 224
CONF = 0.25
BATCH = 64


@functools.lru_cache(maxsize=2)
def _load(path: str):
    from ultralytics import YOLO

    return YOLO(path)


def default_model():
    """The handle-point model, or None when none is configured."""
    from curling_score import weights as weights_mod
    path = weights_mod.spin_path()
    return None if path is None else _load(str(path))


def handle_points(model, patches, imgsz: int = IMGSZ):
    """Per patch (RGB, as `longview.decode` gives them): ``((neck_x, neck_y),
    (grip_x, grip_y), conf)`` in the patch's pixels, ``conf`` the weaker of the
    two points' confidences; None where no stone was found. The most confident
    stone is the one read. Patches go in as BGR, as training read its PNGs."""
    out = []
    for i in range(0, len(patches), BATCH):
        batch = [longview.to_bgr(p) for p in patches[i:i + BATCH]]
        for r in model.predict(batch, imgsz=imgsz, conf=CONF, verbose=False):
            if r.keypoints is None or len(r.boxes) == 0:
                out.append(None)
                continue
            k = int(np.argmax(r.boxes.conf.cpu().numpy()))
            xy = r.keypoints.xy.cpu().numpy()[k]
            kc = r.keypoints.conf.cpu().numpy()[k] if r.keypoints.conf is not None else np.ones(2)
            out.append(((float(xy[0][0]), float(xy[0][1])), (float(xy[1][0]), float(xy[1][1])),
                        float(min(kc))))
    return out
