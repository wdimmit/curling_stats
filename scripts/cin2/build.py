#!/usr/bin/env python
"""cin2: pick, cut and pre-label 100 frames of Cincinnati's end cameras for the hand review.

Each end camera sits above its own hog line and watches throwers deliver from
its own end, hack at the top of the frame and hog line along the bottom.
Cincinnati's throwers hold the stone almost to the hog line, and ds13e, which
learned Granite's distant long cameras, loses it under the hand: on most
throws of the first `--club cincinnati` run its track ends rows short of the
line and the crossing goes untimed.

For each throw a game run placed (`t_release_s`), the delivering end's camera
is read from just before the release to 6 s after it with ds13e at
`MINE_CONF`, the rows `sidepool.band_crop` gives it, as `sidemodel.propose`
reads them. The throw's track is the one that goes deepest. Then:

  lost   the track ends short of the hog line: one frame in the gap between
         where ds13e lost it and where it would reach the line at the speed it
         had, alternately halfway and just before the line
  seen   the track reaches the line: one frame of the stone a metre short of
         it, still in the hand, so the tune keeps what ds13e already gets

`LOST_SHARE` of the frames are lost ones, spread over the game; seen ones fill
the rest. Frames are written at the band crop, which is what the model reads.

    python scripts/cin2/build.py --timeline out2/timeline.json --video VIDEO \
        --weights ds13e.pt --out DIR --manifest datasets/cin2/manifest.json
    python scripts/cin2/build.py --relabel --weights ds13e.pt --out DIR --manifest datasets/cin2/manifest.json
    python scripts/cin2/build.py --render-only --out DIR --manifest datasets/cin2/manifest.json
"""

import argparse
import json
from collections import Counter
from pathlib import Path

N_FRAMES, LOST_SHARE = 100, 0.7
# Pre-labels at 0.1 rather than 0.25, kept to boxes of a stone's width at their
# row: 78 boxes on 66 frames against 51 on 48, so fewer for the reviewer to draw.
MINE_CONF, PRELABEL_CONF, IMGSZ = 0.05, 0.1, 800
SHORT_PX = 10        # a track ending this far above the hog row did not reach it
SEEN_BEFORE_HOG_M = 1.0
# ds13e's boxes here, in metres of the lateral scale at their row: 0.255 wide
# (p25-75 0.243-0.267 over 72 pre-labels, both cameras) and 0.20 tall.
BOX_M = (0.255, 0.20)
DELIVERING = {"top": "bottom", "bottom": "top"}   # the end thrown toward -> the end thrown from
BOX_ALL = ("Box EVERY stone in the frame, including one under the thrower's hand, behind a broom or a "
           "sweeper's leg, or cut off by the bottom edge, and reject every box that is not a stone -- "
           "shoes, gloves, broom heads, sliders.")


def view_from(c):
    from curling_score.geometry.sideview import CAMERA_TO_FAR_TEE_M, SideView

    return SideView(rect=tuple(c["rect"]), tee_row=c["tee_row"], hog_row=c["hog_row"],
                    d_m=c.get("d_m", CAMERA_TO_FAR_TEE_M), centre_col=c["centre_col"],
                    lat_px_per_m_at_tee=c["lat_px_per_m_at_tee"], centre_line=tuple(c["centre_line"]),
                    end_camera=bool(c.get("end_camera")))


def throws(timeline):
    from curling_score.game import hogtime

    for g in timeline["games"]:
        for e in g["ends"]:
            for s in e["shots"]:
                if s.get("t_release_s") is not None:
                    yield {"end": e["number"], "shot": s["number"], "color": s["color"],
                           "t_release": s["t_release_s"],
                           "view": hogtime.CAMERA_FOR[DELIVERING[e["house"]]]}


def deepest(tracks, after):
    best = None
    for k, tr in tracks.items():
        tr = [p for p in tr if p[0] >= after]
        if len(tr) >= 3 and (best is None or max(p[1] for p in tr) > max(p[1] for p in best[1])):
            best = (k, tr)
    return best


def candidate(th, tracks, view, i):
    """Where in this throw's window to take a frame, and why."""
    from curling_score.geometry import constants as C

    got = deepest(tracks, th["t_release"] - 0.5)
    if got is None:
        return None
    tr = sorted(got[1])
    t_last, row_last = tr[-1][0], tr[-1][1]
    y_last = view.metres_at(row_last)
    if row_last < view.hog_row - SHORT_PX:
        # The speed it had over its last second in view.
        early = [p for p in tr if p[0] >= t_last - 1.0]
        dt = early[-1][0] - early[0][0]
        v = (y_last - view.metres_at(early[0][1])) / dt if dt > 0.2 else 0.0
        if v < 0.5:
            return None
        t_hog = t_last + (C.TEE_TO_HOGLINE_M - y_last) / v
        t = (t_last + t_hog) / 2 if i % 2 == 0 else t_hog - 0.1
        return {"kind": "lost", "t": t, "lost_at_m": round(y_last, 2), "v_m_s": round(v, 2)}
    want = C.TEE_TO_HOGLINE_M - SEEN_BEFORE_HOG_M
    p = min(tr, key=lambda p: abs(view.metres_at(p[1]) - want))
    return {"kind": "seen", "t": p[0], "lost_at_m": None, "v_m_s": None}


def spread(pool, n):
    pool = sorted(pool, key=lambda c: c["t"])
    return [pool[i * len(pool) // n] for i in range(n)] if len(pool) > n else pool


def stem_for(vid, view, t):
    return f"{vid}_{view[0]}_{t:09.2f}".replace(".", "_")


def prelabel(model, img, view, crop_top, path):
    """ds13e's stone-sized boxes on a crop, written as YOLO rows. Returns how many."""
    from curling_score.detect import longview, sidemodel

    h, w = img.shape[:2]
    res = model.predict(img, conf=PRELABEL_CONF, imgsz=IMGSZ, verbose=False)[0]
    rows = []
    for k, (x0, b0, x1, b1) in zip(res.boxes.cls.tolist(), res.boxes.xyxy.tolist()):
        expect = view.stone_width_at(b1 + crop_top, longview.STONE_WIDTH_AT_HOG_PX)
        if expect > 1 and sidemodel.WIDTH_TOL[0] <= (x1 - x0) / expect <= sidemodel.WIDTH_TOL[1]:
            rows.append(f"{int(k)} {(x0 + x1) / 2 / w:.6f} {(b0 + b1) / 2 / h:.6f} "
                        f"{(x1 - x0) / w:.6f} {(b1 - b0) / h:.6f}")
    path.write_text("\n".join(rows) + ("\n" if rows else ""))
    return len(rows)


def add_box(view, crop_top, w, h):
    """An added stone's normalised (w, h) at the crop's top and bottom edges.
    The lateral scale is linear in the row, so the review page and the apply
    step interpolate between them by where the box is."""
    c, yh = view._map()
    per_m = [view.lat_px_per_m_at_tee * (row - yh) / (view.tee_row - yh) for row in (crop_top, crop_top + h)]
    return [[round(BOX_M[0] * s / w, 5), round(BOX_M[1] * s / h, 5)] for s in per_m]


def relabel(a, manifest):
    """Pre-label the set on disk again, as `build` would now."""
    import cv2
    from ultralytics import YOLO

    model = YOLO(a.weights)
    views = {k: view_from(c) for k, c in manifest["views"].items()}
    out = a.out.expanduser() / "set"
    for f in manifest["frames"]:
        img = cv2.imread(str(out / f"images/train/{f['stem']}.jpg"))
        f["prelabels"] = prelabel(model, img, views[f["view"]], f["crop_top"], out / f"labels/train/{f['stem']}.txt")
        f["add_box"] = add_box(views[f["view"]], f["crop_top"], f["width"], f["height"])
    manifest["prelabel"].update(weights=Path(a.weights).name, conf=PRELABEL_CONF, width_tol=width_tol())
    a.manifest.write_text(json.dumps(manifest, indent=1))
    print(f"{sum(f['prelabels'] for f in manifest['frames'])} boxes on "
          f"{sum(f['prelabels'] > 0 for f in manifest['frames'])} frames")


def width_tol():
    from curling_score.detect import sidemodel

    return list(sidemodel.WIDTH_TOL)


def build(a):
    import cv2
    from ultralytics import YOLO

    from curling_score.detect import longview, sidemodel
    from curling_score.harvest import sidepool

    timeline = json.loads(a.timeline.read_text())
    views = {k: view_from(timeline["calibration"][k]) for k in ("left", "right")}
    model = YOLO(a.weights)
    pool = []
    for i, th in enumerate(throws(timeline)):
        v = views[th["view"]]
        frames, times = longview.decode(a.video, v.rect, th["t_release"] - 1.0, th["t_release"] + 6.0, 30.0)
        tracks = sidemodel.split_at_gaps(sidemodel.propose(model, frames, v, th["color"], times, conf=MINE_CONF),
                                         sidemodel.STRADDLE_GAP_MAX_S)
        c = candidate(th, tracks, v, i)
        print(f"e{th['end']} r{th['shot']:02d} {th['view']:5s} {th['color']:6s}", c and c["kind"], flush=True)
        if c:
            pool.append({**th, **c})
    n_lost = round(N_FRAMES * LOST_SHARE)
    lost = spread([c for c in pool if c["kind"] == "lost"], n_lost)
    seen = spread([c for c in pool if c["kind"] == "seen"], N_FRAMES - len(lost))

    out = a.out.expanduser()
    img_dir, lbl_dir = out / "set/images/train", out / "set/labels/train"
    for d in (img_dir, lbl_dir):
        d.mkdir(parents=True, exist_ok=True)
    vid = Path(a.video).stem
    frames_out = []
    for c in sorted(lost + seen, key=lambda c: c["t"]):
        v = views[c["view"]]
        y0, y1 = sidepool.band_crop(v)
        frames, times = longview.decode(a.video, v.rect, c["t"], c["t"] + 0.1, 30.0)
        if not len(frames):
            print("no frame", c["t"])
            continue
        img = longview.to_bgr(frames[0][max(0, y0):y1])
        stem = stem_for(vid, c["view"], times[0])
        cv2.imwrite(str(img_dir / f"{stem}.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 95])
        h, w = img.shape[:2]
        n = prelabel(model, img, v, max(0, y0), lbl_dir / f"{stem}.txt")
        frames_out.append({**c, "video": vid, "stem": stem, "t": round(float(times[0]), 3),
                           "crop_top": max(0, y0), "width": w, "height": h, "prelabels": n,
                           "add_box": add_box(v, max(0, y0), w, h)})
        print(f"{stem} {c['kind']}: {n} boxes", flush=True)
    manifest = {"what": a.title, "scope": a.scope,
                "prelabel": {"weights": Path(a.weights).name, "conf": PRELABEL_CONF, "imgsz": IMGSZ,
                             "width_tol": width_tol(),
                             "classes": {0: "red_stone", 1: "yellow_stone"}},
                "selection": {"n": N_FRAMES, "lost_share": LOST_SHARE, "mine_conf": MINE_CONF,
                              "short_px": SHORT_PX, "seen_before_hog_m": SEEN_BEFORE_HOG_M,
                              "throws": len(list(throws(timeline))), "pool": dict(Counter(c["kind"] for c in pool))},
                "views": {k: timeline["calibration"][k] for k in ("left", "right")},
                "by_kind": dict(Counter(f["kind"] for f in frames_out)), "frames": frames_out}
    a.manifest.parent.mkdir(parents=True, exist_ok=True)
    a.manifest.write_text(json.dumps(manifest, indent=1))
    print(f"{len(frames_out)} frames: {manifest['by_kind']}, from a pool of {manifest['selection']['pool']}")


def note_for(f):
    side = "blue" if f["view"] == "right" else "red"
    if f["kind"] == "lost":
        why = (f"end {f['end']} rock {f['shot']} ({f['color']}) from the {side} end: ds13e lost it "
               f"{f['lost_at_m']} m from the tee, moving {f['v_m_s']} m/s, short of the hog line at 6.4 m. "
               f"It is in this frame, most likely under the thrower's hand")
    else:
        why = f"end {f['end']} rock {f['shot']} ({f['color']}) from the {side} end, a metre short of the hog line"
    return f"{why}. {BOX_ALL}"


def render(out, manifest):
    """The review pages, from the set on disk and the manifest's notes. Labels
    are not touched, so a review in progress keeps every edit."""
    from curling_score.train import labels

    meta = {f["stem"]: labels.FrameMeta(box=tuple(map(tuple, f["add_box"])), note=note_for(f),
                                        url=f"https://youtu.be/{f['video']}?t={int(f['t'])}")
            for f in manifest["frames"]}
    frames = [f for seq in labels.load_split(out / "set", "train").values() for f in seq]
    page, n = labels.render_clickable(frames, out / "review", title=manifest["what"],
                                      scope=manifest["scope"], per_page=200, meta=meta)
    print(f"{len(frames)} frames rendered: {page}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeline", type=Path)
    ap.add_argument("--video", type=Path)
    ap.add_argument("--weights")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--manifest", type=Path, required=True)
    ap.add_argument("--render-only", action="store_true")
    ap.add_argument("--relabel", action="store_true", help="pre-label the set on disk again")
    ap.add_argument("--scope", default="cin2:train")
    ap.add_argument("--title", default="cin2: Cincinnati's end cameras (rKiMmtVod0I)")
    a = ap.parse_args()
    if a.relabel:
        relabel(a, json.loads(a.manifest.read_text()))
        return
    if not a.render_only:
        build(a)
        return
    render(a.out.expanduser(), json.loads(a.manifest.read_text()))


if __name__ == "__main__":
    main()
