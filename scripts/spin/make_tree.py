"""A YOLO-pose training tree from a labelled handle-point wave.

    ./.venv/bin/python scripts/spin/make_tree.py --wave ~/curling-work/spin/wave1 \\
        --edits datasets/spin/edits/*.json --out ~/curling-work/spin/tree1 \\
        --hold PHbZ3EKOhMI_e3s15 --hold ...

One class (the stone) with two keypoints, neck then grip end. Sittings are
merged in file-name order, later ones winning, so a frame re-done in a later
sitting takes its last answer; a frame flagged unclear or occluded carries no
points and is left out. Train and val are split by rock, never by frame, so the
two frames of one rock land on the same side. ``--hold`` keeps whole rocks out
of both, for testing a rate on frames the model never saw.

The box is the detector's stone box raised to take in the handle -- ds13c's box
often stops at the cap -- and the keypoints are marked visible. A horizontal
flip leaves the neck the neck and the grip the grip (flip_idx [0, 1]): a
mirrored handle is a handle turned the other way, which is still a handle.
"""
import argparse
import json
import random
import shutil
import sys
from pathlib import Path

from PIL import Image


def merged(paths):
    points, flags = {}, {}
    for p in sorted(paths, key=lambda p: Path(p).name):
        d = json.loads(Path(p).read_text())
        for stem, pt in (d.get("points") or {}).items():
            points[stem] = pt
            flags.pop(stem, None)
        for stem, f in (d.get("flags") or {}).items():
            flags[stem] = f
            points.pop(stem, None)
    return points, flags


def rock_of(stem):
    return stem.rsplit("_", 1)[0]


def label_line(item, pt, size):
    """One YOLO-pose row: class, box centre/size, then (x, y, visible) per point,
    all as fractions of the image."""
    W, H = size
    x0, y0, x1, y1 = item["stone_box"]
    w = x1 - x0
    top = min(y0, pt["neck"][1], pt["grip"][1]) - 0.06 * w
    xs = [x0, x1, pt["grip"][0]]
    bx0, bx1 = max(0.0, min(xs)), min(W, max(xs))
    by0, by1 = max(0.0, top), min(H, y1)
    vals = [0, (bx0 + bx1) / 2 / W, (by0 + by1) / 2 / H, (bx1 - bx0) / W, (by1 - by0) / H]
    for key in ("neck", "grip"):
        vals += [pt[key][0] / W, pt[key][1] / H, 2]
    return " ".join(f"{v:.6f}" if isinstance(v, float) else str(v) for v in vals)


def build(wave, edits, out, hold=(), val_frac=0.2, seed=1):
    wave, out = Path(wave).expanduser(), Path(out).expanduser()
    items = {it["stem"]: it for it in json.loads((wave / "items.json").read_text())}
    points, flags = merged(edits)
    hold = set(hold)
    rocks = sorted({rock_of(s) for s in points if rock_of(s) not in hold})
    rng = random.Random(seed)
    rng.shuffle(rocks)
    n_val = max(1, round(len(rocks) * val_frac))
    val_rocks = set(rocks[:n_val])
    if out.exists():
        shutil.rmtree(out)
    counts = {"train": 0, "val": 0, "held": 0}
    for split in ("train", "val"):
        (out / "images" / split).mkdir(parents=True)
        (out / "labels" / split).mkdir(parents=True)
    for stem, pt in sorted(points.items()):
        if rock_of(stem) in hold:
            counts["held"] += 1
            continue
        split = "val" if rock_of(stem) in val_rocks else "train"
        src = wave / "images" / f"{stem}.png"
        shutil.copy(src, out / "images" / split / src.name)
        with Image.open(src) as im:
            size = im.size
        (out / "labels" / split / f"{stem}.txt").write_text(label_line(items[stem], pt, size) + "\n")
        counts[split] += 1
    (out / "data.yaml").write_text(
        f"path: {out}\ntrain: images/train\nval: images/val\n"
        "kpt_shape: [2, 3]\nflip_idx: [0, 1]\nnames:\n  0: stone\n")
    (out / "split.json").write_text(json.dumps(dict(
        counts=counts, flagged=len(flags), val_rocks=sorted(val_rocks), held=sorted(hold),
        edits=[Path(p).name for p in edits]), indent=1) + "\n")
    return counts, len(flags)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wave", required=True)
    ap.add_argument("--edits", nargs="+", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--hold", action="append", default=[], help="a rock to keep out, e.g. PHbZ3EKOhMI_e3s15")
    ap.add_argument("--val-frac", type=float, default=0.2)
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()
    counts, n_flags = build(args.wave, args.edits, args.out, args.hold, args.val_frac, args.seed)
    print(f"{counts} ({n_flags} flagged frames left out)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
