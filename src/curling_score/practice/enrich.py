"""Everything measured about one delivery, by the passes an end already runs.

`analyze.build_one_end` runs each per-rock pass over an end's shot list. Each
reads one rock at a time, anchored on that rock's own release, crossing or
rest, so here they run over a list of one: the same passes, in the same order,
on the same cameras. Left out is everything that needs an end -- numbering,
the fit, blanks, scores -- and the long camera's release offset, which
`sidereleases.time_side_releases` takes as a median over an end's overhead
releases: alone, a practice throw takes it as zero (the offset measured
-0.11..+0.23 s across games).
"""

import logging
import math
from dataclasses import dataclass

from curling_score.detect import rest
from curling_score.game import (broomtime, fartime, hogtime, linetime, sidereleases,
                                thinking)
from curling_score.game import shots as shots_mod
from curling_score.game.shots import Shot

log = logging.getLogger(__name__)

OTHER_HOUSE = {"top": "bottom", "bottom": "top"}
# The house is read over this long just before the stone arrives and just after
# it rests. An end reads 12 s after each rest, up to the next throw
# (shots.SETTLE_WINDOW_S); a practice card cannot wait that long.
HOUSE_WINDOW_S = 3.0


@dataclass
class Stages:
    """The per-rock passes that read the long cameras, each run in place over a
    list of shots. A test passes its own; `real_stages` gives the pipeline's."""

    hog: object
    side_release: object
    broom: object
    line: object


def real_stages() -> Stages:
    return Stages(hog=hogtime.time_hog_crossings,
                  side_release=sidereleases.time_side_releases,
                  broom=broomtime.time_target_brooms, line=linetime.time_lines)


def _house(frames, t0, t1):
    window = [(t, d) for t, d in frames if t0 <= t <= t1]
    return rest.stones_in_window(window) if window else []


def build_shot(arrival, release, house_frames) -> Shot:
    """The `Shot` for one delivery, as `shots.from_deliveries` builds one in an
    end, from the house just before it arrived and just after it rested. A
    throw that never arrived has no house to read."""
    if arrival is None:
        return Shot(number=1, color=release.color, stones=[], t_rest_s=math.nan,
                    release=release, confidence=0.5, state_known=False)
    before = _house(house_frames, arrival.t_enter - HOUSE_WINDOW_S, arrival.t_enter)
    after = _house(house_frames, arrival.t_rest, arrival.t_rest + HOUSE_WINDOW_S)
    idx = shots_mod._delivered_index(after, arrival, before)
    return Shot(
        number=1, color=arrival.color, stones=after, t_rest_s=arrival.t_rest,
        confidence=1.0 if arrival.came_to_rest else 0.8, delivery=arrival,
        release=release, state_known=bool(after) or not arrival.came_to_rest,
        house_delta=shots_mod.house_delta(before, after, thrower=arrival.color,
                                          delivered=idx),
        delivered_stone_index=idx)


def _run(name, fn, *args, **kw):
    try:
        fn(*args, **kw)
    except Exception:  # noqa: BLE001 - one pass failing costs that figure only
        log.exception("practice: the %s pass failed; the throw goes without it", name)


def enrich(video, setups, sideviews, models, *, house, arrival, release, house_frames,
           throw_frames, stages=None) -> Shot:
    """One throw to ``house``, measured. ``arrival`` is None for a throw that
    never arrived; ``release`` is None when the overhead missed it."""
    stages = stages or real_stages()
    throwing = OTHER_HOUSE[house]
    shot = build_shot(arrival, release, house_frames)
    shots = [shot]
    # The tee crossing for a rock with no release, as an end's clock gets it:
    # the broom window hangs off it.
    _run("tee", thinking.time_shots, shots, throw_frames, setups[throwing].view_y_min_m)
    if sideviews is not None:
        hog_view = sideviews[hogtime.CAMERA_FOR[throwing]]
        dest_view = sideviews[hogtime.CAMERA_FOR[house]]
        _run("hog", stages.hog, shots, video, hog_view)
        _run("side release", stages.side_release, shots, video, hog_view)
        _run("broom", stages.broom, shots, video, dest_view, model=models.broom_model)
        if arrival is not None:
            # The line follows the stone to where it stopped.
            _run("line", stages.line, shots, video, hog_view, dest_view,
                 model=models.line_model)
    _run("far hog", fartime.time_far_crossings, shots,
         near_line=setups[throwing].hog_line, far_line=setups[house].hog_line)
    return shot
