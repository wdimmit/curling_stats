"""Pull median-stacked board frames off a cached VOD and list their cards.

The digits are filled in by hand afterwards: this writes every card it finds
with ``"end": null`` and a contact sheet to read them off. That labelled set is
what MIN_MARGIN is set from -- the five-frame probe in the spec is not enough.
"""

import argparse
import json
from pathlib import Path

import cv2
import numpy as np

from curling_score.game import scoreboard as SB
from curling_score.ingest import frames as F


def sample(video, t, window_s=90.0, max_frames=40):
    """One de-occluded board image at ``t``, or None."""
    lo, hi = max(0.0, t - window_s), t + window_s
    imgs = []
    for ts, img in F.keyframe_sweep(video, start_s=lo, end_s=hi):
        if ts < lo:
            continue
        if len(imgs) >= max_frames:
            break
        imgs.append(img)
    return SB.median_frame(imgs) if imgs else None


def row_strip(img, geom, color, scale=8, pad_y=0.16):
    """One whole card row, upscaled, with a tick and number on every slot.

    Cropping card by card is ambiguous: at ~20 px a tile is barely wider than
    the slot pitch, so a crop loose enough to hold a card hung slightly off
    centre also holds its neighbour's digit, and there is nothing in the crop to
    say which of the two is the one being labelled. A whole row with the slot
    grid drawn over it removes the question, and carries the printed 1-14 row
    underneath the yellow band as a free check that the grid is aligned.
    """
    row = geom.yellow_row if color == "yellow" else geom.red_row
    y0 = max(0, int(row[0] - pad_y * geom.dy))
    y1 = min(img.shape[0], int(row[1] + pad_y * geom.dy))
    half = 0.5 * (geom.slot_x[1] - geom.slot_x[0])
    x0 = max(0, int(geom.slot_x[0] - half))
    x1 = min(img.shape[1], int(geom.slot_x[-1] + half))
    up = cv2.resize(img[y0:y1, x0:x1], None, fx=scale, fy=scale,
                    interpolation=cv2.INTER_NEAREST)

    hdr = 30
    out = np.full((up.shape[0] + hdr + 14, up.shape[1], 3), 35, np.uint8)
    out[hdr:hdr + up.shape[0]] = up
    col = (0, 255, 255) if color == "yellow" else (0, 90, 255)
    for k, sx in enumerate(geom.slot_x, start=1):
        px = int((sx - x0) * scale)
        if not (0 <= px < out.shape[1]):
            continue
        cv2.line(out, (px, hdr - 6), (px, hdr + up.shape[0] + 6), col, 1)
        cv2.putText(out, str(k), (px - 8, hdr - 10), cv2.FONT_HERSHEY_SIMPLEX,
                    0.6, col, 2)
    return out


def contact_sheet(img, geom, path, scale=8):
    """Both card rows of one frame, stacked, for reading the digits by hand.

    Native tiles are ~20 px wide with a 17-22 px digit, which no one can read at
    1:1; INTER_NEAREST keeps the pixel edges crisp rather than inventing the
    smooth strokes a bilinear upscale would.
    """
    ys = row_strip(img, geom, "yellow", scale)
    rs = row_strip(img, geom, "red", scale)
    gap = 12
    w = max(ys.shape[1], rs.shape[1])
    out = np.full((ys.shape[0] + rs.shape[0] + gap, w, 3), 35, np.uint8)
    out[:ys.shape[0], :ys.shape[1]] = ys
    out[ys.shape[0] + gap:, :rs.shape[1]] = rs
    cv2.imwrite(str(path), out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--out", default="datasets/board-cards")
    ap.add_argument("--sheets", default=None,
                    help="scratch dir for the contact sheets read by hand; "
                         "these are working files and do not belong in --out")
    ap.add_argument("--step-s", type=float, default=600.0)
    ap.add_argument("--start-s", type=float, default=0.0)
    ap.add_argument("--end-s", type=float, default=14400.0)
    ap.add_argument("--at", default=None,
                    help="comma-separated seconds to sample instead of sweeping. "
                         "A regular sweep spends most of its samples on board "
                         "states it has already seen -- the board changes about "
                         "once an end -- so the set that is actually labelled is "
                         "chosen from a sweep's log and re-harvested with this.")
    ap.add_argument("--force", action="store_true",
                    help="overwrite a labels.json that already has hand-filled "
                         "digits in it")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    # Every row this writes has ``"end": null``, so a second run over a
    # directory that has already been labelled would silently throw the hand
    # reading away -- hours of work that nothing else on this box holds a copy
    # of. Re-harvesting into a scratch --out is free; this is not.
    existing = out / "labels.json"
    if existing.exists() and not args.force:
        done = [r for r in json.loads(existing.read_text())
                if r.get("end") is not None]
        if done:
            raise SystemExit(
                f"{existing} already carries {len(done)} hand-filled labels; "
                f"harvest into a different --out, or pass --force to discard them"
            )
    sheets = Path(args.sheets) if args.sheets else None
    if sheets:
        sheets.mkdir(parents=True, exist_ok=True)

    if args.at:
        times = [float(s) for s in args.at.replace(",", " ").split()]
    else:
        n = int((args.end_s - args.start_s) // args.step_s) + 1
        times = [args.start_s + i * args.step_s for i in range(n)]

    rows = []
    for t0 in times:
        img = sample(args.video, t0)
        if img is None:
            print(f"t={int(t0):05d}: no frames", flush=True)
            continue
        geom = SB.find_board(img)
        if geom is None:
            print(f"t={int(t0):05d}: no board", flush=True)
            continue
        if not SB.is_readable(img, geom):
            print(f"t={int(t0):05d}: board obstructed", flush=True)
            continue
        reading = SB.read_slots(img, geom)
        if reading.is_blank():
            print(f"t={int(t0):05d}: blank board", flush=True)
            continue
        name = f"board_t{int(t0):05d}.png"
        # These frames are committed, so squeeze them: a 1080p median frame is
        # ~1.6 MB at the default level and ~1.2 MB at 9, for no loss at all.
        cv2.imwrite(str(out / name), img, [cv2.IMWRITE_PNG_COMPRESSION, 9])
        if sheets:
            contact_sheet(img, geom, sheets / f"cards_t{int(t0):05d}.png")
        for color, slots in (("yellow", reading.yellow), ("red", reading.red)):
            for slot in sorted(slots):
                rows.append({"frame": name, "t_s": t0,
                             "color": color, "slot": slot, "end": None})
        print(f"{name}: yellow={sorted(reading.yellow)} red={sorted(reading.red)}",
              flush=True)

    (out / "labels.json").write_text(json.dumps(rows, indent=2) + "\n")
    print(f"{len(rows)} cards over {len({r['frame'] for r in rows})} frames")


if __name__ == "__main__":
    main()
