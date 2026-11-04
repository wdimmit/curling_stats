#!/usr/bin/env python
"""edge1 A/B: whole-recording activity profiles counted two ways, for replaying
the segmentation offline (``ab_segment.py``).

Per recording, one keyframe sweep by the production detector (imgsz 448, conf
0.30) under this checkout's calibration, which finds the back line: each
keyframe's counts of every stone on the sheet (``on_sheet``, what production
counts) and of those in play (``in_play``, without the stones wholly past the
back line), with every stone the back line excluded. One JSON per video.

    python ab_profile.py LIST OUT_DIR       (LIST: "<cache root> <video id>")
"""

import json
import sys
import time
from pathlib import Path

from curling_score import analyze, weights as W
from curling_score.game import profile
from curling_score.ingest import frames as F
from curling_score.live.session import VideoPipeline

lst, out = Path(sys.argv[1]), Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
detector, _, _ = analyze.load_models(W.default_path(), 448, None, skip_longview=True, skip_line=True)
pipeline = VideoPipeline(skip_longview=True, line=False)
for line in lst.read_text().splitlines():
    root, vid = line.split()
    dest = out / f"{vid}.json"
    if dest.exists():
        continue
    path = f"{root}/videos/{vid}.mp4"
    t0 = time.time()
    try:
        setups = pipeline.calibrate(path, 900.0).setups
    except Exception as exc:  # noqa: BLE001 - one bad video must not stop the sweep
        print(vid, "calibration failed:", exc, flush=True)
        continue
    old, new, excluded = [], [], []
    for t, frame in F.keyframe_sweep(path):
        panels = {n: setups[n].crop(frame) for n in ("top", "bottom")}
        lit = {n: profile.lighting.is_playable(p) for n, p in panels.items()}
        names = [n for n in ("top", "bottom") if lit[n]]
        found = detector.find_stones_each([panels[n] for n in names],
                                          [setups[n].calib for n in names])
        a, b = {"top": 0, "bottom": 0}, {"top": 0, "bottom": 0}
        for n, stones in zip(names, found):
            s1, s2 = setups[n].on_sheet(stones), setups[n].in_play(stones)
            a[n], b[n] = len(s1), len(s2)
            excluded += [[round(t, 2), n, round(s.x_px, 1), round(s.y_px, 1), round(s.area_px, 1),
                          s.color, round(float(s.confidence), 3)] for s in s1 if s not in s2]
        row = (round(t, 2), int(lit["top"]), int(lit["bottom"]))
        old.append([row[0], a["top"], a["bottom"], row[1], row[2]])
        new.append([row[0], b["top"], b["bottom"], row[1], row[2]])
    doc = {"video_id": vid,
           "back_lines": {n: None if setups[n].back_line is None else setups[n].back_line.to_json()
                          for n in ("top", "bottom")},
           "back_line_errors": {n: setups[n].back_line_error for n in ("top", "bottom")},
           "on_sheet": old, "in_play": new, "excluded": excluded}
    tmp = dest.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, separators=(",", ":")))
    tmp.rename(dest)
    print(vid, len(old), "keyframes", len(excluded), "excluded",
          {n: (doc["back_lines"][n] or {}).get("columns") for n in ("top", "bottom")},
          f"{time.time() - t0:.0f} s", flush=True)
