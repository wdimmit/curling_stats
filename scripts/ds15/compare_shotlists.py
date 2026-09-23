#!/usr/bin/env python3
"""Compare shot lists, end by end, between the published run and two replays.

For each game: the published timeline (the pipeline as hosted), and two
``split_report.py --out`` replays -- ds11a and ds15-all on the current rules.
Rocks are matched by arrival time (within ``MATCH_S``) and colour, never by
shot number, since one extra or missing rock renumbers every later shot in the
end. Prints per-end counts where any list differs, and every unmatched rock.

    python3 compare_shotlists.py --timelines DIR --out DIR
"""

import argparse
import json
from pathlib import Path

MATCH_S = 2.0


def published(path):
    doc = json.loads(Path(path).read_text())
    return {e["number"]: [(s["t_enter_s"], s["color"]) for s in e["shots"]
                          if not s.get("missing") and s.get("t_enter_s") is not None]
            for e in doc["games"][0]["ends"]}


def replay(path):
    return {e["end"]: [(s["t_enter"], s["color"]) for s in e["shots"] if s.get("t_enter") is not None]
            for e in json.loads(Path(path).read_text())}


def unmatched(a, b):
    """Rocks in ``a`` with no rock of the same colour in ``b`` within MATCH_S."""
    return [(t, c) for t, c in a if not any(c == c2 and abs(t - t2) <= MATCH_S for t2, c2 in b)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--timelines", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    totals = {"ends": 0, "pub_vs_ds11a": 0, "ds11a_vs_ds15all": 0}
    for tl in sorted(a.timelines.glob("*.json")):
        name = tl.stem
        fa, fb = a.out / f"{name}-ds11a.json", a.out / f"{name}-ds15all.json"
        if not (fa.exists() and fb.exists()):
            print(f"{name}: replays missing"); continue
        pub, ra, rb = published(tl), replay(fa), replay(fb)
        lines = []
        for end in sorted(set(pub) | set(ra) | set(rb)):
            p, x, y = pub.get(end, []), ra.get(end, []), rb.get(end, [])
            totals["ends"] += 1
            d1 = unmatched(p, x) + unmatched(x, p)
            d2 = unmatched(x, y) + unmatched(y, x)
            totals["pub_vs_ds11a"] += bool(d1)
            totals["ds11a_vs_ds15all"] += bool(d2)
            if d1 or d2:
                lines.append(f"  end {end}: published {len(p)}, ds11a {len(x)}, ds15all {len(y)}")
                for label, xs, ys in (("published only", p, x), ("ds11a(new rules) only", x, p),
                                      ("ds11a only", x, y), ("ds15all only", y, x)):
                    for t, c in unmatched(xs, ys):
                        lines.append(f"      {label:22s} {c:6s} arrives {t:8.1f}")
        print(f"{name}: {len(pub)} ends" + ("" if lines else " -- identical"))
        print("\n".join(lines)) if lines else None
    print(f"\n{totals['ends']} ends; published vs ds11a-new-rules differ in {totals['pub_vs_ds11a']}; "
          f"ds11a vs ds15-all differ in {totals['ds11a_vs_ds15all']}")


if __name__ == "__main__":
    main()
