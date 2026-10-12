#!/usr/bin/env python
"""Build ends of a recording exactly as the live lane does, and time them.

    python scripts/perf/time_live_end.py s1.ts --calib-until 1805 \\
        --calib s1-calib.pkl --prev 5154.32 \\
        --end bottom:7:5250:6140 --end top:8:6145:7335 --out base.json

One-pass detection on the file itself, no detection cache, the models the lane
loads (`live.lane.video_sessions`) and the calibration a live session would
hold from ``[0, CALIB_UNTIL]``. Ends are chained the way a session chains
them: each one's run-up starts where the one before it closed.

``--calib`` keeps the calibration in a pickle, so two code versions compared
against each other build from the same one. ``--profile`` writes a cProfile
of the first end. The output holds, per end, its wall time, every progress
line it logged (``end N: stages ...`` among them) and the built end itself,
for ``compare_ends.py``.
"""

import argparse
import cProfile
import json
import pickle
import sys
import time
from pathlib import Path
from types import SimpleNamespace

from curling_score import analyze, weights as weights_mod
from curling_score.game import format as format_mod, segment
from curling_score.ingest import frames as F


def calibration(video, until_s, pkl):
    # The pickle is one this script wrote, as replay_end.py keeps its setups.
    if pkl is not None and Path(pkl).exists():
        return pickle.loads(Path(pkl).read_bytes())
    frames = F.spread_keyframes(video, until_s, count=analyze.CALIB_FRAMES)
    got = analyze.calibrate_from(frames, skip_longview=False, progress=print)
    if pkl is not None:
        Path(pkl).write_bytes(pickle.dumps(got))
    return got


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("video")
    p.add_argument("--calib-until", type=float, required=True)
    p.add_argument("--calib", default=None, help="calibration pickle, read or written")
    p.add_argument("--prev", type=float, required=True,
                   help="where the end before the first one closed")
    p.add_argument("--end", action="append", required=True,
                   help="HOUSE:NUMBER:START:END, in order")
    p.add_argument("--out", required=True)
    p.add_argument("--profile", default=None)
    p.add_argument("--imgsz", type=int, default=448)
    args = p.parse_args(argv)

    video = Path(args.video)
    _panels, setups, sideviews = calibration(video, args.calib_until, args.calib)
    detector, broom_model, line_model = analyze.load_models(
        weights_mod.default_path(), args.imgsz, None, skip_longview=False,
        skip_line=False, progress=print)

    said = []

    def progress(msg):
        said.append(msg)
        print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)

    ctx = analyze.EndContext(
        path=video, read_path=video, read_setups=setups, sideviews=sideviews,
        detector=detector, broom_model=broom_model, line_model=line_model,
        fmt=format_mod.FOURS, use_cache=False, one_pass=True, progress=progress)
    ends = []
    for spec in args.end:
        house, number, start, stop = spec.split(":")
        ends.append(segment.EndSegment(number=int(number), house=house,
                                       start_s=float(start), end_s=float(stop)))
    # A placeholder first end, so none of these reaches back across a game.
    first = segment.EndSegment(number=1, house="x", start_s=0.0, end_s=0.0)
    game = SimpleNamespace(index=0, ends=[first, *ends], start_s=0.0,
                           end_s=ends[-1].end_s)

    out, prev = [], args.prev
    for i, end in enumerate(ends):
        said.clear()
        prof = cProfile.Profile() if (args.profile and i == 0) else None
        t0 = time.monotonic()
        if prof is not None:
            prof.enable()
        built, closed = analyze.build_one_end(ctx, game, end, prev, board_score=None)
        if prof is not None:
            prof.disable()
            prof.dump_stats(args.profile)
        wall = time.monotonic() - t0
        print(f"TIMING end {end.number}: build_one_end {wall:.1f} s", flush=True)
        out.append({"house": end.house, "number": end.number, "start_s": end.start_s,
                    "end_s": end.end_s, "prev_s": prev, "closed_s": closed,
                    "wall_s": round(wall, 2), "log": list(said), "built": built})
        prev = closed
    Path(args.out).write_text(json.dumps({"video": str(video), "ends": out},
                                         default=str, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
