"""Same-colour separation gap, old (one diameter) against new (SAME_STONE_GAP_M),
every end of the given games, in one process.

Each end is detected once with `enforce_separation` switched off, then each
arm applies its own gap to every frame and the same rules run on the result --
deliveries, the gap search, release pairing, the clearing drop, the built
shots (as `tracker_ab.py`; farfollow, lost_rocks and the side views are not
run). Prints a line per end that differs.

The raw detections must not reach a worker's cache, whose key takes them for
the real thing: run with CURLING_SCORE_CACHE pointing at a root of its own
(videos/, proxies/ and the setups pickles linked in, an empty detections/).

    python sep_ab.py OUT.json TIMELINE.json [...]

In the worker image with this branch's src on PYTHONPATH, and replay_end.py
(for `setups_for`) beside this file.
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from curling_score import analyze as A, weights as W                     # noqa: E402
from curling_score.detect import delivery as D, release, rocks, sequence, yolo  # noqa: E402
from curling_score.game import fit, secondpass, segment, shots as S       # noqa: E402
from curling_score.ingest import cache, proxy                            # noqa: E402
from replay_end import setups_for                                        # noqa: E402

GAPS = {"old": 2.0 * 0.142, "new": rocks.SAME_STONE_GAP_M}


def separated(seq, gap):
    return [(t, rocks.enforce_separation(list(d), min_gap_m=gap)) for t, d in seq]


def built_end(frames, far_seq, far, setup, from_s, end):
    every = D.find_deliveries(frames, view_x_limit_m=setup.view_x_limit_m,
                              view_y_min_m=setup.view_y_min_m)
    ds = [d for d in every if d.t_enter >= from_s]
    rec = secondpass.search(frames, secondpass.gaps_to_search(ds, end.start_s, end.end_s), ds)
    if rec:
        ds = sorted(ds + rec, key=lambda d: d.t_enter)
    releases, matched, settled = release.find_and_pair(
        far_seq, far.view_y_min_m, ds, frames, since=from_s, view_x_limit_m=far.view_x_limit_m)
    if settled:
        ds = sorted(ds + settled, key=lambda d: d.t_enter)
    thrown_by = {id(d): r for r, d in matched.items()}
    cleared = fit.drop_clearing(ds, frames, fit.released_ids(matched, settled))
    built = S.from_deliveries(fit.fit_end(cleared, paired=fit.paired_ids(matched)),
                              frames, thrown_by=thrown_by)
    return {
        "releases": sorted(round(r.t, 1) for r in releases),
        "shots": [{"n": s.number, "color": s.color, "missing": s.missing,
                   "t_rest": None if s.t_rest_s is None or s.t_rest_s != s.t_rest_s
                   else round(s.t_rest_s, 1),
                   "at": None if s.delivery is None else
                   (round(s.delivery.rest_x_m, 2), round(s.delivery.rest_y_m, 2)),
                   "house": sorted((st.color, round(st.x_m, 1), round(st.y_m, 1))
                                   for st in (s.stones or []))}
                  for s in built],
    }


def run_game(path, detector, root):
    doc = json.loads(Path(path).read_text())
    vid = doc["source"]["video_id"]
    video = root / "videos" / f"{vid}.mp4"
    setups, panels = setups_for(video, vid, root)
    strip = proxy.strip_rect(panels.top, panels.bottom)
    read_path = proxy.proxy_path(vid, strip, root)
    prox = A._proxy_setups(setups, strip)
    out = []
    for game in doc["games"]:
        ends = sorted(game["ends"], key=lambda e: e["number"])
        for k, e in enumerate(ends):
            tic = time.monotonic()
            prev_close = None
            if k:
                prev = ends[k - 1]
                rests = [s["t_rest_s"] for s in prev["shots"] if s.get("t_rest_s") is not None]
                prev_close = min(prev["end_s"], max(rests)) if rests else prev["end_s"]
            from_s = A.run_up_from(prev_close, e["start_s"])
            setup, far = prox[e["house"]], prox[A.OTHER_HOUSE[e["house"]]]
            end = segment.EndSegment(number=e["number"], house=e["house"],
                                     start_s=e["start_s"], end_s=e["end_s"])
            raw = [(t, list(d)) for t, d in sequence.detect_end(
                read_path, setup, end, A.SHOT_FPS, detector, from_s=from_s)]
            far_raw = [(t, list(d)) for t, d in sequence.detect_span(
                read_path, far, from_s, end.end_s, release.RELEASE_FPS, detector)]
            got = {name: built_end(separated(raw, gap), separated(far_raw, gap), far, setup, from_s, end)
                   for name, gap in GAPS.items()}
            same = got["old"] == got["new"]
            out.append({"source": Path(path).stem, "video": vid, "game": game["index"],
                        "end": e["number"], "same": same, **got})
            print(f"{Path(path).stem} g{game['index']} e{e['number']}: "
                  f"{'same' if same else 'DIFFERS'} ({time.monotonic() - tic:.0f} s)", flush=True)
    return out


def main():
    # Detect with no separation at all; each arm applies its own.
    yolo.enforce_separation = lambda dets, min_gap_m=None: list(dets)
    root = cache.default_root()
    detector = yolo.YoloDetector(W.default_path(), conf=0.30, device=None, imgsz=448)
    detector.model.overrides["half"] = True
    results = []
    for path in sys.argv[2:]:
        results += run_game(path, detector, root)
        Path(sys.argv[1]).write_text(json.dumps(results))
    n = sum(1 for r in results if not r["same"])
    print(f"done: {len(results)} ends, {n} differ")


if __name__ == "__main__":
    main()
