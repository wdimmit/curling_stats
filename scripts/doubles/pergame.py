"""Per game: which earlier runs have a byte-equal game at the same index.

Run on the worker, where /data/wdd/curling/doubles-parity/out lives:
    scp scripts/doubles/pergame.py administrator@10.0.0.182:/tmp/ && ssh administrator@10.0.0.182 python3 /tmp/pergame.py
"""
import json, sys
from pathlib import Path
R = Path("/data/wdd/curling/doubles-parity/out")
def games(label, vid):
    p = R / label / vid / "timeline.json"
    if not p.exists():
        return None
    return [json.dumps(g, sort_keys=True) for g in json.loads(p.read_text())["games"]]
for head, pool in (("p6-head-nl", ["base-nl-a", "base-nl-b", "head-nl", "p3-head-nl"]),
                   ("p6-head", ["base-a", "base-b", "head", "p3-head"])):
    print("==", head)
    for vid in ("VXU9xwmugRg", "hOKZoeJNTpM", "AEqLTgM25Tc"):
        h = games(head, vid)
        for i, g in enumerate(h):
            same = [l for l in pool if (gs := games(l, vid)) and i < len(gs) and gs[i] == g]
            print(f"  {vid} game {i}: equals {same or 'NONE'}")
