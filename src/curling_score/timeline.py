"""Assemble the analysed game into a JSON-serialisable timeline.

Positions are in sheet metres with the origin at the tee of the playing house,
``+y`` up-sheet toward the delivery end and ``+x`` to the right facing
down-sheet.
"""

from datetime import datetime, timezone

from curling_score.game import classify, rules, shots as shots_mod
from curling_score.geometry import constants as C
from curling_score.ingest.source import watch_url_at

SCHEMA_VERSION = 2

# The overhead camera only sees the last few metres of a 45 m sheet, so the
# stone comes into view long after it left the hand. To watch the shot being
# called and thrown you have to start this far back from where we first see it.
VIDEO_LEAD_IN_S = 10.0


def _stone(d) -> dict:
    return {
        "color": d.color,
        "x": round(float(d.x_m), 4),
        "y": round(float(d.y_m), 4),
        "distance_to_tee": round(float((d.x_m**2 + d.y_m**2) ** 0.5), 4),
        "in_house": bool((d.x_m**2 + d.y_m**2) ** 0.5 <= C.IN_HOUSE_MAX_D_M),
        "confidence": round(float(d.confidence), 3),
    }


def _track(dv) -> list:
    """The flight as [[t, x, y], ...], rounded to what the viewer can draw.

    Centimetres and hundredths of a second: finer than the detector's own
    accuracy, and it keeps a four-hour game's tracks to a sensible file size.
    """
    if dv is None or not dv.track:
        return []
    return [
        [round(float(t), 2), round(float(x), 3), round(float(y), 3)]
        for t, x, y in dv.track
    ]


def _delta(delta) -> dict | None:
    """Round the house diff for output, leaving its shape alone."""
    if delta is None:
        return None
    def one(s):
        out = {"color": s["color"], "x": round(float(s["x"]), 4),
               "y": round(float(s["y"]), 4)}
        if "distance_m" in s:
            out |= {"from_x": round(float(s["from_x"]), 4),
                    "from_y": round(float(s["from_y"]), 4),
                    "distance_m": round(float(s["distance_m"]), 3)}
        return out
    return {k: [one(s) for s in delta.get(k, [])]
            for k in ("added", "removed", "moved")}


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
        dv = getattr(s, "delivery", None)
        kind, kind_conf = classify.classify_shot(s)
        t_enter = None if dv is None else round(float(dv.t_enter), 2)
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
                "t_enter_s": t_enter,
                # Where to start the video to see the shot being called and
                # thrown, not merely its arrival.
                "t_video_s": (
                    None if t_enter is None else round(max(0.0, t_enter - VIDEO_LEAD_IN_S), 2)
                ),
                "confidence": round(float(s.confidence), 3),
                "color_inferred": bool(s.color_inferred),
                "missing": bool(s.missing),
                "state_known": bool(getattr(s, "state_known", True)),
                "shot_type": kind,
                "shot_type_confidence": round(float(kind_conf), 3),
                "shot_type_source": "auto",
                "reason": None if dv is None else dv.reason,
                "travel_m": None if dv is None else round(float(dv.travel_m), 3),
                "entry_speed_m_s": (
                    None if dv is None else round(float(dv.speed_at()), 3)
                ),
                "track": _track(dv),
                "house_delta": _delta(getattr(s, "house_delta", None)),
                "delivered_stone_index": getattr(s, "delivered_stone_index", None),
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
        # Sixteen rocks are thrown. Where the list is shorter than that, the
        # missing ones could not even be placed, and the viewer has to say so
        # rather than present a short end as a whole one.
        "shots_expected": C.STONES_PER_END,
        "unplaced_shots": max(0, C.STONES_PER_END - len(shots)),
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
                # Prefer the moment before the throw: a link that lands on the
                # stone already at rest shows the one thing the viewer can
                # already see in the house diagram.
                t = shot.get("t_video_s")
                if t is None:
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
