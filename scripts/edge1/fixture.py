#!/usr/bin/env python
"""edge1: the 11/03 sheet 4 changeover as a profile fixture (tests/fixtures/profiles).

Keyframe stone counts per panel over a window of a kept recording, counted by
the production detector (imgsz 448, conf 0.30) two ways -- every stone on the
sheet (``samples_on_sheet``, before the back line) and only those in play
(``samples``, `PanelSetup.in_play`) -- with each panel's back line as found, and
every stone it excluded.

    python fixture.py VIDEO FROM_S TO_S OUT.json
"""

import json
import sys

from curling_score import analyze, weights as W
from curling_score.game import profile
from curling_score.ingest import frames as F
from curling_score.live.session import VideoPipeline

path, t0, t1, out = sys.argv[1], float(sys.argv[2]), float(sys.argv[3]), sys.argv[4]
setups = VideoPipeline(skip_longview=True, line=False).calibrate(path, 900.0).setups
detector, _, _ = analyze.load_models(W.default_path(), 448, None, skip_longview=True, skip_line=True)
on_sheet, in_play, excluded = [], [], []
for t, frame in F.keyframe_sweep(path, start_s=t0, end_s=t1):
    panels = {n: setups[n].crop(frame) for n in ("top", "bottom")}
    playable = {n: profile.lighting.is_playable(p) for n, p in panels.items()}
    found = detector.find_stones_each([panels["top"], panels["bottom"]],
                                      [setups["top"].calib, setups["bottom"].calib])
    a, b = {}, {}
    for n, stones in zip(("top", "bottom"), found):
        s1 = setups[n].on_sheet(stones) if playable[n] else []
        s2 = setups[n].in_play(stones) if playable[n] else []
        a[n], b[n] = len(s1), len(s2)
        excluded += [{"t": round(t, 2), "panel": n, "x_px": round(s.x_px, 1), "y_px": round(s.y_px, 1),
                      "area_px": round(s.area_px, 1), "color": s.color,
                      "confidence": round(float(s.confidence), 3)}
                     for s in s1 if s not in s2]
    on_sheet.append([round(t, 2), a["top"], a["bottom"], int(playable["top"]), int(playable["bottom"])])
    in_play.append([round(t, 2), b["top"], b["bottom"], int(playable["top"]), int(playable["bottom"])])
doc = {
    "video_id": path.rsplit("/", 1)[-1].split(".")[0],
    "what": (f"11/03 Tuesday Super League sheet 4, kept live recording {t0:.0f}-{t1:.0f} s (7 pm game end 7 "
             "to 9 pm game end 2): keyframe stone counts per panel (t, top, bottom, top_playable, "
             "bottom_playable) by overhead-3 at imgsz 448 conf 0.30, of the stones in play (samples, "
             "PanelSetup.in_play) and of every stone on the sheet (samples_on_sheet), with each panel's back "
             "line from a calibration over the first 900 s and the stones it excluded"),
    "back_lines": {n: (None if setups[n].back_line is None else setups[n].back_line.to_json())
                   for n in ("top", "bottom")},
    "back_line_errors": {n: setups[n].back_line_error for n in ("top", "bottom")},
    "excluded": excluded,
    "samples": in_play,
    "samples_on_sheet": on_sheet,
}
json.dump(doc, open(out, "w"), separators=(",", ":"))
print("excluded", len(excluded), excluded[:20])
print("back lines", doc["back_lines"], doc["back_line_errors"])
