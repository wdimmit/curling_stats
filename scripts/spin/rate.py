"""A rock's rotation rate from the handle-point model, run on every frame.

    ./.venv/bin/python scripts/spin/rate.py MODEL.pt OUT.json ROCK.pkl.gz [...] [--plot OUT.png]

Per frame past the hand (8 m past the throwing tee) and inside the view, the
model finds the neck and the grip end. Seen from the side, the grip end sits
r*sin(theta) across from the neck, theta the handle's angle from the line of
sight, so s = (grip_x - neck_x) / stone width swings once per turn. The rate is
the frequency of s: a periodogram for where to start, then a sinusoid fitted
robustly.

Two rates come out. ``f_early`` is the spin as thrown: a constant-rate fit to
the first ~1.25 turns past the hand (from 8 m, about a second after the hog
line) -- the number that matters for a delivery. ``f`` is the average over the
whole stretch in view; a rock that slows reads up to ~15% lower there. The
quarter turns (``events``: end-on crossings and sideways extremes) are kept for
looking at; a slow-down fitted through them and extrapolated back to the hog
line was tried and over-read badly (the first confirmed event is seconds in).

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


def fit(t, x, f0, ks=tuple(np.linspace(-0.02, 0.01, 13))):
    """x = a sin(2pi(f tm + k tm^2 / 2) + ph) + c, f within 30% of f0 and the
    drift k small (spin decays slowly). For a given (f, k) the model is linear in
    (a cos ph, a sin ph, c), so the frequency and drift are searched on a fine
    grid and the rest solved by least squares, reweighted (Huber) so a stray
    frame cannot pull the fit."""
    tm = t - t.mean()
    best = None
    for f in np.linspace(0.7 * f0, 1.3 * f0, 121):
        for k in ks:
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


def smooth(t, x, half_s=0.15):
    """A running median then a running mean, each over +-half_s: frame jitter
    and the odd stray frame out, the swing itself kept."""
    med = np.array([np.median(x[np.abs(t - ti) <= half_s]) for ti in t])
    return np.array([med[np.abs(t - ti) <= half_s].mean() for ti in t])


def quarter_turns(t, x, a, c, hyst=0.3):
    """The swing's quarter turns: (time, kind) with kind "end-on" where it
    crosses its middle (the handle points at or away from the camera) and
    "sideways" at a whole lobe's extreme. Consecutive events are a quarter of
    a turn apart. Crossings are found with hysteresis (the signal has to get
    past +-hyst * a on each side) and placed by interpolation; an extreme is
    only taken from a lobe bounded by crossings on both sides, since one cut
    off by the window's edge has not reached its true extreme."""
    xs = smooth(t, x) - c
    h = hyst * a
    crossings, state, last_out = [], 0, None
    for i, v in enumerate(xs):
        side = 1 if v > h else -1 if v < -h else 0
        if side == 0:
            continue
        if state and side != state:
            j = next(j for j in range(i, last_out, -1) if np.sign(xs[j]) != np.sign(xs[j - 1]))
            tz = t[j - 1] + (t[j] - t[j - 1]) * (0 - xs[j - 1]) / (xs[j] - xs[j - 1])
            crossings.append(float(tz))
        state, last_out = side, i
    events = [(tc, "end-on") for tc in crossings]
    for t0, t1 in zip(crossings, crossings[1:]):
        m = (t > t0) & (t < t1)
        if m.sum() < 5:
            continue
        idx = np.where(m)[0]
        k = idx[int(np.argmax(np.abs(xs[idx])))]
        if 0 < k < len(t) - 1:      # parabola through the peak and its neighbours
            y0, y1, y2 = np.abs(xs[k - 1:k + 2])
            den = y0 - 2 * y1 + y2
            dt = 0.5 * (y0 - y2) / den if den else 0.0
            events.append((float(t[k] + dt * (t[k + 1] - t[k - 1]) / 2), "sideways"))
    return sorted(events)


EARLY_TURNS = 1.25          # the early window spans about this many turns...
EARLY_WINDOW_S = (5.0, 9.0)  # ...but no less and no more than this


def early_window(t, x, f_all):
    """The spin as thrown: a constant-rate sinusoid fitted to the first stretch
    after the hand is off, about a turn and a quarter long. A curve through the
    whole window, evaluated at its start, was tried and over-read: the first
    quarter turn it can confirm comes seconds in, and extrapolating a slow-down
    back from there magnifies a little timing noise into a large one."""
    w = float(np.clip(EARLY_TURNS / max(f_all, 1e-3), *EARLY_WINDOW_S))
    m = t <= t.min() + w
    if m.sum() < 45:
        return {}
    tw, xw = t[m], x[m]
    pw = periodogram(tw, xw, np.linspace(0.6 * f_all, 1.4 * f_all, 81))
    f0 = float(np.linspace(0.6 * f_all, 1.4 * f_all, 81)[int(np.argmax(pw))])
    got = fit(tw, xw, f0, ks=(0.0,))
    return dict(f_early=got["f"], early_from=float(tw.min()),
                early_to=float(tw.max()), early_a=got["a"], early_rms=got["rms"])


def hog_time(recs, t_rel, hog_m=6.401):
    """When the rock crossed the throwing hog line, from its own track."""
    ts = np.array([r["t"] for r in recs]) - t_rel
    ys = np.array([r["y_m"] for r in recs])
    order = np.argsort(ts)
    ts, ys = ts[order], ys[order]
    return float(np.interp(hog_m, np.maximum.accumulate(ys), ts)) if ys.max() > hog_m else None


def rate(model, path, min_conf=0.5):  # noqa: C901
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
    res["t_hog"] = hog_time(d["recs"], meta["t_rel"])
    ev = quarter_turns(t, s, res["a"], res["c"])
    res["events"] = ev
    res.update(early_window(t, s, res["f"]))
    return res, kp


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("model")
    ap.add_argument("out")
    ap.add_argument("rocks", nargs="+")
    ap.add_argument("--plot")
    ap.add_argument("--min-conf", type=float, default=0.5,
                    help="skip frames whose weaker point is below this keypoint confidence")
    args = ap.parse_args()
    from ultralytics import YOLO

    model = YOLO(args.model)
    results, series = [], []
    for p in args.rocks:
        res, kp = rate(model, p, args.min_conf)
        results.append(res)
        series.append((res, kp))
        print(f"{res['rock']} {res['color']:6s} {str(res.get('shot_type')):6s} {res['n']:3d}/{res['frames']:3d} frames  "
              + (f"early {res.get('f_early', float('nan')):.3f}/s  average {res['f']:.3f}/s  "
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
