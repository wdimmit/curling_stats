#!/usr/bin/env python
"""cin3: 100 frames of Cincinnati's end cameras watching throws ARRIVE, for the hand review.

cin2 taught the side model the throwing end. The other end camera -- above the
destination's hog line, looking back at its house -- sees the stone come in
over that line and stop, and that is where cin2a still loses it: under the
sweepers' bodies and brooms right below the camera, and in a house with the
skips standing in it. On the first run with it (`out5`) 41 of 119 throws got
no far hog crossing, and two ends' last rocks were never followed to rest.

Each throw's arrival is anchored on its far crossing, or where none was read,
on 3 s before the overhead saw it enter the house, or 12.5 s after its near
crossing. Then one frame of:

  coming   1.0 s past the anchor: the stone a metre or two in, swept
  house    4.5 s past it: the stone at or in the house, skips about

`MISSED` of the frames come from throws with no far crossing, half of each
kind; the rest from those with one, `HOUSE_SHARE` of them house frames.
Frames are cut from `ROWS_ABOVE_TEE` above the tee to the bottom of the view,
the rows `farfollow` reads, which hold the band `sidemodel.propose` reads.

    python scripts/cin3/build.py --timeline out5/timeline.json --video VIDEO \
        --weights cin2a.pt --out DIR --manifest datasets/cin3/manifest.json
    python scripts/cin3/build.py --render-only --out DIR --manifest datasets/cin3/manifest.json
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "cin2"))
from build import add_box, prelabel, view_from  # noqa: E402  (cin2's)

N_FRAMES, MISSED, HOUSE_SHARE = 100, 40, 0.4
OFFSET_S = {"coming": 1.0, "house": 4.5}
ENTER_BEFORE_S, HOG_AFTER_S = 3.0, 12.5
ROWS_ABOVE_TEE = 80
BOX_ALL = ("Box EVERY stone in the frame -- the one coming in, those already in the house and any "
           "beyond it -- including one under a sweeper, behind a broom or a leg, or cut off by an edge, "
           "and reject every box that is not a stone: shoes, gloves, broom heads, the logo.")


def arrivals(timeline):
    from curling_score.game import hogtime

    for g in timeline["games"]:
        for e in g["ends"]:
            for s in e["shots"]:
                if s.get("missing") or s.get("t_release_s") is None:
                    continue
                if s.get("t_far_s") is not None:
                    anchor, why = s["t_far_s"], "far"
                elif s.get("reason") == "late-entry" and s.get("t_enter_s"):
                    anchor, why = s["t_enter_s"] - ENTER_BEFORE_S, "enter"
                elif s.get("t_hog_s") is not None:
                    anchor, why = s["t_hog_s"] + HOG_AFTER_S, "hog"
                else:
                    continue
                yield {"end": e["number"], "shot": s["number"], "color": s["color"],
                       "shot_type": s["shot_type"], "anchor": anchor, "anchor_from": why,
                       "view": hogtime.CAMERA_FOR[e["house"]]}


def spread(pool, n):
    pool = sorted(pool, key=lambda c: c["anchor"])
    return [pool[i * len(pool) // n] for i in range(n)] if len(pool) > n else pool


def pick(timeline):
    missed = [a for a in arrivals(timeline) if a["anchor_from"] != "far"]
    seen = [a for a in arrivals(timeline) if a["anchor_from"] == "far"]
    chosen = []
    for i, a in enumerate(spread(missed, MISSED)):
        chosen.append({**a, "kind": "coming" if i % 2 == 0 else "house", "missed": True})
    rest = spread(seen, N_FRAMES - len(chosen))
    n_house = round(len(rest) * HOUSE_SHARE)
    for i, a in enumerate(rest):
        chosen.append({**a, "kind": "house" if i * n_house // len(rest) != (i + 1) * n_house // len(rest)
                       else "coming", "missed": False})
    for c in chosen:
        c["t"] = c["anchor"] + OFFSET_S[c["kind"]]
    return chosen


def build(a):
    import cv2
    from ultralytics import YOLO

    from curling_score.detect import longview

    timeline = json.loads(a.timeline.read_text())
    views = {k: view_from(timeline["calibration"][k]) for k in ("left", "right")}
    model = YOLO(a.weights)
    out = a.out.expanduser()
    img_dir, lbl_dir = out / "set/images/train", out / "set/labels/train"
    for d in (img_dir, lbl_dir):
        d.mkdir(parents=True, exist_ok=True)
    vid = Path(a.video).stem
    frames_out = []
    for c in sorted(pick(timeline), key=lambda c: c["t"]):
        v = views[c["view"]]
        top = max(0, int(v.tee_row) - ROWS_ABOVE_TEE)
        frames, times = longview.decode(a.video, v.rect, c["t"], c["t"] + 0.1, 30.0)
        if not len(frames):
            print("no frame", c["t"])
            continue
        img = longview.to_bgr(frames[0][top:])
        stem = f"{vid}_{c['view'][0]}_{times[0]:09.2f}".replace(".", "_")
        cv2.imwrite(str(img_dir / f"{stem}.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 95])
        h, w = img.shape[:2]
        n = prelabel(model, img, v, top, lbl_dir / f"{stem}.txt")
        frames_out.append({**c, "video": vid, "stem": stem, "t": round(float(times[0]), 3),
                           "crop_top": top, "width": w, "height": h, "prelabels": n,
                           "add_box": add_box(v, top, w, h)})
        print(f"{stem} {c['kind']}{' missed' if c['missed'] else ''}: {n} boxes", flush=True)
    manifest = {"what": a.title, "scope": a.scope,
                "prelabel": {"weights": Path(a.weights).name, "note": "cin2's prelabel: conf 0.1, stone-width gate"},
                "selection": {"n": N_FRAMES, "missed": MISSED, "house_share": HOUSE_SHARE, "offset_s": OFFSET_S,
                              "enter_before_s": ENTER_BEFORE_S, "hog_after_s": HOG_AFTER_S,
                              "rows_above_tee": ROWS_ABOVE_TEE,
                              "anchors": dict(Counter(x["anchor_from"] for x in arrivals(timeline)))},
                "views": {k: timeline["calibration"][k] for k in ("left", "right")},
                "by_kind": dict(Counter(f"{f['kind']}{'-missed' if f['missed'] else ''}" for f in frames_out)),
                "frames": frames_out}
    a.manifest.parent.mkdir(parents=True, exist_ok=True)
    a.manifest.write_text(json.dumps(manifest, indent=1))
    print(f"{len(frames_out)} frames: {manifest['by_kind']}; "
          f"{sum(f['prelabels'] for f in frames_out)} pre-labels")


def note_for(f):
    house = "red" if f["view"] == "left" else "blue"
    what = "coming in, about a second past the hog line" if f["kind"] == "coming" else "at or in the house"
    miss = (" cin2a never read this throw's far hog crossing; the time is estimated, so the stone may be "
            "anywhere from the hog line to the house." if f["missed"] else "")
    return (f"end {f['end']} rock {f['shot']} ({f['color']}, {f['shot_type']}) arriving at the {house} house: "
            f"{what}.{miss} {BOX_ALL}")


def render(out, manifest):
    """The review pages, from the set on disk and the manifest's notes."""
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
    ap.add_argument("--scope", default="cin3:train")
    ap.add_argument("--title", default="cin3: Cincinnati's end cameras, throws arriving (rKiMmtVod0I)")
    a = ap.parse_args()
    if a.render_only:
        render(a.out.expanduser(), json.loads(a.manifest.read_text()))
    else:
        build(a)


if __name__ == "__main__":
    main()
