"""Assemble the analysed game into a JSON-serialisable timeline.

Positions are in sheet metres with the origin at the tee of the playing house,
``+y`` up-sheet toward the delivery end and ``+x`` to the right facing
down-sheet.
"""

from copy import deepcopy
from datetime import datetime, timezone

from curling_score.game import classify, fartime, hogtime, rules, shots as shots_mod, split, thinking
from curling_score.game import format as format_mod
from curling_score.geometry import constants as C
from curling_score.ingest.source import watch_url_at

SCHEMA_VERSION = 7

# The overhead camera only sees the last few metres of a 45 m sheet, so the
# stone comes into view long after it left the hand. To watch the shot being
# called and thrown you have to start this far back from where we first see it.
VIDEO_LEAD_IN_S = 10.0

# A doubles game is scheduled for eight ends (R17); an end after that is extra.
SCHEDULED_ENDS = 8


def _broom(b):
    """A `broomtime.TargetBroom` in house metres, or None."""
    if b is None:
        return None
    return {"x": round(float(b.x_m), 4), "y": round(float(b.y_m), 4),
            "seen": round(float(b.seen), 2),
            "confidence": round(float(b.confidence), 3)}


def _line(l):
    """A `linetime.Line` in house metres, or None."""
    if l is None:
        return None
    r = lambda v, n=4: None if v is None else round(float(v), n)
    return {"start": None if l.start is None else {"x": r(l.start[0]), "y": r(l.start[1], 3)},
            "at_hog": {"x": r(l.at_hog_x), "offset_m": r(l.at_hog_offset)},
            "at_broom": {"x": r(l.at_broom_x), "miss_m": r(l.miss)},
            "side": l.side, "curl": l.curl, "confirmed": l.confirmed,
            "hog_path": [[r(y, 2), r(x, 3)] for y, x in l.hog_path],
            "path": [[r(y, 2), r(x, 3)] for y, x in l.path],
            "fit": {"n": int(l.fit_n), "rms_m": r(l.fit_rms)}}


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


def _placement(placed, shots) -> dict | None:
    """A `placement.Placement` for output, or None when none was found."""
    if placed is None:
        return None
    pos = lambda s: None if s is None else {
        "color": s[0], "x": round(float(s[1]), 3), "y": round(float(s[2]), 3)}
    first = shots[0] if shots else None
    agrees = None
    if first is not None and not first.missing and not first.color_inferred:
        # The guard's team throws first, so a first rock of the hammer's colour
        # contradicts the placement read (or the shot list).
        agrees = first.color != placed.hammer
    house_s = placed.house_s
    return {"t_s": round(float(placed.t_s), 2),
            # When the house stone alone first held, for diagnosis.
            "house_s": None if house_s is None else round(float(house_s), 2),
            "hammer": placed.hammer,
            "house": pos(placed.house), "guard": pos(placed.guard),
            "power_play": placed.power_play, "complete": placed.complete,
            "agrees_with_shots": agrees}


def build_end(number, house, start_s, end_s, shots, board_score=None, fmt=None,
             placement=None) -> dict:
    """One end: its shots, the house they left, and the board's score for it.

    ``board_score`` is what the wall board says this end was; ``None`` means
    the board could not speak to it. A blank end is not that: nobody scoring
    is a real ``{"red": 0, "yellow": 0}`` read off the board, and the two have
    to stay tellable apart -- an unread end that came out as a zero would be
    inference wearing the board's clothes.

    The detected score is still computed, as ``detected_score``, because the
    disagreement between the two is the only check we have on either.

    ``fmt`` is the :class:`format.GameFormat` this end was played under;
    ``None`` means fours, which is every chart made before doubles existed.

    ``placement`` is doubles' positioned stones (`placement.Placement`); it
    names the hammer.
    """
    fmt = fmt or format_mod.FOURS
    shots = list(shots)
    scoring = shots_mod.scoring_shot(shots)
    final = scoring.stones if scoring else []
    detected_score = rules.score_end(
        [rules.Stone(color=d.color, x=d.x_m, y=d.y_m) for d in final]
    )

    clock = thinking.for_end(shots)

    out_shots = []
    for i, s in enumerate(shots):
        throw = fmt.throw_info(s.number)
        dv = getattr(s, "delivery", None)
        rel = getattr(s, "release", None)
        sp = split.long_split(dv, t_hog=hogtime.crossing(s),
                              v_hog=hogtime.speed_at_hog(s),
                              far=fartime.crossing(s))
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
                "position": fmt.positions[throw.position_slot - 1],
                "rock_of_player": throw.rock_of_player,
                "label": fmt.shot_label(number, s.number),
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
                # True when the crossing -- and so the interval below -- is the
                # typical lag taken off the arrival rather than a sighting.
                "t_tee_estimated": bool(getattr(s, "tee_estimated", False)),
                "long_split_s": (
                    None if sp is None else round(float(sp.seconds), 2)
                ),
                "long_split_baseline_m": (
                    None if sp is None else round(float(sp.baseline_m), 3)
                ),
                # How far the far crossing was reached for, in the PANEL's y
                # units -- 0 when it was observed. Not the old
                # `long_split_extrapolated_m`, which was the throwing end in
                # metres: different end, different units, and old charts still
                # read that one.
                # The panel's own tripwire against the side view's answer, or
                # null when the panel had no reading. While the composite's
                # sources are out of step this measures the desync rather than
                # the detector -- see scripts/ds13/sync_report.py.
                "long_split_panel_delta_s": (
                    None if sp is None or sp.panel_delta is None
                    else round(float(sp.panel_delta), 3)
                ),
                "long_split_far_reach_u": (
                    None if sp is None else round(float(sp.far_reach), 4)
                ),
                "thinking_time_s": (
                    None if think is None else round(float(think), 2)
                ),
                "track": _track(dv),
                "house_delta": _delta(getattr(s, "house_delta", None)),
                "delivered_stone_index": getattr(s, "delivered_stone_index", None),
                "stones": [_stone(d) for d in s.stones],
                # Where the skip held the broom before the rock crossed the
                # tee, in house metres; null when no pad was held still, or the
                # timeline was made without a broom model.
                "target_broom": _broom(getattr(s, "target_broom", None)),
                # Where the rock's thrown line passed the broom, where it sat
                # before the push and where it went; null when not measured.
                "line": _line(getattr(s, "line", None)),
            }
        )

    out = {
        "number": number,
        "house": house,
        "start_s": round(float(start_s), 2),
        "end_s": round(float(end_s), 2),
        "hammer": shots_mod.hammer_from_shots(shots),
        # The score is the board's or it is nothing. "scored_from_shot" below
        # stays as it was: it names the shot that settled the house, which is
        # a detection fact and not a score.
        "score": None if board_score is None else dict(board_score),
        "score_source": None if board_score is None else "board",
        "detected_score": detected_score,
        "shots": out_shots,
        "shots_observed": sum(1 for s in shots if not s.missing),
        # Every rock an end holds was thrown. Where the list is shorter than
        # that, the missing ones could not even be placed, and the viewer has
        # to say so rather than present a short end as a whole one.
        "shots_expected": fmt.delivered_per_end,
        "unplaced_shots": max(0, fmt.delivered_per_end - len(shots)),
        "splits_measured": sum(1 for o in out_shots if o["long_split_s"] is not None),
        "thinking_time": {
            **{c: round(v, 2) for c, v in clock.by_color.items()},
            "measured_shots": clock.measured_shots,
            "unmeasured_shots": clock.unmeasured_shots,
            "estimated_shots": clock.estimated_shots,
            "anomalies": clock.anomalies,
        },
        "scored_from_shot": scoring.number if scoring else None,
        "final_stones": [_stone(d) for d in final],
        "scoreboard_agrees": None,
    }
    if fmt.placed_per_team:
        # The house stone's team has the hammer: observed directly, where
        # reading it from the first rock depends on having seen rock 1.
        out["placement"] = _placement(placement, shots)
        if placement is not None:
            out["hammer"] = placement.hammer
        out["hammer_source"] = "first_shot" if placement is None else "placement"
    return out


def build_game(index, start_s, end_s, ends, board=None, fmt=None) -> dict:
    """One game, with the board's running score carried across its ends.

    ``board`` is the ``BoardScores`` read off the wall, or None when the board
    could not be read at all. Nothing here falls back to the detected figures:
    they are carried alongside, under ``detected``, purely as the check.

    ``fmt`` is the :class:`format.GameFormat` this game was played under; it
    only matters for whether a blank end passes the hammer.
    """
    blank = (fmt or format_mod.FOURS).blank_passes_hammer
    ends = [dict(e) for e in ends]

    # The running total is only meaningful while every end up to here is
    # known. One unread end and everything after it is unknown too.
    running = {c: 0 for c in rules.COLORS}
    broken = False
    for end in ends:
        if end.get("score") is None:
            broken = True
        if broken:
            end["running"] = None
            continue
        for c in rules.COLORS:
            running[c] += end["score"].get(c, 0)
        end["running"] = dict(running)

    # The rules fix the whole hammer sequence from the first end's hammer and
    # the scores, independently of having seen who threw first in each end --
    # which is where reading it from detected deliveries goes wrong. Comparing
    # the two says whether the end structure hangs together. Those scores are
    # the board's now, so this no longer inherits a detection mistake -- but
    # it can only run as far as the board is known.
    known = []
    for end in ends:
        if end.get("score") is None:
            break
        known.append(end["score"])

    observed = [e.get("hammer") for e in ends[:len(known)]]
    seen = [h for h in observed if h]
    consistent = None
    for end in ends:
        end["hammer_expected"] = None
    if seen:
        expected = rules.hammer_chain(
            seen[0] if observed[0] else rules.first_hammer_given(
                seen[0], observed.index(seen[0]) + 1, known, blank_passes=blank,
            ),
            known, blank_passes=blank,
        )
        for end, want in zip(ends, expected):
            end["hammer_expected"] = want
        consistent = all(
            o == w for o, w in zip(observed, expected) if o
        )

    detected = [e.get("detected_score") or {c: 0 for c in rules.COLORS}
                for e in ends]

    clock = {c: 0.0 for c in rules.COLORS}
    measured = unmeasured = anomalies = estimated = splits = 0
    for end in ends:
        t = end.get("thinking_time") or {}
        for c in rules.COLORS:
            clock[c] += float(t.get(c, 0.0) or 0.0)
        measured += int(t.get("measured_shots", 0) or 0)
        unmeasured += int(t.get("unmeasured_shots", 0) or 0)
        estimated += int(t.get("estimated_shots", 0) or 0)
        anomalies += int(t.get("anomalies", 0) or 0)
        splits += int(end.get("splits_measured", 0) or 0)

    out = {
        "index": index,
        "start_s": round(float(start_s), 2),
        "end_s": round(float(end_s), 2),
        "teams": {c: {"name": None} for c in rules.COLORS},
        "thinking_time": {
            **{c: round(v, 2) for c, v in clock.items()},
            "measured_shots": measured,
            "unmeasured_shots": unmeasured,
            "estimated_shots": estimated,
            "anomalies": anomalies,
        },
        "splits_measured": splits,
        # The board's, and null unless it accounted for every end. What the
        # detector made of the same game sits apart, under "detected", where
        # nobody can mistake it for the score.
        "final": dict(board.final) if board is not None and board.final else None,
        "detected": {
            "score_by_end": [dict(d) for d in detected],
            "final": rules.running_total(detected),
        },
        "hammer_consistent": consistent,
        "ends": ends,
    }
    if (fmt or format_mod.FOURS).placed_per_team:
        plays = {c: [] for c in rules.COLORS}
        for end in ends:
            p = end.get("placement") or {}
            if p.get("power_play"):
                plays[p["hammer"]].append(end["number"])
        out["power_plays"] = plays
        # R17: one power play per team per game, and none in an extra end.
        problems = []
        for c, used in plays.items():
            if len(used) > 1:
                problems.append(f"{c} used {len(used)} power plays (ends {used}); a team has one")
            late = [n for n in used if n > SCHEDULED_ENDS]
            if late:
                problems.append(f"{c} used a power play in an extra end ({late})")
        out["power_play_problems"] = problems
    return out


# The format check (it flags, never overrides). A doubles analysis whose
# complete placement shows in fewer than this share of ends, or whose ends
# offer fourteen or more rocks (counting what the placement dropped), looks
# like fours.
DOUBLES_MIN_PLACED = 0.4
FOURS_MIN_OFFERED = 14
# A fours analysis looks like doubles when its ends offer twelve or fewer
# rocks and three in four offer six or fewer a side.
DOUBLES_MAX_OFFERED = 12
DOUBLES_MAX_A_SIDE = 6
DOUBLES_SHARE = 0.75


def _offered(end) -> int:
    """What detection offered in an end, before the placement took its share.

    ``deliveries_seen`` is counted after the placement's exclusion, which in a
    four-player game analysed as doubles would hide the very rocks that give
    it away. A four-player end has no placement, so nothing is added.
    """
    seen = int(end.get("deliveries_seen", len(end.get("shots") or [])))
    return seen + int((end.get("placement") or {}).get("candidates_dropped", 0))


def format_check(games, fmt) -> dict:
    """Whether the ends look like the format they were analysed as.

    With no ends there is nothing to judge, and the answer is "unknown".
    """
    ends = [e for g in games for e in g.get("ends", [])]
    offered = sorted(_offered(e) for e in ends)
    median = offered[len(offered) // 2] if offered else 0
    out = {"ends": len(ends), "median_offered": median}
    if fmt.placed_per_team:
        found = sum(1 for e in ends if e.get("placement"))
        # Only a complete placement is evidence: a house stone alone is just
        # a stone behind the button, which any game has.
        complete = sum(1 for e in ends if (e.get("placement") or {}).get("complete"))
        out = {**out, "placement_found": found, "placement_complete": complete}
        doubles = bool(ends) and complete / len(ends) >= DOUBLES_MIN_PLACED \
            and median < FOURS_MIN_OFFERED
    else:
        small = sum(1 for e in ends
                    if max((e.get("thrown") or {}).values(), default=99) <= DOUBLES_MAX_A_SIDE)
        doubles = bool(ends) and median <= DOUBLES_MAX_OFFERED \
            and small / len(ends) >= DOUBLES_SHARE
    looks = "unknown" if not ends else "doubles" if doubles else "fours"
    return {**out, "looks_like": looks}


def build_document(video_id, url, sheet, duration_s, calibration, games,
                   window=None, processing_version=None, fmt=None, check=None) -> dict:
    """The whole analysis, ready to write to ``timeline.json``.

    ``window`` is the ``(start_s, end_s)`` of the stream that was analysed when
    the caller asked for only part of it; ``processing_version`` names the
    pipeline and model that produced this, so two documents for one video can
    be told apart. ``fmt`` is the :class:`format.GameFormat` the game was
    played under; a fours document writes no ``format`` block at all, and a
    document without one reads as fours.
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
    doc = {
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
    fmt = fmt or format_mod.FOURS
    if fmt is not format_mod.FOURS:
        # Written only when it says something: a four-player timeline stays
        # byte-for-byte what it was, and a missing block reads as fours.
        doc["format"] = fmt.to_json()
        if check is not None:
            doc["format"]["check"] = check
    elif check is not None and check.get("looks_like") not in (fmt.name, "unknown"):
        doc["format_warning"] = (
            f"analysed as {fmt.name}, but the ends look like "
            f"{check['looks_like']}: a median of {check['median_offered']} rocks "
            f"offered across {check['ends']} ends")
    return doc


def shot_identity(shot: dict) -> int:
    """The number a shot was detected with, which is what overrides key on.

    Moving a shot renumbers everything around it, so the displayed ``number``
    can drift from the one its corrections are filed under; ``id`` keeps the
    original where that has happened.
    """
    return int(shot.get("id", shot["number"]))


def end_identity(end: dict) -> int:
    """The number an end was detected with, which is what overrides key on.

    Trimming the practice off the front renumbers every end that survives, so
    the displayed ``number`` drifts from the one corrections were filed under;
    ``id`` keeps the original where that has happened. Same bargain as
    :func:`shot_identity`, one level up.
    """
    return int(end.get("id", end["number"]))


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


def _renumber(shots: list, end_number: int, patched: set, fmt=None) -> None:
    """Give a reordered end its numbers, throwers and labels afresh.

    Everything a shot's number implies follows it: who threw it, which of
    their two rocks it was, whether it is the hammer. Blanks take their colour
    from the alternation around them, since that colour was only ever
    inferred; a detected rock keeps the colour it was seen with, and so does
    any shot whose colour the charter set by hand.

    ``fmt`` is the :class:`format.GameFormat` the end was played under; ``None``
    means fours.
    """
    fmt = fmt or format_mod.FOURS
    anchors = [(i, s["color"]) for i, s in enumerate(shots)
               if not s.get("color_inferred") or shot_identity(s) in patched]
    for i, s in enumerate(shots):
        s["id"] = shot_identity(s)
        s["number"] = i + 1
        t = fmt.throw_info(i + 1)
        s["has_hammer"] = t.has_hammer
        s["thrower_slot"] = t.position_slot
        s["position"] = fmt.positions[t.position_slot - 1]
        s["rock_of_player"] = t.rock_of_player
        s["label"] = fmt.shot_label(end_number, i + 1)
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
    fmt = format_mod.of_document(document)
    patches: dict[tuple[int, int], dict[int, dict]] = {}
    for key, patch in (overrides or {}).items():
        try:
            g, e, s = (int(part) for part in key.split("."))
        except ValueError:
            continue
        patches.setdefault((g, e), {})[s] = patch
    for game in document.get("games", []):
        for end in game["ends"]:
            here = patches.get((game["index"], end_identity(end)), {})
            for shot in end["shots"]:
                patch = here.get(shot_identity(shot))
                if patch is not None:
                    shot.update(patch)
                    shot["corrected"] = True
            ordered = _reorder(end["shots"])
            if ordered is not end["shots"]:
                colour_set = {s for s, p in here.items() if "color" in p}
                _renumber(ordered, end["number"], colour_set, fmt)
                end["shots"] = ordered
    return document


# Why a game's board scores were left off its ends. The board is keyed by the
# real end number on each card; detection numbers the blocks it found. Nothing
# in the document says how many leading blocks were practice, so the two
# numberings can only be assumed equal -- and this design does not assume.
BOARD_ALIGNMENT_UNKNOWN = (
    "the game opens with an end short of a full end, so it may be "
    "practice, and the board does not account for every detected end -- which "
    "end each card belongs to cannot be settled without a start time"
)
# The same reason as four-player documents have always worded it. Kept word
# for word, because a fours timeline must stay byte-identical to what it was
# before there were formats; any other format gets the wording above, since
# a doubles end is ten delivered rocks, not sixteen.
_BOARD_ALIGNMENT_UNKNOWN_FOURS = (
    "the game opens with an end short of a full sixteen rocks, so it may be "
    "practice, and the board does not account for every detected end -- which "
    "end each card belongs to cannot be settled without a start time"
)


def board_per_end(board: dict | None) -> dict | None:
    """A board block's per-end scores, keyed by real end number, or None.

    The analyser writes the keys as strings, because JSON has no others, and
    a document round-tripped through a file brings them back the same way.
    Both are accepted here so a caller never has to care. None means the
    block cannot speak to individual ends at all, and its scores are then
    left exactly as they were.
    """
    per_end = (board or {}).get("per_end")
    if not isinstance(per_end, dict):
        return None
    out = {}
    for key, score in per_end.items():
        try:
            out[int(key)] = score
        except (TypeError, ValueError):
            return None
    return out


def settle_board_scores(ends: list, block: dict, highest_end: int,
                        fmt=None) -> bool:
    """Whether the board's scores may stay on these ends. Fails closed.

    The board's numbering is the real game's; ours is whatever detection cut
    out of the stream, practice and all. The two agree only when nothing but
    the game was detected, and one leading practice block silently moves
    every score one end early -- confidently wrong, which is worse than the
    inferred score this design removed.

    Two signs together say the alignment is doubtful. The first is the
    practice signature :func:`trim_to_start` already trusts: a leading end
    short of a full sixteen rocks. The second is a board that cannot account
    for every detected end -- if it names an end for every block we found,
    there is no room for a practice block in front of them. Doubtful means
    the scores come back off: the viewer can say the board could not be
    placed, which is true, where a plausible score would not be.

    Both conditions are recorded on ``block`` either way, because "the board
    did not account for every end" was previously visible only as a
    suppressed ``final``, where it read as ordinary conservatism.

    ``fmt`` is the game's :class:`format.GameFormat` (None is fours); it only
    words the reason. The short-end test reads each end's ``shots_expected``.
    """
    covers_every_end = int(highest_end) >= len(ends)
    first_short = bool(ends) and (
        len(ends[0].get("shots") or [])
        < ends[0].get("shots_expected", C.STONES_PER_END)
    )
    block["accounts_for_every_end"] = covers_every_end
    if covers_every_end or not first_short:
        block["scores_withheld"] = None
        return True
    block["scores_withheld"] = (
        _BOARD_ALIGNMENT_UNKNOWN_FOURS
        if (fmt or format_mod.FOURS).name == format_mod.FOURS.name
        else BOARD_ALIGNMENT_UNKNOWN)
    for end in ends:
        end["score"] = None
        end["score_source"] = None
    return False


def trim_to_start(document: dict, start_s: float | None) -> dict:
    """Drop the pre-game practice that the submitter's start time sits behind.

    Club streams open with practice: twenty minutes of players sliding rocks
    before the first end. The sheet never sits empty for ``GAME_GAP_S`` while
    that is going on, so :func:`segment.segment_games` cannot call it a
    separate game, and each block of it clears ``MIN_END_S``, so it arrives as
    leading ends -- numbered, scored, and pushing the real first end down the
    board. "Game starts at" is the one thing we know that the profile does
    not, and this is where it is spent.

    Only *leading* ends go, and only while two things hold: the end begins
    before ``start_s``, and it is short of a full sixteen rocks. The second is
    the guard. A time typed into a box is a guess; sixteen delivered rocks are
    an end, whatever the guess said, and stopping there means a start time
    given too late leaves a fat chart rather than an amputated one. A game
    always keeps at least one end, so a time past the whole game shows it all.

    Per chart, not per run: the run's document stays pristine, and two charts
    of the same game made from different start times each get their own view.
    """
    if start_s is None:
        return document
    document = deepcopy(document)
    fmt = format_mod.of_document(document)
    for i, game in enumerate(document.get("games", [])):
        ends = game.get("ends") or []
        drop = 0
        while drop < len(ends) - 1:
            end = ends[drop]
            if float(end["start_s"]) >= start_s:
                break
            if len(end["shots"]) >= end.get("shots_expected", C.STONES_PER_END):
                break
            drop += 1
        board = game.get("scoreboard")
        if drop == 0:
            # Nothing to amputate. But the guard just refused to drop
            # anything for the same reason a trim would have stopped where
            # it did: it takes this detected end to be the game's real
            # first one. That is exactly the fact a withheld board score was
            # waiting on -- analyze.py could not rule out a practice block
            # in front, and now the submitter's own start time has -- so a
            # withheld board still gets rekeyed here, just without the
            # renumbering a genuine trim would also have done. A board that
            # was never withheld has nothing to gain from this, and is left
            # alone rather than rebuilt for no reason.
            if not (isinstance(board, dict) and board.get("scores_withheld")):
                continue
            kept = [dict(e) for e in ends]
        else:
            kept = [dict(e) for e in ends[drop:]]
            for number, end in enumerate(kept, start=1):
                end["id"] = end_identity(end)
                end["number"] = number
                end["shots"] = [dict(s) for s in end["shots"]]
                for s in end["shots"]:
                    s["label"] = fmt.shot_label(number, s["number"])
        # Everything a game totals -- the running score, the hammer chain, the
        # clock, what the detector made of it -- was totalled over the practice
        # too, so rebuild rather than patch. Keys build_game does not own (the
        # scoreboard block) survive.
        # The board is keyed by the *real* end number printed on each card,
        # and :mod:`analyze` could only attach it by detected end number --
        # which the practice block in front had already shifted. Dropping the
        # practice is what settles the alignment: the first end kept here is
        # the game's first real end, so each kept end's score is re-derived
        # from the surviving board block rather than carried over. Without
        # this the shift merely becomes invisible, which is worse.
        per_end = board_per_end(board)
        unread = None
        if per_end is not None:
            for end in kept:
                score = per_end.get(end["number"])
                if not isinstance(score, dict):
                    score = None
                end["score"] = None if score is None else dict(score)
                end["score_source"] = None if score is None else "board"
            # Same root cause as the scores, one level up: the ends the
            # board never posted are numbered the same way, so the key
            # would otherwise name ends this chart does not have.
            unread = [n for n in range(1, len(kept) + 1)
                      if not isinstance(per_end.get(n), dict)]
        rebuilt = build_game(game["index"], kept[0]["start_s"], kept[-1]["end_s"],
                             kept, fmt=fmt)
        # The board is not rebuilt, so ordinarily what it says the game
        # finished rides through the trim untouched -- it is a fact about
        # the wall, not a total over the ends we kept. But when re-keying
        # leaves every kept end scored, the pre-trim "final" can be a stale
        # None: it was computed against the *detected* end count, practice
        # included, so a board that covers every real end still came back
        # with something left unread. The running total already in hand on
        # the last kept end is that same sum, correctly, so use it instead.
        # Where an end is still genuinely unread, the None stands.
        if isinstance(board, dict):
            if per_end is not None and not unread:
                rebuilt["final"] = deepcopy(rebuilt["ends"][-1]["running"])
            else:
                rebuilt["final"] = deepcopy(board.get("final"))
        game = document["games"][i] = {**game, **rebuilt}
        if isinstance(board, dict):
            block = {**board}
            if per_end is not None:
                block["unread_ends"] = unread
                block["accounts_for_every_end"] = not unread
                # Whatever the analyser could not place, a start time places.
                block["scores_withheld"] = None
            # The board on the wall shows the game and never the practice, so
            # a disagreement the practice caused has to clear with it. The
            # board's own final does not move when ends are dropped; the
            # detected one does, so that is what it is now compared against.
            # Two totals already in the document; nothing re-reads the video.
            if board.get("final") is not None:
                agrees = board["final"] == game["detected"]["final"]
                block["agrees_with_detection"] = agrees
                for end in game["ends"]:
                    end["scoreboard_agrees"] = agrees
            game["scoreboard"] = block
    return document


def ends_trimmed(document: dict) -> int:
    """How many leading ends :func:`trim_to_start` took off the first game."""
    games = document.get("games") or []
    ends = (games[0].get("ends") if games else None) or []
    return (end_identity(ends[0]) - ends[0]["number"]) if ends else 0
