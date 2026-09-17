"""Name what a shot was, from how the stone travelled and what it changed.

This is deliberately coarse. Curl Coach's taxonomy -- hit and stick, hit and
roll, peel, raise, run back, tick, freeze, come around -- is mostly a statement
about what the skip *asked for*, and no amount of tracking recovers intent: a
stone that removes a guard is a peel if that was the call and a wrecked draw if
it was not. So we report only what can be seen:

    draw          came to rest in the house
    guard         came to rest in play, outside the house
    hit           moved or removed a stone that was already there
    draw_through  left play having taken the time a draw takes to get there
    flashed       left play quickly: a takeout that touched nothing
    hogged        seen thrown, never reached the house (see detect.release)
    unknown       not enough of the flight was seen to say

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

The long split does, and the difference is worth being clear about. It is not
the same measurement taken more carefully: it is two hog-line crossings timed
at the paint, 27 m apart, neither of them extrapolated through a panel scale
that is collapsing at exactly the distance the entry speed is read (see
``game.split``). Draws cross in 18-20 s and takeouts in roughly half that, so
where the entry speeds overlapped end to end these do not overlap at all. It
is allowed to separate a draw thrown through the house from a takeout that
flashed, and nothing else.

Most throws have no split -- the far camera loses them before the paint -- and
those default to ``flashed``, which is a policy rather than a reading. The
confidence is what says so.
"""

from curling_score.game import split
from curling_score.geometry import constants as C

DRAW = "draw"
GUARD = "guard"
HIT = "hit"
DRAW_THROUGH = "draw_through"
FLASHED = "flashed"
HOGGED = "hogged"
UNKNOWN = "unknown"

# A shot confirmed by what it did to the house is as certain as we get; one
# resting cleanly in or out of the house is nearly so; one resting right on the
# house edge is a coin toss on which side of the line the detector put it.
CONF_HOUSE_CHANGED = 0.95
CONF_CLEAR_REST = 0.85
CONF_BOUNDARY = 0.5
# How close to the house edge counts as "on the boundary" for confidence.
BOUNDARY_M = 0.15
# Hog to hog, above this is draw weight and below it is takeout weight.
SPLIT_HIT_MAX_S = 12.5


def _changed(delta) -> bool:
    """Whether this shot moved anything that was already on the sheet."""
    if not delta:
        return False
    return bool(delta.get("removed") or delta.get("moved"))


def classify(delivery, house_delta=None, release=None):
    """Return ``(category, confidence)`` for one delivery.

    ``house_delta`` is the before/after diff from :func:`shots.house_delta`;
    pass None when it could not be computed, which costs the hit evidence but
    not the geometry. ``release`` is the throw this arrival was paired to, and
    is only ever read to time the long split; pass None and every shot that
    left play reads as a flash.
    """
    if delivery is None:
        return UNKNOWN, 0.0
    # Seen leaving the thrower's house and never arriving here: taken out of
    # play at the hog line. The house cannot say anything about it.
    if delivery.reason == "hogged":
        return HOGGED, CONF_CLEAR_REST
    # Seen thrown, arrival unseen, and a stone went missing from the house
    # meanwhile: it ran through and took that stone with it.
    if delivery.reason == "release-remove":
        return HIT, CONF_HOUSE_CHANGED

    # A stone that struck something is a hit regardless of where it ended up --
    # including a shooter that rolled out, which is the case the geometry alone
    # would read as a flash.
    if _changed(house_delta):
        return HIT, CONF_HOUSE_CHANGED

    rest_x, rest_y = delivery.rest_x_m, delivery.rest_y_m
    dist = (rest_x * rest_x + rest_y * rest_y) ** 0.5

    # Out of play, having disturbed nothing: either a draw thrown far too heavy
    # or a takeout that flashed. Only the long split tells them apart, and most
    # shots do not have one -- those fall to the takeout, which is the commoner
    # way to end up here, at a confidence that admits it was not measured.
    if delivery.reason == "left-view" or rest_y <= C.THROUGH_BACK_Y_M:
        sp = split.long_split(release, delivery)
        if sp is None:
            return FLASHED, CONF_BOUNDARY
        if sp.seconds > SPLIT_HIT_MAX_S:
            return DRAW_THROUGH, CONF_CLEAR_REST
        return FLASHED, CONF_CLEAR_REST

    if not delivery.came_to_rest:
        return UNKNOWN, 0.0

    # The tee line decides nothing: a stone in the twelve-foot is a draw, and
    # one resting in play outside it is a guard, whether it is in front of the
    # house or behind it.
    edge = abs(dist - C.IN_HOUSE_MAX_D_M)
    conf = CONF_BOUNDARY if edge < BOUNDARY_M else CONF_CLEAR_REST
    return (DRAW if dist <= C.IN_HOUSE_MAX_D_M else GUARD), conf


def classify_shot(shot):
    """Classify a :class:`shots.Shot`, which may be a placeholder."""
    if shot.missing or shot.delivery is None:
        return UNKNOWN, 0.0
    return classify(shot.delivery, shot.house_delta, shot.release)
