#!/usr/bin/env python
"""logo1: empty-sheet panels overhead-2 boxes a stone on (2026-10-30).

overhead-2 reads the red "legrand" logo in sheet 1's top panel as a red stone
once play stops, which made a one-rock game after the last game on two harness
videos (AEqLTgM25Tc, _OGoqF5W9-I). ds17b and RF-DETR Nano do not. Every
keyframe outside the games -- before the first, between, and after the last,
GAP_S clear of an end -- both overhead panels: a panel where overhead-2 boxes
anything and neither ds17b nor Nano boxes anything at all is an empty sheet
overhead-2 is wrong about. Saved as the panel crop with an empty label, the
training format of the member folds, at most PER_VIDEO per video, MIN_GAP_S
apart.

--lit: the 11 videos the first pass found the logo on, lit frames only (mean
grey above LIT), top panel, whether or not overhead-2 boxes the logo there --
the first pass found it only in the dark, but on the harness it fired with the
lights on too (_OGoqF5W9-I). Still nothing from ds17b or Nano.

    python logo_mine.py --timelines DIR --videos DIR --out DIR --new overhead-2.pt \
        --old ds17b.pt --ref nano.pth --sources a,b
"""

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, "/data/curling/stone/ds18/code/scripts/ds17")
import mine_hats as MH  # noqa: E402

from curling_score.detect import yolo  # noqa: E402
from curling_score.ingest import frames as F  # noqa: E402

CONF, IMGSZ = 0.30, 448
GAP_S = 60.0
PER_VIDEO = 4
MIN_GAP_S = 120.0
PANEL = {"top": "t", "bottom": "b"}
LIT = 90
LOGO_VIDEOS = ("nNa2sobjvFw OFpX-ZolgBU GwSqCBTjrog 2Cp3bNjAEWg mRa2zqFXjvw dmljQzQr1xo XnC-moPkja8 "
               "npkVHNuuWQc _tu6K2R2uZM uTEMpg_bwhI _b0O23g2cgo").split()


def outside_games(doc, length_s):
    ends = sorted((e["start_s"], e["end_s"]) for g in doc["games"] for e in g.get("ends") or [])
    out, t = [], 0.0
    for s, e in ends:
        if s - GAP_S > t:
            out.append((t, s - GAP_S))
        t = max(t, e + GAP_S)
    if length_s > t:
        out.append((t, length_s))
    return out


def main():
    ap = argparse.ArgumentParser()
    for k in ("timelines", "videos", "out"):
        ap.add_argument(f"--{k}", type=Path, required=True)
    for k in ("new", "old", "ref", "sources"):
        ap.add_argument(f"--{k}", required=True)
    ap.add_argument("--lit", action="store_true")
    a = ap.parse_args()
    from curling_score.detect import rfdetr as RD
    new = yolo.YoloDetector(a.new, conf=CONF, imgsz=IMGSZ, device=0)
    old = yolo.YoloDetector(a.old, conf=CONF, imgsz=IMGSZ, device=0)
    ref = RD.RfdetrDetector(a.ref, conf=CONF, imgsz=IMGSZ, device=0, fp16=True)
    (a.out / "images").mkdir(parents=True, exist_ok=True)
    (a.out / "labels").mkdir(parents=True, exist_ok=True)
    keep = set(a.sources.split(","))
    seen = set()
    for p in sorted(a.timelines.glob("tl_*.json")):
        if p.stem[3:] not in keep:
            continue
        doc = json.loads(p.read_text())
        vid = doc["source"]["video_id"]
        video = a.videos / f"{vid}.mp4"
        if vid in seen or not video.is_file() or (a.lit and vid not in LOGO_VIDEOS):
            continue
        seen.add(vid)
        panels = {k: c for k, c in doc["calibration"].items()
                  if k in (("top",) if a.lit else PANEL) and c and c.get("rect") and c.get("center_px")}
        calibs = {k: MH.calib_of(c) for k, c in panels.items()}
        cap = cv2.VideoCapture(str(video))
        length_s = cap.get(cv2.CAP_PROP_FRAME_COUNT) / max(cap.get(cv2.CAP_PROP_FPS), 1)
        cap.release()
        cands = []
        for t0, t1 in outside_games(doc, length_s):
            for t, img in F.keyframe_sweep(video, start_s=t0, end_s=t1):
                for k, c in panels.items():
                    x, y, w, h = (int(v) for v in c["rect"])
                    crop = img[y:y + h, x:x + w]
                    if a.lit and crop.mean() < LIT:
                        continue
                    fn = new.find_stones_batch([crop], calibs[k])[0]
                    if not fn and not a.lit:
                        continue
                    if old.find_stones_batch([crop], calibs[k])[0] or ref.find_stones_batch([crop], calibs[k])[0]:
                        continue
                    d = max(fn, key=lambda d: d.confidence) if fn else None
                    cands.append((float(d.confidence) if d else 0.0, t, k, crop, d))
        # The surest first, then spread out in time.
        picked = []
        for conf, t, k, crop, d in sorted(cands, key=lambda c: -c[0]):
            if len(picked) >= PER_VIDEO:
                break
            if any(abs(t - q[1]) < MIN_GAP_S for q in picked):
                continue
            picked.append((conf, t, k, crop, d))
        rows = []
        for conf, t, k, crop, d in picked:
            stem = f"{vid}_{PANEL[k]}_{t:09.2f}".replace(".", "_") + ("_logo1lit" if a.lit else "_logo1")
            cv2.imwrite(str(a.out / "images" / f"{stem}.jpg"), crop, [cv2.IMWRITE_JPEG_QUALITY, 95])
            (a.out / "labels" / f"{stem}.txt").write_text("")
            half = float(np.sqrt(d.area_px)) / 2 if d else 0
            rows.append({"stem": stem, "video": vid, "t": round(t, 2), "panel": k, "conf": round(conf, 3),
                         "color": d.color if d else None,
                         "px": [round(d.x_px - half), round(d.y_px - half), round(d.x_px + half),
                                round(d.y_px + half)] if d else None})
        with open(a.out / "picked.jsonl", "a") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")
        print(vid, "outside-game hits", len(cands), "picked", len(rows), flush=True)


if __name__ == "__main__":
    main()
