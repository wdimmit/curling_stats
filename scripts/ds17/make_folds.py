#!/usr/bin/env python3
"""Training sets for ds17: ds11 train, ds15, ds16, ds17 (and ds17b), two games held out.

Fold ``heldout`` holds out ds17's frames from _OGoqF5W9-I (last season: the red
toque that outvoted three yellow releases) and 0SWB4g3SJoE (this season: the
most throws with a thrower's head moving with the stone), and trains on
everything else. Fold ``all`` holds nothing out: the candidate to replace
ds16a. ds11's val split stays the model-selection set, as for ds11a, ds15a and
ds16a, so the only change from ds16a's training set is ds17.

Everything is symlinks. Stdlib only, to run on the GPU box without the package.

    python3 make_folds.py --ds11 /data/wdd/curling/ds11/set \
        --ds15 /data/wdd/curling/ds15/set --ds15-excluded /data/wdd/curling/ds15/excluded.json \
        --ds16 /data/wdd/curling/ds16/build/set --ds17 /data/wdd/curling/ds17/build/set \
        --out /data/wdd/curling/ds17/folds
"""

import argparse
import json
from pathlib import Path

HELD = ("_OGoqF5W9-I", "0SWB4g3SJoE")
YAML = "train: images/train\nval: images/val\nnames:\n  0: red_stone\n  1: yellow_stone\n"


def link(src: Path, dst: Path):
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.is_symlink() or dst.exists():
        dst.unlink()
    dst.symlink_to(src.resolve())


def video_of(stem: str) -> str:
    # "<video_id>_<b|t>_<secs>_<frac>"; a YouTube id may itself contain "_".
    return stem.rsplit("_", 3)[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ds11", type=Path, required=True)
    ap.add_argument("--ds15", type=Path, required=True)
    ap.add_argument("--ds15-excluded", type=Path, required=True)
    ap.add_argument("--ds16", type=Path, required=True)
    ap.add_argument("--ds17", type=Path, required=True)
    ap.add_argument("--ds17b", type=Path, default=None,
                    help="round 2: arriving stones the round-1 model lost (in every fold)")
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    skip15 = {f["stem"] for f in json.loads(a.ds15_excluded.read_text())["frames"]}
    sources = [
        ("ds11", a.ds11, sorted((a.ds11 / "images/train").glob("*.jpg"))),
        ("ds15", a.ds15, [p for p in sorted((a.ds15 / "images/train").glob("*.jpg"))
                          if p.stem not in skip15]),
        ("ds16", a.ds16, sorted((a.ds16 / "images/train").glob("*.jpg"))),
        # --apply --drop-unreviewed has already removed every frame nobody ticked.
        ("ds17", a.ds17, sorted((a.ds17 / "images/train").glob("*.jpg"))),
    ]
    if a.ds17b:
        sources.append(("ds17b", a.ds17b, sorted((a.ds17b / "images/train").glob("*.jpg"))))
    for fold in ("heldout", "all"):
        root = a.out / fold
        n = {"ds11": 0, "ds15": 0, "ds16": 0, "ds17": 0, "ds17b": 0, "heldout": 0}
        for name, base, imgs in sources:
            for img in imgs:
                held = fold == "heldout" and name == "ds17" and video_of(img.stem) in HELD
                split = "heldout" if held else "train"
                link(img, root / "images" / split / img.name)
                link(base / "labels/train" / f"{img.stem}.txt",
                     root / "labels" / split / f"{img.stem}.txt")
                n["heldout" if held else name] += 1
        link(a.ds11 / "images/val", root / "images/val")
        link(a.ds11 / "labels/val", root / "labels/val")
        (root / "curling.yaml").write_text(YAML)
        (root / "heldout.yaml").write_text(YAML.replace("val: images/val", "val: images/heldout"))
        print(fold, n)


if __name__ == "__main__":
    main()
