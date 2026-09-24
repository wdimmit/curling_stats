"""Restate a labelling session's boxes on a taller crop of the same frames.

Wave 1 was labelled on crops that stopped 3 m in front of the tee; the crop now
runs past the hog line (a skip calling a guard holds the pad in front of the
house). The top row is unchanged, so every box keeps its pixels: only its
normalised y and height rescale, by old height over new. The original session
file stays as it was; this writes a new one beside it.

    ./.venv/bin/python scripts/broom/recrop_edits.py \\
        --edits datasets/broom/edits/broom-wave1-20260923-202323.json \\
        --old datasets/broom/manifest-wave1.json \\
        --new datasets/broom/manifest-wave1b.json \\
        --scope broom:wave1b \\
        --out datasets/broom/edits/broom-wave1b-from-wave1-20260923-202323.json
"""
import argparse
import json
import sys
from pathlib import Path


def restate(edits: dict, old_rows: dict, new_rows: dict, scope: str) -> dict:
    """``edits`` with every box moved from its old crop onto its new one.

    ``reviewed`` is carried exactly: restating geometry says nothing new about
    which frames a person looked at.
    """
    boxes = {}
    for stem, rows in edits.get("boxes", {}).items():
        old, new = old_rows[stem], new_rows[stem]
        if old["crop_top"] != new["crop_top"] or old["width"] != new["width"]:
            raise ValueError(f"{stem}: the crop's top or width moved, so its "
                             f"boxes cannot be restated by scaling")
        k = old["height"] / new["height"]
        boxes[stem] = [[r[0], r[1], round(r[2] * k, 6), r[3], round(r[4] * k, 6)]
                       for r in rows]
    return {"scope": scope, "boxes": boxes,
            "reviewed": list(edits.get("reviewed", []))}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--edits", required=True)
    ap.add_argument("--old", required=True)
    ap.add_argument("--new", required=True)
    ap.add_argument("--scope", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    load = lambda p: json.loads(Path(p).read_text())
    by_stem = lambda rows: {r["stem"]: r for r in rows}
    out = restate(load(args.edits), by_stem(load(args.old)), by_stem(load(args.new)),
                  args.scope)
    out["restated_from"] = Path(args.edits).name
    Path(args.out).write_text(json.dumps(out, indent=1) + "\n")
    n = sum(len(v) for v in out["boxes"].values())
    print(f"{n} boxes on {len(out['boxes'])} frames -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
