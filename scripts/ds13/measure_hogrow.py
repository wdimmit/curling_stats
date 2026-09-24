"""What row IS the hog line, according to the person who marked the crossings?

`geometry/sideview._hog_row` fits the line by luminance: the row whose dip
against the rows above it is deepest. That fit is what every timing rests on,
and on this video's LEFT view it appears to be a few pixels low -- every
crossing there reads about 0.09 s late while the right view reads +0.006 s.

This measures the row without using the fitter at all. For each hand-marked
crossing, ds13b's track says where the stone's edge was at every moment; the
mark says when the person judged it to touch the paint. Interpolating the
track to that instant gives the row the paint is actually on, by the same
definition the marks use. The median over a view's marks is that view's answer.

The luminance profile around both rows is printed beside it, so the fit can be
judged against the pixels rather than against another number.
"""
import argparse
import json
import statistics
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--marks", default="datasets/hogmarks/VXU9xwmugRg.json")
    ap.add_argument("--weights", default="weights/ds13b.pt")
    ap.add_argument("--views", required=True)
    ap.add_argument("--fps", type=float, default=30.0)
    args = ap.parse_args()

    import numpy as np
    from ultralytics import YOLO

    from curling_score.detect import longview, sidemodel
    from curling_score.harvest import sideviews as SV

    doc = json.loads(Path(args.marks).read_text())
    vid = doc["video_id"]
    views = dict(SV.usable_views(
        SV.from_json(json.loads(Path(args.views).read_text())[vid])))
    model = YOLO(args.weights)

    by_view: dict[str, list] = {}
    for end in doc["ends"]:
        name = end["side_view"]
        view = views[name]
        for m in end["marks"]:
            lo = m["release_t_s"] + longview.WINDOW_S[0]
            hi = m["release_t_s"] + longview.WINDOW_S[1]
            frames, times = longview.decode(args.video, view.rect, lo, hi,
                                            args.fps)
            if not len(frames):
                continue
            tracks = sidemodel.propose(model, frames, view, m["color"], times)
            best = max(tracks.values(), key=len, default=None)
            if best is None or len(best) < 2:
                continue
            truth = m["hog_crossing_s"]
            # The two samples straddling the person's instant.
            pair = None
            for a, b in zip(best, best[1:]):
                if a[0] <= truth <= b[0]:
                    pair = (a, b)
                    break
            if pair is None:
                continue
            (t0, r0, *_), (t1, r1, *_) = pair
            row = r0 + (truth - t0) / (t1 - t0) * (r1 - r0)
            by_view.setdefault(name, []).append(row)

    print(f"{vid}\n")
    for name, rows in sorted(by_view.items()):
        view = views[name]
        med = statistics.median(rows)
        print(f"{name}:")
        print(f"  fitted hog_row      {view.hog_row:7.2f}")
        print(f"  marks say it is     {med:7.2f}   (n={len(rows)}, "
              f"sd {statistics.pstdev(rows):.2f} px)")
        print(f"  fit is low by       {med - view.hog_row:+7.2f} px")

        # The pixels themselves, so the fit can be judged against them.
        frames, _ = longview.decode(args.video, view.rect,
                                    doc["ends"][0]["marks"][0]["release_t_s"],
                                    doc["ends"][0]["marks"][0]["release_t_s"] + 0.2,
                                    2.0)
        if len(frames):
            lum = np.asarray(frames[0], np.float32).mean(axis=(1, 2))
            a, b = int(view.hog_row) - 8, int(view.hog_row) + 8
            print(f"  luminance {a}..{b}:")
            for i in range(a, b + 1):
                bar = "#" * max(0, int((lum[i] - lum[a:b].min()) * 1.2))
                flag = " <- fitted" if i == int(round(view.hog_row)) else ""
                flag += " <- marks" if i == int(round(med)) else ""
                print(f"    {i}  {lum[i]:6.1f} {bar}{flag}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
