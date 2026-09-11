"""Assemble the analysed game into a JSON-serialisable timeline.

Positions are in sheet metres with the origin at the tee of the playing house,
``+y`` up-sheet toward the delivery end and ``+x`` to the right facing
down-sheet.
"""

from datetime import datetime, timezone

from curling_score.game import rules, shots as shots_mod
from curling_score.geometry import constants as C
from curling_score.ingest.source import watch_url_at

SCHEMA_VERSION = 1


def _stone(d) -> dict:
    return {
        "color": d.color,
        "x": round(float(d.x_m), 4),
        "y": round(float(d.y_m), 4),
        "distance_to_tee": round(float((d.x_m**2 + d.y_m**2) ** 0.5), 4),
        "in_house": bool((d.x_m**2 + d.y_m**2) ** 0.5 <= C.IN_HOUSE_MAX_D_M),
        "confidence": round(float(d.confidence), 3),
    }


def build_end(number, house, start_s, end_s, shots) -> dict:
    """One end: its shots, the house they left, and the resulting score."""
    shots = list(shots)
    scoring = shots_mod.scoring_shot(shots)
    final = scoring.stones if scoring else []
    score = rules.score_end(
        [rules.Stone(color=d.color, x=d.x_m, y=d.y_m) for d in final]
    )

    out_shots = []
    for s in shots:
        throw = s.throw
        out_shots.append(
            {
                "number": s.number,
                "color": s.color,
                "has_hammer": throw.has_hammer,
                "thrower_slot": throw.position_slot,
                "position": C.POSITION_NAMES[throw.position_slot],
                "rock_of_player": throw.rock_of_player,
                "label": rules.shot_label(number, s.number),
                "t_rest_s": None if s.missing else round(float(s.t_rest_s), 2),
                "confidence": round(float(s.confidence), 3),
                "color_inferred": bool(s.color_inferred),
                "missing": bool(s.missing),
                "stones": [_stone(d) for d in s.stones],
            }
        )

    return {
        "number": number,
        "house": house,
        "start_s": round(float(start_s), 2),
        "end_s": round(float(end_s), 2),
        "hammer": shots_mod.hammer_from_shots(shots),
        "score": score,
        "shots": out_shots,
        "shots_observed": sum(1 for s in shots if not s.missing),
        "scored_from_shot": scoring.number if scoring else None,
        "final_stones": [_stone(d) for d in final],
        "scoreboard_agrees": None,
    }


def build_game(index, start_s, end_s, ends) -> dict:
    """One game, with the running score carried across its ends."""
    ends = [dict(e) for e in ends]
    running = {c: 0 for c in rules.COLORS}
    for end in ends:
        for c in rules.COLORS:
            running[c] += end.get("score", {}).get(c, 0)
        end["running"] = dict(running)
    # The rules fix the whole hammer sequence from the first end's hammer and
    # the scores, independently of having seen who threw first in each end --
    # which is where reading it from detected deliveries goes wrong. Comparing
    # the two says whether the end structure hangs together.
    observed = [e.get("hammer") for e in ends]
    seen = [h for h in observed if h]
    consistent = None
    if seen:
        expected = rules.hammer_chain(
            seen[0] if observed[0] else rules.first_hammer_given(
                seen[0], observed.index(seen[0]) + 1,
                [e.get("score", {}) for e in ends],
            ),
            [e.get("score", {}) for e in ends],
        )
        for end, want in zip(ends, expected):
            end["hammer_expected"] = want
        consistent = all(
            o == w for o, w in zip(observed, expected) if o
        )

    return {
        "index": index,
        "start_s": round(float(start_s), 2),
        "end_s": round(float(end_s), 2),
        "teams": {c: {"name": None} for c in rules.COLORS},
        "final": dict(running),
        "hammer_consistent": consistent,
        "ends": ends,
    }


def build_document(video_id, url, sheet, duration_s, calibration, games) -> dict:
    """The whole analysis, ready to write to ``timeline.json``."""
    games = [dict(g) for g in games]
    for game in games:
        for end in game["ends"]:
            for shot in end["shots"]:
                t = shot.get("t_rest_s")
                shot["youtube_url"] = (
                    watch_url_at(video_id, t) if t is not None else None
                )
    return {
        "schema_version": SCHEMA_VERSION,
        "source": {
            "url": url,
            "video_id": video_id,
            "sheet": sheet,
            "duration_s": round(float(duration_s), 2),
            "analysed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        },
        "calibration": calibration,
        "games": games,
    }


def apply_overrides(document: dict, overrides: dict) -> dict:
    """Layer hand corrections over the detected timeline.

    Overrides are keyed ``"<game>.<end>.<shot>"`` so re-running the analysis
    never destroys work someone did by hand.
    """
    for key, patch in (overrides or {}).items():
        try:
            g, e, s = (int(part) for part in key.split("."))
        except ValueError:
            continue
        for game in document.get("games", []):
            if game["index"] != g:
                continue
            for end in game["ends"]:
                if end["number"] != e:
                    continue
                for shot in end["shots"]:
                    if shot["number"] == s:
                        shot.update(patch)
                        shot["corrected"] = True
    return document
