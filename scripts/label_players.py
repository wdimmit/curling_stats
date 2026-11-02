#!/usr/bin/env python
"""Publish overhead frames as player items, for members to box the people in.

    python scripts/label_players.py publish --images DIR --labels DIR \\
        --dataset overhead-3 --fold all --batch players-1 --weights yolo11x-pose.pt
    ... publish ... --dry-run --out /tmp/players-look

DIR is an existing YOLO dataset folder: images (any depth below --images, as in
`images/train` and `images/val`) and a `<stem>.txt` of stone labels (0 red,
1 yellow) under --labels at the same relative path. Each image becomes one
item whose `starts` are the people a COCO pose model (yolo11x-pose, person
class, 640 px, confidence >= 0.1) finds, and whose `stones` are the known
stones, drawn on the page for context only. The member boxes every player,
head to feet as seen from above, not the broom, and a player cut off at the
edge up to the edge; the answer trains the next stone detector's player class.

Pose runs on the CPU, so it can share a box with a production worker; give a
long run a low `nice`. The images go up as they are on disk, a page of items at a time. ADMIN_TOKEN and
BASE_URL are read as for scripts/label_fp.py (BASE_URL defaults to
https://curling.dimmit.net; the token is tried with one cheap admin read before
the model loads, and is never printed). --weights, or POSE_WEIGHTS, is the
pose checkpoint; nothing is downloaded.

--dry-run sends nothing and needs no token. --out, with or without it, writes
each item's JSON and `contact.jpg`, the starts (green) and stones over 40 random
frames, for a look before publishing.

An item's id is `p_` and a hash of dataset/fold/stem (label_publish.opaque_id),
so it says nothing about the video, and the same frame is the same id on every
run; dataset, fold and stem stay in the item's origin, which members are never
sent. Rerunning changes nothing already open.

The floor is 0.1, the lowest of 0.1, 0.15 and 0.25 whose junk stayed well under
a box a frame. Counted by eye on 30 frames (20 random overhead-3 training
frames and the 10 frames of player_mine.py's smoke run, 74 players):
0.25 found 42 and missed 32, with 1 junk box; 0.15 found 49, missed 25, junk
1; 0.1 found 54, missed 20, junk 2 (one a second box on a player already
boxed). At 0.25 most frames went out with players unboxed, which invites
members to press Enter on them. The rest are mostly players seen straight
down or down on the ice, which the model misses at any confidence.

Boxes are the model's own, not trimmed to its keypoints plus a margin. Both
were rendered on 30 overhead-3 frames and looked at: the keypoints are
unreliable on a top-down body, so the trim pulled boxes off hands and feet and
collapsed a player cut off at the frame's edge to a sliver (several of the 30
frames), while the raw box held the whole body and only now and then a few
pixels of broom. The member adjusts a box that
is a little loose far more easily than one that has lost a limb.
"""

import argparse
import json
import math
import os
import random
import sys
from pathlib import Path

import numpy as np

_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import label_publish  # noqa: E402

POSE_CONF = 0.1
POSE_IMGSZ = 640
# The server refuses an answer box under this size (service.labelling.MIN_BOX_PX),
# and one more than 1 px outside the image, so a start is clipped and kept only
# if it stays a box.
MIN_BOX_PX = 3.0
CONTACT_FRAMES = 40
CONTACT_COLS, CONTACT_TILE = 8, (200, 344)
SUFFIXES = (".jpg", ".jpeg", ".png")
COLORS = ("red", "yellow")


def load_model(path: str):
    from ultralytics import YOLO

    return YOLO(path)


def pose_starts(img, model) -> list[dict]:
    """The people ``model`` (a yolo11x-pose) finds in a BGR image, as starts.

    Boxes are in the image's pixels, clipped to the image and dropped if
    under MIN_BOX_PX once clipped. Runs on the CPU.
    """
    h, w = img.shape[:2]
    res = model.predict(img, imgsz=POSE_IMGSZ, conf=POSE_CONF, classes=[0], device="cpu",
                        verbose=False)[0]
    out = []
    if res.boxes is None:
        return out
    for b, c, cf in zip(res.boxes.xyxy.cpu().numpy(), res.boxes.cls.cpu().numpy(),
                        res.boxes.conf.cpu().numpy()):
        if int(c) != 0 or cf < POSE_CONF:
            continue
        x0, y0 = max(0.0, float(b[0])), max(0.0, float(b[1]))
        x1, y1 = min(float(w), float(b[2])), min(float(h), float(b[3]))
        if x1 - x0 < MIN_BOX_PX or y1 - y0 < MIN_BOX_PX:
            continue
        out.append({"x0": round(x0, 1), "y0": round(y0, 1), "x1": round(x1, 1),
                    "y1": round(y1, 1), "conf": round(float(cf), 3)})
    return out


def stones_from_label(txt: str, w: int, h: int) -> list[dict]:
    """The stones of a YOLO label file, in pixels, clipped to the image.

    Classes other than 0 and 1, short or non-numeric lines, and stones with
    nothing left inside the image are ignored; the page draws them for context
    only, but never off the canvas.
    """
    out = []
    for line in txt.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0] not in ("0", "1"):
            continue
        try:
            cx, cy, bw, bh = (float(v) for v in parts[1:5])
        except ValueError:
            continue
        if not all(math.isfinite(v) for v in (cx, cy, bw, bh)):
            continue
        x0, y0 = max(0.0, (cx - bw / 2) * w), max(0.0, (cy - bh / 2) * h)
        x1, y1 = min(float(w), (cx + bw / 2) * w), min(float(h), (cy + bh / 2) * h)
        if x1 <= x0 or y1 <= y0:
            continue
        out.append({"x0": round(x0, 1), "y0": round(y0, 1), "x1": round(x1, 1),
                    "y1": round(y1, 1), "color": COLORS[int(parts[0])]})
    return out


def item_for(dataset: str, fold: str, stem: str, w: int, h: int, starts: list[dict],
             stones: list[dict], batch: str, extra_origin: dict) -> dict:
    """The create call's item for one frame; the server sets payload["image"]."""
    origin = {"dataset": dataset, "fold": fold, "stem": stem, **extra_origin}
    return {"id": label_publish.opaque_id("p_", f"{dataset}/{fold}/{stem}"), "kind": "player",
            "batch": batch,
            "payload": {"w": int(w), "h": int(h), "starts": starts, "stones": stones,
                        "origin": origin}}


def _frames(images: Path) -> list[Path]:
    return sorted(p for p in images.rglob("*") if p.suffix.lower() in SUFFIXES)


def contact_sheet(frames: list[tuple]) -> np.ndarray:
    """A grid of ``(image, item)``: each frame's starts in green and stones in their colour."""
    import cv2

    tw, th = CONTACT_TILE
    tiles = []
    for img, item in frames:
        p = item["payload"]
        k = min(tw / p["w"], th / p["h"])
        tile = cv2.resize(img, (max(1, round(p["w"] * k)), max(1, round(p["h"] * k))))
        for key, bgr in (("stones", None), ("starts", (0, 200, 0))):
            for b in p[key]:
                color = bgr or ((0, 0, 255) if b["color"] == "red" else (0, 220, 255))
                cv2.rectangle(tile, (round(b["x0"] * k), round(b["y0"] * k)),
                              (round(b["x1"] * k), round(b["y1"] * k)), color, 1)
        canvas = np.full((th, tw, 3), 255, np.uint8)
        canvas[:tile.shape[0], :tile.shape[1]] = tile
        tiles.append(canvas)
    rows = -(-len(tiles) // CONTACT_COLS)
    tiles += [np.full((th, tw, 3), 255, np.uint8)] * (rows * CONTACT_COLS - len(tiles))
    return np.vstack([np.hstack(tiles[r * CONTACT_COLS:(r + 1) * CONTACT_COLS])
                      for r in range(rows)])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("publish", help="pre-box and publish the frames")
    p.add_argument("--images", type=Path, required=True, metavar="DIR")
    p.add_argument("--labels", type=Path, required=True, metavar="DIR")
    p.add_argument("--dataset", required=True)
    p.add_argument("--fold", required=True)
    p.add_argument("--batch", required=True, help="the items' batch name")
    p.add_argument("--weights", default=os.environ.get("POSE_WEIGHTS"), metavar="PT",
                   help="the yolo11x-pose checkpoint (default $POSE_WEIGHTS)")
    p.add_argument("--dry-run", action="store_true",
                   help="send nothing; write the items and a contact sheet to --out")
    p.add_argument("--out", type=Path, metavar="DIR")
    args = ap.parse_args(argv)

    if args.dry_run and not args.out:
        print("--dry-run needs --out", file=sys.stderr)
        return 2
    if not args.images.is_dir():
        print(f"no such images folder: {args.images}", file=sys.stderr)
        return 2
    if not args.weights:
        print("give --weights or set POSE_WEIGHTS", file=sys.stderr)
        return 2
    token = os.environ.get("ADMIN_TOKEN", "").strip()
    if not args.dry_run and not token:
        print("set ADMIN_TOKEN (see scripts/users.py)", file=sys.stderr)
        return 2
    base = os.environ.get("BASE_URL", "https://curling.dimmit.net").rstrip("/")

    if not args.dry_run:
        refused = label_publish.preflight(base, token)
        if refused:
            print(refused, file=sys.stderr)
            return 1

    import cv2

    model = load_model(args.weights)
    # Only the contact sheet's frames stay decoded, and an image's bytes are read
    # again when its page is published: a run over thousands of frames on a box
    # shared with workers holds the items and paths, not the pixels.
    paths = _frames(args.images)
    looked_at = set(random.Random(0).sample(range(len(paths)), min(CONTACT_FRAMES, len(paths))))
    items, files, frames, skipped = [], {}, [], 0
    for n, path in enumerate(paths):
        try:
            img = cv2.imread(str(path))
            if img is None:
                raise ValueError("not a readable image")
            h, w = img.shape[:2]
            label = args.labels / path.relative_to(args.images).with_suffix(".txt")
            stones = stones_from_label(label.read_text(), w, h) if label.is_file() else []
            item = item_for(args.dataset, args.fold, path.stem, w, h, pose_starts(img, model),
                            stones, args.batch, {})
        except Exception as err:            # one bad frame must not stop the rest
            skipped += 1
            print(f"{path.name}: {type(err).__name__}: {err}, skipped", file=sys.stderr)
            continue
        items.append(item)
        files[item["id"]] = path
        if n in looked_at:
            frames.append((img, item))

    ids = [i["id"] for i in items]
    if len(set(ids)) != len(ids):
        print("two frames map to the same item id; nothing sent", file=sys.stderr)
        return 1
    people = sum(len(i["payload"]["starts"]) for i in items)
    print(f"{len(items)} items in batch {args.batch}, {people} pre-boxed people"
          + (f", {skipped} skipped" if skipped else ""))
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        for item in items:
            (args.out / f"{item['id']}.json").write_text(json.dumps(item, indent=1))
        if frames:
            cv2.imwrite(str(args.out / "contact.jpg"), contact_sheet(frames))
        print(f"wrote {len(items)} item files and contact.jpg to {args.out}")
    if args.dry_run:
        print("dry run: sent nothing")
        return 0
    got = {"opened": [], "skipped": [], "missing_image": []}
    try:
        for i in range(0, len(items), label_publish.PAGE):
            page = items[i:i + label_publish.PAGE]
            page_got = label_publish.publish(base, token, page,
                                             {x["id"]: files[x["id"]].read_bytes() for x in page})
            for k in got:
                got[k] += page_got[k]
    except OSError as err:                  # URLError and HTTPError are OSErrors
        print(f"could not publish: {label_publish.why(err)}", file=sys.stderr)
        return 1
    print(f"opened {len(got['opened'])}, already past pending {len(got['skipped'])}, "
          f"image missing {len(got['missing_image'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
