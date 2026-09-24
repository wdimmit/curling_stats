"""Score the broom pass on a held-out game against the user's boxes.

For each shot, the truth is every pad the user boxed on the shot's t_tee-0.3
frame, at its foot, in house metres. The pass decodes [t_tee-1, t_tee] at
broomtime.FPS, runs the model and pick_target -- exactly what analyze does. The
spec's bar:
  - at least 80% of shots with a pad boxed get a marker within 0.30 m of one;
  - at most 5% of markers are more than 0.60 m from every boxed pad, or sit on
    a shot with none boxed.

    ./.venv/bin/python scripts/broom/eval_heldout.py --weights <best.pt> \\
        --video ~/.cache/curling_score/videos/hOKZoeJNTpM.mp4 \\
        --manifest datasets/broom/manifest-wave2-hokz.json \\
        --edits 'datasets/broom/edits/broom-wave2-hokz-*.json'
"""
import argparse
import glob
import json
import math
import sys
from pathlib import Path

HIT_M, WILD_M = 0.30, 0.60


def _view(row):
    from curling_score.geometry.sideview import SideView
    return SideView(rect=tuple(row["rect"]), tee_row=row["tee_row"],
                    hog_row=row["hog_row"], centre_col=row["centre_col"],
                    lat_px_per_m_at_tee=row["lat_px_per_m_at_tee"])


def truth(row, boxes):
    """Each boxed pad's foot, in house metres."""
    v = _view(row)
    return [v.to_house(b[1] * row["width"],
                       row["crop_top"] + (b[2] + b[4] / 2) * row["height"])
            for b in boxes]


def score(results):
    """``results``: ``(truth_points, marker_or_None)`` per shot."""
    with_pad = hits = markers = wild = 0
    for pts, m in results:
        near = (min((math.hypot(m[0] - x, m[1] - y) for x, y in pts),
                    default=math.inf) if m is not None else math.inf)
        if pts:
            with_pad += 1
            hits += near <= HIT_M
        if m is not None:
            markers += 1
            wild += near > WILD_M
    return {"with_pad": with_pad, "hits": hits, "markers": markers, "wild": wild,
            "hit_rate": hits / with_pad if with_pad else None,
            "wild_rate": wild / markers if markers else None}


def main() -> int:
    from ultralytics import YOLO

    from curling_score.detect import broommodel, longview
    from curling_score.game import broomtime
    from curling_score.train import labels

    ap = argparse.ArgumentParser()
    for a in ("--weights", "--video", "--manifest", "--edits"):
        ap.add_argument(a, required=True)
    args = ap.parse_args()
    model = YOLO(str(Path(args.weights).expanduser()))
    edits = labels.merge_edits(*(json.loads(Path(f).read_text())
                                 for f in sorted(glob.glob(args.edits))))
    rows = [r for r in json.loads(Path(args.manifest).read_text())
            if r["offset"] == -0.3 and r["stem"] in edits.boxes]
    results = []
    for r in rows:
        v = _view(r)
        frames, _ = longview.decode(Path(args.video).expanduser(), v.rect,
                                    r["t_tee"] - broomtime.WINDOW_S, r["t_tee"],
                                    broomtime.FPS)
        samples = [(i, *v.to_house(p.col, p.row), p.conf)
                   for i, pads in enumerate(broommodel.find(model, frames, v))
                   for p in pads]
        m = broomtime.pick_target(samples, len(frames))
        results.append((truth(r, edits.boxes[r["stem"]]),
                        None if m is None else (m.x_m, m.y_m)))
    print(json.dumps(score(results), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
