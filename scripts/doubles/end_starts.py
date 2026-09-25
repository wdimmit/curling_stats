"""Contact sheets of each end's opening, to see how doubles stones are placed.

Phase 0 of the mixed doubles work (docs/superpowers/specs/
2026-09-25-mixed-doubles-design.md) needs to know how the club sets the two
positioned stones: by hand at the far end, slid down the sheet, or pushed in
from behind the house. That decides whether the release detector sees phantom
throws, so it is looked at before anything is built.

For every end in a timeline this grabs a frame every ``--step`` seconds from
the moment the previous end's last rock stopped (or ``--lead`` seconds before
the end began, for a game's first end) until the ``--upto``-th shot entered,
and tiles them into sheets with the video time on each tile. Runs wherever
the video is -- on the worker, inside the image, since the laptop has no disk.

    python scripts/doubles/end_starts.py timeline.json video.mp4 out/ \
        [--step 3] [--upto 3] [--width 640] [--ends 1,2,3]
"""

import argparse
import json
from pathlib import Path

import cv2
import numpy as np


def windows(doc, lead_s: float, upto: int, tail_s: float):
    """(game index, end number, t0, t1) for every end's opening."""
    for game in doc["games"]:
        prev_rest = None
        for end in game["ends"]:
            seen = [s for s in end["shots"] if not s.get("missing")]
            t0 = prev_rest if prev_rest is not None else end["start_s"] - lead_s
            entries = [s["t_enter_s"] for s in seen if s.get("t_enter_s") is not None]
            if len(entries) >= upto:
                t1 = entries[upto - 1] + 5.0
            else:
                t1 = end["start_s"] + tail_s
            yield game["index"], end["number"], max(0.0, t0), t1
            rests = [s["t_rest_s"] for s in seen if s.get("t_rest_s") is not None]
            prev_rest = max(rests) if rests else end["end_s"]


def grab(cap, t: float, width: int):
    cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
    ok, frame = cap.read()
    if not ok:
        return None
    h, w = frame.shape[:2]
    frame = cv2.resize(frame, (width, round(h * width / w)), interpolation=cv2.INTER_AREA)
    label = f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:04.1f}  ({t:.0f}s)"
    cv2.rectangle(frame, (0, 0), (width, 22), (0, 0, 0), -1)
    cv2.putText(frame, label, (6, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
    return frame


def tile(frames, cols: int):
    h, w = frames[0].shape[:2]
    rows = -(-len(frames) // cols)
    sheet = np.zeros((rows * h, cols * w, 3), np.uint8)
    for i, f in enumerate(frames):
        r, c = divmod(i, cols)
        sheet[r * h:(r + 1) * h, c * w:(c + 1) * w] = f
    return sheet


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("timeline")
    ap.add_argument("video")
    ap.add_argument("out")
    ap.add_argument("--step", type=float, default=3.0)
    ap.add_argument("--upto", type=int, default=3,
                    help="stop this many entered shots into the end")
    ap.add_argument("--lead", type=float, default=150.0)
    ap.add_argument("--tail", type=float, default=180.0,
                    help="window length when too few shots entered")
    ap.add_argument("--width", type=int, default=640)
    ap.add_argument("--cols", type=int, default=4)
    ap.add_argument("--per-sheet", type=int, default=16)
    ap.add_argument("--ends", default="", help="comma list, e.g. 1,2,5")
    args = ap.parse_args(argv)

    doc = json.loads(Path(args.timeline).read_text())
    only = {int(x) for x in args.ends.split(",") if x.strip()}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    cap = cv2.VideoCapture(args.video)
    for g, e, t0, t1 in windows(doc, args.lead, args.upto, args.tail):
        if only and e not in only:
            continue
        frames = []
        t = t0
        while t <= t1:
            f = grab(cap, t, args.width)
            if f is not None:
                frames.append(f)
            t += args.step
        for k in range(0, len(frames), args.per_sheet):
            name = out / f"g{g + 1}e{e:02d}_{k // args.per_sheet:02d}.jpg"
            cv2.imwrite(str(name), tile(frames[k:k + args.per_sheet], args.cols),
                        [cv2.IMWRITE_JPEG_QUALITY, 80])
        print(f"game {g + 1} end {e}: {t0:.0f}-{t1:.0f}s, {len(frames)} frames")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
