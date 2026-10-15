#!/usr/bin/env python
"""Who named which game's teams what: the history behind every public name.

    ADMIN_TOKEN=... python scripts/team_name_edits.py [--game s_abc] [--limit N] [--json]

BASE_URL defaults to https://curling.dimmit.net. The admin token is the
`curling-admin-token` secret:
    ADMIN_TOKEN=$(~/google-cloud-sdk/bin/gcloud secrets versions access latest \\
        --secret=curling-admin-token --project=curling-stats-508323)

Anyone signed in may rename any game's teams, and the names are public, so
each change is kept with the account that made it. Each prints newest first
with that account, the game's page, each name before and after, and what the
names are now -- an edit somebody has since put right reads as settled. To
put a name back, open the game and name the teams again, signed in.
"""

import argparse
import json
import os
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

# As flags.py: the characters that change what a terminal does or shows. The
# server strips them from names on the way in; this is the second guard.
_BIDI = set(range(0x202A, 0x202F)) | set(range(0x2066, 0x206A))


def _unsafe(ch: str) -> bool:
    if ch in "\n\t":
        return False
    return unicodedata.category(ch) in ("Cc", "Cs") or ord(ch) in _BIDI


def _safe(text: str) -> str:
    return "".join(ch.encode("unicode_escape", "backslashreplace").decode() if _unsafe(ch)
                   else ch for ch in text)


def _name(name) -> str:
    return "(none)" if name is None else json.dumps(name, ensure_ascii=False)


def describe(edit: dict, base: str) -> str:
    """One edit as a few lines: when, which game, who, and each name's change."""
    user = edit.get("user") or {}
    who = " ".join(x for x in (user.get("name"), f"<{user['email']}>" if user.get("email")
                               else None) if x) or "(account gone)"
    lines = [f"{edit['at']}  {edit['source_id']}  {edit.get('title') or ''}".rstrip(),
             f"  by {who} ({user.get('uid')})"]
    now = edit.get("now") or {}
    for colour in ("red", "yellow"):
        if colour in (edit.get("after") or {}):
            line = (f"  {colour}: {_name(edit['before'].get(colour))}"
                    f" -> {_name(edit['after'][colour])}")
            if edit.get("now") is not None and now.get(colour) != edit["after"][colour]:
                line += f"   (now {_name(now.get(colour))})"
            lines.append(line)
    lines.append(f"  {base}/g/{edit['source_id']}/")
    return _safe("\n".join(lines))


def _call(url: str, token: str):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
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
    ap.add_argument("--game", help="one game's source id (s_...); every game's without")
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    token = os.environ.get("ADMIN_TOKEN", "").strip()
    if not token:
        print("set ADMIN_TOKEN (see this script's docstring)", file=sys.stderr)
        return 2
    base = os.environ.get("BASE_URL", "https://curling.dimmit.net").rstrip("/")
    query = {"limit": args.limit, **({"source_id": args.game} if args.game else {})}
    try:
        got = _call(f"{base}/api/admin/team-name-edits?{urllib.parse.urlencode(query)}",
                    token)["edits"]
    except OSError as err:              # URLError and HTTPError are OSErrors
        print(f"could not list team-name edits: {_why(err)}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(got, indent=1, ensure_ascii=False))
    else:
        print("\n\n".join(describe(e, base) for e in got) or "no team-name edits")
        if len(got) >= args.limit:
            print(f"\n{len(got)} shown; there may be more (raise --limit)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
