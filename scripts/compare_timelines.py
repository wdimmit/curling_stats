"""How far one timeline is from another -- a live one against the VOD's.

    python scripts/compare_timelines.py live/timeline.json vod/timeline.json

Games pair by overlap, ends by number within a game, shots by number within
an end. A shot matches when both have the same colour, both were thrown or
both are missing, and the delivered stone came to rest within 10 cm. The live
path calibrates from the first minutes and reads the recording rather than the
strip proxy, so a small number of differences is expected; this says how many
and where.
"""

import json
import math
import sys

REST_TOLERANCE_M = 0.10


def _overlap(a, b):
    return min(a["end_s"], b["end_s"]) - max(a["start_s"], b["start_s"])


def _rest(shot):
    i = shot.get("delivered_stone_index")
    if shot.get("missing") or i is None:
        return None
    stone = shot["stones"][i]
    return stone["x"], stone["y"]


def _compare_shot(a, b):
    if a["color"] != b["color"]:
        return f"colour {a['color']} vs {b['color']}"
    if bool(a.get("missing")) != bool(b.get("missing")):
        return "missing in " + ("A" if a.get("missing") else "B")
    ra, rb = _rest(a), _rest(b)
    if (ra is None) != (rb is None):
        return "rest known in only one"
    if ra is not None:
        d = math.dist(ra, rb)
        if d > REST_TOLERANCE_M:
            return f"rest {d * 100:.1f} cm apart"
    return None


def compare(doc_a, doc_b) -> dict:
    report = {"games_paired": 0, "ends_matched": 0, "ends_total": 0,
              "shots_matched": 0, "shots_total": 0, "scores_equal": 0,
              "scores_total": 0, "finals_equal": 0, "mismatches": []}
    games_b = list(doc_b["games"])
    for ga in doc_a["games"]:
        gb = max(games_b, key=lambda g: _overlap(ga, g), default=None)
        if gb is None or _overlap(ga, gb) <= 0:
            report["ends_total"] += len(ga["ends"])
            continue
        games_b.remove(gb)
        report["games_paired"] += 1
        report["finals_equal"] += int(ga.get("final") == gb.get("final"))
        ends_b = {e["number"]: e for e in gb["ends"]}
        numbers = sorted({e["number"] for e in ga["ends"]} | set(ends_b))
        ends_a = {e["number"]: e for e in ga["ends"]}
        for n in numbers:
            report["ends_total"] += 1
            ea, eb = ends_a.get(n), ends_b.get(n)
            if ea is None or eb is None or ea["house"] != eb["house"]:
                continue
            report["ends_matched"] += 1
            report["scores_total"] += 1
            report["scores_equal"] += int(ea.get("score") == eb.get("score"))
            shots_a = {s["number"]: s for s in ea["shots"]}
            shots_b = {s["number"]: s for s in eb["shots"]}
            for k in sorted(set(shots_a) | set(shots_b)):
                report["shots_total"] += 1
                sa, sb = shots_a.get(k), shots_b.get(k)
                why = ("only in B" if sa is None else "only in A" if sb is None
                       else _compare_shot(sa, sb))
                if why is None:
                    report["shots_matched"] += 1
                else:
                    report["mismatches"].append(
                        {"game": ga["index"], "end": n, "shot": k, "why": why})
    return report


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    a, b = (json.loads(open(p).read()) for p in argv[:2])
    r = compare(a, b)
    pct = 100.0 * r["shots_matched"] / max(1, r["shots_total"])
    print(f"games paired {r['games_paired']}; ends {r['ends_matched']}/{r['ends_total']}; "
          f"shots {r['shots_matched']}/{r['shots_total']} ({pct:.1f}%); "
          f"board scores {r['scores_equal']}/{r['scores_total']}; "
          f"finals equal {r['finals_equal']}/{r['games_paired']}")
    for m in r["mismatches"]:
        print(f"  game {m['game'] + 1} end {m['end']} shot {m['shot']}: {m['why']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
