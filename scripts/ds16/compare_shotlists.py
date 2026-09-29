#!/usr/bin/env python3
"""Compare two replays of the 16-game harness, end by end: ds15a and a ds16 model.

The rock matching is ds15's (`scripts/ds15/compare_shotlists.py`): by colour,
and by arrival time (within MATCH_S) or the time of the release a rock was
paired with (within RELEASE_MATCH_S) -- never by shot number, since one extra
or missing rock renumbers every later shot in the end. Prints each end where
the lists differ and every rock only one of them holds.

    python3 compare_shotlists.py --timelines DIR --base DIR --new DIR
"""

import argparse
import json
from pathlib import Path

MATCH_S = 3.0
RELEASE_MATCH_S = 0.5


def replay(path):
    return {e["end"]: [(s["t_enter"], s["color"], s.get("t_release")) for s in e["shots"]
                       if s.get("t_enter") is not None]
            for e in json.loads(Path(path).read_text())}


def unmatched(a, b):
    """Rocks in ``a`` with no rock of the same colour in ``b``, by arrival or release."""
    def near(u, v, tol):
        return u is not None and v is not None and abs(u - v) <= tol

    def same(r, q):
        return r[1] == q[1] and (near(r[0], q[0], MATCH_S) or near(r[2], q[2], RELEASE_MATCH_S)
                                 or near(r[0], q[2], RELEASE_MATCH_S)
                                 or near(r[2], q[0], RELEASE_MATCH_S))
    return [r for r in a if not any(same(r, q) for q in b)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--timelines", type=Path, required=True)
    ap.add_argument("--base", type=Path, required=True)
    ap.add_argument("--new", type=Path, required=True)
    a = ap.parse_args()
    ends = differ = rocks_base = rocks_new = 0
    for tl in sorted(a.timelines.glob("*.json")):
        name = tl.stem
        fb, fn = a.base / f"{name}.json", a.new / f"{name}.json"
        if not (fb.exists() and fn.exists()):
            print(f"{name}: replay missing")
            continue
        rb, rn = replay(fb), replay(fn)
        lines = []
        for end in sorted(set(rb) | set(rn)):
            x, y = rb.get(end, []), rn.get(end, [])
            ends += 1
            rocks_base += len(x)
            rocks_new += len(y)
            only_b, only_n = unmatched(x, y), unmatched(y, x)
            if only_b or only_n:
                differ += 1
                lines.append(f"  end {end}: ds15a {len(x)}, ds16 {len(y)}")
                for label, rs in (("ds15a only", only_b), ("ds16 only", only_n)):
                    for t, c, rel in rs:
                        r = "" if rel is None else f"  released {rel:8.1f}"
                        lines.append(f"      {label:10s} {c:6s} arrives {t:8.1f}{r}")
        print(f"{name}: {len(rb)} ends" + ("" if lines else " -- identical"))
        if lines:
            print("\n".join(lines))
    print(f"\n{ends} ends, {rocks_base} rocks on ds15a and {rocks_new} on ds16; "
          f"the lists differ in {differ}")


if __name__ == "__main__":
    main()
