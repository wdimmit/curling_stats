"""Compare the segmentation the colour-threshold profile gives with ds17b's,
from the per-video JSON files written by the worker A/B (seg_ab.py).

    python scripts/profile_ab.py OUT_DIR

Each file holds, for both counters, the games after join + board split as
lists of [house, start_s, end_s] ends. Ends are paired by house and overlap;
a video is listed when its games or ends differ, and the boundary shifts of
the paired ends are summarised over all videos.
"""
import json
import statistics
import sys
from pathlib import Path


def overlap(a, b):
    return min(a[2], b[2]) - max(a[1], b[1])


def pair(old, new):
    """Old and new ends paired one to one by house and largest overlap, in
    order; the rest left unpaired."""
    pairs, used = [], set()
    for i, a in enumerate(old):
        best = None
        for j, b in enumerate(new):
            if j in used or a[0] != b[0] or overlap(a, b) <= 0:
                continue
            if best is None or overlap(a, b) > overlap(a, new[best]):
                best = j
        if best is not None:
            used.add(best)
            pairs.append((i, best))
    return pairs, [i for i in range(len(old)) if i not in {p for p, _ in pairs}], \
        [j for j in range(len(new)) if j not in used]


def flat(games):
    return [(gi, e) for gi, g in enumerate(games) for e in g]


def main(out_dir):
    files = sorted(p for p in Path(out_dir).glob("*.json") if not p.name.endswith(".counts.json"))
    shifts_start, shifts_end, changed, n_ends = [], [], [], 0
    for p in files:
        doc = json.loads(p.read_text())
        old = doc["variants"]["thresholds"]["games"]
        new = doc["variants"]["ds17b"]["games"]
        fo, fn = flat(old), flat(new)
        n_ends += len(fo)
        pairs, lost, gained = pair([e for _, e in fo], [e for _, e in fn])
        regame = [(fo[i][0], fn[j][0]) for i, j in pairs]
        same_games = len(old) == len(new) and all(a == b for a, b in regame)
        for i, j in pairs:
            shifts_start.append(fn[j][1][1] - fo[i][1][1])
            shifts_end.append(fn[j][1][2] - fo[i][1][2])
        big = [(fo[i][1], fn[j][1]) for i, j in pairs
               if abs(fn[j][1][1] - fo[i][1][1]) > 60 or abs(fn[j][1][2] - fo[i][1][2]) > 60]
        if lost or gained or not same_games or big:
            changed.append((doc, [len(g) for g in old], [len(g) for g in new],
                            [fo[i][1] for i in lost], [fn[j][1] for j in gained], big))
    print(f"{len(files)} videos, {n_ends} ends on the thresholds' profile")
    for name, xs in (("start", shifts_start), ("end", shifts_end)):
        a = sorted(abs(x) for x in xs)
        print(f"paired end {name} shift: median {statistics.median(a):.0f} s, "
              f"p90 {a[int(0.9 * len(a))]:.0f} s, max {a[-1]:.0f} s; "
              f"{sum(1 for x in a if x == 0)} of {len(a)} unmoved, "
              f"{sum(1 for x in a if x > 60)} moved over 60 s")
    print(f"\n{len(changed)} videos whose games or ends differ, or an end moved over 60 s:")
    for doc, lo, ln, lost, gained, big in changed:
        print(f"\n{doc['vid']} {doc['format']} {doc['source']} {doc['title']}")
        print(f"  games/ends: {lo} -> {ln}")
        for e in lost:
            print(f"  only on thresholds: {e[0]} {e[1]:.0f}-{e[2]:.0f} ({e[2] - e[1]:.0f} s)")
        for e in gained:
            print(f"  only on ds17b:      {e[0]} {e[1]:.0f}-{e[2]:.0f} ({e[2] - e[1]:.0f} s)")
        for a, b in big:
            print(f"  moved: {a[0]} {a[1]:.0f}-{a[2]:.0f} -> {b[1]:.0f}-{b[2]:.0f}")


if __name__ == "__main__":
    main(sys.argv[1])
