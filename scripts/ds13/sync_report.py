"""Which recordings have their cameras in sync, and which do not.

The composite frame carries four sources -- two overhead panels and two wide
side cameras -- and the compositor does not guarantee they show the same
instant. On AEqLTgM25Tc one pair sits a steady 0.20 s apart and another stepped
from 0.9 s out to in-sync partway through the recording. On VXU9xwmugRg both
pairs agree to 0.05 s.

That matters twice over. The cross-check in `split.long_split` compares the
side view against the THROWING panel, so a desync there refuses good crossings
-- which is how this was found. But a split is
``t_far(arriving panel) - t_near(side camera)``, a DIFFERENT pair, so a
recording can pass the cross-check and still carry splits wrong by the arriving
panel's offset. A 0.9 s error on a 13 s split is 7% of the ice speed, and
nothing in the output says so.

This reads the pairings `split_coverage.py --dump-refused` writes and reports,
per recording and per pair: the offset, its spread, and whether it steps. It
does not correct anything. The cross-check measures (side, throwing panel) and
a split needs (arriving panel, side); four sources and two equations do not
determine the ones we need, so subtracting a measured offset from the wrong
pair would make the number worse while looking fixed.
"""
import argparse
import json
import statistics
import sys
from pathlib import Path

# Within a regime the pairings are tight -- sd about 0.05 s on a video that is
# in sync. Beyond this the two cameras are not showing the same instant.
IN_SYNC_S = 0.10
# A step is only a step if both sides of it are tight and far apart. Half the
# ~0.9 s seen on AEqLTgM25Tc, comfortably clear of the 0.05 s noise floor.
STEP_S = 0.40

OTHER = {"top": "bottom", "bottom": "top"}
CAM = {"top": "left", "bottom": "right"}


def find_step(rows):
    """Where the offset changed, as ``(t, before, after)`` or None.

    A changepoint: the split that leaves both sides TIGHTEST, scored by total
    absolute deviation from each side's own median. Maximising the difference
    of medians instead looks equivalent and is not -- on a bimodal set the
    median of a mixed group sits at the edge of one cluster, so the score keeps
    climbing as the split moves past the real change. That put
    AEqLTgM25Tc's step at t=12617 when it is at 9400: every crossing before it
    is 0.9 s out and every one after is in sync, which a glance at the series
    shows and the median difference did not.
    """
    if len(rows) < 8:
        return None
    best = None
    for i in range(4, len(rows) - 3):
        a = [r["delta"] for r in rows[:i]]
        b = [r["delta"] for r in rows[i:]]
        ma, mb = statistics.median(a), statistics.median(b)
        cost = sum(abs(v - ma) for v in a) + sum(abs(v - mb) for v in b)
        if best is None or cost < best[0]:
            best = (cost, rows[i]["t_hog"], ma, mb)
    if best and abs(best[3] - best[2]) >= STEP_S:
        return best[1], best[2], best[3]
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pairs", help="the *_pairs.json split_coverage wrote")
    ap.add_argument("--json", help="also write the verdicts here")
    args = ap.parse_args()

    rows = json.loads(Path(args.pairs).read_text())
    videos = sorted({r["video"] for r in rows})
    out = []
    print(f"{len(rows)} paired crossings over {len(videos)} recordings\n")

    for vid in videos:
        pairs = {}
        for h in ("top", "bottom"):
            sel = sorted([r for r in rows
                          if r["video"] == vid and OTHER[r["house"]] == h],
                         key=lambda r: r["t_hog"])
            if len(sel) < 4:
                pairs[h] = {"n": len(sel), "verdict": "too few crossings"}
                continue
            d = [r["delta"] for r in sel]
            step = find_step(sel)
            med, sd = statistics.median(d), statistics.pstdev(d)
            if step:
                verdict = (f"STEPS at t={step[0]:.0f}s: "
                           f"{step[1]:+.2f} -> {step[2]:+.2f}")
            elif abs(med) <= IN_SYNC_S:
                verdict = "in sync"
            else:
                verdict = f"OFFSET {med:+.2f}s, steady"
            pairs[h] = {"n": len(sel), "median": round(med, 3),
                        "sd": round(sd, 3), "verdict": verdict}
        worst = max((p.get("median", 0) or 0 for p in pairs.values()), key=abs)
        clean = all(p.get("verdict") == "in sync" for p in pairs.values())
        out.append({"video": vid, "pairs": pairs, "usable": clean})
        print(f"{vid}  {'OK' if clean else 'SUSPECT'}")
        for h, p in pairs.items():
            if "median" not in p:
                print(f"    side {CAM[h]:5s} vs {h:6s} panel: {p['verdict']}")
                continue
            print(f"    side {CAM[h]:5s} vs {h:6s} panel: n={p['n']:3d} "
                  f"median {p['median']:+.3f} sd {p['sd']:.3f}  -- {p['verdict']}")

    ok = [o for o in out if o["usable"]]
    print(f"\n{len(ok)}/{len(out)} recordings have both pairs in sync "
          f"within {IN_SYNC_S}s")
    if len(ok) < len(out):
        print("  suspect: " + ", ".join(o["video"] for o in out if not o["usable"]))
        print("\n  A split on a suspect recording is wrong by the ARRIVING")
        print("  panel's offset, which this cannot measure -- the cross-check")
        print("  sees the throwing panel. Treat their splits as unverified.")
    if args.json:
        Path(args.json).write_text(json.dumps(out, indent=1))
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
