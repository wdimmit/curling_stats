"""A/B two side models where production uses them: timing the throwing hog line
and the line pass. Both models see the same decoded frames, through the same
production functions (sidemodel.find_in_frames, linetime.hog_track, fit_line,
find_start); only the model differs.

    # hand-marked crossings (datasets/hogmarks)
    ab_side.py marks --marks datasets/hogmarks/VXU9xwmugRg.json --video V.mp4 \\
        --timeline T.json --out ab_marks_vxu9.json A=weights/ds13b.pt B=ds13c.pt

    # every rock of a game: crossing time, the line at the hog line and the
    # broom, the start -- against each other and against the published chart
    ab_side.py line --timeline T.json --video V.mp4 --out ab_line.json A=... B=...
    ab_side.py line --timeline T.json --clips DIR --offsets offsets.txt ...

``--clips`` holds clip_<name>.mp4 files cut at the offsets in ``--offsets``
("<name> <start s>" per line, name e<end>_r<NN>).
"""
import argparse
import json
import statistics
import sys
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


def models_from(specs):
    from ultralytics import YOLO
    return [(s.split("=", 1)[0], YOLO(s.split("=", 1)[1])) for s in specs]


def shifted_decode(off: float):
    def dec(video, rect, t0, t1, fps=30.0):
        f, t = longview.decode(video, rect, t0 - off, t1 - off, fps)
        return f, [x + off for x in t]
    return dec


def marks(args) -> int:
    doc = json.loads(Path(args.marks).read_text())
    cal = json.loads(Path(args.timeline).read_text())["calibration"]
    models = models_from(args.models)
    rows = []
    for end in doc["ends"]:
        view = view_from(cal[end["side_view"]])
        for m in end["marks"]:
            lo = m["release_t_s"] + longview.WINDOW_S[0]; hi = m["release_t_s"] + longview.WINDOW_S[1]
            frames, times = longview.decode(args.video, view.rect, lo, hi, 30.0)
            if not len(frames):
                continue
            row = {"release": m["release_t_s"], "color": m["color"], "truth": m["hog_crossing_s"]}
            for name, model in models:
                got = sidemodel.find_in_frames(model, frames, view, m["color"], times)
                row[name] = {"t": got.t, "key": got.key,
                             "err": None if got.t is None else got.t - m["hog_crossing_s"]}
            rows.append(row)
    Path(args.out).write_text(json.dumps(rows, indent=1))
    print(f"{len(rows)} hand-marked crossings")
    for name, _ in models:
        errs = [abs(r[name]["err"]) for r in rows if r[name]["t"] is not None]
        print(f"  {name}: timed {len(errs)}/{len(rows)}; |error| median {statistics.median(errs):.3f} s, "
              f"worst {max(errs):.3f} s; within 0.15 s {sum(e <= 0.15 for e in errs)}")
    return 0


def line(args) -> int:
    doc = json.loads(Path(args.timeline).read_text())
    cal = doc["calibration"]
    views = {n: view_from(cal[n]) for n in ("left", "right")}
    models = models_from(args.models)
    offsets = {}
    if args.offsets:
        for ln in Path(args.offsets).read_text().splitlines():
            if ln.strip():
                n, o = ln.split()[:2]; offsets[n] = float(o)
    # One line per rock as it finishes, beside --out: a run killed partway (this
    # box's memory is shared with other sessions' test runs) resumes where it stopped.
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
        if args.clips:
            video = str(Path(args.clips) / f"clip_{name}.mp4")
            if not Path(video).exists() or name not in offsets:
                continue
            off = offsets[name]
        else:
            video, off = args.video, 0.0
        dec = shifted_decode(off)
        hv = views[hogtime.CAMERA_FOR[OTHER_HOUSE[e["house"]]]]
        t_rel = s["t_release_s"]
        frames, times = dec(video, hv.rect, t_rel + longview.WINDOW_S[0], t_rel + longview.WINDOW_S[1], 30.0)
        if not len(frames):
            continue
        b = s.get("target_broom"); pub = s.get("line") or {}
        row = {"rock": name, "color": s["color"],
               "pub": {"at_hog": (pub.get("at_hog") or {}).get("x"),
                       "at_broom": (pub.get("at_broom") or {}).get("x"),
                       "start": pub.get("start")}}
        for mname, model in models:
            got = sidemodel.find_in_frames(model, frames, hv, s["color"], times)
            track = linetime.hog_track(got, hv)
            if track and max(p[3] for p in track) < linetime.EXTEND_IF_SHORT_OF_M:
                extra = linetime._extend_for(type("S", (), {"color": s["color"]})(), hv, video, t_rel,
                                             model, dec, sidemodel.detect_band)
                track = linetime.hog_track(got, hv, extra)
            fit = linetime.fit_line(track)
            st = linetime.find_start(model, video, hv, s["color"], t_rel, decode=dec,
                                     detect=sidemodel.detect_band)
            row[mname] = {"t": got.t, "key": got.key, "n": len(track),
                          "at_hog": None if fit is None else fit.x(linetime.HOG_Y),
                          "at_broom": None if fit is None or b is None else fit.x(b["y"]),
                          "start": None if st is None else {"x": st[0], "y": st[1]}}
        rows.append(row)
        with part.open("a") as fh:
            fh.write(json.dumps(row) + "\n")
        del frames
        print(f"\r{k}/{len(rocks)} {name}   ", end="", flush=True)
    Path(args.out).write_text(json.dumps(rows, indent=1))
    print(f"\n{len(rows)} rocks")
    for mname, _ in models:
        r = [x[mname] for x in rows]
        dh = [abs(x[mname]["at_hog"] - x["pub"]["at_hog"]) for x in rows
              if x[mname]["at_hog"] is not None and x["pub"]["at_hog"] is not None]
        print(f"  {mname}: crossing timed {sum(q['t'] is not None for q in r)}, line {sum(q['at_hog'] is not None for q in r)} "
              f"(published {sum(x['pub']['at_hog'] is not None for x in rows)}), start {sum(q['start'] is not None for q in r)} "
              f"(published {sum(bool(x['pub']['start']) for x in rows)}); at the hog line vs published: "
              f"max |d| {max(dh) * 100 if dh else float('nan'):.2f} cm")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=("marks", "line"))
    ap.add_argument("--marks"); ap.add_argument("--video"); ap.add_argument("--timeline", required=True)
    ap.add_argument("--clips"); ap.add_argument("--offsets")
    ap.add_argument("--out", required=True)
    ap.add_argument("models", nargs="+", help="name=weights, two of them")
    args = ap.parse_args()
    return marks(args) if args.mode == "marks" else line(args)


if __name__ == "__main__":
    sys.exit(main())
