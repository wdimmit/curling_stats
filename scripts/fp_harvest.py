#!/usr/bin/env python
"""List the false-positive frames the hosted workers have saved.

    python scripts/fp_harvest.py list
    python scripts/fp_harvest.py list --since 2026-10-20
    python scripts/fp_harvest.py list --caches /data/wdd/curling-cache /other/cache

With WORKER_FP_HARVEST=1 a worker saves a few whole frames per game where the
pipeline's own checks found the rock detectors wrong, under
<cache>/harvest/fp/<video_id>/ (the .jpg files and a frames.jsonl of facts).
Neither worker mounts the other's cache, so both may save a pile for the same
video and models: the listing keeps one pile per (video, models), the one
saved earliest, and counts the rest as duplicates.

Prints the frames kept by check, by ISO week saved, by camera and by league
(the part of the title after its last " - "), then the disk each cache's
harvest takes and the duplicate count. --caches defaults to the two worker
caches on the box.

Operating it: frames live in <cache root>/harvest/fp/<video_id>/
(/data/wdd/curling-cache and /data/wdd/curling-cache-2 on the host) and the
pruner never deletes them. Each cache is capped by WORKER_FP_HARVEST_GB (20
each, so about 40 GB across both workers); when over, the worker stops saving
and logs it once. The files are root-owned but world-readable. The switch is
WORKER_FP_HARVEST in the compose file's environment:, which overrides
worker.env.
"""

import argparse
import json
import sys
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

from curling_score.fpframes import store

DEFAULT_CACHES = ("/data/wdd/curling-cache", "/data/wdd/curling-cache-2")


def load(caches) -> list[dict]:
    """Every saved row under each cache, each with a "cache" key naming where."""
    rows = []
    for cache in caches:
        base = Path(cache) / "harvest" / "fp"
        try:
            videos = sorted(p.name for p in base.iterdir() if p.is_dir())
        except OSError:
            continue
        for video in videos:
            for r in store.read_rows(cache, video):
                rows.append(dict(r, cache=str(cache)))
    return rows


def _models_key(row: dict) -> str:
    return json.dumps(row.get("models"), sort_keys=True, default=str)


def _saved(row: dict) -> str:
    # ISO-8601 UTC strings sort in time order; a row without one sorts last.
    return row.get("saved_at") or "~"


def dedupe(rows) -> tuple[list[dict], list[dict]]:
    """(kept, duplicates): per (video, models) only the rows of the cache that
    saved first, by its earliest saved_at; a tie goes to the cache named first."""
    first: dict[tuple, tuple] = {}
    for r in rows:
        key = (r.get("video_id"), _models_key(r))
        rank = (_saved(r), str(r.get("cache")))
        if key not in first or rank < first[key]:
            first[key] = rank
    kept, dupes = [], []
    for r in rows:
        key = (r.get("video_id"), _models_key(r))
        (kept if str(r.get("cache")) == first[key][1] else dupes).append(r)
    return kept, dupes


def league(title) -> str:
    """The part of the title after its last " - ", or "?"."""
    if not title or " - " not in title:
        return "?"
    return title.rsplit(" - ", 1)[1].strip() or "?"


def _week(saved_at) -> str:
    try:
        dt = datetime.fromisoformat(str(saved_at))
    except ValueError:
        return "?"
    iso = dt.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def summary(rows) -> dict:
    """Counts by check, ISO week of saved_at, camera and league."""
    return {
        "check": dict(Counter(r.get("check") or "?" for r in rows)),
        "week": dict(Counter(_week(r.get("saved_at")) for r in rows)),
        "camera": dict(Counter(r.get("camera") or "?" for r in rows)),
        "league": dict(Counter(league(r.get("title")) for r in rows)),
    }


def _since(rows, day: date) -> list[dict]:
    cut = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    out = []
    for r in rows:
        try:
            dt = datetime.fromisoformat(str(r.get("saved_at")))
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if dt >= cut:
            out.append(r)
    return out


def report(rows, dupes, caches) -> list[str]:
    out = [f"{len(rows)} frames kept"]
    for name, counts in summary(rows).items():
        out.append(f"by {name}:")
        for k in sorted(counts):
            out.append(f"  {k}: {counts[k]}")
    out.append("disk used:")
    for cache in caches:
        out.append(f"  {cache}: {store.size_gb(cache):.3f} GB")
    out.append(f"{len(dupes)} duplicate{'' if len(dupes) == 1 else 's'}"
               " (a later worker's pile for the same video and models)")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    ls = sub.add_parser("list", help="what the workers have saved")
    ls.add_argument("--since", type=date.fromisoformat, metavar="YYYY-MM-DD",
                    help="only frames saved on or after this day (UTC)")
    ls.add_argument("--caches", nargs="+", default=list(DEFAULT_CACHES), metavar="DIR")
    args = ap.parse_args(argv)

    kept, dupes = dedupe(load(args.caches))
    if args.since:
        kept, dupes = _since(kept, args.since), _since(dupes, args.since)
    print("\n".join(report(kept, dupes, args.caches)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
