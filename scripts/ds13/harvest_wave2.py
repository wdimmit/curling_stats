"""Wave 2: widen the set using the model wave 1 trained.

Wave 1 came from nine whole VODs and their shot lists, which is narrow -- one
rink over nine nights, and ``datasets/ds11/README.md`` is explicit that narrow
sets are how this project previously got detectors that scored well and saw
poorly. Widening properly meant new video, and ds11's 120-video clip corpus is
already on the worker: 10 clips a video, 7.2 GB, with side views already
calibrated in ``datasets/ds13/sideviews.json``.

Clips cannot give a shot list -- 24 seconds holds no ends to segment -- which
is what made them useless for wave 1. They are fine now, because ``ds13a``
finds stones directly. The expensive machinery that found deliveries is not
needed to *grow* the set, only to time them.

**Selecting by where the detector fires is the ds3 trap**, and this is written
to avoid it. ds3 scored mAP50 0.977 while finding 11 of 17 hand-observed
deliveries, because its labels came from the detector being validated. So the
per-video quota is not "the 20 the model liked most":

    8  confident  (>= CONF_SURE)      -- cheap confirmations, hold precision
    8  uncertain  (CONF_LOW..SURE)    -- the decision boundary, where a label
                                         actually changes the model
    4  nothing found in the band      -- candidate misses, and true negatives

Uncertainty is the half that earns its keep. A frame the model already calls
0.9 teaches it almost nothing; a frame it calls 0.4 is where it is deciding.
"""
import argparse
import json
import random
import sys
from collections import Counter
from pathlib import Path

import cv2
import numpy as np

CONF_LOW, CONF_SURE = 0.25, 0.70
QUOTA = {"sure": 8, "unsure": 8, "none": 4}
SCAN_FPS = 1.5


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clips", default="/data/wdd/curling/ds11/clips")
    ap.add_argument("--views", default="datasets/ds13/sideviews.json")
    ap.add_argument("--videos", required=True, help="json list of video ids")
    ap.add_argument("--weights", default="weights/ds13a.pt")
    ap.add_argument("--out", required=True)
    ap.add_argument("--per-video", type=int, default=20)
    args = ap.parse_args()

    from curling_score.detect import longview
    from curling_score.harvest import sidepool, sideviews as SV
    from curling_score.ingest import frames as F
    from curling_score.train import boxedit
    from ultralytics import YOLO

    out = Path(args.out)
    (out / "images").mkdir(parents=True, exist_ok=True)
    views_doc = json.loads(Path(args.views).read_text())
    wanted = json.loads(Path(args.videos).read_text())
    model = YOLO(args.weights)

    items, tally = [], Counter()
    for n, vid in enumerate(wanted, 1):
        raw = views_doc.get(vid)
        if raw is None:
            print(f"[{n}/{len(wanted)}] {vid}: no views, skipped", flush=True)
            continue
        vv = SV.from_json(raw)
        views = dict(SV.usable_views(vv))
        clips = sorted((Path(args.clips) / vid).glob("*.mkv"))
        if not clips or not views:
            print(f"[{n}/{len(wanted)}] {vid}: no clips or no views", flush=True)
            continue

        seen = []
        for clip in clips:
            offset = F.stream_start_s(clip)
            for name, view in views.items():
                y0, y1 = sidepool.band_crop(view)
                for t, img in F.window(clip, 0.0, 24.0, fps=SCAN_FPS,
                                       crop=view.rect):
                    lo, hi = max(0, y0), min(img.shape[0], y1)
                    crop = img[lo:hi, :]
                    if crop.size == 0:
                        continue
                    geom = boxedit.frame_geometry(
                        view, longview.STONE_WIDTH_AT_HOG_PX, row_offset=lo)
                    res = model.predict(crop, imgsz=800, conf=CONF_LOW,
                                        verbose=False)[0]
                    boxes, best = [], 0.0
                    for b, c, cf in zip(res.boxes.xyxy.cpu().numpy(),
                                        res.boxes.cls.cpu().numpy(),
                                        res.boxes.conf.cpu().numpy()):
                        x0, yy0, x1, yy1 = (float(v) for v in b)
                        expect = geom["k"] * (yy1 - geom["yh"])
                        # Gate on geometry, not confidence: the one bad box in
                        # the cold evaluation was 11.44x the predicted width at
                        # 0.65 confidence, which confidence alone would keep.
                        if not (expect > 1 and 0.5 <= (x1 - x0) / expect <= 2.0):
                            continue
                        boxes.append([int(c), (x0 + x1) / 2 / crop.shape[1],
                                      (yy0 + yy1) / 2 / crop.shape[0],
                                      (x1 - x0) / crop.shape[1],
                                      (yy1 - yy0) / crop.shape[0]])
                        best = max(best, float(cf))
                    bucket = ("sure" if best >= CONF_SURE
                              else "unsure" if boxes else "none")
                    seen.append({"clip": clip.name, "view": name,
                                 "t_abs": round(t + offset, 2), "bucket": bucket,
                                 "boxes": boxes, "conf": round(best, 3),
                                 "crop": crop, "geom": geom})

        rng = random.Random(hash(vid) & 0xFFFF)
        by_bucket = {}
        for s in seen:
            by_bucket.setdefault(s["bucket"], []).append(s)
        chosen = []
        for bucket, want in QUOTA.items():
            pool = by_bucket.get(bucket, [])
            rng.shuffle(pool)
            # One frame per clip-view before a second from any: two frames
            # 0.7 s apart are the same moment wearing two names.
            spread, used = [], Counter()
            for s in sorted(pool, key=lambda s: used[(s["clip"], s["view"])]):
                if used[(s["clip"], s["view"])] >= 2:
                    continue
                spread.append(s)
                used[(s["clip"], s["view"])] += 1
                if len(spread) >= want:
                    break
            chosen.extend(spread)
            tally[f"{bucket}:{len(spread)}/{want}"] += 0
            tally[bucket] += len(spread)

        for s in chosen[:args.per_video]:
            stem = (f"{vid}_{s['view'][0]}_{s['t_abs']:09.2f}").replace(".", "_")
            cv2.imwrite(str(out / "images" / f"{stem}.jpg"), s["crop"],
                        [cv2.IMWRITE_JPEG_QUALITY, 92])
            items.append({"stem": stem, "image": f"images/{stem}.jpg",
                          "width": s["crop"].shape[1],
                          "height": s["crop"].shape[0],
                          "boxes": s["boxes"], "geom": s["geom"],
                          "bucket": s["bucket"], "conf": s["conf"],
                          "video_id": vid})
        print(f"[{n}/{len(wanted)}] {vid}: scanned {len(seen)}, kept "
              f"{len(chosen[:args.per_video])}", flush=True)

    (out / "items.json").write_text(json.dumps(items))
    print(f"\n{len(items)} frames from {len({i['video_id'] for i in items})} videos")
    print("by bucket:", dict(Counter(i["bucket"] for i in items)))
    print("frames with a proposed box:", sum(1 for i in items if i["boxes"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
