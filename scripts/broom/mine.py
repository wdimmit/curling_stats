"""Find the shots where the broom model holds a pad still well off the thrown
line: the frames a hard-negative wave is cut from.

A real target is where the thrower aimed, so the line passes within a few
tens of centimetres of it. Sunday Open Doubles 10/04 sheet 3 (flag f_1Np1)
read a standing player's shoe and broom at the back of the house as the
target for four rocks, and the line missed it by 1.1-2.2 m every time. This
reads every shot's window as broomtime does (10 fps over [t_tee - 1, t_tee],
the same clusters), then measures each held pad against the published line.
A pad more than ``--miss`` metres off it that also looks weak, sits behind the
tee or shares the window with another held pad is a candidate (`suspect`),
and so is a shot where boxes under the pipeline's floor cluster behind the tee
(`weak_suspect`): the reviewer deletes the box on a shoe, keeps the one on a
real pad, and boxes a pad the model missed. Every cluster, held or passing, is
written out, so another rule can be tried without the video. It runs on a
machine that has the video.

    python scripts/broom/mine.py --timeline TL.json --video VID.mp4 \\
        --weights broom3.pt --out candidates/<sid>.json
    python scripts/broom/harvest.py ... --only candidates/<sid>.keys.json
"""
import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np

from curling_score.game import broomtime
from curling_score.game.linetime import HOG_Y
from curling_score.geometry import constants as C
from curling_score.geometry import sideview

CAMERA_FOR = {"top": "left", "bottom": "right"}     # game/hogtime.CAMERA_FOR


def view_of(entry: dict) -> sideview.SideView:
    """The view as the timeline calibrated it, centre line and all."""
    return sideview.SideView(
        rect=tuple(entry["rect"]), tee_row=entry["tee_row"], hog_row=entry["hog_row"],
        centre_col=entry.get("centre_col"),
        lat_px_per_m_at_tee=entry.get("lat_px_per_m_at_tee"),
        centre_line=tuple(entry["centre_line"]) if entry.get("centre_line") else None)


def line_x(shot: dict):
    """The thrown line's x(y), from the two points the timeline publishes:
    the throwing hog line and either the target broom's depth or the tee.
    None for a shot without a line."""
    line = shot.get("line") or {}
    hog = (line.get("at_hog") or {}).get("x")
    if hog is None:
        return None
    broom = shot.get("target_broom")
    if line.get("at_broom") and broom:
        y2, x2 = broom["y"], line["at_broom"]["x"]
    elif line.get("at_tee"):
        y2, x2 = 0.0, line["at_tee"]["x"]
    else:
        return None
    slope = (hog - x2) / (HOG_Y - y2)
    return lambda y: x2 + slope * (y - y2)


def clusters_of(samples, n_frames: int) -> list:
    """Every cluster of pads, as broomtime.pick_target forms them: the ones
    held still first, best first (the first is the one it would pick), then
    the passing ones by how often they were seen."""
    keep = [p for p in samples
            if abs(p[1]) <= broomtime.X_MAX_M
            and -C.R_12FT_M - broomtime.BEHIND_M <= p[2] <= C.TEE_TO_HOGLINE_M]
    clusters: list[list] = []
    for p in sorted(keep, key=lambda q: -q[3]):
        for c in clusters:
            cx = float(np.median([q[1] for q in c]))
            cy = float(np.median([q[2] for q in c]))
            if (abs(p[1] - cx) <= broomtime.CLUSTER_M
                    and abs(p[2] - cy) <= broomtime.CLUSTER_ALONG_M):
                c.append(p)
                break
        else:
            clusters.append([p])
    out = []
    for c in clusters:
        frames_in = len({q[0] for q in c})
        seen = frames_in / max(n_frames, broomtime.EXPECTED_FRAMES)
        x = float(np.median([q[1] for q in c]))
        y = float(np.median([q[2] for q in c]))
        held = seen > broomtime.MIN_SEEN
        out.append(((not held, -frames_in, math.hypot(x, y)),
                    {"x": round(x, 3), "y": round(y, 3), "seen": round(seen, 2),
                     "conf": round(float(np.median([q[3] for q in c])), 3),
                     "max_conf": round(max(q[3] for q in c), 3), "held": held}))
    return [h for _rank, h in sorted(out, key=lambda h: h[0])]


def held_pads(samples, n_frames: int) -> list:
    """Every pad held still, best first: the first is the one broomtime
    would pick."""
    return [c for c in clusters_of(samples, n_frames) if c["held"]]


# broom3 holds a real pad at 0.75 or better on 98% of shots (19 games, 2026-10-05);
# the shoes it took for pads held at 0.30-0.65, mostly behind the tee, and on
# half of them a real pad on the line was held as well.
SUSPECT_CONF = 0.75
SUSPECT_BEHIND_Y_M = -0.9
# Under the pipeline's floor the model still stirs on shoes now and then: a
# cluster of these behind the tee in a third of the window is a shoe on the
# edge of becoming a pad, and as worth a negative label as one over it.
WEAK_SEEN = 0.3


def suspect(held, miss_m: float) -> bool:
    """Whether a shot's held pads look like the shoes of f_1Np1: one well
    off the line that is also weak, behind the tee, or not alone. A strong
    pad off the line on its own is a target the throw missed."""
    return any(h["miss"] is not None and abs(h["miss"]) > miss_m
               and (h["conf"] < SUSPECT_CONF or h["y"] < SUSPECT_BEHIND_Y_M or len(held) > 1)
               for h in held)


def weak_suspect(weak) -> bool:
    """Whether a cluster under the pipeline's floor sat behind the tee for a
    good part of the window."""
    return any(w["seen"] >= WEAK_SEEN and w["y"] < SUSPECT_BEHIND_Y_M for w in weak)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeline", required=True)
    ap.add_argument("--video", required=True)
    ap.add_argument("--weights", required=True)
    ap.add_argument("--out", required=True, help="candidates JSON; the [game, end, shot] "
                    "keys for harvest --only go beside it as <stem>.keys.json")
    ap.add_argument("--floor", type=float, default=0.1,
                    help="also read boxes down to this confidence, below the pipeline's")
    ap.add_argument("--miss", type=float, default=1.0,
                    help="a held pad this far off the line, in metres, may be a candidate")
    args = ap.parse_args()

    from ultralytics import YOLO

    from curling_score.detect import broommodel, longview

    doc = json.loads(Path(args.timeline).expanduser().read_text())
    cal = doc["calibration"]
    views = {n: view_of(cal[n]) for n in ("left", "right")}
    video = Path(args.video).expanduser()
    model = YOLO(str(Path(args.weights).expanduser()))

    rows, n_read = [], 0
    for g in doc["games"]:
        for e in g["ends"]:
            view = views[CAMERA_FOR[e["house"]]]
            if not view.has_lateral:
                continue            # broomtime reads no brooms here either
            for s in e["shots"]:
                t = s.get("t_tee_s")
                if s.get("missing") or t is None or t < broomtime.WINDOW_S:
                    continue
                frames, _ = longview.decode(video, view.rect, t - broomtime.WINDOW_S, t,
                                            broomtime.FPS)
                if not len(frames):
                    continue
                n_read += 1
                samples = []
                for i, pads in enumerate(broommodel.find(model, frames, view, conf=args.floor)):
                    for p in pads:
                        x, y = view.to_house(p.col, p.row)
                        samples.append((i, x, y, p.conf))
                strong = [q for q in samples if q[3] >= broommodel.CONF_MIN]
                clusters = clusters_of(strong, len(frames))
                weak = clusters_of([q for q in samples if q[3] < broommodel.CONF_MIN], len(frames))
                fx = line_x(s)
                for c in clusters + weak:
                    c["miss"] = None if fx is None else round(fx(c["y"]) - c["x"], 3)
                held = [c for c in clusters if c["held"]]
                why = [k for k, hit in (("held", suspect(held, args.miss)),
                                        ("weak", weak_suspect(weak))) if hit]
                rows.append({"game": g["index"], "end": e["number"], "shot": s["number"],
                             "t_tee": t, "published": s.get("target_broom"),
                             "clusters": clusters, "weak": weak, "candidate": why})
                print(f"\r{n_read} shots read", end="", flush=True)

    out = Path(args.out).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"source": doc["source"], "weights": args.weights,
                               "miss_m": args.miss, "rows": rows}, indent=1) + "\n")
    keys = [[r["game"], r["end"], r["shot"]] for r in rows if r["candidate"]]
    out.with_suffix(".keys.json").write_text(json.dumps(keys) + "\n")
    n_held = sum(1 for r in rows if any(c["held"] for c in r["clusters"]))
    print(f"\n{n_read} shots, {n_held} with a pad held, {len(keys)} candidates -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
