#!/usr/bin/env python
"""ds16: the 2026-27 season's houses, and the thrower in the yellow toque.

ds15a was trained on last season's ice. This season sheet 4's buttons carry a
painted portrait, which ds15a reads as a yellow stone at 0.92-0.94 in 97% of
frames -- so every sheet 4 house holds a phantom yellow on the button, and the
detected score gives yellow every end. And on bLkgfZaDSKw, red's third wears a
yellow toque: from above it is a round yellow blob sliding up the centre line
behind the red stone, and ds15a reads each of their releases yellow.

Two kinds of frame, all cut from the videos the worker has cached:

* houses: 100 frames from ten 2026-27 videos on sheets 1-4 -- settled houses
  a couple of seconds after a rock stops (on sheet 4, weighted towards real
  stones resting on or beside the portrait), and the throwing house mid-end,
  where the portrait shows bare among the players. Half of them are sheet 4.
* toque: red's third's twelve slides out of the hack on bLkgfZaDSKw, twice
  each.

Every frame is pre-labelled by the pipeline's detector at a low confidence and
carries a note saying what to look for; the review decides. Writes the set
(images/labels, split ``train``; folds are chosen at training time), the review
pages, and the manifest, which is the committed half of the dataset.

    python scripts/ds16/build_season.py --timelines DIR --videos DIR --out DIR \
        --manifest datasets/ds16/manifest.json
"""

import argparse
import json
from pathlib import Path

import cv2
import numpy as np

from curling_score import weights as weights_mod
from curling_score.detect import yolo
from curling_score.ingest import frames as F
from curling_score.train import labels

PRELABEL_CONF = 0.05          # as ds15: the reviewer is shown what ds15a nearly saw
IMGSZ = 448                   # what the pipeline detects at
SETTLED_S = 2.0               # after a rock's rest, for a settled house
THROWING_AFTER_S = 8.0        # after a release: the stone is gone, the house is bare
# (video, source, sheet, settled, throwing): how many of each kind of house.
VIDEOS = [
    ("bLkgfZaDSKw", "s_1aAUMPjTB3eCfHqHM", 4, 9, 7),
    ("Y_XxbP1-G_o", "s_0qR8SYtWpF8o2hCtq", 4, 10, 7),
    ("8un0-JBKkLA", "s_11MefKj8rSvZiFrlA", 4, 10, 7),
    ("qEtMW7vWiYA", "s_06xNpsdZAJoCvuffR", 1, 8, 4),
    ("2z7vOezY9Bw", "s_1ICAFKqsG5OaONluo", 2, 6, 2),
    ("Po27AF7podc", "s_1cGUTmZWBAxJNx9Ne", 2, 4, 2),
    ("wZlFn8PoGPk", "s_0m4tRBUh2g7pby2qS", 2, 3, 1),
    ("PHbZ3EKOhMI", "s_0N8Q2sB4vY8Hv4Ooq", 3, 6, 2),
    ("qMDNIgIGHZs", "s_0J7c7G9Gj22Nmj5dp", 3, 6, 2),
    ("a57Go8rhkIo", "s_0PXVGfZmiusxIlyX5", 3, 3, 1),
]
# On sheet 4, this many of each video's settled houses are ones where a real
# stone rests within NEAR_BUTTON_M of the tee, over or beside the portrait.
NEAR_BUTTON = 5
NEAR_BUTTON_M = (0.06, 0.35)
TOQUE_VIDEO, TOQUE_SOURCE = "bLkgfZaDSKw", "s_1aAUMPjTB3eCfHqHM"
# Where red's third's slide is: the side camera's reference crossing, 2 m behind
# the throwing tee; the toque passes the tee about a second later.
TOQUE_OFFSETS_S = (0.3, 1.1)
# End 2 rock 10 has no release in the timeline: the overhead read it yellow and
# the rules dropped it. The long camera timed its slide here.
TOQUE_EXTRA = {(2, 10): 1364.85}
OTHER = {"top": "bottom", "bottom": "top"}

PORTRAIT = ("Sheet 4's button has a painted portrait: it is NOT a stone. Reject a box "
            "on it unless a real stone covers it; a stone on the portrait is still a stone.")
TOQUE = ("Red's third wears a yellow toque: the round yellow head sliding behind the "
         "stone is a person, NOT a stone. The stone in front of them is red.")
EVERY = "Label every stone in the panel, parked ones at the edges included."


def stem_for(vid, house, t):
    return f"{vid}_{house[0]}_{t:09.2f}".replace(".", "_")


def frame_at(video, t, rect):
    for tt, img in F.window(video, t, t + 0.2, fps=30.0, crop=rect):
        return tt, img
    return None, None


def spread(items, k, rng):
    """``k`` of ``items`` spread evenly through the game, jittered."""
    if k <= 0 or not items:
        return []
    if k >= len(items):
        return list(items)
    idx = np.linspace(0, len(items) - 1, k)
    idx = np.clip(np.round(idx + rng.uniform(-0.4, 0.4, k)), 0, len(items) - 1).astype(int)
    return [items[i] for i in sorted(set(idx.tolist()))]


def rocks(doc):
    """Every rock seen, with its end's house."""
    for g in doc["games"]:
        for e in g["ends"]:
            for s in e["shots"]:
                if not s["missing"] and s.get("t_rest_s") is not None:
                    yield e, s


def plan_video(vid, sheet, doc, n_settled, n_throwing, rng):
    """(t, panel, kind, note) for one video's house frames."""
    all_rocks = list(rocks(doc))
    out = []
    settled = all_rocks
    if sheet == 4:
        near = [(e, s) for e, s in all_rocks
                if any(NEAR_BUTTON_M[0] <= st["distance_to_tee"] < NEAR_BUTTON_M[1]
                       for st in s["stones"])]
        picked = spread(near, min(NEAR_BUTTON, n_settled), rng)
        for e, s in picked:
            out.append((s["t_rest_s"] + SETTLED_S, e["house"], "settled-near-button",
                        f"e{e['number']}s{s['number']} settled; a real stone near the button"))
        n_settled -= len(picked)
        taken = {id(s) for _e, s in picked}
        settled = [(e, s) for e, s in all_rocks if id(s) not in taken]
    for e, s in spread(settled, n_settled, rng):
        out.append((s["t_rest_s"] + SETTLED_S, e["house"], "settled",
                    f"e{e['number']}s{s['number']} settled, {len(s['stones'])} stones read"))
    thrown = [(e, s) for e, s in all_rocks if s.get("t_release_s") is not None]
    for e, s in spread(thrown, n_throwing, rng):
        out.append((s["t_release_s"] + THROWING_AFTER_S, OTHER[e["house"]], "throwing-house",
                    f"e{e['number']}s{s['number']} thrown {THROWING_AFTER_S:.0f} s ago; "
                    "the throwing house mid-end"))
    return out


def plan_toque(doc):
    out = []
    for g in doc["games"]:
        for e in g["ends"]:
            for s in e["shots"]:
                if s["color"] != "red" or s.get("position") != "third":
                    continue
                t = s.get("t_release_s") or TOQUE_EXTRA.get((e["number"], s["number"]))
                if t is None:
                    continue
                for dt in TOQUE_OFFSETS_S:
                    out.append((t + dt, OTHER[e["house"]], "toque",
                                f"e{e['number']}s{s['number']} red third's slide, +{dt:.1f} s"))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--timelines", type=Path, required=True,
                    help="a directory of tl_<source>.json, the served timelines")
    ap.add_argument("--videos", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--manifest", type=Path, required=True)
    ap.add_argument("--seed", type=int, default=16)
    args = ap.parse_args()
    out = args.out.expanduser()
    img_dir, lbl_dir = out / "set/images/train", out / "set/labels/train"
    for d in (img_dir, lbl_dir):
        d.mkdir(parents=True, exist_ok=True)
    det = yolo.YoloDetector(weights_mod.default_path(), conf=PRELABEL_CONF, imgsz=IMGSZ)
    rng = np.random.default_rng(args.seed)

    jobs = []
    for vid, sid, sheet, n_settled, n_throwing in VIDEOS:
        doc = json.loads((args.timelines / f"tl_{sid}.json").read_text())
        for t, panel, kind, why in plan_video(vid, sheet, doc, n_settled, n_throwing, rng):
            jobs.append((vid, sid, sheet, doc, t, panel, kind, why))
    toque_doc = json.loads((args.timelines / f"tl_{TOQUE_SOURCE}.json").read_text())
    for t, panel, kind, why in plan_toque(toque_doc):
        jobs.append((TOQUE_VIDEO, TOQUE_SOURCE, 4, toque_doc, t, panel, kind, why))

    frames_out, meta = [], {}
    for vid, sid, sheet, doc, t, panel, kind, why in jobs:
        cal = doc["calibration"][panel]
        tt, img = frame_at(args.videos / f"{vid}.mp4", t, tuple(cal["rect"]))
        if img is None:
            print("no frame", vid, t, panel)
            continue
        stem = stem_for(vid, panel, tt)
        if any(f["stem"] == stem for f in frames_out):
            continue
        cv2.imwrite(str(img_dir / f"{stem}.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 95])
        res = det.model.predict(img, conf=PRELABEL_CONF, imgsz=IMGSZ, verbose=False)[0]
        rows = [f"{int(c)} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}"
                for c, (cx, cy, w, h) in zip(res.boxes.cls.tolist(), res.boxes.xywhn.tolist())]
        (lbl_dir / f"{stem}.txt").write_text("\n".join(rows) + ("\n" if rows else ""))
        tee = [round(v, 1) for v in cal["center_px"]]
        notes = [f"sheet {sheet}, {kind}: {why}."]
        if sheet == 4:
            notes.append(f"{PORTRAIT} (button at col {tee[0]:.0f}, row {tee[1]:.0f})")
        if kind == "toque":
            notes.append(TOQUE)
        notes.append(EVERY)
        meta[stem] = labels.FrameMeta(note=" ".join(notes),
                                      url=f"https://youtu.be/{vid}?t={int(tt)}")
        frames_out.append({"stem": stem, "video": vid, "source": sid, "sheet": sheet,
                           "panel": panel, "kind": kind, "t": round(tt, 3), "why": why,
                           "tee_px": tee, "prelabels": len(rows)})
        print(f"{stem} {kind:20s} {len(rows)} prelabels", flush=True)

    counts = {}
    for f in frames_out:
        counts[f["kind"]] = counts.get(f["kind"], 0) + 1
    manifest = {"what": "ds16: the 2026-27 season's houses (sheet 4's portrait button) "
                        "and red's third's yellow toque on bLkgfZaDSKw",
                "prelabel": {"weights": weights_mod.default_path().name, "conf": PRELABEL_CONF,
                             "imgsz": IMGSZ, "classes": det.model.names},
                "seed": args.seed, "counts": counts, "frames": frames_out}
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(manifest, indent=1))
    frames = [f for seq in labels.load_split(out / "set", "train").values() for f in seq]
    page, n = labels.render_clickable(frames, out / "review", title="ds16: 2026-27 houses and the toque",
                                      scope="ds16:train", per_page=200, meta=meta)
    print(f"{len(frames_out)} frames {counts}, {n} page(s): {page}")


if __name__ == "__main__":
    main()
