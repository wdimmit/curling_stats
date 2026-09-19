#!/usr/bin/env python
"""Render one shot's split over the video, with the detections that made it.

A split is two line crossings, and when one of them is wrong the served chart
cannot say so -- it publishes a number. This draws the evidence instead: the
panel crops the detector actually saw, every detection it returned at the pass's
own sample rate, the hog tripwire in each panel, and the straight line
``split.crossing_time`` drew between the two samples either side of it.

Written to show why game ``AEqLTgM25Tc`` end 1 shot 4 has a 10.50 s split that
cannot be true. The strip is rotated a quarter turn so the sheet runs left to
right: throwing panel on the left, playing panel on the right, and the stone
travels across the frame the way it travels down the ice.

    python scripts/render_split.py --end 1 --shot 4 --out e1s4.mp4 \
        --cache-root ~/.cache/curling_replay --timeline timeline.json
"""

import argparse
import json
import pickle
import subprocess
from pathlib import Path

import cv2
import numpy as np

from curling_score import analyze as A, weights as weights_mod
from curling_score.detect import release, sequence, yolo
from curling_score.game import fit, secondpass, segment, shots as shots_mod, split
from curling_score.ingest import frames as F, proxy

SCALE = 1.5
HUD_H = 190
RED, YEL, WHITE = (60, 60, 235), (60, 210, 235), (245, 245, 245)
GREY, CYAN, MAGENTA = (150, 150, 150), (230, 200, 60), (220, 80, 220)


def strip_row(setup, y_px):
    """A panel-crop row as a row of the whole strip."""
    return setup.rect[1] + y_px


def sheet_to_strip(setup, x_m, y_m):
    """Sheet metres -> (strip row, panel column), via the panel's own inverse.

    ``PanelCalib.to_pixels`` is the exact inverse of the ``to_sheet`` every
    detection went through, flip included -- hand-rolling it got the flipped
    panel's lateral axis backwards.
    """
    cx, cy = setup.calib.to_pixels(x_m, y_m)
    return setup.rect[1] + cy, cx


def row_for_y_m(setup, y_m):
    return sheet_to_strip(setup, 0.0, y_m)[0]


def rot(row, col, h_strip=1060, w_strip=298):
    """Strip (row, col) -> the quarter-turned canvas (x, y), before scaling."""
    return row, (w_strip - 1 - col)


def put(img, text, org, scale=0.45, color=WHITE, thick=1):
    """Text with a readable edge over moving ice.

    The outline is drawn at the *same* thickness as the fill, offset four ways.
    Drawing it thicker instead widens the glyph advances, so the outline runs
    longer than the text it is meant to back and its tail shows as a ghost of
    the last few characters.
    """
    x, y = org
    for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        cv2.putText(img, text, (x + dx, y + dy), cv2.FONT_HERSHEY_SIMPLEX,
                    scale, (0, 0, 0), thick, cv2.LINE_AA)
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, color, thick, cv2.LINE_AA)


def dashed(img, p0, p1, color, thick=1, dash=9):
    p0, p1 = np.array(p0, float), np.array(p1, float)
    n = int(max(np.abs(p1 - p0)) // dash) or 1
    for i in range(n):
        if i % 2:
            continue
        a = p0 + (p1 - p0) * (i / n)
        b = p0 + (p1 - p0) * min(1.0, (i + 1) / n)
        cv2.line(img, tuple(a.astype(int)), tuple(b.astype(int)), color, thick, cv2.LINE_AA)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeline", required=True)
    ap.add_argument("--cache-root", required=True)
    ap.add_argument("--end", type=int, required=True)
    ap.add_argument("--shot", type=int, required=True)
    ap.add_argument("--from-s", type=float, required=True)
    ap.add_argument("--to-s", type=float, required=True)
    ap.add_argument("--fps", type=float, default=10.0)
    ap.add_argument("--out", required=True)
    ap.add_argument("--dump-at", type=float, nargs="*", default=[],
                    help="also write a PNG of the raw canvas at these times")
    args = ap.parse_args()

    root = Path(args.cache_root).expanduser()
    doc = json.loads(Path(args.timeline).read_text())
    vid = doc["source"]["video_id"]
    setups, panels = pickle.loads((root / f"setups-{vid}.pkl").read_bytes())
    strip = proxy.strip_rect(panels.top, panels.bottom)
    rp = proxy.proxy_path(vid, strip, root)
    rs = A._proxy_setups(setups, strip)

    e = next(x for x in doc["games"][0]["ends"] if x["number"] == args.end)
    house, thr = e["house"], A.OTHER_HOUSE[e["house"]]
    play_setup, throw_setup = rs[house], rs[thr]

    det = yolo.YoloDetector(weights_mod.default_path(), conf=0.30, device=None, imgsz=448)
    det.model.overrides["half"] = True

    earlier = [x for x in doc["games"][0]["ends"] if x["number"] < args.end]
    prev_close = None
    if earlier:
        p = max(earlier, key=lambda x: x["end_s"])
        r = [s["t_rest_s"] for s in p["shots"] if s.get("t_rest_s") is not None]
        prev_close = min(p["end_s"], max(r)) if r else p["end_s"]
    from_s = A.run_up_from(prev_close, e["start_s"])
    end = segment.EndSegment(number=args.end, house=house, start_s=e["start_s"], end_s=e["end_s"])

    # Exactly the two passes the pipeline runs, at their own sample rates.
    seq = list(sequence.detect_end(rp, play_setup, end, A.SHOT_FPS, det, from_s=from_s))
    fseq = list(sequence.detect_span(rp, throw_setup, from_s, e["end_s"], release.RELEASE_FPS, det))

    ds = [d for d in A.delivery.find_deliveries(
        seq, view_x_limit_m=play_setup.view_x_limit_m, view_y_min_m=play_setup.view_y_min_m)
        if d.t_enter >= from_s]
    rec = secondpass.search(seq, secondpass.gaps_to_search(ds, end.start_s, end.end_s), ds)
    if rec:
        ds = sorted(ds + rec, key=lambda d: d.t_enter)
    rels, tb, un = release.find_and_pair(fseq, throw_setup.view_y_min_m, ds, seq, since=from_s,
                                          view_x_limit_m=throw_setup.view_x_limit_m)
    if un:
        ds = sorted(ds + un, key=lambda d: d.t_enter)
    shots = shots_mod.from_deliveries(fit.fit_end(ds), seq, thrown_by={id(d): r for r, d in tb.items()})
    shot = next(s for s in shots if s.number == args.shot)
    rt, dt = list(shot.release.track), list(shot.delivery.track)
    sp = split.long_split(shot.release, shot.delivery)
    HOG = split.HOG_APPARENT_Y_M

    # The two samples the near crossing is interpolated between.
    def bracket(track):
        p = [(float(t), float(x), float(y)) for t, x, y in track]
        for (t0, x0, y0), (t1, x1, y1) in zip(p, p[1:]):
            if (y0 - HOG) * (y1 - HOG) <= 0 and y0 != y1:
                return (t0, x0, y0), (t1, x1, y1)
        return None, None
    (nt0, nx0, ny0), (nt1, nx1, ny1) = bracket(rt)
    (ft0, fx0, fy0), (ft1, fx1, fy1) = bracket(dt)

    hog_throw = row_for_y_m(throw_setup, HOG)
    hog_play = row_for_y_m(play_setup, HOG)
    tee_throw = row_for_y_m(throw_setup, 0.0)
    tee_play = row_for_y_m(play_setup, 0.0)
    play_edge = play_setup.rect[1] if play_setup.calib.flipped else play_setup.rect[1] + play_setup.rect[3]

    W = int(1060 * SCALE)
    H = int(298 * SCALE) + HUD_H
    tmp = Path(args.out).with_suffix(".raw.mp4")
    vw = cv2.VideoWriter(str(tmp), cv2.VideoWriter_fourcc(*"mp4v"), args.fps, (W, H))

    # index the two passes by time
    play_by = {round(t, 3): d for t, d in seq}
    throw_by = {round(t, 3): d for t, d in fseq}

    def nearest(table, t, tol):
        best, bt = None, None
        for k, v in table.items():
            if abs(k - t) <= tol and (bt is None or abs(k - t) < abs(bt - t)):
                best, bt = v, k
        return best, bt

    def X(row):
        return int(row * SCALE)

    def Y(col):
        return int((298 - 1 - col) * SCALE)

    for t, img in F.window(rp, args.from_s, args.to_s, args.fps):
        canvas = np.zeros((H, W, 3), np.uint8)
        vid_img = cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)
        vid_img = cv2.resize(vid_img, (W, int(298 * SCALE)), interpolation=cv2.INTER_CUBIC)
        canvas[: int(298 * SCALE)] = vid_img
        top = int(298 * SCALE)

        # panel bounds and the seam between the two crops
        for s_ in (throw_setup, play_setup):
            for row in (s_.rect[1], s_.rect[1] + s_.rect[3]):
                cv2.line(canvas, (X(row), 0), (X(row), top), (70, 70, 70), 1)

        # tee lines and hog tripwires
        for row in (tee_throw, tee_play):
            dashed(canvas, (X(row), 0), (X(row), top), GREY, 1, 14)
            put(canvas, "tee", (X(row) - 12, top - 14), 0.42, GREY)
        for row in (hog_throw, hog_play):
            cv2.line(canvas, (X(row), 0), (X(row), top), CYAN, 2, cv2.LINE_AA)
            put(canvas, "hog tripwire", (X(row) - 100, 26), 0.46, CYAN)
            put(canvas, "apparent y = 4.441", (X(row) - 100, 46), 0.4, CYAN)
        # the playing panel clears the tripwire by 13 px and no more
        cv2.line(canvas, (X(play_edge), 0), (X(play_edge), top), (90, 160, 255), 1, cv2.LINE_AA)
        put(canvas, "panel edge - 13 px of margin", (X(play_edge) + 8, top - 40), 0.42, (90, 160, 255))

        put(canvas, f"{thr.upper()} PANEL / throwing end / release pass, 5 fps",
            (14, 26), 0.5, WHITE)
        put(canvas, f"{house.upper()} PANEL / playing end / house pass, 10 fps",
            (X(play_setup.rect[1]) + 14, 26), 0.5, WHITE)

        # tracks so far
        def draw_track(track, setup, color, upto):
            pts = [(X(strip_row(setup, 0) + 0), 0)]
            pts = []
            for tt, xx, yy in track:
                if tt > upto + 1e-6:
                    break
                row, col = sheet_to_strip(setup, xx, yy)
                pts.append((X(row), Y(col)))
            for a, b in zip(pts, pts[1:]):
                cv2.line(canvas, a, b, color, 2, cv2.LINE_AA)
            return pts
        draw_track(rt, throw_setup, (90, 200, 90), t)
        draw_track(dt, play_setup, (90, 200, 90), t)

        # the straight line split.crossing_time drew through the hole
        if nt1 - nt0 > 0.3:
            r0, c0 = sheet_to_strip(throw_setup, nx0, ny0)
            r1, c1 = sheet_to_strip(throw_setup, nx1, ny1)
            dashed(canvas, (X(r0), Y(c0)), (X(r1), Y(c1)), MAGENTA, 2, 11)
            if nt0 <= t <= nt1:
                f = (t - nt0) / (nt1 - nt0)
                gr, gc = r0 + (r1 - r0) * f, c0 + (c1 - c0) * f
                cv2.circle(canvas, (X(gr), Y(gc)), int(11 * SCALE), MAGENTA, 2, cv2.LINE_AA)
                put(canvas, "split.py has the stone here", (X(gr) - 95, Y(gc) - 30), 0.44, MAGENTA)

        # detections, drawn as the box area the detector returned
        def draw_dets(dets, setup, tag, tol):
            if not dets:
                return 0
            for d in dets:
                row, col = strip_row(setup, d.y_px), d.x_px
                side = max(4.0, float(np.sqrt(d.area_px)))
                hx, hy = int(side * SCALE / 2), int(side * SCALE / 2)
                cx, cy = X(row), Y(col)
                col_bgr = RED if d.color == "red" else YEL
                thin = 1 if d.area_px < 200 or d.confidence < 0.5 else 2
                cv2.rectangle(canvas, (cx - hx, cy - hy), (cx + hx, cy + hy), col_bgr, thin, cv2.LINE_AA)
                put(canvas, f"{d.confidence:.2f} / {d.area_px:.0f}px", (cx - 34, cy + hy + 14), 0.38, col_bgr)
            return len(dets)

        td, tt_ = nearest(throw_by, t, 0.11)
        pd, pt_ = nearest(play_by, t, 0.06)
        n_throw = draw_dets(td, throw_setup, "throw", 0.11)
        n_play = draw_dets(pd, play_setup, "play", 0.06)

        # HUD
        cv2.rectangle(canvas, (0, top), (W, H), (18, 18, 22), -1)
        put(canvas, f"t = {t:7.2f} s", (16, top + 32), 0.72, WHITE, 2)
        put(canvas, f"end {args.end} shot {args.shot} ({shot.color})    published split "
                    f"{sp.seconds:.2f} s", (250, top + 32), 0.62, WHITE, 1)
        first_t = rt[0][0]
        if t < first_t:
            msg, col = "before the release", GREY
        elif t < nt0:
            msg, col = "release tracked down the throwing panel", (120, 230, 120)
        elif t < nt1 - 1e-6:
            msg, col = (f"NOTHING DETECTED for {nt1 - nt0:.1f} s - the tracker bridges the hole",
                        (80, 120, 255))
        elif abs(t - nt1) < 1e-6:
            msg, col = ("the one detection that closed the hole: 0.33 confidence, 96 px "
                        "- a quarter of a stone", (80, 120, 255))
        elif t < ft0 - 0.5:
            msg, col = "stone in flight, seen by neither panel", GREY
        else:
            msg, col = "arrival, playing panel", (120, 230, 120)
        put(canvas, msg, (16, top + 62), 0.58, col, 2)
        # flash each crossing as the code records it
        for ct, lbl, cc in ((sp.t_start, "near hog crossing recorded here", MAGENTA),
                            (sp.t_end, "far hog crossing recorded here", (120, 230, 120))):
            if 0 <= t - ct < 0.45:
                row = hog_throw if cc is MAGENTA else hog_play
                cv2.line(canvas, (X(row), 0), (X(row), top), cc, 4, cv2.LINE_AA)
                put(canvas, lbl, (X(row) - 110, top - 64), 0.5, cc, 2)
        put(canvas, f"detections this sample - throwing panel: {n_throw}    "
                    f"playing panel: {n_play}", (16, top + 92), 0.5, WHITE)
        put(canvas, f"near crossing interpolated between t={nt0:.2f} (y={ny0:.3f}) and "
                    f"t={nt1:.2f} (y={ny1:.3f})  ->  crosses at t={sp.t_start:.2f}",
            (16, top + 120), 0.48, MAGENTA)
        put(canvas, f"far crossing between t={ft0:.2f} (y={fy0:.3f}) and t={ft1:.2f} "
                    f"(y={fy1:.3f})  ->  crosses at t={sp.t_end:.2f}", (16, top + 144), 0.48, (120, 230, 120))
        put(canvas, "box label = confidence / area px.   a real stone images ~350-400 px",
            (16, top + 170), 0.46, WHITE)
        for want in args.dump_at:
            if abs(t - want) < 0.051:
                cv2.imwrite(str(Path(args.out).with_suffix(f".{want:.1f}.png")), canvas)
        vw.write(canvas)
    vw.release()
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(tmp),
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20",
                    "-movflags", "+faststart", str(args.out)], check=True)
    tmp.unlink()
    print("wrote", args.out)


if __name__ == "__main__":
    main()
