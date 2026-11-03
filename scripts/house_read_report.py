#!/usr/bin/env python
"""Replay every end of a game from the caches and record the house each rock left.

For judging a change to how a house is read (``shots.from_deliveries``): run it
once with the old code on ``PYTHONPATH`` and once with the new, then compare
the two ``--out`` files with ``--compare``. Every step before the house read is
``analyze``'s, from the caches, as in ``scripts/replay_end.py``: detection,
the second pass, pairing with releases, ``drop_clearing`` and ``fit_end``.

    CURLING_SCORE_CACHE=<root> PYTHONPATH=src python scripts/house_read_report.py \\
        <timeline.json> --weights <pt> --out old.json [--dump-windows DIR]
    python scripts/house_read_report.py --compare old.json new.json [...] \\
        [--expect proto_old.json proto_new.json]

``--dump-windows`` also pickles, per rock, the frames of the window its house
is read over -- a few hundred kilobytes an end, against megabytes for the whole
sequence -- so a rule for cutting that window can be tried offline.

``--house-weights`` gives the receiving panel's read its own model (default: the
``--weights`` file, one detector for both); the far panel's read keeps
``--weights``. The detector's player boxes go through to ``from_deliveries``,
which puts back a stone lost under a player; each row's ``held`` lists those,
and is empty when ``from_deliveries`` (an older ``PYTHONPATH``) cannot.

``--compare`` also lists the stones the new run has that the old did not
(``recovered``: no same-colour stone of the old row within 0.15 m), as "put
back" when the new row's ``held`` has it, else "seen". ``--expect`` takes
another pair, say a prototype's, and every stone that pair recovered which the
new run's own row for that shot (same video, game, end and shot number) has no
same-colour stone within 0.15 m of -- or has no row, or a missing one -- prints
``STILL LOST`` and the exit code is 1. It is the new run's row that counts, not
this comparison's recovered list: the prototype read with an older model, and a
stone the baseline already sees is not "recovered" here but is not lost.
Rows are matched on video, game, end and shot number; new rows with no old row
are counted and printed, not silently dropped.

A diff that adds a stone of the colour that did not throw is impossible on the
ice: nothing but the thrown rock arrives. It is the house read's own error
signal, and ``--compare`` counts it for the last rock of each end apart from
the rest, because only the last rock is read into the clearing that follows.
"""

import argparse
import inspect
import json
import math
import pickle
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))

from curling_score import analyze as analyze_mod, weights as weights_mod  # noqa: E402
from curling_score.detect import delivery as D, release, sequence, yolo  # noqa: E402
from curling_score.game import fit, secondpass, segment, shots as shots_mod  # noqa: E402
from curling_score.ingest import cache, proxy  # noqa: E402

from replay_end import setups_for  # noqa: E402


# Two stones this near are one stone: a read moves a settled stone by less.
SEEN_M = 0.15


def _stone(s):
    return {"color": s.color, "x": round(s.x_m, 3), "y": round(s.y_m, 3)}


def _prev_close(game, doc, e):
    earlier = [x for x in game["ends"] if x["number"] < e["number"]]
    if not earlier:
        earlier = [x for g in doc["games"] if g["index"] < game["index"] for x in g["ends"]]
    if not earlier:
        return None
    prev = max(earlier, key=lambda x: x["end_s"])
    rests = [s["t_rest_s"] for s in prev["shots"] if s.get("t_rest_s") is not None]
    return min(prev["end_s"], max(rests)) if rests else prev["end_s"]


def _near(a, b):
    return a["color"] == b["color"] and math.hypot(a["x"] - b["x"], a["y"] - b["y"]) <= SEEN_M


def replay(timeline, root, weights, dump=None, house_weights=None):
    doc = json.loads(Path(timeline).read_text())
    vid = doc["source"]["video_id"]
    video = root / "videos" / f"{vid}.mp4"
    setups, panels = setups_for(video, vid, root)
    strip = proxy.strip_rect(panels.top, panels.bottom)
    read_path = proxy.proxy_path(vid, strip, root)
    proxied = analyze_mod._proxy_setups(setups, strip)
    detector = yolo.YoloDetector(weights, conf=0.30, device=None, imgsz=448)
    detector.model.overrides["half"] = True
    house_detector = detector
    if house_weights is not None and Path(house_weights) != Path(weights):
        house_detector = yolo.YoloDetector(house_weights, conf=0.30, device=None, imgsz=448)
        house_detector.model.overrides["half"] = True

    out = []
    with_players = read = 0
    for game in doc["games"]:
        for e in game["ends"]:
            setup = proxied[e["house"]]
            far = proxied[analyze_mod.OTHER_HOUSE[e["house"]]]
            from_s = analyze_mod.run_up_from(_prev_close(game, doc, e), e["start_s"])
            end = segment.EndSegment(number=e["number"], house=e["house"],
                                     start_s=e["start_s"], end_s=e["end_s"])
            # The detector's own frames, players and all: tracking, the second
            # pass and pairing read them as stone lists, as ever.
            seq = list(sequence.detect_end(
                read_path, setup, end, analyze_mod.SHOT_FPS, house_detector, from_s=from_s))
            read += len(seq)
            with_players += sum(bool(getattr(d, "players", ())) for _, d in seq)
            ds = [d for d in D.find_deliveries(seq, view_x_limit_m=setup.view_x_limit_m,
                                               view_y_min_m=setup.view_y_min_m)
                  if d.t_enter >= from_s]
            rec = secondpass.search(seq, secondpass.gaps_to_search(ds, end.start_s, end.end_s), ds)
            if rec:
                ds = sorted(ds + rec, key=lambda d: d.t_enter)
            far_seq = list(sequence.detect_span(read_path, far, from_s, end.end_s,
                                                release.RELEASE_FPS, detector))
            _, thrown_by, unaccounted = release.find_and_pair(
                far_seq, far.view_y_min_m, ds, seq, since=from_s,
                view_x_limit_m=far.view_x_limit_m)
            if unaccounted:
                ds = sorted(ds + unaccounted, key=lambda d: d.t_enter)
            kept = fit.fit_end(fit.drop_clearing(
                ds, seq, fit.released_ids(thrown_by, unaccounted)),
                paired=fit.paired_ids(thrown_by))
            held = []
            extra = {"held": held} if "held" in inspect.signature(
                shots_mod.from_deliveries).parameters else {}
            shots = shots_mod.from_deliveries(
                kept, seq, thrown_by={id(d): r for r, d in thrown_by.items()},
                house_frames=seq, **extra)
            last = max((s.number for s in shots if not s.missing), default=None)
            windows = {}
            for i, dv in enumerate(kept):
                lo = dv.t_rest
                hi = kept[i + 1].t_enter if i + 1 < len(kept) else float("inf")
                windows[round(dv.t_rest, 2)] = (lo, min(lo + shots_mod.SETTLE_WINDOW_S, hi))
            for s in shots:
                row = {"video": vid, "game": game["index"], "end": e["number"],
                       "shot": s.number, "color": s.color, "missing": s.missing,
                       "last": s.number == last,
                       "t_rest": None if s.missing else round(s.t_rest_s, 2),
                       "stones": [_stone(x) for x in s.stones],
                       "house_delta": s.house_delta,
                       "delivered_stone_index": s.delivered_stone_index,
                       "held": [{"color": h["color"], "x": round(h["x"], 3),
                                 "y": round(h["y"], 3)}
                                for h in held if h["shot"] == s.number]}
                out.append(row)
                if dump and not s.missing:
                    lo, hi = windows[round(s.t_rest_s, 2)]
                    row["window"] = [lo, hi]
                    key = f"{vid}-g{game['index']}-e{e['number']}-s{s.number}"
                    with open(Path(dump) / f"{key}.pkl", "wb") as fh:
                        pickle.dump({"row": row, "frames": [
                            (t, d) for t, d in seq if lo <= t <= hi]}, fh)
            print(f"{vid} g{game['index']} e{e['number']}: {len(shots)} shots", flush=True)
    print(f"{vid} frames with players: {with_players} of {read}", flush=True)
    return out


def impossible(row):
    d = row.get("house_delta") or {}
    return any(a["color"] != row["color"] for a in d.get("added", []))


def _load_rows(paths):
    return [r for p in paths for r in json.loads(Path(p).read_text())]


def recovered(old, new):
    """Stones of ``new`` that ``old`` had no same-colour stone within ``SEEN_M``
    of, shot by shot (rows matched on video, game, end and shot number, as
    ``--expect`` does; ``unmatched`` counts the rows that find no partner).
    ``how`` is "put back" when the new row's ``held`` has the stone
    -- same colour within ``SEEN_M`` -- else "seen". Old rows without ``held``
    are fine; only the new row's is read."""
    by_old = {_shot_key(r): r for r in old}
    out = []
    for n in new:
        r = by_old.get(_shot_key(n))
        if n["missing"] or r is None or r["missing"]:
            continue
        for st in n["stones"]:
            if any(_near(st, o) for o in r["stones"]):
                continue
            how = "put back" if any(_near(st, h) for h in n.get("held", [])) else "seen"
            out.append({"video": n["video"], "game": n["game"], "end": n["end"],
                        "shot": n["shot"], "t_rest": n.get("t_rest"),
                        "color": st["color"], "x": st["x"], "y": st["y"], "how": how})
    return out


def unmatched(old, new):
    """How many non-missing rows of ``new`` have no row in ``old`` -- their
    stones are not looked at by ``recovered``."""
    have = {_shot_key(r) for r in old}
    return sum(not n["missing"] and _shot_key(n) not in have for n in new)


def _key(r):
    return (r["video"], r["game"], r["end"], r.get("t_rest"))


def _shot_key(r):
    return (r["video"], r["game"], r["end"], r["shot"])


def compare(old_paths, new_paths, expect=None):
    """Print how the houses changed; the exit code is 1 when ``expect`` -- a
    ``(old_paths, new_paths)`` pair -- recovered a stone the new run's own row
    for that shot lacks."""
    old, new = _load_rows(old_paths), _load_rows(new_paths)
    key = _key
    by_new = {key(r): r for r in new}
    counts = {}
    for tag, rows in (("old", old), ("new", new)):
        for part in ("last", "other"):
            sel = [r for r in rows if not r["missing"] and r["last"] == (part == "last")]
            counts[(tag, part)] = (sum(map(impossible, sel)), len(sel))
    for part in ("last", "other"):
        (a, n), (b, m) = counts[("old", part)], counts[("new", part)]
        print(f"{part:5s} rocks: impossible diffs old {a}/{n}, new {b}/{m}")
    changed = []
    for r in old:
        if r["missing"]:
            continue
        n = by_new.get(key(r))
        if n is None:
            changed.append((r, None))
        elif n["stones"] != r["stones"]:
            changed.append((r, n))
    print(f"houses changed: {len(changed)} of {sum(not r['missing'] for r in old)}")
    for r, n in changed:
        what = "no longer a rock" if n is None else (
            f"{len(r['stones'])} -> {len(n['stones'])} stones"
            f"{'  impossible->ok' if impossible(r) and not impossible(n) else ''}"
            f"{'  ok->IMPOSSIBLE' if n and impossible(n) and not impossible(r) else ''}")
        print(f"  {r['video']} g{r['game']} e{r['end']} #{r['shot']:2d} {r['color']:6s}"
              f"{' last' if r['last'] else '     '}  {what}")
    got = recovered(old, new)
    by_how = {h: sum(g["how"] == h for g in got) for h in ("put back", "seen")}
    print(f"recovered: {len(got)} stones ({by_how['put back']} put back, {by_how['seen']} seen);"
          f" {unmatched(old, new)} new rows with no old row (not looked at)")
    for g in got:
        print(f"  {g['video']} g{g['game']} e{g['end']} #{g['shot']:2d} {g['color']:6s}"
              f" at ({g['x']:.2f}, {g['y']:.2f})  {g['how']}")
    if not expect:
        return 0
    p_old, p_new = _load_rows(expect[0]), _load_rows(expect[1])
    proto = recovered(p_old, p_new)
    print(f"prototype pair: {unmatched(p_old, p_new)} new rows with no old row (not looked at)")
    rows = {_shot_key(r): r for r in new}
    lost = []
    for p in proto:
        r = rows.get(_shot_key(p))
        if r is None or r["missing"]:
            lost.append((p, "no row in the new run" if r is None else "the new run's row is missing"))
        elif not any(_near(p, st) for st in r["stones"]):
            lost.append((p, "not in the new run's row"))
    print(f"expected from the prototype: {len(proto)} stones, {len(lost)} still lost")
    for p, why in lost:
        print(f"  STILL LOST {p['video']} g{p['game']} e{p['end']} #{p['shot']:2d} "
              f"{p['color']:6s} at ({p['x']:.2f}, {p['y']:.2f})  {why}")
    return 1 if lost else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("timeline", nargs="*")
    ap.add_argument("--cache-root", default=None)
    ap.add_argument("--weights", default=None)
    ap.add_argument("--house-weights", default=None, metavar="PT",
                    help="the receiving panel's model (default: --weights)")
    ap.add_argument("--out", default=None)
    ap.add_argument("--dump-windows", default=None, metavar="DIR")
    ap.add_argument("--compare", nargs=2, metavar=("OLD", "NEW"), action="append",
                    help="compare two --out files (repeatable, one pair per game)")
    ap.add_argument("--expect", nargs=2, metavar=("PROTO_OLD", "PROTO_NEW"), action="append",
                    help="with --compare: another pair whose recovered stones this one must keep")
    args = ap.parse_args()
    if args.expect and not args.compare:
        ap.error("--expect goes with --compare")
    if args.compare:
        expect = ([a for a, _ in args.expect], [b for _, b in args.expect]) if args.expect else None
        sys.exit(compare([a for a, _ in args.compare], [b for _, b in args.compare], expect))
    root = Path(args.cache_root) if args.cache_root else cache.default_root()
    if args.dump_windows:
        Path(args.dump_windows).mkdir(parents=True, exist_ok=True)
    rows = []
    for tl in args.timeline:
        rows += replay(tl, root, args.weights or weights_mod.default_path(),
                       dump=args.dump_windows, house_weights=args.house_weights)
    if args.out:
        Path(args.out).write_text(json.dumps(rows))


if __name__ == "__main__":
    main()
