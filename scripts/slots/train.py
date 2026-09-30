"""Train the slot presence model (`game/slotmodel.py`) and export numpy weights.

    PYTHONPATH=$PWD/src /home/tcuser/src/curling_score/.venv/bin/python \\
        scripts/slots/train.py <survey-dir> [<survey-dir> ...] [--out src/curling_score/game/slot_weights.npz]

Each dir is a slot dataset -- datasets/slots as committed (`slots.jsonl`,
`windows.npz`), or a survey's own output (`win/`) -- with the review page's
`labels-auto.json` and `edits/*.json`. A slot's label is the
reviewed one where a person saw it, else its automatic one; slots left "ask"
or marked "skip" are not trained on.

First every video is held out in turn (leave-one-video-out): the report gives
each fold's errors, and how the held-out slots compare with today's
threshold rule. Then one model is trained on everything and exported, after
asserting that the numpy forward pass agrees with torch's.
"""
import argparse
import glob
import json
import os
import sys
from pathlib import Path

import cv2
import numpy as np

from curling_score.game import slotmodel as SM

ARCH = dict(c1=8, c2=16, k1=3, k2=3, fc=32)


def load(dirs):
    X, y, vids, rows = [], [], [], []
    for d in dirs:
        d = Path(d).expanduser()
        auto = json.loads((d / "labels-auto.json").read_text())
        human = {}
        for f in sorted(glob.glob(str(d / "edits" / "*.json"))):
            human.update(json.loads(Path(f).read_text())["labels"])
        windows = _windows(d)
        for line in open(d / "slots.jsonl"):
            r = json.loads(line)
            lab = human.get(r["id"], auto.get(r["id"]))
            if lab not in ("card", "blank"):
                continue
            win = windows(r["id"])
            if win is None:
                continue
            X.append(SM.as_input(win))
            y.append(1 if lab == "card" else 0)
            vids.append(r["vid"])
            rows.append(dict(r, label=lab, human=r["id"] in human))
    return np.stack(X).astype(np.float32), np.array(y), np.array(vids), rows


def _windows(d: Path):
    """The slot windows of one dataset: `windows.npz` as committed
    (datasets/slots), else the survey's `win/<id>.png` files."""
    if (d / "windows.npz").exists():
        z = np.load(d / "windows.npz")
        offs = np.concatenate([[0], np.cumsum(z["shapes"][:, 0].astype(int) * z["shapes"][:, 1])])
        index = {str(i): k for k, i in enumerate(z["ids"])}
        pix, shapes = z["pixels"], z["shapes"]

        def get(i):
            k = index.get(i)
            if k is None:
                return None
            return pix[offs[k]:offs[k + 1]].reshape(int(shapes[k][0]), int(shapes[k][1]))
        return get
    return lambda i: cv2.imread(str(d / "win" / f"{i}.png"), cv2.IMREAD_GRAYSCALE)


def augment(x, rng):
    """A shift of up to 2 px each way, a little exposure on the raw channel and
    a little contrast on the normalised one, per copy."""
    out = np.empty_like(x)
    for i, a in enumerate(x):
        dy, dx = rng.integers(-2, 3, size=2)
        a = np.roll(np.roll(a, dy, axis=1), dx, axis=2)
        raw = a[0] + rng.uniform(-0.25, 0.25) + rng.normal(0, 0.03, a[0].shape)   # exposure: +/-16 grey levels
        norm = a[1] * rng.uniform(0.85, 1.15) + rng.normal(0, 0.05, a[1].shape)
        out[i] = np.stack([raw, norm])
    return out


def _net(device):
    import torch
    from torch import nn
    h, w = SM.SLOT_SHAPE
    flat = ARCH["c2"] * (h // 4) * (w // 4)
    net = nn.Sequential(
        nn.Conv2d(SM.CHANNELS, ARCH["c1"], ARCH["k1"], padding=ARCH["k1"] // 2), nn.ReLU(), nn.MaxPool2d(2),
        nn.Conv2d(ARCH["c1"], ARCH["c2"], ARCH["k2"], padding=ARCH["k2"] // 2), nn.ReLU(), nn.MaxPool2d(2),
        nn.Flatten(), nn.Linear(flat, ARCH["fc"]), nn.ReLU(), nn.Linear(ARCH["fc"], 2)).to(device)
    return net, flat


def to_numpy(net, flat) -> SM.SlotModel:
    c1, c2, f1, f2 = net[0], net[3], net[7], net[9]
    p = [c1.weight.detach().cpu().numpy().reshape(ARCH["c1"], -1).T, c1.bias.detach().cpu().numpy(),
         c2.weight.detach().cpu().numpy().reshape(ARCH["c2"], -1).T, c2.bias.detach().cpu().numpy(),
         f1.weight.detach().cpu().numpy().T, f1.bias.detach().cpu().numpy(),
         f2.weight.detach().cpu().numpy().T, f2.bias.detach().cpu().numpy()]
    return SM.SlotModel([np.ascontiguousarray(v, np.float32) for v in p], dict(ARCH, flat=flat))


def check_equivalence(net, model, n=64, tol=2e-4):
    import torch
    x = np.random.default_rng(0).normal(size=(n, SM.CHANNELS, *SM.SLOT_SHAPE)).astype(np.float32)
    torch.backends.cudnn.allow_tf32 = False
    torch.backends.cuda.matmul.allow_tf32 = False
    net.eval()
    with torch.no_grad():
        dev = next(net.parameters()).device
        want = torch.softmax(net(torch.from_numpy(x).to(dev)), 1).cpu().numpy()
    worst = float(np.abs(want - model.probs(x)).max())
    if worst > tol:
        raise AssertionError(f"numpy and torch forward differ by {worst:.2e}")
    return worst


def fit(X, y, seed=0, epochs=25, copies=2, batch=256, lr=2e-3):
    import torch
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    torch.manual_seed(seed)
    rng = np.random.default_rng(seed)
    net, flat = _net(device)
    opt = torch.optim.Adam(net.parameters(), lr=lr, weight_decay=1e-4)
    # Cards are about a tenth of the slots: weight them up rather than let the
    # net learn that "blank" is nearly always right.
    w = torch.tensor([1.0, float(max(1.0, (y == 0).sum() / max(1, (y == 1).sum())))],
                     dtype=torch.float32, device=device)
    loss_fn = torch.nn.CrossEntropyLoss(weight=w)
    for _ in range(epochs):
        xe = np.concatenate([augment(X, rng) for _ in range(copies)])
        ye = np.concatenate([y] * copies)
        xt = torch.from_numpy(xe).to(device)
        yt = torch.as_tensor(ye, device=device)
        order = torch.randperm(len(xt), device=device)
        net.train()
        for s in range(0, len(order), batch):
            sel = order[s:s + batch]
            opt.zero_grad(set_to_none=True)
            loss_fn(net(xt[sel]), yt[sel]).backward()
            opt.step()
    net.eval()
    model = to_numpy(net, flat)
    check_equivalence(net, model)
    return model


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dirs", nargs="+")
    ap.add_argument("--out", default=None)
    ap.add_argument("--no-folds", action="store_true")
    args = ap.parse_args()
    X, y, vids, rows = load(args.dirs)
    print(f"{len(y)} labelled slots: {int(y.sum())} card, {int((y == 0).sum())} blank; "
          f"{sum(r['human'] for r in rows)} seen by a person; videos {sorted(set(vids))}", flush=True)
    if not args.no_folds:
        tot = dict(fn=0, fp=0, n=0, ones=0, ones_hit=0, old_miss_fixed=0, old_miss=0, old_phantom=0, old_phantom_fixed=0)
        held_p, held_y = [], []
        for v in sorted(set(vids)):
            tr, te = vids != v, vids == v
            m = fit(X[tr], y[tr])
            p = m.probs(X[te])[:, 1]
            held_p.extend(p.tolist()); held_y.extend(y[te].tolist())
            pred = p >= SM.MIN_P_CARD
            yt = y[te]
            rt = [r for r, k in zip(rows, te) if k]
            fn = int(((yt == 1) & ~pred).sum()); fp = int(((yt == 0) & pred).sum())
            ones = [(r, q) for r, q, t in zip(rt, pred, yt) if t == 1 and r.get("digit") == 1]
            om = [(r, q) for r, q, t in zip(rt, pred, yt) if t == 1 and not r["old"]]
            op = [(r, q) for r, q, t in zip(rt, pred, yt) if t == 0 and r["old"]]
            tot["fn"] += fn; tot["fp"] += fp; tot["n"] += int(te.sum())
            tot["ones"] += len(ones); tot["ones_hit"] += sum(q for _, q in ones)
            tot["old_miss"] += len(om); tot["old_miss_fixed"] += sum(q for _, q in om)
            tot["old_phantom"] += len(op); tot["old_phantom_fixed"] += sum(not q for _, q in op)
            print(f"  held out {v}: {int(te.sum())} slots ({int(yt.sum())} card) -> missed cards {fn}, "
                  f"false cards {fp}; '1' cards {sum(q for _, q in ones)}/{len(ones)}; "
                  f"cards today's rule missed {sum(q for _, q in om)}/{len(om)}; "
                  f"today's false cards refused {sum(not q for _, q in op)}/{len(op)}", flush=True)
            for r, q, t, pp in zip(rt, pred, yt, p):
                if bool(q) != bool(t):
                    print(f"      wrong: {r['id']} label {r['label']} p_card {pp:.3f} digit {r.get('digit')} ink {r['ink']} old {r['old']}")
        print(f"all folds: {tot}")
        hp, hy = np.array(held_p), np.array(held_y)
        print("held-out, by cutoff:  " + "  ".join(
            f"{c}: missed {int(((hy == 1) & (hp < c)).sum())} false {int(((hy == 0) & (hp >= c)).sum())}"
            for c in (0.5, 0.8, 0.9, 0.95, 0.98)))
    model = fit(X, y)
    out = args.out or str(SM.WEIGHTS)
    model.save(out)
    print("weights ->", out, os.path.getsize(out), "bytes")


if __name__ == "__main__":
    sys.exit(main())
