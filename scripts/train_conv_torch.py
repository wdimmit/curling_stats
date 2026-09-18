"""Train the card-digit conv model on the GPU and export numpy weights.

A development tool, not part of the shipped path. It needs `torch`, which lives
in the `gpu` extra, and nothing under `src/curling_score/game/` imports it: the
model that ships is `digits.ConvModel`, whose forward pass is numpy, so
`curling-score analyze` keeps working on a base install (ruling R10, as amended
-- inference must be numpy, training need not be).

Run it against this worktree's source explicitly, because the torch venv lives
in another checkout:

    PYTHONPATH=$PWD/src /home/tcuser/src/curling_score/.venv/bin/python \\
        scripts/train_conv_torch.py

`fit()` returns a `digits.ConvModel` with the trained weights in it, so the gate
script trains through torch and measures through exactly the numpy forward pass
production will run. `check_equivalence()` is the thing that makes that safe:
it asserts the two forward passes agree on the same inputs, which is what stops
a training/serving skew from being mistaken for a modelling result.

The layout the export relies on, checked by `check_equivalence` and by
`tests/test_digits.py`:

* conv weight (out, in, kh, kw) -> (in*kh*kw, out), because `_im2col` lays a
  patch out as (channel, row, column) in that order;
* the flatten before the first dense layer is (channel, row, column) on both
  sides, which is what `Tensor.flatten(1)` and `reshape(n, -1)` both do;
* linear weight (out, in) -> (in, out).
"""

import argparse
import time

import numpy as np

from curling_score.game import digits as D
from curling_score.game.scoreboard import GLYPH_SHAPE

# Wider than the numpy model was, because the reason that one was small was the
# cost of training it on a CPU, and that reason is gone. Still small in absolute
# terms: the training set is 54 physical cards and 2052 printed glyphs.
ARCH = dict(c1=32, c2=64, k1=5, k2=3, fc=128)


def _net(arch, dropout, device):
    import torch
    from torch import nn

    h, w = GLYPH_SHAPE
    flat = arch["c2"] * (h // 4) * (w // 4)
    net = nn.Sequential(
        nn.Conv2d(1, arch["c1"], arch["k1"], padding=arch["k1"] // 2),
        nn.ReLU(),
        nn.MaxPool2d(2),
        nn.Conv2d(arch["c1"], arch["c2"], arch["k2"], padding=arch["k2"] // 2),
        nn.ReLU(),
        nn.MaxPool2d(2),
        nn.Flatten(),
        nn.Dropout(dropout),
        nn.Linear(flat, arch["fc"]),
        nn.ReLU(),
        nn.Linear(arch["fc"], D.NCLASS),
    ).to(device)
    for m in net:
        if isinstance(m, (nn.Conv2d, nn.Linear)):
            torch.nn.init.kaiming_normal_(m.weight, nonlinearity="relu")
            torch.nn.init.zeros_(m.bias)
    return net


def to_numpy_model(net, arch) -> "D.ConvModel":
    """The trained torch net as the numpy `ConvModel` that ships."""
    conv1, conv2 = net[0], net[3]
    fc1, fc2 = net[8], net[10]
    h, w = GLYPH_SHAPE
    spec = dict(arch, flat=arch["c2"] * (h // 4) * (w // 4))
    p = [
        conv1.weight.detach().cpu().numpy().reshape(arch["c1"], -1).T,
        conv1.bias.detach().cpu().numpy(),
        conv2.weight.detach().cpu().numpy().reshape(arch["c2"], -1).T,
        conv2.bias.detach().cpu().numpy(),
        fc1.weight.detach().cpu().numpy().T,
        fc1.bias.detach().cpu().numpy(),
        fc2.weight.detach().cpu().numpy().T,
        fc2.bias.detach().cpu().numpy(),
    ]
    return D.ConvModel([np.ascontiguousarray(v, np.float32) for v in p], spec)


def check_equivalence(net, model, rng=None, n=64, tol=2e-4):
    """Assert the numpy forward pass and the torch one agree.

    Training on the GPU and reading with numpy is two implementations of one
    function, and nothing else in this task would catch them drifting apart: a
    skew would show up as a worse gate number and read as "convolution does not
    help". Returns the largest absolute difference in class probability.
    """
    import torch

    rng = rng if rng is not None else np.random.default_rng(0)
    x = rng.normal(size=(n, *GLYPH_SHAPE)).astype(np.float32)
    net.eval()
    # TF32 is on by default on this card and carries about ten mantissa bits,
    # which shows up here as a 6e-4 disagreement that is the GPU's arithmetic
    # and not a wrong weight layout. Turned off for the comparison so that what
    # is left is the layout, which is what this checks. Training keeps it.
    cudnn_tf32 = torch.backends.cudnn.allow_tf32
    matmul_tf32 = torch.backends.cuda.matmul.allow_tf32
    torch.backends.cudnn.allow_tf32 = False
    torch.backends.cuda.matmul.allow_tf32 = False
    try:
        with torch.no_grad():
            device = next(net.parameters()).device
            t = torch.from_numpy(x).reshape(n, 1, *GLYPH_SHAPE).to(device)
            want = torch.softmax(net(t), dim=1).cpu().numpy()
    finally:
        torch.backends.cudnn.allow_tf32 = cudnn_tf32
        torch.backends.cuda.matmul.allow_tf32 = matmul_tf32
    got = model.probs(x.reshape(n, -1))
    worst = float(np.abs(want - got).max())
    if worst > tol:
        raise AssertionError(f"numpy and torch forward differ by {worst:.2e}")
    return worst


def fit(x, y, seed, epochs=40, copies=8, lr=1e-3, batch=128, weight_decay=1e-4,
        dropout=0.3, arch=ARCH, log=None, verify=True):
    """Train on the GPU; return the numpy `ConvModel`.

    Same data recipe as the MLP's: a fresh augmented draw every epoch, `copies`
    per glyph, Adam, weight decay on the matrices only. The augmentation is
    `digits.augment` unchanged, on the CPU, because it is the one thing in this
    task that must not differ between the two models being compared.
    """
    import torch

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    torch.manual_seed(seed)
    rng = np.random.default_rng(seed)
    net = _net(arch, dropout, device)

    decay, plain = [], []
    for name, param in net.named_parameters():
        (plain if name.endswith("bias") else decay).append(param)
    opt = torch.optim.Adam([{"params": decay, "weight_decay": weight_decay},
                            {"params": plain, "weight_decay": 0.0}], lr=lr)
    loss_fn = torch.nn.CrossEntropyLoss()
    idx_of = {d: i for i, d in enumerate(D.LABELS)}

    for epoch in range(epochs):
        xe, ye = D.augmented_batch(x, y, rng=rng, copies=copies)
        xt = torch.from_numpy(
            xe.reshape(len(xe), 1, *GLYPH_SHAPE)).to(device)
        yt = torch.as_tensor([idx_of[int(v)] for v in ye], device=device)
        order = torch.randperm(len(xt), device=device)
        net.train()
        total = 0.0
        for s in range(0, len(order), batch):
            sel = order[s:s + batch]
            opt.zero_grad(set_to_none=True)
            loss = loss_fn(net(xt[sel]), yt[sel])
            loss.backward()
            opt.step()
            total += float(loss) * len(sel)
        if log is not None:
            log(epoch, total / len(xt))

    net.eval()
    model = to_numpy_model(net, arch)
    if verify:
        check_equivalence(net, model, rng=rng)
    return model


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--glyphs", default="datasets/board-glyphs/glyphs.npz")
    ap.add_argument("--cards", default="datasets/board-cards-train/cards.npz")
    ap.add_argument("--out", default=None, help="write the weights here")
    ap.add_argument("--epochs", type=int, default=40)
    ap.add_argument("--copies", type=int, default=8)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    import sys
    sys.path.insert(0, "scripts")
    import gate_digits as G

    xp, yp = G.load_printed(args.glyphs)
    rows, _ph = G.load_cards(args.cards)
    x = np.concatenate([xp, np.stack([r[0] for r in rows])])
    y = np.concatenate([yp, np.array([int(r[1]) for r in rows], int)])
    print(f"training on {len(xp)} printed glyphs and {len(rows)} card rows",
          flush=True)

    t0 = time.time()
    model = fit(x, y, args.seed, epochs=args.epochs, copies=args.copies,
                log=lambda e, l: print(f"  epoch {e + 1:3d} loss {l:.4f} "
                                       f"({time.time() - t0:.0f}s)", flush=True))
    pred, _conf = model.predict_many(
        np.stack([D.as_input(r[0]) for r in rows]))
    truth = np.array([int(r[1]) for r in rows])
    print(f"in-sample card rows {int((pred == truth).sum())}/{len(rows)}")
    if args.out:
        model.save(args.out)
        print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
