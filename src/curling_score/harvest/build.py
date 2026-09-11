"""Assemble the YOLO tree from a manifest and a pool of crops.

A pure function of (manifest, pool): same inputs, same tree, every time. That
is the property ds8 lacked -- its set was whatever the pipeline happened to
find that day, so the hand corrections keyed to its frame names could never be
replayed. Here the manifest names the frames and the pool holds the pixels, and
nothing else gets a vote.

The split directories are cleared first. A rebuild after dropping a video must
not leave that video's frames sitting in the tree, silently training on games
the manifest says are not in the set.
"""

import shutil
from pathlib import Path

from curling_score.harvest import manifest as M
from curling_score.train import dataset


def build(doc, pool_dir, out_dir, splits=("train", "val")) -> dict:
    """Write images and label files for every frame the manifest names."""
    pool_dir, out_dir = Path(pool_dir), Path(out_dir)
    for split in splits:
        for kind in ("images", "labels"):
            d = out_dir / kind / split
            if d.exists():
                shutil.rmtree(d)
            d.mkdir(parents=True, exist_ok=True)

    stats = {"written": 0, "missing": 0, "labels": 0, "empty": 0}
    for split, cand in M.iter_frames(doc):
        src = pool_dir / cand.video_id / f"{cand.stem}.jpg"
        if not src.is_file():
            stats["missing"] += 1
            continue
        shutil.copyfile(src, out_dir / "images" / split / f"{cand.stem}.jpg")
        # "".join, so a frame with no stones gets a genuinely empty file rather
        # than a stray newline: ultralytics reads that as a background image.
        (out_dir / "labels" / split / f"{cand.stem}.txt").write_text(
            "".join(lab.render() + "\n" for lab in cand.labels))
        stats["written"] += 1
        stats["labels"] += len(cand.labels)
        if not cand.labels:
            stats["empty"] += 1

    dataset.write_yaml(out_dir)
    return stats
