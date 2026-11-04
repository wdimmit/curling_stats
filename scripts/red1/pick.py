#!/usr/bin/env python
"""red1: pick 150 frames from red_mine.py's candidates and save them as a pile
label_fp.py can publish (``<root>/harvest/fp/<video>/frames.jsonl`` + jpgs).

    python pick.py --mined DIR --sources sources.json --videos DIR [DIR..] --root DIR [--sheet out.jpg]

  clothing  75: an overhead-1 red box with no Nano box near it. 50 that ds17b
            did not make (overhead-1's own) and 25 it did. One per thing: in a
            video, a box within SAME_PLACE_M of one already picked is the same
            thing (a sign, a chair) and is passed over.
  release   75: one frame per red throw, the one Nano is surest of. 45 where
            ds17b had the stone (overhead-1's own miss) and 30 where neither did.

At most PER_VIDEO of each kind per video, picked round-robin across videos so
no one game fills the set. Deterministic: candidates sort by video, then time.
"""

import argparse
import json
import subprocess
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

SHARES = {"clothing": (50, 25), "release": (45, 30)}   # (own, shared)
PER_VIDEO = 3
SAME_PLACE_M = 0.5
MIN_GAP_S = 20.0


def dist(a, b):
    return float(np.hypot(a["det"]["x"] - b["det"]["x"], a["det"]["y"] - b["det"]["y"]))


def round_robin(by_video, n, accept):
    out, idx = [], {v: 0 for v in by_video}
    vids = sorted(by_video)
    while len(out) < n:
        moved = False
        for v in vids:
            lst = by_video[v]
            while idx[v] < len(lst):
                c = lst[idx[v]]
                idx[v] += 1
                if accept(c, out):
                    out.append(c)
                    moved = True
                    break
            if len(out) >= n:
                break
        if not moved:
            break
    return out


def pick(cands, kind, own_n, shared_n, taken):
    def ok_common(c, out):
        mine = [o for o in taken + out if o["video"] == c["video"]]
        if sum(o["kind"] == kind for o in mine) >= PER_VIDEO:
            return False
        for o in mine:
            if abs(o["t"] - c["t"]) < MIN_GAP_S:
                return False
            if kind == "clothing" and o["kind"] == kind and o["panel"] == c["panel"] and dist(o, c) < SAME_PLACE_M:
                return False
        return True

    got = []
    for own, n in ((True, own_n), (False, shared_n)):
        pool = [c for c in cands if c["kind"] == kind and (c["old_has"] if kind == "release" else not c["old_has"]) == own]
        if kind == "release":
            best = {}
            for c in pool:
                k = (c["source"], c["t_rel"])
                if k not in best or c["det"]["conf"] > best[k]["det"]["conf"]:
                    best[k] = c
            pool = list(best.values())
        by_video = defaultdict(list)
        for c in sorted(pool, key=lambda c: (c["video"], c["t"])):
            by_video[c["video"]].append(c)
        got += round_robin(by_video, n, lambda c, out: ok_common(c, got + out))
    return got


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mined", type=Path, required=True)
    ap.add_argument("--sources", type=Path, required=True)
    ap.add_argument("--videos", type=Path, nargs="+", required=True)
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--sheet", type=Path)
    a = ap.parse_args()
    import cv2

    from curling_score.fpframes import store

    titles = {s["video"]: s["title"] for s in json.loads(a.sources.read_text())}
    cands = []
    for p in sorted(a.mined.glob("*.json")):
        cands += json.loads(p.read_text())["cands"]
    # A video two sources share was swept once, but its throws are per source.
    picked = []
    for kind, (own, shared) in SHARES.items():
        got = pick(cands, kind, own, shared, picked)
        print(kind, len(got), "own", sum(c["old_has"] != (kind == "clothing") for c in got))
        picked += got

    saved_at = datetime.now(timezone.utc).isoformat()
    tiles = []
    for c in picked:
        video = next((d / f"{c['video']}.mp4" for d in a.videos if (d / f"{c['video']}.mp4").is_file()), None)
        img = store.read_frame(video, c["t"]) if video else None
        if img is None:
            print("no frame", c["video"], c["t"])
            continue
        stem = f"{c['video']}_{c['panel']}_{c['t']:09.2f}".replace(".", "_") + "_red_" + c["kind"]
        d = store.video_dir(a.root, c["video"])
        d.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(d / f"{stem}.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, store.JPEG_QUALITY])
        row = {"stem": stem, "check": "red." + c["kind"], "camera": "overhead", "where": c["panel"],
               "t": c["t"], "box": c["box"], "top_estimated": False, "color": "red",
               "conf": c["det"]["conf"], "info": {k: c.get(k) for k in ("old_has", "new_yellow", "t_rel", "shot", "end", "n_new", "n_ref")},
               "rect": c["rect"], "game": c.get("game"), "end": c.get("end"), "house": None,
               "video_id": c["video"], "title": titles.get(c["video"]), "pipeline": "red1-mine",
               "models": {"overhead": "overhead-1", "ref": "rfdetr-nano448", "old": "ds17b"},
               "job": "red1", "saved_at": saved_at}
        with open(d / "frames.jsonl", "a") as fh:
            fh.write(json.dumps(row) + "\n")
        if a.sheet:
            x0, y0, x1, y1 = (int(v) for v in c["box"])
            cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
            t = img.copy()
            cv2.rectangle(t, (x0, y0), (x1, y1), (0, 255, 0) if c["kind"] == "release" else (255, 0, 255), 2)
            h, w = t.shape[:2]
            xa, ya = max(0, min(w - 240, cx - 120)), max(0, min(h - 180, cy - 90))
            tile = t[ya:ya + 180, xa:xa + 240].copy()
            cv2.putText(tile, f"{c['kind'][0]}{'*' if c['old_has'] != (c['kind'] == 'clothing') else ''} {c['det']['conf']:.2f}",
                        (4, 14), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)
            tiles.append(tile)
    if a.sheet and tiles:
        cols = 10
        while len(tiles) % cols:
            tiles.append(np.zeros_like(tiles[0]))
        rows = [np.hstack(tiles[i:i + cols]) for i in range(0, len(tiles), cols)]
        cv2.imwrite(str(a.sheet), np.vstack(rows), [cv2.IMWRITE_JPEG_QUALITY, 85])
    subprocess.run(["chmod", "-R", "a+rX", str(a.root)], check=False)
    print("saved", len(picked))


if __name__ == "__main__":
    main()
