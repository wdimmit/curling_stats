#!/usr/bin/env python
"""How much of a timeline got a `line`, how wide the throws read, and -- against
a baseline made with --no-line -- that nothing else moved.

    python scripts/line/validate_lines.py out/line-vxu9/timeline.json \
        --baseline out/line-base-vxu9/timeline.json
"""
import argparse
import json
import statistics

IGNORED_TOP = {"processing_version", "analysed_at"}
LINE_KEYS = {"line"}


def shots(doc):
    for g in doc["games"]:
        for e in g["ends"]:
            for s in e["shots"]:
                yield g["index"], e["number"], s


def summary(doc):
    broomed = [s for _g, _e, s in shots(doc) if s.get("target_broom") and not s.get("missing")]
    lined = [s for s in broomed if s.get("line")]
    out = [s["line"]["at_broom"]["miss_m"] * (1 if s["target_broom"]["x"] >= 0 else -1)
           for s in lined if abs(s["target_broom"]["x"]) > 0.3]
    conf = [s["line"]["confirmed"] for s in lined]
    print(f"shots with a broom {len(broomed)}, with a line {len(lined)} "
          f"({len(lined) / max(1, len(broomed)):.0%})")
    if out:
        print(f"  line at the broom, + outside: median {100 * statistics.median(out):+.0f} cm "
              f"over {len(out)} shots with |broom x| > 0.3")
    print(f"  confirmed {conf.count(True)}, disagrees {conf.count(False)}, unseen {conf.count(None)}")


def _unstamped(v):
    """A top-level value without the run's own stamps (`source` carries
    `analysed_at` and the processing version)."""
    return {k: x for k, x in v.items() if k not in IGNORED_TOP} if isinstance(v, dict) else v


def diff(doc, base):
    bad = 0
    for k in set(doc) | set(base):
        if k in IGNORED_TOP or k in ("games", "calibration", "schema_version"):
            continue
        if _unstamped(doc.get(k)) != _unstamped(base.get(k)):
            print(f"  top-level {k} differs"); bad += 1
    n_doc, n_base = len(list(shots(doc))), len(list(shots(base)))
    if n_doc != n_base:
        print(f"  shot counts differ: {n_doc} vs {n_base}"); bad += 1
    for (g, e, s), (_g, _e, b) in zip(shots(doc), shots(base)):
        for k in set(s) | set(b):
            if k in LINE_KEYS:
                continue
            if s.get(k) != b.get(k):
                print(f"  game {g} end {e} shot {s['number']}: {k} differs"); bad += 1
    print(f"  {bad} differences outside `line`")
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("timeline")
    ap.add_argument("--baseline")
    a = ap.parse_args()
    doc = json.load(open(a.timeline))
    summary(doc)
    if a.baseline:
        raise SystemExit(1 if diff(doc, json.load(open(a.baseline))) else 0)


if __name__ == "__main__":
    main()
