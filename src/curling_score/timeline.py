"""Assemble the analysed game into a JSON-serialisable timeline.

Positions are in sheet metres with the origin at the tee of the playing house,
``+y`` up-sheet toward the delivery end and ``+x`` to the right facing
down-sheet.
"""

from datetime import datetime, timezone

from curling_score.game import classify, rules, shots as shots_mod, split, thinking
from curling_score.geometry import constants as C
from curling_score.ingest.source import watch_url_at

SCHEMA_VERSION = 3

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

    clock = thinking.for_end(shots)

    out_shots = []
    for i, s in enumerate(shots):
        throw = s.throw
        dv = getattr(s, "delivery", None)
        rel = getattr(s, "release", None)
        sp = split.long_split(rel, dv)
        t_tee = thinking.tee_crossing(s)
        think = clock.per_shot[i] if i < len(clock.per_shot) else None
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
                # Seen leaving the other house. None whenever that camera lost
                # the throw, which is most of what limits the two timings below.
                "t_release_s": None if rel is None else round(float(rel.t), 2),
                "release_speed_m_s": (
                    None if rel is None else round(float(rel.speed_m_s), 3)
                ),
                "t_tee_s": None if t_tee is None else round(float(t_tee), 2),
                "long_split_s": (
                    None if sp is None else round(float(sp.seconds), 2)
                ),
                "long_split_baseline_m": (
                    None if sp is None else round(float(sp.baseline_m), 3)
                ),
                "long_split_extrapolated_m": (
                    None if sp is None else round(float(sp.extrapolated_m), 3)
                ),
                "thinking_time_s": (
                    None if think is None else round(float(think), 2)
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
        "splits_measured": sum(1 for o in out_shots if o["long_split_s"] is not None),
        "thinking_time": {
            **{c: round(v, 2) for c, v in clock.by_color.items()},
            "measured_shots": clock.measured_shots,
            "unmeasured_shots": clock.unmeasured_shots,
            "anomalies": clock.anomalies,
        },
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

    clock = {c: 0.0 for c in rules.COLORS}
    measured = unmeasured = anomalies = splits = 0
    for end in ends:
        t = end.get("thinking_time") or {}
        for c in rules.COLORS:
            clock[c] += float(t.get(c, 0.0) or 0.0)
        measured += int(t.get("measured_shots", 0) or 0)
        unmeasured += int(t.get("unmeasured_shots", 0) or 0)
        anomalies += int(t.get("anomalies", 0) or 0)
        splits += int(end.get("splits_measured", 0) or 0)

    return {
        "index": index,
        "start_s": round(float(start_s), 2),
        "end_s": round(float(end_s), 2),
        "teams": {c: {"name": None} for c in rules.COLORS},
        "thinking_time": {
            **{c: round(v, 2) for c, v in clock.items()},
            "measured_shots": measured,
            "unmeasured_shots": unmeasured,
            "anomalies": anomalies,
        },
        "splits_measured": splits,
        "final": dict(running),
        "hammer_consistent": consistent,
        "ends": ends,
    }


def build_document(video_id, url, sheet, duration_s, calibration, games,
                   window=None, processing_version=None) -> dict:
    """The whole analysis, ready to write to ``timeline.json``.

    ``window`` is the ``(start_s, end_s)`` of the stream that was analysed when
    the caller asked for only part of it; ``processing_version`` names the
    pipeline and model that produced this, so two documents for one video can
    be told apart.
    """
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
    start_s, end_s = window if window else (None, None)
    return {
        "schema_version": SCHEMA_VERSION,
        "processing_version": processing_version,
        "source": {
            "url": url,
            "video_id": video_id,
            "sheet": sheet,
            "duration_s": round(float(duration_s), 2),
            "analysed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "window": {
                "start_s": None if start_s is None else round(float(start_s), 2),
                "end_s": None if end_s is None else round(float(end_s), 2),
            },
        },
        "calibration": calibration,
        "games": games,
    }


def shot_identity(shot: dict) -> int:
    """The number a shot was detected with, which is what overrides key on.

    Moving a shot renumbers everything around it, so the displayed ``number``
    can drift from the one its corrections are filed under; ``id`` keeps the
    original where that has happened.
    """
    return int(shot.get("id", shot["number"]))


def _reorder(shots: list) -> list:
    """Honour ``before`` on any shot: it was thrown before the shot named.

    A blank appended to the end of a short end is the filler's best guess at
    where an unseen rock went. When the charter knows better -- the video shows
    two guards before the first rock we saw -- they move it, and this is the
    move. Shots are placed in the order of their original numbers so two moved
    before the same target keep their relative order.
    """
    moved = [s for s in shots if isinstance(s.get("before"), int)
             and s["before"] != shot_identity(s)]
    if not moved:
        return shots
    ids = {shot_identity(s) for s in shots}
    moved = [s for s in moved if s["before"] in ids]
    if not moved:
        return shots
    out = [s for s in shots if s not in moved]
    for s in sorted(moved, key=shot_identity):
        at = next((i for i, o in enumerate(out)
                   if shot_identity(o) == s["before"]), len(out))
        out.insert(at, s)
    return out


def _renumber(shots: list, end_number: int, patched: set) -> None:
    """Give a reordered end its numbers, throwers and labels afresh.

    Everything a shot's number implies follows it: who threw it, which of
    their two rocks it was, whether it is the hammer. Blanks take their colour
    from the alternation around them, since that colour was only ever
    inferred; a detected rock keeps the colour it was seen with, and so does
    any shot whose colour the charter set by hand.
    """
    anchors = [(i, s["color"]) for i, s in enumerate(shots)
               if not s.get("color_inferred") or shot_identity(s) in patched]
    for i, s in enumerate(shots):
        s["id"] = shot_identity(s)
        s["number"] = i + 1
        t = rules.throw_info(i + 1)
        s["has_hammer"] = t.has_hammer
        s["thrower_slot"] = t.position_slot
        s["position"] = C.POSITION_NAMES[t.position_slot]
        s["rock_of_player"] = t.rock_of_player
        s["label"] = rules.shot_label(end_number, i + 1)
        if s.get("color_inferred") and anchors and shot_identity(s) not in patched:
            j, color = min(anchors, key=lambda a: abs(a[0] - i))
            s["color"] = color if (i - j) % 2 == 0 else rules.other_color(color)


def apply_overrides(document: dict, overrides: dict) -> dict:
    """Layer hand corrections over the detected timeline.

    Overrides are keyed ``"<game>.<end>.<shot>"`` so re-running the analysis
    never destroys work someone did by hand. A patch may carry ``before``,
    naming the shot this one was actually thrown before; the end is then put
    in that order and renumbered, with the original numbers kept in ``id`` so
    the keys still resolve.
    """
    patches: dict[tuple[int, int], dict[int, dict]] = {}
    for key, patch in (overrides or {}).items():
        try:
            g, e, s = (int(part) for part in key.split("."))
        except ValueError:
            continue
        patches.setdefault((g, e), {})[s] = patch
    for game in document.get("games", []):
        for end in game["ends"]:
            here = patches.get((game["index"], end["number"]), {})
            for shot in end["shots"]:
                patch = here.get(shot_identity(shot))
                if patch is not None:
                    shot.update(patch)
                    shot["corrected"] = True
            ordered = _reorder(end["shots"])
            if ordered is not end["shots"]:
                colour_set = {s for s, p in here.items() if "color" in p}
                _renumber(ordered, end["number"], colour_set)
                end["shots"] = ordered
    return document
