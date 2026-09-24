"""Cut the destination-facing long camera just before each tee crossing, for
labelling broom heads with SAM.

Phase 0 (docs/superpowers/specs/2026-09-23-target-broom-design.md) found the
skip's pad down and still across t_tee-2.0..+0.5 on 45 of 49 shots, so two
frames per shot, 0.7 s apart inside the pipeline's own window, are two looks at
one placement rather than two placements. The camera is the one at the throwing
end, CAMERA_FOR[end.house] -- the other one from hogtime's for the same end.

    ./.venv/bin/python scripts/broom/harvest.py \\
        --timeline ~/curling-work/ds15/games/timelines/VXU9xwmugRg.json \\
        --video ~/.cache/curling_score/videos/VXU9xwmugRg.mp4 \\
        --out ~/curling-work/broom/wave1 \\
        --manifest datasets/broom/manifest-wave1.json --scope broom:wave1
"""
import argparse
import dataclasses
import json
import sys
from pathlib import Path

import cv2
import numpy as np

from curling_score.geometry import sideview

OFFSETS_S = (-1.0, -0.3)
CAMERA_FOR = {"top": "left", "bottom": "right"}     # game/hogtime.CAMERA_FOR
ABOVE_TEE_ROWS = 130        # the skip's legs and the shaft, above the house
PAST_Y_M, BELOW_PAD = 3.0, 15   # down to 3 m in front of the tee, and a margin
PLATE_FRAMES = 24           # analyze.CALIB_FRAMES: frames medianed into a plate


def view_for(entry: dict, frames=None) -> sideview.SideView:
    """One side view as the timeline calibrated it, with its lateral scale.

    Timelines from before the lateral calibration carry only the depth rows, so
    the scale is fitted here from ``frames`` -- the view's own crops, far apart
    in time, medianed into a clean plate exactly as analyze does. Without it a
    labelled box could not be mapped back to the house, and the broom set is
    scored through exactly that mapping.
    """
    view = sideview.SideView(
        rect=tuple(entry["rect"]), tee_row=entry["tee_row"],
        hog_row=entry["hog_row"], centre_col=entry.get("centre_col"),
        lat_px_per_m_at_tee=entry.get("lat_px_per_m_at_tee"))
    if view.has_lateral or frames is None:
        return view
    plate = np.median(np.stack([np.asarray(f, np.float32) for f in frames]), axis=0)
    own = dataclasses.replace(view, rect=(0, 0, view.rect[2], view.rect[3]))
    return dataclasses.replace(sideview.solve_lateral(plate, own), rect=view.rect)


def manifest_row(side: sideview.SideView, **fields) -> dict:
    """A frame's manifest entry: what it is, plus everything ``to_house`` needs."""
    return {**fields, "rect": list(side.rect), "tee_row": side.tee_row,
            "hog_row": side.hog_row, "centre_col": side.centre_col,
            "lat_px_per_m_at_tee": side.lat_px_per_m_at_tee}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeline", required=True)
    ap.add_argument("--video", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--scope", required=True)
    ap.add_argument("--every", type=int, default=1)
    args = ap.parse_args()

    from curling_score.detect import longview
    from curling_score.harvest.sideframes import stem_for
    from curling_score.train import boxedit

    doc = json.loads(Path(args.timeline).expanduser().read_text())
    vid = doc["source"]["video_id"]
    cal = doc["calibration"]
    video = Path(args.video).expanduser()
    out = Path(args.out).expanduser()
    (out / "images").mkdir(parents=True, exist_ok=True)

    shots = [(e, s) for g in doc["games"] for e in g["ends"] for s in e["shots"]
             if not s.get("missing") and s.get("t_tee_s") is not None]
    ts = [s["t_tee_s"] for _, s in shots]
    views = {}
    for n in ("left", "right"):
        frames = []
        if "centre_col" not in cal[n]:
            for t in np.linspace(min(ts), max(ts), PLATE_FRAMES):
                f, _ = longview.decode(video, tuple(cal[n]["rect"]), float(t),
                                       float(t) + 0.2, fps=5)
                if len(f):
                    frames.append(f[0])
        views[n] = view_for(cal[n], frames or None)
        print(f"{n}: centre col {views[n].centre_col:.1f}, "
              f"{views[n].lat_px_per_m_at_tee:.1f} px/m at the tee", flush=True)
    items, manifest, skipped = [], [], 0
    for i, (end, shot) in enumerate(shots):
        if i % args.every:
            continue
        name = CAMERA_FOR[end["house"]]
        view = views[name]
        top = max(0, int(view.tee_row - ABOVE_TEE_ROWS))
        bot = min(view.rect[3], int(view.row_for(PAST_Y_M) + BELOW_PAD))
        geom = boxedit.frame_geometry(view, longview.STONE_WIDTH_AT_HOG_PX,
                                      row_offset=top)
        for off in OFFSETS_S:
            t = round(shot["t_tee_s"] + off, 2)
            if t < 0:
                skipped += 1
                continue
            frames, _ = longview.decode(video, view.rect, t, t + 0.05, fps=30)
            if not len(frames):
                skipped += 1        # past the end of the video, or a bad seek
                continue
            crop = cv2.cvtColor(frames[0][top:bot], cv2.COLOR_RGB2BGR)
            stem = stem_for(vid, name, t)
            cv2.imwrite(str(out / "images" / f"{stem}.jpg"), crop,
                        [cv2.IMWRITE_JPEG_QUALITY, 92])
            items.append({"stem": stem, "image": f"images/{stem}.jpg",
                          "width": crop.shape[1], "height": crop.shape[0],
                          "boxes": [], "geom": geom})
            manifest.append(manifest_row(
                view, stem=stem, video_id=vid, view=name, t_abs=t,
                t_tee=shot["t_tee_s"], offset=off,
                tee_estimated=bool(shot.get("t_tee_estimated")),
                end=end["number"], shot=shot["number"], color=shot["color"],
                crop_top=top, width=crop.shape[1], height=crop.shape[0]))
        print(f"\r{i + 1}/{len(shots)} shots", end="", flush=True)

    (out / "items.json").write_text(json.dumps(items))
    Path(args.manifest).write_text(json.dumps(manifest, indent=1) + "\n")
    page = boxedit.render(items, out, scope=args.scope, kind="broom",
                          title=f"Broom heads -- {vid}")
    n_shots = len({(m["end"], m["shot"]) for m in manifest})
    print(f"\n{len(items)} frames from {n_shots} shots"
          f"{f', {skipped} skipped (outside the video)' if skipped else ''}; "
          f"page {page}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
