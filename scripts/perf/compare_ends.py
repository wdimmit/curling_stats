#!/usr/bin/env python
"""Two `time_live_end.py` runs side by side: time per end and per stage, and
whether the built ends are identical.

    python scripts/perf/compare_ends.py base.json new.json

Exits 1 if any built end differs, listing the first differing fields.
"""

import json
import re
import sys

STAGES = re.compile(r"end \d+: stages (.*) s \(total ([\d.]+) s\)")


def stages(log):
    for line in log:
        m = STAGES.search(line)
        if m:
            return {k: float(v) for k, v in
                    (part.rsplit(" ", 1) for part in m.group(1).split(", "))}
    # A version without the stage clock still logs its lines and spin.
    got = {}
    for line in log:
        m = re.search(r": (lines|spin) \d+/\d+ in ([\d.]+) s", line)
        if m:
            got[m.group(1)] = float(m.group(2))
    return got


def diffs(a, b, path="", limit=20):
    out = []
    if type(a) is not type(b):
        return [f"{path}: {a!r} != {b!r}"]
    if isinstance(a, dict):
        for k in sorted(set(a) | set(b), key=str):
            if k not in a or k not in b:
                out.append(f"{path}.{k}: only in {'new' if k in b else 'base'}")
            else:
                out += diffs(a[k], b[k], f"{path}.{k}", limit)
            if len(out) >= limit:
                break
    elif isinstance(a, list):
        if len(a) != len(b):
            out.append(f"{path}: {len(a)} items != {len(b)}")
        for i, (x, y) in enumerate(zip(a, b)):
            out += diffs(x, y, f"{path}[{i}]", limit)
            if len(out) >= limit:
                break
    elif a != b:
        out.append(f"{path}: {a!r} != {b!r}")
    return out[:limit]


def main(argv) -> int:
    base, new = (json.load(open(p)) for p in argv[1:3])
    same = True
    for a, b in zip(base["ends"], new["ends"]):
        sa, sb = stages(a["log"]), stages(b["log"])
        print(f"end {a['number']}: {a['wall_s']:.1f} s -> {b['wall_s']:.1f} s "
              f"({b['wall_s'] - a['wall_s']:+.1f})")
        for name in list(sa) + [n for n in sb if n not in sa]:
            x, y = sa.get(name), sb.get(name)
            fx = f"{x:6.1f}" if x is not None else "     -"
            fy = f"{y:6.1f}" if y is not None else "     -"
            print(f"   {name:7s} {fx} -> {fy}")
        d = diffs(a["built"], b["built"], f"end{a['number']}")
        if d:
            same = False
            print("   BUILT DIFFERS:")
            for line in d:
                print(f"     {line}")
        else:
            print("   built: identical")
    return 0 if same else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
