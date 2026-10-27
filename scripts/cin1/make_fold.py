#!/usr/bin/env python3
"""cin1: ds17b's training set plus the reviewed Cincinnati frames, as one fold.

ds17b, the production overhead model, trained on ds11 train, ds15 (less its
excluded frames), ds16, ds17 and ds17b, with ds11 val choosing the epoch. This
links exactly those, from the laptop's copies -- the Ryzen's ds17 has since
been relabelled with the stone_unknown class (ds18r) -- and adds cin1, so the
only change from ds17b is Cincinnati.

Everything is symlinks. Stdlib only, to run without the package.

    python3 make_fold.py --curling /data/wdd/curling --cin1 /data/wdd/scratch/cincinnati/cin1/set1/set \
        --out /data/wdd/curling/cin1/folds/all
"""

import argparse
import json
from pathlib import Path

YAML = "train: images/train\nval: images/val\nnames:\n  0: red_stone\n  1: yellow_stone\n"


def link(src: Path, dst: Path):
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.is_symlink() or dst.exists():
        dst.unlink()
    dst.symlink_to(src.resolve())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--curling", type=Path, required=True, help="the root holding ds11/ ds15/ ds16/ ds17/ ds17b/")
    ap.add_argument("--cin1", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    c = a.curling
    skip15 = {f["stem"] for f in json.loads((c / "ds15/excluded.json").read_text())["frames"]}
    sources = [
        ("ds11", c / "ds11/set"),
        ("ds15", c / "ds15/set"),
        ("ds16", c / "ds16/build/set"),
        ("ds17", c / "ds17/build/set"),
        ("ds17b", c / "ds17b/build/set"),
        ("cin1", a.cin1),
    ]
    n = {}
    for name, base in sources:
        imgs = [p for p in sorted((base / "images/train").glob("*.jpg"))
                if not (name == "ds15" and p.stem in skip15)]
        for img in imgs:
            link(img, a.out / "images/train" / img.name)
            link(base / "labels/train" / f"{img.stem}.txt", a.out / "labels/train" / f"{img.stem}.txt")
        n[name] = len(imgs)
    link(c / "ds11/set/images/val", a.out / "images/val")
    link(c / "ds11/set/labels/val", a.out / "labels/val")
    (a.out / "curling.yaml").write_text(YAML)
    print(n, "->", len(list((a.out / "images/train").iterdir())), "frames")


if __name__ == "__main__":
    main()
