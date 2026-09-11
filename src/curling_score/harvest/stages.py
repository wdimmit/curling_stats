"""The harvest, as five resumable stages.

Separate stages because the whole run is an hour of downloading and half an
hour of GPU, and a job that starts over when the network hiccups is a job
nobody leaves running. Each stage reads a file and writes a file; each is safe
to run again.

    plan    playlist            -> videos.json    which games, which split
    clips   videos.json         -> clips/         half-minute cuts, kept
    pool    clips/              -> pool/          crops + the detector's guess
    select  pool/               -> manifest.json  which frames, and what was short
    build   manifest.json       -> images/labels  the YOLO tree
"""

import json
import random
import time
from pathlib import Path

from curling_score.harvest import (build as build_mod, candidates as C, clips,
                                   manifest as M, plan as plan_mod, playlist,
                                   pool as pool_mod, setups)

BLOCK_HINTS = ("sign in to confirm", "not a bot", "429", "too many requests")
BACKOFF_S = (300, 600, 1200)
PAUSE_S = (3.0, 7.0)


def _load(path):
    return json.loads(Path(path).read_text())


def _save(path, doc):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=1) + "\n")
    return path


def _blocked(msg: str) -> bool:
    low = msg.lower()
    return any(h in low for h in BLOCK_HINTS)


def stage_plan(args) -> int:
    entries, problems = playlist.enumerate_playlist(args.playlist)
    for p in problems:
        print(f"  unparseable: {p}")
    complaints = playlist.check(entries)
    for c in complaints:
        print(f"  {c}")
    if args.check and (problems or complaints):
        print("season is not a full grid", flush=True)
        return 1

    # One metadata request per video to learn what YouTube will actually serve.
    # It is worth the minutes: two Tuesdays of this season are 720p, where a
    # stone is ~14 px against ~20 px, and nothing downstream could tell.
    print(f"{len(entries)} videos; checking what each will serve...", flush=True)
    formats = {}
    for i, e in enumerate(entries, 1):
        try:
            src = clips.resolve(e.video_id, min_height=0)
            formats[e.video_id] = {"chosen_h": src.height, "chosen_fps": src.fps,
                                   "chosen_id": src.format_id}
        except Exception as exc:  # noqa: BLE001
            print(f"  {e.video_id}: {exc}")
        if i % 20 == 0:
            print(f"  {i}/{len(entries)}", flush=True)

    doc = plan_mod.build(entries, formats, n_val=args.val_dates,
                         playlist_url=args.playlist, min_height=args.min_height)
    out = _save(args.out, doc)
    s = doc["summary"]
    print(f"wrote {out}: {s['videos']} videos over {s['dates']} dates, "
          f"{s['train_videos']} train / {s['val_videos']} val, "
          f"{s['dropped_videos']} dropped")
    print(f"  val dates: {', '.join(doc['val_dates'])}")
    return 0


def stage_clips(args) -> int:
    doc = _load(args.videos)
    videos = doc["videos"][:args.limit] if args.limit else doc["videos"]
    root = Path(args.root)
    report_path = root.parent / "clips_report.json"
    report = _load(report_path) if report_path.exists() else {}
    t_start = time.time()

    for i, v in enumerate(videos, 1):
        vid = v["video_id"]
        if report.get(vid, {}).get("ok") == args.clips:
            continue
        row = {"date": v["date"], "sheet": v["sheet"], "split": v["split"]}
        t0 = time.time()
        for attempt in range(len(BACKOFF_S) + 1):
            try:
                src = clips.resolve(vid)
                starts = clips.section_times(src.duration_s, n=args.clips)
                got, failed = clips.download_clips(
                    src, starts, args.seconds, root, jobs=args.jobs)
                row.update(ok=len(got), failed=failed, format_id=src.format_id,
                           height=src.height, fps=src.fps,
                           mb=round(sum(c.size_bytes for c in got) / 1e6, 1),
                           starts=[round(c.requested_start_s, 3) for c in got],
                           pts_starts=[round(c.pts_start_s, 3) for c in got])
                break
            except Exception as exc:  # noqa: BLE001
                msg = f"{type(exc).__name__}: {exc}"
                # A burst of ~140 metadata requests once got the whole public
                # IP blocked for a quarter of an hour. Wait it out rather than
                # spending the next hour rediscovering it.
                if _blocked(msg) and attempt < len(BACKOFF_S):
                    print(f"    blocked; sleeping {BACKOFF_S[attempt]}s", flush=True)
                    time.sleep(BACKOFF_S[attempt])
                    continue
                row.update(ok=0, error=msg[:200])
                break
        report[vid] = row
        _save(report_path, report)
        ok = sum(1 for r in report.values() if r.get("ok"))
        rate = (time.time() - t_start) / i
        print(f"[{i:3d}/{len(videos)}] {vid} {v['date']} s{v['sheet']} -> "
              f"{row.get('ok', 0)}/{args.clips} {row.get('mb', 0)}MB "
              f"(ok {ok}, eta {(len(videos) - i) * rate / 60:.0f}m) "
              f"{row.get('error', '')[:70]}", flush=True)
        time.sleep(random.uniform(*PAUSE_S))

    ok = sum(1 for r in report.values() if r.get("ok"))
    print(f"\n{ok}/{len(videos)} videos, "
          f"{sum(r.get('mb', 0) for r in report.values()) / 1000:.1f} GB")
    return 0 if ok else 1


def stage_pool(args) -> int:
    from curling_score.detect import yolo
    from curling_score.ingest import frames as F

    doc = _load(args.videos)
    videos = doc["videos"][:args.limit] if args.limit else doc["videos"]
    root, out = Path(args.root), Path(args.out)
    det = yolo.YoloDetector(args.weights, conf=args.conf, device=args.device,
                            imgsz=args.imgsz)
    det.model.overrides["half"] = True

    banked, setup_doc, t_start = {}, {}, time.time()
    for i, v in enumerate(videos, 1):
        vid = v["video_id"]
        paths = sorted((root / vid).glob("*.mkv"))
        if len(paths) < setups.MIN_LAYOUT_FRAMES:
            print(f"[{i:3d}] {vid}: only {len(paths)} clips, skipped", flush=True)
            continue
        # One frame per clip, spread across four hours: panels are found by
        # what does not change, so frames close together read a settled house
        # as a separator bar.
        timed = []
        for path in paths:
            offset = F.stream_start_s(path)
            for t, img in F.keyframe_sweep(path):
                timed.append((t + offset, img))
                break
        setup = setups.derive(vid, timed)
        setup_doc[vid] = setups.to_json(setup)
        if not setups.is_usable(setup):
            why = setup.error or "; ".join(
                f"{p.name}: {p.error}" for p in setup.panels.values())
            print(f"[{i:3d}] {vid}: UNUSABLE -- {why}", flush=True)
            continue
        cands, stats = pool_mod.build_video_pool(
            vid, paths, setup, det, out, fps=args.fps)
        banked[vid] = [M.candidate_to_json(c) for c in cands]
        _save(out / "candidates.json", banked)
        _save(out / "setups.json", setup_doc)
        print(f"[{i:3d}/{len(videos)}] {vid} {v['date']} s{v['sheet']} -> "
              f"{len(cands)} candidates, {stats['flights']} flights "
              f"({(time.time() - t_start) / i:.0f}s/video)", flush=True)

    usable = len(banked)
    print(f"\n{usable}/{len(videos)} videos usable, "
          f"{sum(len(v) for v in banked.values())} candidates")
    return 0 if usable else 1


def stage_select(args) -> int:
    plan_doc = _load(args.videos)
    banked = _load(Path(args.pool) / "candidates.json")
    splits = {v["video_id"]: v["split"] for v in plan_doc["videos"]}

    chosen = {}
    for vid, rows in banked.items():
        cands = [M.candidate_from_json(r) for r in rows]
        quota = C.quota_for(splits.get(vid, "train"), wave=args.wave)
        chosen[vid] = C.select(cands, quota=quota, backfill=args.wave != 1)

    doc = M.build_manifest(chosen, splits=splits, quota=C.quota_for("train", args.wave),
                           extra={"pool": str(args.pool), "wave": args.wave,
                                  "videos_file": str(args.videos)})
    out = _save(args.manifest, doc)
    s = doc["summary"]
    print(f"wrote {out}: {s['frames']} frames "
          f"({s['train_frames']} train / {s['val_frames']} val) "
          f"from {s['videos']} videos")
    print(f"  wave {args.wave}, bins: {s['bins']}")
    if s["videos_short"]:
        print(f"  {len(s['videos_short'])} video(s) could not fill every bin")
    return 0


def stage_build(args) -> int:
    doc = _load(args.manifest)
    stats = build_mod.build(doc, args.pool, args.out)
    print(f"{stats['written']} frames, {stats['labels']} labels, "
          f"{stats['empty']} confirmed-empty")
    if stats["missing"]:
        print(f"  {stats['missing']} frame(s) named by the manifest are not in "
              f"the pool -- the two disagree")
        return 1
    return 0


STAGES = {"plan": stage_plan, "clips": stage_clips, "pool": stage_pool,
          "select": stage_select, "build": stage_build}


def run(args) -> int:
    return STAGES[args.stage](args)
