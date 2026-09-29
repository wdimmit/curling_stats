"""The delivery read against today's start read, rock by rock, on real video:
what schema 8 changes about a line and what it costs.

    delivery_ab.py --timeline T.json --video V.mp4 --weights W.pt --out ab.json

Both use the same crossing, track and model, through the production
functions. The old start is ``linetime.find_start`` (its own 5 fps read); the
new is ``read_delivery``'s, from the one 10 fps read that also gives
``delivery_path`` its frames. Reported:

- the start, old against new: how far it moved, and whether its side (the
  hack call's evidence) changed;
- the delivery: how often it reaches the rest (in front of the hack) and
  1.5 m past the hog line, its points and bytes;
- seconds per rock for each read.

A run killed partway resumes from ``<out>.part.jsonl``.
"""
import argparse
import json
import statistics
import sys
import time
from pathlib import Path

import numpy as np

from curling_score.analyze import OTHER_HOUSE
from curling_score.detect import longview, sidemodel
from curling_score.game import hogtime, linetime
from curling_score.geometry import sideview


def view_from(e: dict) -> sideview.SideView:
    return sideview.SideView(
        rect=tuple(e["rect"]), tee_row=e["tee_row"], hog_row=e["hog_row"],
        centre_col=e.get("centre_col"), lat_px_per_m_at_tee=e.get("lat_px_per_m_at_tee"),
        centre_line=tuple(e["centre_line"]) if e.get("centre_line") else None)


def timed(fn, *a, **k):
    t0 = time.monotonic()
    out = fn(*a, **k)
    return out, time.monotonic() - t0


def rock(model, video, hv, s):
    t_rel = s["t_release_s"]
    frames, times = longview.decode(video, hv.rect, t_rel + longview.WINDOW_S[0],
                                    t_rel + longview.WINDOW_S[1], 30.0)
    if not len(frames):
        return None
    got = sidemodel.find_in_frames(model, frames, hv, s["color"], times)
    del frames
    track = linetime.hog_track(got, hv)
    if track and max(p[3] for p in track) < linetime.EXTEND_IF_SHORT_OF_M:
        extra = linetime._extend_for(type("S", (), {"color": s["color"]})(), hv, video, t_rel,
                                     model, longview.decode, sidemodel.detect_band)
        track = linetime.hog_track(got, hv, extra)
    if linetime.fit_line(track) is None:
        return {"line": False}
    old, s_old = timed(linetime.find_start, model, video, hv, s["color"], t_rel,
                       decode=longview.decode, detect=sidemodel.detect_band)
    fr, s_new = timed(linetime.read_delivery, model, video, hv, s["color"], t_rel, track[0][0],
                      decode=longview.decode, detect=sidemodel.detect_band)
    d, s_path = timed(linetime.delivery_path, track, fr, t_rel)
    pub = [[round(t, 2), round(y, 2), round(x, 3)] for t, y, x in d]
    yps = [linetime.TEE_Y - y for _t, y, _x in d]
    return {"line": True, "t_band": track[0][0] - t_rel,
            "old": None if old is None else list(old), "new": None if fr.start is None else list(fr.start),
            "s_old": s_old, "s_new": s_new, "s_path": s_path, "frames": len(fr.times),
            "n": len(d), "bytes": len(json.dumps(pub, separators=(",", ":"))),
            "rest": bool(yps and min(yps) < linetime.DELIVERY_REST_BEHIND_M),
            "end": bool(yps and max(yps) > 7.5), "first_t": d[0][0] if d else None,
            "delivery": pub}


def spread(name, v, unit="cm", k=100.0):
    v = sorted(x * k for x in v)
    if v:
        print(f"  {name}: median {statistics.median(v):.2f} {unit}, p95 {v[int(0.95 * (len(v) - 1))]:.2f}, "
              f"max {v[-1]:.2f}; within 1 {unit} {sum(x <= 1.0 for x in v)}/{len(v)}")


def report(rows, brooms=None) -> None:
    """``brooms``: rock -> (x, y), for what the start's move does to the
    published offset at the hog line (the start-to-broom line there)."""
    lined = [r for r in rows if r.get("line")]
    print(f"{len(rows)} rocks, {len(lined)} with a line")
    both = [r for r in lined if r["old"] and r["new"]]
    flips = [r["rock"] for r in both if (r["old"][0] <= 0) != (r["new"][0] <= 0)]
    print(f"start: old {sum(bool(r['old']) for r in lined)}, new {sum(bool(r['new']) for r in lined)}; "
          f"both {len(both)}")
    # Across is what the hack call and the start-to-broom line turn on; along,
    # 38 m from the broom, barely moves that line at the hog line.
    spread("moved across", [abs(r["old"][0] - r["new"][0]) for r in both])
    spread("moved along", [abs(r["old"][1] - r["new"][1]) for r in both])
    if brooms:
        d = [abs(linetime.aim_x(r["old"], brooms[r["rock"]], linetime.HOG_Y)
                 - linetime.aim_x(r["new"], brooms[r["rock"]], linetime.HOG_Y)) for r in both if r["rock"] in brooms]
        spread("offset at the hog line changed", d)
    print(f"  side changed: {len(flips)} {flips}")
    lost = [r["rock"] for r in lined if r["old"] and not r["new"]]
    gained = [r["rock"] for r in lined if r["new"] and not r["old"]]
    print(f"  lost {lost}, gained {gained}")
    print(f"delivery: reaches the rest {sum(r['rest'] for r in lined)}/{len(lined)}, "
          f"past hog+1.1 m {sum(r['end'] for r in lined)}/{len(lined)}, empty {sum(r['n'] == 0 for r in lined)}")
    for k, fmt in (("n", "{:.0f}"), ("bytes", "{:.0f}"), ("frames", "{:.0f}"), ("t_band", "{:.2f}"),
                   ("s_old", "{:.2f}"), ("s_new", "{:.2f}"), ("s_path", "{:.3f}")):
        v = sorted(r[k] for r in lined)
        print(f"  {k:7s} median " + fmt.format(statistics.median(v)) + "  p95 "
              + fmt.format(v[int(0.95 * (len(v) - 1))]) + "  max " + fmt.format(v[-1]))
    print(f"  seconds added per rock, median: {statistics.median(r['s_new'] + r['s_path'] - r['s_old'] for r in lined):.2f}")


def brooms_of(doc) -> dict:
    return {f"e{e['number']}_r{s['number']:02d}": (s["target_broom"]["x"], s["target_broom"]["y"])
            for g in doc["games"] for e in g["ends"] for s in e["shots"] if s.get("target_broom")}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeline", required=True)
    ap.add_argument("--video", required=True)
    ap.add_argument("--weights", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    from ultralytics import YOLO
    model = YOLO(args.weights)
    doc = json.loads(Path(args.timeline).read_text())
    views = {n: view_from(doc["calibration"][n]) for n in ("left", "right")}
    part = Path(str(args.out) + ".part.jsonl")
    done = {}
    if part.exists():
        for ln in part.read_text().splitlines():
            if ln.strip():
                r = json.loads(ln); done[r["rock"]] = r
    rows = list(done.values())
    rocks = [(e, s) for g in doc["games"] for e in g["ends"] for s in e["shots"]
             if not s.get("missing") and s.get("t_release_s") is not None]
    for k, (e, s) in enumerate(rocks, 1):
        name = f"e{e['number']}_r{s['number']:02d}"
        if name in done:
            continue
        hv = views[hogtime.CAMERA_FOR[OTHER_HOUSE[e["house"]]]]
        got = rock(model, args.video, hv, s)
        if got is None:
            continue
        row = {"rock": name, "color": s["color"], **got}
        rows.append(row)
        with part.open("a") as fh:
            fh.write(json.dumps(row) + "\n")
        print(f"\r{k}/{len(rocks)} {name}   ", end="", flush=True)
    Path(args.out).write_text(json.dumps(rows))
    print()
    report(rows, brooms_of(doc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
