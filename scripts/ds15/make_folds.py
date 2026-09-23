#!/usr/bin/env python3
"""Leave-one-game-out training sets: ds11 train plus ds15 from the other games.

Frames of one match are strongly correlated, so a model trained on a rock and
scored on that same rock proves nothing. Each fold holds one of ds15's three
games out entirely -- its frames become that fold's ``heldout`` split -- and
trains on ds11's reviewed train split plus ds15's frames from the other two.
ds11's val split stays the model-selection set, exactly as it was for ds11a.
Fold ``all`` holds nothing out: the candidate to replace ds11a.

Everything is symlinks, so a fold costs nothing to rebuild. Stdlib only, to run
on the GPU box without the package installed.

    python3 make_folds.py --ds11 /data/wdd/curling/ds11/set \
        --ds15 /data/wdd/curling/ds15/set --excluded excluded.json \
        --out /data/wdd/curling/ds15/folds
"""

import argparse
import json
from pathlib import Path

GAMES = ("AEqLTgM25Tc", "VXU9xwmugRg", "hOKZoeJNTpM")
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
    ap.add_argument("--excluded", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    skip = {f["stem"] for f in json.loads(a.excluded.read_text())["frames"]}
    ds15 = [p for p in sorted((a.ds15 / "images/train").glob("*.jpg")) if p.stem not in skip]
    ds11_train = sorted((a.ds11 / "images/train").glob("*.jpg"))
    for held in GAMES + ("all",):
        root = a.out / held
        n = {"ds11": 0, "ds15": 0, "heldout": 0}
        for img in ds11_train:
            link(img, root / "images/train" / img.name)
            link(a.ds11 / "labels/train" / f"{img.stem}.txt", root / "labels/train" / f"{img.stem}.txt")
            n["ds11"] += 1
        for img in ds15:
            split = "heldout" if video_of(img.stem) == held else "train"
            link(img, root / "images" / split / img.name)
            link(a.ds15 / "labels/train" / f"{img.stem}.txt",
                 root / "labels" / split / f"{img.stem}.txt")
            n["ds15" if split == "train" else "heldout"] += 1
        link(a.ds11 / "images/val", root / "images/val")
        link(a.ds11 / "labels/val", root / "labels/val")
        (root / "curling.yaml").write_text(YAML)
        (root / "heldout.yaml").write_text(YAML.replace("val: images/val", "val: images/heldout"))
        print(held, n)


if __name__ == "__main__":
    main()
