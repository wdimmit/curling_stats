#!/usr/bin/env python
"""Replay one end's delivery detection from the caches and show every decision.

The pipeline keeps its detections, so an end can be re-run in seconds without a
GPU pass, and every candidate the tracker offered can be laid next to what the
rules kept. This is how game 3 end 4 of the 5U championship was diagnosed: the
chart said the first rock was a red draw, the charter said it was rock 3, and
the replay showed two guards the tracker had seen and then lost.

    python scripts/replay_end.py out/timeline.json 4
    python scripts/replay_end.py out/timeline.json 4 --gates 2808 2832 red

The caches are wherever ``$CURLING_SCORE_CACHE`` (or ``--cache-root``) points.
To replay a game the hosted worker processed, copy that video's files from the
worker's cache first: ``videos/<id>.mp4``, ``proxies/<id>.*.mp4`` and
``detections/`` (rsync keeps the pinned mtimes the detection keys depend on).
``--gates`` prints the raw detections in a window and walks every track that
starts there through ``find_deliveries``' gates, naming the one that refused it.
"""

import argparse
import json
import pickle
from pathlib import Path

from curling_score import analyze as analyze_mod, weights as weights_mod
from curling_score.detect import delivery as D, release, sequence, yolo
from curling_score.game import (endcheck, fit, profile, secondpass, segment,
                                shots as shots_mod, split, thinking)
from curling_score.geometry import layout
from curling_score.ingest import cache, frames as F, proxy


def setups_for(video, vid, root):
    """Panel calibration, computed once per video and kept beside the caches."""
    pkl = Path(root) / f"setups-{vid}.pkl"
    if pkl.exists():
        return pickle.loads(pkl.read_bytes())
    calib_frames = F.sample_keyframes(video, count=analyze_mod.CALIB_FRAMES,
                                      stride=analyze_mod.CALIB_STRIDE)
    panels = layout.detect_panels(calib_frames)
    setups = profile.calibrate_panels(calib_frames, panels)
    pkl.write_bytes(pickle.dumps((setups, panels)))
    return setups, panels


def show(tag, deliveries, start_s):
    for d in sorted(deliveries, key=lambda d: d.t_enter):
        flag = "" if d.t_enter >= start_s else "  <-- before the run-up"
        print(f"  {tag} {d.color:6s} enter={d.t_enter:7.1f} rest={d.t_rest:7.1f} "
              f"y0={d.entry_y_m:5.2f} -> ({d.rest_x_m:5.2f},{d.rest_y_m:5.2f}) "
              f"travel={d.travel_m:5.2f} {d.reason:12s} rest={d.came_to_rest} "
              f"n={len(d.track)}{flag}")


def gates(frames, lo, hi, color, x_limit):
    """Why each track starting in [lo, hi] was or was not a delivery."""
    print(f"\n== raw detections {lo}-{hi}" + (f" ({color})" if color else ""))
    for t, dets in frames:
        if lo <= t <= hi:
            ds = [d for d in dets if color is None or d.color == color]
            if ds:
                print(f"  {t:7.1f}: " + "  ".join(
                    f"{d.color[0]}({d.x_m:+.2f},{d.y_m:+.2f}) c={d.confidence:.2f}"
                    for d in sorted(ds, key=lambda d: -d.y_m)))
    print(f"\n== tracks starting in the window (built over {lo - 40:.0f}-{hi + 40:.0f})")
    window = [(t, d) for t, d in frames if lo - 40 <= t <= hi + 40]
    for full in D._build_tracks(window):
        if not (lo <= full.ts[0] <= hi) or (color and full.color != color):
            continue
        i0 = D._trim_borrowed_start(frames, full)
        print(f"\n  {full.color} track t={full.ts[0]:.1f}..{full.ts[-1]:.1f} n={len(full.ts)} "
              f"y {full.ys[0]:+.2f}->{full.ys[-1]:+.2f} x {full.xs[0]:+.2f}->{full.xs[-1]:+.2f}"
              f"  borrowed start: {i0} samples")
        pieces = ([("head (the stone that was sitting there)", D._head(full, i0))] if i0 else []) \
            + [("track", D._tail(full, i0))]
        for label, track in pieces:
            print(f"    -- {label}")
            _judge(frames, track, x_limit)


def _judge(frames, track, x_limit):
    if len(track.ts) < 4:
        print("     REFUSED: fewer than 4 samples"); return
    late = False
    if track.ys[0] < D.MIN_ENTRY_Y_M:
        print(f"     entry y {track.ys[0]:.2f} < MIN_ENTRY_Y_M {D.MIN_ENTRY_Y_M}: late-entry path")
        if track.ys[0] < D.LATE_ENTRY_MIN_Y_M:
            print("     REFUSED: below LATE_ENTRY_MIN_Y_M"); return
        if not D._is_new_stone(frames, track, D._rest_index(track) or 0):
            print("     REFUSED: late entry, and not a new stone"); return
        late = True
    rest_i = D._rest_index(track)
    print(f"     rest_index={rest_i}" + (
        f" at t={track.ts[rest_i]:.1f} ({track.xs[rest_i]:+.2f},{track.ys[rest_i]:+.2f})"
        if rest_i is not None else " (never at rest)"))

    def appear(at_i):
        s = D._settled_index(track, at_i)
        print(f"     house-appear? entry_ok={track.ys[0] >= D.MIN_ENTRY_Y_M} "
              f"still_there={D._still_there(frames, track.color, track.xs[s], track.ys[s], track.ts[s])} "
              f"place_was_empty={D._place_was_empty(frames, track.color, track.xs[s], track.ys[s], track.ts[0])} "
              f"appeared_without_replacing={D._appeared_without_replacing(frames, track)}")
        print("     ACCEPTED house-appear" if D._arrived_at_rest(frames, track, at_i)
              else "     REFUSED house-appear")

    if rest_i == 0:
        print("     at rest from the first sighting")
        appear(0); return
    end_i = rest_i if rest_i is not None else len(track.ts) - 1
    travelled = track.ys[0] - track.ys[end_i]
    path = sum(abs(track.ys[k + 1] - track.ys[k]) for k in range(end_i))
    print(f"     travelled={travelled:.2f} path={path:.2f} lateral={abs(track.xs[end_i] - track.xs[0]):.2f}")
    if travelled < D.MIN_TRAVEL_M:
        print(f"     travel < MIN_TRAVEL_M {D.MIN_TRAVEL_M}")
        if rest_i is None:
            print("     REFUSED: never at rest either"); return
        appear(end_i); return
    if path <= 0 or travelled / path < D.MIN_NET_TRAVEL_FRACTION:
        print(f"     REFUSED: net travel fraction {travelled / path if path else 0:.2f} "
              f"< {D.MIN_NET_TRAVEL_FRACTION}"); return
    if abs(track.xs[end_i] - track.xs[0]) > D.MAX_LATERAL_RATIO * travelled:
        print("     REFUSED: too lateral"); return
    settled = rest_i is not None and D._still_there(
        frames, track.color, track.xs[end_i], track.ys[end_i], track.ts[end_i])
    print(f"     still_there at rest={settled}")
    if not settled:
        rest, claim = D._house_change(frames, track, end_i)
        left = D._left_the_view(track, end_i, x_limit)
        print(f"     house_change rest={rest} claim={claim} left_view={left}")
        if rest is None and claim is None and not left:
            print("     REFUSED: no evidence it changed anything"); return
    print("     ACCEPTED", "late-entry" if late else "rest / house change")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("timeline", help="the timeline.json the end came from")
    ap.add_argument("end", type=int, help="end number")
    ap.add_argument("--game", type=int, default=0, help="game index (default 0)")
    ap.add_argument("--cache-root", default=None)
    ap.add_argument("--weights", default=None)
    ap.add_argument("--gates", nargs="+", metavar="ARG",
                    help="LO HI [COLOR]: trace the gates for tracks starting in that window")
    ap.add_argument("--dump", metavar="PKL",
                    help="also pickle the detection sequence here, for offline work")
    args = ap.parse_args()

    doc = json.loads(Path(args.timeline).read_text())
    vid = doc["source"]["video_id"]
    game = next(g for g in doc["games"] if g["index"] == args.game)
    e = next(e for e in game["ends"] if e["number"] == args.end)
    earlier = [x for x in game["ends"] if x["number"] < e["number"]]
    if not earlier:   # the first end: the previous game's last end, if any
        earlier = [x for g in doc["games"] if g["index"] < game["index"] for x in g["ends"]]
    prev_close = None
    if earlier:
        prev = max(earlier, key=lambda x: x["end_s"])
        rests = [s["t_rest_s"] for s in prev["shots"] if s.get("t_rest_s") is not None]
        prev_close = min(prev["end_s"], max(rests)) if rests else prev["end_s"]
    from_s = analyze_mod.run_up_from(prev_close, e["start_s"])
    root = Path(args.cache_root) if args.cache_root else cache.default_root()
    video = root / "videos" / f"{vid}.mp4"
    if not video.is_file():
        raise SystemExit(f"no cached video at {video}")

    setups, panels = setups_for(video, vid, root)
    strip = proxy.strip_rect(panels.top, panels.bottom)
    read_path = proxy.proxy_path(vid, strip, root)
    if not read_path.is_file():
        raise SystemExit(f"no cached proxy at {read_path}")
    setup = analyze_mod._proxy_setups(setups, strip)[e["house"]]
    weights = args.weights or weights_mod.default_path()
    detector = yolo.YoloDetector(weights, conf=0.30, device=None, imgsz=448)
    detector.model.overrides["half"] = True

    end = segment.EndSegment(number=e["number"], house=e["house"],
                             start_s=e["start_s"], end_s=e["end_s"])
    seq = list(sequence.detect_end(read_path, setup, end, analyze_mod.SHOT_FPS, detector,
                                   from_s=from_s))
    frames = [(t, list(d)) for t, d in seq]
    if args.dump:
        pickle.dump(frames, open(args.dump, "wb"))
    print(f"end {end.number} ({end.house}) {end.start_s:.0f}-{end.end_s:.0f}: "
          f"{len(frames)} frames {frames[0][0]:.1f}..{frames[-1][0]:.1f}")

    x_limit = setup.view_x_limit_m
    every = D.find_deliveries(frames, view_x_limit_m=x_limit, view_y_min_m=setup.view_y_min_m)
    print(f"\n== find_deliveries offered {len(every)} (run-up from {from_s:.0f})")
    show("cand", every, from_s)
    ds = [d for d in every if d.t_enter >= from_s]

    gaps = secondpass.gaps_to_search(ds, end.start_s, end.end_s)
    print(f"\n== gaps searched: {[(round(g.start_s, 1), round(g.end_s, 1), g.expected_color) for g in gaps]}")
    rec = secondpass.search(frames, gaps, ds)
    print(f"== recovered {len(rec)}"); show("rec ", rec, end.start_s)
    if rec:
        ds = sorted(ds + rec, key=lambda d: d.t_enter)
    # The thrower's house: every release crosses it on the way out.
    far = analyze_mod._proxy_setups(setups, strip)[analyze_mod.OTHER_HOUSE[e["house"]]]
    releases, matched, settled = release.find_and_pair(
        sequence.detect_span(read_path, far, from_s, end.end_s, release.RELEASE_FPS, detector),
        far.view_y_min_m, ds, frames, since=from_s)
    print(f"\n== releases seen leaving the {analyze_mod.OTHER_HOUSE[e['house']]} house: {len(releases)}")
    for r in releases:
        to = matched.get(r)
        print(f"  {r.color:6s} released {r.t:7.1f} at {r.speed_m_s:.1f} m/s, followed to y={r.y_exit_m:+.2f}"
              + (f" -> arrived {to.t_enter:.1f} ({to.t_enter - r.t:.0f} s later)" if to else "   <-- NO ARRIVAL: hogged?"))
    # ``find_and_pair`` has already settled these against the house, and has
    # dropped the releases explained as a colour misread -- so this is exactly
    # what the pipeline will add, not an approximation of it.
    for d in settled:
        print(f"     -> the house says: {d.reason}" + (f" at ({d.rest_x_m:+.2f},{d.rest_y_m:+.2f})" if d.reason == release.REASON_ADD else ""))
    if settled:
        ds = sorted(ds + settled, key=lambda d: d.t_enter)
    # What the two derived timings make of this end. Both are best-effort by
    # construction, so the counts matter as much as the numbers.
    thrown_by = {id(d): r for r, d in matched.items()}
    built = shots_mod.from_deliveries(fit.fit_end(ds), frames, thrown_by=thrown_by)
    clock = thinking.for_end(built)
    print("\n== timings")
    measured = 0
    for sh, secs in zip(built, clock.per_shot):
        sp = split.long_split(getattr(sh, "release", None),
                              getattr(sh, "delivery", None))
        if sp:
            measured += 1
            split_txt = f"{sp.seconds:6.2f}s (est {sp.extrapolated_m:.1f} m)"
        else:
            split_txt = "     -             "
        clock_txt = "    -" if secs is None else f"{secs:5.1f}s"
        print(f"  {sh.number:2d} {sh.color:6s} split {split_txt}  thinking {clock_txt}")
    print(f"  splits {measured}/{len(built)}; clock read from {clock.measured_shots}"
          f" (+{clock.unmeasured_shots} unmeasured, {clock.anomalies} anomalies)")
    print("  thinking time: "
          + ", ".join(f"{c} {v:.0f}s" for c, v in clock.by_color.items()))

    kept = fit.fit_end(ds)
    print(f"\n== fit_end kept {len(kept)}, dropped {len(ds) - len(kept)}")
    show("DROP", [d for d in ds if d not in kept], end.start_s)
    print("== kept:"); show("keep", kept, end.start_s)
    audit = endcheck.check(ds)
    print("audit", audit.thrown, audit.problems)

    print(f"\n== shots ({'as charted' if len(kept) == 16 else 'with blanks'})")
    for s in shots_mod.from_deliveries(kept, frames):
        print(f"  #{s.number:2d} {s.color:6s} missing={s.missing} known={s.state_known} "
              f"stones={len(s.stones)}" + ("" if s.missing else f" t_rest={s.t_rest_s:.1f}"))

    if args.gates:
        lo, hi = float(args.gates[0]), float(args.gates[1])
        color = args.gates[2] if len(args.gates) > 2 else None
        gates(frames, lo, hi, color, x_limit)


if __name__ == "__main__":
    main()
