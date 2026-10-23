"""Every published game whose video the worker still caches, for the 2026-27
card harvest.

    python scripts/cards2627/manifest.py <cached.txt> <out.json>

<cached.txt> is `<cache-root> <video_id>` per line, listed on the worker. The
catalogue is paged through `/api/games?before=`; each game keeps its window
(start_s, end_s) and the published detected score of each end, which the
labeller cross-checks cards against. Timelines are fetched once per source.
"""
import json
import sys
import time
import urllib.parse
import urllib.request

SITE = "https://curling.dimmit.net"


def get(path, tries=5):
    """One GET, retried with a pause: Cloud Run answers 503 while it scales."""
    for i in range(tries):
        try:
            with urllib.request.urlopen(SITE + path, timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code != 503 or i == tries - 1:
                raise
            time.sleep(5 * (i + 1))


def main():
    cached = {}
    for line in open(sys.argv[1]):
        root, vid = line.split()
        cached.setdefault(vid, root)
    games, before, seen = [], None, set()
    while True:
        page = get("/api/games" + (f"?before={urllib.parse.quote(before)}" if before else ""))
        for g in page["games"]:
            if g["source_id"] in seen:
                continue
            seen.add(g["source_id"])
            games.append(g)
        before = page.get("older")
        if not before:
            break
    out = []
    for g in games:
        if g["video_id"] not in cached or g.get("status") != "ready":
            continue
        if (g.get("format") or "fours") != "fours":
            continue
        doc = get(f"/g/{g['source_id']}/timeline.json")
        for game in doc.get("games", []):
            if game.get("practice"):
                continue
            ends = [{"number": e["number"],
                     "detected": e.get("detected_score"),
                     "board": e.get("score") if e.get("score_source") == "board" else None}
                    for e in game.get("ends", [])]
            out.append({"source_id": g["source_id"], "video_id": g["video_id"],
                        "root": cached[g["video_id"]], "title": g["title"],
                        "sheet": g["sheet"], "played_at": g["played_at"],
                        "game_index": game["index"],
                        "start_s": game["start_s"], "end_s": game["end_s"],
                        "ends": ends})
    json.dump(out, open(sys.argv[2], "w"), indent=1)
    print(len(games), "listed;", len(out), "games on cached videos,",
          len({o["video_id"] for o in out}), "videos")


if __name__ == "__main__":
    main()
