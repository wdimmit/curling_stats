#!/usr/bin/env python
"""The nightly review over downloaded timelines, to tune what it flags.

    python scripts/review.py DIR [--baseline-from DIR2] [--any]

DIR holds served timelines, one game each -- /g/<source_id>/timeline.json saved
as <source_id>.json. Prints each game the nightly review would flag, how many
games each check fired on, and the coverage thresholds it judged them by.
The baseline is DIR's own ends unless --baseline-from names another folder;
--any also counts games with any finding at all, flagged or not.
"""

import argparse
import collections
import glob
import json
import os
import sys

from curling_score import autoreview


def load(folder):
    """(name, document, game) for every game of every timeline in ``folder``."""
    for path in sorted(glob.glob(os.path.join(folder, "*.json"))):
        name = os.path.basename(path)[:-5]
        try:
            with open(path) as fh:
                doc = json.load(fh)
            games = doc["games"]
            if not isinstance(games, list):
                raise TypeError("games is not a list")
        except (OSError, ValueError, KeyError, TypeError) as err:
            print(f"skipped {name}.json: {err}", file=sys.stderr)
            continue
        for game in games:
            yield name, doc, game


def baseline_from(folder) -> autoreview.Baseline:
    return autoreview.Baseline.from_ends(
        (autoreview.game_format(g), autoreview.end_metrics(e))
        for _n, _d, g in load(folder) for e in g.get("ends", []))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("dir")
    ap.add_argument("--baseline-from")
    ap.add_argument("--any", action="store_true")
    args = ap.parse_args(argv)
    base = baseline_from(args.baseline_from or args.dir)
    print("thresholds:", ", ".join(
        f"{fmt} {k} {v:.2f}" for (fmt, k), v in sorted(base.thresholds.items()))
        or f"none -- fewer than {autoreview.BASELINE_MIN_ENDS} ends a format, "
           f"so the fixed floor ({autoreview.COVERAGE_FLOOR:.0%})")
    print()
    counts, total, flagged, anything = collections.Counter(), 0, 0, 0
    for name, doc, game in load(args.dir):
        got = autoreview.review_game(game, base)
        total += 1
        anything += bool(got.findings)
        counts.update({f.check for f in got.findings})
        if got.raise_flag:
            flagged += 1
            src = doc.get("source", {})
            print(f"{name}  {src.get('video_id')} S{src.get('sheet')}  "
                  f"{autoreview.summary(got)}")
    print(f"\n{flagged}/{total} games flagged"
          + (f"; {anything} with any finding" if args.any else ""))
    for check, n in counts.most_common():
        print(f"  {check:20} {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
