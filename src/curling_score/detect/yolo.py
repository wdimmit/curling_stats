"""Stone detection with a trained YOLO model.

The colour-threshold detector is precise but its recall falls away exactly
where it matters -- a stone under sweeping, motion-blurred on a takeout, or lit
differently from the frames the thresholds were tuned on. Those misses are what
limit delivery detection, and no amount of threshold fiddling fixes them,
because the failure is that a fixed hue/saturation box cannot describe a stone
under all those conditions.

This is a drop-in alternative: same ``find_stones(panel, calib)`` signature,
same :class:`~curling_score.detect.rocks.Detection` output in sheet metres, so
everything downstream is unchanged.
"""

from functools import lru_cache

from curling_score.detect.rocks import Detection, enforce_separation
from curling_score.geometry import constants as C

CLASS_COLORS = ("red", "yellow")
DEFAULT_CONF = 0.25


def stones_from_rows(rows, calib, conf=None):
    """Stones in sheet metres from ``(cx, cy, w, h, conf, cls)`` box rows."""
    out = []
    for cx, cy, w, h, score, cls in rows:
        if conf is not None and score < conf:
            continue
        x_m, y_m = calib.to_sheet(cx, cy)
        # The adjacent sheet is visible at the edge of some panels.
        if abs(x_m) > C.SIDELINE_ABS_X_M:
            continue
        out.append(
            Detection(
                color=CLASS_COLORS[int(cls)],
                x_m=float(x_m),
                y_m=float(y_m),
                x_px=cx,
                y_px=cy,
                area_px=w * h,
                confidence=score,
            )
        )
    # Two same-colour boxes nearer than a stone's width are not two stones.
    return enforce_separation(out)


def boxes_to_stones(boxes, calib, conf=None):
    """Convert model boxes, one at a time, into stones positioned in sheet metres."""
    return stones_from_rows(
        [(*(float(v) for v in box.xywh[0]), float(box.conf[0]), float(box.cls[0]))
         for box in boxes],
        calib, conf)


def result_rows(results):
    """Each result's boxes as ``(cx, cy, w, h, conf, cls)`` rows of floats.

    One copy off the GPU for the whole batch. Reading each box's fields one at
    a time synchronises with the device about six times a box: 20 ms of a
    64-panel batch with 133 boxes, against under a millisecond. The centres
    and sizes are still worked out on the boxes' own device by `Boxes.xywh`,
    as they always were, so no value changes by a bit.
    """
    import torch

    parts = [torch.cat((r.boxes.xywh, r.boxes.conf[:, None], r.boxes.cls[:, None]), 1)
             for r in results]
    flat = torch.cat(parts).cpu().tolist() if parts else []
    rows, i = [], 0
    for part in parts:
        rows.append(flat[i:i + len(part)])
        i += len(part)
    return rows


@lru_cache(maxsize=1)
def _knows_quantize() -> bool:
    from ultralytics.cfg import DEFAULT_CFG_DICT

    return "quantize" in DEFAULT_CFG_DICT


def _precision(model) -> dict:
    """Half precision asked for by the name this ultralytics reads it under.

    Callers ask for it the old way, ``overrides["half"]``, which the detection
    cache's key reads. Ultralytics 8.4 turns that into ``quantize=16`` inside
    the predictor but not in the model's overrides, so on every later call the
    two disagree and it builds the predictor again -- a deep copy, a fuse and a
    warm-up pass for every batch: 190 ms a 64-panel batch against 73, with the
    same boxes either way. Asking for ``quantize=16`` outright keeps the one.
    """
    if model.overrides.get("half") and _knows_quantize():
        return {"quantize": 16}
    return {}


class YoloDetector:
    """A trained stone detector, interchangeable with the classical one."""

    def __init__(self, weights, conf: float = DEFAULT_CONF, device=None,
                 imgsz: int = 640):
        from ultralytics import YOLO

        self.model = YOLO(str(weights))
        self.conf = conf
        self.device = device
        self.imgsz = imgsz

    def twin(self):
        """Another detector on the same weights and settings, with a predictor
        of its own, so that two threads can detect at once. Made once and kept:
        a live lane's detector is shared by every stream it follows."""
        twin = self.__dict__.get("_twin")
        if twin is None:
            twin = YoloDetector(self.model.ckpt_path, conf=self.conf, device=self.device,
                                imgsz=self.imgsz)
            twin.model.overrides.update(self.model.overrides)
            self._twin = twin
        return twin

    def _predict(self, source):
        return self.model.predict(
            source, conf=self.conf, imgsz=self.imgsz, device=self.device,
            verbose=False, **_precision(self.model),
        )

    def find_stones(self, panel, calib):
        return stones_from_rows(result_rows(self._predict(panel)[:1])[0], calib)

    def find_stones_batch(self, panels, calib):
        """Detect over many panels at once -- far faster on a GPU."""
        return [stones_from_rows(rows, calib)
                for rows in result_rows(self._predict(panels))]

    def find_stones_each(self, panels, calibs):
        """Detect over several panels at once, each placed by its own
        calibration -- both houses of a frame in one call."""
        if not panels:
            return []
        return [stones_from_rows(rows, calib)
                for rows, calib in zip(result_rows(self._predict(list(panels))), calibs)]
