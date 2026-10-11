"""Follow-cam clips of rocks, for judging a measured spin by eye.

    ./.venv/bin/python scripts/spin/clips.py MODEL OUT_DIR --rock NAME=ROCK.pkl.gz[@RATES.json] [...]

Each clip follows one stone down the long camera that faces its thrower, from
5 m past the throwing tee (the hand still on, so the release is in it) to the
bottom of the frame, every frame at 30 fps, scaled so the stone is always the
same size. Over it: the model's neck (blue) and grip end (orange) where it is
measured (from 8 m), the frames of the early window marked, and the rock's
measured rates from rate.py's output. Writes OUT_DIR/<name>.mp4 and an
index.html that plays them side by side.
"""
import argparse
import gzip
import json
import pickle
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

CANVAS = (400, 330)
STONE_PX = 200
START_M = 5.0
MEASURE_M = 8.0


def points(model, recs, batch=64):
    """Per record, the model's (neck, grip, conf) in the patch's pixels, or None."""
    out = []
    for i in range(0, len(recs), batch):
        chunk = recs[i:i + batch]
        res = model.predict([np.ascontiguousarray(r["patch"][:, :, ::-1]) for r in chunk],
                            imgsz=224, conf=0.25, verbose=False)
        for p in res:
            if p.keypoints is None or len(p.boxes) == 0:
                out.append(None)
                continue
            k = int(np.argmax(p.boxes.conf.cpu().numpy()))
            xy = p.keypoints.xy.cpu().numpy()[k]
            kc = p.keypoints.conf.cpu().numpy()[k] if p.keypoints.conf is not None else np.ones(2)
            out.append((xy[0], xy[1], float(kc.min())))
    return out


def frame(rec, pts, label, t_rel, early):
    W, H = CANVAS
    x0, y0, x1, y1 = rec["box"]
    ox, oy = rec["origin"]
    sc = STONE_PX / (x1 - x0)
    im = Image.fromarray(rec["patch"])
    im = im.resize((max(1, round(im.width * sc)), max(1, round(im.height * sc))), Image.LANCZOS)
    cx, by = ((x0 + x1) / 2 - ox) * sc, (y1 - oy) * sc
    dx, dy = round(W / 2 - cx), round(H - 40 - by)
    can = Image.new("RGB", CANVAS, (28, 28, 28))
    can.paste(im, (dx, dy))
    d = ImageDraw.Draw(can)
    t = rec["t"] - t_rel
    if pts is not None and rec["y_m"] >= MEASURE_M:
        (nx, ny), (gx, gy), conf = pts
        n = (nx * sc + dx, ny * sc + dy)
        g = (gx * sc + dx, gy * sc + dy)
        d.line([n, g], fill=(255, 255, 255), width=1)
        for (x, y), col in ((n, (79, 209, 255)), (g, (255, 160, 64))):
            d.ellipse([x - 5, y - 5, x + 5, y + 5], outline=col, width=2)
    d.rectangle([0, 0, W, 34], fill=(0, 0, 0))
    d.text((6, 3), label, fill=(255, 255, 0))
    state = ("hand on / not measured" if rec["y_m"] < MEASURE_M else
             "EARLY WINDOW" if early and early[0] <= t <= early[1] else "measured")
    d.text((6, 19), f"{t:5.1f} s after release   {rec['y_m']:4.1f} m past the tee   {state}",
           fill=(255, 120, 120) if state == "EARLY WINDOW" else (200, 200, 200))
    return np.asarray(can)


def clip(model, rock_path, rates, name, out_dir):
    d = pickle.load(gzip.open(rock_path))   # written by our own extract.py
    meta = d["meta"]
    recs = [r for r in d["recs"] if r["y_m"] >= START_M and r["box"][3] < 1066]
    if not recs:
        return None
    pts = points(model, recs)
    r = rates or {}
    early = (r["early_from"], r["early_to"]) if "early_from" in r else None
    label = (f"{name}  {meta['color']} {meta.get('shot_type') or ''}   early {r.get('f_early', float('nan')):.3f} turns/s"
             f"   average {r.get('f', float('nan')):.3f}")
    path = Path(out_dir) / f"{name}.mp4"
    W, H = CANVAS
    ff = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
                           "-s", f"{W}x{H}", "-r", "30", "-i", "-", "-c:v", "libx264", "-crf", "20",
                           "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(path)], stdin=subprocess.PIPE)
    for rec, p in zip(recs, pts):
        ff.stdin.write(frame(rec, p, label, meta["t_rel"], early).tobytes())
    ff.stdin.close()
    ff.wait()
    return path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("model")
    ap.add_argument("out_dir")
    ap.add_argument("--rock", action="append", required=True, help="NAME=ROCK.pkl.gz[@RATES.json]")
    ap.add_argument("--title", default="Spin outliers")
    args = ap.parse_args()
    from ultralytics import YOLO

    model = YOLO(args.model)
    out = Path(args.out_dir).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    made = []
    for spec in args.rock:
        name, rest = spec.split("=", 1)
        rock_path, _, rates_path = rest.partition("@")
        rates = None
        if rates_path:
            rock = Path(rock_path).name.split(".")[0]
            rates = next((r for r in json.loads(Path(rates_path).read_text()) if r["rock"] == rock), None)
        p = clip(model, rock_path, rates, name, out)
        if p:
            made.append((name, rates or {}))
            print(f"{name}: {p}", flush=True)
    cells = "\n".join(
        f'<figure><video src="{n}.mp4" controls loop muted autoplay playsinline></video>'
        f'<figcaption><b>{n}</b> early {r.get("f_early", float("nan")):.3f} turns/s, '
        f'average {r.get("f", float("nan")):.3f}</figcaption></figure>' for n, r in made)
    (out / "index.html").write_text(f"""<!doctype html><meta charset="utf-8"><title>{args.title}</title>
<style>body{{font:14px system-ui;background:#1d1f22;color:#ddd;margin:16px}}
main{{display:flex;flex-wrap:wrap;gap:14px}}figure{{margin:0}}video{{width:400px;display:block}}
figcaption{{padding:4px 2px}}</style><h1>{args.title}</h1><main>{cells}</main>
""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
