"""The leave-one-video-out gate for the card-digit reader, and its reject sweep.

This script exists to produce one number under conditions fixed before any model
was written, so that the number does not depend on which architecture is being
judged. It takes `--model` and knows nothing else about it.

**The gate** (stated in the plan before the work started):

    out-of-sample, leave-one-video-out over `datasets/board-cards-train`,
    at the confidence threshold where the pooled WRONG count is zero,
    coverage per FRAME must reach 87%. The MLP reaches 60.8%.

Four things about how it measures, each of which was got wrong once already and
corrected against a measurement:

* **Split by VIDEO.** One physical card hangs for a whole game and appears in
  every sampled frame of one video, so a row-level or card-level split puts
  copies of the same object on both sides and reports a flattering number. Each
  fold trains on nine videos' cards plus the printed row and is measured only on
  the tenth video's rows.
* **Both sides go through the production preprocessing.** `read_cards` reads a
  card with `_card_glyph`, which is `card_window`, then tile segmentation, then
  `_glyph_ink`. `cards.npz` stores the raw window, i.e. the output of
  `card_window`, so the stored window is put through `scoreboard.glyph_in_window`
  -- literally the rest of `_card_glyph` -- on both the training and the
  measuring side. Training on raw windows while measuring through `_card_glyph`
  once produced a confident, meaningless 81.5%.
* **Measure per FRAME.** Production's sampler reads one board state and
  classifies each card once, so a card that reads correctly in the frame it is
  read in is a success. An earlier cut demanded every frame of a card clear the
  threshold -- something production never asks -- and reported 24% where the
  honest figure was 60.8%. The per-card view is still printed, because it is the
  one the 42/54 baseline is quoted in, but it is not the gate.
* **Report a seed range.** Seed-to-seed spread on this data has covered 43-49 of
  69 rows with identical inputs. A single run cannot distinguish an improvement
  from the noise floor, so every seed trains all ten folds and the spread is
  printed beside the median.

The threshold is swept over a grid fixed here, not chosen per model. Alongside
it the script reports the *tight* threshold -- just above the most confident
wrong read, i.e. the best coverage any threshold could buy at zero wrong -- for
every model alike, since a grid point is an arbitrary place to stop.

Read-only: trains in memory, saves nothing.
"""

import os

# Set before numpy is imported, and the reason is measured: these matrices are
# small enough that BLAS's threads spend their time synchronising. One training
# epoch took 7.3 s across twenty threads and 0.6 s on one. The parallelism that
# does pay is one process per fold, below.
for _var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
             "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_var, "1")

import argparse                                                  # noqa: E402
import collections                                               # noqa: E402
import json                                                      # noqa: E402
import multiprocessing as mp                                     # noqa: E402
import sys                                                       # noqa: E402
import time                                                      # noqa: E402

import cv2                                                       # noqa: E402
import numpy as np                                               # noqa: E402

cv2.setNumThreads(1)  # same argument, for the augmentation's warps and blurs

from curling_score.game import digits as D
from curling_score.game import scoreboard as SB

PRINTED = "datasets/board-glyphs/glyphs.npz"
COLLECTED = "datasets/board-cards-train/cards.npz"

# Fixed before the CNN was written, and applied identically to every model.
THRESHOLDS = (0.0, 0.5, 0.9, 0.99, 0.999, 0.9999, 0.99999)
TARGET_COVERAGE = 0.87


# ------------------------------------------------------------------- the data

def load_cards(path=COLLECTED):
    """Every stored card window as a normalised glyph, through production's tail.

    Returns (rows, phantoms). A row is (glyph, label, video, sheet, ident); a
    phantom is the same with a non-digit label -- the `no_card` false positives,
    which a digit threshold has no business accepting but which are a
    presence-layer problem, not a digit one.

    Windows production would refuse outright -- no bright tile, too little ink --
    yield None here exactly as they would there, and are dropped: the model is
    never asked about them in production either.
    """
    rows, phantoms = [], []
    with np.load(path, allow_pickle=False) as z:
        patches, shapes = z["patches"], z["shape"]
        ends, vids = z["end"], z["video_id"]
        sheets, colors, slots = z["sheet"], z["color"], z["slot"]
        for i in range(len(patches)):
            h, w = int(shapes[i][0]), int(shapes[i][1])
            ink = SB.glyph_in_window(patches[i][:h, :w])
            if ink is None or ink.size < 4:
                continue
            glyph = SB._normalise(ink)
            label = str(ends[i])
            vid, sheet = str(vids[i]), int(sheets[i])
            # A physical card is one (video, colour, slot, label): within one
            # video the board is not cleared and a card hangs untouched.
            ident = (vid, str(colors[i]), int(slots[i]), label)
            entry = (glyph, label, vid, sheet, ident)
            (rows if label.isdigit() else phantoms).append(entry)
    return rows, phantoms


def load_printed(path=PRINTED):
    with np.load(path, allow_pickle=False) as z:
        return z["x"].astype(np.float32), z["y"].astype(int)


# ----------------------------------------------------------- the architectures

def _fit_mlp(x, y, seed, epochs, copies):
    """The incumbent: the 256-128 ReLU MLP already in `digits.Model`."""
    rng = np.random.default_rng(seed)
    model = D.Model.initialise(hidden=(256, 128), rng=rng)
    return model.fit(None, None, epochs=epochs, rng=rng,
                     augment_from=(x, y), copies=copies)


def _fit_cnn(x, y, seed, epochs, copies):
    """The escalation: a conv net trained on the GPU, read back through numpy.

    Training runs in torch because hand-written conv gradients are the kind of
    thing that is wrong in a way that looks like "convolution does not help".
    What comes back is a `digits.ConvModel`, so everything the gate measures
    goes through the numpy forward pass production will run, and
    `train_conv_torch.check_equivalence` asserts those two agree before the
    model is handed over. Needs the `gpu` extra; `cnn_numpy` is the same
    architecture family trained without it.
    """
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import train_conv_torch

    return train_conv_torch.fit(x, y, seed, epochs=epochs, copies=copies)


def _fit_cnn_numpy(x, y, seed, epochs, copies):
    """`digits.ConvModel` trained by its own numpy backward pass, no torch."""
    rng = np.random.default_rng(seed)
    model = D.ConvModel.initialise(rng=rng)
    return model.fit(None, None, epochs=epochs, rng=rng,
                     augment_from=(x, y), copies=copies)


ARCHITECTURES = {"mlp": _fit_mlp, "cnn": _fit_cnn, "cnn_numpy": _fit_cnn_numpy}


# ------------------------------------------------------------------- the folds

def _fold(task):
    """One fold: train on nine videos plus the printed row, read the tenth.

    A module-level function so a process pool can run the folds concurrently.
    Every fold's randomness comes from its explicit seed, so the result does not
    depend on how many processes ran or in what order they finished.
    """
    held, rows, phantoms, xp, yp, model, seed, epochs, copies = task
    train = [r for r in rows if r[2] != held]
    x = np.concatenate([xp, np.stack([r[0] for r in train])])
    y = np.concatenate([yp, np.array([int(r[1]) for r in train], int)])
    t0 = time.time()
    fitted = ARCHITECTURES[model](x, y, seed, epochs, copies)
    results = []
    for glyph, label, _vid, _sheet, ident in (
            [r for r in rows if r[2] == held] + [p for p in phantoms
                                                 if p[2] == held]):
        pred, conf = fitted.predict(glyph)
        results.append((ident, int(label) if label.isdigit() else None,
                        pred, conf))
    return held, len(train), results, time.time() - t0


def run_folds(rows, xp, yp, model, seed, epochs, copies, phantoms=(),
              jobs=None, log=print):
    """Predict every row from the fold in which its own video was held out.

    Returns {ident: [(digit, pred, conf), ...]} -- one entry per frame, every
    one of them out-of-sample.
    """
    videos = sorted({r[2] for r in rows})
    tasks = [(held, rows, phantoms, xp, yp, model, seed, epochs, copies)
             for held in videos]
    out = collections.defaultdict(list)
    with mp.get_context("fork").Pool(jobs or len(videos)) as pool:
        for held, n_train, results, secs in pool.imap_unordered(_fold, tasks):
            log(f"    fold {held}: {n_train} training cards, "
                f"{len(results)} held-out rows, {secs:.0f}s")
            for ident, digit, pred, conf in results:
                out[ident].append((digit, pred, conf))
    return dict(out)


# ----------------------------------------------------------------- the metrics

def sweep(preds, thresholds=THRESHOLDS):
    """Per-frame read / refused / wrong at each threshold, over digit rows."""
    frames = [f for ident, fs in preds.items() if ident[3].isdigit() for f in fs]
    ph_frames = [f for ident, fs in preds.items()
                 if not ident[3].isdigit() for f in fs]
    table = []
    for t in thresholds:
        read = sum(1 for d, p, c in frames if c >= t and p == d)
        wrong = sum(1 for d, p, c in frames if c >= t and p != d)
        refused = len(frames) - read - wrong
        accepted_ph = sum(1 for _d, _p, c in ph_frames if c >= t)
        table.append({
            "threshold": t, "read": read, "refused": refused, "wrong": wrong,
            "coverage": read / len(frames) if frames else 0.0,
            "phantom_frames_accepted": accepted_ph,
            "phantom_frames": len(ph_frames),
        })
    return table


def tight_threshold(preds):
    """The lowest threshold at which no digit row is read wrong.

    Just above the most confident wrong read: any lower and that read is
    accepted, any higher and correct reads are refused for nothing. It is the
    best coverage a threshold can buy at zero wrong, and it is computed the same
    way for every model. It is read off the measured set, so it is the ceiling
    on this data rather than a threshold that could be shipped as-is.
    """
    wrong = [c for ident, fs in preds.items() if ident[3].isdigit()
             for d, p, c in fs if p != d]
    if not wrong:
        return 0.0
    return float(np.nextafter(max(wrong), 1.0))


def per_digit(preds, t):
    """Rows read / refused / wrong at threshold `t`, by true digit."""
    out = {}
    for ident, fs in preds.items():
        if not ident[3].isdigit():
            continue
        d = int(ident[3])
        e = out.setdefault(d, {"rows": 0, "read": 0, "refused": 0, "wrong": 0,
                               "cards": set(), "cards_read": set()})
        e["rows"] += len(fs)
        e["cards"].add(ident)
        for digit, pred, conf in fs:
            if conf < t:
                e["refused"] += 1
            elif pred == digit:
                e["read"] += 1
            else:
                e["wrong"] += 1
        if all(p == digit for digit, p, _c in fs):
            e["cards_read"].add(ident)
    return {d: {**{k: v for k, v in e.items() if not k.startswith("cards")},
                "distinct_cards": len(e["cards"]),
                "distinct_correct": len(e["cards_read"])}
            for d, e in sorted(out.items())}


def distinct_cards(preds):
    """The per-card view the 42/54 baseline is quoted in: every frame correct.

    Not the gate -- production reads one board state, so it never demands a card
    be right in every frame it was ever photographed in -- but it is the number
    the previous rounds reported, and reproducing it is how this script is
    checked against them.
    """
    cards = [(ident, fs) for ident, fs in preds.items() if ident[3].isdigit()]
    ok = sum(1 for _i, fs in cards if all(p == d for d, p, _c in fs))
    return ok, len(cards)


# --------------------------------------------------------------------- the run

def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", choices=sorted(ARCHITECTURES), default="mlp")
    ap.add_argument("--seeds", type=int, default=3,
                    help="each seed retrains all ten folds; the spread is the "
                         "noise floor any claimed improvement must beat")
    ap.add_argument("--epochs", type=int, default=40)
    ap.add_argument("--copies", type=int, default=8,
                    help="augmented copies per glyph per epoch")
    ap.add_argument("--jobs", type=int, default=None,
                    help="folds to train concurrently (default: all ten)")
    ap.add_argument("--json", default=None, help="write the full result JSON")
    args = ap.parse_args()

    xp, yp = load_printed()
    rows, phantoms = load_cards()
    videos = sorted({r[2] for r in rows})
    idents = {r[4] for r in rows}
    print(f"{len(rows)} card rows over {len(idents)} distinct cards and "
          f"{len(videos)} videos, plus {len(phantoms)} phantom rows "
          f"({len({p[4] for p in phantoms})} distinct)")
    print(f"{len(xp)} printed glyphs in every fold's training set")
    print(f"model {args.model}, {args.seeds} seed(s), {args.epochs} epochs, "
          f"{args.copies} augmented copies per glyph per epoch\n")

    runs = []
    for seed in range(args.seeds):
        print(f"  seed {seed}", flush=True)
        t0 = time.time()
        preds = run_folds(rows, xp, yp, args.model, seed, args.epochs,
                          args.copies, phantoms=phantoms, jobs=args.jobs,
                          log=lambda m: print(m, flush=True))
        table = sweep(preds)
        tight = tight_threshold(preds)
        tight_row = sweep(preds, (tight,))[0]
        ok, n = distinct_cards(preds)
        grid_zero = next((r for r in table if r["wrong"] == 0), None)
        runs.append({
            "seed": seed, "grid": table, "tight": tight_row,
            "grid_zero_wrong": grid_zero,
            "distinct_correct": ok, "distinct_cards": n,
            "per_digit_tight": per_digit(preds, tight),
            "seconds": round(time.time() - t0, 1),
        })
        print(f"    distinct cards all-frames-correct {ok}/{n}; "
              f"tight threshold {tight:.6f} -> coverage "
              f"{100 * tight_row['coverage']:.1f}% at 0 wrong "
              f"({time.time() - t0:.0f}s)\n", flush=True)

    print(f"{'threshold':>10} {'read':>6} {'refused':>8} {'wrong':>6} "
          f"{'coverage':>9}   {'phantom frames':>15}   (per frame, seed 0)")
    for r in runs[0]["grid"]:
        print(f"{r['threshold']:>10} {r['read']:>6} {r['refused']:>8} "
              f"{r['wrong']:>6} {100 * r['coverage']:>8.1f}%   "
              f"{r['phantom_frames_accepted']:>6}/{r['phantom_frames']:<8}"
              f"{'' if r['wrong'] == 0 else '   <-- wrong scores'}")

    def spread(vals):
        return (f"{100 * min(vals):.1f}-{100 * max(vals):.1f}% "
                f"(median {100 * float(np.median(vals)):.1f}%)")

    grid_cov = [r["grid_zero_wrong"]["coverage"] if r["grid_zero_wrong"] else 0.0
                for r in runs]
    grid_thr = [r["grid_zero_wrong"]["threshold"] if r["grid_zero_wrong"] else None
                for r in runs]
    tight_cov = [r["tight"]["coverage"] for r in runs]
    cards = [f"{r['distinct_correct']}/{r['distinct_cards']}" for r in runs]

    print(f"\nGATE ({args.model}, {args.seeds} seeds, per frame, zero wrong)")
    print(f"  lowest grid threshold with 0 wrong: {grid_thr}")
    print(f"  coverage there:                     {spread(grid_cov)}")
    print(f"  coverage at the tight threshold:    {spread(tight_cov)}")
    print(f"  distinct cards correct in every frame: {', '.join(cards)}")
    passed = min(tight_cov) >= TARGET_COVERAGE
    print(f"  gate is coverage >= {100 * TARGET_COVERAGE:.0f}% at 0 wrong: "
          f"{'PASS' if passed else 'FAIL'}")

    print("\nper digit, at the tight threshold (seed 0)")
    print(f"  {'digit':>5} {'rows':>5} {'read':>5} {'refused':>8} {'wrong':>6} "
          f"{'cards':>6} {'all frames right':>17}")
    for d, e in runs[0]["per_digit_tight"].items():
        print(f"  {d:>5} {e['rows']:>5} {e['read']:>5} {e['refused']:>8} "
              f"{e['wrong']:>6} {e['distinct_cards']:>6} "
              f"{e['distinct_correct']:>17}")

    if args.json:
        with open(args.json, "w") as fh:
            json.dump({"model": args.model, "epochs": args.epochs,
                       "copies": args.copies, "runs": runs}, fh, indent=2,
                      default=str)
        print(f"\nwrote {args.json}")


if __name__ == "__main__":
    main()
