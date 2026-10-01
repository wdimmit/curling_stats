#!/usr/bin/env python
"""Pare a served timeline down to what the game report reads, for a test fixture.

    python scripts/pare_report_fixture.py timeline.json overrides.json out.json

Keeps the first game's ends, scores, hammer, scoreboard and thinking, and each
rock's colour, position, type, grade and clock -- and drops the tracks, lines
and stones that make a real timeline 300 KB. The overrides ride along
unchanged, so the fixture is the game exactly as its charter graded it.
"""
import json
import sys

SHOT = ("number", "id", "color", "color_inferred", "position", "rock_of_player", "thrower_slot",
        "has_hammer", "label", "shot_type", "shot_type_source", "thinking_time_s",
        "t_tee_estimated", "missing", "state_known", "user_score", "t_guess_s")
END = ("number", "id", "house", "start_s", "end_s", "hammer", "score", "score_source",
       "running", "thinking_time", "unplaced_shots", "shots_expected", "splits_measured",
       "detected_score")
GAME = ("index", "start_s", "end_s", "teams", "final", "scoreboard", "hammer_consistent",
        "thinking_time", "detected")


def pare(doc: dict) -> dict:
    g = doc["games"][0]
    game = {k: g[k] for k in GAME if k in g}
    game["ends"] = []
    for e in g["ends"]:
        end = {k: e[k] for k in END if k in e}
        end["shots"] = [{**{k: s[k] for k in SHOT if k in s}, "stones": []} for s in e["shots"]]
        game["ends"].append(end)
    chart = doc.get("chart") or {}
    return {"schema_version": doc["schema_version"],
            "source": {"video_id": doc["source"]["video_id"], "sheet": doc["source"]["sheet"]},
            "chart": {"league": chart.get("league"), "title": chart.get("title")},
            "games": [game]}


def main() -> None:
    timeline, overrides, out = sys.argv[1:4]
    doc = pare(json.load(open(timeline)))
    fixture = {"doc": doc, "overrides": json.load(open(overrides))}
    with open(out, "w") as f:
        json.dump(fixture, f, indent=1, sort_keys=True)
        f.write("\n")


if __name__ == "__main__":
    main()
