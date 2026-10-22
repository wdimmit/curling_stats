#!/usr/bin/env python
"""Pick 1,000 cached rocks and publish each as a release item for members to mark.

    docker run --rm --cpus=2 --cpu-shares=128 \\
        -v /data/wdd/curling-cache:/c1:ro -v /data/wdd/curling-cache-2:/c2:ro \\
        -v /data/wdd/scratch/label-rel:/w -e ADMIN_TOKEN -e BASE_URL -e PYTHONPATH=/w/src \\
        --entrypoint python curling-worker:local /w/scripts/label_releases.py \\
        pick --caches /c1 /c2 --n 1000 --seed 20261023 --out /w/picks.json
    ... label_releases.py publish --picks /w/picks.json --caches /c1 /c2 \\
        [--batch releases-1] [--checks datasets/releasemarks] [--limit N] [--dry-run --out DIR]

On the worker box, with this repo's scripts/ and src/ copied to
/data/wdd/scratch/label-rel. --cpus=2 and a low --cpu-shares keep the run from
competing with game processing.

`pick` lists the videos in the caches (`<cache>/videos/<video_id>.mp4`), asks the
public site which games each one holds and reads each game's timeline.json for
the rocks that have a delivery line and a release time, then chooses n of them.
It leaves out every rock already marked by hand in datasets/releasemarks, and
spreads the rest across games: each game's rocks are shuffled by the seed and
dealt round-robin, so a game of 130 rocks does not crowd out a game of 40.
Only the site's read-only API is used, one request at a time, and every reply
is kept under --scratch so a rerun asks for nothing it has.

`publish` cuts each pick with scripts/mark_release.py's `extract` (the crop that
follows the rock, ~100+ frames at 30 fps), tiles the frames into one JPEG
sprite and sends it as an item whose id is `r_` and a hash of the video, end
and rock (label_publish.opaque_id), so it says nothing about the game or
whether it is a check; those, and the per-frame readings (`o`, `yp`, the
model's own), stay in the item's origin, which members are never sent. One
rock at a time:
the frames go in a scratch folder that is deleted once the rock is uploaded,
and a rock that cannot be cut or sent is skipped with its id and why while the
rest carry on. Rerunning leaves open items alone, as for scripts/label_fp.py.
--checks adds up to --checks-limit (20) of the hand-marked `random` rocks whose
video is cached as items carrying the known mark as their `reference`; the
mark's frame is found by its time, which is the mark file's `t_release_s` plus
`o`, so a timeline reprocessed since the marking (a moved release time) still
lands on the same moment. ADMIN_TOKEN and BASE_URL are as for label_fp.py; the
token is tried first with one cheap admin read, and a refused token or an
unreachable site ends the run before any rock is cut. --dry-run needs neither,
sends nothing, and writes each sprite and item JSON to --out. --limit
publishes only the first N picks (to look at a few before the lot).

The run exits non-zero if any rock was skipped or none was published, and stops
after MAX_PUBLISH_FAILURES publishes in a row fail (the site is down, not the
rock).
"""

import argparse
import importlib.util
import json
import os
import random
import re
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import label_publish  # noqa: E402

SPRITE_COLS = 10
SPRITE_QUALITY = 85
SITE = "https://curling.dimmit.net"
FPS = 30.0
ID_OK = re.compile(r"^[A-Za-z0-9_.-]{1,120}$")
ORIGIN_KEYS = ("source_id", "video_id", "title", "end", "rock", "color", "position", "label")
PROGRESS_EVERY = 25
CHECKS_LIMIT = 20
RETRIES = 3
# Publishes that fail one after another before the run gives up.
MAX_PUBLISH_FAILURES = 5


def _mark_release():
    """scripts/mark_release.py, loaded by path as tests/test_mark_release.py does."""
    spec = importlib.util.spec_from_file_location("mark_release", _HERE / "mark_release.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def candidates(timeline: dict, source_id: str, video_id: str) -> list[dict]:
    """Every shot of the timeline's game with a delivery line and a release time."""
    out = []
    for end in (timeline.get("games") or [{}])[0].get("ends") or []:
        for s in end.get("shots") or []:
            line = s.get("line") or {}
            if not line.get("delivery") or s.get("t_release_s") is None:
                continue
            out.append({"source_id": source_id, "video_id": video_id, "end": end["number"],
                        "rock": s["number"], "color": s.get("color"),
                        "position": s.get("position"), "label": s.get("label")})
    return out


def pick(cands: list[dict], n: int, seed: int, exclude: set[tuple[str, int, int]]) -> list[dict]:
    """n rocks, spread over the sources: each source's rocks shuffled by the
    seed, then one from each source in turn (sources sorted by id) until n or
    all are used. Rocks in ``exclude`` as (video_id, end, rock) are left out."""
    rng = random.Random(seed)
    by_source = defaultdict(list)
    for c in cands:
        if (c["video_id"], c["end"], c["rock"]) not in exclude:
            by_source[c["source_id"]].append(c)
    piles = []
    for sid in sorted(by_source):
        rocks = list(by_source[sid])
        rng.shuffle(rocks)
        piles.append(rocks)
    out = []
    while len(out) < n and any(piles):
        for rocks in piles:
            if rocks and len(out) < n:
                out.append(rocks.pop())
    return out


def marked(folder: Path) -> set[tuple[str, int, int]]:
    """(video_id, end, rock) of every rock in the hand-mark files s_*.json."""
    out = set()
    for f in sorted(Path(folder).glob("s_*.json")):
        d = json.loads(f.read_text())
        for r in d.get("rocks") or []:
            out.add((d["video_id"], r["end"], r["rock"]))
    return out


def check_rocks(folder: Path, cached: set[str], limit: int = CHECKS_LIMIT) -> list[dict]:
    """Up to ``limit`` hand-marked rocks to check members against: the `random`
    ones marked off or held, whose video is cached. Each is a pick plus the
    known ``mark`` as {"mark", "o", "t_release_s"}."""
    out = []
    for f in sorted(Path(folder).glob("s_*.json")):
        d = json.loads(f.read_text())
        if d.get("video_id") not in cached:
            continue
        for r in d.get("rocks") or []:
            if r.get("chosen") != "random" or r.get("mark") not in ("off", "held"):
                continue
            if len(out) >= limit:
                return out
            out.append({"source_id": d["source_id"], "video_id": d["video_id"],
                        "title": d.get("title"), "end": r["end"], "rock": r["rock"],
                        "color": r.get("color"), "position": r.get("position"),
                        "label": r.get("label"),
                        "mark": {"mark": r["mark"], "o": r.get("o"),
                                 "t_release_s": r.get("t_release_s")}})
    return out


def make_sprite(frame_paths: list[Path], cols: int = SPRITE_COLS) -> tuple[bytes, dict]:
    """The frames tiled left to right, top to bottom, as one JPEG (q85), and
    {"cols","fw","fh","n"}. All frames must be one size."""
    import cv2
    import numpy as np
    imgs = [cv2.imread(str(p)) for p in frame_paths]
    if not imgs or any(i is None for i in imgs):
        raise ValueError("a frame could not be read")
    fh, fw = imgs[0].shape[:2]
    if any(i.shape[:2] != (fh, fw) for i in imgs):
        raise ValueError("frames differ in size")
    rows = -(-len(imgs) // cols)
    sheet = np.zeros((rows * fh, cols * fw, 3), np.uint8)
    for k, img in enumerate(imgs):
        r, c = divmod(k, cols)
        sheet[r * fh:(r + 1) * fh, c * fw:(c + 1) * fw] = img
    ok, buf = cv2.imencode(".jpg", sheet, [cv2.IMWRITE_JPEG_QUALITY, SPRITE_QUALITY])
    if not ok:
        raise ValueError("could not encode the sprite")
    return buf.tobytes(), {"cols": cols, "fw": fw, "fh": fh, "n": len(imgs)}


def _key(p: dict, check: bool = False) -> str:
    return f"{'check:' if check else ''}{p['video_id']}/e{p['end']:02d}/r{p['rock']:02d}"


def assign_ids(picks: list[dict], check: bool = False) -> None:
    """Set each pick's ``id``: ``r_`` and a hash of the video, end and rock.
    Two sources of one video that both hold the same end and rock number have
    their source folded into the hashed key so they stay apart. A check's key
    is marked, so a check and a pick of one rock are different items."""
    seen = Counter(_key(p, check) for p in picks)
    for p in picks:
        key = _key(p, check)
        if seen[key] > 1:
            key += f"/{p['source_id']}"
        p["id"] = label_publish.opaque_id("r_", key)


def reference_for(mark: dict, extracted: dict):
    """The release reference for a known mark: `held`, or `off` at the frame
    nearest the marked moment.

    ``o`` is seconds from the rock's release time, and the release time of a
    reprocessed timeline is not the one the mark was made against (the mark
    file stores its own per rock as ``t_release_s``), so the marked moment is
    carried over as an absolute time: o_ref = marked t_release + o - this
    extraction's t_release. A mark without its time is taken as it is.
    """
    if mark["mark"] == "held":
        return {"mark": "held", "frame": None}
    o = mark["o"]
    if mark.get("t_release_s") is not None and extracted.get("t_release_s") is not None:
        o = mark["t_release_s"] + o - extracted["t_release_s"]
    frames = extracted["frames"]
    best = min(range(len(frames)), key=lambda i: abs(frames[i]["o"] - o))
    return {"mark": "off", "frame": best}


def item_for(pick: dict, extracted: dict, sprite_meta: dict, batch: str, reference=None) -> dict:
    """The create call's item for one rock; the server sets payload["image"].

    The frames' readings (``o`` is where the model put the release) go in the
    origin with the rest of what members must not see; the export reads them
    from there."""
    origin = {k: pick.get(k) for k in ORIGIN_KEYS}
    origin["t_release_s"] = extracted.get("t_release_s")
    origin["frames"] = [{"o": f["o"], "yp": f["yp"]} for f in extracted["frames"]]
    item = {"id": pick.get("id") or label_publish.opaque_id("r_", _key(pick)),
            "kind": "release", "batch": batch,
            "payload": {**sprite_meta, "fps": FPS, "origin": origin}}
    if reference is not None:
        item["reference"] = reference
    return item


def _get_json(url: str, cache: Path):
    """The JSON at ``url``, from ``cache`` if it was fetched before."""
    if cache.exists():
        return json.loads(cache.read_text())
    for attempt in range(RETRIES):
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                body = r.read()
            break
        except urllib.error.HTTPError as err:       # the site answers 503 now and then
            if err.code < 500 or attempt == RETRIES - 1:
                raise
            time.sleep(2 * (attempt + 1))
    data = json.loads(body)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(body)
    return data


def fetch_games(base: str, video_id: str, scratch: Path) -> list[dict]:
    q = urllib.parse.urlencode({"video_id": video_id})
    return _get_json(f"{base}/api/games?{q}", scratch / "api" / f"games-{video_id}.json")["games"]


def fetch_timeline(base: str, source_id: str, scratch: Path) -> dict:
    return _get_json(f"{base}/g/{source_id}/timeline.json",
                     scratch / "api" / f"timeline-{source_id}.json")


def cached_videos(caches: list[str]) -> dict[str, Path]:
    """{video_id: path} for every <cache>/videos/<id>.mp4; the first cache wins."""
    out = {}
    for c in caches:
        for p in sorted((Path(c) / "videos").glob("*.mp4")):
            out.setdefault(p.stem, p)
    return out


def _pick_cmd(args, base) -> int:
    videos = cached_videos(args.caches)
    scratch = args.scratch
    exclude = marked(args.marks)
    cands, sources, failed = [], 0, 0
    for n, vid in enumerate(sorted(videos), 1):
        try:
            games = [g for g in fetch_games(base, vid, scratch) if g.get("status") == "ready"]
        except (OSError, ValueError, KeyError) as err:
            failed += 1
            print(f"{vid}: no games, {label_publish.why(err) if isinstance(err, OSError) else err}",
                  file=sys.stderr)
            continue
        for g in games:
            try:
                tl = fetch_timeline(base, g["source_id"], scratch)
            except (OSError, ValueError) as err:
                failed += 1
                print(f"{g['source_id']}: no timeline, "
                      f"{label_publish.why(err) if isinstance(err, OSError) else err}",
                      file=sys.stderr)
                continue
            got = candidates(tl, g["source_id"], vid)
            for c in got:
                c["title"] = g.get("title")
            if got:
                sources += 1
            cands.extend(got)
        if n % PROGRESS_EVERY == 0:
            print(f"  {n}/{len(videos)} videos read, {len(cands)} rocks so far", flush=True)
    picks = pick(cands, args.n, args.seed, exclude)
    spread = Counter(p["source_id"] for p in picks)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"seed": args.seed, "n": args.n, "picks": picks}, indent=1))
    print(f"{len(videos)} cached videos, {sources} games with rocks, {len(cands)} candidate "
          f"rocks ({len(exclude)} hand-marked left out), {failed} lookups failed")
    if spread:
        print(f"picked {len(picks)} from {len(spread)} games: "
              f"{min(spread.values())}-{max(spread.values())} per game, wrote {args.out}")
    else:
        print("picked nothing")
    return 0


def _one(p: dict, video: Path, tl: dict, mr, batch: str, scratch: Path, reference=None):
    """``(item, sprite bytes)`` for one pick; its frames are deleted after."""
    dest = scratch / "frames" / p["id"]
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)
    try:
        ex = mr.extract(str(video), tl, p["end"], p["rock"], dest, "")
        if not ex["frames"]:
            raise ValueError("no frames")
        sprite, meta = make_sprite([dest / f["file"] for f in ex["frames"]])
        return item_for(p, ex, meta, batch,
                        reference_for(reference, ex) if reference else None), sprite
    finally:
        shutil.rmtree(dest, ignore_errors=True)


def _publish_cmd(args, base) -> int:
    token = os.environ.get("ADMIN_TOKEN", "").strip()
    if not args.dry_run and not token:
        print("set ADMIN_TOKEN (see scripts/users.py)", file=sys.stderr)
        return 2
    if not args.dry_run:
        refused = label_publish.preflight(base, token)
        if refused:
            print(refused, file=sys.stderr)
            return 1
    videos = cached_videos(args.caches)
    batch = args.batch or f"releases-{date.today().isoformat()}"
    picks = json.loads(args.picks.read_text())["picks"]
    if args.limit is not None:
        picks = picks[:args.limit]
    assign_ids(picks)
    checks = check_rocks(args.checks, set(videos), args.checks_limit) if args.checks else []
    assign_ids(checks, check=True)
    work = [(p, None) for p in picks] + [(c, c["mark"]) for c in checks]
    mr = _mark_release()
    if args.dry_run:
        args.out.mkdir(parents=True, exist_ok=True)
    done = skipped = missing = failing = 0
    aborted = False
    for n, (p, ref) in enumerate(work, 1):
        try:
            video = videos.get(p["video_id"])
            if video is None:
                raise ValueError("video is not in the caches")
            tl = fetch_timeline(base, p["source_id"], args.scratch)
            item, sprite = _one(p, video, tl, mr, batch, args.scratch, ref)
            if args.dry_run:
                (args.out / f"{item['id']}.jpg").write_bytes(sprite)
                (args.out / f"{item['id']}.json").write_text(json.dumps(item, indent=1))
            else:
                try:
                    got = label_publish.publish(base, token, [item], {item["id"]: sprite})
                except OSError:
                    failing += 1
                    raise
                failing = 0
                missing += len(got["missing_image"])
            done += 1
        except Exception as err:      # one bad rock must not stop the rest
            skipped += 1
            why = label_publish.why(err) if isinstance(err, OSError) else \
                f"{type(err).__name__}: {err}"
            print(f"{p['id']} (video {p['video_id']} e{p['end']} r{p['rock']}): "
                  f"skipped, {why}", file=sys.stderr)
        if failing >= MAX_PUBLISH_FAILURES:
            print(f"{failing} publishes failed in a row; stopping, {len(work) - n} rocks "
                  "not tried", file=sys.stderr)
            aborted = True
            break
        if n % PROGRESS_EVERY == 0:
            print(f"  {n}/{len(work)} rocks, {skipped} skipped", flush=True)
    print(f"{done} items in batch {batch}" + (f", {skipped} skipped" if skipped else "")
          + (f", image missing {missing}" if missing else ""))
    if args.dry_run:
        print(f"dry run: wrote {done} sprites and item files to {args.out}; sent nothing")
    # Anything skipped, or nothing made, is for the owner to look at.
    return 1 if skipped or not done or aborted else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, text in (("pick", "choose the rocks"), ("publish", "cut and publish the picks")):
        p = sub.add_parser(name, help=text)
        p.add_argument("--caches", nargs="+", default=[], metavar="DIR")
        p.add_argument("--scratch", type=Path, metavar="DIR",
                       default=Path(tempfile.gettempdir()) / "label-releases",
                       help="where replies and frames are kept (default: the temp dir)")
        if name == "pick":
            p.add_argument("--n", type=int, default=1000)
            p.add_argument("--seed", type=int, default=20261023)
            p.add_argument("--marks", type=Path, default=Path("datasets/releasemarks"),
                           metavar="DIR", help="hand marks to leave out")
            p.add_argument("--out", type=Path, required=True, metavar="picks.json")
        else:
            p.add_argument("--picks", type=Path, required=True, metavar="picks.json")
            p.add_argument("--batch", help="the items' batch name (default releases-<today>)")
            p.add_argument("--checks", type=Path, metavar="DIR",
                           help="hand marks (datasets/releasemarks) to add as check items")
            p.add_argument("--checks-limit", type=int, default=CHECKS_LIMIT)
            p.add_argument("--limit", type=int, metavar="N", help="only the first N picks")
            p.add_argument("--dry-run", action="store_true",
                           help="send nothing; write each sprite and item JSON to --out")
            p.add_argument("--out", type=Path, metavar="DIR")
    args = ap.parse_args(argv)
    if not args.caches:
        print("give --caches", file=sys.stderr)
        return 2
    if args.cmd == "publish" and args.dry_run and not args.out:
        print("--dry-run needs --out", file=sys.stderr)
        return 2
    base = os.environ.get("BASE_URL", SITE).rstrip("/")
    return _pick_cmd(args, base) if args.cmd == "pick" else _publish_cmd(args, base)


if __name__ == "__main__":
    sys.exit(main())
