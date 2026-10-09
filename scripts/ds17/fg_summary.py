#!/usr/bin/env python
"""Summarise fullgames.py: each model against the reference over whole games.

    python scripts/ds17/fg_summary.py DIR [--ref ds16a]
"""
import argparse
import json
from pathlib import Path


def runs(bits, ch):
    best = cur = 0
    for b in bits:
        cur = cur + 1 if b == ch else 0
        best = max(best, cur)
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dir", type=Path)
    ap.add_argument("--ref", default="ds16a")
    a = ap.parse_args()
    docs = [json.loads(p.read_text()) for p in sorted(a.dir.glob("*.json"))]
    throws = [t for d in docs for t in d["throws"]]
    arrivals = [x for d in docs for x in d["arrivals"]]
    names = list(throws[0]["m"]) if throws else list(arrivals[0]["m"])
    print(f"{len(docs)} sources, {len(throws)} throws, {len(arrivals)} arrivals")
    print(f"{'model':12s} | stone found | throws w/ suspects | hat tracks | boxes>=0.5 || arrival frames hit | "
          f"vs {a.ref}: lost / gained | arrivals with a 2+ lost run | arrivals lost outright")
    for n in names:
        nt = len(throws)
        st = sum(1 for t in throws if t["m"][n][0])
        su = sum(1 for t in throws if t["m"][n][1])
        sh = sum(1 for t in throws if t["m"][n][3])
        hi = sum(t["m"][n][2] for t in throws)
        fr = sum(len(x["m"][n]) for x in arrivals)
        hit = sum(x["m"][n].count("1") for x in arrivals)
        lost = gained = run2 = gone = 0
        for x in arrivals:
            r, m = x["m"][a.ref], x["m"][n]
            diff = "".join("L" if rb == "1" and mb == "0" else ("G" if rb == "0" and mb == "1" else ".")
                           for rb, mb in zip(r, m))
            lost += diff.count("L")
            gained += diff.count("G")
            run2 += runs(diff, "L") >= 2
            gone += r.count("1") >= 2 and m.count("1") == 0
        print(f"{n:12s} | {100*st/nt:10.2f}% | {su:5d} ({100*su/nt:4.1f}%) | {sh:4d} ({100*sh/nt:3.1f}%) | {hi:5d} || "
              f"{100*hit/fr:6.2f}% | {lost:6d} / {gained:6d} | {run2:5d} | {gone:4d}")


if __name__ == "__main__":
    main()
