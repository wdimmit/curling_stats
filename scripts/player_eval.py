#!/usr/bin/env python
"""How well does a model find the players: precision and recall, loss curves, a contact sheet.

    python scripts/player_eval.py --val /data/wdd/curling/pilot-players/folds/val \\
        --model runs/pilot-players/weights/best.pt [--pose yolo11x-pose.pt] [--conf 0.25] \\
        [--curves runs/pilot-players/results.csv runs/overhead-3/results.csv] \\
        [--sheet FRAMES_DIR] [--out DIR]

--val is a folder with `images/` and `labels/` (any depth; a label is
`labels/<path of the image>.txt`), as overhead4_merge.py writes one; the truth
is each label's class 2 lines. --model is an overhead-4 checkpoint and its
class 2 is scored at --conf. --pose adds yolo11x-pose's person class (COCO 0),
the incumbent, at 0.25 and at 0.1. A prediction is right if it overlaps an
unmatched player at IoU >= 0.5 (`match`, greedy, most confident first); the
counts are over every frame, and a frame with no label file is skipped.

--curves takes the runs' results.csv files (or NAME=PATH) and writes
`curves.png` to --out (default .): train and val box, cls and dfl loss and
mAP50-95 by epoch, a colour per run, val dashed. --sheet takes a folder of
frames and writes `sheet.jpg`, the model's player boxes at conf 0.25 over 40 of
them, the same ones for the same folder.
"""

import argparse
import csv
import random
import sys
from pathlib import Path

_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

PLAYER_CLASS = 2
POSE_PERSON = 0
IOU_MATCH = 0.5
SHEET_FRAMES = 40
SUFFIXES = (".jpg", ".jpeg", ".png")
LOSSES = ("box", "cls", "dfl")
MAP = "metrics/mAP50-95(B)"


def _iou(a: tuple, b: tuple) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    if w <= 0 or h <= 0:
        return 0.0
    inter = w * h
    return inter / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter)


def match(pred: list[tuple], truth: list[tuple], iou: float = IOU_MATCH) -> tuple[int, int, int]:
    """Greedy matching of predictions to truth: each prediction, most confident
    first, takes the unmatched truth box it overlaps most if that is at least
    ``iou``. Boxes are (x0, y0, x1, y1[, conf]); a prediction without a
    confidence ranks last. Returns (tp, fp, fn)."""
    free = list(range(len(truth)))
    tp = 0
    for p in sorted(pred, key=lambda b: -(b[4] if len(b) > 4 else 0.0)):
        best = max(free, key=lambda i: _iou(p, truth[i]), default=None)
        if best is not None and _iou(p, truth[best]) >= iou:
            free.remove(best)
            tp += 1
    return tp, len(pred) - tp, len(free)


def truth_players(label: str, w: int, h: int) -> list[tuple]:
    """The class 2 lines of a YOLO label as pixel boxes."""
    out = []
    for line in label.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0] != str(PLAYER_CLASS):
            continue
        cx, cy, bw, bh = (float(v) for v in parts[1:5])
        out.append(((cx - bw / 2) * w, (cy - bh / 2) * h, (cx + bw / 2) * w, (cy + bh / 2) * h))
    return out


def precision_recall(tp: int, fp: int, fn: int) -> tuple[float, float]:
    return tp / (tp + fp) if tp + fp else 0.0, tp / (tp + fn) if tp + fn else 0.0


def _frames(images: Path) -> list[Path]:
    return sorted(p for p in images.rglob("*") if p.suffix.lower() in SUFFIXES)


def predict(model, path: Path, cls: int, conf: float) -> tuple[list[tuple], tuple[int, int]]:
    """``model``'s class ``cls`` boxes on one image as (x0, y0, x1, y1, conf), and
    the image's (w, h)."""
    res = model.predict(str(path), conf=conf, classes=[cls], verbose=False)[0]
    h, w = res.orig_shape
    boxes = [] if res.boxes is None else [
        (*map(float, b), float(c)) for b, c in zip(res.boxes.xyxy.cpu().numpy(),
                                                   res.boxes.conf.cpu().numpy())]
    return boxes, (w, h)


def evaluate(model, val: Path, cls: int, confs: list[float]) -> dict[float, tuple[int, int, int]]:
    """Summed (tp, fp, fn) over the frames of ``val`` at each confidence. The model
    runs once, at the lowest."""
    totals = {c: [0, 0, 0] for c in confs}
    for path in _frames(val / "images"):
        label = val / "labels" / path.relative_to(val / "images").with_suffix(".txt")
        if not label.is_file():
            continue
        boxes, (w, h) = predict(model, path, cls, min(confs))
        truth = truth_players(label.read_text(), w, h)
        for c in confs:
            for i, v in enumerate(match([b for b in boxes if b[4] >= c], truth)):
                totals[c][i] += v
    return {c: tuple(t) for c, t in totals.items()}


def read_results(path: Path) -> dict[str, list[float]]:
    """A results.csv as columns by name; ultralytics pads its headers with spaces."""
    with open(path, newline="") as fh:
        rows = list(csv.reader(fh))
    names = [h.strip() for h in rows[0]]
    cols: dict[str, list[float]] = {n: [] for n in names}
    for row in rows[1:]:
        for n, v in zip(names, row):
            try:
                cols[n].append(float(v))
            except ValueError:
                cols[n].append(float("nan"))
    return cols


def plot_curves(runs: dict[str, dict[str, list[float]]], dest: Path) -> None:
    """Train and val box, cls and dfl loss and mAP50-95 by epoch, one colour per run."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(2, 2, figsize=(12, 8))
    panels = [(f"{k} loss", [(f"train/{k}_loss", "-"), (f"val/{k}_loss", "--")]) for k in LOSSES]
    panels.append(("mAP50-95", [(MAP, "-")]))
    for ax, (title, series) in zip(axes.flat, panels):
        for i, (name, cols) in enumerate(runs.items()):
            for col, style in series:
                if col in cols:
                    ax.plot(cols.get("epoch") or range(len(cols[col])), cols[col], style,
                            color=f"C{i}", label=f"{name} {col.split('/')[0]}"
                            if len(series) > 1 else name)
        ax.set_title(title)
        ax.set_xlabel("epoch")
        ax.grid(alpha=0.3)
        ax.legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(dest, dpi=110)
    plt.close(fig)


def _run_name(arg: str) -> tuple[str, Path]:
    """``NAME=PATH``, or a results.csv named for its folder."""
    if "=" in arg:
        name, path = arg.split("=", 1)
        return name, Path(path)
    return Path(arg).resolve().parent.name, Path(arg)


def write_sheet(model, frames: Path, dest: Path, conf: float) -> int:
    """The model's player boxes over a sample of ``frames``, as a contact sheet."""
    import cv2

    import label_players

    paths = _frames(frames)
    pick = sorted(random.Random(0).sample(range(len(paths)), min(SHEET_FRAMES, len(paths))))
    shown = []
    for i in pick:
        img = cv2.imread(str(paths[i]))
        if img is None:
            continue
        boxes, (w, h) = predict(model, paths[i], PLAYER_CLASS, conf)
        starts = [{"x0": b[0], "y0": b[1], "x1": b[2], "y1": b[3]} for b in boxes]
        shown.append((img, {"payload": {"w": w, "h": h, "starts": starts, "stones": []}}))
    if shown:
        cv2.imwrite(str(dest), label_players.contact_sheet(shown))
    return len(shown)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--val", type=Path, metavar="DIR", help="a folder with images/ and labels/")
    ap.add_argument("--model", metavar="PT", help="an overhead-4 checkpoint (player is class 2)")
    ap.add_argument("--pose", metavar="PT", help="yolo11x-pose, scored on its person class")
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--curves", nargs="+", metavar="RESULTS_CSV")
    ap.add_argument("--sheet", type=Path, metavar="FRAMES_DIR")
    ap.add_argument("--out", type=Path, default=Path("."), metavar="DIR",
                    help="where curves.png and sheet.jpg go (default .)")
    args = ap.parse_args(argv)
    if not (args.val or args.curves or args.sheet):
        ap.error("give --val, --curves or --sheet")
    if (args.val or args.sheet) and not args.model:
        ap.error("--val and --sheet need --model")
    if args.val and not (args.val / "images").is_dir():
        print(f"no images/ in {args.val}", file=sys.stderr)
        return 2
    args.out.mkdir(parents=True, exist_ok=True)

    if args.model:
        from ultralytics import YOLO

        model = YOLO(args.model)
    if args.val:
        scored = [("player (class 2)", model, PLAYER_CLASS, [args.conf])]
        if args.pose:
            scored.append(("pose person (class 0)", YOLO(args.pose), POSE_PERSON, [0.25, 0.1]))
        for name, m, cls, confs in scored:
            for conf, (tp, fp, fn) in evaluate(m, args.val, cls, confs).items():
                p, r = precision_recall(tp, fp, fn)
                print(f"{name} at {conf}: precision {p:.3f} recall {r:.3f} (tp {tp} fp {fp} fn {fn})")
    if args.curves:
        runs = dict(_run_name(a) for a in args.curves)
        plot_curves({n: read_results(p) for n, p in runs.items()}, args.out / "curves.png")
        print(f"wrote {args.out / 'curves.png'}")
    if args.sheet:
        n = write_sheet(model, args.sheet, args.out / "sheet.jpg", args.conf)
        print(f"wrote {args.out / 'sheet.jpg'} ({n} frames)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
