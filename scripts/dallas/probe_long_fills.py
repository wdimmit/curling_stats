#!/usr/bin/env python
"""Run the long camera's far-hog timing and guard placement over a finished chart.

    python probe_long_fills.py VIDEO TIMELINE OUT.json [--ends 1,2,...]

No pipeline: each rock of the chart (its t_hog_s, colour) is handed to
`fartime.time_far_crossings_from_long_camera`, and each end to `longguards.add`,
against the converted video. Writes per rock: the far crossing, the split it
makes with t_hog_s, and the end's travel; per end: the long stones it added.
"""

import argparse
import copy
import json
from pathlib import Path
from types import SimpleNamespace

from curling_score import clubs
from curling_score.game import clips as game_clips
from curling_score.game import fartime, hogtime, longguards


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video", type=Path)
    ap.add_argument("timeline", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--ends")
    a = ap.parse_args()
    tl = json.loads(a.timeline.read_text())
    views = game_clips.sideviews_from_calibration(tl["calibration"])
    want = {int(x) for x in a.ends.split(",")} if a.ends else None
    rocks, ends = [], []
    for e in tl["games"][0]["ends"]:
        if want and e["number"] not in want:
            continue
        view = views[hogtime.CAMERA_FOR[e["house"]]]
        shots = [SimpleNamespace(missing=s.get("missing", False), color=s.get("color"),
                                 t_hog_s=s.get("t_hog_s"), release=None, far_crossing=None,
                                 number=s["number"], travel=s.get("travel_m"), type=s.get("shot_type"))
                 for s in e["shots"]]
        n = fartime.time_far_crossings_from_long_camera(shots, str(a.video), view)
        for s in shots:
            t_far = s.far_crossing.t if s.far_crossing is not None else None
            rocks.append({"end": e["number"], "shot": s.number, "color": s.color, "type": s.type,
                          "t_hog": s.t_hog_s, "t_far": t_far, "travel_m": s.travel,
                          "split": None if t_far is None or s.t_hog_s is None else round(t_far - s.t_hog_s, 2)})
        built = copy.deepcopy(e)
        g = longguards.add(built, view, longguards.stones_reader(str(a.video), view),
                           clubs.DALLAS.guards_from_long_m)
        ends.append({"end": e["number"], "far_timed": n, "long_stones": g,
                     "by_shot": [{"shot": s["number"], "type": s.get("shot_type"),
                                  "was_placed": e["shots"][i].get("delivered_stone_index") is not None,
                                  "now_placed": s.get("delivered_stone_index") is not None,
                                  "long": [{k: st[k] for k in ("color", "x", "y")}
                                           for st in s.get("stones") or [] if st.get("source") == "long"]}
                                 for i, s in enumerate(built["shots"])]})
        print(f"end {e['number']}: far {n}/{len(shots)}, long stones {g}", flush=True)
    a.out.write_text(json.dumps({"rocks": rocks, "ends": ends}, indent=1))


if __name__ == "__main__":
    main()
