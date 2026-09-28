#!/usr/bin/env python3
"""Training sets for ds16: ds11 train, ds15, and ds16, with one game held out.

Fold ``8un0-JBKkLA`` holds out that game's ds16 frames -- a sheet 4 game, so
the portrait button is scored on a game the model never saw -- and trains on
everything else. Fold ``all`` holds nothing out: the candidate to replace
ds15a. ds11's val split stays the model-selection set, exactly as it was for
ds11a and ds15a, and ds15 goes in whole, as it did for ds15a, so the only
change from ds15a's training set is ds16.

Everything is symlinks, so a fold costs nothing to rebuild. Stdlib only, to run
on the GPU box without the package installed.

    python3 make_folds.py --ds11 /data/wdd/curling/ds11/set \
        --ds15 /data/wdd/curling/ds15/set --ds15-excluded /data/wdd/curling/ds15/excluded.json \
        --ds16 /data/wdd/curling/ds16/build/set --out /data/wdd/curling/ds16/folds
"""

import argparse
import json
from pathlib import Path

HELD = ("8un0-JBKkLA",)
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
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    skip15 = {f["stem"] for f in json.loads(a.ds15_excluded.read_text())["frames"]}
    sources = [
        ("ds11", a.ds11, sorted((a.ds11 / "images/train").glob("*.jpg"))),
        ("ds15", a.ds15, [p for p in sorted((a.ds15 / "images/train").glob("*.jpg"))
                          if p.stem not in skip15]),
        # --apply --drop-unreviewed has already removed every frame nobody ticked.
        ("ds16", a.ds16, sorted((a.ds16 / "images/train").glob("*.jpg"))),
    ]
    for held in HELD + ("all",):
        root = a.out / held
        n = {"ds11": 0, "ds15": 0, "ds16": 0, "heldout": 0}
        for name, base, imgs in sources:
            for img in imgs:
                split = "heldout" if name == "ds16" and video_of(img.stem) == held else "train"
                link(img, root / "images" / split / img.name)
                link(base / "labels/train" / f"{img.stem}.txt",
                     root / "labels" / split / f"{img.stem}.txt")
                n[name if split == "train" else "heldout"] += 1
        link(a.ds11 / "images/val", root / "images/val")
        link(a.ds11 / "labels/val", root / "labels/val")
        (root / "curling.yaml").write_text(YAML)
        (root / "heldout.yaml").write_text(YAML.replace("val: images/val", "val: images/heldout"))
        print(held, n)


if __name__ == "__main__":
    main()
