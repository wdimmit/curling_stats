#!/usr/bin/env python
"""edge1: stones overhead-3 counts on a sheet with no play on it (2026-11-04).

11/03 Tuesday Super League sheet 4 (T2jGXKs2s9Q) charted its 7 pm and 9 pm
games as one: four minutes into the changeover, with both houses empty, a tan
broom head poking into the top panel's corner behind the back line read as a
yellow stone for two keyframes (7920 and 7925 s, conf 0.61 / 0.44). The
activity profile ran end 8 on to it, and the empty sheet that should have ended
the game came out 115 s instead of ~280.

Every keyframe away from play -- more than PAD_S from any end's first entry or
last rest, by the rocks rather than the segment bounds, which such a hit
stretches -- both panels, counted as the live profile counts them (production
calibration, sideline filter, CONF, IMGSZ). Every hit goes to ``hits.jsonl``;
the surest PER_VIDEO per video, those centred past the back line first,
MIN_GAP_S apart, are cut as panel crops with an empty label, the training
format of the member folds. Away from play is not the same as empty -- a
practice throw after a game is a real stone -- so every crop is checked by
eye before it is trained on as empty (``contact.py``).

    python edge_mine.py --kept LIST --timelines DIR --out DIR

LIST: lines of "<cache root> <video id>"; DIR: tl_<source id>.json, every
game of a video.
"""

import argparse
import json
from collections import defaultdict
from pathlib import Path

import cv2

from curling_score import analyze, weights as W
from curling_score.geometry import constants as C
from curling_score.ingest import frames as F
from curling_score.live.session import VideoPipeline

CONF, IMGSZ = 0.30, 448
PAD_S = 30.0
PER_VIDEO = 6
MIN_GAP_S = 60.0
PANEL = {"top": "t", "bottom": "b"}


def play_windows(docs):
    """(t0, t1) of every end, from its first rock's entry to its last rock's
    rest, padded by PAD_S; merged and sorted."""
    spans = []
    for doc in docs:
        for g in doc.get("games", []):
            for e in g["ends"]:
                ts = [t for s in e["shots"] for t in (s.get("t_enter_s"), s.get("t_rest_s"),
                                                      s.get("t_video_s")) if t is not None]
                ts += [e["start_s"]] if not ts else []
                if ts:
                    spans.append((min(ts) - PAD_S, max(ts) + PAD_S))
    out = []
    for a, b in sorted(spans):
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def away_from_play(windows, length_s):
    out, t = [], 0.0
    for a, b in windows:
        if a > t:
            out.append((t, a))
        t = max(t, b)
    if t < length_s:
        out.append((t, length_s))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kept", type=Path, required=True)
    ap.add_argument("--timelines", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    (a.out / "images").mkdir(parents=True, exist_ok=True)
    (a.out / "labels").mkdir(parents=True, exist_ok=True)
    docs = defaultdict(list)
    for p in sorted(a.timelines.glob("tl_*.json")):
        try:
            doc = json.loads(p.read_text())
        except ValueError:
            continue
        docs[doc["source"]["video_id"]].append(doc)
    done = set()
    if (a.out / "done.txt").exists():
        done = set((a.out / "done.txt").read_text().split())
    detector, _, _ = analyze.load_models(W.default_path(), IMGSZ, None, skip_longview=True,
                                         skip_line=True)
    pipeline = VideoPipeline(skip_longview=True, line=False)
    for line in a.kept.read_text().splitlines():
        root, vid = line.split()
        if vid in done or vid not in docs:
            continue
        video = Path(root) / "videos" / f"{vid}.mp4"
        length_s = max(d["source"].get("duration_s") or 0 for d in docs[vid])
        try:
            setups = pipeline.calibrate(str(video), 900.0).setups
        except Exception as exc:  # noqa: BLE001 - one bad video must not stop the sweep
            print(vid, "calibration failed:", exc, flush=True)
            continue
        hits = []
        for t0, t1 in away_from_play(play_windows(docs[vid]), length_s):
            for t, img in F.keyframe_sweep(str(video), start_s=t0, end_s=t1):
                crops = {k: setups[k].crop(img) for k in PANEL}
                found = detector.find_stones_each([crops["top"], crops["bottom"]],
                                                  [setups["top"].calib, setups["bottom"].calib])
                for k, stones in zip(PANEL, found):
                    for s in setups[k].on_sheet(stones):
                        hits.append({"video": vid, "t": round(t, 2), "panel": k,
                                     "color": s.color, "conf": round(float(s.confidence), 3),
                                     "x_m": round(s.x_m, 3), "y_m": round(s.y_m, 3),
                                     "x_px": round(s.x_px, 1), "y_px": round(s.y_px, 1),
                                     "area_px": round(s.area_px, 1),
                                     "past_back": s.y_m < -C.TEE_TO_BACKLINE_M,
                                     "_crop": crops[k]})
        # Back-line hits first, then the surest; spread out in time.
        picked = []
        for h in sorted(hits, key=lambda h: (not h["past_back"], -h["conf"])):
            if len(picked) >= PER_VIDEO:
                break
            if any(abs(h["t"] - q["t"]) < MIN_GAP_S for q in picked):
                continue
            picked.append(h)
        for h in picked:
            stem = f"{vid}_{PANEL[h['panel']]}_{h['t']:09.2f}".replace(".", "_") + "_edge1"
            h["stem"] = stem
            cv2.imwrite(str(a.out / "images" / f"{stem}.jpg"), h["_crop"],
                        [cv2.IMWRITE_JPEG_QUALITY, 95])
            (a.out / "labels" / f"{stem}.txt").write_text("")
        with open(a.out / "hits.jsonl", "a") as fh:
            for h in hits:
                fh.write(json.dumps({k: v for k, v in h.items() if k != "_crop"}) + "\n")
        with open(a.out / "done.txt", "a") as fh:
            fh.write(vid + "\n")
        print(vid, "away-from-play hits", len(hits), "past back",
              sum(h["past_back"] for h in hits), "picked", len(picked), flush=True)


if __name__ == "__main__":
    main()
