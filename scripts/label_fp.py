#!/usr/bin/env python
"""Publish the harvested false-positive frames as box items for members to label.

    docker run --rm -v /data/wdd/curling-cache:/c1:ro -v /data/wdd/curling-cache-2:/c2:ro \\
        -e ADMIN_TOKEN -e BASE_URL --entrypoint python curling-worker:local \\
        scripts/label_fp.py publish --caches /c1 /c2 [--since 2026-10-20] [--batch NAME]
    ... scripts/label_fp.py publish --caches /c1 /c2 --dry-run --out /out
    ... scripts/label_fp.py publish --checks DIR [--checks-camera overhead|side]

(from /data/wdd/curling_score/ on the worker box, or with the repo mounted and
PYTHONPATH=src; the image has the weights and ultralytics.) BASE_URL defaults
to https://curling.dimmit.net and ADMIN_TOKEN is the `curling-admin-token`
secret, as for scripts/users.py. Nothing needs a GPU: the pre-boxing runs on
the CPU.

Each row `fp_harvest.py list` counts (one pile per video and models) becomes an
item `b_<stem>`: the frame cropped to what a member needs to see, with the
current detectors' boxes drawn in as `starts` and the box the pipeline's check
doubted marked `flagged`. An overhead row's crop is its panel. A side row's is
a 720x360 window on the flagged box, kept inside the panel; ds13's own band
crop needs a calibrated view that frames.jsonl does not carry.

--checks takes a folder of `<stem>.jpg` and a `boxes.json` of
`{stem: [{"x0","y0","x1","y1","color"}]}`: frames already labelled by hand,
published whole as `bc_<stem>` with those boxes as the item's `reference`, so
a member's answer can be held against a known one. Boxes are in the image's
own pixels, and all the images are of one camera (--checks-camera, side by
default). The most confident pre-box is the flagged one; with none, a centred
40x17 box is.

An id the server would refuse (outside A-Za-z0-9_.- or past 120 characters) is
mapped by `item_id`: odd characters become `_`, and a long or changed stem
gets a hash of the original on the end, so the same stem is the same id on
every run. Rerunning on the same piles changes nothing already open and gives
a pending item a fresh upload (the server decides). --dry-run sends nothing
and needs no token: it writes each crop and item JSON to --out.
"""

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import fp_harvest  # noqa: E402
import label_publish  # noqa: E402
from curling_score.fpframes import store  # noqa: E402

CROP_W, CROP_H = 720, 360          # long camera, whole-frame px
PREBOX_CONF = 0.25
KEEP_INSIDE = 0.4                  # a detection is kept if >= 40% of its area is in the crop
SAME_BOX_IOU = 0.5                 # a detection this close to the flagged box is the flagged box
JPEG_QUALITY = 90
# The pipeline's own settings: analyze.analyze's imgsz for the overhead panels,
# and detect.sidemodel's for the side view.
OVERHEAD_IMGSZ, SIDE_IMGSZ = 448, 800
FRAME_W, FRAME_H = 1920, 1080
FALLBACK_BOX = (40, 17)            # a check frame the models find nothing in
ID_OK = re.compile(r"^[A-Za-z0-9_.-]{1,120}$")
ORIGIN_KEYS = ("video_id", "title", "t", "camera", "where", "check", "models", "pipeline", "stem")


def crop_for(row: dict) -> tuple[int, int, int, int]:
    """The (x, y, w, h) of a row's frame, in whole-frame pixels, to publish."""
    rx, ry, rw, rh = (int(v) for v in (row.get("rect") or (0, 0, FRAME_W, FRAME_H)))
    if row.get("camera") != "side":
        return rx, ry, rw, rh
    w, h = min(CROP_W, rw), min(CROP_H, rh)
    x0, y0, x1, y1 = row["box"]
    x = round((x0 + x1) / 2 - w / 2)
    y = round((y0 + y1) / 2 - h / 2)
    return max(rx, min(x, rx + rw - w)), max(ry, min(y, ry + rh - h)), w, h


def _area(b) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def _inter(a, b) -> float:
    return _area((max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])))


def _iou(a, b) -> float:
    i = _inter(a, b)
    return i / (_area(a) + _area(b) - i) if i else 0.0


def _box(d: dict) -> tuple:
    return d["x0"], d["y0"], d["x1"], d["y1"]


def starts_for(dets: list[dict], flagged: dict, crop: tuple) -> list[dict]:
    """The item's starts, in crop pixels: the detections and, last, the flagged box.

    ``dets`` and ``flagged`` are dicts of ``x0 y0 x1 y1 color conf`` in
    whole-frame pixels. A detection is clipped to the crop and dropped if less
    than KEEP_INSIDE of it is inside; one at IoU >= 0.5 with the flagged box
    is the flagged box and gives way to it. The flag's colour is the row's,
    else the detection it replaced, else red.
    """
    cx, cy, cw, ch = crop
    window = (cx, cy, cx + cw, cy + ch)
    fbox = _box(flagged)
    color, best = flagged.get("color"), 0.0
    out = []
    for d in dets:
        box = _box(d)
        iou = _iou(box, fbox)
        if iou >= SAME_BOX_IOU:
            if color is None and iou > best:
                color, best = d.get("color"), iou
            continue
        if not _area(box) or _inter(box, window) / _area(box) < KEEP_INSIDE:
            continue
        out.append(_start(_clip(box, window), crop, d.get("color") or "red", d.get("conf"), False))
    out.append(_start(_clip(fbox, window), crop, color or "red", flagged.get("conf"), True))
    return out


def _clip(box, window) -> tuple:
    return (max(box[0], window[0]), max(box[1], window[1]),
            min(box[2], window[2]), min(box[3], window[3]))


def _start(box, crop, color, conf, flagged) -> dict:
    x0, y0, x1, y1 = (round(v, 1) for v in (box[0] - crop[0], box[1] - crop[1],
                                            box[2] - crop[0], box[3] - crop[1]))
    return {"x0": x0, "y0": y0, "x1": x1, "y1": y1, "color": color,
            "conf": None if conf is None else round(float(conf), 3), "flagged": flagged}


def item_id(stem: str, prefix: str = "b_") -> str:
    """``b_<stem>``, mapped when the server's pattern would refuse it."""
    plain = prefix + stem
    if ID_OK.match(plain):
        return plain
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", plain)[:100]
    return safe + "-" + hashlib.sha1(stem.encode()).hexdigest()[:10]


def item_for(row: dict, crop: tuple, starts: list[dict], batch: str, *,
             prefix: str = "b_", reference: dict | None = None) -> dict:
    """The create call's item for one frame; the server sets payload["image"]."""
    origin = {k: row.get(k) for k in ORIGIN_KEYS}
    origin["crop"] = [int(v) for v in crop]
    item = {"id": item_id(row["stem"], prefix), "kind": "box", "batch": batch,
            "payload": {"w": int(crop[2]), "h": int(crop[3]), "starts": starts,
                        "origin": origin}}
    if reference is not None:
        item["reference"] = reference
    return item


def load_detectors():
    """``(detect, names)``: ``detect(img, camera)`` is the current weights' boxes on
    a BGR image, in that image's pixels, and ``names`` says which weights."""
    from ultralytics import YOLO

    from curling_score import weights

    paths = {"overhead": weights.default_path(), "side": weights.side_path()}
    models = {k: YOLO(str(p)) for k, p in paths.items() if p is not None}

    def detect(img, camera):
        model = models.get(camera)
        if model is None:
            return []
        res = model.predict(img, imgsz=SIDE_IMGSZ if camera == "side" else OVERHEAD_IMGSZ,
                            conf=PREBOX_CONF, device="cpu", verbose=False)[0]
        out = []
        if res.boxes is None:
            return out
        for b, c, cf in zip(res.boxes.xyxy.cpu().numpy(), res.boxes.cls.cpu().numpy(),
                            res.boxes.conf.cpu().numpy()):
            if int(c) in (0, 1):
                out.append({"x0": float(b[0]), "y0": float(b[1]), "x1": float(b[2]),
                            "y1": float(b[3]), "color": ("red", "yellow")[int(c)],
                            "conf": float(cf)})
        return out

    return detect, {k: p.stem for k, p in paths.items() if p is not None}


def _jpeg(img) -> bytes:
    import cv2
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
    if not ok:
        raise ValueError("could not encode the crop")
    return buf.tobytes()


def _shift(dets, dx, dy):
    return [dict(d, x0=d["x0"] + dx, y0=d["y0"] + dy, x1=d["x1"] + dx, y1=d["y1"] + dy)
            for d in dets]


def harvest_item(row: dict, detect, batch: str, names=None):
    """``(item, jpeg bytes)`` for one harvested row, or None when its frame
    cannot be read or the row lacks the box that says what was doubted."""
    import cv2
    path = store.video_dir(row["cache"], row["video_id"]) / f"{row['stem']}.jpg"
    img = cv2.imread(str(path))
    if img is None or not row.get("box"):
        return None
    crop = crop_for(row)
    x, y, w, h = crop
    dets = _shift(detect(img[y:y + h, x:x + w], row.get("camera")), x, y)
    x0, y0, x1, y1 = row["box"]
    flagged = {"x0": x0, "y0": y0, "x1": x1, "y1": y1,
               "color": row.get("color"), "conf": row.get("conf")}
    item = item_for(row, crop, starts_for(dets, flagged, crop), batch)
    if names:
        item["payload"]["origin"]["prebox"] = names
    return item, _jpeg(img[y:y + h, x:x + w])


def check_items(folder: Path, camera: str, detect, batch: str, names=None):
    """``(item, jpeg bytes)`` for each frame of a check folder."""
    import cv2
    boxes = json.loads((folder / "boxes.json").read_text())
    for stem in sorted(boxes):
        img = cv2.imread(str(folder / f"{stem}.jpg"))
        if img is None:
            print(f"check {stem}: no readable {stem}.jpg, skipped", file=sys.stderr)
            continue
        h, w = img.shape[:2]
        crop = (0, 0, w, h)
        dets = detect(img, camera)
        if dets:
            top = max(dets, key=lambda d: d["conf"])
            flagged = dict(top, color=None)
        else:
            fw, fh = FALLBACK_BOX
            flagged = {"x0": (w - fw) / 2, "y0": (h - fh) / 2, "x1": (w + fw) / 2,
                       "y1": (h + fh) / 2, "color": None, "conf": None}
        row = {"stem": stem, "camera": camera}
        ref = {"boxes": [{k: b[k] for k in ("x0", "y0", "x1", "y1", "color")}
                         for b in boxes[stem]]}
        item = item_for(row, crop, starts_for(dets, flagged, crop), batch,
                        prefix="bc_", reference=ref)
        if names:
            item["payload"]["origin"]["prebox"] = names
        yield item, _jpeg(img)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("publish", help="crop, pre-box and publish the frames")
    p.add_argument("--caches", nargs="+", default=[], metavar="DIR")
    p.add_argument("--since", type=date.fromisoformat, metavar="YYYY-MM-DD",
                   help="only frames saved on or after this day (UTC)")
    p.add_argument("--batch", help="the items' batch name (default fp-<today>)")
    p.add_argument("--checks", type=Path, metavar="DIR",
                   help="a folder of hand-labelled <stem>.jpg and boxes.json")
    p.add_argument("--checks-camera", choices=("side", "overhead"), default="side")
    p.add_argument("--dry-run", action="store_true",
                   help="send nothing; write each crop and item JSON to --out")
    p.add_argument("--out", type=Path, metavar="DIR")
    args = ap.parse_args(argv)

    if args.dry_run and not args.out:
        print("--dry-run needs --out", file=sys.stderr)
        return 2
    if not args.caches and not args.checks:
        print("give --caches and/or --checks", file=sys.stderr)
        return 2
    token = os.environ.get("ADMIN_TOKEN", "").strip()
    if not args.dry_run and not token:
        print("set ADMIN_TOKEN (see scripts/users.py)", file=sys.stderr)
        return 2
    base = os.environ.get("BASE_URL", "https://curling.dimmit.net").rstrip("/")
    batch = args.batch or f"fp-{date.today().isoformat()}"

    detect, names = load_detectors()
    made, skipped = [], 0
    if args.caches:
        kept, _dupes = fp_harvest.dedupe(fp_harvest.load(args.caches))
        if args.since:
            kept = fp_harvest._since(kept, args.since)
        for row in kept:
            got = harvest_item(row, detect, batch, names)
            if got is None:
                skipped += 1
                print(f"{row.get('stem')}: frame or box missing, skipped", file=sys.stderr)
            else:
                made.append(got)
    if args.checks:
        made.extend(check_items(args.checks, args.checks_camera, detect, batch, names))

    ids = [i["id"] for i, _ in made]
    if len(set(ids)) != len(ids):
        print("two frames map to the same item id; nothing sent", file=sys.stderr)
        return 1
    items = [i for i, _ in made]
    images = {i["id"]: jpg for i, jpg in made}
    print(f"{len(items)} items in batch {batch}" + (f", {skipped} skipped" if skipped else ""))
    if args.dry_run:
        args.out.mkdir(parents=True, exist_ok=True)
        for item in items:
            (args.out / f"{item['id']}.jpg").write_bytes(images[item["id"]])
            (args.out / f"{item['id']}.json").write_text(json.dumps(item, indent=1))
        print(f"dry run: wrote {len(items)} crops and item files to {args.out}; sent nothing")
        return 0
    try:
        got = label_publish.publish(base, token, items, images)
    except OSError as err:              # URLError and HTTPError are OSErrors
        print(f"could not publish: {label_publish.why(err)}", file=sys.stderr)
        return 1
    print(f"opened {len(got['opened'])}, already past pending {len(got['skipped'])}, "
          f"image missing {len(got['missing_image'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
