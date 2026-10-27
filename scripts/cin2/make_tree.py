#!/usr/bin/env python
"""A reviewed Cincinnati end-camera set (cin2, cin3) as a training folder beside ds13e's,
plus the yaml to train on them together.

The review's edits are applied to a copy of the set: rejects removed, every
frame kept, an emptied one as a negative, and added stones sized by their row
from the manifest's `add_box` (`labels.size_at`, review-reject-all eea577d0).
Every fifth frame in time goes to val.

    python scripts/cin2/make_tree.py --set DIR/set --manifest datasets/cin2/manifest.json \
        --edits datasets/cin2/edits/label-edits-cin2-train-2026102714225.json --trees TREES
    python scripts/cin2/make_tree.py --set DIR/set --manifest datasets/cin3/manifest.json \
        --edits datasets/cin3/edits/label-edits-cin3-train-2026102716110.json --trees TREES \
        --folder cin3 --also cin2 --name cin3a
"""

import argparse
import json
import shutil
from pathlib import Path

DS13E = ("wave2", "hack1", "broom1", "kink1")
VAL_EVERY = 5


def main():
    from curling_score.train import labels

    ap = argparse.ArgumentParser()
    ap.add_argument("--set", type=Path, required=True)
    ap.add_argument("--manifest", type=Path, required=True)
    ap.add_argument("--edits", type=Path, required=True)
    ap.add_argument("--trees", type=Path, required=True)
    ap.add_argument("--name", default="cin2a")
    ap.add_argument("--folder", default="cin2")
    ap.add_argument("--also", nargs="*", default=[], help="Cincinnati folders already built")
    a = ap.parse_args()

    manifest = json.loads(a.manifest.read_text())
    edits = labels.parse_edits_full(json.loads(a.edits.read_text()))
    assert edits.scope == manifest["scope"], (edits.scope, manifest["scope"])
    work = a.trees / f"{a.folder}-applied"
    if work.exists():
        shutil.rmtree(work)
    shutil.copytree(a.set, work)
    size = {f["stem"]: tuple(map(tuple, f["add_box"])) for f in manifest["frames"]}
    counts = labels.apply_edits(work, "train", edits.reject, edits.add, keep_empty=True,
                                box_for=size.get, reviewed=edits.reviewed, drop_unreviewed=True)
    print(counts)

    tree = a.trees / a.folder
    if tree.exists():
        shutil.rmtree(tree)
    stems = [f["stem"] for f in sorted(manifest["frames"], key=lambda f: f["t"])]
    for i, stem in enumerate(stems):
        split = "val" if i % VAL_EVERY == VAL_EVERY - 1 else "train"
        for kind, ext in (("images", "jpg"), ("labels", "txt")):
            src = work / kind / "train" / f"{stem}.{ext}"
            if src.exists():
                (tree / kind / split).mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, tree / kind / split / src.name)
    sets = DS13E + tuple(a.also) + (a.folder,)
    (a.trees / f"{a.name}.yaml").write_text(
        f"# {a.name}: ds13e's data ({', '.join(DS13E)}) and Cincinnati's end cameras "
        f"({', '.join(sets[len(DS13E):])})\n"
        f"path: {a.trees.resolve()}\n"
        f"train: [{', '.join(f'{s}/images/train' for s in sets)}]\n"
        f"val: [{', '.join(f'{s}/images/val' for s in sets)}]\n"
        "names:\n  0: red_stone\n  1: yellow_stone\n")
    boxes = sum(len(p.read_text().split()) // 5 for p in (tree / "labels").rglob("*.txt"))
    print(f"{a.folder}: {len(list((tree / 'images/train').glob('*.jpg')))} train, "
          f"{len(list((tree / 'images/val').glob('*.jpg')))} val, {boxes} boxes")


if __name__ == "__main__":
    main()
