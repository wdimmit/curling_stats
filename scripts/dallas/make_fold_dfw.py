#!/usr/bin/env python3
"""A long-camera fold for DFW: long-1's training frames plus the user's exported DFW
frames, with a validation set of DFW frames only, so the epoch is chosen on Dallas.

The held-out frames are those the previous model never trained on: every exported
frame not in --seen (the earlier export) from the first game, and the last
--hold-dfw3 frames of 46wEMds_tYQ by time. Symlinks only; stdlib only.

    python3 make_fold_dfw.py --base /data/wdd/curling/long-1/folds/all \
        --export dfw2/export2/boxes/member/side --seen dfw2/export1/boxes/member/side/member.jsonl \
        --out /data/wdd/curling/dfw2/folds/b
"""

import argparse
import json
from pathlib import Path


def link(src: Path, dst: Path):
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.is_symlink() or dst.exists():
        dst.unlink()
    dst.symlink_to(src.resolve())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", type=Path, required=True)
    ap.add_argument("--export", type=Path, required=True, help="labels_export --member output for the side camera")
    ap.add_argument("--seen", type=Path, required=True, help="member.jsonl of the export the last model trained on")
    ap.add_argument("--hold-dfw3", type=int, default=7)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    seen = {json.loads(line)["id"] for line in a.seen.read_text().splitlines() if line.strip()}
    rows = [json.loads(line) for line in (a.export / "member.jsonl").read_text().splitlines() if line.strip()]
    live = sorted((r for r in rows if r["origin"].get("video_id") == "46wEMds_tYQ"), key=lambda r: r["origin"]["t"])
    val = {r["id"] for r in rows if r["id"] not in seen and r["origin"].get("video_id") != "46wEMds_tYQ"}
    val |= {r["id"] for r in live[-a.hold_dfw3:]} if a.hold_dfw3 else set()
    n = {"base": 0, "dfw_train": 0, "dfw_val": 0}
    for img in sorted((a.base / "images/train").iterdir()):
        link(img, a.out / "images/train" / img.name)
        link(a.base / "labels/train" / f"{img.stem}.txt", a.out / "labels/train" / f"{img.stem}.txt")
        n["base"] += 1
    for r in rows:
        split = "val" if r["id"] in val else "train"
        link(a.export / "images" / f"{r['id']}.jpg", a.out / f"images/{split}" / f"{r['id']}.jpg")
        link(a.export / "labels" / f"{r['id']}.txt", a.out / f"labels/{split}" / f"{r['id']}.txt")
        n["dfw_" + split] += 1
    (a.out / "data.yaml").write_text(f"path: {a.out}\ntrain: images/train\nval: images/val\n"
                                     "names:\n  0: red_stone\n  1: yellow_stone\n")
    (a.out / "val_ids.json").write_text(json.dumps(sorted(val)))
    print(n)


if __name__ == "__main__":
    main()
