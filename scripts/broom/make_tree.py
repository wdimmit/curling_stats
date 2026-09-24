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
    """The frames some session named, with the boxes it gave them."""
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
