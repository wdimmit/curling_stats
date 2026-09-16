"""Colour scan against trained detector, on the crossings a person timed.

``datasets/hogmarks/VXU9xwmugRg.json`` holds 27 crossings a person marked
frame by frame against the paint. That is the only ground truth this project
has for when a stone actually crossed, and it is what
``tests/test_longview.py::TestAgainstHandMarkedCrossings`` measures against --
a test marked slow, so the ordinary suite never runs it.

Both detectors get the same window, the same view, the same gates. Only the
step that says "there is a stone at this row in this frame" differs. Writes
each model-timed crossing's frame out so the timing can be checked by eye
rather than only by arithmetic.
"""
import argparse
import json
import statistics
import sys
from collections import Counter
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--marks", default="datasets/hogmarks/VXU9xwmugRg.json")
    ap.add_argument("--weights", default="weights/ds13b.pt")
    ap.add_argument("--views", required=True, help="banked sideviews json")
    ap.add_argument("--out", required=True)
    ap.add_argument("--fps", type=float, default=30.0)
    args = ap.parse_args()

    import cv2
    import numpy as np
    from ultralytics import YOLO

    from curling_score.detect import longview, sidemodel
    from curling_score.harvest import sideviews as SV

    out = Path(args.out)
    (out / "frames").mkdir(parents=True, exist_ok=True)
    doc = json.loads(Path(args.marks).read_text())
    vid = doc["video_id"]
    views = dict(SV.usable_views(
        SV.from_json(json.loads(Path(args.views).read_text())[vid])))
    model = YOLO(args.weights)

    rows = []
    for end in doc["ends"]:
        view = views[end["side_view"]]
        for m in end["marks"]:
            lo = m["release_t_s"] + longview.WINDOW_S[0]
            hi = m["release_t_s"] + longview.WINDOW_S[1]
            frames, times = longview.decode(args.video, view.rect, lo, hi,
                                            args.fps)
            if not len(frames):
                continue
            classic = longview.find_in_frames(frames, view, m["color"], times)
            modelled = sidemodel.find_in_frames(model, frames, view,
                                                m["color"], times)
            truth = m["hog_crossing_s"]
            rows.append({
                "release": m["release_t_s"], "color": m["color"],
                "truth": truth, "view": end["side_view"],
                "classic_t": classic.t, "classic_key": classic.key,
                "classic_err": None if classic.t is None else classic.t - truth,
                "model_t": modelled.t, "model_key": modelled.key,
                "model_err": None if modelled.t is None else modelled.t - truth,
            })

            if modelled.t is not None:
                # The frame nearest the model's answer, with the fitted hog
                # line drawn: the timing is only believable if the stone is on
                # the paint in the picture.
                i = min(range(len(times)), key=lambda j: abs(times[j] - modelled.t))
                img = np.ascontiguousarray(np.asarray(frames[i])[..., ::-1])
                hog = int(round(view.hog_row))
                cv2.line(img, (0, hog), (img.shape[1], hog), (0, 0, 255), 2)
                err = modelled.t - truth
                cv2.putText(img, f"model {modelled.t:.2f}s  hand {truth:.2f}s  "
                                 f"err {err:+.3f}s", (8, 30),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                            (0, 255, 0) if abs(err) <= 0.15 else (0, 180, 255),
                            2, cv2.LINE_AA)
                band = img[max(0, hog - 120):hog + 120, :]
                cv2.imwrite(str(out / "frames" /
                                f"{m['release_t_s']:08.1f}_{m['color']}.jpg"),
                            band, [cv2.IMWRITE_JPEG_QUALITY, 90])

    (out / "compare.json").write_text(json.dumps(rows, indent=1))

    def report(tag):
        timed = [r for r in rows if r[f"{tag}_t"] is not None]
        errs = [abs(r[f"{tag}_err"]) for r in timed]
        keys = Counter(r[f"{tag}_key"] for r in rows if r[f"{tag}_t"] is None)
        within = sum(1 for e in errs if e <= 0.15)
        print(f"\n{tag}:")
        print(f"  timed        {len(timed)}/{len(rows)}")
        if errs:
            print(f"  |error|      median {statistics.median(errs):.3f}s  "
                  f"worst {max(errs):.3f}s")
            print(f"  within 0.15s {within}/{len(timed)}  "
                  f"({100 * within / len(rows):.0f}% of all {len(rows)})")
        if keys:
            print(f"  refused      {dict(keys)}")

    print(f"{len(rows)} hand-marked crossings")
    report("classic")
    report("model")
    return 0


if __name__ == "__main__":
    sys.exit(main())
