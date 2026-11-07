"""Every rock's clip for a static demo, cut as the clips worker cuts them.

    python cut_clips.py TIMELINE.json VIDEO OUT_DIR

The rocks are the timeline's (service/gameclips.rock_spec), the cameras its
calibration (gameclips.clip_calibration); each clip is burned in
(game/clipburn) and lands at OUT_DIR/<rock name>.mp4, the name the viewer asks
by (core/shotclip.shotClipName). OUT_DIR/clips.json maps each name that was
cut to its file, for the demo's timeline (``chart.clips``). A clip already in
OUT_DIR is kept, so a second run only cuts what is missing.
"""

import argparse
import json
import logging
import shutil
from pathlib import Path

from curling_score.game import clips as game_clips
from curling_score.service import gameclips


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("timeline", type=Path)
    ap.add_argument("video", type=Path)
    ap.add_argument("out", type=Path)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    doc = json.loads(a.timeline.read_text())
    cal = gameclips.clip_calibration(doc.get("calibration"))
    sideviews = game_clips.sideviews_from_calibration(cal)
    panels = game_clips.panels_from_calibration(cal)
    a.out.mkdir(parents=True, exist_ok=True)
    rocks = [gameclips.rock_spec(g, e, s) for g in doc["games"] for e in g["ends"]
             for s in e.get("shots") or [] if not s.get("missing")]
    todo = [r for r in rocks if not (a.out / f"{r['name']}.mp4").is_file()]
    print(f"{len(rocks)} rocks, {len(todo)} to cut; cameras {sorted(sideviews)} panels {sorted(panels)}",
          flush=True)

    def deliver(name, path):
        shutil.copy(path, a.out / f"{name}.mp4")
        return True

    results = game_clips.clip_rocks(todo, a.video, sideviews, a.out / "work", deliver, panels=panels,
                                    on_rock=lambda n, r: print(n, r, flush=True))
    shutil.rmtree(a.out / "work", ignore_errors=True)
    made = {r["name"]: f"{r['name']}.mp4" for r in rocks if (a.out / f"{r['name']}.mp4").is_file()}
    (a.out / "clips.json").write_text(json.dumps(made, indent=1))
    skipped = {n: r.get("reason") for n, r in results.items() if not r.get("clip")}
    print(f"{len(made)} of {len(rocks)} rocks have a clip; not cut: {skipped}", flush=True)


if __name__ == "__main__":
    main()
