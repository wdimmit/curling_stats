#!/usr/bin/env python3
"""Compare shot lists, end by end, between the published run and two replays.

For each game: the published timeline (the pipeline as hosted), and two
``split_report.py --out`` replays -- ds11a and ds15-all on the current rules.
Rocks are matched by colour and by arrival time (within ``MATCH_S``) or the
time of the release they were paired with (within ``RELEASE_MATCH_S``) --
never by shot number, since one extra or missing rock renumbers every later shot in the
end. Prints per-end counts where any list differs, and every unmatched rock.

    python3 compare_shotlists.py --timelines DIR --out DIR
"""

import argparse
import json
from pathlib import Path

MATCH_S = 3.0
RELEASE_MATCH_S = 0.5


def published(path):
    doc = json.loads(Path(path).read_text())
    return {e["number"]: [(s["t_enter_s"], s["color"], s.get("t_release_s")) for s in e["shots"]
                          if not s.get("missing") and s.get("t_enter_s") is not None]
            for e in doc["games"][0]["ends"]}


def replay(path):
    return {e["end"]: [(s["t_enter"], s["color"], s.get("t_release")) for s in e["shots"]
                       if s.get("t_enter") is not None]
            for e in json.loads(Path(path).read_text())}


def unmatched(a, b):
    """Rocks in ``a`` with no rock of the same colour in ``b``, by arrival or release."""
    def near(u, v, tol):
        return u is not None and v is not None and abs(u - v) <= tol

    def same(r, q):
        # A rock whose arrival was never seen is placed at its release time
        # ("release-add"), so one run's arrival can be the other's release.
        return r[1] == q[1] and (near(r[0], q[0], MATCH_S) or near(r[2], q[2], RELEASE_MATCH_S)
                                 or near(r[0], q[2], RELEASE_MATCH_S)
                                 or near(r[2], q[0], RELEASE_MATCH_S))
    return [r for r in a if not any(same(r, q) for q in b)]


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
                    for t, c, rel in unmatched(xs, ys):
                        rs = "" if rel is None else f"  released {rel:8.1f}"
                        lines.append(f"      {label:22s} {c:6s} arrives {t:8.1f}{rs}")
        print(f"{name}: {len(pub)} ends" + ("" if lines else " -- identical"))
        print("\n".join(lines)) if lines else None
    print(f"\n{totals['ends']} ends; published vs ds11a-new-rules differ in {totals['pub_vs_ds11a']}; "
          f"ds11a vs ds15-all differ in {totals['ds11a_vs_ds15all']}")


if __name__ == "__main__":
    main()
