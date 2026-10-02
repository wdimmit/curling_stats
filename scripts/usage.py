#!/usr/bin/env python
"""How many people use the site, and how often they chart rather than watch.

    python scripts/usage.py [--days 30] [--tz America/Los_Angeles] [--exclude-ip IP ...]
                            [--save FILE | --input FILE] [--json]

It reads Cloud Run's own request logs with `gcloud logging read`, so it
needs your gcloud login, not the admin token. Cloud Logging keeps them 30
days, so no report reaches further back than that.

A visitor is an address plus its browser, which makes the count rough:
- a phone on mobile data can show up as several visitors as its carrier
  hands it new addresses (IPv6 is folded to its /64, which takes care of
  most of that), and
- people behind one address with the same browser count once.

Dropped before anything is counted:
- your network: any address that called /api/admin/ or /api/worker/ (your
  scripts, the nightly export, the worker box). Your phone off wifi is
  not caught; pass its address, or for IPv6 its /64, with --exclude-ip.
- scanners: any address that got a 4xx probing .env, .php, wp-* and the like.
- bots and link previews: crawler user agents, and anything that fetched a
  page without the scripts and data a browser goes on to fetch.

Charting is starting a chart, submitting a video, or saving grading on a
/c/ link. Everything else a visitor does is viewing. A session is a run of
one visitor's page loads and charting with no 30-minute gap; it is a
charting session if any charting happened in it.

--save writes the raw log entries, client addresses included, so keep that
file out of the repo.
"""

import argparse
import ipaddress
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

PROJECT = "curling-stats-508323"
SERVICE = "curling-chart"
SESSION_GAP = timedelta(minutes=30)
FIELDS = ("timestamp", "httpRequest.remoteIp", "httpRequest.requestMethod",
          "httpRequest.status", "httpRequest.requestUrl", "httpRequest.userAgent")

# The HTML pages, by what a visitor came to do. Everything else is the
# scripts, data and polling those pages go on to fetch.
PAGES = [("catalogue", re.compile(r"/(games)?")),
         ("watch", re.compile(r"/g/[^/]+/")),
         ("chart", re.compile(r"/c/[^/]+/")),
         ("share", re.compile(r"/s/[^/]+/")),
         ("submit", re.compile(r"/submit")),
         ("mine", re.compile(r"/mine")),
         ("thinking", re.compile(r"/thinking")),
         ("join", re.compile(r"/join/[^/]+"))]
PAGE_KINDS = [kind for kind, _ in PAGES]

# (action, method, path, the status that means it happened). A 409 chart
# start is "you already have one" and a /s/ grading save is a 403: neither
# changed anything.
ACTIONS = [("chart_started", "POST", re.compile(r"/api/charts"), {201}),
           ("video_submitted", "POST", re.compile(r"/api/submissions"), {201}),
           ("grading_saved", "POST", re.compile(r"/c/([^/]+)/overrides\.json"), {200}),
           ("game_details", "POST", re.compile(r"/api/games/[^/]+/(scores|teams|league)"), {200})]
# Reopening a chart you already had is still sitting down to chart.
CHARTING = {"chart_started", "chart_reopened", "video_submitted", "grading_saved"}

CHART_PATH = re.compile(r"/c/([^/]+)/")
REOPEN_WINDOW = timedelta(minutes=2)
SUBRESOURCE = re.compile(r"\.(js|css|json|woff2)$|^/api/")
OWNER_PATH = re.compile(r"^/api/(admin|worker)/")
SCAN_PATH = re.compile(r"\.(env|php|git|aspx?|jsp|cgi|sql|bak|old|swp)\b|/\.(aws|ssh|svn|hg)\b"
                       r"|wp-|/wp\d*/|/wordpress|/blog/|xmlrpc|phpmyadmin|/cgi-bin/|/actuator",
                       re.IGNORECASE)
BOT_UA = re.compile(r"bot|crawl|spider|slurp|preview|expanding|facebookexternalhit|curl|wget"
                    r"|python|httpx|go-http|java/|okhttp|libwww|wordpress|scheduler|headless"
                    r"|censys|sniffer|scan|zgrab|whatsapp|dalvik|networkingextension",
                    re.IGNORECASE)


@dataclass(frozen=True)
class Req:
    at: datetime
    ip: str
    method: str
    status: int
    path: str
    ua: str


def parse(entry: dict) -> Req | None:
    """One `gcloud logging read --format=json` entry, or None if it is not a request."""
    http = entry.get("httpRequest") or {}
    if not http.get("requestUrl") or not entry.get("timestamp"):
        return None
    at = datetime.fromisoformat(entry["timestamp"].replace("Z", "+00:00"))
    return Req(at=at, ip=http.get("remoteIp", ""), method=http.get("requestMethod", ""),
               status=int(http.get("status") or 0), path=urlsplit(http["requestUrl"]).path,
               ua=http.get("userAgent", ""))


def network(ip: str) -> str:
    """The address a visitor is known by: IPv6 folded to its /64, IPv4 as it is."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    if addr.version == 6:
        return str(ipaddress.ip_network(f"{addr}/64", strict=False))
    return str(addr)


def browser(ua: str) -> str:
    """The user agent with its version numbers blanked, so an update is not a new visitor."""
    return re.sub(r"\d+(?:[._]\d+)*", "#", ua)


def page_kind(r: Req) -> str | None:
    if r.method != "GET" or not (200 <= r.status < 300 or r.status == 304):
        return None
    return next((kind for kind, rx in PAGES if rx.fullmatch(r.path)), None)


def action(r: Req) -> tuple[str, str | None] | None:
    """(action, the chart it graded), or None if the request changed nothing."""
    for name, method, rx, ok in ACTIONS:
        m = rx.fullmatch(r.path)
        if m and r.method == method and r.status in ok:
            return name, m.group(1) if name == "grading_saved" else None
    return None


def is_bot(ua: str) -> bool:
    return not ua or bool(BOT_UA.search(ua))


def _in(ip: str, nets) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in n for n in nets)


def screen(reqs: list[Req], exclude=()) -> tuple[dict, dict]:
    """Split the log into the visitors worth counting and what was dropped.

    Returns ({visitor: [its requests, oldest first]}, exclusion counts)."""
    extra = [ipaddress.ip_network(x, strict=False) for x in exclude]
    owner = {r.ip for r in reqs if OWNER_PATH.match(r.path) and "Scheduler" not in r.ua}
    scanners = {r.ip for r in reqs if 400 <= r.status < 500 and SCAN_PATH.search(r.path)}
    dropped = Counter()
    by_visitor = defaultdict(list)
    for r in reqs:
        if r.ip in owner or _in(r.ip, extra):
            dropped["owner"] += 1
        elif r.ip in scanners:
            dropped["scanner"] += 1
        elif is_bot(r.ua):
            dropped["bot"] += 1
        else:
            by_visitor[(network(r.ip), browser(r.ua))].append(r)
    # A browser goes on to fetch the page's scripts and data; a link
    # preview takes the HTML and the favicon and stops.
    visitors, previews = {}, 0
    for v, rs in by_visitor.items():
        acted = any(action(r) for r in rs)
        if acted or (any(page_kind(r) for r in rs) and any(SUBRESOURCE.search(r.path) for r in rs)):
            visitors[v] = sorted(rs, key=lambda r: r.at)
        else:
            previews += 1
            dropped["preview"] += len(rs)
    excluded = {"owner_addresses": len(owner) + len(extra), "owner_requests": dropped["owner"],
                "scanner_addresses": len(scanners), "scanner_requests": dropped["scanner"],
                "bot_requests": dropped["bot"], "preview_clients": previews,
                "preview_requests": dropped["preview"]}
    return visitors, excluded


def sessions(rs: list[Req]) -> list[tuple[datetime, bool]]:
    """(start, charted) for each of one visitor's sessions, from its page loads and charting."""
    out, last = [], None
    for r in rs:
        act = action(r)
        if not (act or page_kind(r)):
            continue
        if last is None or r.at - last >= SESSION_GAP:
            out.append([r.at, False])
        if act and act[0] in CHARTING:
            out[-1][1] = True
        last = r.at
    return [tuple(s) for s in out]


def reopens(rs: list[Req], first_seen: dict) -> set[Req]:
    """The chart starts in one visitor's requests that handed back a chart they had.

    /api/charts answers 201 for "here is the chart you already have" as well
    as for a new one. The /c/ page the browser goes to next tells them
    apart: a chart that was in the log before the press is a reopen. One
    made before the window opened and not visited since reads as new."""
    out = set()
    for i, r in enumerate(rs):
        if (action(r) or (None,))[0] != "chart_started":
            continue
        nxt = next((n for n in rs[i + 1:] if n.at - r.at <= REOPEN_WINDOW
                    and page_kind(n) == "chart"), None)
        if nxt and first_seen[CHART_PATH.match(nxt.path).group(1)] < r.at:
            out.add(r)
    return out


def _empty() -> dict:
    return {"visitors": set(), "charted": set(), "chart_sessions": 0, "view_sessions": 0,
            "views": Counter(), "chart_started": 0, "chart_reopened": 0, "video_submitted": 0,
            "grading_saved": 0, "charts_graded": set(), "game_details": 0}


def report(visitors: dict, tz: ZoneInfo | None = None) -> dict:
    """Daily and weekly counts. `tz` None is this machine's local time."""
    local = (lambda t: t.astimezone(tz)) if tz else (lambda t: t.astimezone())
    days = defaultdict(_empty)
    first_seen = {}
    for rs in visitors.values():
        for r in rs:
            if m := CHART_PATH.match(r.path):
                first_seen[m.group(1)] = min(first_seen.get(m.group(1), r.at), r.at)
    starters = Counter()
    for v, rs in visitors.items():
        again = reopens(rs, first_seen)
        for r in rs:
            kind, act = page_kind(r), action(r)
            if not (kind or act):
                continue
            d = days[local(r.at).date()]
            d["visitors"].add(v)
            if kind:
                d["views"][kind] += 1
            if act:
                name, chart = act
                if r in again:
                    name = "chart_reopened"
                elif name == "chart_started":
                    starters[v] += 1
                d[name] += 1
                if chart:
                    d["charts_graded"].add(chart)
                if name in CHARTING:
                    d["charted"].add(v)
        for start, charted in sessions(rs):
            days[local(start).date()]["chart_sessions" if charted else "view_sessions"] += 1
    weeks = defaultdict(_empty)
    for day, d in days.items():
        w = weeks[day - timedelta(days=day.weekday())]
        for k, val in d.items():
            if isinstance(val, set):
                w[k] |= val
            elif isinstance(val, Counter):
                w[k].update(val)
            else:
                w[k] += val

    def rows(buckets: dict) -> list[dict]:
        out, seen = [], set()
        for key in sorted(buckets):
            b = buckets[key]
            out.append({"start": key.isoformat(), "visitors": len(b["visitors"]),
                        "charted": len(b["charted"]),
                        "viewed_only": len(b["visitors"] - b["charted"]),
                        "returning": len(b["visitors"] & seen),
                        "chart_sessions": b["chart_sessions"], "view_sessions": b["view_sessions"],
                        "page_views": sum(b["views"].values()),
                        "views": {k: b["views"][k] for k in PAGE_KINDS},
                        "charts_started": b["chart_started"],
                        "charts_reopened": b["chart_reopened"],
                        "charts_graded": len(b["charts_graded"]),
                        "grading_saves": b["grading_saved"],
                        "videos_submitted": b["video_submitted"],
                        "game_details": b["game_details"]})
            seen |= b["visitors"]
        return out

    active_days = Counter(v for d in days.values() for v in d["visitors"])
    counted = set(active_days)
    charters = set().union(*(d["charted"] for d in days.values())) if days else set()
    all_sessions = [s for v in counted for s in sessions(visitors[v])]
    return {"days": rows(days), "weeks": rows(weeks),
            "totals": {"visitors": len(counted), "charted": len(charters),
                       "viewed_only": len(counted - charters),
                       "came_back": sum(1 for n in active_days.values() if n >= 2),
                       "median_days_active": statistics.median(active_days.values())
                       if active_days else 0,
                       "chart_sessions": sum(1 for _, c in all_sessions if c),
                       "view_sessions": sum(1 for _, c in all_sessions if not c),
                       "charts_started": sum(starters.values()),
                       "charts_reopened": sum(d["chart_reopened"] for d in days.values()),
                       "charts_per_starter": round(sum(starters.values()) / len(starters), 1)
                       if starters else 0}}


def _table(headers: list[str], rows: list[list]) -> str:
    widths = [max(len(str(x)) for x in col) for col in zip(headers, *rows)]
    line = lambda cells: "  ".join(str(c).rjust(w) if i else str(c).ljust(w)
                                   for i, (c, w) in enumerate(zip(cells, widths)))
    return "\n".join([line(headers), *(line(r) for r in rows)])


def render(rep: dict, excluded: dict, window: str) -> str:
    ex = excluded
    addresses = lambda n: f"{n} address" + ("" if n == 1 else "es")
    out = [f"Usage {window}",
           f"Dropped: your network ({addresses(ex['owner_addresses'])}, "
           f"{ex['owner_requests']:,} requests), scanners ({addresses(ex['scanner_addresses'])}, "
           f"{ex['scanner_requests']:,} requests), bots ({ex['bot_requests']:,} requests), "
           f"link previews ({ex['preview_clients']} clients)", ""]
    cols = ["visitors", "charted", "viewed only", "returning", "chart sess", "view sess",
            "page views", "started", "graded", "submitted", "details"]

    def row(b):
        return [b["start"], b["visitors"], b["charted"], b["viewed_only"], b["returning"],
                b["chart_sessions"], b["view_sessions"], b["page_views"], b["charts_started"],
                b["charts_graded"], b["videos_submitted"], b["game_details"]]

    out += [_table(["week of", *cols], [row(w) for w in rep["weeks"]]), ""]
    out += [_table(["week of", *PAGE_KINDS],
                   [[w["start"], *(w["views"][k] for k in PAGE_KINDS)] for w in rep["weeks"]]), ""]
    out += [_table(["day", *cols], [row(d) for d in rep["days"]]), ""]
    t = rep["totals"]
    out += [f"{t['visitors']} visitors: {t['charted']} charted at least once, "
            f"{t['viewed_only']} only viewed; {t['came_back']} came back on another day "
            f"(median days active: {t['median_days_active']}).",
            f"{t['chart_sessions']} charting sessions, {t['view_sessions']} viewing sessions; "
            f"{t['charts_started']} charts started, {t['charts_per_starter']} per person "
            f"who started one; {t['charts_reopened']} more presses of Chart reopened a chart "
            f"the person already had.",
            "",
            "started = charts started, graded = charts that got grading saved, "
            "submitted = videos submitted, details = scores/teams/league entered. "
            "Returning counts only from the window's start. Until 2026-09-30, / was "
            "the submit page, so earlier submit views are under catalogue."]
    return "\n".join(out)


def find_gcloud() -> str | None:
    found = shutil.which("gcloud")
    if found:
        return found
    home = os.path.expanduser("~/google-cloud-sdk/bin/gcloud")
    return home if os.path.exists(home) else None


def fetch(gcloud: str, project: str, since: datetime) -> list[dict]:
    log_filter = " AND ".join([
        'resource.type="cloud_run_revision"',
        f'resource.labels.service_name="{SERVICE}"',
        'logName:"run.googleapis.com%2Frequests"',
        f'timestamp>="{since.strftime("%Y-%m-%dT%H:%M:%SZ")}"',
        'NOT httpRequest.requestUrl:"/api/worker/"',
        'NOT httpRequest.userAgent:"Google-Cloud-Scheduler"'])
    got = subprocess.run([gcloud, "logging", "read", log_filter, f"--project={project}",
                          f"--format=json({','.join(FIELDS)})"],
                         capture_output=True, text=True)
    if got.returncode:
        raise RuntimeError(got.stderr.strip() or f"gcloud exited {got.returncode}")
    return json.loads(got.stdout or "[]")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--days", type=int, default=30, help="how far back (the logs keep 30)")
    ap.add_argument("--tz", help="time zone for days and weeks (default: this machine's)")
    ap.add_argument("--exclude-ip", action="append", default=[], metavar="IP",
                    help="also drop this address or network, e.g. your phone")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--save", metavar="FILE", help="also write the raw log entries here")
    src.add_argument("--input", metavar="FILE", help="read entries saved with --save")
    ap.add_argument("--project", default=PROJECT)
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
            entries = json.load(f)
    else:
        gcloud = find_gcloud()
        if not gcloud:
            print("gcloud not found (looked on PATH and in ~/google-cloud-sdk/bin)",
                  file=sys.stderr)
            return 2
        try:
            entries = fetch(gcloud, args.project, since)
        except (RuntimeError, OSError, ValueError) as err:
            print(f"could not read the request logs: {err}", file=sys.stderr)
            return 1
        if args.save:
            with open(args.save, "w") as f:
                json.dump(entries, f)
    reqs = [r for r in map(parse, entries) if r]
    if args.input and reqs:
        # A saved file's window ends where the file does, not today.
        since = max(r.at for r in reqs) - timedelta(days=args.days)
    reqs = [r for r in reqs if r.at >= since]
    visitors, excluded = screen(reqs, args.exclude_ip)
    rep = report(visitors, tz)
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
