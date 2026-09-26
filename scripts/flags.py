#!/usr/bin/env python
"""List and resolve the flags people have sent from the viewer.

    ADMIN_TOKEN=... python scripts/flags.py list [--status open|resolved|all] [--limit N] [--json]
    ADMIN_TOKEN=... python scripts/flags.py resolve f_abc f_def

BASE_URL defaults to https://curling.dimmit.net. The admin token is the
`curling-admin-token` secret:
    ADMIN_TOKEN=$(~/google-cloud-sdk/bin/gcloud secrets versions access latest \\
        --secret=curling-admin-token --project=curling-stats-508323)

Each flag prints with a link to the page it was sent from, opened at its
rock, and YouTube at that moment. The run id and override key say exactly
what was on screen: a chart stays on its run, but after a reprocess a /g/
link shows the new run, where end and rock numbers may differ.
"""

import argparse
import json
import os
import sys
import unicodedata
import urllib.error
import urllib.request
from datetime import datetime

PAGES = {"c": ("/c/", "chart_id"), "s": ("/s/", "share_slug"), "g": ("/g/", "source_id")}

# The characters that change what a terminal does or shows: controls (an
# escape code runs), lone surrogates, and the bidi overrides that reorder
# what follows. Not every category-C character: ZWJ (Cf) holds emoji
# together, and Cn only means "newer than this Python's Unicode tables".
_BIDI = set(range(0x202A, 0x202F)) | set(range(0x2066, 0x206A))


def _unsafe(ch: str) -> bool:
    if ch in "\n\t":
        return False
    return unicodedata.category(ch) in ("Cc", "Cs") or ord(ch) in _BIDI


def _safe(text: str) -> str:
    """`text` with anything that could drive the terminal escaped.

    The server strips these on the way in; this is the second guard, because
    a stored escape code printed raw runs in the terminal reading it."""
    return "".join(ch.encode("unicode_escape", "backslashreplace").decode() if _unsafe(ch)
                   else ch for ch in text)


def rock_link(flag: dict, base: str) -> str:
    where, place = flag["where"], flag["place"]
    prefix, field = PAGES[where["link"]]
    page = f"{base}{prefix}{where[field]}/"
    if place.get("rock") is None or place.get("end") is None:
        return page
    return f"{page}#e={place['end']}&s={place['rock']}"


def youtube_link(flag: dict) -> str | None:
    t = flag["place"].get("t_video_s")
    vid = flag["where"].get("video_id")
    return f"https://youtu.be/{vid}?t={int(t)}" if t is not None and vid else None


def describe(flag: dict, base: str) -> str:
    where, place = flag["where"], flag["place"]
    when = datetime.fromisoformat(flag["created_at"]).astimezone().strftime("%Y-%m-%d %H:%M")
    who = (flag.get("user") or {}).get("email") or "anonymous"
    game = place.get("game_index")
    # The game's number in the video. The reporter's dialog shows no game
    # number when the page holds one game, so the two never disagree.
    at = f"Game {game + 1 if game is not None else '?'}"
    if place.get("end") is not None:
        at += f" · End {place['end']}"
    if place.get("rock") is not None:
        at += f" · Rock {place['rock']}" + (f" ({place['label']})" if place.get("label") else "")
    lines = [f"{flag['id']}  {flag['status']}  {when}  {who}",
             f"  {where.get('title') or where.get('video_id')}",
             f"  {at}"]
    lines += [f"  > {line}" for line in flag["note"].splitlines() or [""]]
    lines.append(f"  {rock_link(flag, base)}")
    yt = youtube_link(flag)
    if yt:
        lines.append(f"  {yt}")
    lines.append(f"  run {where.get('run_id')} · key {place.get('key')}"
                 f" · pipeline {where.get('processing_version')}")
    return _safe("\n".join(lines))


def _call(method: str, url: str, token: str):
    req = urllib.request.Request(url, method=method,
                                 headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def _why(err: Exception) -> str:
    """An HTTP or network failure as one line: the status and the API's detail."""
    if isinstance(err, urllib.error.HTTPError):
        try:
            detail = json.loads(err.read() or b"{}").get("detail")
        except ValueError:
            detail = None
        return f"{err.code} {detail or err.reason}"
    return str(getattr(err, "reason", err))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    ls = sub.add_parser("list")
    ls.add_argument("--status", default="open", choices=("open", "resolved", "all"))
    ls.add_argument("--limit", type=int, default=200)
    ls.add_argument("--json", action="store_true")
    rs = sub.add_parser("resolve")
    rs.add_argument("ids", nargs="+")
    args = ap.parse_args(argv)
    token = os.environ.get("ADMIN_TOKEN", "").strip()
    if not token:
        print("set ADMIN_TOKEN (see this script's docstring)", file=sys.stderr)
        return 2
    base = os.environ.get("BASE_URL", "https://curling.dimmit.net").rstrip("/")
    if args.cmd == "list":
        try:
            got = _call("GET", f"{base}/api/admin/flags?status={args.status}"
                               f"&limit={args.limit}", token)["flags"]
        except OSError as err:          # URLError and HTTPError are OSErrors
            print(f"could not list flags: {_why(err)}", file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps(got, indent=1))
        else:
            print("\n\n".join(describe(f, base) for f in got) or "no flags")
            if len(got) >= args.limit:
                print(f"\n{len(got)} shown; there may be more (raise --limit)")
        return 0
    # Each id on its own: one typo must not leave the rest unresolved.
    failed = 0
    for fid in args.ids:
        try:
            f = _call("POST", f"{base}/api/admin/flags/{fid}/resolve", token)
        except OSError as err:
            print(f"{fid}: {_why(err)}", file=sys.stderr)
            failed += 1
            continue
        print(f"{f['id']} resolved")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
