#!/usr/bin/env python
"""The cameras' times against a person's own sensors, throw by throw.

    ADMIN_TOKEN=... python scripts/practice/sensor_pairs.py --session ps_...
    ADMIN_TOKEN=... python scripts/practice/sensor_pairs.py --user WHO

WHO is an account's email address or uid: every session of theirs a sensor
app pushed to. BASE_URL defaults to https://curling.dimmit.net; the admin
token is the `curling-admin-token` secret (see scripts/users.py).

For each throw the session paired with a sensor timing (service/practice.py
match_timings) it prints the camera's hog-to-hog split against the sensor's
long time, the camera's release-to-hog against the sensor's short time -- only
roughly comparable, since the release is not the back line -- and the gap
between the two hog crossings, which is the phone's clock against ours. Then
the median and p90 of the split difference, and the median gap.
"""

import argparse
import os
import statistics
import sys
import urllib.parse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from users import _call, _why, find  # noqa: E402


def rows(sessions: list) -> list[dict]:
    """One row per paired throw, from /api/admin/practice/timings."""
    out = []
    for s in sessions:
        for p in s["pairs"]:
            th, tm = p["throw"], p["timing"]
            split, long_s = th.get("split_s"), tm.get("long_s")
            rel_hog = (None if th.get("t_hog_s") is None or th.get("t_release_s") is None
                       else th["t_hog_s"] - th["t_release_s"])
            out.append({"session": s["session"]["id"], "throw": th["id"], "split_s": split,
                        "long_s": long_s,
                        "split_diff": None if split is None or long_s is None else split - long_s,
                        "release_to_hog": rel_hog, "short_s": tm.get("short_s"),
                        "gap_s": p["gap_s"]})
    return out


def _p90(values):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(0.9 * (len(ordered) - 1)))]


def summary(rs: list[dict]) -> dict:
    diffs = [r["split_diff"] for r in rs if r["split_diff"] is not None]
    gaps = [r["gap_s"] for r in rs]
    return {"pairs": len(rs), "with_split": len(diffs),
            "median_diff": statistics.median(diffs) if diffs else None,
            "p90_abs_diff": _p90([abs(d) for d in diffs]) if diffs else None,
            "median_gap": statistics.median(gaps) if gaps else None}


def _f(v, n=2):
    return "-" if v is None else f"{v:.{n}f}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    which = ap.add_mutually_exclusive_group(required=True)
    which.add_argument("--session", help="one practice session's id")
    which.add_argument("--user", help="an account's email address or uid")
    args = ap.parse_args(argv)
    token = os.environ.get("ADMIN_TOKEN", "").strip()
    if not token:
        print("set ADMIN_TOKEN (see scripts/users.py)", file=sys.stderr)
        return 2
    base = os.environ.get("BASE_URL", "https://curling.dimmit.net").rstrip("/")
    try:
        if args.session:
            query = {"session": args.session}
        else:
            u = find(_call(f"{base}/api/admin/users?limit=1000", token)["users"], args.user)
            if u is None:
                print(f"no account for {args.user!r}", file=sys.stderr)
                return 1
            query = {"user": u["id"]}
        got = _call(f"{base}/api/admin/practice/timings?{urllib.parse.urlencode(query)}", token)
    except OSError as err:
        print(f"could not reach the site: {_why(err)}", file=sys.stderr)
        return 1
    rs = rows(got["sessions"])
    unpaired = sum(len(s["unpaired"]) for s in got["sessions"])
    print(f"{'session':<20} {'throw':<10} {'split':>6} {'long':>6} {'diff':>6}"
          f" {'rel>hog':>7} {'short':>6} {'gap':>6}")
    for r in rs:
        print(f"{r['session']:<20} {r['throw']:<10} {_f(r['split_s']):>6} {_f(r['long_s']):>6}"
              f" {_f(r['split_diff']):>6} {_f(r['release_to_hog']):>7} {_f(r['short_s']):>6}"
              f" {_f(r['gap_s']):>6}")
    s = summary(rs)
    print(f"\n{s['pairs']} paired ({s['with_split']} with both splits), {unpaired} timings unpaired;"
          f" split - long median {_f(s['median_diff'], 3)} s, |diff| p90"
          f" {_f(s['p90_abs_diff'], 3)} s; clock gap median {_f(s['median_gap'])} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
