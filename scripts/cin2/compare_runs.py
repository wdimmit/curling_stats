#!/usr/bin/env python
"""cin2: what a game run on rKiMmtVod0I measured, run against run, with the board's scores.

    python scripts/cin2/compare_runs.py out2 out3   (run dirs under /w, as on the laptop)
"""
import json, sys
from collections import Counter
# Read off the stream's scoreboard; end 8 was never posted.
TRUTH = {1: ("red", 1), 2: ("yellow", 1), 3: ("red", 1), 4: ("red", 1), 5: ("red", 1), 6: ("yellow", 1), 7: ("red", 2)}
for run in sys.argv[1:]:
    d = json.load(open(f"/w/{run}/timeline.json"))
    shots = [s for g in d["games"] for e in g["ends"] for s in e["shots"]]
    ends = [e for g in d["games"] for e in g["ends"]]
    c = Counter()
    for s in shots:
        c["shots"] += 1
        c["t_hog"] += s.get("t_hog_s") is not None
        c["t_far"] += s.get("t_far_s") is not None
        c["split"] += s.get("long_split_s") is not None
        c["line"] += bool(s.get("line"))
        c["spin"] += bool(s.get("spin"))
        c["hogged"] += s.get("shot_type") == "hogged"
        c["missing"] += bool(s.get("missing"))
        c["rel_" + str(s.get("release_source"))] += 1
    for e in ends:
        c["lost_found"] += e.get("lost_rocks_found") or 0
    exact = team = 0
    for e in ends:
        t = TRUTH.get(e["number"])
        ds = e.get("detected_score") or {}
        got = max(ds.items(), key=lambda kv: kv[1]) if ds else None
        if t and got:
            team += got[0] == t[0] and got[1] > 0
            exact += got == t
    print(run, len(ends), "ends", dict(c), "score exact", exact, "team", team, "of", len(TRUTH))
    for e in ends:
        print("  e%d" % e["number"], e.get("detected_score"), "truth", TRUTH.get(e["number"]),
              "hog %d/%d" % (sum(s.get("t_hog_s") is not None for s in e["shots"]), len(e["shots"])),
              "hogged", sum(s.get("shot_type") == "hogged" for s in e["shots"]))
