"""Compare two practice replays of one recording: the composite against the cameras.

    python scripts/cameras/compare_replays.py out/run-comp out/run-cams

Each directory is a ``practice-replay --out``. Throws are paired by house and
colour, then by when they came to rest (or were released, for one that never
arrived) within ``PAIR_S``. It prints the throws only one side has, and for
each pair how far apart the two put the rest and the split, then the second
run's latency. Camera-direct phase 2's gate (the plan's Task 9): no unmatched
throw, every rest within ``REST_MAX_CM`` and their median within
``REST_MEDIAN_CM``, and the second run's latency p90 within ``LATENCY_P90_S``.
Exits 1 when the gate fails.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np

PAIR_S = 1.0
REST_MAX_CM = 5.0
REST_MEDIAN_CM = 2.0
LATENCY_P90_S = 20.0


def load(directory: Path) -> list[dict]:
    lines = (Path(directory) / "throws.jsonl").read_text().splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def when(throw: dict) -> float:
    return throw["t_rest_s"] if throw.get("arrived") else throw["t_release_s"]


def apart(x: dict, y: dict) -> float:
    """How far apart in time two throws are: by release when both have one
    (a stone that leaves the panel is last seen at a moment that differs
    between runs, so its rest time does too), else as ``when``."""
    if x.get("t_release_s") is not None and y.get("t_release_s") is not None:
        return abs(x["t_release_s"] - y["t_release_s"])
    return abs(when(x) - when(y))


def pair(a: list[dict], b: list[dict], tol_s: float = PAIR_S):
    """``(pairs, only_a, only_b)``: each of ``a``'s throws with the nearest of
    ``b``'s of the same house and colour within ``tol_s``, in time order."""
    left = list(b)
    pairs, only_a = [], []
    for x in sorted(a, key=when):
        near = [y for y in left if (y["house"], y["color"]) == (x["house"], x["color"])
                and apart(x, y) <= tol_s]
        if not near:
            only_a.append(x)
            continue
        y = min(near, key=lambda y: apart(x, y))
        left.remove(y)
        pairs.append((x, y))
    return pairs, only_a, sorted(left, key=when)


def rest_cm(x: dict, y: dict) -> float | None:
    if not (x.get("rest") and y.get("rest")):
        return None
    return 100.0 * math.dist((x["rest"]["x"], x["rest"]["y"]), (y["rest"]["x"], y["rest"]["y"]))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("a", type=Path, help="the composite's replay")
    p.add_argument("b", type=Path, help="the cameras' replay")
    args = p.parse_args(argv)
    a, b = load(args.a), load(args.b)
    pairs, only_a, only_b = pair(a, b)
    for x in only_a:
        print(f"only in {args.a.name}: {x['id']} {x['house']} {x['color']}")
    for y in only_b:
        print(f"only in {args.b.name}: {y['id']} {y['house']} {y['color']}")
    rests = []
    for x, y in pairs:
        d = rest_cm(x, y)
        if d is not None:
            rests.append(d)
        split = ("-" if x.get("split_s") is None or y.get("split_s") is None
                 else f"{1000 * (y['split_s'] - x['split_s']):+.0f} ms")
        print(f"{x['id']} ~ {y['id']} {x['house']} {x['color']}: rest "
              f"{'-' if d is None else f'{d:.1f} cm'}, split {split}")
    latencies = [y["latency_s"] for y in b if y.get("latency_s") is not None]
    failed = []
    if only_a or only_b:
        failed.append(f"{len(only_a)} + {len(only_b)} unmatched")
    if rests:
        print(f"rest: {len(rests)} pairs, median {np.median(rests):.1f} cm, "
              f"max {max(rests):.1f} cm")
        if max(rests) > REST_MAX_CM or np.median(rests) > REST_MEDIAN_CM:
            failed.append("rest")
    if latencies:
        p50, p90 = np.percentile(latencies, [50, 90])
        print(f"{args.b.name} latency p50 {p50:.1f} s, p90 {p90:.1f} s")
        if p90 > LATENCY_P90_S:
            failed.append(f"latency p90 {p90:.1f} s")
    print("gate: PASS" if not failed else f"gate: FAIL ({'; '.join(failed)})")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
