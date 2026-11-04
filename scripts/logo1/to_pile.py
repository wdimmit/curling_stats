#!/usr/bin/env python
"""logo1: the first pass's hits not trained on, as a pile label_fp.py can publish.

logo_mine.py's first pass found 222 empty-sheet panels overhead-2 boxed a stone
on; 35 of them (the ice logo in the dark) went into overhead-3 with empty
labels. The rest are mostly players between games -- heads, shoes, broom heads,
jackets -- where a real stone in a hand or by the button may be among them, so
they go to /label for a member to box rather than into training as empty.
Saved like red1's pick.py: the whole frame and a row in
``<root>/harvest/fp/<video>/frames.jsonl``.

    python to_pile.py --logo DIR --timelines DIR --videos DIR [DIR..] --skip trained.txt --root DIR
"""

import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--logo", type=Path, required=True)
    ap.add_argument("--timelines", type=Path, required=True)
    ap.add_argument("--videos", type=Path, nargs="+", required=True)
    ap.add_argument("--skip", type=Path, required=True)
    ap.add_argument("--root", type=Path, required=True)
    a = ap.parse_args()
    import cv2

    from curling_score.fpframes import store

    skip = set(a.skip.read_text().split())
    calib, titles = {}, {}
    for p in sorted(a.timelines.glob("tl_*.json")):
        doc = json.loads(p.read_text())
        vid = doc["source"]["video_id"]
        calib.setdefault(vid, doc["calibration"])
        titles.setdefault(vid, doc["source"].get("title"))
    rows = [json.loads(line) for f in sorted(a.logo.glob("c*/picked.jsonl")) for line in open(f)]
    saved_at = datetime.now(timezone.utc).isoformat()
    n = 0
    for r in rows:
        if r["stem"] in skip:
            continue
        video = next((d / f"{r['video']}.mp4" for d in a.videos if (d / f"{r['video']}.mp4").is_file()), None)
        img = store.read_frame(video, r["t"]) if video else None
        if img is None:
            print("no frame", r["video"], r["t"])
            continue
        rect = [int(v) for v in calib[r["video"]][r["panel"]]["rect"]]
        box = [r["px"][0] + rect[0], r["px"][1] + rect[1], r["px"][2] + rect[0], r["px"][3] + rect[1]]
        d = store.video_dir(a.root, r["video"])
        d.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(d / f"{r['stem']}.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, store.JPEG_QUALITY])
        row = {"stem": r["stem"], "check": "logo1.player", "camera": "overhead", "where": r["panel"],
               "t": r["t"], "box": box, "top_estimated": False, "color": r["color"], "conf": r["conf"],
               "info": {}, "rect": rect, "game": None, "end": None, "house": None,
               "video_id": r["video"], "title": titles.get(r["video"]), "pipeline": "logo1-mine",
               "models": {"overhead": "overhead-2", "ref": "rfdetr-nano448", "old": "ds17b"},
               "job": "logo1", "saved_at": saved_at}
        with open(d / "frames.jsonl", "a") as fh:
            fh.write(json.dumps(row) + "\n")
        n += 1
    subprocess.run(["chmod", "-R", "a+rX", str(a.root)], check=False)
    print("saved", n)


if __name__ == "__main__":
    main()
