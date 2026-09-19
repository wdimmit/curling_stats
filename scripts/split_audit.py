#!/usr/bin/env python
"""Why each rock in an end has, or has not, a hog-to-hog split.

``game/split.py`` needs three things and publishes nothing without all of
them: a release tracked out past the throwing end's hog line, an arrival
picked up before the playing end's hog line, and the two crossings agreeing
that the stone did not speed up in between. The served timeline shows only
the answer, and for the release side not even the track -- so a rock with no
split is unexplained from the outside.

This replays an end exactly as ``analyze`` does (cache hit, no GPU pass) and
prints, for every shot the rules kept, which of the three it failed and by how
much. Written for the split audit of ``AEqLTgM25Tc``; it is the same shape as
``scripts/replay_end.py``, which explains a missing *shot* rather than a
missing *timing*.

    python scripts/split_audit.py timeline.json --cache-root ~/.cache/curling_replay
"""

import argparse
import json
import pickle
from pathlib import Path

from curling_score import analyze as A, weights as weights_mod
from curling_score.detect import release, sequence, yolo
from curling_score.game import (fit, hogtime, secondpass, segment,
                                shots as shots_mod, split)
from curling_score.ingest import proxy


def setups_for(vid, root):
    return pickle.loads((Path(root) / f"setups-{vid}.pkl").read_bytes())


def _bracket_gap(track, y_line):
    """Seconds between the two samples the crossing is interpolated across."""
    pts = [(float(a), float(c)) for a, _b, c in track or ()]
    for (t0, y0), (t1, y1) in zip(pts, pts[1:]):
        if (y0 - y_line) * (y1 - y_line) <= 0 and y0 != y1:
            return round(t1 - t0, 2)
    return None


def audit_end(doc, e, root, detector, setups, panels):
    strip = proxy.strip_rect(panels.top, panels.bottom)
    read_path = proxy.proxy_path(doc["source"]["video_id"], strip, root)
    read_setups = A._proxy_setups(setups, strip)
    setup = read_setups[e["house"]]
    far = read_setups[A.OTHER_HOUSE[e["house"]]]

    game = doc["games"][0]
    earlier = [x for x in game["ends"] if x["number"] < e["number"]]
    prev_close = None
    if earlier:
        prev = max(earlier, key=lambda x: x["end_s"])
        rests = [s["t_rest_s"] for s in prev["shots"] if s.get("t_rest_s") is not None]
        prev_close = min(prev["end_s"], max(rests)) if rests else prev["end_s"]
    from_s = A.run_up_from(prev_close, e["start_s"])

    end = segment.EndSegment(number=e["number"], house=e["house"],
                             start_s=e["start_s"], end_s=e["end_s"])
    seq = list(sequence.detect_end(read_path, setup, end, A.SHOT_FPS, detector,
                                   from_s=from_s))
    ds = [d for d in A.delivery.find_deliveries(
        seq, view_x_limit_m=setup.view_x_limit_m, view_y_min_m=setup.view_y_min_m)
        if d.t_enter >= from_s]
    rec = secondpass.search(seq, secondpass.gaps_to_search(ds, end.start_s, end.end_s), ds)
    if rec:
        ds = sorted(ds + rec, key=lambda d: d.t_enter)
    far_seq = list(sequence.detect_span(read_path, far, from_s, end.end_s,
                                        release.RELEASE_FPS, detector))
    releases, thrown_by, unaccounted = release.find_and_pair(
        far_seq, far.view_y_min_m, ds, seq, since=from_s,
        view_x_limit_m=far.view_x_limit_m)
    if unaccounted:
        ds = sorted(ds + unaccounted, key=lambda d: d.t_enter)
    kept = fit.fit_end(ds)
    shots = shots_mod.from_deliveries(kept, seq, thrown_by={id(d): r for r, d in thrown_by.items()})

    out = []
    for s in shots:
        rel, dv = getattr(s, "release", None), getattr(s, "delivery", None)
        rt = list(getattr(rel, "track", ()) or ())
        dt = list(getattr(dv, "track", ()) or ())
        rec = {
            "shot": s.number, "color": s.color,
            "t_release_s": None if rel is None else round(float(rel.t), 1),
            "release_speed_m_s": None if rel is None else round(float(rel.speed_m_s), 2),
            # How far up the throwing panel the release was followed, against
            # the tripwire it has to reach.
            "release_y_exit_m": None if rel is None else round(float(rel.y_exit_m), 3),
            "release_n": len(rt),
            "release_crossed": None if rel is None else split.hog_crossing(rt) is not None,
            "t_enter_s": None if dv is None else round(float(dv.t_enter), 1),
            "arrival_y0_m": None if not dt else round(float(dt[0][2]), 3),
            "arrival_n": len(dt),
            "arrival_crossed": split.crossing_time(dt, split.HOG_APPARENT_Y_M) is not None,
            "reason": None if dv is None else dv.reason,
            "near_speed": None, "far_speed": None, "split_s": None,
            "t_start_s": None, "t_end_s": None,
            "release_y0_m": None if not rt else round(float(rt[0][2]), 3),
            "release_dur_s": None if len(rt) < 2 else round(float(rt[-1][0] - rt[0][0]), 1),
            "arrival_y_last_m": None if not dt else round(float(dt[-1][2]), 3),
        }
        ts = split.hog_crossing(rt)
        te = split.crossing_time(dt, split.HOG_APPARENT_Y_M)
        rec["t_start_s"] = None if ts is None else round(float(ts), 2)
        rec["t_end_s"] = None if te is None else round(float(te), 2)
        # ``crossing_time`` interpolates between the two samples either side of
        # the line with no cap on how far apart they are. Record that spacing:
        # a crossing bracketed by a multi-second hole is a straight line drawn
        # through a hole, not a timing.
        rec["near_gap_s"] = _bracket_gap(rt, split.HOG_APPARENT_Y_M)
        rec["far_gap_s"] = _bracket_gap(dt, split.HOG_APPARENT_Y_M)
        if rel is not None:
            rec["near_speed"] = split.speed_at_line(rt)
        rec["far_speed"] = split.speed_at_line(dt)
        for k in ("near_speed", "far_speed"):
            if rec[k] is not None:
                rec[k] = round(float(rec[k]), 3)
        sp = split.long_split(rel, dv, t_hog=hogtime.crossing(s),
                              v_hog=hogtime.speed_at_hog(s))
        if sp:
            rec["split_s"] = round(sp.seconds, 2)
        out.append(rec)
    return {"end": e["number"], "house": e["house"],
            "releases_seen": len(releases), "shots": out,
            "all_releases": [
                {"color": r.color, "t": round(r.t, 1),
                 "y_exit_m": round(float(r.y_exit_m), 3),
                 "speed": round(float(r.speed_m_s), 2),
                 "crossed": split.hog_crossing(list(r.track)) is not None,
                 "paired": r in thrown_by}
                for r in releases]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("timeline")
    ap.add_argument("--cache-root", required=True)
    ap.add_argument("--ends", type=int, nargs="*")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    doc = json.loads(Path(args.timeline).read_text())
    root = Path(args.cache_root)
    setups, panels = setups_for(doc["source"]["video_id"], root)
    detector = yolo.YoloDetector(weights_mod.default_path(), conf=0.30, device=None, imgsz=448)
    detector.model.overrides["half"] = True

    res = []
    for e in doc["games"][0]["ends"]:
        if args.ends and e["number"] not in args.ends:
            continue
        print(f"-- end {e['number']} ({e['house']})", flush=True)
        res.append(audit_end(doc, e, root, detector, setups, panels))
        Path(args.out).write_text(json.dumps(res, indent=1))
    print("wrote", args.out)


if __name__ == "__main__":
    main()
