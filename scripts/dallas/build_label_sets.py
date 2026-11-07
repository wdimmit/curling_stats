#!/usr/bin/env python
"""DFW label sets for the site's /label page: long-camera rocks being thrown, and overhead frames.

    python build_label_sets.py long     --game VIDEO TIMELINE ... --n 150 --green 0.6 --out DIR
    python build_label_sets.py overhead --game VIDEO TIMELINE ... --n 100 --out DIR

Each --game is a converted (three-panel) video and its timeline (the site's
/g/<id>/timeline.json): the stream's own clock, its calibration.

long: rocks in or just out of the thrower's hand, from the long camera facing
the thrower (`hogtime.CAMERA_FOR` of the other house), at RELEASE_AT of the
tee-to-hog slide, cycled throw by throw. ``--green`` of them green (the
red_stone class), the rest yellow, each colour spread evenly over the games'
throws in time. The crop is that camera's panel from CROP_ABOVE_TEE rows
above its tee line, as dfw2/dfw3 were. Pre-boxed with the long model
(dfw2b), with cin2's stone-width gate.

overhead: frames spread evenly in time over each game (equal shares per
game), alternating the two houses, cropped to the panel. Pre-boxed with the
overhead model (dfw1a).

Writes DIR/images/<stem>.jpg, DIR/labels/<stem>.txt (YOLO: 0 red = DFW's
green, 1 yellow) and DIR/picks.jsonl -- the frames folder `label_fp.py publish
--frames` publishes, each item's camera from its pick.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "cin2"))
from build import prelabel, view_from  # noqa: E402  (cin2's)

RELEASE_AT = (0.65, 0.8, 0.92, 1.05)
CROP_ABOVE_TEE = 120
OVERHEAD_CONF, OVERHEAD_IMGSZ = 0.25, 448
EDGE_S = 60.0          # overhead frames keep this far from a game's start and end
LONG_WEIGHTS = "/opt/curling/weights/dfw2b.pt"
OVERHEAD_WEIGHTS = "/opt/curling/weights/dfw1a.pt"


def throws(video_id, timeline, color):
    from curling_score.game import hogtime

    other = {"top": "bottom", "bottom": "top"}
    n = 0
    for g in timeline["games"]:
        for e in g["ends"]:
            for s in e["shots"]:
                if s.get("missing") or s.get("color") != color:
                    continue
                tee, hog = s.get("t_tee_s"), s.get("t_hog_s")
                if tee is None or hog is None or hog <= tee:
                    continue
                u = RELEASE_AT[n % len(RELEASE_AT)]
                n += 1
                yield {"video": video_id, "game": g["index"], "end": e["number"], "shot": s["number"],
                       "color": color, "t": tee + u * (hog - tee), "at": u,
                       "view": hogtime.CAMERA_FOR[other[e["house"]]]}


def spread(pool, n):
    pool = sorted(pool, key=lambda c: (c["video"], c["t"]))
    return [pool[i * len(pool) // n] for i in range(n)] if len(pool) > n else pool


def frame_at(cap, t, fps=30.0):
    import cv2

    cap.set(cv2.CAP_PROP_POS_FRAMES, int(round(t * fps)))
    ok, f = cap.read()
    return f if ok else None


def write(out, stem, img, rows_or_n, pick, picks):
    import cv2

    cv2.imwrite(str(out / "images" / f"{stem}.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 95])
    picks.write(json.dumps({"stem": stem, **pick}) + "\n")


def long_set(a, games):
    import cv2
    from ultralytics import YOLO

    n_green = round(a.n * a.green)
    chosen = []
    for color, n in (("red", n_green), ("yellow", a.n - n_green)):
        pool = [t for vid, path, tl in games for t in throws(vid, tl, color)]
        chosen += spread(pool, n)
    model = YOLO(a.weights or LONG_WEIGHTS)
    caps = {vid: (cv2.VideoCapture(str(path)), tl) for vid, path, tl in games}
    made = 0
    with open(a.out / "picks.jsonl", "a") as picks:
        for c in sorted(chosen, key=lambda c: (c["video"], c["t"])):
            cap, tl = caps[c["video"]]
            v = view_from(tl["calibration"][c["view"]])
            x, y, w, h = v.rect
            top = max(0, int(v.tee_row) - CROP_ABOVE_TEE)
            f = frame_at(cap, c["t"])
            if f is None:
                print("no frame", c)
                continue
            img = f[y + top:y + h, x:x + w]
            stem = f"{c['video']}_{c['view'][0]}_{c['t']:09.2f}".replace(".", "_")
            nbox = prelabel(model, img, v, top, a.out / "labels" / f"{stem}.txt")
            write(a.out, stem, img, nbox, {"camera": "side", "video": c["video"], "t": round(c["t"], 3),
                                           "kind": "release", "game": c["game"], "end": c["end"],
                                           "shot": c["shot"], "color": c["color"], "at": c["at"],
                                           "club": "dallas"}, picks)
            made += 1
    print(f"long: {made} frames ({n_green} green asked)")


def overhead_set(a, games):
    import cv2
    from ultralytics import YOLO

    model = YOLO(a.weights or OVERHEAD_WEIGHTS)
    per_game = [a.n // len(games) + (1 if i < a.n % len(games) else 0) for i in range(len(games))]
    made = 0
    with open(a.out / "picks.jsonl", "a") as picks:
        for (vid, path, tl), k in zip(games, per_game):
            cap = cv2.VideoCapture(str(path))
            g = tl["games"][0]
            t0, t1 = g["start_s"] + EDGE_S, g["end_s"] - EDGE_S
            for i in range(k):
                t = t0 + (i + 0.5) * (t1 - t0) / k
                panel = ("top", "bottom")[i % 2]
                x, y, w, h = tl["calibration"][panel]["rect"]
                f = frame_at(cap, t)
                if f is None:
                    print("no frame", vid, t)
                    continue
                img = f[y:y + h, x:x + w]
                stem = f"{vid}_{panel[0]}_{t:09.2f}".replace(".", "_")
                res = model.predict(img, conf=OVERHEAD_CONF, imgsz=OVERHEAD_IMGSZ, verbose=False)[0]
                rows = [f"{int(c)} {(x0 + x1) / 2 / w:.6f} {(y0 + y1) / 2 / h:.6f} "
                        f"{(x1 - x0) / w:.6f} {(y1 - y0) / h:.6f}"
                        for c, (x0, y0, x1, y1) in zip(res.boxes.cls.tolist(), res.boxes.xyxy.tolist())]
                (a.out / "labels" / f"{stem}.txt").write_text("\n".join(rows) + ("\n" if rows else ""))
                write(a.out, stem, img, len(rows), {"camera": "overhead", "video": vid, "t": round(t, 3),
                                                    "kind": "spread", "panel": panel, "game": g["index"],
                                                    "club": "dallas"}, picks)
                made += 1
    print(f"overhead: {made} frames")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("which", choices=("long", "overhead"))
    ap.add_argument("--game", nargs=2, action="append", metavar=("VIDEO", "TIMELINE"), required=True)
    ap.add_argument("--n", type=int, required=True)
    ap.add_argument("--green", type=float, default=0.6, help="long: the share of green rocks")
    ap.add_argument("--weights")
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    for d in ("images", "labels"):
        (a.out / d).mkdir(parents=True, exist_ok=True)
    games = []
    for video, timeline in a.game:
        tl = json.loads(Path(timeline).read_text())
        games.append((tl["source"]["video_id"], Path(video), tl))
    (long_set if a.which == "long" else overhead_set)(a, games)


if __name__ == "__main__":
    main()
