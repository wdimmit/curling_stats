#!/usr/bin/env python3
"""The 2026-11-07 DFW folds: each base model's training frames plus the labels from
dfwcurling.dimmit.net/label, validated on held-out DFW frames only, so the epoch
is chosen on Dallas. Symlinks only; stdlib only.

    python3 make_folds_1107.py --root /data/wdd/curling

  dfw1b  (overhead) dfw1a's fold (train + val) + the 100 overhead frames; val = every
         HOLD_EVERY-th of them by video and time.
  dfw2c  (long)     dfw2b's fold (train + its 10 DFW val) + the 150 release frames;
         val as above.
  bdfw1  (broom)    broom tree b3all's train + the 75 DFW broom frames; val as above.
"""

import argparse
import json
from pathlib import Path

HOLD_EVERY = 7


def link(src: Path, dst: Path):
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.is_symlink() or dst.exists():
        dst.unlink()
    dst.symlink_to(src.resolve())


def add_split(src_root: Path, split: str, out: Path, to: str):
    n = 0
    for img in sorted((src_root / "images" / split).glob("*")):
        lbl = src_root / "labels" / split / (img.stem + ".txt")
        link(img, out / "images" / to / img.name)
        if lbl.exists():
            link(lbl, out / "labels" / to / lbl.name)
        n += 1
    return n


def add_export(folder: Path, rows_file: str, out: Path):
    rows = [json.loads(line) for line in (folder / rows_file).read_text().splitlines() if line.strip()]
    rows.sort(key=lambda r: ((r.get("origin") or {}).get("video") or (r.get("origin") or {}).get("video_id") or "",
                             (r.get("origin") or {}).get("t") or 0))
    counts = {"train": 0, "val": 0}
    for i, r in enumerate(rows):
        split = "val" if i % HOLD_EVERY == HOLD_EVERY // 2 else "train"
        link(folder / "images" / f"{r['id']}.jpg", out / "images" / split / f"{r['id']}.jpg")
        link(folder / "labels" / f"{r['id']}.txt", out / "labels" / split / f"{r['id']}.txt")
        counts[split] += 1
    return counts


def yaml(out: Path, names: dict):
    (out / "data.yaml").write_text(f"path: {out}\ntrain: images/train\nval: images/val\nnames:\n"
                                   + "".join(f"  {k}: {v}\n" for k, v in names.items()))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    a = ap.parse_args()
    r = a.root
    exp = r / "dfw-1107" / "export"
    stones = {0: "red_stone", 1: "yellow_stone"}

    out = r / "dfw-1107" / "folds" / "dfw1b"
    base = r / "dfw1" / "folds" / "all"
    n = add_split(base, "train", out, "train") + add_split(base, "val", out, "train")
    got = add_export(exp / "boxes" / "member" / "overhead", "member.jsonl", out)
    yaml(out, stones)
    print("dfw1b: base", n, "+ dfw", got)

    out = r / "dfw-1107" / "folds" / "dfw2c"
    base = r / "dfw2" / "folds" / "b"
    n = add_split(base, "train", out, "train") + add_split(base, "val", out, "train")
    got = add_export(exp / "boxes" / "member" / "side", "member.jsonl", out)
    yaml(out, stones)
    print("dfw2c: base", n, "+ dfw", got)

    out = r / "dfw-1107" / "folds" / "bdfw1"
    base = r / "broom_trees" / "b3all"
    n = add_split(base, "train", out, "train")
    got = add_export(exp / "brooms", "brooms.jsonl", out)
    yaml(out, {0: "broom_head"})
    print("bdfw1: base", n, "+ dfw", got)


if __name__ == "__main__":
    main()
