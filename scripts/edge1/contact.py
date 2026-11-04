#!/usr/bin/env python
"""edge1: contact sheets of edge_mine.py's crops, for the eye check.

Every crop with its picked hit circled red and every other detection in the
same keyframe and panel circled yellow -- so a real stone left in the picture
(a practice throw, stones being gathered after a game) stands out. A crop goes
into training with its empty label only if there is no stone anywhere in it.

    python contact.py RUN_DIR OUT_DIR [--per-sheet 24] [--only LIST] [--scale 0.5]
"""

import argparse
import json
from collections import defaultdict
from pathlib import Path

from PIL import Image, ImageDraw


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--per-sheet", type=int, default=24)
    ap.add_argument("--only", type=Path, help="stems to show, one a line")
    ap.add_argument("--scale", type=float, default=1.0)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    by_frame = defaultdict(list)
    for line in (a.run / "hits.jsonl").read_text().splitlines():
        h = json.loads(line)
        by_frame[(h["video"], h["panel"], h["t"])].append(h)
    stems = sorted(p.stem for p in (a.run / "images").glob("*.jpg"))
    if a.only:
        wanted = set(a.only.read_text().split())
        stems = [s for s in stems if s in wanted]
    tiles = []
    for stem in stems:
        vid, panel, rest = stem[:11], stem[12], stem[14:]
        t = float(rest.split("_edge1")[0].replace("_", ".", 1))
        panel = "top" if panel == "t" else "bottom"
        im = Image.open(a.run / "images" / f"{stem}.jpg").convert("RGB")
        d = ImageDraw.Draw(im)
        hits = sorted(by_frame[(vid, panel, t)], key=lambda h: (not h["past_back"], -h["conf"]))
        for i, h in enumerate(hits):
            r = max(14, (h["area_px"] ** 0.5) * 0.8)
            d.ellipse([h["x_px"] - r, h["y_px"] - r, h["x_px"] + r, h["y_px"] + r],
                      outline=(255, 0, 0) if i == 0 else (255, 200, 0), width=3)
        if a.scale != 1.0:
            im = im.resize((round(im.width * a.scale), round(im.height * a.scale)))
        top = hits[0] if hits else None
        label = f"{stem[:-6]}  {top['color'][0]}{top['conf']:.2f} y{top['y_m']:+.2f}" if top else stem
        tiles.append((label, im))
    cols = 8
    for n in range(0, len(tiles), a.per_sheet):
        chunk = tiles[n:n + a.per_sheet]
        w = max(im.width for _, im in chunk)
        h = max(im.height for _, im in chunk)
        rows = (len(chunk) + cols - 1) // cols
        sheet = Image.new("RGB", (cols * (w + 6), rows * (h + 30)), "white")
        for i, (label, im) in enumerate(chunk):
            x, y = (i % cols) * (w + 6), (i // cols) * (h + 30)
            sheet.paste(im, (x, y + 28))
            ImageDraw.Draw(sheet).text((x + 2, y + 2), f"{n + i}: {label}", fill="black")
        sheet.save(a.out / f"sheet{n // a.per_sheet:02d}.jpg", quality=88)
    (a.out / "index.json").write_text(json.dumps([lbl for lbl, _ in tiles], indent=0))
    print(len(tiles), "crops")


if __name__ == "__main__":
    main()
