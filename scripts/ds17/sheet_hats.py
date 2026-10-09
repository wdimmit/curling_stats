#!/usr/bin/env python
"""Contact sheet of mined throws: the throwing panel at each throw's most
confident suspect, every suspect in that frame ringed (magenta) with its
colour and confidence. For a look before anything is labelled.

    python scripts/ds17/sheet_hats.py --mined DIR --timelines DIR --videos DIR --out PNG [--top N] [--shadows]
"""
import argparse
import json
from pathlib import Path

import cv2

from curling_score.ingest import frames as F


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mined", type=Path, required=True)
    ap.add_argument("--timelines", type=Path, required=True)
    ap.add_argument("--videos", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--top", type=int, default=24)
    ap.add_argument("--shadows", action="store_true", help="only throws with a shadow track")
    ap.add_argument("--pick", type=Path, default=None, help="JSON list of [source, t_release] to show, in order")
    args = ap.parse_args()
    recs = [r for p in sorted(args.mined.glob("*.json")) for r in json.loads(p.read_text())["throws"]
            if r["suspects"] and (r["shadow_n"] or not args.shadows)]
    recs.sort(key=lambda r: (-r["shadow_n"], -r["max_conf"]))
    if args.pick:
        order = {(a, round(b, 2)): i for i, (a, b) in enumerate(json.loads(args.pick.read_text()))}
        recs = sorted((r for r in recs if (r["source"], round(r["t_release"], 2)) in order),
                      key=lambda r: order[(r["source"], round(r["t_release"], 2))])
    tiles = []
    for r in recs[: args.top]:
        cal = json.loads((args.timelines / f"tl_{r['source']}.json").read_text())["calibration"][r["panel"]]
        best = max(r["suspects"], key=lambda s: s["conf"])
        img = None
        for _t, im in F.window(args.videos / f"{r['video']}.mp4", best["t"], best["t"] + 0.12, 30.0,
                               crop=tuple(cal["rect"])):
            img = im.copy()
            break
        if img is None:
            continue
        for s in r["suspects"]:
            if abs(s["t"] - best["t"]) < 0.01:
                rad = max(8, int((s["area"] / 3.14) ** 0.5))
                cv2.circle(img, (int(s["x_px"]), int(s["y_px"])), rad, (255, 0, 255), 2)
                cv2.putText(img, f"{s['color'][0]}{s['conf']:.2f}", (int(s["x_px"]) + rad, int(s["y_px"])),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 0, 255), 1)
        label = f"{r['video'][:4]} e{r['end']}r{r['shot']} {r['color'][0]} {r['position'] or ''} sh{r['shadow_n']} t{best['t']:.1f}"
        cv2.putText(img, label, (3, 14), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 0, 0), 2)
        cv2.putText(img, label, (3, 14), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255, 255, 255), 1)
        tiles.append(cv2.resize(img, (220, int(220 * img.shape[0] / img.shape[1]))))
    if not tiles:
        print("nothing to show")
        return
    h = max(t.shape[0] for t in tiles)
    tiles = [cv2.copyMakeBorder(t, 0, h - t.shape[0], 0, 4, cv2.BORDER_CONSTANT, value=(255, 255, 255)) for t in tiles]
    rows = [cv2.hconcat(tiles[i:i + 8]) for i in range(0, len(tiles), 8)]
    w = max(r.shape[1] for r in rows)
    rows = [cv2.copyMakeBorder(r, 0, 4, 0, w - r.shape[1], cv2.BORDER_CONSTANT, value=(255, 255, 255)) for r in rows]
    cv2.imwrite(str(args.out), cv2.vconcat(rows))
    print(f"{len(tiles)} throws -> {args.out}")


if __name__ == "__main__":
    main()
