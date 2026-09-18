"""Train the card-digit classifier on the board's self-labelled printed row.

Train on printed glyphs, prove it on real cards. The printed 1-14 row labels
itself, so the training set is free and unlimited; the 11 hand-labelled
physical cards in `datasets/board-cards` are never trained on and are the gate.

Three numbers come out of this, and they are the gate the whole plan turns on:

1. accuracy on held-out *printed* frames -- correlation managed 73%;
2. accuracy on the 11 distinct real cards, and whether each is correct in
   *every* frame it appears in -- correlation managed 0 of 11;
3. the confidence distribution over the ~519 empty slot positions against real
   cards, which says whether a threshold can separate "a card is here" from
   "nothing here" -- 54% of empty slots cleared the old correlation margin.

Hyperparameters are chosen against (1) alone. (2) and (3) are looked at once,
at the end: tuning toward 11 held-out cards would spend the only thing they are
good for.
"""

import argparse
import json
from pathlib import Path

import numpy as np

from curling_score.game import digits as D


def evaluate_printed(model, x, y):
    """Accuracy on unaugmented printed glyphs, as `digits` measures it."""
    return D.accuracy(model, x, y)


def evaluate_cards(model, root):
    """Per-physical-card results on the hand-labelled set."""
    cards = D.card_glyphs(root)
    groups = D.distinct_cards(cards)
    rows, all_conf = [], []
    for key in sorted(groups):
        game, color, slot, end = key
        reads, confs = [], []
        for glyph, _, meta in sorted(groups[key], key=lambda e: e[2]["t_s"]):
            digit, conf = model.predict(glyph)
            reads.append(digit)
            confs.append(conf)
            all_conf.append(conf)
        rows.append({
            "game": game, "color": color, "slot": slot, "end": end,
            "frames": len(reads),
            "reads": reads,
            "correct_frames": sum(1 for d in reads if d == end),
            "all_frames_correct": all(d == end for d in reads),
            "min_conf": min(confs), "median_conf": float(np.median(confs)),
        })
    per_row = sum(r["correct_frames"] for r in rows)
    n_rows = sum(r["frames"] for r in rows)
    return {
        "distinct_cards": len(rows),
        "cards_right_in_every_frame": sum(1 for r in rows
                                          if r["all_frames_correct"]),
        "rows": n_rows,
        "row_accuracy": per_row / n_rows if n_rows else 0.0,
        "table": rows,
        "confidences": all_conf,
    }


def evaluate_empty(model, root):
    """Confidence over slot positions with no card in them."""
    empties = D.empty_slot_glyphs(root)
    refused, confs = 0, []
    for glyph, _ in empties:
        if glyph is None:
            refused += 1  # no card tile found: refused before the model is asked
            continue
        _, conf = model.predict(glyph)
        confs.append(conf)
    return {
        "empty_slots": len(empties),
        "refused_before_the_model": refused,
        "reached_the_model": len(confs),
        "confidences": confs,
    }


def percentiles(vals, ps=(0, 5, 25, 50, 75, 95, 100)):
    if not len(vals):
        return {}
    return {f"p{p}": round(float(np.percentile(vals, p)), 4) for p in ps}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--glyphs", default="datasets/board-glyphs/glyphs.npz")
    ap.add_argument("--cards", default="datasets/board-cards")
    ap.add_argument("--out", default=str(D.WEIGHTS))
    ap.add_argument("--hidden", default="256,128")
    ap.add_argument("--epochs", type=int, default=40)
    ap.add_argument("--copies", type=int, default=8,
                    help="augmented copies drawn per glyph per epoch")
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--batch", type=int, default=128)
    ap.add_argument("--weight-decay", type=float, default=1e-4)
    ap.add_argument("--holdout", type=float, default=0.2)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--no-save", action="store_true")
    ap.add_argument("--tune", action="store_true",
                    help="report gate 1 only. Hyperparameters are chosen "
                         "against held-out printed frames, and this makes that "
                         "literal: the 11 real cards are held out, and looking "
                         "at them while turning knobs is how a held-out set "
                         "stops being one")
    ap.add_argument("--report", default=None, help="write the full report JSON")
    args = ap.parse_args()

    with np.load(args.glyphs, allow_pickle=False) as z:
        x, y, t_s = z["x"], z["y"].astype(int), z["t_s"]
        window_s = float(z["window_s"])

    train, val, train_times, val_times = D.frame_split(
        t_s, window_s, holdout=args.holdout, seed=args.seed)
    print(f"{len(x)} glyphs over {len(np.unique(t_s))} frames; "
          f"train {train.sum()} glyphs / {len(train_times)} frames, "
          f"val {val.sum()} glyphs / {len(val_times)} frames", flush=True)

    rng = np.random.default_rng(args.seed)
    hidden = tuple(int(v) for v in args.hidden.split(",") if v)
    model = D.Model.initialise(hidden=hidden, rng=rng)

    xt, yt = x[train], y[train]
    xv, yv = x[val], y[val]

    def on_epoch(epoch, loss):
        if (epoch + 1) % 5 == 0 or epoch == 0:
            acc, *_ = evaluate_printed(model, xv, yv)
            print(f"  epoch {epoch + 1:3d}  loss {loss:.4f}  "
                  f"held-out printed {acc:.4f}", flush=True)

    model.fit(None, None, epochs=args.epochs, lr=args.lr, batch=args.batch,
              weight_decay=args.weight_decay, rng=rng, on_epoch=on_epoch,
              augment_from=(xt, yt), copies=args.copies)

    printed_acc, pred, conf = evaluate_printed(model, xv, yv)
    train_acc, *_ = evaluate_printed(model, xt, yt)

    confusion = {}
    for want, got in zip(yv, pred):
        if want != got:
            confusion[f"{want}->{got}"] = confusion.get(f"{want}->{got}", 0) + 1

    cards = None if args.tune else evaluate_cards(model, args.cards)
    empty = None if args.tune else evaluate_empty(model, args.cards)

    report = {
        "harvest": {
            "glyphs": int(len(x)),
            "frames": int(len(np.unique(t_s))),
            "train_frames": int(len(train_times)),
            "val_frames": int(len(val_times)),
            "window_s": window_s,
        },
        "model": {"hidden": list(hidden), "epochs": args.epochs,
                  "copies": args.copies, "lr": args.lr,
                  "weight_decay": args.weight_decay},
        "gate_1_printed_held_out": {
            "accuracy": round(printed_acc, 4),
            "n": int(val.sum()),
            "train_accuracy": round(train_acc, 4),
            "correlation_baseline": 0.73,
            "confusions": confusion,
        },
    }
    if cards is not None:
        report["gate_2_real_cards"] = {
            k: v for k, v in cards.items() if k != "confidences"
        }
        report["gate_3_empty_slots"] = {
            **{k: v for k, v in empty.items() if k != "confidences"},
            "empty_confidence": percentiles(empty["confidences"]),
            "card_confidence": percentiles(cards["confidences"]),
        }
    print(json.dumps(report, indent=2))

    if args.report:
        Path(args.report).write_text(json.dumps(report, indent=2) + "\n")
    if not args.no_save:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        model.save(args.out)
        print(f"wrote {args.out} ({Path(args.out).stat().st_size} bytes)")


if __name__ == "__main__":
    main()
