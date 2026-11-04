#!/usr/bin/env python3
"""mv1: overhead-3's training fold plus the reviewed Martensville frames.

overhead-3, the production overhead model, trained on the laptop's
`overhead-3/folds/all` (overhead-2's set plus 45 logo negatives), with ds11
val choosing the epoch. This links exactly that fold's frames and adds mv1's,
so the only change from overhead-3 is Martensville.

Everything is symlinks to the real files. Stdlib only, to run without the
package.

    python3 make_fold.py --base /data/wdd/curling/overhead-3/folds/all \
        --add /data/wdd/scratch/czone/mv1/set1/set --out /data/wdd/curling/mv1/folds/all
"""

import argparse
from pathlib import Path


def link(src: Path, dst: Path):
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.is_symlink() or dst.exists():
        dst.unlink()
    dst.symlink_to(src.resolve())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", type=Path, required=True, help="overhead-3's fold, with data.yaml")
    ap.add_argument("--add", type=Path, nargs="+", required=True, help="sets with images/train + labels/train")
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    n = {}
    for name, base in [("base", a.base)] + [(p.name if p.name != "set" else p.parent.parent.name, p) for p in a.add]:
        imgs = sorted((base / "images/train").iterdir())
        for img in imgs:
            lab = base / "labels/train" / f"{img.stem}.txt"
            if not lab.exists():
                raise SystemExit(f"no label for {img}")
            link(img, a.out / "images/train" / img.name)
            link(lab, a.out / "labels/train" / lab.name)
        n[name] = len(imgs)
    for d in ("images/val", "labels/val"):
        link(a.base / d, a.out / d)
    yaml = (a.base / "data.yaml").read_text().splitlines()
    yaml = [f"path: {a.out}" if line.startswith("path:") else line for line in yaml]
    (a.out / "data.yaml").write_text("\n".join(yaml) + "\n")
    print(n, "->", len(list((a.out / "images/train").iterdir())), "frames")


if __name__ == "__main__":
    main()
