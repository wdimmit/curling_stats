#!/usr/bin/env python
"""Build the acceptance fixture for the painted far tripwire.

For each of the six panels of the three hand-marked games: the panel's
calibration median (exactly what ``profile.calibrate_panel`` sees) as a PNG,
and its calibration. For each hand-marked rock: its arrival track. Tracks come
from the phase-3 probe outputs (``<cache>/<video>-phase3.json``), written by a
replay on 2026-09-22; the marks are ``datasets/hogmarks``.

    PYTHONPATH=src python scripts/phase3/build_far_tripwire_fixture.py
"""

import json
import pickle
from pathlib import Path

import cv2

from curling_score import analyze as A
from curling_score.game import profile
from curling_score.ingest import frames as F

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "tests" / "fixtures" / "far_tripwire"
GAMES = {"AEqLTgM25Tc": Path.home() / ".cache/curling_replay",
         "VXU9xwmugRg": Path.home() / ".cache/curling_score",
         "hOKZoeJNTpM": Path.home() / ".cache/curling_score"}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    marks = [m for m in json.loads((REPO / "datasets/hogmarks/receiving-controls.json").read_text())["marks"]]
    marks += [dict(m, video="hOKZoeJNTpM") for m in
              json.loads((REPO / "datasets/hogmarks/hOKZoeJNTpM-receiving.json").read_text())["marks"]]
    panels_out, cases = {}, []
    for vid, root in GAMES.items():
        setups, _p = pickle.loads((root / f"setups-{vid}.pkl").read_bytes())
        frames = F.sample_keyframes(root / "videos" / f"{vid}.mp4",
                                    count=A.CALIB_FRAMES, stride=A.CALIB_STRIDE)
        for name, st in setups.items():
            median = profile.panel_median(frames, st.rect, name)
            png = f"plate-{vid}-{name}.png"
            cv2.imwrite(str(OUT / png), median)
            c = st.calib
            panels_out[f"{vid}/{name}"] = {
                "plate": png, "center_px": list(c.center_px), "px_per_m": c.px_per_m,
                "edge_erosion_px": c.edge_erosion_px, "residual_m": c.residual_m,
                "flipped": c.flipped}
        tracks = {(e["end"], r["shot"]): r["track"]
                  for e in json.loads((root / f"{vid}-phase3.json").read_text()) for r in e["shots"]}
        for m in marks:
            if m["video"] != vid or m.get("crossing_s") is None:
                continue
            cases.append({"video": vid, "panel": m["destination_panel"], "end": m["end"],
                          "shot": m["shot"], "mark_s": m["crossing_s"],
                          "track": tracks[(m["end"], m["shot"])]})
    (OUT / "cases.json").write_text(json.dumps({"panels": panels_out, "cases": cases}))
    print(f"wrote {len(panels_out)} panels, {len(cases)} cases to {OUT}")


if __name__ == "__main__":
    main()
