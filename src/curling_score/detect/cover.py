"""Whether a stone the house read lost was only covered by a player.

A stone the overhead stops seeing is either gone or hidden, and the stone
detections alone cannot say which: a skip crouched over a stone behind the
house for the whole of a rock's window reads exactly like that stone knocked
out of play (6vOTUuUSMKc g1 e7 rock 15, f_0zkA). The detector's own player
boxes (`rocks.Seen.players`, class 2) can. A hidden stone's spot is under a
player in every frame it goes unseen, where one really taken out leaves bare
ice there as soon as nobody stands on it.

An off-the-shelf COCO pose model finds the players from above, though not
easily: it learned people side-on, and a crouched body seen from straight up
is a cap and two shoulders. On that rock yolo11n and yolo11s missed the
skip outright; yolo11l-pose at 0.3 found him only once he had settled, and
yolo11x-pose at 0.1 nearly throughout. The harness (16 games, 1,718 rocks)
put back 15 stones that way, every one checked against the video. The
overhead detector, trained on players, reads them without that second model;
a frame with no ``players`` attribute -- a plain list, the classical detector,
a two-class model -- has none, and nothing is put back from it.
"""

from curling_score.geometry import constants as C

# A detection of the stone's colour this close to its spot is the stone,
# seen: that frame says nothing about cover.
SEEN_M = 0.15
# A stone counts as covered when a player was over its spot in this share of
# the frames it went unseen in, and it went unseen in at least MIN_UNSEEN of
# them. Anything less means some frame showed its spot open and empty: the
# stone left. The house is read at 10 fps, so five frames is half a second.
COVERED_SHARE = 0.8
MIN_UNSEEN = 5


def covered(x_m: float, y_m: float, color: str, window) -> bool:
    """Was this stone hidden by a player whenever ``window`` -- the read's
    ``(t, detections)`` frames -- missed it?

    A player's box, grown by a stone's radius, is over the spot when it holds
    it: the stone is under the box if its edge is. A frame showing a stone of
    the *other* colour on the spot counts unseen and never covered, player
    box or not: a stone sitting there proves the spot is in view, so ours is
    not under anybody (a hit-and-stick leaves the shooter where the removed
    stone sat, a sweeper beside it).
    """
    unseen = hidden = 0
    for _, dets in window:
        near = [s for s in dets
                if ((s.x_m - x_m) ** 2 + (s.y_m - y_m) ** 2) ** 0.5 <= SEEN_M]
        if any(s.color == color for s in near):
            continue
        unseen += 1
        if near:
            continue
        pad = C.STONE_RADIUS_M
        hidden += any(p.x0_m - pad <= x_m <= p.x1_m + pad and p.y0_m - pad <= y_m <= p.y1_m + pad
                      for p in getattr(dets, "players", ()))
    return unseen >= MIN_UNSEEN and hidden >= COVERED_SHARE * unseen
