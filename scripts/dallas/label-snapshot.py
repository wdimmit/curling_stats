#!/usr/bin/env python3
"""Every few minutes, save the local label server's items and answers to disk.

The server keeps everything in memory, so a restart would lose the labels.
This keeps a snapshot whenever the number of answers changes: snapshots/<UTC
time>.json, every item of every state with its answers, as the admin listing
gives them. Stdlib only; the admin token is read from ./admin_token.

    nohup python3 label-snapshot.py > snapshot.log 2>&1 &
"""

import json
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE, EVERY_S = "http://localhost:8797", 300
STATES = ("pending", "open", "done", "retired")
here = Path(__file__).resolve().parent
token = (here / "admin_token").read_text().strip()
out = here / "snapshots"
out.mkdir(exist_ok=True)


def items(kind, state):
    after, got = None, []
    while True:
        url = f"{BASE}/api/admin/label-items?kind={kind}&state={state}&limit=500" + (f"&after={after}" if after else "")
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=60) as r:
            page = json.loads(r.read())
        got += page["items"]
        if len(page["items"]) < 500:
            return got
        after = page["after"]


last = -1
while True:
    try:
        snap = {s: items("box", s) for s in STATES}
        n = sum(len(i["answers"]) for s in snap.values() for i in s)
        if n != last:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            (out / f"{stamp}.json").write_text(json.dumps(snap))
            print(stamp, n, "answers", flush=True)
            last = n
    except Exception as err:  # the server restarting, say: try again next time
        print("snapshot failed:", err, flush=True)
    time.sleep(EVERY_S)
