#!/usr/bin/env python3
"""cin1: the phantom red stones, before and after -- from two `mine.py` passes.

Per house: frames with any box at 0.3+, frames whose only 0.3+ boxes are red on
the 12-to-8 band (the lettering), and band reds at 0.3+ / 0.5+ per frame.

    python3 phantoms.py mined-ds17b.json mined-cin1a.json
"""

import json
import sys

BAND_M = (1.15, 1.95)


def summary(path):
    d = json.load(open(path))
    for panel in ("top", "bottom"):
        fr = [f for f in d["frames"] if f["panel"] == panel]
        band = lambda b: BAND_M[0] <= b["r"] <= BAND_M[1] and b["color"] == "red"
        any3 = only = b3 = b5 = 0
        for f in fr:
            sure = [b for b in f["boxes"] if b["conf"] >= 0.3]
            any3 += bool(sure)
            only += bool(sure) and all(band(b) for b in sure)
            b3 += sum(band(b) for b in sure)
            b5 += sum(band(b) and b["conf"] >= 0.5 for b in sure)
        n = len(fr)
        print(f"  {panel:6s} {n} frames: any box {any3 / n:6.1%}  only band reds {only / n:6.1%}  "
              f"band reds/frame 0.3+ {b3 / n:.2f}  0.5+ {b5 / n:.2f}")


for p in sys.argv[1:]:
    print(p)
    summary(p)
