"""Are two timelines the same, apart from what changes on every run?

    python scripts/doubles/compare_docs.py a.json b.json

Prints the first few differing paths and exits 1 if there are any.
"""

import json
import sys

VOLATILE = (("schema_version",), ("processing_version",), ("source", "analysed_at"))


def strip(doc):
    for path in VOLATILE:
        d = doc
        for k in path[:-1]:
            d = d.get(k, {})
        d.pop(path[-1], None)
    return doc


def diff(a, b, path="", out=None, limit=20):
    out = [] if out is None else out
    if len(out) >= limit:
        return out
    if type(a) is not type(b):
        out.append(f"{path}: {a!r} != {b!r}")
    elif isinstance(a, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                out.append(f"{path}.{k}: only in {'b' if k not in a else 'a'}")
            else:
                diff(a[k], b[k], f"{path}.{k}", out, limit)
    elif isinstance(a, list):
        if len(a) != len(b):
            out.append(f"{path}: {len(a)} items != {len(b)}")
        for i, (x, y) in enumerate(zip(a, b)):
            diff(x, y, f"{path}[{i}]", out, limit)
    elif a != b:
        out.append(f"{path}: {a!r} != {b!r}")
    return out


def main(argv):
    a, b = (strip(json.load(open(p))) for p in argv[1:3])
    found = diff(a, b)
    for line in found:
        print(line)
    print("identical" if not found else f"{len(found)}+ differences")
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
