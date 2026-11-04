#!/usr/bin/env python
"""edge1: zoom sheets for the eye check -- each candidate crop's only hit at
3x, beside the whole crop small, numbered for a verdict list.

    python zoom.py RUN_DIR OUT_DIR --only LIST
"""
import argparse
import json
from collections import defaultdict
from pathlib import Path

from PIL import Image, ImageDraw

ap = argparse.ArgumentParser()
ap.add_argument("run", type=Path)
ap.add_argument("out", type=Path)
ap.add_argument("--only", type=Path, required=True)
a = ap.parse_args()
a.out.mkdir(parents=True, exist_ok=True)
by = defaultdict(list)
for line in (a.run / "hits.jsonl").read_text().splitlines():
    h = json.loads(line)
    by[(h["video"], h["panel"], h["t"])].append(h)
rows, picked = [], []
for stem in a.only.read_text().split():
    vid, panel = stem[:11], "top" if stem[12] == "t" else "bottom"
    t = float(stem[14:].split("_edge1")[0].replace("_", ".", 1))
    hs = by[(vid, panel, t)]
    if len(hs) != 1:
        continue
    h = hs[0]
    im = Image.open(a.run / "images" / f"{stem}.jpg").convert("RGB")
    R = 40
    box = (int(h["x_px"]) - R, int(h["y_px"]) - R, int(h["x_px"]) + R, int(h["y_px"]) + R)
    zoom = im.crop(box).resize((240, 240), Image.LANCZOS)
    thumb = im.resize((im.width * 240 // im.height, 240))
    ImageDraw.Draw(thumb).ellipse(
        [(h["x_px"] - 14) * 240 / im.height, (h["y_px"] - 14) * 240 / im.height,
         (h["x_px"] + 14) * 240 / im.height, (h["y_px"] + 14) * 240 / im.height],
        outline=(255, 0, 0), width=2)
    tile = Image.new("RGB", (240 + thumb.width + 4, 262), "white")
    tile.paste(zoom, (0, 22))
    tile.paste(thumb, (244, 22))
    ImageDraw.Draw(tile).text((2, 4), f"{len(picked)}: {h['color'][0]}{h['conf']:.2f} {panel[0]} y{h['y_m']:+.1f}",
                              fill="black")
    picked.append(stem)
    rows.append(tile)
cols, per = 6, 30
for n in range(0, len(rows), per):
    chunk = rows[n:n + per]
    w, hh = max(t.width for t in chunk), 262
    sheet = Image.new("RGB", (cols * (w + 6), ((len(chunk) + cols - 1) // cols) * (hh + 6)), "white")
    for i, t in enumerate(chunk):
        sheet.paste(t, ((i % cols) * (w + 6), (i // cols) * (hh + 6)))
    sheet.save(a.out / f"zoom{n // per:02d}.jpg", quality=85)
(a.out / "zoom-index.txt").write_text("\n".join(picked) + "\n")
print(len(picked), "single-hit candidates")
