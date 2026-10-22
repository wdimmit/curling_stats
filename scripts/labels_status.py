#!/usr/bin/env python
"""How the members' labelling is going: per flow, per member, and how pairs agree.

    ADMIN_TOKEN=... python scripts/labels_status.py
    ADMIN_TOKEN=... python scripts/labels_status.py --kind box

BASE_URL defaults to https://curling.dimmit.net. The admin token is the
`curling-admin-token` secret:
    ADMIN_TOKEN=$(~/google-cloud-sdk/bin/gcloud secrets versions access latest \\
        --secret=curling-admin-token --project=curling-stats-508323)

Read-only, and nothing is downloaded but the item listings: for each flow, the
items by state and by how many labels they have; how the finished pairs came
out (train/labelagree.py, the rules the export uses); and per member, labels,
median seconds, can't-tells, for boxes how often nothing was changed, and how
they did on the check items whose answer is already known. To get the labels
themselves, run scripts/labels_export.py.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from curling_score.train.labelagree import (  # noqa: E402
    agrees_with_reference, box_verdict, release_verdict)

KINDS = ("box", "release")
STATES = ("open", "done", "retired", "pending")
PAGE = 500


def _call(url: str, token: str):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=60) as r:
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


def fetch(base: str, token: str, kind: str) -> list[dict]:
    """Every item of a flow, in every state, with its answers."""
    items = []
    for state in STATES:
        after = None
        while True:
            q = {"kind": kind, "state": state, "limit": PAGE}
            if after:
                q["after"] = after
            got = _call(f"{base}/api/admin/label-items?{urllib.parse.urlencode(q)}", token)
            items += got["items"]
            after = got.get("after")
            if not after or not got["items"]:
                break
    return items


def _changed_nothing(answer: dict) -> bool:
    """A box answer that only kept what the model drew (the rubber stamp)."""
    return (not answer.get("cant_tell")
            and all(s in ("untouched", "kept") for s in answer.get("starts", []))
            and not any(b.get("start") is None for b in answer.get("boxes", [])))


def _cant_tell(kind: str, answer: dict) -> bool:
    return bool(answer.get("cant_tell")) if kind == "box" else answer.get("mark") == "cant_tell"


def summarise(kind: str, items: list[dict], names: dict) -> list[str]:
    """The report for one flow, as lines."""
    states = Counter(i["state"] for i in items)
    counts = Counter(i["n_labels"] for i in items)
    out = [f"{'Rock boxes' if kind == 'box' else 'Release marks'}: {len(items)} items",
           "  " + ", ".join(f"{states[s]} {s}" for s in STATES if states[s]),
           "  labels per item: " + ", ".join(f"{counts[n]} with {n}" for n in sorted(counts))]

    verdict = box_verdict if kind == "box" else release_verdict
    pairs = Counter()
    for it in items:
        if it["state"] == "done" and not it.get("reference") and len(it["answers"]) >= 2:
            a, b = sorted(it["answers"], key=lambda x: x["at"])[:2]
            v = verdict(a["answer"], b["answer"])
            pairs["agree" if v["agree"] else v["why"]] += 1
    if pairs:
        out.append(f"  finished pairs: {sum(pairs.values())} -- "
                   + ", ".join(f"{n} {why}" for why, n in pairs.most_common()))

    per = defaultdict(lambda: {"n": 0, "secs": [], "cant": 0, "stamp": 0, "checks": 0, "right": 0})
    for it in items:
        for a in it["answers"]:
            m = per[a["uid"]]
            m["n"] += 1
            m["secs"].append(a["seconds"])
            m["cant"] += _cant_tell(kind, a["answer"])
            if kind == "box":
                m["stamp"] += _changed_nothing(a["answer"])
            if it.get("reference"):
                m["checks"] += 1
                m["right"] += agrees_with_reference(kind, a["answer"], it["reference"])
    for uid, m in sorted(per.items(), key=lambda kv: -kv[1]["n"]):
        secs = sorted(m["secs"])
        bits = [f"{m['n']} labels", f"median {secs[len(secs) // 2]:.0f} s", f"{m['cant']} can't tell"]
        if kind == "box":
            bits.append(f"{m['stamp']} changed nothing")
        bits.append(f"checks {m['right']}/{m['checks']}" if m["checks"] else "no checks yet")
        out.append(f"  {names.get(uid) or uid}: " + ", ".join(bits))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--kind", choices=KINDS, help="one flow only")
    args = ap.parse_args(argv)
    token = os.environ.get("ADMIN_TOKEN", "").strip()
    if not token:
        print("set ADMIN_TOKEN (see this script's docstring)", file=sys.stderr)
        return 2
    base = os.environ.get("BASE_URL", "https://curling.dimmit.net").rstrip("/")
    try:
        names = {u["id"]: u.get("email") or u.get("name")
                 for u in _call(f"{base}/api/admin/users?limit=1000", token)["users"]}
        for n, kind in enumerate([args.kind] if args.kind else KINDS):
            print(("\n" if n else "") + "\n".join(summarise(kind, fetch(base, token, kind), names)))
    except OSError as err:              # URLError and HTTPError are OSErrors
        print(f"could not read the labelling: {_why(err)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
