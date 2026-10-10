"""How a practice replay's throws compare with the end pipeline's shots.

    python scripts/practice/compare_throws.py out/practice/vxu9/throws.jsonl \
        out/practice/ref-VXU9/timeline.json --from 2400 --to 4200

The reference is the per-end pipeline's timeline of the same video (`curling-
score analyze`). A throw matches the reference shot of its colour and house
that came to rest within DT_S of it and within POS_M of where it did; a throw
that never arrived matches a shot the pipeline settled as hogged, released
within DT_S. Shots thrown in the last TAIL_S before --to are left out of both
sides: the replay stops at --to, before they could be confirmed.
"""

import argparse
import json
import math
import sys
from pathlib import Path

DT_S = 3.0
POS_M = 0.5
# Two releases of one throw: the overhead's and the long camera's agree to a
# few tenths (sidereleases: -0.11..+0.23 s, 0.45 s at worst).
SAME_RELEASE_S = 1.0
TAIL_S = 45.0
# A shot with no release is dated this long before its rest.
REST_TO_THROW_S = 20.0


def _pct(values, q):
    v = sorted(values)
    if not v:
        return None
    return round(v[min(len(v) - 1, math.ceil(q * len(v)) - 1)], 3)


def _stats(values) -> dict:
    return {"n": len(values), "p50": _pct(values, 0.5), "p90": _pct(values, 0.9),
            "max": None if not values else round(max(values), 3)}


def _thrown(t_release, t_rest):
    if t_release is not None:
        return t_release
    return None if t_rest is None else t_rest - REST_TO_THROW_S


def reference(doc, t_from, t_to) -> list:
    out = []
    for game in doc["games"]:
        for end in game["ends"]:
            for s in end["shots"]:
                if s.get("missing"):
                    continue
                thrown = _thrown(s.get("t_release_s"), s.get("t_rest_s"))
                if thrown is None or not t_from <= thrown <= t_to - TAIL_S:
                    continue
                idx, stones = s.get("delivered_stone_index"), s.get("stones") or []
                stone = stones[idx] if idx is not None and idx < len(stones) else None
                at_broom = (s.get("line") or {}).get("at_broom") or {}
                out.append({"house": end["house"], "color": s["color"],
                            "t_rest_s": s.get("t_rest_s"), "t_release_s": s.get("t_release_s"),
                            "arrived": s.get("reason") != "hogged",
                            "rest": None if stone is None else (stone["x"], stone["y"]),
                            "split_s": s.get("long_split_s"),
                            "broom": s.get("target_broom") is not None,
                            "miss_m": at_broom.get("miss_m")})
    return out


def _gap(th, r):
    if r["house"] != th["house"] or r["color"] != th["color"] or r["arrived"] != th["arrived"]:
        return None
    # Both seen released: the release names the throw. A stone that ran out of
    # view has no rest to agree on -- its "rest" is when it left, on each side.
    if th["t_release_s"] is not None and r["t_release_s"] is not None:
        gap = abs(r["t_release_s"] - th["t_release_s"])
        return gap if gap <= SAME_RELEASE_S else None
    if th["arrived"]:
        if r["t_rest_s"] is None or abs(r["t_rest_s"] - th["t_rest_s"]) > DT_S:
            return None
        if r["rest"] and th["rest"] and math.dist(
                r["rest"], (th["rest"]["x"], th["rest"]["y"])) > POS_M:
            return None
        return abs(r["t_rest_s"] - th["t_rest_s"])
    if r["t_release_s"] is None or th["t_release_s"] is None:
        return None
    gap = abs(r["t_release_s"] - th["t_release_s"])
    return gap if gap <= DT_S else None


def match(throws, refs):
    pairs, used, matched = [], set(), set()
    for k, th in enumerate(throws):
        best = None
        for i, r in enumerate(refs):
            if i in used:
                continue
            gap = _gap(th, r)
            if gap is not None and (best is None or gap < best[0]):
                best = (gap, i)
        if best is not None:
            used.add(best[1])
            matched.add(k)
            pairs.append((th, refs[best[1]]))
    false = [th for k, th in enumerate(throws) if k not in matched]
    missed = [r for i, r in enumerate(refs) if i not in used]
    return pairs, false, missed


def summarize(pairs, false, missed, throws) -> dict:
    arrived = [(th, r) for th, r in pairs if th["arrived"]]
    n_ref = len(arrived) + sum(1 for r in missed if r["arrived"])
    rest = [100 * math.dist(r["rest"], (th["rest"]["x"], th["rest"]["y"]))
            for th, r in arrived if r["rest"] and th["rest"]]
    split = [abs(th["split_s"] - r["split_s"]) for th, r in arrived
             if th["split_s"] is not None and r["split_s"] is not None]
    miss = [100 * abs(th["line"]["miss_m"] - r["miss_m"]) for th, r in arrived
            if th.get("line") and th["line"].get("miss_m") is not None
            and r["miss_m"] is not None]
    broom = {"both": 0, "neither": 0, "throw_only": 0, "reference_only": 0}
    for th, r in arrived:
        mine = th.get("broom") is not None
        broom[{(True, True): "both", (False, False): "neither", (True, False): "throw_only",
               (False, True): "reference_only"}[(mine, r["broom"])]] += 1
    latency = [th["latency_s"] for th in throws if th.get("latency_s") is not None]
    unreleased = [th for th in throws if th.get("release_source") is None and th["arrived"]]
    paired_ids = {id(th) for th, _ in pairs}
    return {
        "reference_arrived": n_ref, "matched_arrived": len(arrived),
        "recall_arrived": None if not n_ref else round(len(arrived) / n_ref, 3),
        "reference_hogged": sum(1 for r in missed if not r["arrived"])
        + sum(1 for th, _ in pairs if not th["arrived"]),
        "matched_hogged": sum(1 for th, _ in pairs if not th["arrived"]),
        "false_throws": len(false), "throws": len(throws),
        "rest_cm": _stats(rest), "split_s": _stats(split), "miss_cm": _stats(miss),
        "broom": broom,
        "latency_s": _stats(latency),
        "unreleased": {"n": len(unreleased),
                       "matched": sum(1 for th in unreleased if id(th) in paired_ids)},
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("throws", help="throws.jsonl from curling-score practice-replay")
    p.add_argument("timeline", help="timeline.json from curling-score analyze")
    p.add_argument("--from", dest="t_from", type=float, required=True)
    p.add_argument("--to", dest="t_to", type=float, required=True)
    p.add_argument("--out", default=None, help="write the report here as JSON")
    a = p.parse_args(argv)
    throws = [json.loads(x) for x in Path(a.throws).read_text().splitlines() if x.strip()]
    throws = [th for th in throws
              if a.t_from <= (_thrown(th["t_release_s"], th["t_rest_s"]) or -1) <= a.t_to - TAIL_S]
    refs = reference(json.loads(Path(a.timeline).read_text()), a.t_from, a.t_to)
    pairs, false, missed = match(throws, refs)
    report = summarize(pairs, false, missed, throws)
    report["missed"] = missed
    report["false"] = [{k: th[k] for k in ("id", "house", "color", "t_rest_s", "t_release_s")}
                       for th in false]
    print(json.dumps({k: v for k, v in report.items() if k not in ("missed", "false")},
                     indent=2))
    if a.out:
        Path(a.out).write_text(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
