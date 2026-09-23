"""The deliveries the arriving panel never sees cross its hog line.

After the release guard came off, `no_far_hog_crossing` is the largest single
loss: 40 of the 55 unpublished crossings on VXU9xwmugRg. The diagnostics say
something very specific about them. Every one of the 40 has a delivery track --
median 54 points -- and every one of those tracks STARTS at its far end,
0.058 m short of that panel's own painted hog line (`geometry/hogpaint.HogLine`,
found per panel -- this measurement predates it and was taken against the old
global `split.HOG_APPARENT_Y_M`) on median.

So the stone is not lost. It appears in the panel already past the line, by
about four tenths of a stone's radius. The crossing happens just outside the
frame.

This renders the moment: the arriving panel across the second either side of
the track's first point, with the hog line drawn where the calibration puts
it. If the reading is right, the stone should be arriving at the top edge with
the line above it, out of shot.
"""
import argparse
import json
import sys
from pathlib import Path

# ``replay_end.py`` lives under ``scripts/``, one level up from this script.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--refused", required=True, help="no_far_hog.json")
    ap.add_argument("--out", required=True)
    ap.add_argument("--limit", type=int, default=6)
    ap.add_argument("--cache-root", default=None)
    args = ap.parse_args()

    import cv2
    import numpy as np

    from curling_score.ingest import cache, frames as F
    import replay_end

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rows = json.loads(Path(args.refused).read_text())[:args.limit]

    root = Path(args.cache_root) if args.cache_root else cache.default_root()
    vid = Path(args.video).stem
    setups, panels = replay_end.setups_for(args.video, vid, root)
    print(f"panels: top {panels.top} bottom {panels.bottom}", flush=True)

    for r in rows:
        # Which panel holds the delivery is not in the dump, so both are shown
        # and the one with the stone in it is the arriving end.
        strips = []
        for name, rect in (("top", panels.top), ("bottom", panels.bottom)):
            setup = setups[name] if isinstance(setups, dict) else getattr(setups, name)
            imgs = [im for _t, im in F.window(args.video, r["track_t0"] - 1.0,
                                              r["track_t0"] + 1.0, fps=3.0,
                                              crop=rect)]
            if not imgs:
                continue
            row = np.hstack(imgs)
            line = setup.hog_line
            if line is not None:
                pts = [(int(c), int(round(np.polyval(line.coef, c)))) for c in range(0, rect[2], 4)]
                for k in range(len(imgs)):
                    off = k * rect[2]
                    for (c0, r0), (c1, r1) in zip(pts, pts[1:]):
                        cv2.line(row, (off + c0, r0), (off + c1, r1), (0, 0, 255), 1)
                cv2.putText(row, "hog (paint outer edge)", (4, 14),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 255), 1)
            else:
                cv2.putText(row, f"no hog line: {setup.hog_line_error}", (6, 18),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 200, 255), 1)
            lab = np.full((20, row.shape[1], 3), 20, np.uint8)
            cv2.putText(lab, f"{name} panel", (4, 14),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (230, 230, 230), 1)
            strips.append(np.vstack([row, lab]))
        if not strips:
            continue
        w = max(s.shape[1] for s in strips)
        strips = [cv2.copyMakeBorder(s, 0, 0, 0, w - s.shape[1],
                                     cv2.BORDER_CONSTANT, value=(20, 20, 20))
                  for s in strips]
        img = np.vstack(strips)
        cap = np.full((24, img.shape[1], 3), 20, np.uint8)
        cv2.putText(cap, f"e{r['end']}s{r['shot']} {r['color']}  track starts "
                         f"y={r['y_max']:.3f} m, first sighting", (4, 17),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (230, 230, 230), 1)
        cv2.imwrite(str(out / f"e{r['end']}s{r['shot']}_{r['color']}.jpg"),
                    np.vstack([img, cap]), [cv2.IMWRITE_JPEG_QUALITY, 88])
        print(f"  e{r['end']}s{r['shot']} {r['color']}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
