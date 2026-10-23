"""Harvest every hung card's raw window from this season's cached games.

    python scripts/cards2627/harvest.py <manifest.json> <out-dir> [--jobs 4]
        [--step 240]

Runs inside a worker container, with the caches mounted at the paths the
manifest names. For each game it reads the board the way production does
(`read_cards_at`'s median of keyframes, `find_board`, `is_readable`,
`read_slots`), every `--step` seconds from five minutes in to a minute before
the game's end -- never past it, where the next game's board would be read as
this one's. Each present slot keeps:

* its raw `card_window` (`win/<id>.png`), the window `cards.npz` stores, so the
  training set goes through production's `glyph_in_window` like the old set;
* the shipped model's read: digit, winning probability, and the runner-up.

and each read keeps the board crop (`board/<vid>_<t>.jpg`) for the review page.
One `reads.jsonl` per video, written last, is the resume marker: a video whose
file exists is skipped.
"""
import argparse
import json
import multiprocessing as mp
import os
from pathlib import Path

import cv2
import numpy as np

cv2.setNumThreads(1)

from curling_score.game import digits as D            # noqa: E402
from curling_score.game import scoreboard as SB       # noqa: E402
from curling_score.ingest import frames as F          # noqa: E402

HALF_WINDOW_S = 20.0


def board_at(path, t):
    imgs = [im for _, im in F.keyframe_sweep(path, start_s=t - HALF_WINDOW_S,
                                              end_s=t + HALF_WINDOW_S)]
    if len(imgs) < 3:
        return None
    return SB.median_frame(imgs[:40])


def harvest_video(task):
    vid, games, out, step = task
    out = Path(out)
    done = out / vid / "reads.jsonl"
    if done.exists():
        return vid, None
    (out / vid / "win").mkdir(parents=True, exist_ok=True)
    (out / vid / "board").mkdir(parents=True, exist_ok=True)
    model = D.load_default_model()
    rows = []
    for g in games:
        path = f"{g['root']}/videos/{vid}.mp4"
        t = g["start_s"] + 300.0
        times = []
        while t < g["end_s"] - 60.0:
            times.append(t)
            t += step
        times.append(g["end_s"] - 60.0)
        for t in times:
            img = board_at(path, t)
            if img is None:
                continue
            geom = SB.find_board(img)
            if geom is None or not SB.is_readable(img, geom):
                continue
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            present = SB.read_slots(img, geom)
            tag = f"{vid}_{int(t):06d}"
            bx0, bx1 = int(geom.anchor_x - geom.dy), int(geom.slot_x[-1] + geom.dy)
            by0 = int(geom.top_line_y - 0.6 * geom.dy)
            by1 = int(geom.bottom_line_y + 0.3 * geom.dy)
            cv2.imwrite(str(out / vid / "board" / f"{tag}.jpg"),
                        img[max(0, by0):by1, max(0, bx0):bx1])
            for color in ("yellow", "red"):
                for slot in sorted(getattr(present, color)):
                    win = SB.card_window(gray, geom, color, slot)
                    if win is None:
                        continue
                    sid = f"{tag}_{color[0]}{slot:02d}"
                    cv2.imwrite(str(out / vid / "win" / f"{sid}.png"), win)
                    ink = SB.glyph_in_window(win)
                    rec = dict(id=sid, video_id=vid, source_id=g["source_id"],
                               game_index=g["game_index"], sheet=g["sheet"],
                               date=g["played_at"][:10], t_s=t, color=color,
                               slot=slot, digit=None, conf=0.0, second=None,
                               second_p=0.0)
                    if ink is not None and ink.size >= 4:
                        p = model.probs(D.as_input(SB._normalise(ink)))[0]
                        order = np.argsort(p)[::-1]
                        rec.update(digit=int(D.LABELS[order[0]]),
                                   conf=round(float(p[order[0]]), 6),
                                   second=int(D.LABELS[order[1]]),
                                   second_p=round(float(p[order[1]]), 6))
                    rows.append(rec)
    tmp = done.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(r) + "\n" for r in rows))
    os.replace(tmp, done)
    return vid, len(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("manifest")
    ap.add_argument("out")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--step", type=float, default=240.0)
    args = ap.parse_args()
    by_vid = {}
    for g in json.load(open(args.manifest)):
        by_vid.setdefault(g["video_id"], []).append(g)
    tasks = [(v, gs, args.out, args.step) for v, gs in sorted(by_vid.items())]
    with mp.Pool(args.jobs) as pool:
        for i, (vid, n) in enumerate(pool.imap_unordered(harvest_video, tasks), 1):
            print(f"[{i}/{len(tasks)}] {vid} "
                  + ("skipped (done)" if n is None else f"{n} card reads"),
                  flush=True)


if __name__ == "__main__":
    main()
