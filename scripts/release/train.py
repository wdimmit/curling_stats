#!/usr/bin/env python
"""Train the release model on cut.py's crops, and score it one game held out
at a time against the hand marks.

The spike's trainer (``~/curling-work/body-spike/scripts/hand_train.py
--mode stack --scale 2``) on the production pieces: stacks of frames picked
as `releasetime.stacks` picks them, the network `releasemodel.build` makes,
the one input transform `releasemodel.prepare`, and a held-out rock scored by
`releasemodel.p_off` and `releasetime.release_at` -- what the pipeline will
run -- so a fold's score is the pipeline's on that game.

    # on the GPU box, inside the worker image, with the repo mounted:
    python scripts/release/train.py /data/wdd/scratch/release/crops/*.npz \\
        --out /data/wdd/scratch/release/run1 --init resnet18-f37072fd.pth \\
        --folds "SL1+SL2+SL3+SL4+SL5,SL1,SL2,SL3,SL4,SL5,T1020" --save weights/release1.pt

``--init`` is torchvision's ImageNet ResNet-18 (a ``state_dict``); every
weight but the final layer starts from it. A fold is games joined by "+",
held out together; the default is every game alone. ``results.json`` in
``--out`` has every held-out rock with its fold. ``--save`` then trains once
more on every rock and writes the float16 ``state_dict`` the pipeline loads.
"""

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

from curling_score.detect import releasemodel
from curling_score.game import linetime, releasetime

TEE_Y = linetime.TEE_Y

# The spike's schedule: AdamW, a one-cycle learning rate, binary cross-entropy
# on the labelled frames (cut.py's -1s left out).
EPOCHS = 5
BATCH = 128
LR = 3e-4
WEIGHT_DECAY = 1e-4
PCT_START = 0.15

# The spike's augmentation: a shift of up to SHIFT_PX (the path can sit a few
# tenths of a metre off the rock), a mirror, and a gain and bias for the light.
SHIFT_PX = 8
GAIN = 0.25
BIAS = 0.08


def stack_rows(o) -> np.ndarray:
    """``(N, len(STACK_S))`` indices into one rock's frames: the frames each of
    its stacks is made of. `releasetime.stacks` picks them by time; handed each
    frame's own index as its picture, it hands back the indices it picked, so
    training gathers exactly the frames production stacks without holding
    every rock's stacks in memory."""
    n = len(o)
    index = np.arange(n, dtype=float).reshape(n, 1, 1) * 255.0
    return np.rint(releasetime.stacks(index, o)[:, :, 0, 0]).astype(int)


class Data:
    """Every rock of the given npz files end to end; ``meta[r]["a"]:["b"]`` are
    rock ``r``'s frames, ``rows`` each frame's stack."""

    def __init__(self, paths):
        crops, o, label, self.meta = [], [], [], []
        base = 0
        for path in paths:
            with np.load(path, allow_pickle=False) as z:
                meta, offs = json.loads(str(z["meta"])), z["offs"]
                for i, m in enumerate(meta):
                    m["a"], m["b"] = base + int(offs[i]), base + int(offs[i + 1])
                crops.append(z["crops"])
                o.append(z["o"].astype(float))
                label.append(z["label"])
            self.meta += meta
            base += len(crops[-1])
        self.crops, self.o = np.concatenate(crops), np.concatenate(o)
        self.label = np.concatenate(label)
        self.rows = np.concatenate([m["a"] + stack_rows(self.o[m["a"]:m["b"]]) for m in self.meta])

    def stacks(self, idx) -> np.ndarray:
        """``(B, 3, H, W)`` float32 in 0..1 for frames ``idx``: what
        `releasetime.stacks` would give for them."""
        return (self.crops[self.rows[idx]] / 255.0).astype(np.float32)


def augment(x, rng):
    """The same shift, mirror, gain and bias for every frame of a stack, so
    the hand's motion between them is left as it was."""
    import torch
    import torch.nn.functional as F

    b, _c, h, w = x.shape
    padded = F.pad(x, (SHIFT_PX,) * 4, mode="replicate")
    out = torch.empty_like(x)
    for i in range(b):
        dx, dy = rng.integers(0, 2 * SHIFT_PX + 1, 2)
        out[i] = padded[i, :, dy:dy + h, dx:dx + w]
    flip = torch.from_numpy(rng.random(b) < 0.5).to(x.device)
    out[flip] = out[flip].flip(-1)
    gain = 1 + GAIN * torch.from_numpy(rng.uniform(-1, 1, (b, 1, 1, 1))).float().to(x.device)
    bias = BIAS * torch.from_numpy(rng.uniform(-1, 1, (b, 1, 1, 1))).float().to(x.device)
    return out * gain + bias


def initial_model(init):
    """`releasemodel.build`'s network with every weight but the final layer
    from ``init``, an ImageNet ResNet-18 ``state_dict``."""
    import torch

    model = releasemodel.build()
    state = torch.load(init, map_location="cpu", weights_only=True)
    missing, unexpected = model.load_state_dict(
        {k: v for k, v in state.items() if not k.startswith("fc.")}, strict=False)
    if unexpected or set(missing) != {"fc.weight", "fc.bias"}:
        raise SystemExit(f"{init} is not a ResNet-18 state_dict: "
                         f"missing {missing}, unexpected {unexpected}")
    return model


def fit(data, rocks, init, epochs, batch, device, seed):
    """A model trained on ``rocks``' labelled frames. Seeded per call, so the
    final model does not depend on which folds ran before it."""
    import torch
    import torch.nn.functional as F

    torch.manual_seed(seed)
    rng = np.random.default_rng(seed)
    idx = np.concatenate([np.arange(data.meta[r]["a"], data.meta[r]["b"]) for r in rocks])
    idx = idx[data.label[idx] >= 0]
    model = initial_model(init).to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=LR, weight_decay=WEIGHT_DECAY)
    sched = torch.optim.lr_scheduler.OneCycleLR(
        opt, max_lr=LR, total_steps=epochs * (len(idx) // batch + 1), pct_start=PCT_START)
    model.train()
    for epoch in range(epochs):
        rng.shuffle(idx)
        total = 0.0
        for s in range(0, len(idx), batch):
            b = idx[s:s + batch]
            x = releasemodel.prepare(augment(torch.from_numpy(data.stacks(b)).to(device), rng))
            y = torch.from_numpy(data.label[b].astype(np.float32)).to(device)
            loss = F.binary_cross_entropy_with_logits(model(x).squeeze(1), y)
            opt.zero_grad()
            loss.backward()
            opt.step()
            sched.step()
            total += float(loss) * len(b)
        print(f"    epoch {epoch + 1}: loss {total / len(idx):.4f}", flush=True)
    return model


def score(model, data, r, fold) -> dict:
    """One held-out rock read as the pipeline reads it."""
    m = data.meta[r]
    o = data.o[m["a"]:m["b"]]
    model.eval()
    p = releasemodel.p_off(model, releasetime.stacks(data.crops[m["a"]:m["b"]], o))
    path = m["delivery"]
    # o was stored as float32; clipped back onto the path so the last frame's
    # rounding cannot put it a hair past the path's end.
    got = releasetime.release_at(p, np.clip(o, path[0][0], path[-1][0]), path)
    pred = None if got is None else round(TEE_Y - got[1], 3)
    truth = m["mark_yp"]
    keep = ("id", "game", "end", "rock", "color", "position", "mark")
    return {"fold": fold, **{k: m.get(k) for k in keep},
            "mark_yp": truth, "pred_o": None if got is None else round(got[0], 4), "pred_yp": pred,
            "err": None if pred is None or truth is None else round(pred - truth, 3),
            "p": [round(float(v), 3) for v in p]}


def summary(rows) -> str:
    """Median |err|, how many within 0.3/0.5/1.0 m and over 1 m, per colour;
    the share is of the rocks read, as the spike counted it."""
    off = [r for r in rows if r["mark"] == "off"]
    read = [r for r in off if r["err"] is not None]
    held = [r for r in rows if r["mark"] == "held"]
    out = f"{len(read)}/{len(off)} read"
    if read:
        a = np.abs([r["err"] for r in read])
        out += (f", |err| median {np.median(a):.2f} m, within 0.3 m {np.mean(a <= 0.3):.0%}, "
                f"0.5 m {np.mean(a <= 0.5):.0%}, 1.0 m {np.mean(a <= 1.0):.0%}, "
                f"over 1 m {int((a > 1.0).sum())}, bias {np.median([r['err'] for r in read]):+.2f}")
        for colour in ("red", "yellow"):
            c = [abs(r["err"]) for r in read if r["color"] == colour]
            if c:
                out += f", {colour} {np.median(c):.2f} ({len(c)})"
    if held:
        out += f"; held {sum(r['pred_yp'] is None for r in held)}/{len(held)} read as held"
    return out


def folds_of(spec, games) -> list[str]:
    """The folds to run: ``spec``'s, or every game alone."""
    folds = [f.replace(" ", "") for f in spec.split(",")] if spec else sorted(games)
    for fold in folds:
        unknown = set(fold.split("+")) - set(games)
        if unknown:
            raise SystemExit(f"fold {fold}: no game {sorted(unknown)} in the data, "
                             f"which has {sorted(games)}")
        if set(fold.split("+")) >= set(games):
            raise SystemExit(f"fold {fold} holds out every game, leaving nothing to train on")
    return folds


def save(model, path):
    """The float16 ``state_dict`` `releasemodel._load` reads."""
    import torch

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    torch.save({k: v.detach().cpu().half() if v.is_floating_point() else v.detach().cpu()
                for k, v in model.state_dict().items()}, path)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("data", nargs="+", help="cut.py's <tag>.npz files")
    ap.add_argument("--out", required=True, help="where results.json goes")
    ap.add_argument("--init", required=True, help="torchvision's ImageNet ResNet-18 state_dict")
    ap.add_argument("--folds", default="",
                    help='folds, comma-separated; a fold is games held out together, "+"-joined')
    ap.add_argument("--epochs", type=int, default=EPOCHS)
    ap.add_argument("--batch", type=int, default=BATCH)
    ap.add_argument("--device", default=None, help="default: cuda if there is one")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--save", help="train on every rock after the folds and write the weights here")
    args = ap.parse_args(argv)

    data = Data(args.data)
    games = sorted({m["game"] for m in data.meta})
    folds = folds_of(args.folds, games)
    import torch
    device = args.device or ("cuda" if torch.cuda.is_available() else "cpu")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    print(f"{len(data.meta)} rocks, {len(data.crops)} frames, games {games}, on {device}",
          flush=True)

    results = []
    for fold in folds:
        tic = time.time()
        held = set(fold.split("+"))
        train_rocks = [r for r, m in enumerate(data.meta) if m["game"] not in held]
        test_rocks = [r for r, m in enumerate(data.meta) if m["game"] in held]
        print(f"  fold {fold}: train {len(train_rocks)} rocks, test {len(test_rocks)}", flush=True)
        model = fit(data, train_rocks, args.init, args.epochs, args.batch, device, args.seed)
        rows = [score(model, data, r, fold) for r in test_rocks]
        results += rows
        (out / "results.json").write_text(json.dumps(results))
        print(f"  fold {fold}: {summary(rows)} ({time.time() - tic:.0f} s)", flush=True)
    # A rock held out in two folds (alone, and with its league) counts in both.
    alone = [r for r in results if "+" not in r["fold"]]
    if alone and len(alone) < len(results):
        print(f"games one at a time: {summary(alone)}", flush=True)
    if results:
        print(f"ALL: {summary(results)}", flush=True)
    if args.save:
        print(f"  final: train {len(data.meta)} rocks", flush=True)
        save(fit(data, list(range(len(data.meta))), args.init, args.epochs, args.batch, device,
                 args.seed), args.save)
        print(f"saved {args.save}", flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
