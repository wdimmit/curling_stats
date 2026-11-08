"""Guards the overhead cannot see, placed from the long camera behind the thrower.

DFW's overheads show the house and about 3.5 m in front of the tee: a guard
resting between there and the hog line is in no panel, and the overhead's
house state has no stone for it. The long camera at the throwing end looks
down the sheet at that ice. At each rock's rest, the stones it shows in that
band are mapped onto the house (`SideView.to_house`, read at the stone's
footprint: its near edge, toward the camera, one radius from its centre) and
added to that rock's stones as ``"source": "long"`` -- an estimate, the camera
seeing that ice at a shallow angle (roughly +/-0.2-0.4 m). A stone already
in the overhead's state (within DEDUPE_M) is not added twice. Each rock's
state is read at its own rest, so a guard stays until it is hit or the end is
over. A rock the overhead could not place (no ``delivered_stone_index``) is
marked as the long stone of its colour that its own rest brought -- and one the
far house never saw arrive, timed across the far hog line, is read once it has
had time to stop there (`_read_at`) and, found, is a guard.
"""

import math

from curling_score.geometry import constants as C

# Within this of a stone the state has already, a long stone is that stone.
DEDUPE_M = 0.45
# Past the hog line, a stone is out of play.
MAX_Y_M = C.TEE_TO_HOGLINE_M + C.STONE_RADIUS_M
# Read this long after a rock's rest: the stone has stopped, sweepers moving off.
AFTER_REST_S = 0.5
# A rock the far house never saw arrive has no rest of its own -- it "rests"
# MIN_LAG_S after its release, still sliding mid-sheet. Timed across the far
# hog line, it is read this long after that crossing instead: DFW's guards
# stop 3-5 s past the line (46wEMds_tYQ end 3 rock 1: crossed at +19.0 s,
# still from +23.5 s). Never later than just before the next rock is thrown.
AFTER_FAR_S = 6.0
BEFORE_NEXT_S = 1.0
# What a rock nothing else could name becomes when the guard it left is found.
NAMEABLE = ("unknown", "hogged")


def _read_at(shot, next_shot) -> float:
    """When to read the stones a rock left: just after its rest, or for one with
    no arrival but a far hog crossing, once it has had time to stop past it."""
    t = shot["t_rest_s"] + AFTER_REST_S
    if shot.get("delivered_stone_index") is None and shot.get("t_far_s") is not None:
        t = max(t, shot["t_far_s"] + AFTER_FAR_S)
        nxt = (next_shot or {}).get("t_release_s")
        if nxt is not None:
            t = min(t, nxt - BEFORE_NEXT_S)
    return t


def place(stones, view, known, from_m: float) -> list[dict]:
    """``stones`` -- ``(edge_row, cx, color)`` in view pixels -- as timeline
    stones, for those resting between ``from_m`` in front of the tee and the
    hog line, on the sheet, and not already ``known`` (house dicts)."""
    out = []
    for row, cx, color in stones:
        x, y_edge = view.to_house(cx, row)
        y = y_edge - C.STONE_RADIUS_M
        if not (from_m <= y <= MAX_Y_M) or abs(x) > C.SIDELINE_ABS_X_M:
            continue
        if any(math.hypot(x - k["x"], y - k["y"]) < DEDUPE_M for k in list(known) + out):
            continue
        x, y = round(x, 4), round(y, 4)
        out.append({"color": color, "x": x, "y": y,
                    "distance_to_tee": math.hypot(x, y), "in_house": False,
                    "confidence": None, "source": "long"})
    return out


def add(end: dict, view, read_stones, from_m: float) -> int:
    """Add the long camera's guards to each rock of a built ``end`` (and its
    final stones). ``read_stones(t)`` gives the ``(edge_row, cx, color)`` of
    the stones the view shows at ``t``. Returns how many long stones it added
    in all."""
    n, last = 0, []
    shots = end.get("shots") or []
    for i, shot in enumerate(shots):
        if shot.get("missing") or shot.get("t_rest_s") is None:
            continue
        stones = shot.setdefault("stones", [])
        nxt = next((s for s in shots[i + 1:] if not s.get("missing")), None)
        extra = place(read_stones(_read_at(shot, nxt)), view, stones, from_m)
        if shot.get("delivered_stone_index") is None:
            new = [g for g in extra if g["color"] == shot.get("color")
                   and not any(math.hypot(g["x"] - p["x"], g["y"] - p["y"]) < DEDUPE_M for p in last)]
            if len(new) == 1:
                shot["delivered_stone_index"] = len(stones) + extra.index(new[0])
                if shot.get("shot_type") in NAMEABLE and shot.get("t_far_s") is not None:
                    shot["shot_type"] = "guard"
        stones.extend(extra)
        n += len(extra)
        last = extra
    if last and end.get("final_stones") is not None:
        end["final_stones"].extend(dict(g) for g in last)
    return n


READ_S, READ_FPS = 0.6, 10.0


def stones_reader(video, view, *, model=None, decode=None, propose=None):
    """``read_stones(t)`` for `add`: the stones of both colours the side model
    finds in ``view`` over READ_S from ``t``, each a track's median
    ``(edge_row, cx, color)`` -- they are standing still."""
    import numpy as np

    from curling_score.detect import longview, sidemodel

    model = model or sidemodel.default_model()
    decode = decode or longview.decode
    propose = propose or sidemodel.propose

    def read(t):
        if model is None:
            return []
        frames, times = decode(video, view.rect, t, t + READ_S, READ_FPS)
        if not len(frames):
            return []
        out = []
        for color in ("red", "yellow"):
            for track in propose(model, frames, view, color, times).values():
                if len(track) >= 2:
                    out.append((float(np.median([r for _t, r, *_ in track])),
                                float(np.median([c for *_x, c in track])), color))
        return out
    return read
