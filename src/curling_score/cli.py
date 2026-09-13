"""Command line entry point."""

import argparse
import json
import logging
import sys
from pathlib import Path

from curling_score import analyze as analyze_mod
from curling_score import weights as weights_mod
from curling_score.ingest import cache, source


def _default_weights():
    """The standard detector, or None if this checkout has no weights.

    Resolved for --help and for the default value, so the help text names the
    model that will actually run. A missing file must not stop `--help` or a
    subcommand that never detects, so the error is deferred to use.
    """
    try:
        path = weights_mod.default_path()
    except FileNotFoundError:
        return None
    return str(path) if path else None


def _analyze(args) -> int:
    doc = analyze_mod.analyze(args.url, root=args.cache_root, shot_fps=args.fps,
                              use_proxy=not args.no_proxy,
                              weights=args.weights, imgsz=args.imgsz,
                              device=args.device, start_s=args.start,
                              end_s=args.end, sheet=args.sheet,
                              skip_scoreboard=args.no_scoreboard)
    out = analyze_mod.write(doc, args.out)
    print(f"\nwrote {out}")
    for game in doc["games"]:
        board = game.get("scoreboard")
        note = ""
        if board:
            note = (
                f"   [wall board: {board['final']}"
                f"{'' if board['agrees_with_detection'] else '  MISMATCH'}]"
            )
        print(f"\nGame {game['index'] + 1}: {game['final']}{note}")
        for end in game["ends"]:
            observed = end["shots_observed"]
            print(
                f"  end {end['number']:2d} ({end['house']:6s}) "
                f"hammer={end['hammer'] or '-':6s} "
                f"score={end['score']} shots={observed}/{len(end['shots'])}"
            )
    print(f"\nView with:  curling-score serve --out {args.out}")
    return 0


def _fetch(args) -> int:
    path = cache.ensure_cached(args.url)
    print(path)
    return 0


def _review(args) -> int:
    """Render a page of windows where a delivery was probably missed."""
    from pathlib import Path

    from curling_score.analyze import _proxy_setups
    from curling_score.detect import delivery, release, sequence
    from curling_score.game import (
        fit, misses, profile, secondpass, segment, shots as shots_mod,
    )
    from curling_score.geometry import constants as C, layout
    from curling_score.ingest import frames as F, proxy
    from curling_score.train import review

    info = source.fetch_info(args.url)
    path = cache.ensure_cached(args.url)
    calib_frames = F.sample_keyframes(path, count=24, stride=90)
    panels = layout.detect_panels(calib_frames)
    setups = profile.calibrate_panels(calib_frames, panels)

    read_path, read_setups = path, setups
    if not args.no_proxy:
        strip = proxy.strip_rect(panels.top, panels.bottom)
        read_path = proxy.ensure_proxy(path, info.video_id, strip, progress=print)
        read_setups = _proxy_setups(setups, strip)

    detector = None
    if args.weights:
        from curling_score.detect import yolo

        detector = yolo.YoloDetector(args.weights, conf=0.30, device=args.device,
                                     imgsz=args.imgsz)
        detector.model.overrides["half"] = True

    games = segment.segment_games(profile.build_profile(read_path, read_setups))
    rows = []
    for gi, game in enumerate(games, 1):
        prev_end_s = None
        for end in game.ends:
            setup = read_setups[end.house]
            from_s = analyze_mod.run_up_from(prev_end_s, end.start_s)
            seq = _detect_end(read_path, setup, end, args.fps, detector, from_s)
            found = [
                d for d in delivery.find_deliveries(
                    seq, view_x_limit_m=setup.view_x_limit_m,
                    view_y_min_m=setup.view_y_min_m,
                )
                if d.t_enter >= from_s
            ]
            # Match the analysis pipeline: the constraint-guided second pass is
            # part of detection now, so the review must reflect what it found or
            # it flags problems that no longer exist.
            gaps = secondpass.gaps_to_search(found, end.start_s, end.end_s)
            recovered = secondpass.search(seq, gaps, found)
            offered = sorted(found + recovered, key=lambda d: d.t_enter)
            far = read_setups[analyze_mod.OTHER_HOUSE[end.house]]
            _releases, _thrown_by, unaccounted = release.find_and_pair(
                sequence.detect_span(read_path, far, from_s, end.end_s,
                                     release.RELEASE_FPS, detector),
                far.view_y_min_m, offered, seq, since=from_s)
            offered = sorted(offered + unaccounted, key=lambda d: d.t_enter)
            # The rules trim the candidate list before anything is built from
            # it, so the review has to review what survives -- otherwise it
            # flags gaps around candidates the pipeline has already discarded.
            found = fit.fit_end(offered)
            prev_end_s = min(end.end_s, found[-1].t_rest) if found else end.end_s
            sh = shots_mod.from_deliveries(found, seq)
            scoring = shots_mod.scoring_shot(sh)
            in_house = None
            if scoring is not None:
                in_house = sum(
                    1 for d in scoring.stones
                    if (d.x_m**2 + d.y_m**2) ** 0.5 <= C.IN_HOUSE_MAX_D_M
                )
            for c in misses.find_candidates(found, end.start_s, end.end_s):
                rows.append({
                    "game": gi, "end": end.number, "house": end.house,
                    "start_s": round(c.start_s, 1), "end_s": round(c.end_s, 1),
                    "reason": c.reason, "confidence": round(c.confidence, 3),
                    "expected_color": c.expected_color, "kind": "miss",
                })
            # The window the score is actually read from. It decides the end,
            # so it is worth confirming directly rather than only hunting misses.
            for sw in misses.find_score_windows(
                found, end.start_s, end.end_s,
                scored_at=None if scoring is None else scoring.t_rest_s,
                stones_in_house=in_house,
            ):
                rows.append({
                    "game": gi, "end": end.number, "house": end.house,
                    "start_s": round(sw.start_s, 1), "end_s": round(sw.end_s, 1),
                    "reason": sw.reason, "confidence": sw.confidence,
                    # Trust, inverted: a score window that cannot be relied on
                    # is the one worth looking at.
                    "priority": round(1.0 - sw.confidence, 3),
                    "expected_color": sw.color, "kind": "score",
                    "note": (f"{sw.later_unchecked_s:.0f}s of the end runs on "
                             f"after this with no delivery found"),
                })
            # The other direction: detections that are probably not throws.
            for sus in misses.find_suspects(found):
                rows.append({
                    "game": gi, "end": end.number, "house": end.house,
                    "start_s": round(sus.start_s, 1), "end_s": round(sus.end_s, 1),
                    "reason": sus.reason, "confidence": round(sus.confidence, 3),
                    "expected_color": sus.color, "kind": "suspect",
                })
            # Deliveries believed without their flight ever being seen.
            # The only route that catches a guard thrown short, and the only
            # one a stone parked at the delivery end can also satisfy, so it
            # is the part of the answer most in need of a human glance.
            for we in misses.find_weak_evidence(found):
                rows.append({
                    "game": gi, "end": end.number, "house": end.house,
                    "start_s": round(we.start_s, 1), "end_s": round(we.end_s, 1),
                    "reason": we.reason, "confidence": we.confidence,
                    "expected_color": we.color, "kind": "appear",
                })
            # And the candidates the rules would not allow. Each one lost a
            # contest with a neighbour of its own colour; which of the two was
            # the stone is the question an eye settles instantly.
            for cf in misses.find_conflicts(offered, found):
                rows.append({
                    "game": gi, "end": end.number, "house": end.house,
                    "start_s": round(cf.start_s, 1), "end_s": round(cf.end_s, 1),
                    "reason": cf.reason, "confidence": cf.confidence,
                    "expected_color": cf.color, "kind": "conflict",
                    "note": (f"confirmed by {cf.evidence}"
                             if cf.evidence else None),
                })
            dropped = len(offered) - len(found)
            # Flushed: redirected to a file these lines sit in the buffer,
            # and a long silent stretch is indistinguishable from a hang. One
            # such stretch was read as a hang and the run killed, when it was
            # rendering strips perfectly happily.
            print(f"  game {gi} end {end.number}: {len(found)}/16 deliveries"
                  f"{f' (+{len(recovered)} recovered)' if recovered else ''}"
                  f"{f' (-{dropped} against the rules)' if dropped else ''}"
                  f"{f', {in_house} in the house' if in_house is not None else ''}",
                  flush=True)

    page, n = review.build(read_path, read_setups, rows, Path(args.out),
                           info.video_id, detector=detector,
                           progress=lambda m: print(m, flush=True))
    total = sum(r["end_s"] - r["start_s"] for r in rows) / 60.0
    print(f"\n{n} windows, {total:.0f} min of video to review "
          f"(the stream is {info.duration_s / 60:.0f} min)")
    print(f"open {page}")
    return 0


def _detect_end(path, setup, end, fps, detector, from_s=None):
    """Detections for one end, batched, cached, and with its run-up."""
    from curling_score.detect import sequence

    return sequence.detect_end(path, setup, end, fps, detector, from_s=from_s)


def _inspect(args) -> int:
    """Render the detections over a span, so they can be judged by eye.

    Every automated measure of the detector is circular -- the model learned
    from the classical detector's labels -- so the only way to find out what it
    is actually firing on is to look. A window of game 2 end 1 held no
    deliveries at all, just people talking, and one of them was wearing red
    shoes.
    """
    from pathlib import Path

    import cv2

    from curling_score.analyze import _proxy_setups
    from curling_score.detect import sequence
    from curling_score.game import profile
    from curling_score.geometry import layout
    from curling_score.ingest import frames as F, proxy
    from curling_score.train import review

    info = source.fetch_info(args.url)
    path = cache.ensure_cached(args.url)
    calib_frames = F.sample_keyframes(path, count=24, stride=90)
    panels = layout.detect_panels(calib_frames)
    setups = profile.calibrate_panels(calib_frames, panels)

    read_path, read_setups = path, setups
    if not args.no_proxy:
        strip = proxy.strip_rect(panels.top, panels.bottom)
        read_path = proxy.ensure_proxy(path, info.video_id, strip,
                                       progress=print)
        read_setups = _proxy_setups(setups, strip)

    detector = None
    if args.weights:
        from curling_score.detect import yolo

        detector = yolo.YoloDetector(args.weights, conf=0.30,
                                     device=args.device, imgsz=args.imgsz)
        detector.model.overrides["half"] = True

    houses = [args.house] if args.house else ["top", "bottom"]
    out_dir = Path(args.out)
    (out_dir / "frames").mkdir(parents=True, exist_ok=True)
    rows = []
    for house in houses:
        setup = read_setups[house]
        seq = {t: d for t, d in sequence.detect_span(
            read_path, setup, args.start, args.end, 10.0, detector)}
        if not seq:
            continue
        times = sorted(seq)
        step = max(1, int(round(10.0 / max(args.fps, 0.01))))
        picked, labels = [], []
        for t in times[::step]:
            img = None
            for tt, frame in F.window(read_path, t, t + 0.15, 10.0,
                                      crop=setup.rect):
                img = frame
                break
            if img is None:
                continue
            picked.append(review.annotate(img, seq[t], setup.calib))
            labels.append(f"{t:.1f}s  n={len(seq[t])}")
            rows.append((house, t, seq[t]))
        sheet = review.contact_sheet(picked, labels)
        if sheet is not None:
            name = f"frames/{house}-{int(args.start)}-{int(args.end)}.jpg"
            cv2.imwrite(str(out_dir / name), sheet,
                        [cv2.IMWRITE_JPEG_QUALITY, 92])
            print(f"wrote {out_dir / name}", flush=True)

    listing = []
    for house, t, dets in rows:
        for d in dets:
            listing.append(
                f"{house:6s} {t:9.1f} {d.color:6s} "
                f"({d.x_m:+.2f},{d.y_m:+.2f}) conf {d.confidence:.2f} "
                f"area {d.area_px:.0f}px")
    (out_dir / "detections.txt").write_text("\n".join(listing) + "\n")
    print(f"{len(listing)} detections listed in {out_dir / 'detections.txt'}")
    return 0


def _train(args) -> int:
    """Train a detector with the settings every run since ds2 has used."""
    from curling_score.train.run import Run, train

    run = Run(data=Path(args.data).resolve(), project=Path(args.project),
              name=args.name, base=args.base, epochs=args.epochs,
              patience=args.patience, device=args.device)
    train(run)
    best = run.project / run.name / "weights" / "best.pt"
    print(f"\nbest weights: {best}")
    print("copy them somewhere that is not /tmp before anything reboots")
    return 0


def labels_meta(args):
    """Per-frame extras for the review page, read out of a ds11 manifest.

    The label file cannot carry any of this: the size to give a box added to an
    empty frame, a link to the moment in the video, or the frames either side
    of a stone in flight.
    """
    import json

    from curling_score.harvest import manifest as M
    from curling_score.ingest import source
    from curling_score.train.labels import FrameMeta

    doc = json.loads(Path(args.manifest).read_text())
    pool = Path(args.pool) if getattr(args, "pool", None) else None
    out = {}
    for _split, cand in M.iter_frames(doc):
        near = ()
        if pool and cand.neighbours:
            near = tuple(p for p in
                         (pool / cand.video_id / f"{n}.jpg" for n in cand.neighbours)
                         if p.is_file())
        out[cand.stem] = FrameMeta(
            box=tuple(cand.box_wh) or None,
            url=source.watch_url_at(cand.video_id, cand.t_abs),
            kind=cand.kind,
            neighbours=near)
    return out


def _labels(args) -> int:
    """Look at what the model was taught, and correct it."""
    import json

    from curling_score.train import labels

    if args.apply:
        # Review happens over sittings and in two waves, each exporting its own
        # file, so what gets applied is always a merge.
        edits = labels.merge_edits(
            *(json.loads(Path(a).read_text()) for a in args.apply))
        if edits.scope and args.scope and edits.scope != args.scope:
            print(f"refusing: these edits are scoped {edits.scope!r}, not "
                  f"{args.scope!r}", file=sys.stderr)
            return 2
        c = labels.apply_edits(args.dataset, args.split, edits.reject, edits.add,
                               keep_empty=args.keep_empty,
                               reviewed=edits.reviewed,
                               drop_unreviewed=args.drop_unreviewed)
        print(f"{c['removed']} labels removed, {c['added']} added, "
              f"across {c['rewritten']} files")
        if c["kept_empty"]:
            print(f"  {c['kept_empty']} frame(s) kept as confirmed-empty")
        if c["emptied"]:
            print(f"  {c['emptied']} frame(s) left with no labels and dropped")
        if c["unreviewed"]:
            print(f"  {c['unreviewed']} frame(s) dropped: nobody reviewed them")
        if c["missing_frames"]:
            print(f"  {c['missing_frames']} addition(s) skipped: the frame is "
                  f"no longer in the set")
        seen, total, _missing = labels.coverage(
            args.dataset, args.split, edits.reviewed)
        print(f"  {seen}/{total} frames reviewed")
        return 0

    frames = labels.load_split(args.dataset, args.split)
    n = sum(len(v) for v in frames.values())
    print(f"{n} frames in {len(frames)} sequences", flush=True)
    findings = labels.find_suspect_labels(frames)
    samples = labels.sample_frames(frames, every=args.sample_every)
    print(f"{len(findings)} suspect, {len(samples)} sampled", flush=True)
    if args.fix:
        if args.all:
            # ds11's frames are minutes apart, so every "run" has length one
            # and the suspect ranking says nothing. The set is reviewed whole.
            show = [f for seq in frames.values() for f in seq]
        else:
            # Suspect frames first, then the spread, without repeating a frame.
            seen, show = set(), []
            for f in [x.frame for x in findings] + samples:
                if f.image not in seen:
                    seen.add(f.image)
                    show.append(f)
        page, count = labels.render_clickable(
            show, args.out, title=f"Label fixing — {args.split}",
            scope=args.scope or f"{Path(args.dataset).name}:{args.split}",
            per_page=args.per_page,
            meta=labels_meta(args) if args.manifest else None)
        print(f"{count} frames to review\nopen {page}")
        print("Reject bad boxes, add missing ones, Export, then:\n"
              f"  curling-score labels {args.dataset} --split {args.split} "
              "--apply label-edits.json")
        return 0
    page, count = labels.render(findings, samples, args.out)
    print(f"{count} strips\nopen {page}")
    return 0


def _serve(args) -> int:
    from curling_score.viewer import serve

    serve(Path(args.out), port=args.port)
    return 0


def _info(args) -> int:
    print(json.dumps(source.fetch_info(args.url).__dict__, indent=2))
    return 0



def _stage_names():
    """The harvest stages, for anything that needs to name them."""
    from curling_score.harvest import stages

    return sorted(stages.STAGES)


def _harvest(args) -> int:
    """Build a season's worth of training candidates, one stage at a time.

    The stages are separate and resumable because the whole thing takes an hour
    of downloading and half an hour of GPU, and a job that has to start over
    when the network hiccups is a job nobody runs.
    """
    from curling_score.harvest import stages

    return stages.run(args)


def _add_harvest(sub):
    p = sub.add_parser(
        "harvest", help="build a training set from many games (see ds11)")
    stage = p.add_subparsers(dest="stage", required=True)

    q = stage.add_parser("plan", help="enumerate a playlist and fix the splits")
    q.add_argument("playlist")
    q.add_argument("--out", default="datasets/ds11/videos.json")
    q.add_argument("--val-dates", type=int, default=5,
                   help="whole Tuesdays to hold out (default: 5)")
    q.add_argument("--min-height", type=int, default=1080,
                   help="drop videos YouTube will not serve this tall")
    q.add_argument("--check", action="store_true",
                   help="fail unless every date has every sheet")
    q.add_argument("--all-val", action="store_true",
                   help="a held-out test set: every video is one split")

    q = stage.add_parser("clips", help="fetch the short clips each video needs")
    q.add_argument("--videos", default="datasets/ds11/videos.json")
    q.add_argument("--root", required=True, help="where clips are cached")
    q.add_argument("--clips", type=int, default=10)
    q.add_argument("--seconds", type=float, default=24.0)
    q.add_argument("--jobs", type=int, default=10,
                   help="parallel fetches; YouTube throttles per connection")
    q.add_argument("--limit", type=int)

    q = stage.add_parser("pool", help="detect over the clips and bank candidates")
    q.add_argument("--videos", default="datasets/ds11/videos.json")
    q.add_argument("--root", required=True, help="where clips are cached")
    q.add_argument("--out", required=True, help="where candidate crops go")
    q.add_argument("--weights", required=True)
    q.add_argument("--weights-extra",
                   help="union a second model's detections into the first "
                        "guess, so a set built to compare two models does not "
                        "flatter either")
    q.add_argument("--conf", type=float, default=0.25)
    q.add_argument("--imgsz", type=int, default=640)
    q.add_argument("--device", default=None)
    q.add_argument("--fps", type=float, default=5.0,
                   help="detection rate inside a clip; tracking needs several")
    q.add_argument("--limit", type=int)

    q = stage.add_parser("select", help="choose this wave's frames")
    q.add_argument("--pool", required=True)
    q.add_argument("--videos", default="datasets/ds11/videos.json")
    q.add_argument("--manifest", default="datasets/ds11/manifest.json")
    q.add_argument("--wave", type=int, default=2, choices=(1, 2, 3),
                   help="1 is the pilot: one frame per bin per video; "
                        "3 takes throws only, which no earlier set contains")

    q = stage.add_parser("build", help="write the YOLO tree from a manifest")
    q.add_argument("--manifest", default="datasets/ds11/manifest.json")
    q.add_argument("--pool", required=True)
    q.add_argument("--out", required=True)

    p.set_defaults(func=_harvest)


def main(argv=None) -> int:
    from curling_score.diagnostics import enable_stack_dumps

    enable_stack_dumps()
    parser = argparse.ArgumentParser(
        prog="curling-score",
        description="Turn a Seattle Curling Club YouTube VOD into a game timeline.",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("analyze", help="analyse a VOD and write timeline.json")
    p.add_argument("url")
    p.add_argument("--out", default="out", help="output directory (default: out)")
    p.add_argument("--fps", type=float, default=analyze_mod.SHOT_FPS)
    p.add_argument("--no-proxy", action="store_true",
                   help="decode the full frame instead of a cropped strip proxy")
    p.add_argument("--weights", default=_default_weights(),
                   help=f"a trained YOLO model to detect with "
                        f"(default: {_default_weights() or 'classical'})")
    p.add_argument("--imgsz", type=int, default=448)
    p.add_argument("--device", default=None)
    p.add_argument("--start", type=float, default=None,
                   help="analyse from this many seconds into the stream")
    p.add_argument("--end", type=float, default=None,
                   help="...up to this many seconds")
    p.add_argument("--sheet", type=int, default=None,
                   help="sheet number, when the title does not say")
    p.add_argument("--no-scoreboard", action="store_true",
                   help="skip reading the wall board (the only pass that needs "
                        "the full-resolution original)")
    p.add_argument("--cache-root", default=None,
                   help="where videos, proxies and detections are kept "
                        "(default: $CURLING_SCORE_CACHE or ~/.cache/curling_score)")
    p.set_defaults(func=_analyze)

    _add_harvest(sub)

    p = sub.add_parser("train", help="train a stone detector")
    p.add_argument("--data", required=True, help="path to curling.yaml")
    p.add_argument("--name", required=True, help="run name")
    p.add_argument("--project", default="runs")
    p.add_argument("--base", default="yolo11n.pt")
    p.add_argument("--epochs", type=int, default=40)
    p.add_argument("--patience", type=int, default=15)
    p.add_argument("--device", default=0)
    p.set_defaults(func=_train)

    p = sub.add_parser("fetch", help="download a VOD into the cache")
    p.add_argument("url")
    p.set_defaults(func=_fetch)

    p = sub.add_parser("review",
                       help="page of windows where a delivery was probably missed")
    p.add_argument("url")
    p.add_argument("--out", default="review")
    p.add_argument("--fps", type=float, default=10.0)
    p.add_argument("--weights", default=_default_weights(),
                   help=f"a trained YOLO model to detect with "
                        f"(default: {_default_weights() or 'classical'})")
    p.add_argument("--imgsz", type=int, default=448)
    p.add_argument("--device", default=None)
    p.add_argument("--no-proxy", action="store_true")
    p.set_defaults(func=_review)

    p = sub.add_parser(
        "inspect",
        help="render every detection over a span of frames, to look at")
    p.add_argument("url")
    p.add_argument("--start", type=float, required=True,
                   help="seconds into the stream")
    p.add_argument("--end", type=float, required=True)
    p.add_argument("--house", choices=("top", "bottom"),
                   help="which panel; default is whichever is in play")
    p.add_argument("--fps", type=float, default=1.0,
                   help="frames to render per second of video")
    p.add_argument("--out", default="inspect")
    p.add_argument("--weights", default=_default_weights(),
                   help=f"a trained YOLO model to detect with "
                        f"(default: {_default_weights() or 'classical'})")
    p.add_argument("--imgsz", type=int, default=640)
    p.add_argument("--device")
    p.add_argument("--no-proxy", action="store_true")
    p.set_defaults(func=_inspect)

    p = sub.add_parser(
        "labels", help="review the labels a training set was built from")
    p.add_argument("dataset", help="the dataset root, holding images/ labels/")
    p.add_argument("--split", default="train", help="train or val")
    p.add_argument("--out", default="labelreview")
    p.add_argument("--sample-every", type=int, default=40,
                   help="also show every Nth frame, to catch anything systematic")
    p.add_argument("--fix", action="store_true",
                   help="a page where labels can be clicked to reject them")
    # nargs="+" with extend, so a shell glob over a directory of sessions
    # works as written, and so does repeating the flag.
    p.add_argument("--apply", metavar="EDITS_JSON", nargs="+", action="extend",
                   help="apply exported label edits; several are merged")
    p.add_argument("--all", action="store_true",
                   help="review every frame, not just the ranked suspects")
    p.add_argument("--per-page", type=int, default=200,
                   help="frames per review page (default: 200)")
    p.add_argument("--scope", help="storage key for the review session")
    p.add_argument("--manifest",
                   help="a ds11 manifest, for deep links and box sizes")
    p.add_argument("--pool", help="the candidate pool, for motion context frames")
    p.add_argument("--keep-empty", action="store_true",
                   help="keep a frame a reviewer stripped to nothing")
    p.add_argument("--drop-unreviewed", action="store_true",
                   help="remove frames nobody ticked as done")
    p.set_defaults(func=_labels)

    p = sub.add_parser("serve", help="open the timeline viewer")
    p.add_argument("--out", default="out")
    p.add_argument("--port", type=int, default=8000)
    p.set_defaults(func=_serve)

    p = sub.add_parser("info", help="print video metadata")
    p.add_argument("url")
    p.set_defaults(func=_info)

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(message)s",
    )
    if args.verbose:
        logging.getLogger("curling_score").setLevel(logging.INFO)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
