"""A rock's rotation rate from the handle-point model, run on every frame.

    ./.venv/bin/python scripts/spin/rate.py MODEL.pt OUT.json ROCK.pkl.gz [...] [--plot OUT.png]

Per frame past the hand (8 m past the throwing tee) and inside the view, the
model finds the neck and the grip end. Seen from the side, the grip end sits
r*sin(theta) across from the neck, theta the handle's angle from the line of
sight, so s = (grip_x - neck_x) / stone width swings once per turn. The rate is
the frequency of s: a periodogram for where to start, then a sinusoid whose
frequency may drift slowly (spin decays), fitted robustly.

Direction is not read here: the swing is the same either way round. The
published curl gives it.
"""
import argparse
import gzip
import json
import pickle
import sys
from pathlib import Path

import numpy as np

Y_START_M = 8.0
FRAME_BOTTOM_ROW = 1066
FREQS = np.linspace(0.05, 0.8, 301)


def keypoints(model, recs, imgsz=224, batch=64):
    """(t, y_m, s, h, conf) per frame: s the grip's offset across from the neck,
    h its height above the neck, both in stone widths; conf the lower of the
    two points' confidences. Frames where nothing is found are skipped."""
    out = []
    for i in range(0, len(recs), batch):
        chunk = recs[i:i + batch]
        res = model.predict([np.ascontiguousarray(r["patch"][:, :, ::-1]) for r in chunk],
                            imgsz=imgsz, conf=0.25, verbose=False)
        for r, p in zip(chunk, res):
            if p.keypoints is None or len(p.boxes) == 0:
                continue
            k = int(np.argmax(p.boxes.conf.cpu().numpy()))
            xy = p.keypoints.xy.cpu().numpy()[k]
            kc = p.keypoints.conf.cpu().numpy()[k] if p.keypoints.conf is not None else np.ones(2)
            w = r["box"][2] - r["box"][0]
            out.append((r["t"], r["y_m"], float((xy[1, 0] - xy[0, 0]) / w),
                        float((xy[0, 1] - xy[1, 1]) / w), float(kc.min())))
    return out


def periodogram(t, x, freqs=FREQS):
    x = x - x.mean()
    out = []
    for f in freqs:
        w = 2 * np.pi * f
        tau = np.arctan2(np.sum(np.sin(2 * w * t)), np.sum(np.cos(2 * w * t))) / (2 * w)
        c, s = np.cos(w * (t - tau)), np.sin(w * (t - tau))
        out.append(0.5 * ((x @ c) ** 2 / (c @ c) + (x @ s) ** 2 / (s @ s)) / max(x.var(), 1e-9))
    return np.array(out)


def fit(t, x, f0):
    """x = a sin(2pi(f tm + k tm^2 / 2) + ph) + c, f within 30% of f0 and the
    drift k small (spin decays slowly). For a given (f, k) the model is linear in
    (a cos ph, a sin ph, c), so the frequency and drift are searched on a fine
    grid and the rest solved by least squares, reweighted (Huber) so a stray
    frame cannot pull the fit."""
    tm = t - t.mean()
    best = None
    for f in np.linspace(0.7 * f0, 1.3 * f0, 121):
        for k in np.linspace(-0.02, 0.01, 13):
            ph_t = 2 * np.pi * (f * tm + 0.5 * k * tm ** 2)
            A = np.stack([np.sin(ph_t), np.cos(ph_t), np.ones_like(tm)], axis=1)
            wts = np.ones_like(x)
            for _ in range(4):
                coef, *_ = np.linalg.lstsq(A * wts[:, None], x * wts, rcond=None)
                r = x - A @ coef
                scale = 1.4826 * np.median(np.abs(r)) + 1e-6
                wts = np.sqrt(np.minimum(1.0, 1.5 * scale / np.maximum(np.abs(r), 1e-9)))
            cost = float(np.sum((wts * r) ** 2))
            if best is None or cost < best[0]:
                best = (cost, f, k, coef, r)
    _, f, k, (bs, bc, c), r = best
    return dict(f=float(f), k=float(k), a=float(np.hypot(bs, bc)), ph=float(np.arctan2(bc, bs)), c=float(c),
                t_mid=float(t.mean()), rms=float(np.sqrt(np.mean(r ** 2))),
                f_start=float(f + k * (t.min() - t.mean())), f_end=float(f + k * (t.max() - t.mean())))


def rate(model, path, min_conf=0.5):
    d = pickle.load(gzip.open(path))   # written by our own extract.py
    meta = d["meta"]
    recs = [r for r in d["recs"] if r["y_m"] >= Y_START_M and r["box"][3] < FRAME_BOTTOM_ROW]
    kp = [k for k in keypoints(model, recs) if k[4] >= min_conf]
    name = Path(path).name.split(".")[0]
    res = dict(rock=name, video=meta.get("video"), color=meta["color"], curl=(meta.get("line") or {}).get("curl"),
               shot_type=meta.get("shot_type"), n=len(kp), frames=len(recs))
    if len(kp) < 60:
        return res, kp
    t = np.array([k[0] for k in kp]) - meta["t_rel"]
    s = np.array([k[2] for k in kp])
    pw = periodogram(t, s)
    f0 = float(FREQS[int(np.argmax(pw))])
    res.update(span_s=float(t.max() - t.min()), f_peak=f0, peak=float(pw.max()), **fit(t, s, f0))
    res["turns_in_view"] = res["f"] * res["span_s"]
    return res, kp


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("model")
    ap.add_argument("out")
    ap.add_argument("rocks", nargs="+")
    ap.add_argument("--plot")
    args = ap.parse_args()
    from ultralytics import YOLO

    model = YOLO(args.model)
    results, series = [], []
    for p in args.rocks:
        res, kp = rate(model, p)
        results.append(res)
        series.append((res, kp))
        print(f"{res['rock']} {res['color']:6s} {str(res.get('shot_type')):6s} {res['n']:3d}/{res['frames']:3d} frames  "
              + (f"f={res['f']:.3f}/s ({res['f_start']:.3f}->{res['f_end']:.3f})  peak {res['f_peak']:.3f}  "
                 f"a={res['a']:.2f} rms={res['rms']:.2f}  turns in view {res['turns_in_view']:.1f}" if "f" in res else "too few"),
              flush=True)
    Path(args.out).write_text(json.dumps(results, indent=1) + "\n")
    if args.plot:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        n = len(series)
        fig, axs = plt.subplots(n, 1, figsize=(11, 2.2 * n), squeeze=False)
        for ax, (res, kp) in zip(axs[:, 0], series):
            if not kp:
                continue
            d = pickle.load(gzip.open([p for p in args.rocks if Path(p).name.startswith(res["rock"] + ".")][0]))
            t = np.array([k[0] for k in kp]) - d["meta"]["t_rel"]
            ax.plot(t, [k[2] for k in kp], ".", ms=2, label="grip across (s)")
            ax.plot(t, [k[3] for k in kp], ".", ms=2, alpha=0.5, label="grip above (h)")
            if "f" in res:
                tt = np.linspace(t.min(), t.max(), 500)
                tm = tt - res["t_mid"]
                ax.plot(tt, res["a"] * np.sin(2 * np.pi * (res["f"] * tm + 0.5 * res["k"] * tm ** 2) + res["ph"])
                        + res["c"], "r-", lw=1)
            ax.set_title(f"{res['rock']} {res['color']} f={res.get('f', 0):.3f}/s", fontsize=9)
            ax.axhline(0, color="k", lw=0.5)
        axs[0, 0].legend(fontsize=7, loc="upper right")
        plt.tight_layout()
        plt.savefig(args.plot, dpi=70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
