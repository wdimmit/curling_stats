"""Merge per-game harvests into one wave: one page, one manifest.

`harvest.py` cuts one timeline at a time and overwrites its --out, so a wave
drawn from several games is harvested into one directory each and merged here.
Stems carry the video id, view and time, so they cannot collide across games.

    ./.venv/bin/python scripts/broom/merge_waves.py --out ~/curling-work/broom/wave4/all \\
        --manifest datasets/broom/manifest-wave4.json --scope broom:wave4 \\
        --title "Broom heads -- 2026-09-28 misses" ~/curling-work/broom/wave4/waves/s_*/

Each input directory holds images/ and items.json; its manifest is the
`<dir>.manifest.json` beside it, as harvest_one.sh writes them.
"""
import argparse
import json
import shutil
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--scope", required=True)
    ap.add_argument("--title", default="Broom heads")
    ap.add_argument("waves", nargs="+")
    args = ap.parse_args()

    from curling_score.train import boxedit

    out = Path(args.out).expanduser()
    (out / "images").mkdir(parents=True, exist_ok=True)
    items, manifest, seen = [], [], set()
    proposed = False
    for w in args.waves:
        w = Path(w).expanduser()
        rows = json.loads(w.with_name(w.name + ".manifest.json").read_text())
        for it in json.loads((w / "items.json").read_text()):
            if it["stem"] in seen:
                raise SystemExit(f"stem {it['stem']} is in two waves")
            seen.add(it["stem"])
            shutil.copy(w / it["image"], out / "images" / f"{it['stem']}.jpg")
            items.append({**it, "image": f"images/{it['stem']}.jpg"})
        manifest.extend(rows)
        proposed = proposed or any(r.get("proposed") for r in rows)

    order = {m["stem"]: (m["video_id"], m["t_abs"]) for m in manifest}
    items.sort(key=lambda it: order[it["stem"]])
    manifest.sort(key=lambda m: (m["video_id"], m["t_abs"]))
    (out / "items.json").write_text(json.dumps(items))
    Path(args.manifest).expanduser().write_text(json.dumps(manifest, indent=1) + "\n")
    page = boxedit.render(items, out, scope=args.scope, kind="broom",
                          title=args.title, proposals=proposed)
    print(f"{len(items)} frames from {len(args.waves)} games; page {page}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
