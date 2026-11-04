#!/usr/bin/env python3
"""mv1: the logo phantoms, before and after -- from two `cin1/mine.py` passes.

The phantom spots are the manifest's static cells (overhead-3's boxes that sat
still for 30%+ of the game). Per house and model: the frames with a sure box on
a spot, sure boxes on spots per frame, and the frames with no sure box at all
(the empty house the segmenter needs between ends). A real stone that comes to
rest on a spot counts here too, so neither model reaches zero.

    python3 phantoms.py datasets/mv1/manifest.json overhead-3=mined.json mv1a=mined-mv1a.json
"""

import json
import sys

SURE = 0.3


def cell(b):
    x0, y0, x1, y1 = b["xyxy"]
    return int((x0 + x1) / 2 // 12), int((y0 + y1) / 2 // 12)


def main():
    man = json.load(open(sys.argv[1]))
    static = {p: {tuple(c) for c in cs} for p, cs in man["selection"]["static_cells"].items()}
    for arg in sys.argv[2:]:
        name, path = arg.split("=", 1)
        frames = json.load(open(path))["frames"]
        print(name)
        for panel in ("top", "bottom"):
            fr = [f for f in frames if f["panel"] == panel]
            on = empty = boxes = 0
            for f in fr:
                sure = [b for b in f["boxes"] if b["conf"] >= SURE]
                spot = [b for b in sure if cell(b) in static[panel]]
                on += bool(spot)
                boxes += len(spot)
                empty += not sure
            n = len(fr)
            print(f"  {panel:6s} {n} frames: box on a logo spot {on / n:6.1%}  spot boxes/frame {boxes / n:.2f}  "
                  f"empty house {empty / n:6.1%}")


if __name__ == "__main__":
    main()
