#!/usr/bin/env python
"""Side-view windows of a live recording: the keyframe lead against the flat one.

    python scripts/perf/check_ts_lead.py s1.ts --n 200 --seed 1

Decodes random windows (both side views, 30/10/5/4 fps, 0.5-12 s long) twice --
entered `longview._lead` seconds early, and entered `frames.TS_SEEK_LEAD_S`
early as before -- and says how many came back byte for byte the same, and
what each way cost. Exits 1 on any difference.
"""

import argparse
import random
import sys
import time

from curling_score.detect import longview
from curling_score.ingest import frames as F

RECTS = ((0, 0, 810, 1080), (1110, 0, 810, 1080))


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("video")
    p.add_argument("--n", type=int, default=200)
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--from-s", type=float, default=60.0)
    p.add_argument("--to-s", type=float, default=None)
    args = p.parse_args(argv)
    to_s = args.to_s or F.probe(args.video).duration_s - 15.0
    rng = random.Random(args.seed)
    same = 0
    t_new = t_flat = 0.0
    leads = []
    for i in range(args.n):
        t0 = round(rng.uniform(args.from_s, to_s), 3)
        t1 = t0 + rng.choice((0.5, 1.0, 2.5, 5.5, 6.5, 10.0, 12.0))
        fps = rng.choice((30.0, 10.0, 5.0, 4.0))
        rect = rng.choice(RECTS)
        tic = time.perf_counter()
        lead = longview._lead(args.video, t0)
        got, got_t = longview._decode_now(args.video, rect, t0, t1, fps, lead)
        t_new += time.perf_counter() - tic
        tic = time.perf_counter()
        want, want_t = longview._run(
            longview._command(args.video, rect, t0, t1, fps, F.TS_SEEK_LEAD_S), rect, t0, fps)
        t_flat += time.perf_counter() - tic
        leads.append(lead)
        ok = got_t == want_t and got.tobytes() == want.tobytes()
        same += ok
        if not ok:
            print(f"DIFFERS t0={t0} t1={t1} fps={fps} rect={rect} lead={lead}: "
                  f"{len(got)} frames vs {len(want)}", flush=True)
    print(f"{same}/{args.n} windows identical; mean lead {sum(leads) / len(leads):.2f} s; "
          f"keyframe lead {t_new / args.n * 1000:.0f} ms a window, "
          f"flat lead {t_flat / args.n * 1000:.0f} ms")
    return 0 if same == args.n else 1


if __name__ == "__main__":
    sys.exit(main())
