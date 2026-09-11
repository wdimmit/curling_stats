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

from curling_score.detect.rocks import Detection, enforce_separation
from curling_score.geometry import constants as C

CLASS_COLORS = ("red", "yellow")
DEFAULT_CONF = 0.25


def boxes_to_stones(boxes, calib, conf=None):
    """Convert model boxes into stones positioned in sheet metres."""
    out = []
    for box in boxes:
        score = float(box.conf[0])
        if conf is not None and score < conf:
            continue
        cx, cy, w, h = (float(v) for v in box.xywh[0])
        x_m, y_m = calib.to_sheet(cx, cy)
        # The adjacent sheet is visible at the edge of some panels.
        if abs(x_m) > C.SIDELINE_ABS_X_M:
            continue
        out.append(
            Detection(
                color=CLASS_COLORS[int(box.cls[0])],
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


class YoloDetector:
    """A trained stone detector, interchangeable with the classical one."""

    def __init__(self, weights, conf: float = DEFAULT_CONF, device=None,
                 imgsz: int = 640):
        from ultralytics import YOLO

        self.model = YOLO(str(weights))
        self.conf = conf
        self.device = device
        self.imgsz = imgsz

    def find_stones(self, panel, calib):
        results = self.model.predict(
            panel, conf=self.conf, imgsz=self.imgsz, device=self.device,
            verbose=False,
        )
        return boxes_to_stones(results[0].boxes, calib)

    def find_stones_batch(self, panels, calib):
        """Detect over many panels at once -- far faster on a GPU."""
        results = self.model.predict(
            panels, conf=self.conf, imgsz=self.imgsz, device=self.device,
            verbose=False,
        )
        return [boxes_to_stones(r.boxes, calib) for r in results]
