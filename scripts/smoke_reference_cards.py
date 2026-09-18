"""Smoke test on the 11 reference cards from a different season.

Trains on everything the gate trains on -- 2052 printed glyphs plus all 538
collected card rows from the ten videos -- and reads the hand-labelled reference
set in `datasets/board-cards`, which is from a different season and is in no
fold of `scripts/gate_digits.py`. About two distinct cards per digit and no
digit above 6, so it is a smoke test and not a gate: it can disconfirm (a model
that cannot read a card it has never seen the season of), but passing it proves
little on its own.

A development tool. Like `train_conv_torch`, it needs torch from the `gpu`
extra, and it takes a list of seeds:

    PYTHONPATH=$PWD/src python scripts/smoke_reference_cards.py 0 1 2
"""
import sys
import time

sys.path.insert(0, "scripts")

import numpy as np
import gate_digits as G
import train_conv_torch as T
from curling_score.game import digits as D

seeds = [int(v) for v in (sys.argv[1:] or ["0"])]
xp, yp = G.load_printed()
rows, _ph = G.load_cards()
x = np.concatenate([xp, np.stack([r[0] for r in rows])])
y = np.concatenate([yp, np.array([int(r[1]) for r in rows], int)])
print(f"training on {len(xp)} printed glyphs + {len(rows)} card rows", flush=True)

cards = D.card_glyphs("datasets/board-cards")
groups = D.distinct_cards(cards)
print(f"reference: {len(cards)} rows, {len(groups)} distinct cards", flush=True)

for seed in seeds:
    t0 = time.time()
    model = T.fit(x, y, seed, epochs=40, copies=8)
    ok = sum(1 for g, end, _ in cards if model.predict(g)[0] == end)
    per = {}
    for g, end, _ in cards:
        d, _c = model.predict(g)
        e = per.setdefault(end, [0, 0])
        e[0] += d == end
        e[1] += 1
    consistent = sum(1 for key, entries in groups.items()
                     if all(model.predict(g)[0] == key[-1] for g, _, _ in entries))
    conf_ok = sum(1 for g, end, _ in cards
                  if model.predict(g)[0] == end and model.predict(g)[1] >= 0.999)
    conf_bad = sum(1 for g, end, _ in cards
                   if model.predict(g)[0] != end and model.predict(g)[1] >= 0.999)
    print(f"seed {seed}: rows {ok}/{len(cards)}, distinct consistent "
          f"{consistent}/{len(groups)}, per-digit "
          f"{ {k: f'{v[0]}/{v[1]}' for k, v in sorted(per.items())} }, "
          f"at thr 0.999 correct {conf_ok} wrong {conf_bad} ({time.time()-t0:.0f}s)",
          flush=True)
