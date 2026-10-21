#!/usr/bin/env python
"""Move chart-level doubles swaps onto their games.

    ADMIN_TOKEN=... python scripts/switched_ends.py            # what would move
    ADMIN_TOKEN=... python scripts/switched_ends.py --apply    # move it

Before switched ends lived on the game (Source.switched_ends), a chart's End
box kept a doubles team's role swap as an end-level override on that chart.
This copies each onto its game and takes it off the chart, so the game holds
one answer per end (POST /api/admin/switched-ends/migrate). Run it without
--apply first and read what it lists:
  moved       copied to the game
  normalised  the chart swapped the first end, so the game is given every
              other end; the plays listed now name the other player
  conflict    the game already says something else: nothing changes
  other run   a chart on an older run: left in place

BASE_URL defaults to https://curling.dimmit.net. The admin token is the
`curling-admin-token` secret:
    ADMIN_TOKEN=$(~/google-cloud-sdk/bin/gcloud secrets versions access latest \\
        --secret=curling-admin-token --project=curling-stats-508323)
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

SECTIONS = (("moved", "moved"), ("normalised", "normalised"), ("conflicts", "conflict"),
            ("other_runs", "other run"))


def lines(body: dict, base: str) -> list[str]:
    """What the migration answered, one item a line, each with its game and
    chart, then whether anything changed."""
    out = []
    for key, name in SECTIONS:
        for m in body.get(key) or []:
            ends = ", ".join(str(n) for n in m.get("ends") or []) or "none"
            line = (f"{name}: {m['color']} ends {ends} — {base}/g/{m['source_id']}/"
                    f" (chart {base}/s/{m['chart']}/)")
            if m.get("plays"):
                line += f"; plays saved from it: {', '.join(m['plays'])}"
            out.append(line)
    if not out:
        out.append("nothing to move")
    out.append("applied" if body.get("applied") else
               "dry run: nothing changed; run again with --apply")
    return out


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
    ap.add_argument("--apply", action="store_true", help="move them, not just list them")
    args = ap.parse_args(argv)
    token = os.environ.get("ADMIN_TOKEN", "").strip()
    if not token:
        print("set ADMIN_TOKEN (see this script's docstring)", file=sys.stderr)
        return 2
    base = os.environ.get("BASE_URL", "https://curling.dimmit.net").rstrip("/")
    req = urllib.request.Request(
        f"{base}/api/admin/switched-ends/migrate?apply={'true' if args.apply else 'false'}",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        data=b"{}", method="POST")
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            body = json.load(r)
    except OSError as err:              # URLError and HTTPError are OSErrors
        print(f"could not reach the service: {_why(err)}", file=sys.stderr)
        return 1
    print("\n".join(lines(body, base)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
