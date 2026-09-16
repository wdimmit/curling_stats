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

import functools
import json
import time
from pathlib import Path

from curling_score.harvest import build as build_mod
from curling_score.harvest import manifest as M
from curling_score.harvest import sideframes, sidepool, sideviews
from curling_score.ingest import frames as F
from curling_score import analyze

MIN_PLATE_FRAMES = sideviews.MIN_PLATE_FRAMES


def _load(path):
    return json.loads(Path(path).read_text())


def _save(path, doc):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=1) + "\n")
    return path


def _require(args, **required) -> bool:
    """Print a clear error and return True if any required arg is missing.

    ``cli._add_sideframes`` builds all four stages from one shared
    seven-argument block with nothing marked ``required`` -- the reachability
    test parses every stage against that same shared block, so marking one
    stage's argument required there would break the others' parsing rather
    than just this one's. So each stage checks what it actually needs here,
    instead of a missing ``--root`` surfacing three calls deep as
    ``TypeError: argument should be a str or an os.PathLike object, not
    NoneType``.
    """
    missing = [f"--{name}" for name, val in required.items() if not val]
    if missing:
        print(f"sideframes {args.stage}: {', '.join(missing)} required")
    return bool(missing)


def _one_frame_per_clip(root: Path):
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


def cached_video(root: Path, vid: str):
    """The full VOD under a pipeline cache root, or None.

    Two corpora feed these stages now. ds11's is 24 s clips at
    ``<root>/<vid>/*.mkv``; the shot-driven set needs whole VODs, because a
    shot list comes from segmenting a whole game and a 24 s clip has no ends
    in it. A pipeline cache root holds those at ``<root>/videos/<vid>.mp4``
    with warm proxies and detections beside them.

    Which one a root is, is answered by looking rather than by a flag: a flag
    would let a run silently take the clip path over a cache root and produce
    an empty corpus with a zero exit code.
    """
    path = root / "videos" / f"{vid}.mp4"
    return path if path.is_file() else None


def _plate_frames(root: Path, vid: str):
    """Frames to fit the side views from, whichever corpus this root is."""
    video = cached_video(root, vid)
    if video is None:
        return [img for _, img in _one_frame_per_clip(root / vid)]
    return F.sample_keyframes(video, count=analyze.CALIB_FRAMES,
                              stride=analyze.CALIB_STRIDE)


def stage_views(args) -> int:
    if _require(args, root=args.root):
        return 2
    doc = _load(args.videos)
    videos = doc["videos"][:args.limit] if args.limit else doc["videos"]
    root = Path(args.root)

    out_path = Path(args.views)
    banked = _load(out_path) if out_path.exists() else {}
    both = one = none = skipped = 0
    t_start = time.time()

    for i, v in enumerate(videos, 1):
        vid = v["video_id"]
        if vid in banked and not args.force:
            skipped += 1
            continue

        try:
            plates = _plate_frames(root, vid)
            if len(plates) < MIN_PLATE_FRAMES:
                h, w = (plates[0].shape[:2] if plates else (0, 0))
                vv = sideviews.VideoViews(
                    vid, (w, h), {},
                    f"only {len(plates)} plate frame(s), need {MIN_PLATE_FRAMES}")
            else:
                vv = sideviews.derive(vid, plates)
        except Exception as exc:  # noqa: BLE001 -- one bad video must not stop the other 119
            vv = sideviews.VideoViews(vid, (0, 0), {},
                                       f"{type(exc).__name__}: {exc}")

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

    processed = both + one + none
    print(f"\nthis run: {both + one}/{processed} newly-processed video(s) with "
          f"at least one usable view ({both} both, {one} one, {none} none)"
          + (f"; {skipped} already banked, skipped (--force to redo)"
             if skipped else ""))

    # A separate tally over everything ever banked, not just this run's
    # videos: once a video already banked is skipped, this run's own counts
    # and the corpus-wide ones are two different populations, and printing
    # only one invites reading it as the other.
    all_both = all_one = all_none = 0
    for raw in banked.values():
        n = len(sideviews.usable_views(sideviews.from_json(raw)))
        if n == 2:
            all_both += 1
        elif n == 1:
            all_one += 1
        else:
            all_none += 1
    print(f"overall: {all_both + all_one}/{len(banked)} banked video(s) with "
          f"at least one usable view ({all_both} both, {all_one} one, "
          f"{all_none} none)")
    return 0 if (all_both + all_one) else 1


@functools.lru_cache(maxsize=1)
def _build_detector(imgsz: int):
    from curling_score import weights as weights_mod
    from curling_score.detect import yolo

    return yolo.YoloDetector(weights_mod.default_path(), conf=0.30,
                             device=None, imgsz=imgsz)


def _detector(args):
    """The shot detector the window walk needs, built once per run.

    Only the cached-VOD path uses one: proposing from clips never had a shot
    list to build. Kept out of module import so the clip corpus, and every
    test that touches these stages, still runs with no GPU and no weights.
    """
    return _build_detector(getattr(args, "imgsz", 448))


def stage_propose(args) -> int:
    if _require(args, root=args.root, out=args.out):
        return 2
    doc = _load(args.videos)
    videos = doc["videos"][:args.limit] if args.limit else doc["videos"]
    root, out = Path(args.root), Path(args.out)
    views_doc = _load(args.views)

    pool_path = out / "candidates.json"
    errors_path = out / "propose_errors.json"
    banked = _load(pool_path) if pool_path.exists() else {}
    errors = _load(errors_path) if errors_path.exists() else {}
    skipped = usable = failed = 0
    agg_refusals: dict = {}
    t_start = time.time()

    for i, v in enumerate(videos, 1):
        vid = v["video_id"]
        if vid in banked and not args.force:
            skipped += 1
            continue

        raw = views_doc.get(vid)
        if raw is None:
            print(f"[{i:3d}] {vid}: not in {args.views}, skipped", flush=True)
            continue
        vv = sideviews.from_json(raw)
        if not sideviews.is_usable(vv):
            print(f"[{i:3d}] {vid}: UNUSABLE -- {vv.error or 'no usable view'}",
                  flush=True)
            continue
        views = dict(sideviews.usable_views(vv))
        video = cached_video(root, vid)
        paths = [] if video is not None else sorted((root / vid).glob("*.mkv"))
        if video is None and not paths:
            print(f"[{i:3d}] {vid}: no clips and no cached video, skipped",
                  flush=True)
            continue

        try:
            if video is not None:
                cands, stats = sidepool.build_video_pool_from_windows(
                    vid, video, views, out, root,
                    detector=_detector(args), fps=args.fps,
                    progress=lambda m: print(f"    {m}", flush=True))
            else:
                cands, stats = sidepool.build_video_pool(vid, paths, vv, out,
                                                         fps=args.fps)
        except Exception as exc:  # noqa: BLE001 -- one bad video must not stop the other 119
            errors[vid] = f"{type(exc).__name__}: {exc}"
            banked[vid] = []
            _save(pool_path, banked)
            _save(errors_path, errors)
            failed += 1
            print(f"[{i:3d}/{len(videos)}] {vid} {v['date']} -> FAILED -- "
                  f"{errors[vid]}", flush=True)
            continue

        banked[vid] = [M.side_candidate_to_json(c) for c in cands]
        errors.pop(vid, None)
        _save(pool_path, banked)
        _save(errors_path, errors)
        usable += 1
        for key, n in stats["refusals"].items():
            agg_refusals[key] = agg_refusals.get(key, 0) + n

        print(f"[{i:3d}/{len(videos)}] {vid} {v['date']} -> "
              f"{len(cands)} candidates, {stats['written']} written "
              f"({(time.time() - t_start) / i:.0f}s/video)", flush=True)

    processed = usable + failed
    print(f"\nthis run: {usable}/{processed} newly-proposed video(s)"
          + (f", {failed} failed" if failed else "")
          + (f"; {skipped} already banked, skipped (--force to redo)"
             if skipped else ""))

    # As in stage_views: everything ever banked, a different population from
    # this run's videos the moment a restart can skip one.
    all_failed = len(errors)
    all_usable = len(banked) - all_failed
    print(f"overall: {all_usable}/{len(banked)} banked video(s) proposed, "
          f"{sum(len(c) for c in banked.values())} candidates"
          + (f", {all_failed} failed -- see {errors_path}" if all_failed else ""))
    if errors:
        print(f"  failed: {', '.join(sorted(errors))}")
    if agg_refusals:
        # Not an exact count of refused windows: `build_video_pool`'s own
        # docstring says a clip-view that mixes "clear" moments (no colour,
        # never refused) with occluded or proposal moments (which can be)
        # inflates this by up to 2x. Treat it as a rough sense of where the
        # detector struggles, not a number to report to two figures.
        print(f"  refusals by gate, roughly (can read up to 2x high): "
              f"{agg_refusals}")
    return 0 if all_usable else 1


def _expand_quota() -> dict:
    """``SCENE_QUOTA`` is per view; expand both halves to the same per-bin
    keys ``sideframes.select``'s ``shortfall`` uses, so a reader reconciling
    the manifest's own numbers gets the real 600-frame target rather than
    half of it.
    """
    quota = {f"scene:{view}:{position}": want
             for view in ("left", "right")
             for position, want in sideframes.SCENE_QUOTA.items()}
    quota.update({f"outcome:{key}": want
                  for key, want in sideframes.OUTCOME_QUOTA.items()})
    return quota


def _supply(pool) -> dict:
    """How many pool *frames* were eligible for each bin, before any cap.

    ``select``'s ``shortfall`` counts frames -- it dedupes the pool by stem.
    Counting rows here instead would let one frame inflate a bin's supply,
    printed right next to a shortfall that cannot be inflated the same way --
    exactly backwards when a large supply number is meant to say "the caps, not
    the archive, are why". So this dedupes first, the same way, and counts
    frames.

    Since ``c3fa3e8`` the banked pool comes only from ``pick_writes``, which is
    already stem-unique, so on the pool this is actually handed the dedupe is a
    no-op. It stays because it makes this function correct against *a* pool
    rather than against the one shape the propose stage happens to produce
    today -- the cost is a dict comprehension, and the alternative is a
    correctness claim that silently depends on a caller two modules away.

    ``select``'s ``shortfall`` alone cannot tell "the archive had none" from
    "the caps were already spent by the scene pass" -- both read as a bin at
    zero. Counting the pool itself, before selection ever runs, can.
    """
    frames = list({c.stem: c for c in pool}.values())
    supply = {f"scene:{view}:{position}":
              sum(1 for c in frames if c.view == view and c.position == position)
              for view in ("left", "right")
              for position in sideframes.SCENE_QUOTA}
    supply.update({f"outcome:{key}": sum(1 for c in frames if c.outcome == key)
                   for key in sideframes.OUTCOME_QUOTA})
    return supply


def stage_select(args) -> int:
    if _require(args, pool=args.pool):
        return 2
    plan_doc = _load(args.videos)
    banked = _load(Path(args.pool) / "candidates.json")
    splits = {v["video_id"]: v["split"] for v in plan_doc["videos"]}

    pool = [M.side_candidate_from_json(r)
            for rows in banked.values() for r in rows]
    supply = _supply(pool)
    chosen, shortfall = sideframes.select(pool)

    quota = _expand_quota()
    doc = M.build_side_manifest(
        chosen, shortfall, supply, splits, quota,
        extra={"pool": str(args.pool), "videos_file": str(args.videos)})
    out = _save(args.manifest, doc)
    s = doc["summary"]
    print(f"wrote {out}: {s['frames']} frames "
          f"({s['train_frames']} train / {s['val_frames']} val) "
          f"from {s['videos']} videos, target {s['target']}")
    if s["shortfall"]:
        supply_n = sum(s["supply"].get(k, 0) for k in s["shortfall"])
        print(f"  {len(s['shortfall'])} bin(s) short, "
              f"{sum(s['shortfall'].values())} frame(s) total "
              f"({supply_n} eligible candidate(s) sat in those bins before "
              f"caps -- a large number here means the caps, not the archive, "
              f"are why)")
    return 0


def stage_build(args) -> int:
    if _require(args, pool=args.pool, out=args.out):
        return 2
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
