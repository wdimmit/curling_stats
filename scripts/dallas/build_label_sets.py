#!/usr/bin/env python
"""DFW label sets for the site's /label page: long-camera rocks being thrown, and overhead frames.

    python build_label_sets.py long     --game VIDEO TIMELINE ... --n 150 --green 0.6 --out DIR
    python build_label_sets.py overhead --game VIDEO TIMELINE ... --n 100 --out DIR
    python build_label_sets.py broom    --game VIDEO TIMELINE ... --n 75 --out DIR
    python build_label_sets.py publish-brooms --out DIR --batch NAME   (ADMIN_TOKEN, BASE_URL)

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

broom: the long camera facing the house thrown to, around the release (half
way from tee to hog), while the skip holds the target: the broom model's own
crop (`broommodel.crop_rows`, full view width). Pre-boxed with broom4; the
stones (dfw2b) go along greyed, as context, in stones/<stem>.txt. Published
by publish-brooms as /label's "broom" items, boxed as players are.

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
BROOM_WEIGHTS = "/opt/curling/weights/broom4.pt"
BROOM_CONF, BROOM_IMGSZ = 0.25, 640
BROOM_AT = 0.5
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


def broom_set(a, games):
    import cv2
    from ultralytics import YOLO

    from curling_score.detect.broommodel import crop_rows
    from curling_score.game import hogtime

    pool = []
    for vid, path, tl in games:
        for g in tl["games"]:
            for e in g["ends"]:
                for s in e["shots"]:
                    tee, hog = s.get("t_tee_s"), s.get("t_hog_s")
                    if s.get("missing") or tee is None or hog is None or hog <= tee:
                        continue
                    pool.append({"video": vid, "game": g["index"], "end": e["number"],
                                 "shot": s["number"], "color": s["color"],
                                 "t": tee + BROOM_AT * (hog - tee),
                                 "view": hogtime.CAMERA_FOR[e["house"]]})
    chosen = spread(pool, a.n)
    brooms, stones_model = YOLO(a.weights or BROOM_WEIGHTS), YOLO(LONG_WEIGHTS)
    caps = {vid: (cv2.VideoCapture(str(path)), tl) for vid, path, tl in games}
    (a.out / "stones").mkdir(exist_ok=True)
    made = 0
    with open(a.out / "picks.jsonl", "a") as picks:
        for c in sorted(chosen, key=lambda c: (c["video"], c["t"])):
            cap, tl = caps[c["video"]]
            v = view_from(tl["calibration"][c["view"]])
            x, y, w, _h = v.rect
            top, bot = crop_rows(v)
            f = frame_at(cap, c["t"])
            if f is None:
                print("no frame", c)
                continue
            img = f[y + top:y + bot, x:x + w]
            ih, iw = img.shape[:2]
            stem = f"{c['video']}_b{c['view'][0]}_{c['t']:09.2f}".replace(".", "_")
            res = brooms.predict(img, conf=BROOM_CONF, imgsz=BROOM_IMGSZ, verbose=False)[0]
            rows = [f"0 {(x0 + x1) / 2 / iw:.6f} {(y0 + y1) / 2 / ih:.6f} {(x1 - x0) / iw:.6f} {(y1 - y0) / ih:.6f}"
                    for x0, y0, x1, y1 in res.boxes.xyxy.tolist()]
            (a.out / "labels" / f"{stem}.txt").write_text("\n".join(rows) + ("\n" if rows else ""))
            prelabel(stones_model, img, v, top, a.out / "stones" / f"{stem}.txt")
            write(a.out, stem, img, len(rows), {"camera": "side", "video": c["video"], "t": round(c["t"], 3),
                                                "kind": "broom", "game": c["game"], "end": c["end"],
                                                "shot": c["shot"], "club": "dallas"}, picks)
            made += 1
    print(f"broom: {made} frames")


def publish_brooms(a):
    """The broom folder as /label "broom" items (a player's payload: starts to
    fix, stones greyed and locked), sent with label_publish."""
    import os

    import cv2

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import label_players
    import label_publish

    picks = {}
    for line in (a.out / "picks.jsonl").read_text().splitlines():
        row = json.loads(line)
        picks[row["stem"]] = row
    items, images = [], {}
    for path in sorted((a.out / "images").glob("*.jpg")):
        img = cv2.imread(str(path))
        h, w = img.shape[:2]
        def boxes(folder):
            f = a.out / folder / f"{path.stem}.txt"
            return label_players.stones_from_label(f.read_text(), w, h) if f.is_file() else []
        starts = [{k: b[k] for k in ("x0", "y0", "x1", "y1")}
                  for b in boxes("labels") if b["x1"] - b["x0"] >= label_players.MIN_BOX_PX
                  and b["y1"] - b["y0"] >= label_players.MIN_BOX_PX]
        item_id = label_publish.opaque_id("w_", f"dfw-broom/{path.stem}")
        items.append({"id": item_id, "kind": "broom", "batch": a.batch,
                      "payload": {"w": w, "h": h, "starts": starts, "stones": boxes("stones"),
                                  "stones_locked": True,
                                  "origin": {"stem": path.stem, **picks.get(path.stem, {})}}})
        images[item_id] = path.read_bytes()
    got = label_publish.publish(os.environ.get("BASE_URL", ""), os.environ["ADMIN_TOKEN"], items, images)
    print(f"{len(items)} broom items in batch {a.batch}: opened {len(got['opened'])}, "
          f"skipped {len(got['skipped'])}, missing image {len(got['missing_image'])}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("which", choices=("long", "overhead", "broom", "publish-brooms"))
    ap.add_argument("--game", nargs=2, action="append", metavar=("VIDEO", "TIMELINE"))
    ap.add_argument("--n", type=int)
    ap.add_argument("--batch")
    ap.add_argument("--green", type=float, default=0.6, help="long: the share of green rocks")
    ap.add_argument("--weights")
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    if a.which == "publish-brooms":
        return publish_brooms(a)
    for d in ("images", "labels"):
        (a.out / d).mkdir(parents=True, exist_ok=True)
    games = []
    for video, timeline in a.game:
        tl = json.loads(Path(timeline).read_text())
        games.append((tl["source"]["video_id"], Path(video), tl))
    {"long": long_set, "overhead": overhead_set, "broom": broom_set}[a.which](a, games)


if __name__ == "__main__":
    main()
