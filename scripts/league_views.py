#!/usr/bin/env python
"""How often each league's games are viewed.

    python scripts/league_views.py [--days 30] [--tz America/Los_Angeles] [--exclude-ip IP ...]
                                   [--save FILE | --input FILE] [--json]

A view is a page load of one game: its watch page (/g/), a chart's share
link (/s/), or the chart itself (/c/). The three are counted apart so a
league people chart is not mistaken for one people watch. A reload counts
again; the visitors column is the count that does not.

The page loads come from Cloud Run's request logs, screened exactly as
scripts/usage.py screens them (your network, scanners, bots and link
previews dropped), so this needs your gcloud login and reaches back 30 days
at most. Which game a chart or share link shows, and that game's league, come
from Firestore's `charts` and `sources` collections over its REST API with
the same login.

A game's league is the one the thinking report groups by
(thinking_report.league_of): the club's playlist, else the league at the end
of the stream title, else the label typed into the catalogue. "Other" is a
game with none of the three.

--save writes the raw log entries, client addresses included, so keep that
file out of the repo.
"""

import argparse
import ipaddress
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from curling_score.service.thinking_report import league_of

sys.path.insert(0, str(Path(__file__).resolve().parent))
import usage  # noqa: E402

GAME_PAGES = {"watch": re.compile(r"/g/([^/]+)/"), "share": re.compile(r"/s/([^/]+)/"),
              "chart": re.compile(r"/c/([^/]+)/")}
KINDS = list(GAME_PAGES)
SOURCE_FIELDS = ["title", "league", "playlist_title"]
CHART_FIELDS = ["source_id", "share_slug"]
# A chart or game deleted since it was viewed, or a chart that names no game.
NOT_ON_RECORD = "(game not on record)"

# As flags.py: the characters that change what a terminal does or shows. A
# league label is typed by whoever is signed in.
_BIDI = set(range(0x202A, 0x202F)) | set(range(0x2066, 0x206A))


def _safe(text: str) -> str:
    return "".join(ch.encode("unicode_escape", "backslashreplace").decode()
                   if unicodedata.category(ch) in ("Cc", "Cs") or ord(ch) in _BIDI else ch
                   for ch in text)


def _doc_id(doc: dict) -> str:
    return doc["name"].rsplit("/", 1)[1]


def _str(doc: dict, name: str) -> str | None:
    return ((doc.get("fields") or {}).get(name) or {}).get("stringValue")


def leagues_by_game(sources: list[dict]) -> dict[str, str]:
    """{source id: its league}, from Firestore REST `sources` documents."""
    return {_doc_id(d): league_of(SimpleNamespace(**{f: _str(d, f) for f in SOURCE_FIELDS}))[0]
            for d in sources}


def games_by_chart(charts: list[dict]) -> tuple[dict, dict]:
    """({chart id: source id}, {share slug: source id}), from `charts` documents."""
    by_id, by_slug = {}, {}
    for d in charts:
        sid = _str(d, "source_id")
        by_id[_doc_id(d)] = sid
        if slug := _str(d, "share_slug"):
            by_slug[slug] = sid
    return by_id, by_slug


def game_of(r: usage.Req, by_chart: dict, by_slug: dict) -> tuple[str, str | None] | None:
    """(which page, the source it shows), or None if `r` is not a game's page.

    The source is None for a chart or share link that is not on record."""
    kind = usage.page_kind(r)
    if kind not in GAME_PAGES:
        return None
    key = GAME_PAGES[kind].fullmatch(r.path).group(1)
    return kind, {"watch": key, "share": by_slug.get(key), "chart": by_chart.get(key)}[kind]


def report(visitors: dict, leagues: dict, by_chart: dict, by_slug: dict,
           tz: ZoneInfo | None = None) -> dict:
    """Each league's views over the window, and week by week. `tz` None is
    this machine's local time."""
    local = (lambda t: t.astimezone(tz)) if tz else (lambda t: t.astimezone())
    rows = defaultdict(lambda: {"games": set(), "visitors": set(), "views": Counter(),
                                "weeks": Counter()})
    every = {"games": set(), "visitors": set(), "views": 0}
    for v, rs in visitors.items():
        for r in rs:
            got = game_of(r, by_chart, by_slug)
            if not got:
                continue
            kind, sid = got
            row = rows[leagues.get(sid, NOT_ON_RECORD)]
            if sid in leagues:
                row["games"].add(sid)
                every["games"].add(sid)
            row["visitors"].add(v)
            row["views"][kind] += 1
            day = local(r.at).date()
            row["weeks"][day - timedelta(days=day.weekday())] += 1
            every["visitors"].add(v)
            every["views"] += 1
    weeks = sorted({w for row in rows.values() for w in row["weeks"]})
    out = [{"league": name, "games": len(row["games"]), "visitors": len(row["visitors"]),
            "views": sum(row["views"].values()), **{k: row["views"][k] for k in KINDS},
            "weeks": {w.isoformat(): row["weeks"][w] for w in weeks}}
           for name, row in rows.items()]
    out.sort(key=lambda x: (x["league"] == NOT_ON_RECORD, -x["views"], x["league"]))
    return {"weeks": [w.isoformat() for w in weeks], "leagues": out,
            "totals": {"views": every["views"], "games": len(every["games"]),
                       "visitors": len(every["visitors"])}}


def render(rep: dict, excluded: dict, window: str) -> str:
    out = [f"League views {window}", usage.dropped(excluded), ""]
    if not rep["leagues"]:
        return "\n".join([*out, "No game was viewed in the window."])
    out += [usage._table(
        ["league", "games", "visitors", "views", *KINDS, "per game"],
        [[_safe(x["league"]), x["games"], x["visitors"], x["views"], *(x[k] for k in KINDS),
          f"{x['views'] / x['games']:.1f}" if x["games"] else "-"] for x in rep["leagues"]]), ""]
    out += [usage._table(["views, week of", *(w[5:] for w in rep["weeks"])],
                         [[_safe(x["league"]), *x["weeks"].values()] for x in rep["leagues"]]), ""]
    t = rep["totals"]
    n = lambda k, word: f"{t[k]:,} {word}" + ("" if t[k] == 1 else "s")
    out += [f"{n('views', 'view')} of {n('games', 'game')} by {n('visitors', 'visitor')}.", "",
            "views = page loads, reloads included: watch = the game's /g/ page, share = a "
            "chart's /s/ link, chart = the /c/ chart page. A visitor who viewed two leagues "
            "counts in both. per game = views / games."]
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--days", type=int, default=30, help="how far back (the logs keep 30)")
    ap.add_argument("--tz", help="time zone for weeks (default: this machine's)")
    ap.add_argument("--exclude-ip", action="append", default=[], metavar="IP",
                    help="also drop this address or network, e.g. your phone")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--save", metavar="FILE",
                     help="also write the raw log entries, games and charts here")
    src.add_argument("--input", metavar="FILE", help="read a file written with --save")
    ap.add_argument("--project", default=usage.PROJECT)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    try:
        tz = ZoneInfo(args.tz) if args.tz else None
        for x in args.exclude_ip:
            ipaddress.ip_network(x, strict=False)
    except (ValueError, KeyError) as err:
        print(f"bad argument: {err}", file=sys.stderr)
        return 2
    since = datetime.now(timezone.utc) - timedelta(days=args.days)
    if args.input:
        with open(args.input) as f:
            saved = json.load(f)
        if not isinstance(saved, dict) or "sources" not in saved:
            print(f"{args.input} was not written by league_views.py --save", file=sys.stderr)
            return 2
        entries, sources, charts = saved["requests"], saved["sources"], saved["charts"]
    else:
        gcloud = usage.find_gcloud()
        if not gcloud:
            print("gcloud not found (looked on PATH and in ~/google-cloud-sdk/bin)",
                  file=sys.stderr)
            return 2
        try:
            entries = usage.fetch(gcloud, args.project, since)
        except (RuntimeError, OSError, ValueError) as err:
            print(f"could not read the request logs: {err}", file=sys.stderr)
            return 1
        try:   # without them no view has a league, so there is no report
            sources = usage.fetch_collection(gcloud, args.project, "sources", SOURCE_FIELDS)
            charts = usage.fetch_collection(gcloud, args.project, "charts", CHART_FIELDS)
        except (RuntimeError, OSError, ValueError) as err:
            print(f"could not read the games and charts: {err}", file=sys.stderr)
            return 1
        if args.save:
            with open(args.save, "w") as f:
                json.dump({"requests": entries, "sources": sources, "charts": charts}, f)
    reqs = [r for r in map(usage.parse, entries) if r]
    if args.input and reqs:
        # A saved file's window ends where the file does, not today.
        since = max(r.at for r in reqs) - timedelta(days=args.days)
    reqs = [r for r in reqs if r.at >= since]
    visitors, excluded = usage.screen(reqs, args.exclude_ip)
    rep = report(visitors, leagues_by_game(sources), *games_by_chart(charts), tz)
    if args.json:
        print(json.dumps({"excluded": excluded, **rep}, indent=1))
        return 0
    if not reqs:
        print("no requests in the window")
        return 0
    first, last = min(r.at for r in reqs), max(r.at for r in reqs)
    show = (lambda t: t.astimezone(tz)) if tz else (lambda t: t.astimezone())
    window = (f"{show(first):%Y-%m-%d %H:%M} to {show(last):%Y-%m-%d %H:%M} "
              f"({args.tz or show(last).tzname()})")
    print(render(rep, excluded, window))
    return 0


if __name__ == "__main__":
    sys.exit(main())
