"""Train a stone detector, with the settings every run since ds2 has used.

Brought into the repo from a script that lived in a scratchpad. That script and
its siblings are half the reason ds8 cannot be reproduced: the dataset builder
and the trainer both vanished with a `/tmp` wipe, so nothing was left that could
say what the model had been shown or how.

The hyperparameters are deliberately frozen. Batch stays at 16 even though the
3070 could take more, because the point of every run after ds2 has been to
compare datasets, and a run that changed the recipe as well would confound the
only comparison being made.
"""

from dataclasses import dataclass
from pathlib import Path

# Colour is the class signal here -- red against yellow handles -- so hue jitter
# stays tight or the model learns to confuse the two teams. The sheet has no
# meaningful left/right or up/down, hence fliplr but not flipud.
SETTINGS = dict(
    imgsz=640, batch=16, workers=8,
    hsv_h=0.005, hsv_s=0.5, hsv_v=0.4,
    fliplr=0.5, flipud=0.0, degrees=5.0,
    mosaic=1.0, close_mosaic=8,
    plots=True, val=True, seed=0,
)


@dataclass(frozen=True)
class Run:
    data: Path
    project: Path
    name: str
    base: str = "yolo11n.pt"
    epochs: int = 40
    patience: int = 15
    device: str | int | None = 0

    @property
    def last(self) -> Path:
        return self.project / self.name / "weights" / "last.pt"

    @property
    def marker(self) -> Path:
        """Which base this run started from.

        Resuming reads the architecture out of the checkpoint and ignores the
        base entirely, so without this a request for yolo11s silently continued
        an existing yolo11n run -- which is exactly what happened once, and the
        only clue was one line of log.
        """
        return self.project / self.name / "base.txt"


def check_base(run: Run) -> None:
    if run.last.exists() and run.marker.exists():
        started = run.marker.read_text().strip()
        if started != run.base:
            raise SystemExit(
                f"run {run.name!r} was started from {started!r} but "
                f"{run.base!r} was asked for. Delete {run.project / run.name} "
                f"to start over, or use a different run name.")


def train(run: Run):
    """Start or resume a run. A dropped session costs one epoch, not a night."""
    from ultralytics import YOLO

    check_base(run)
    if run.last.exists():
        print(f"resuming from {run.last}", flush=True)
        return YOLO(str(run.last)).train(resume=True)

    print(f"starting fresh from {run.base}", flush=True)
    run.marker.parent.mkdir(parents=True, exist_ok=True)
    run.marker.write_text(run.base + "\n")
    return YOLO(run.base).train(
        data=str(run.data), epochs=run.epochs, patience=run.patience,
        device=run.device, project=str(run.project), name=run.name,
        exist_ok=True, **SETTINGS)
