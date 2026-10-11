"""A branch timeline's spin against the spike's rates for the same rocks.

    python scripts/spin/ab_parity.py new/timeline.json SPIKE_RATES.json [...] [--game=N]

The spike read every rock at 30 fps over its own crops with the same model;
the pipeline reads at 10 fps. The spike read the first game of the timeline it
was given (``scripts/spin/extract.py``: ``games[0]``), so one game is paired,
by (end, shot), over the ends the spike's rates cover. ``--game`` picks it
where the two timelines split the video differently: 0SWB4g3SJoE's spike game
is this pipeline's second (``--game=1``). Prints how many each read, and the
pipeline's rpm against 60 x the spike's early rate.
"""
import json
import sys

import numpy as np

args = [a for a in sys.argv[1:] if not a.startswith("--game=")]
game = next((int(a.split("=")[1]) for a in sys.argv[1:] if a.startswith("--game=")), 0)
doc = json.load(open(args[0]))
spike, covered = {}, set()
for path in args[1:]:
    for r in json.load(open(path)):
        covered.add(int(r["rock"][1:r["rock"].index("s")]))
        if "f_early" in r and r["a"] >= 0.1 and r["a"] / r["rms"] >= 3 and r["turns_in_view"] >= 0.5:
            spike[r["rock"]] = 60.0 * r["f_early"]
pairs, only_pipe, only_spike, total = [], 0, 0, 0
for e in doc["games"][game]["ends"]:
    if e["number"] not in covered:
        continue
    for s in e["shots"]:
        if s.get("missing"):
            continue
        total += 1
        k = f"e{e['number']}s{s['number']:02d}"
        got = (s.get("spin") or {}).get("rpm")
        if got is not None and k in spike:
            pairs.append((k, got, spike[k]))
        elif got is not None:
            only_pipe += 1
        elif k in spike:
            only_spike += 1
d = np.array([abs(a / b - 1) for _k, a, b in pairs])
print(f"ends {sorted(covered)}, rocks {total}: both {len(pairs)}, pipeline only {only_pipe}, "
      f"spike only {only_spike}")
if len(d):
    print(f"pipeline vs spike: median {np.median(d) * 100:.1f}%, 90% {np.percentile(d, 90) * 100:.1f}%, "
          f">10%: {[(k, round(a, 1), round(b, 1)) for k, a, b in pairs if abs(a / b - 1) > 0.10]}")
