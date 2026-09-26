"""How a doubles timeline's placement reads compare with the hand marks.

    python scripts/doubles/placement_acceptance.py \\
        datasets/doubles/marks/placements.json tl1.json [tl2.json ...]

Per marked end:
- the hammer (the house stone's colour);
- the power-play side;
- rock 1's colour against the first thrower in the video, when rock 1 was
  seen;
- whether the end kept exactly ten shots.

Exits 1 on any hammer or power-play mismatch, which the placement stage exists
to get right.
"""

import json
import sys


def compare(marks: dict, docs: list) -> dict:
    out = {"ends": 0, "hammer_ok": 0, "power_play_ok": 0, "first_ok": 0,
           "first_seen": 0, "ten_shots": 0, "mismatches": [], "rows": []}
    for doc in docs:
        vid = doc["source"]["video_id"]
        want = {e["end"]: e for e in marks["games"].get(vid, {}).get("ends", [])}
        if not want or not doc.get("games"):
            continue
        for e in doc["games"][0]["ends"]:
            m = want.get(e["number"])
            if m is None:
                continue
            out["ends"] += 1
            p = e.get("placement") or {}
            want_pp = (m.get("power_play") or {}).get("side")
            hammer_ok = p.get("hammer") == m["house_stone"]
            pp_ok = bool(p) and p.get("power_play") == want_pp
            first = (e.get("shots") or [None])[0]
            seen = first is not None and not first["missing"] and not first["color_inferred"]
            first_ok = seen and first["color"] == m["first_thrower"]
            ten = len(e.get("shots") or []) == 10
            out["hammer_ok"] += hammer_ok
            out["power_play_ok"] += pp_ok
            out["first_seen"] += seen
            out["first_ok"] += first_ok
            out["ten_shots"] += ten
            row = {"video": vid, "end": e["number"], "hammer": p.get("hammer"),
                   "want_hammer": m["house_stone"], "power_play": p.get("power_play"),
                   "want_power_play": want_pp, "first": first["color"] if seen else None,
                   "want_first": m["first_thrower"], "shots": len(e.get("shots") or [])}
            out["rows"].append(row)
            if not (hammer_ok and pp_ok):
                out["mismatches"].append(row)
    return out


def main(argv) -> int:
    marks = json.load(open(argv[1]))
    got = compare(marks, [json.load(open(p)) for p in argv[2:]])
    for r in got["rows"]:
        flag = "" if r not in got["mismatches"] else "   <-- MISMATCH"
        print(f"{r['video']} e{r['end']}: hammer {r['hammer']}/{r['want_hammer']} "
              f"pp {r['power_play']}/{r['want_power_play']} "
              f"first {r['first']}/{r['want_first']} shots {r['shots']}{flag}")
    n = got["ends"]
    print(f"\n{n} marked ends: hammer {got['hammer_ok']}/{n}, power play "
          f"{got['power_play_ok']}/{n}, rock 1 colour {got['first_ok']}/{got['first_seen']} "
          f"where seen, ten shots {got['ten_shots']}/{n}")
    return 1 if got["mismatches"] else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
