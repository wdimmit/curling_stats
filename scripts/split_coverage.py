#!/usr/bin/env python
"""How much of a game the long split now covers, and whether to believe it.

Runs the whole pipeline over every cached video and reports three things per
sheet: coverage against the 46% the overhead panels managed alone, agreement
with the panel tripwire wherever both fired, and -- on the reference VOD --
agreement with the crossings marked by hand in ``datasets/hogmarks``.

    python scripts/split_coverage.py --cache-root ~/.cache/curling_score

Detection is read from the cache built by earlier passes over these videos
(``detect/cache.py``) -- nothing in this script's imports touches the files
that key it, so a video already detected costs no GPU time here, only the
handful of seconds it takes to walk the cached results back into shots.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

import numpy as np

# ``replay_end.py`` lives beside this script, not under a ``scripts`` package
# (there is no ``__init__.py``), so it is reached the same way this script
# reaches its own directory: by being on ``sys.path``, which a direct
# ``python scripts/split_coverage.py`` invocation already puts there.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from curling_score import analyze as analyze_mod, weights as weights_mod
from curling_score.detect import delivery as D, longview, release, sequence, yolo
from curling_score.game import fit, hogtime, profile, secondpass, segment, shots as shots_mod, split
from curling_score.geometry import sideview
from curling_score.ingest import cache, frames as F, proxy
from replay_end import setups_for

REPO_ROOT = Path(__file__).resolve().parents[1]
HOGMARKS_DIR = REPO_ROOT / "datasets" / "hogmarks"
GROUND_TRUTH = REPO_ROOT / "validation" / "ground_truth.json"

# The historical 46% (docs/superpowers/specs/2026-09-14-long-camera-hog-crossing-design.md)
# was measured with the throwing-end crossing coming straight off the panel's
# own tripwire -- before the side view, and before the cross-check gate in
# split.py required a t_hog to compare against (47a2d49 pinned that). Passing
# the panel's own reading in as t_hog reproduces exactly that: the cross-check
# trivially agrees with itself, so long_split runs the same speed-tolerance
# and end-crossing logic it always does, sourced from the panel alone.
def panel_only_split(shot):
    r = getattr(shot, "release", None)
    if r is None:
        return None
    panel_t = split.hog_crossing(getattr(r, "track", ()))
    if panel_t is None:
        return None
    return split.long_split(r, getattr(shot, "delivery", None), t_hog=panel_t)


def side_split(shot):
    return split.long_split(getattr(shot, "release", None),
                            getattr(shot, "delivery", None),
                            t_hog=hogtime.crossing(shot))


def _sheet_for(vid):
    if GROUND_TRUTH.is_file():
        doc = json.loads(GROUND_TRUTH.read_text())
        info = doc.get("videos", {}).get(vid)
        if info and "sheet" in info:
            return info["sheet"]
    return None


def _views_for(video_path, setups, panels):
    """The two side views, solved from the same calib plate as the panels.

    Each view is independent: one side of a composite can fail to calibrate
    (a light out, a camera down) while the other is fine, so failures are
    caught per view, not for the pair.
    """
    calib_frames = F.sample_keyframes(video_path, count=analyze_mod.CALIB_FRAMES,
                                      stride=analyze_mod.CALIB_STRIDE)
    h, w = calib_frames[0].shape[:2]
    plate = np.median(np.stack([f.astype(np.float32) for f in calib_frames]), axis=0)
    views, errors = {}, {}
    try:
        rects = sideview.locate(panels, width=w, height=h)
    except sideview.SideViewError as exc:
        errors["both"] = str(exc)
        return views, errors
    for name, rect in rects.items():
        try:
            views[name] = sideview.solve(plate, rect, name=name)
        except sideview.SideViewError as exc:
            errors[name] = str(exc)
    return views, errors


def _shots_for_end(read_path, proxy_setups, end, from_s, detector):
    """Everything analyze.py does for one end, short of the clock and hogtime."""
    setup = proxy_setups[end.house]
    seq = list(sequence.detect_end(read_path, setup, end, analyze_mod.SHOT_FPS,
                                   detector, from_s=from_s))
    deliveries = [d for d in D.find_deliveries(
                      seq, view_x_limit_m=setup.view_x_limit_m,
                      view_y_min_m=setup.view_y_min_m)
                  if d.t_enter >= from_s]
    gaps = secondpass.gaps_to_search(deliveries, end.start_s, end.end_s)
    recovered = secondpass.search(seq, gaps, deliveries)
    if recovered:
        deliveries = sorted(deliveries + recovered, key=lambda d: d.t_enter)
    far = proxy_setups[analyze_mod.OTHER_HOUSE[end.house]]
    far_seq = list(sequence.detect_span(read_path, far, from_s, end.end_s,
                                        release.RELEASE_FPS, detector))
    releases, thrown_by, unaccounted = release.find_and_pair(
        far_seq, far.view_y_min_m, deliveries, seq, since=from_s)
    if unaccounted:
        deliveries = sorted(deliveries + unaccounted, key=lambda d: d.t_enter)
    kept = fit.fit_end(deliveries)
    shots = shots_mod.from_deliveries(
        kept, seq, thrown_by={id(d): r for r, d in thrown_by.items()})
    next_from = min(end.end_s, kept[-1].t_rest) if kept else end.end_s
    return shots, next_from


def _time_crossings(shots, video_path, view):
    """Run hogtime.time_hog_crossings, recording the Crossing behind each shot.

    ``time_hog_crossings`` only keeps the ones that succeed (it sets
    ``t_hog_s``); refusal reasons are the point of this report, so a recording
    ``find`` is threaded through instead of reading ``longview.find_crossing``
    a second time with different code.
    """
    invokable = [s for s in shots if not getattr(s, "missing", False)
                 and (getattr(s, "release", None) is not None
                      or getattr(s, "delivery", None) is not None)]
    got_list = []

    def recording_find(video_, view_, color, t0, t1):
        got = longview.find_crossing(video_, view_, color, t0, t1)
        got_list.append(got)
        return got

    hogtime.time_hog_crossings(shots, video_path, view, find=recording_find)
    return dict(zip((id(s) for s in invokable), got_list))


class VideoStats:
    def __init__(self, vid, sheet):
        self.vid = vid
        self.sheet = sheet
        self.n_ends = 0
        self.n_shots = 0
        self.side_splits = 0
        self.panel_splits = 0
        self.diffs = []           # t_hog - panel, wherever both fired
        self.refusals = {}        # reason -> count
        self.hand = None          # (errors, refused) on the reference VOD, if applicable

    def add_refusal(self, reason):
        self.refusals[reason] = self.refusals.get(reason, 0) + 1

    def report(self):
        lines = [f"== {self.vid} (sheet {self.sheet if self.sheet is not None else 'unknown'}) =="]
        lines.append(f"  ends measured: {self.n_ends}")
        if self.n_shots:
            lines.append(f"  side-view coverage:  {self.side_splits}/{self.n_shots} "
                        f"({100 * self.side_splits / self.n_shots:.1f}%)")
            lines.append(f"  panel-only coverage: {self.panel_splits}/{self.n_shots} "
                        f"({100 * self.panel_splits / self.n_shots:.1f}%)")
        else:
            lines.append("  no shots measured")
        if self.diffs:
            ad = sorted(abs(d) for d in self.diffs)
            lines.append(f"  agreement where both fired: n={len(ad)}, "
                        f"median |diff|={statistics.median(ad):.3f}s, "
                        f"worst |diff|={ad[-1]:.3f}s")
        else:
            lines.append("  agreement where both fired: no shots had both readings")
        if self.refusals:
            lines.append("  refusal reasons:")
            for reason, n in sorted(self.refusals.items(), key=lambda kv: -kv[1]):
                lines.append(f"    {n:3d}  {reason}")
        if self.hand is not None:
            errors, refused = self.hand
            if errors:
                ae = sorted(abs(e) for e in errors)
                lines.append(f"  hand-mark agreement: {len(errors)}/{len(errors) + len(refused)} found, "
                            f"median |error|={statistics.median(ae):.3f}s, "
                            f"worst |error|={ae[-1]:.3f}s")
            else:
                lines.append("  hand-mark agreement: every mark refused")
            if refused:
                lines.append(f"    {len(refused)} refused: {refused}")
        return "\n".join(lines)


def measure_video(video_path, root, detector):
    vid = video_path.stem
    sheet = _sheet_for(vid)
    stats = VideoStats(vid, sheet)
    print(f"-- {vid}: calibrating (panels + side views)...", flush=True)

    setups, panels = setups_for(video_path, vid, root)
    views, view_errors = _views_for(video_path, setups, panels)
    for name, err in view_errors.items():
        print(f"  {vid}: {name} side view unusable: {err}", flush=True)
    print(f"  {vid}: side views available: {sorted(views)}", flush=True)

    strip = proxy.strip_rect(panels.top, panels.bottom)
    read_path = proxy.proxy_path(vid, strip, root)
    if not read_path.is_file():
        print(f"  {vid}: no cached proxy, building one (this is the slow path)",
             flush=True)
        read_path = proxy.ensure_proxy(video_path, vid, strip, root=root)
    proxy_setups = analyze_mod._proxy_setups(setups, strip)

    print(f"  {vid}: building the whole-video activity profile...", flush=True)
    samples = profile.build_profile(read_path, proxy_setups)
    games = segment.segment_games(samples)
    total_ends = sum(len(g.ends) for g in games)
    print(f"  {vid}: {len(games)} game(s), {total_ends} end(s) total", flush=True)

    done_ends = 0
    prev_end_s = None
    for game in games:
        for end in game.ends:
            from_s = analyze_mod.run_up_from(
                prev_end_s, end.start_s, crossed_games=(end is game.ends[0]))
            print(f"  {vid} game {game.index} end {end.number} ({end.house}): "
                 f"detecting shots ({done_ends}/{total_ends} ends done so far)...",
                 flush=True)
            try:
                shots, prev_end_s = _shots_for_end(
                    read_path, proxy_setups, end, from_s, detector)
            except Exception as exc:  # noqa: BLE001 -- one bad end must not sink the run
                print(f"  {vid} game {game.index} end {end.number}: "
                     f"FAILED to build shots: {exc!r}", flush=True)
                prev_end_s = end.end_s
                done_ends += 1
                continue
            if not shots:
                print(f"  {vid} game {game.index} end {end.number}: no shots built",
                     flush=True)
                done_ends += 1
                continue
            stats.n_ends += 1
            stats.n_shots += len(shots)

            camera = hogtime.CAMERA_FOR[analyze_mod.OTHER_HOUSE[end.house]]
            view = views.get(camera)
            crossing_by_id = {}
            if view is not None:
                print(f"  {vid} game {game.index} end {end.number}: "
                     f"timing {len(shots)} hog crossings from the {camera} side view...",
                     flush=True)
                crossing_by_id = _time_crossings(shots, video_path, view)

            end_side_splits = end_panel_splits = 0
            for shot in shots:
                if side_split(shot) is not None:
                    stats.side_splits += 1
                    end_side_splits += 1
                if panel_only_split(shot) is not None:
                    stats.panel_splits += 1
                    end_panel_splits += 1

                got = crossing_by_id.get(id(shot))
                if got is not None and not got:
                    stats.add_refusal(got.reason)
                elif view is None and not getattr(shot, "missing", False):
                    stats.add_refusal("no calibrated side view for this panel")

                t_hog = hogtime.crossing(shot)
                r = getattr(shot, "release", None)
                panel_t = split.hog_crossing(getattr(r, "track", ())) if r else None
                if t_hog is not None and panel_t is not None:
                    stats.diffs.append(t_hog - panel_t)

            done_ends += 1
            print(f"  {vid} game {game.index} end {end.number}: done "
                 f"-- {len(shots)} shots, side {end_side_splits}, "
                 f"panel {end_panel_splits} ({done_ends}/{total_ends} ends)",
                 flush=True)

    marks_path = HOGMARKS_DIR / f"{vid}.json"
    if marks_path.is_file() and views:
        print(f"  {vid}: checking against hand marks in {marks_path.name}...",
             flush=True)
        stats.hand = _hand_mark_agreement(video_path, views, marks_path)

    return stats


def _hand_mark_agreement(video_path, views, marks_path):
    """Same measurement as tests/test_longview.py, run directly for the report."""
    doc = json.loads(marks_path.read_text())
    errors, refused = [], []
    for end in doc["ends"]:
        view = views.get(end["side_view"])
        if view is None:
            for m in end["marks"]:
                refused.append((m["release_t_s"], "no calibrated view for this panel"))
            continue
        for m in end["marks"]:
            lo = m["release_t_s"] + longview.WINDOW_S[0]
            hi = m["release_t_s"] + longview.WINDOW_S[1]
            got = longview.find_crossing(video_path, view, m["color"], lo, hi)
            if not got:
                refused.append((m["release_t_s"], got.reason))
                continue
            errors.append(got.t - m["hog_crossing_s"])
    return errors, refused


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--cache-root", default=None)
    ap.add_argument("--weights", default=None)
    ap.add_argument("--videos", nargs="+", default=None,
                    help="video ids to measure (default: every *.mp4 in the cache)")
    args = ap.parse_args()

    root = Path(args.cache_root) if args.cache_root else cache.default_root()
    video_dir = root / "videos"
    if args.videos:
        video_paths = [video_dir / f"{v}.mp4" for v in args.videos]
        missing = [p for p in video_paths if not p.is_file()]
        if missing:
            raise SystemExit(f"not cached: {[p.stem for p in missing]}")
    else:
        video_paths = sorted(video_dir.glob("*.mp4"))
    if not video_paths:
        raise SystemExit(f"no cached videos under {video_dir}")

    print(f"measuring {len(video_paths)} cached video(s): "
         f"{[p.stem for p in video_paths]}", flush=True)

    weights = args.weights or weights_mod.default_path()
    detector = yolo.YoloDetector(weights, conf=0.30, device=None, imgsz=448)
    detector.model.overrides["half"] = True

    all_stats = []
    for video_path in video_paths:
        stats = measure_video(video_path, root, detector)
        all_stats.append(stats)
        print(flush=True)
        print(stats.report(), flush=True)

    print("\n== overall ==", flush=True)
    total_shots = sum(s.n_shots for s in all_stats)
    total_side = sum(s.side_splits for s in all_stats)
    total_panel = sum(s.panel_splits for s in all_stats)
    all_diffs = [d for s in all_stats for d in s.diffs]
    print(f"  videos measured: {len(all_stats)}", flush=True)
    print(f"  ends measured:   {sum(s.n_ends for s in all_stats)}", flush=True)
    print(f"  shots measured:  {total_shots}", flush=True)
    if total_shots:
        print(f"  side-view coverage:  {total_side}/{total_shots} "
             f"({100 * total_side / total_shots:.1f}%)", flush=True)
        print(f"  panel-only coverage: {total_panel}/{total_shots} "
             f"({100 * total_panel / total_shots:.1f}%)", flush=True)
    if all_diffs:
        ad = sorted(abs(d) for d in all_diffs)
        print(f"  agreement where both fired: n={len(ad)}, "
             f"median |diff|={statistics.median(ad):.3f}s, worst |diff|={ad[-1]:.3f}s",
             flush=True)
    print("\n  This script only measures; the gate in the task brief "
         "(coverage >= 90%, panel agreement <= 0.25s, hand marks <= 0.1s) "
         "is reported against below, not decided here.", flush=True)


if __name__ == "__main__":
    main()
