#!/usr/bin/env python3
"""Training folds: a shipped model's set plus a member's own boxes
(`labels_export.py --member`), for either camera.

Fold ``heldout`` trains on the base set and the member's frames from every
video but --hold, whose frames become images/heldout: how the new model does on
games the member labelled and it never saw. Fold ``all`` trains on all of them,
the candidate to ship. Both keep the base set's val, so model selection is
unchanged from the model being replaced.

Everything is symlinks; a label is found by swapping /images/ for /labels/ in
its image's path, as Ultralytics does. Stdlib only, to run on the GPU box.

    python3 member_folds.py --member .../boxes/member/overhead \\
        --train /data/wdd/curling/ds17/folds/all/images/train \\
        --train /data/wdd/curling/ds17b/build/set/images/train \\
        --val /data/wdd/curling/ds11/set/images/val \\
        --hold pe328PUupXs XbBwOnjkhpY 4RrNWSeNnMU --out /data/wdd/curling/overhead-1/folds
"""

import argparse
import json
from pathlib import Path

NAMES = "names:\n  0: red_stone\n  1: yellow_stone\n"


def label_of(img: Path) -> Path:
    s = str(img)
    i = s.rfind("/images/")
    return Path(s[:i] + "/labels/" + s[i + len("/images/"):]).with_suffix(".txt")


def link(src: Path, dst: Path):
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.is_symlink() or dst.exists():
        dst.unlink()
    dst.symlink_to(src.resolve())


def add(img: Path, root: Path, split: str):
    link(img, root / "images" / split / img.name)
    link(label_of(img), root / "labels" / split / f"{img.stem}.txt")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--member", type=Path, required=True,
                    help="boxes/member/<camera> from labels_export.py --member")
    ap.add_argument("--train", type=Path, action="append", required=True,
                    help="an images/ dir of the base set's training frames (repeatable)")
    ap.add_argument("--val", type=Path, action="append", required=True,
                    help="an images/ dir of the base set's val frames (repeatable)")
    ap.add_argument("--hold", nargs="*", default=[], help="videos held out of fold heldout")
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    rows = [json.loads(line) for line in (a.member / "member.jsonl").read_text().splitlines()]
    video = {r["id"]: r["origin"].get("video_id") for r in rows}
    base = [p for d in a.train for p in sorted(d.glob("*.jpg"))]
    val = [p for d in a.val for p in sorted(d.glob("*.jpg"))]
    mine = sorted((a.member / "images").glob("*.jpg"))
    for fold in ("heldout", "all"):
        root = a.out / fold
        n = {"base": 0, "member": 0, "heldout": 0, "val": 0}
        for p in base:
            add(p, root, "train")
            n["base"] += 1
        for p in val:
            add(p, root, "val")
            n["val"] += 1
        for p in mine:
            held = fold == "heldout" and video.get(p.stem) in a.hold
            add(p, root, "heldout" if held else "train")
            n["heldout" if held else "member"] += 1
        yaml = f"path: {root}\ntrain: images/train\nval: images/val\n" + NAMES
        (root / "data.yaml").write_text(yaml)
        (root / "heldout.yaml").write_text(yaml.replace("val: images/val", "val: images/heldout"))
        print(fold, n)


if __name__ == "__main__":
    main()
