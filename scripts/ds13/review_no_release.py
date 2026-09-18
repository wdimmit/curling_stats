"""The shots the side view can time but the overhead panel never released.

After ds13b took over as the crossing proposer, 44 of the 90 unpublished
splits on VXU9xwmugRg fail as `no_release`: `split.long_split` has a hog
crossing from the side, and no release track from the panel to pair it with.
That is now the largest single loss, and it is entirely upstream of the side
view -- so it is worth looking at what the panel was actually seeing.

For each such shot this builds a filmstrip of the THROWING panel across the
seconds the release should be in, with the side view's own crossing frame
beside it for reference. The side view says a stone went past; the strip says
what the panel had to work with while it did.

The window is anchored on the side crossing rather than on the shot list,
because the shot list is what failed: these shots have no release time, so
there is nothing in them to anchor to except the stone's arrival.
"""
import argparse
import json
import math
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--windows", required=True, help="banked windows json")
    ap.add_argument("--views", required=True)
    ap.add_argument("--video-id", required=True)
    ap.add_argument("--weights", default="weights/ds13b.pt")
    ap.add_argument("--out", required=True)
    ap.add_argument("--limit", type=int, default=24)
    args = ap.parse_args()

    import cv2
    import numpy as np
    from ultralytics import YOLO

    from curling_score import analyze as analyze_mod
    from curling_score.detect import longview, sidemodel
    from curling_score.geometry import layout
    from curling_score.harvest import sideviews as SV
    from curling_score.ingest import frames as F

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    wins = json.loads(Path(args.windows).read_text())
    views = dict(SV.usable_views(
        SV.from_json(json.loads(Path(args.views).read_text())[args.video_id])))
    model = YOLO(args.weights)

    # The panels, so the strip can show the end the stone was thrown from.
    plates = F.sample_keyframes(args.video, count=analyze_mod.CALIB_FRAMES,
                                stride=analyze_mod.CALIB_STRIDE)
    panels = layout.detect_panels(plates)
    # A side camera watches the OTHER end, so the throwing panel is the one
    # whose own hog line this camera sees -- the same alternation as CAMERA_FOR.
    PANEL_FOR = {"left": panels.top, "right": panels.bottom}

    no_rel = [w for w in wins if w["t_release"] is None]
    print(f"{len(no_rel)} windows with no release; showing up to {args.limit}",
          flush=True)

    rows = []
    for w in no_rel[:args.limit]:
        view = views.get(w["camera"])
        if view is None:
            continue
        frames, times = longview.decode(args.video, view.rect, w["t0"], w["t1"],
                                        30.0)
        if not len(frames):
            continue
        got = sidemodel.find_in_frames(model, frames, view, w["color"], times)
        if got.t is None:
            rows.append({"end": w["end_number"], "shot": w["shot_number"],
                         "color": w["color"], "camera": w["camera"],
                         "t_hog": None, "why": got.key})
            continue

        # The panel strip: the seconds before the crossing, where a release is.
        px, py, pw, ph = PANEL_FOR[w["camera"]]
        t0, t1 = got.t - 7.0, got.t - 1.0
        strip, st = F_window(F, args.video, t0, t1, 2.0, (px, py, pw, ph))
        if not strip:
            continue
        stem = f"e{w['end_number']}s{w['shot_number']:02d}_{w['color']}"
        tiles = []
        for t, img in zip(st, strip):
            lab = np.full((18, img.shape[1], 3), 20, np.uint8)
            cv2.putText(lab, f"{got.t - t:.1f}s before", (2, 13),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.38, (220, 220, 220), 1)
            tiles.append(np.vstack([img, lab]))
        panel_row = np.hstack(tiles)

        i = min(range(len(times)), key=lambda j: abs(times[j] - got.t))
        side = np.ascontiguousarray(np.asarray(frames[i])[..., ::-1])
        hog = int(round(view.hog_row))
        cv2.line(side, (0, hog), (side.shape[1], hog), (0, 0, 255), 2)
        side = side[max(0, hog - 90):hog + 90, :]
        side = cv2.resize(side, (int(side.shape[1] * panel_row.shape[0]
                                     / max(1, side.shape[0])),
                                 panel_row.shape[0]))
        cv2.imwrite(str(out / f"{stem}.jpg"),
                    np.hstack([panel_row, side]),
                    [cv2.IMWRITE_JPEG_QUALITY, 88])
        rows.append({"end": w["end_number"], "shot": w["shot_number"],
                     "color": w["color"], "camera": w["camera"],
                     "t_hog": round(got.t, 2), "why": "timed"})
        print(f"  e{w['end_number']}s{w['shot_number']:02d} {w['color']:6s} "
              f"{w['camera']:5s} crossing {got.t:.2f}", flush=True)

    (out / "report.json").write_text(json.dumps(rows, indent=1))
    timed = sum(1 for r in rows if r["t_hog"] is not None)
    print(f"\n{timed}/{len(rows)} of the no-release shots still time a crossing "
          f"from the side view", flush=True)
    return 0


def F_window(F, video, t0, t1, fps, crop):
    imgs, ts = [], []
    for t, img in F.window(video, t0, t1, fps=fps, crop=crop):
        imgs.append(img)
        ts.append(t)
    return imgs, ts


if __name__ == "__main__":
    sys.exit(main())
