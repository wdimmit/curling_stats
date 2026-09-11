"""Name what a shot was, from how the stone travelled and what it changed.

This is deliberately coarse. Curl Coach's taxonomy -- hit and stick, hit and
roll, peel, raise, run back, tick, freeze, come around -- is mostly a statement
about what the skip *asked for*, and no amount of tracking recovers intent: a
stone that removes a guard is a peel if that was the call and a wrecked draw if
it was not. So we report only what can be seen:

    draw     came to rest in the house
    guard    came to rest in play, short of the house
    hit      moved or removed a stone that was already there
    through  ran out of play without leaving anything behind
    unknown  not enough of the flight was seen to say

and leave the fine-grained type to the person charting, who knows the call.

The only evidence for a hit is the house. Entry speed looked like it should
work as a second opinion -- takeout weight against draw weight -- and it does
not. Measured across both games of the reference VOD, labelling each delivery by
whether it moved anything (evidence independent of velocity):

    house changed    n=82   median 0.80 m/s   max 1.73
    house unchanged  n=118  median 0.38 m/s   max 3.29

The distributions overlap end to end. The stone is only in this view for its
last few metres, by which point a takeout has shed most of its weight, so the
measurement that should separate them barely does. A takeout that missed
everything is therefore indistinguishable from a draw, and this says ``unknown``
rather than guessing.

The speed is still reported, because a person watching the video can use it.
It just does not decide anything.
"""

from curling_score.geometry import constants as C

DRAW = "draw"
GUARD = "guard"
HIT = "hit"
THROUGH = "through"
UNKNOWN = "unknown"

# A shot confirmed by what it did to the house is as certain as we get; one
# resting cleanly in or out of the house is nearly so; one resting right on the
# house edge is a coin toss on which side of the line the detector put it.
CONF_HOUSE_CHANGED = 0.95
CONF_CLEAR_REST = 0.85
CONF_BOUNDARY = 0.5
# How close to the house edge counts as "on the boundary" for confidence.
BOUNDARY_M = 0.15


def _changed(delta) -> bool:
    """Whether this shot moved anything that was already on the sheet."""
    if not delta:
        return False
    return bool(delta.get("removed") or delta.get("moved"))


def classify(delivery, house_delta=None):
    """Return ``(category, confidence)`` for one delivery.

    ``house_delta`` is the before/after diff from :func:`shots.house_delta`;
    pass None when it could not be computed, which costs the hit evidence but
    not the geometry.
    """
    if delivery is None:
        return UNKNOWN, 0.0

    # A stone that struck something is a hit regardless of where it ended up --
    # including a shooter that rolled out, which is the case the geometry alone
    # would read as "through".
    if _changed(house_delta):
        return HIT, CONF_HOUSE_CHANGED

    rest_x, rest_y = delivery.rest_x_m, delivery.rest_y_m
    dist = (rest_x * rest_x + rest_y * rest_y) ** 0.5

    # Out of play, having disturbed nothing: thrown through the house. It may
    # equally have been a takeout that missed, but nothing observable here
    # tells the two apart, so the charter gets the honest answer.
    if delivery.reason == "left-view" or rest_y <= C.THROUGH_BACK_Y_M:
        return THROUGH, CONF_CLEAR_REST

    if not delivery.came_to_rest:
        return UNKNOWN, 0.0

    edge = abs(dist - C.IN_HOUSE_MAX_D_M)
    if dist <= C.IN_HOUSE_MAX_D_M:
        return DRAW, CONF_BOUNDARY if edge < BOUNDARY_M else CONF_CLEAR_REST
    if rest_y > 0:
        return GUARD, CONF_BOUNDARY if edge < BOUNDARY_M else CONF_CLEAR_REST
    # Past the tee and outside the rings: a draw thrown too deep, not a guard.
    return DRAW, CONF_BOUNDARY


def classify_shot(shot):
    """Classify a :class:`shots.Shot`, which may be a placeholder."""
    if shot.missing or shot.delivery is None:
        return UNKNOWN, 0.0
    return classify(shot.delivery, shot.house_delta)
