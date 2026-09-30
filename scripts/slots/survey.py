"""Every card slot's presence statistics across the cached 09/28 VODs and the
kept 09/29 recordings, sampled as boards are read (median of keyframes within
20 s), for choosing a presence test that sees a thin "1" card."""
import json, os, sys, cv2, numpy as np
from curling_score.game import scoreboard as SB
from curling_score.ingest import frames as F

VIDEOS = [("/c/videos/%s.mp4" % v, v) for v in ("1xmDI5EkwOU", "VVgLH4uBxYs", "VTldBAiftKk", "orvBvuFdLgk", "LVpNIsWstHA")] + \
         [("/c/keep-0929/%s.rec.0.ts" % v, v) for v in ("MVnwn2R8Rt8", "UIHi7VJySg0", "4l60dFdwgVo", "w_aFsB4HwUE", "5CVNnER02aM")]
STEP = 300.0
out = open("/s/slots.jsonl", "w")
os.makedirs("/s/crops", exist_ok=True)
os.makedirs("/s/win", exist_ok=True)
for path, vid in VIDEOS:
    n_board, t, empty = 0, 60.0, 0
    while empty < 2:
        buf = [im for ft, im in F.keyframe_sweep(path, start_s=t - 20, end_s=t + 20)]
        t0, t = t, t + STEP
        if not buf:
            empty += 1
            continue
        empty = 0
        if len(buf) < 3:
            continue
        img = SB.median_frame(buf)
        g = SB.find_board(img)
        if g is None or not SB.is_readable(img, g):
            continue
        n_board += 1
        bx0, bx1 = int(g.anchor_x - g.dy), int(g.slot_x[-1] + g.dy)
        by0, by1 = int(g.top_line_y - 0.3 * g.dy), int(g.bottom_line_y + 0.3 * g.dy)
        cv2.imwrite(f"/s/win/board_{vid}_{int(t0):06d}.jpg", img[max(0, by0):by1, max(0, bx0):bx1])
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(float)
        gray8 = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        present = SB.read_slots(img, g)
        half_w = max(2, int(0.14 * g.dy))
        for color, row in (("yellow", g.yellow_row), ("red", g.red_row)):
            boxes = SB._row_boxes(gray, row, g.slot_x, half_w)
            level = SB._row_level(boxes)
            if level is None:
                continue
            for k, box in enumerate(boxes, start=1):
                if box is None:
                    continue
                d = {p: round(float(level - np.percentile(box, p)), 1) for p in (8, 5, 3, 2)}
                bright = round(float(np.percentile(box, 92) - level), 1)
                b95 = round(float(np.percentile(box, 95) - level), 1)
                b98 = round(float(np.percentile(box, 98) - level), 1)
                bmax = round(float(box.max() - level), 1)
                ink = round(float((box < level - 30).mean()), 3)
                ink20 = round(float((box < level - 20).mean()), 3)
                win = SB.card_window(gray8, g, color, k)
                if win is not None:
                    wf = win.astype(float)
                    wb98 = round(float(np.percentile(wf, 98) - level), 1)
                    wink = round(float((wf < level - 30).mean()), 3)
                else:
                    wb98 = wink = None
                old = k in getattr(present, color)
                digit, conf = SB.read_digit(SB._card_glyph(gray8, g, color, k))
                sid = f"{vid}_{int(t0):06d}_{color[0]}{k:02d}"
                if win is not None:
                    cv2.imwrite(f"/s/win/{sid}.png", win)
                rec = dict(id=sid, vid=vid, t=t0, color=color, slot=k, dy=round(g.dy, 1), old=old, bright=bright,
                           dark8=d[8], dark5=d[5], dark3=d[3], dark2=d[2], ink=ink, ink20=ink20, b95=b95, b98=b98, bmax=bmax, wb98=wb98, wink=wink, digit=digit, conf=round(float(conf), 5))
                out.write(json.dumps(rec) + "\n")
                if (bright >= 12 or b98 >= 12) and not old and ink >= 0.02:
                    w = win
                    if w is not None:
                        cv2.imwrite(f"/s/crops/{vid}_{int(t0)}_{color}_{k}.png", w)
    out.flush()
    print(vid, "boards read", n_board, flush=True)
