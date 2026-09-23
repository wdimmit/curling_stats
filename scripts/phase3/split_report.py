#!/usr/bin/env python
"""Before-and-after long-split report for one replayed game.

Replays every end of a game end-by-end exactly as ``analyze`` does, from the
caches: the same steps as ``scripts/split_audit.py``'s ``audit_end``, plus
``hogtime.time_hog_crossings`` on the side view (built with
``sideview.locate``/``sideview.solve`` from the calibration median, as
``analyze`` does) and ``fartime.time_far_crossings`` on the destination
panel's painted hog line. Each shot's split is then
``split.long_split(dv, t_hog=hogtime.crossing(s), v_hog=hogtime.speed_at_hog(s),
far=fartime.crossing(s))``, exactly as ``timeline.py`` computes it.

Prints, per game: how many rocks were seen, how the destination panel's hog
line was reached (observed at the paint, reached for, or not found at all),
how many rocks got a split, and -- against ``--before``, a JSON file of the
shape this script's own ``--out`` writes -- which rocks (matched by
``(end, shot)``) gained a split, which lost one, and how far a rock's split
moved when it had one both times. Also prints each panel's hog-line status.

This is a report, not a gate: nothing here fails a build or asserts anything.

    CURLING_SCORE_CACHE=<root> PYTHONPATH=src python scripts/phase3/split_report.py \
        <timeline.json> --cache-root <root> --video <root>/videos/<id>.mp4 \
        --before <root>/<id>-splits-branch.json [--out <root>/<id>-splits-phase3.json]
"""

import argparse
import inspect
import json
import statistics
import sys
from pathlib import Path

import numpy as np

from curling_score import analyze as A, weights as weights_mod
from curling_score.detect import release, sequence, yolo
from curling_score.game import (fartime, fit, hogtime, secondpass, segment,
                                shots as shots_mod, split)
from curling_score.geometry import sideview
from curling_score.ingest import frames as F, proxy

# ``replay_end.py`` lives in ``scripts/``, one level up from this file (this
# file is ``scripts/phase3/split_report.py``).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import replay_end


def replay_game(timeline_path, root, video, weights=None):
    """Replay every end of the timeline's first game, as ``analyze`` would.

    Returns ``(video_id, per_end, setups)``. ``per_end`` is a list of
    ``{"end": n, "shots": [...]}`` -- the same shape a ``--before`` file
    has -- and ``setups`` is the raw (non-proxy) panel calibration, read for
    its hog-line status.
    """
    doc = json.loads(timeline_path.read_text())
    vid = doc["source"]["video_id"]
    setups, panels = replay_end.setups_for(video, vid, root)
    strip = proxy.strip_rect(panels.top, panels.bottom)
    rp = proxy.proxy_path(vid, strip, root)
    rs = A._proxy_setups(setups, strip)
    det = yolo.YoloDetector(weights or weights_mod.default_path(), conf=0.30, device=None,
                            imgsz=448)
    det.model.overrides["half"] = True
    bounded = "view_x_limit_m" in inspect.signature(release.find_and_pair).parameters
    calib = F.sample_keyframes(video, count=A.CALIB_FRAMES, stride=A.CALIB_STRIDE)
    h, w = calib[0].shape[:2]
    plate = np.median(np.stack([f.astype("float32") for f in calib]), axis=0)
    views = {n: sideview.solve(plate, r, name=n)
             for n, r in sideview.locate(panels, width=w, height=h).items()}
    print(f"panels: {sorted(setups)} | side views: {sorted(views)}", flush=True)

    game = doc["games"][0]
    per_end = []
    for e in game["ends"]:
        setup, far = rs[e["house"]], rs[A.OTHER_HOUSE[e["house"]]]
        earlier = [x for x in game["ends"] if x["number"] < e["number"]]
        prev_close = None
        if earlier:
            p = max(earlier, key=lambda x: x["end_s"])
            r = [s["t_rest_s"] for s in p["shots"] if s.get("t_rest_s") is not None]
            prev_close = min(p["end_s"], max(r)) if r else p["end_s"]
        from_s = A.run_up_from(prev_close, e["start_s"])
        end = segment.EndSegment(number=e["number"], house=e["house"],
                                 start_s=e["start_s"], end_s=e["end_s"])
        seq = list(sequence.detect_end(rp, setup, end, A.SHOT_FPS, det, from_s=from_s))
        ds = [d for d in A.delivery.find_deliveries(
                  seq, view_x_limit_m=setup.view_x_limit_m, view_y_min_m=setup.view_y_min_m)
              if d.t_enter >= from_s]
        rec = secondpass.search(seq, secondpass.gaps_to_search(ds, end.start_s, end.end_s), ds)
        if rec:
            ds = sorted(ds + rec, key=lambda d: d.t_enter)
        far_seq = list(sequence.detect_span(rp, far, from_s, e["end_s"], release.RELEASE_FPS, det))
        kw = {"view_x_limit_m": far.view_x_limit_m} if bounded else {}
        rels, tb, un = release.find_and_pair(far_seq, far.view_y_min_m, ds, seq, since=from_s, **kw)
        if un:
            ds = sorted(ds + un, key=lambda d: d.t_enter)
        kept = fit.fit_end(ds)
        shots = shots_mod.from_deliveries(kept, seq, thrown_by={id(d): r for r, d in tb.items()})
        hogtime.time_hog_crossings(shots, video, views[hogtime.CAMERA_FOR[A.OTHER_HOUSE[e["house"]]]])
        # ``setup`` is the destination panel, ``far`` the throwing panel --
        # exactly as ``analyze`` names them at this point in the pipeline.
        fartime.time_far_crossings(shots, near_line=far.hog_line, far_line=setup.hog_line)

        rows = []
        for s in shots:
            if getattr(s, "missing", False):
                continue
            dv, rel = getattr(s, "delivery", None), getattr(s, "release", None)
            t_hog = hogtime.crossing(s)
            fc = fartime.crossing(s)
            sp = split.long_split(dv, t_hog=t_hog, v_hog=hogtime.speed_at_hog(s), far=fc)
            why = ("ok" if sp else
                   "no delivery" if dv is None else
                   "no side-view crossing" if t_hog is None else
                   "no far crossing" if fc is None or fc.t is None else
                   "refused by pairing check")
            rows.append({
                "shot": s.number, "color": s.color, "release": rel is not None,
                "t_release": None if rel is None else float(rel.t),
                "t_enter": None if dv is None else float(dv.t_enter),
                "t_hog": t_hog,
                "far_t": None if fc is None else fc.t,
                "far_reach": None if fc is None else fc.reach,
                "split_s": None if not sp else round(sp.seconds, 2),
                "why": why,
            })
        per_end.append({"end": e["number"], "shots": rows})
        print(f"end {e['number']}: {sum(1 for r in rows if r['split_s'] is not None)}/{len(rows)} splits",
              flush=True)
    return vid, per_end, setups


def summarize(per_end):
    """(rocks, observed, reached_for, none, splits) over every non-missing shot."""
    rows = [r for end in per_end for r in end["shots"]]
    observed = sum(1 for r in rows if r["far_t"] is not None and r["far_reach"] == 0.0)
    reached = sum(1 for r in rows if r["far_t"] is not None and r["far_reach"] not in (0.0, None))
    none_far = sum(1 for r in rows if r["far_t"] is None)
    splits = sum(1 for r in rows if r["split_s"] is not None)
    return len(rows), observed, reached, none_far, splits


def _dist(vals):
    if not vals:
        return None
    vals = sorted(vals)
    p90_i = min(len(vals) - 1, int(round(0.9 * (len(vals) - 1))))
    return {"n": len(vals), "median": statistics.median(vals),
            "p90": vals[p90_i], "max": vals[-1]}


def compare(per_end, before):
    """Match rocks by (end, shot) against a ``--before`` file already loaded."""
    before_by = {(e["end"], s["shot"]): s.get("split_s")
                 for e in before for s in e["shots"]}
    now_by = {(e["end"], s["shot"]): s.get("split_s")
              for e in per_end for s in e["shots"]}
    matched = sorted(set(before_by) & set(now_by))
    gained = [k for k in matched if before_by[k] is None and now_by[k] is not None]
    lost = [k for k in matched if before_by[k] is not None and now_by[k] is None]
    both = [abs(now_by[k] - before_by[k]) for k in matched
            if before_by[k] is not None and now_by[k] is not None]
    return {
        "before_total": len(before_by), "before_split": sum(1 for v in before_by.values() if v is not None),
        "now_total": len(now_by), "now_split": sum(1 for v in now_by.values() if v is not None),
        "matched": len(matched), "gained": gained, "lost": lost, "both_dist": _dist(both),
    }


def print_report(video_id, per_end, setups, before):
    rocks, observed, reached, none_far, splits = summarize(per_end)
    print(f"\n== {video_id}")
    print(f"rocks: {rocks}")
    print(f"far crossings: {observed} observed, {reached} reached for, {none_far} none  (of {rocks})")
    print(f"splits: {splits}/{rocks}")
    if before is not None:
        c = compare(per_end, before)
        print(f"against --before: splits then {c['before_split']}/{c['before_total']}, "
              f"now {c['now_split']}/{c['now_total']}  ({c['matched']} rocks matched by (end, shot))")
        print(f"  gained: {len(c['gained'])}  lost: {len(c['lost'])}")
        d = c["both_dist"]
        if d:
            print(f"  |now - then| over {d['n']} rocks split both times: "
                  f"median {d['median']:.3f}s  p90 {d['p90']:.3f}s  max {d['max']:.3f}s")
        else:
            print("  no rocks were split both times")
    print("hog lines:")
    for name, st in sorted(setups.items()):
        line, err = getattr(st, "hog_line", None), getattr(st, "hog_line_error", None)
        if line is not None:
            print(f"  {name}: found  width_px={line.width_px:.1f}  columns={line.columns}")
        else:
            print(f"  {name}: NOT FOUND -- {err}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("timeline", help="the timeline.json the game came from")
    ap.add_argument("--cache-root", required=True)
    ap.add_argument("--video", required=True,
                    help="path to the cached video, e.g. ~/.cache/curling_score/videos/<id>.mp4")
    ap.add_argument("--before", required=True,
                    help="the pre-change per-rock splits JSON to compare against")
    ap.add_argument("--out", default=None,
                    help="write the per-rock JSON here, in the same shape as --before, "
                         "so a future run can compare against it")
    ap.add_argument("--weights", default=None,
                    help="overhead detector weights (default: the pipeline's, ds11a)")
    args = ap.parse_args()

    root, video = Path(args.cache_root), Path(args.video)
    video_id, per_end, setups = replay_game(Path(args.timeline), root, video, args.weights)

    if args.out:
        Path(args.out).write_text(json.dumps(per_end, indent=1))
        print(f"wrote {args.out}", flush=True)

    before = json.loads(Path(args.before).read_text())
    print_report(video_id, per_end, setups, before)


if __name__ == "__main__":
    main()
