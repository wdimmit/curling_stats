"""Does training on this season's cards beat the shipped reader on this season?

    python scripts/cards2627/gate2627.py --new datasets/board-cards-2627/cards.npz \\
        [--folds 5] [--seeds 2] [--json out.json]

The shipped weights never saw a 2026-27 card, so every new row is out of sample
for them as they stand. The retrained arm is measured the same way: the new
set's videos are split into `--folds` groups by date (a league night's five
sheets share a board's lighting and a set of cards' wear, so a night is held
out whole), and each fold trains on everything `train_conv_torch` ships with --
the printed row, the 2025-26 card set, the reference cards -- plus the other
folds' new cards, then reads its own held-out nights.

Both arms are scored per frame through `gate_digits.sweep`, at the shipped
threshold and at the grid, so the numbers sit beside the original gate's. With
`--harvest`, each arm also reads every harvested window of its held-out nights
-- labelled or not, as production reads every slot it finds -- into
`--reads-out`, for `boards.py` to replay the game's board read from. Run on the
GPU box, as `train_conv_torch` is.
"""
import argparse
import collections
import glob
import json
import os
import sys
import time

import cv2
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import gate_digits as G          # noqa: E402
import train_conv_torch as T     # noqa: E402

from curling_score.game import digits as D            # noqa: E402
from curling_score.game import scoreboard as SB       # noqa: E402


def dates_of(path):
    with np.load(path, allow_pickle=False) as z:
        return dict(zip(z["video_id"].tolist(), z["date"].tolist()))


def fold_of_videos(videos, dates, k):
    """Nights round-robin into k folds, oldest first, so each fold spans the
    season rather than one week of it."""
    nights = sorted({dates[v] for v in videos})
    night_fold = {n: i % k for i, n in enumerate(nights)}
    return {v: night_fold[dates[v]] for v in videos}


def base_training(args):
    xp, yp = G.load_printed(args.glyphs)
    old_rows, _ = G.load_cards(args.old)
    x = [xp, np.stack([r[0] for r in old_rows])]
    y = [yp, np.array([int(r[1]) for r in old_rows], int)]
    ref = [(SB._normalise(g), int(e)) for g, e, _m in D.card_glyphs(args.ref_cards)
           if g is not None]
    if ref:
        x.append(np.stack([g for g, _ in ref]))
        y.append(np.array([e for _, e in ref], int))
    return np.concatenate(x), np.concatenate(y)


def harvest_windows(root):
    """Every harvested read, with its glyph through production's tail."""
    out = []
    for f in sorted(glob.glob(os.path.join(root, "*", "reads.jsonl"))):
        for line in open(f):
            r = json.loads(line)
            win = cv2.imread(os.path.join(root, r["video_id"], "win", r["id"] + ".png"),
                             cv2.IMREAD_GRAYSCALE)
            ink = SB.glyph_in_window(win) if win is not None else None
            glyph = SB._normalise(ink) if ink is not None and ink.size >= 4 else None
            out.append((r["id"], r["video_id"], glyph))
    return out


def read_windows(model, windows):
    return {rid: (model.predict(g) if g is not None else (None, 0.0))
            for rid, _vid, g in windows}


def read(model, rows):
    out = collections.defaultdict(list)
    for glyph, label, _vid, _sheet, ident in rows:
        pred, conf = model.predict(glyph)
        out[ident].append((int(label) if label.isdigit() else None, pred, conf))
    return dict(out)


def summary(name, preds):
    table = G.sweep(preds)
    tight = G.tight_threshold(preds)
    t_row = G.sweep(preds, (tight,))[0]
    shipped = next(r for r in table if r["threshold"] == SB.MIN_CONFIDENCE)
    print(f"  {name}: at {SB.MIN_CONFIDENCE} read {shipped['read']} refused "
          f"{shipped['refused']} wrong {shipped['wrong']} -> "
          f"{100 * shipped['coverage']:.1f}%, phantoms "
          f"{shipped['phantom_frames_accepted']}/{shipped['phantom_frames']}; "
          f"tight {tight:.6f} -> {100 * t_row['coverage']:.1f}%", flush=True)
    return {"grid": table, "tight": tight, "tight_row": t_row,
            "per_digit_shipped": G.per_digit(preds, SB.MIN_CONFIDENCE)}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--new", required=True)
    ap.add_argument("--old", default=G.COLLECTED)
    ap.add_argument("--glyphs", default=G.PRINTED)
    ap.add_argument("--ref-cards", default="datasets/board-cards")
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--seeds", type=int, default=2)
    ap.add_argument("--epochs", type=int, default=40)
    ap.add_argument("--copies", type=int, default=8)
    ap.add_argument("--json", default=None)
    ap.add_argument("--harvest", default=None, help="the harvest's out dir")
    ap.add_argument("--reads-out", default="gate2627-reads.json")
    args = ap.parse_args()

    rows, phantoms = G.load_cards(args.new)
    held_all = rows + phantoms
    dates = dates_of(args.new)
    fold = fold_of_videos({r[2] for r in held_all}, dates, args.folds)
    xb, yb = base_training(args)
    print(f"new set: {len(rows)} card rows over {len({r[4] for r in rows})} cards, "
          f"{len(phantoms)} phantom rows, {len(fold)} videos in {args.folds} folds; "
          f"base training {len(xb)} glyphs", flush=True)

    windows = harvest_windows(args.harvest) if args.harvest else []
    reads = {"shipped": read_windows(D.load_default_model(), windows)}
    result = {"shipped": summary("shipped", read(D.load_default_model(), held_all))}
    for seed in range(args.seeds):
        preds, wins = {}, {}
        for k in range(args.folds):
            train = [r for r in rows if fold[r[2]] != k]
            x = np.concatenate([xb, np.stack([r[0] for r in train])])
            y = np.concatenate([yb, np.array([int(r[1]) for r in train], int)])
            t0 = time.time()
            model = T.fit(x, y, seed, epochs=args.epochs, copies=args.copies)
            held = [r for r in held_all if fold[r[2]] == k]
            preds.update(read(model, held))
            wins.update(read_windows(model, [w for w in windows
                                             if fold.get(w[1], -1) == k]))
            print(f"    seed {seed} fold {k}: {len(train)} new rows trained, "
                  f"{len(held)} held, {time.time() - t0:.0f}s", flush=True)
        result[f"retrained_seed{seed}"] = summary(f"retrained seed {seed}", preds)
        reads[f"retrained_seed{seed}"] = wins

    print("\nper digit at the shipped threshold (read/refused/wrong):")
    arms = [k for k in result]
    digits = sorted({d for a in arms for d in result[a]["per_digit_shipped"]})
    for d in digits:
        cells = []
        for a in arms:
            e = result[a]["per_digit_shipped"].get(d)
            cells.append("-" if e is None else f"{e['read']}/{e['refused']}/{e['wrong']}")
        print(f"  {d}: " + "   ".join(f"{a} {c}" for a, c in zip(arms, cells)))
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(result, fh, indent=1, default=str)
    if windows:
        with open(args.reads_out, "w") as fh:
            json.dump({a: {k: [v[0], round(float(v[1]), 6)] for k, v in r.items()}
                       for a, r in reads.items()}, fh)


if __name__ == "__main__":
    main()
