"""ds13, as four resumable stages.

    views    ds11 clips              -> sideviews.json    both cameras, or why not
    propose  clips + sideviews.json  -> pool/             crops and what was seen
    select   pool/                   -> manifest.json     600 frames, and what was short
    build    manifest.json           -> images/labels     the YOLO tree

Each reads a file and writes a file, and each is safe to run again -- the
corpus is 1197 clips on a box across the network, and a job that starts over
when something hiccups is a job nobody leaves running.

The splits are ds11's, read straight out of ``datasets/ds11/videos.json``: the
same season, the same five held-out Tuesdays. A side-view model and an overhead
model measured on the same held-out games can be compared; measured on two
splits that merely look alike, they cannot.
"""

import datetime as _dt
import json
import time
from pathlib import Path

from curling_score.harvest import build as build_mod
from curling_score.harvest import manifest as M
from curling_score.harvest import sideframes, sidepool, sideviews
from curling_score.ingest import frames as F

MIN_PLATE_FRAMES = sideviews.MIN_PLATE_FRAMES


def _load(path):
    return json.loads(Path(path).read_text())


def _save(path, doc):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=1) + "\n")
    return path


def _one_frame_per_clip(root: Path, vid: str):
    """``(t_abs, frame)`` for one keyframe from every clip a video has.

    Same trick ``harvest.stages.stage_pool`` uses for the overhead panels:
    one frame per clip spreads the plate across the whole video rather than
    a single 24 s window, and a settled house or a crowd of players is what
    does *not* change between clips far apart in time.
    """
    timed = []
    for path in sorted(root.glob("*.mkv")):
        offset = F.stream_start_s(path)
        for t, img in F.keyframe_sweep(path):
            timed.append((t + offset, img))
            break
    return timed


def stage_views(args) -> int:
    doc = _load(args.videos)
    videos = doc["videos"][:args.limit] if args.limit else doc["videos"]
    root = Path(args.root)

    out_path = Path(args.views)
    banked = _load(out_path) if out_path.exists() else {}
    both = one = none = 0
    t_start = time.time()

    for i, v in enumerate(videos, 1):
        vid = v["video_id"]
        timed = _one_frame_per_clip(root / vid, vid)

        if len(timed) < MIN_PLATE_FRAMES:
            h, w = (timed[0][1].shape[:2] if timed else (0, 0))
            vv = sideviews.VideoViews(
                vid, (w, h), {},
                f"only {len(timed)} clip frame(s), need {MIN_PLATE_FRAMES}")
        else:
            vv = sideviews.derive(vid, [img for _, img in timed])

        banked[vid] = sideviews.to_json(vv)
        _save(out_path, banked)

        n_usable = len(sideviews.usable_views(vv))
        if n_usable == 2:
            both += 1
        elif n_usable == 1:
            one += 1
        else:
            none += 1
        note = f" -- {vv.error}" if vv.error else ""
        print(f"[{i:3d}/{len(videos)}] {vid} {v['date']} -> "
              f"{n_usable} view(s) usable{note} "
              f"(both {both}, one {one}, none {none}) "
              f"({(time.time() - t_start) / i:.0f}s/video)", flush=True)

    print(f"\n{both + one}/{len(videos)} videos with at least one usable "
          f"view ({both} both, {one} one, {none} none)")
    return 0 if (both + one) else 1


def stage_propose(args) -> int:
    doc = _load(args.videos)
    videos = doc["videos"][:args.limit] if args.limit else doc["videos"]
    root, out = Path(args.root), Path(args.out)
    views_doc = _load(args.views)

    pool_path = out / "candidates.json"
    banked = _load(pool_path) if pool_path.exists() else {}
    usable = 0
    agg_refusals: dict = {}
    t_start = time.time()

    for i, v in enumerate(videos, 1):
        vid = v["video_id"]
        raw = views_doc.get(vid)
        if raw is None:
            print(f"[{i:3d}] {vid}: not in {args.views}, skipped", flush=True)
            continue
        vv = sideviews.from_json(raw)
        if not sideviews.is_usable(vv):
            print(f"[{i:3d}] {vid}: UNUSABLE -- {vv.error or 'no usable view'}",
                  flush=True)
            continue
        paths = sorted((root / vid).glob("*.mkv"))
        if not paths:
            print(f"[{i:3d}] {vid}: no clips, skipped", flush=True)
            continue

        cands, stats = sidepool.build_video_pool(vid, paths, vv, out, fps=args.fps)
        banked[vid] = [M.side_candidate_to_json(c) for c in cands]
        _save(pool_path, banked)
        usable += 1
        for key, n in stats["refusals"].items():
            agg_refusals[key] = agg_refusals.get(key, 0) + n

        print(f"[{i:3d}/{len(videos)}] {vid} {v['date']} -> "
              f"{len(cands)} candidates, {stats['written']} written "
              f"({(time.time() - t_start) / i:.0f}s/video)", flush=True)

    print(f"\n{usable}/{len(videos)} videos proposed, "
          f"{sum(len(c) for c in banked.values())} candidates")
    if agg_refusals:
        # Not an exact count of refused windows: `build_video_pool`'s own
        # docstring says a clip-view that mixes "clear" moments (no colour,
        # never refused) with occluded or proposal moments (which can be)
        # inflates this by up to 2x. Treat it as a rough sense of where the
        # detector struggles, not a number to report to two figures.
        print(f"  refusals by gate, roughly (can read up to 2x high): "
              f"{agg_refusals}")
    return 0 if usable else 1


def _build_side_manifest(chosen, shortfall, splits, quota, extra=None) -> dict:
    """The chosen side-view frames, grouped by video, with the shortfall.

    Unlike ``manifest.build_manifest``, ``sideframes.select`` chooses across
    the whole pool in one pass rather than per video, so there is one
    ``shortfall`` for the run, not one per video -- it belongs in the
    summary, next to the quota it failed to fill.
    """
    videos, counts = {}, {"train": 0, "val": 0}
    by_video: dict = {}
    for c in chosen:
        by_video.setdefault(c.video_id, []).append(c)

    for vid in sorted(by_video):
        split = splits[vid]  # a video with no split is a bug, not a default
        picked = by_video[vid]
        counts[split] = counts.get(split, 0) + len(picked)
        videos[vid] = {
            "split": split,
            "frames": [M.side_candidate_to_json(c) for c in picked],
        }

    doc = {
        "created": _dt.date.today().isoformat(),
        "quota": dict(quota),
        "summary": {
            "videos": len(videos),
            "frames": sum(counts.values()),
            "train_frames": counts.get("train", 0),
            "val_frames": counts.get("val", 0),
            "shortfall": dict(sorted(shortfall.items())),
        },
        "videos": videos,
    }
    if extra:
        clash = sorted(set(extra) & set(doc))
        if clash:
            raise ValueError(
                f"extra would overwrite the manifest's own {', '.join(clash)}")
        doc.update(extra)
    return doc


def stage_select(args) -> int:
    plan_doc = _load(args.videos)
    banked = _load(Path(args.pool) / "candidates.json")
    splits = {v["video_id"]: v["split"] for v in plan_doc["videos"]}

    pool = [M.side_candidate_from_json(r)
            for rows in banked.values() for r in rows]
    chosen, shortfall = sideframes.select(pool)

    quota = {"scene": dict(sideframes.SCENE_QUOTA),
             "outcome": dict(sideframes.OUTCOME_QUOTA)}
    doc = _build_side_manifest(
        chosen, shortfall, splits, quota,
        extra={"pool": str(args.pool), "videos_file": str(args.videos)})
    out = _save(args.manifest, doc)
    s = doc["summary"]
    print(f"wrote {out}: {s['frames']} frames "
          f"({s['train_frames']} train / {s['val_frames']} val) "
          f"from {s['videos']} videos")
    if s["shortfall"]:
        print(f"  {len(s['shortfall'])} bin(s) short, "
              f"{sum(s['shortfall'].values())} frame(s) total")
    return 0


def stage_build(args) -> int:
    doc = _load(args.manifest)
    stats = build_mod.build(doc, args.pool, args.out, iter_frames=M.side_iter_frames)
    print(f"{stats['written']} frames, {stats['labels']} labels, "
          f"{stats['empty']} confirmed-empty")
    if stats["missing"]:
        print(f"  {stats['missing']} frame(s) named by the manifest are not in "
              f"the pool -- the two disagree")
        return 1
    return 0


STAGES = {"views": stage_views, "propose": stage_propose,
          "select": stage_select, "build": stage_build}


def run(args) -> int:
    return STAGES[args.stage](args)
