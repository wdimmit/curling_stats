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
        # `longview.decode` already cropped to view.rect, so a frame may arrive
        # either full-width or view-width; take the rows from whichever.
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
