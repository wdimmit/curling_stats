#!/usr/bin/env python
"""edge1 A/B: replay segmentation on ``ab_profile.py``'s profiles, four arms.

    base      main's segment.py on every stone on the sheet   (production)
    back      main's segment.py on the stones in play         (the back line alone)
    stray     this segment.py on every stone on the sheet     (the stray rule alone)
    new       this segment.py on the stones in play           (both)

Under each PYTHONHASHSEED given (``_smooth``'s tie-break is hash-ordered, so
each seed is a subprocess), prints every video whose games or ends differ from
base in any arm, with the hosted chart's ends (first entry -> last rest) to
judge them by.

    python ab_segment.py PROFILES_DIR TIMELINES_DIR MAIN_SEGMENT_PY [--seeds 0-9]
"""

import argparse
import importlib.util
import json
import os
import subprocess
import sys
from collections import defaultdict
from pathlib import Path


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def run_one(a):
    from curling_score.game import segment as new
    old = load("segment_main", a.main)
    out = {}
    for p in sorted(Path(a.profiles).glob("*.json")):
        d = json.loads(p.read_text())
        arms = {}
        for arm, mod, key in (("base", old, "on_sheet"), ("back", old, "in_play"),
                              ("stray", new, "on_sheet"), ("new", new, "in_play")):
            s = [mod.Sample(t, a_, b, bool(c), bool(e)) for t, a_, b, c, e in d[key]]
            arms[arm] = [[(e.house, e.start_s, e.end_s) for e in g.ends]
                         for g in mod.segment_games(s)]
        out[d["video_id"]] = arms
    json.dump(out, sys.stdout)


def chart_ends(timelines, vid):
    rows = []
    for p in Path(timelines).glob("*.json"):
        try:
            d = json.loads(p.read_text())
        except ValueError:
            continue
        if d["source"]["video_id"] != vid:
            continue
        for g in d.get("games", []):
            for e in g["ends"]:
                ts = [s.get("t_enter_s") for s in e["shots"] if s.get("t_enter_s")]
                rs = [s.get("t_rest_s") for s in e["shots"] if s.get("t_rest_s")]
                rows.append((round(g["start_s"]), g["index"], e["number"], e["house"],
                             round(min(ts)) if ts else None, round(max(rs)) if rs else None,
                             len(e["shots"])))
    return sorted(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("profiles")
    ap.add_argument("timelines")
    ap.add_argument("main")
    ap.add_argument("--seeds", default="0-9")
    ap.add_argument("--one", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--windows", action="store_true",
                    help="check each hosted end's last rest against base's and new's windows")
    a = ap.parse_args()
    if a.one:
        return run_one(a)
    if a.windows:
        import statistics
        for arm, st in windows(a.profiles, a.timelines, a.main).items():
            t = sorted(st["tails"])
            print(f"{arm}: {st['ends']} chart ends covered; last rest outside the window: "
                  f"{len(st['outside'])}; tail after the last rest median "
                  f"{statistics.median(t):.0f} s, min {t[0]:.0f} s, p10 {t[len(t) // 10]:.0f} s, p90 {t[int(len(t) * 0.9)]:.0f} s, max {t[-1]:.0f} s")
            for row in st["outside"]:
                print("   outside", row)
        return
    lo, hi = (int(x) for x in a.seeds.split("-"))
    per_seed = {}
    for seed in range(lo, hi + 1):
        env = dict(os.environ, PYTHONHASHSEED=str(seed))
        res = subprocess.run([sys.executable, __file__, a.profiles, a.timelines, a.main, "--one"],
                             env=env, capture_output=True, text=True, check=True)
        per_seed[seed] = json.loads(res.stdout)
    vids = sorted(per_seed[lo])
    changed = defaultdict(set)
    for vid in vids:
        for seed, res in per_seed.items():
            arms = res[vid]
            for arm in ("back", "stray", "new"):
                if arms[arm] != arms["base"]:
                    changed[vid].add(arm)
    print(f"{len(vids)} videos, seeds {lo}-{hi}; changed in any arm: {len(changed)}")
    for arm in ("back", "stray", "new"):
        print(f"  {arm}: {sum(arm in v for v in changed.values())}")
    for vid in sorted(changed):
        print(f"\n== {vid}  arms {sorted(changed[vid])}")
        for seed in (lo,):
            arms = per_seed[seed][vid]
            for arm in ("base", "back", "stray", "new"):
                print(f"  {arm:5s} games {[len(g) for g in arms[arm]]}")
            # The ends that differ between base and new, by game.
            b = [(gi, e) for gi, g in enumerate(arms["base"]) for e in g]
            n = [(gi, e) for gi, g in enumerate(arms["new"]) for e in g]
            only_b = [x for x in b if x[1] not in [y[1] for y in n]]
            only_n = [x for x in n if x[1] not in [y[1] for y in b]]
            for gi, e in only_b:
                print(f"    base g{gi} {e}")
            for gi, e in only_n:
                print(f"    new  g{gi} {e}")
        seeds_differ = sorted({s for s, r in per_seed.items() if r[vid]["new"] != per_seed[lo][vid]["new"]})
        if seeds_differ:
            print(f"  new differs from seed {lo} under seeds {seeds_differ}")
        print("  chart:", chart_ends(a.timelines, vid))



def windows(profiles, timelines, main_py, seed=0):
    """Every hosted chart end against the segment end that covers it, in base
    and new: whether its last rock's rest falls inside, and the tail after it."""
    env = dict(os.environ, PYTHONHASHSEED=str(seed))
    res = json.loads(subprocess.run([sys.executable, __file__, profiles, timelines, main_py, "--one"],
                                    env=env, capture_output=True, text=True, check=True).stdout)
    stats = {arm: {"ends": 0, "outside": [], "tails": []} for arm in ("base", "new")}
    for vid, arms in res.items():
        chart = [r for r in chart_ends(timelines, vid) if r[4] is not None and r[5] is not None]
        for arm in ("base", "new"):
            segs = [e for g in arms[arm] for e in g]
            for (_g0, gi, num, house, first, last, n) in chart:
                cover = [e for e in segs if e[0] == house and e[1] <= last and e[2] >= first]
                if not cover:
                    continue
                stats[arm]["ends"] += 1
                e = max(cover, key=lambda e: min(e[2], last) - max(e[1], first))
                if e[2] < last:
                    stats[arm]["outside"].append((vid, gi, num, house, first, last, e))
                else:
                    stats[arm]["tails"].append(e[2] - last)
    return stats


if __name__ == "__main__":
    main()
