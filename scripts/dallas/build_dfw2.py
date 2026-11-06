#!/usr/bin/env python
"""dfw2: 100 frames of DFW's long cameras, green and yellow stones, for the hand review.

long-1 has seen only red and yellow, and on DFW's long cameras it misses the
green stones or reads them as yellow -- even on the green-to-red copy, where
the far greens are small and pale (vvy26k01ZcI, 2026-11-06). Green is to be
the red_stone class. Each long camera sits behind one hack and looks down the
sheet at the far house, so every throw is in both:

  release  the camera looking at the delivering house: the stone in the
           thrower's hand, far up the view, RELEASE_AT of the way from the
           delivering tee to the hog line (the run's t_release_s is 2-3 s
           early here; its tee and hog times match the video)
  travel   the camera looking at the target house: the stone in the middle
           of the view, TRAVEL_AT of the way from the hog line to the house.
           Neither camera sees the near hog line: the views run from the far
           tee to 23 m out, so a stone near its thrower is never in them
  coming   the camera looking at the target house: the stone a couple of
           metres short of it, COMING_BEFORE_S before the overhead saw it enter
  house    the same camera, HOUSE_AFTER_S after the stone came to rest: a
           busy house, skips and sweepers about

Throws are those a game run (`out0`, on the green-to-red copy) placed. A
camera's frame is used only where that camera is real pixels: a score view
shows one long camera and the normaliser filled the other with the empty
plate, and the bottom rows of the one it shows lie under the score graphic, so
a `travel` frame, low in the view, must come from the three-panel view. GUARD_S keeps clear of
layout changes. Frames are cut from CROP_ABOVE_TEE rows above the far tee to
the bottom of the view, in true colours from the normalised video; pre-labels
are long-1 on the same frame of the green-to-red copy, through cin2's
stone-width gate, and added boxes are sized by row as in cin2.

    python build_dfw2.py --timeline out0/timeline.json --layout norm/c?.json --video full.mp4 \\
        --g2r full-g2r.mp4 --weights long-1.pt --out DIR --manifest manifest.json
    python build_dfw2.py --render-only --out DIR --manifest manifest.json
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "cin2"))
from build import add_box, prelabel, view_from  # noqa: E402  (cin2's)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_dfw1 import load_layout  # noqa: E402

SHARES = {"release": 30, "travel": 20, "coming": 25, "house": 25}
RELEASE_AT, TRAVEL_AT = (0.5, 0.9), (0.25, 0.5)  # alternated throw by throw
COMING_BEFORE_S, HOUSE_AFTER_S = 2.0, 0.5
CROP_ABOVE_TEE = 120
GUARD_S = 1.0
# Where each long camera sits in the composite: S0 shows the one on the right
# (it looks at the top house), S1 the one on the left.
SHOWN_BY = {"S0": 1143, "S1": 0}
BOX_ALL = ("GREEN stones are the RED class: box each green stone as red (r), and every yellow as yellow (y). "
           "Box EVERY stone in the frame -- in the thrower's hand, under a sweeper, behind a broom or a leg, "
           "far off in the house, or cut off by an edge, and the stones on the neighbouring sheets or parked "
           "behind the house too (the pipeline keeps to its own sheet) -- and reject every box that is not a "
           "stone: shoes, gloves, broom heads, sliders.")


def throws(timeline, color=None):
    from curling_score.game import hogtime

    other = {"top": "bottom", "bottom": "top"}
    for g in timeline["games"]:
        for e in g["ends"]:
            for s in e["shots"]:
                if s.get("missing") or (color and s["color"] != color):
                    continue
                yield {"end": e["number"], "shot": s["number"], "color": s["color"],
                       "shot_type": s.get("shot_type"), "tee": s.get("t_tee_s"), "hog": s.get("t_hog_s"),
                       "enter": s.get("t_enter_s"), "rest": s.get("t_rest_s"),
                       "target_view": hogtime.CAMERA_FOR[e["house"]],
                       "delivering_view": hogtime.CAMERA_FOR[other[e["house"]]]}


def real(t, view_x, kind, kinds, fps):
    """Whether this camera is real pixels around t (and, for a travel frame, its bottom rows too)."""
    i = int(round(t * fps))
    lo, hi = max(0, i - int(GUARD_S * fps)), min(len(kinds), i + int(GUARD_S * fps) + 1)
    near = set(kinds[lo:hi])
    if len(near) != 1:
        return None
    k = near.pop()
    if k == "T":
        return k
    if k in SHOWN_BY and SHOWN_BY[k] == view_x and kind != "travel":
        return k
    return None


def candidates(timeline, views, kinds, fps, color=None, release_at=RELEASE_AT):
    pools = {k: [] for k in SHARES}
    for n, th in enumerate(throws(timeline, color)):
        tee, hog, enter = th["tee"], th["hog"], th["enter"]
        u, v = release_at[n % len(release_at)], TRAVEL_AT[n % 2]
        for kind, view, t in (
                ("release", th["delivering_view"], tee and hog and tee + u * (hog - tee)),
                ("travel", th["target_view"], hog and enter and hog + v * (enter - hog)),
                ("coming", th["target_view"], th["enter"] and th["enter"] - COMING_BEFORE_S),
                ("house", th["target_view"], th["rest"] and th["rest"] + HOUSE_AFTER_S)):
            if not t:
                continue
            layout = real(t, views[view].rect[0], kind, kinds, fps)
            if layout:
                pools[kind].append({**th, "kind": kind, "view": view, "t": t, "layout": layout})
    return pools


def spread(pool, n):
    pool = sorted(pool, key=lambda c: c["t"])
    return [pool[i * len(pool) // n] for i in range(n)] if len(pool) > n else pool


def build(a):
    import cv2
    import numpy as np
    from ultralytics import YOLO

    timeline = json.loads(a.timeline.read_text())
    views = {k: view_from(timeline["calibration"][k]) for k in ("left", "right")}
    # No layout logs: a stream that only ever showed the three-panel view.
    kinds, fps = load_layout(a.layout) if a.layout else (["T"] * 10 ** 7, 30.0)
    shares = dict(SHARES) if not a.shares else {
        k: int(n) for k, n in (kv.split("=") for kv in a.shares.split(","))}
    release_at = tuple(float(u) for u in a.release_at.split(",")) if a.release_at else RELEASE_AT
    pools = candidates(timeline, views, kinds, fps, a.color, release_at)
    chosen = [c for kind, n in shares.items() for c in spread(pools.get(kind, []), n)]
    model = YOLO(a.weights)
    out = a.out.expanduser()
    img_dir, lbl_dir = out / "set/images/train", out / "set/labels/train"
    for d in (img_dir, lbl_dir):
        d.mkdir(parents=True, exist_ok=True)
    # Without a green-to-red copy the model labels the true colours itself.
    caps = {"true": cv2.VideoCapture(str(a.video)), "g2r": cv2.VideoCapture(str(a.g2r or a.video))}
    frames_out = []
    for c in sorted(chosen, key=lambda c: c["t"]):
        v = views[c["view"]]
        x, y, w, h = v.rect
        top = max(0, int(v.tee_row) - CROP_ABOVE_TEE)
        i = int(round(c["t"] * fps))
        got = {}
        for name, cap in caps.items():
            cap.set(cv2.CAP_PROP_POS_FRAMES, i)
            ok, f = cap.read()
            got[name] = f[y + top:y + h, x:x + w] if ok else None
        img, swap = got["true"], got["g2r"]
        if img is None or swap is None:
            print("no frame", c["t"])
            continue
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        notgreen = ~((hsv[..., 0] >= 50) & (hsv[..., 0] <= 100) & (hsv[..., 1] >= 40))
        drift = float(np.abs(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)
                             - cv2.cvtColor(swap, cv2.COLOR_BGR2GRAY))[notgreen].mean())
        if drift > 4.0:
            print(f"t={c['t']:.1f}: copies differ by {drift:.1f}; skipped")
            continue
        t = i / fps
        stem = f"{a.video_id}_{c['view'][0]}_{t:09.2f}".replace(".", "_")
        if (img_dir / f"{stem}.jpg").exists():
            continue  # two kinds landed on one frame
        cv2.imwrite(str(img_dir / f"{stem}.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 95])
        ih, iw = img.shape[:2]
        n = prelabel(model, swap, v, top, lbl_dir / f"{stem}.txt")
        frames_out.append({**c, "video": a.video_id, "stem": stem, "t": round(t, 3), "frame": i,
                           "crop_top": top, "width": iw, "height": ih, "prelabels": n,
                           "copy_drift": round(drift, 2), "add_box": add_box(v, top, iw, ih)})
        print(f"{stem} {c['kind']} {c['layout']}: {n} boxes (drift {drift:.2f})", flush=True)
    manifest = {"what": a.title, "scope": a.scope,
                "images": "normalised video, true colours (scripts/dallas/normalize.py)",
                "prelabel": {"weights": Path(a.weights).name, "on": "the green-to-red copy of the same frame",
                             "note": "cin2's prelabel: conf 0.1, stone-width gate",
                             "classes": {0: "red_stone (DFW: green)", 1: "yellow_stone"}},
                "selection": {"shares": shares, "release_at": release_at, "travel_at": TRAVEL_AT,
                              "color": a.color, "prelabel_on": "green-to-red copy" if a.g2r else "true colours",
                              "coming_before_s": COMING_BEFORE_S, "house_after_s": HOUSE_AFTER_S,
                              "crop_above_tee": CROP_ABOVE_TEE, "guard_s": GUARD_S,
                              "pools": {k: len(v) for k, v in pools.items()}},
                "views": {k: timeline["calibration"][k] for k in ("left", "right")},
                "by_kind": dict(Counter(f["kind"] for f in frames_out)),
                "by_layout": dict(Counter(f["layout"] for f in frames_out)), "frames": frames_out}
    a.manifest.parent.mkdir(parents=True, exist_ok=True)
    a.manifest.write_text(json.dumps(manifest, indent=1))
    print(f"{len(frames_out)} frames: {manifest['by_kind']} {manifest['by_layout']}; "
          f"{sum(f['prelabels'] for f in frames_out)} pre-labels; pools {manifest['selection']['pools']}")


def note_for(f):
    side = "right" if f["views_x"] else "left"
    what = {"release": "the throw being delivered at the far end, stone in the thrower's hand",
            "travel": "the stone on its way down the sheet toward the far house",
            "coming": "the stone a couple of metres short of the far house",
            "house": "the house just after the stone came to rest"}[f["kind"]]
    return (f"{side} long camera, end {f['end']} rock {f['shot']} ({f['color']} in the run, which may be wrong): "
            f"{what}. {BOX_ALL}")


def render(out, manifest):
    """The review pages, from the set on disk and the manifest's notes."""
    from curling_score.train import labels

    xs = {k: v["rect"][0] for k, v in manifest["views"].items()}
    meta = {f["stem"]: labels.FrameMeta(box=tuple(map(tuple, f["add_box"])),
                                        note=note_for({**f, "views_x": xs[f["view"]]}),
                                        url=f"https://youtu.be/{f['video']}?t={int(f['t'])}")
            for f in manifest["frames"]}
    frames = [f for seq in labels.load_split(out / "set", "train").values() for f in seq]
    page, n = labels.render_clickable(frames, out / "review", title=manifest["what"],
                                      scope=manifest["scope"], per_page=200, meta=meta)
    print(f"{len(frames)} frames rendered: {page}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeline", type=Path)
    ap.add_argument("--layout", nargs="+", help="the normaliser's per-chunk logs, in order")
    ap.add_argument("--video", type=Path, help="normalised video, true colours")
    ap.add_argument("--g2r", type=Path, help="normalised video, green-to-red; without it, the true colours")
    ap.add_argument("--color", help="only this colour's throws (red = DFW's green)")
    ap.add_argument("--shares", help="kind=n,... in place of SHARES, e.g. release=30")
    ap.add_argument("--release-at", help="fractions of the tee-to-hog slide, cycled throw by throw")
    ap.add_argument("--weights")
    ap.add_argument("--video-id", default="vvy26k01ZcI")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--manifest", type=Path, required=True)
    ap.add_argument("--render-only", action="store_true")
    ap.add_argument("--scope", default="dfw2:train")
    ap.add_argument("--title", default="dfw2: DFW's long cameras, green and yellow stones (vvy26k01ZcI)")
    a = ap.parse_args()
    if a.render_only:
        render(a.out.expanduser(), json.loads(a.manifest.read_text()))
    else:
        build(a)


if __name__ == "__main__":
    main()
