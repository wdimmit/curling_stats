"""Re-order a wave that is already being labelled: the frames already done
first, as they were, then the rest mixed across games (wave.interleave).

    ./.venv/bin/python scripts/spin/reorder.py ~/curling-work/spin/wave1

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
    args = ap.parse_args()
    d = Path(args.directory).expanduser()
    items = json.loads((d / "items.json").read_text())
    scope = page_scope(d)
    done = done_stems(d)
    before = [it["stem"] for it in items if it["stem"] in done]
    ordered = wave.interleave(items, first=before, seed=args.seed)
    assert sorted(it["stem"] for it in ordered) == sorted(it["stem"] for it in items)
    (d / "items.json.bak").write_text((d / "items.json").read_text())
    (d / "items.json").write_text(json.dumps(ordered, indent=1) + "\n")
    page.render(d, ordered, scope)
    print(f"{len(ordered)} frames, {len(before)} done kept first; scope {scope!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
