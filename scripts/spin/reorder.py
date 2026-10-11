"""Re-order a wave that is already being labelled: the frames already done
first, as they were, then the rest mixed across games (wave.interleave).

    ./.venv/bin/python scripts/spin/reorder.py ~/curling-work/spin/wave1

``--colour-first yellow`` puts that colour's frames ahead of the other's, and
``--early-first`` puts frames inside the early-rate window (before EARLY_MAX_M
past the tee: rate.py measures the spin as thrown from 8 m, about a second past
the hog line, over the next turn or so) ahead of the near ones; each group is
still mixed across games.

"Done" is every frame with points or a flag in any saved sitting
(WAVE/edits/*.json). The page keeps its own copy of the work per frame, under
its scope, so this keeps the scope the page already has: a reload then shows the
new order with nothing lost. The old items.json is kept beside it.
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import page  # noqa: E402
import wave  # noqa: E402


EARLY_MAX_M = 20.0


def tiered(items, first, seed, colour_first=None, early_first=False):
    """Done frames first, then the rest in tiers -- the preferred colour before
    the other, and within each the early-window frames before the near ones --
    each tier mixed across games by wave.interleave."""
    done = [it for it in items if it["stem"] in set(first)]
    rest = [it for it in items if it["stem"] not in set(first)]

    def tier(it):
        c = 0 if colour_first is None or it["color"] == colour_first else 1
        e = 0 if not early_first or it["y_m"] < EARLY_MAX_M else 1
        return (c, e)

    out = list(done)
    for key in sorted({tier(it) for it in rest}):
        out += wave.interleave([it for it in rest if tier(it) == key], seed=seed)
    return out


def done_stems(directory) -> set:
    done = set()
    for f in sorted(Path(directory, "edits").glob("*.json")):
        d = json.loads(f.read_text())
        done |= set(d.get("points") or {}) | set(d.get("flags") or {})
    return done


def page_scope(directory) -> str:
    m = re.search(r"^const SCOPE = (\".*\");$", Path(directory, "index.html").read_text(), re.M)
    if not m:
        raise SystemExit(f"no SCOPE in {directory}/index.html")
    return json.loads(m.group(1))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("directory")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--colour-first", choices=("red", "yellow"))
    ap.add_argument("--early-first", action="store_true")
    args = ap.parse_args()
    d = Path(args.directory).expanduser()
    items = json.loads((d / "items.json").read_text())
    scope = page_scope(d)
    done = done_stems(d)
    before = [it["stem"] for it in items if it["stem"] in done]
    ordered = tiered(items, before, args.seed, args.colour_first, args.early_first)
    assert sorted(it["stem"] for it in ordered) == sorted(it["stem"] for it in items)
    (d / "items.json.bak").write_text((d / "items.json").read_text())
    (d / "items.json").write_text(json.dumps(ordered, indent=1) + "\n")
    page.render(d, ordered, scope)
    print(f"{len(ordered)} frames, {len(before)} done kept first; scope {scope!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
