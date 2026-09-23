#!/usr/bin/env python
"""ds15: frames of arriving stones the overhead detector found late.

For every arriving rock on the three locally cached games whose destination
hog-line crossing is not observed -- reached back for (``split.far_crossing``)
or first seen too far past the paint to reach for at all -- cut the
destination panel at the moment the stone should have been on the line, and
midway from there to where ds11a first saw it. Each frame is pre-labelled by
ds11a at a low confidence, and carries a note saying where to look: the
detector's miss is the point, so the reviewer mostly adds boxes.

Writes the set (images/labels, split ``train``; folds are chosen at training
time), context crops at the first sighting, the review pages, and
``datasets/ds15/manifest.json``, which records every rock and frame and is the
committed half of the dataset.

    PYTHONPATH=src python scripts/ds15/build_farhog.py --out ~/curling-work/ds15
"""

import argparse
import json
import pickle
from pathlib import Path

import cv2

from curling_score import weights as weights_mod
from curling_score.detect import yolo
from curling_score.game import split
from curling_score.ingest import frames as F
from curling_score.train import labels

REPO = Path(__file__).resolve().parents[2]
GAMES = {"AEqLTgM25Tc": Path.home() / ".cache/curling_replay",
         "VXU9xwmugRg": Path.home() / ".cache/curling_score",
         "hOKZoeJNTpM": Path.home() / ".cache/curling_score"}
PRELABEL_CONF = 0.05
IMGSZ = 448                   # what the pipeline detects at
# For a stone too far past the line to reach back for, a four-point fit may
# still say when it crossed; beyond this lead it is guesswork, and a nominal
# approach speed (panel units per second, typical near the far edge) is used.
NONE_LEAD_MAX_S = 3.0
NOMINAL_SPEED_U = 0.6


def fit_crossing(track, line):
    """Uncapped version of split.far_crossing's fit: (t, lead, how)."""
    pts = sorted((float(t), float(x), float(y)) for t, x, y in track)
    t0, x0, y0 = pts[0]
    y_line = line.y_at(x0)
    early = pts[:4]
    if len(early) == 4:
        my = sum(p[2] for p in early) / 4
        mt = sum(p[0] for p in early) / 4
        den = sum((p[2] - my) ** 2 for p in early)
        if den > 0:
            slope = sum((p[2] - my) * (p[0] - mt) for p in early) / den
            t = mt + (y_line - my) * slope
            if 0 < t0 - t <= NONE_LEAD_MAX_S:
                return t, t0 - t, "fit"
    lead = min(max((y_line - y0) / NOMINAL_SPEED_U, 0.2), NONE_LEAD_MAX_S)
    return t0 - lead, lead, "nominal"


def frame_at(video, t, rect):
    for tt, img in F.window(video, t, t + 0.2, fps=30.0, crop=rect):
        return tt, img
    return None, None


def stem_for(vid, house, t):
    return f"{vid}_{house[0]}_{t:09.2f}".replace(".", "_")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    out = args.out.expanduser()
    img_dir, lbl_dir, ctx_dir = (out / "set/images/train", out / "set/labels/train",
                                 out / "context")
    for d in (img_dir, lbl_dir, ctx_dir):
        d.mkdir(parents=True, exist_ok=True)
    det = yolo.YoloDetector(weights_mod.default_path(), conf=PRELABEL_CONF, imgsz=IMGSZ)
    names = det.model.names

    rocks, meta, counts = [], {}, {}
    for vid, root in GAMES.items():
        # Our own cache, written by replay_end.setups_for on this machine.
        setups, _panels = pickle.loads((root / f"setups-{vid}.hogline.pkl").read_bytes())
        video = root / "videos" / f"{vid}.mp4"
        for e in json.loads((root / f"{vid}-phase3.json").read_text()):
            house = e["house"]
            st = setups[house]
            for s in e["shots"]:
                tr = [tuple(p) for p in s.get("track") or ()]
                if not tr:
                    counts["no_track"] = counts.get("no_track", 0) + 1
                    continue
                if split.line_crossing(tr, st.hog_line) is not None:
                    counts["observed"] = counts.get("observed", 0) + 1
                    continue
                t_first, x_first, y_first = min(tr)
                t_x, reach = split.far_crossing(tr, st.hog_line, max_reach=split.FAR_REACH_MAX_U)
                if t_x is not None:
                    kind, how, lead = "reached", "far_crossing", t_first - t_x
                else:
                    kind = "none"
                    t_x, lead, how = fit_crossing(tr, st.hog_line)
                    reach = st.hog_line.y_at(x_first) - y_first
                counts[kind] = counts.get(kind, 0) + 1
                y_line = st.hog_line.y_at(x_first)
                rock = {"video": vid, "end": e["end"], "shot": s["shot"], "color": s["color"],
                        "panel": house, "kind": kind, "estimate": how,
                        "t_first": t_first, "t_cross_est": round(t_x, 3),
                        "lead_s": round(lead, 3), "reach_u": round(reach, 4), "frames": []}
                _tc, ctx = frame_at(video, t_first, st.rect)
                ctx_path = None
                if ctx is not None:
                    ctx_path = ctx_dir / f"{stem_for(vid, house, t_first)}_first.jpg"
                    cv2.imwrite(str(ctx_path), ctx, [cv2.IMWRITE_JPEG_QUALITY, 95])
                for frac, tag in ((0.0, "line"), (0.5, "mid")):
                    t_want = t_x + frac * (t_first - t_x)
                    tt, img = frame_at(video, t_want, st.rect)
                    if img is None:
                        continue
                    stem = stem_for(vid, house, tt)
                    if any(f["stem"] == stem for f in rock["frames"]):
                        continue      # a short reach-back: mid is the line frame
                    cv2.imwrite(str(img_dir / f"{stem}.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 95])
                    res = det.model.predict(img, conf=PRELABEL_CONF, imgsz=IMGSZ, verbose=False)[0]
                    rows = [f"{int(c)} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}"
                            for c, (cx, cy, w, h) in zip(res.boxes.cls.tolist(),
                                                         res.boxes.xywhn.tolist())]
                    (lbl_dir / f"{stem}.txt").write_text("\n".join(rows) + ("\n" if rows else ""))
                    y_exp = y_line + frac * (y_first - y_line)
                    col, row = st.calib.to_pixels(x_first, y_exp)
                    note = (f"{s['color']} e{e['end']}s{s['shot']} {tag}: expect near "
                            f"col {col:.0f}, row {row:.0f} ({kind}, lead {lead:.2f}s)")
                    meta[stem] = labels.FrameMeta(
                        note=note, neighbours=(ctx_path,) if ctx_path else ())
                    rock["frames"].append({"stem": stem, "t": round(tt, 3), "tag": tag,
                                           "expect_px": [round(col, 1), round(row, 1)],
                                           "prelabels": len(rows)})
                rocks.append(rock)
        print(vid, {k: v for k, v in counts.items()}, flush=True)

    manifest = {"what": "ds15: arriving stones the overhead detector found past the far hog line",
                "prelabel": {"weights": weights_mod.default_path().name, "conf": PRELABEL_CONF,
                             "imgsz": IMGSZ, "classes": names},
                "counts": counts, "rocks": rocks}
    dst = REPO / "datasets/ds15/manifest.json"
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(manifest, indent=1))
    frames = [f for seq in labels.load_split(out / "set", "train").values() for f in seq]
    page, n = labels.render_clickable(frames, out / "review", title="ds15: far hog line",
                                      scope="ds15:train", per_page=250, meta=meta)
    print(f"{len(rocks)} rocks, {len(frames)} frames, {n} page(s): {page}")


if __name__ == "__main__":
    main()
