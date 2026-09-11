"""Read the club's wall scoreboard.

The board is the traditional club design: a fixed strip of numbers 1-14 that is
the *cumulative* score, with the end number written on a card hung above it
(yellow) or below it (red). So a card's position is the running total and its
digit is which end produced it -- which means the running total can be read
without any OCR at all, just by asking which slots are occupied.

This is **validation only**. The club often updates the board late, sometimes
several ends late, so it must never be used to time anything.
"""

from dataclasses import dataclass

SLOTS = 14
COLORS = ("red", "yellow")


class ScoreboardError(RuntimeError):
    """The board could not be read or is internally inconsistent."""


@dataclass(frozen=True)
class BoardReading:
    """Which cumulative-score slots carry a card, per team."""

    yellow: set
    red: set

    def is_blank(self) -> bool:
        return not self.yellow and not self.red

    def key(self):
        return (frozenset(self.yellow), frozenset(self.red))


def cumulative(reading: BoardReading) -> dict:
    """The running total each team has reached.

    Cards accumulate left to right, so only the rightmost one matters.
    """
    return {
        "yellow": max(reading.yellow, default=0),
        "red": max(reading.red, default=0),
    }


def split_games(readings):
    """Split a run of readings wherever the board is cleared for a new game."""
    games, current = [], []
    for r in readings:
        if r.is_blank():
            if current:
                games.append(current)
                current = []
            continue
        current.append(r)
    if current:
        games.append(current)
    return games


def per_end_scores(readings) -> list[dict]:
    """Convert a sequence of board states into the score for each end.

    Consecutive identical readings are the same posted state seen twice, so
    only changes count. Each change is one end's score.
    """
    out: list[dict] = []
    prev = {"yellow": 0, "red": 0}
    last_key = None
    for reading in readings:
        if reading.key() == last_key:
            continue
        last_key = reading.key()
        now = cumulative(reading)
        delta = {c: now[c] - prev[c] for c in COLORS}
        if all(v == 0 for v in delta.values()):
            continue
        if any(v < 0 for v in delta.values()):
            raise ScoreboardError(
                f"cumulative score went backwards: {prev} -> {now}"
            )
        if all(v > 0 for v in delta.values()):
            raise ScoreboardError(
                f"both teams cannot score in one end: {prev} -> {now}"
            )
        out.append(delta)
        prev = now
    return out


import cv2
import numpy as np

# The yellow and red team markers, one above the other at the board's left edge.
YELLOW_HSV = ((18, 90, 110), (38, 255, 255))
RED_HSV = [((0, 90, 60), (8, 255, 255)), ((172, 90, 60), (179, 255, 255))]
_MIN_MARKER_AREA = 120
_MARKER_DX = 25  # the two markers sit in the same column
_MARKER_DY = (35, 95)

# Slot geometry, expressed in units of the marker separation. Measured
# consistent to ~1 px across all five sheets.
_SLOT1_OFFSET = 0.737
_SLOT_PITCH = 0.362

# A card is a white tile carrying a black digit, so it is both *brighter* and
# *darker* than the flat grey board around it. Both halves are needed: a
# spectator standing in front of the board has plenty of internal contrast too,
# but is uniformly darker than the board and never brighter. Measured against
# the board's own level: real cards read +22..+29 bright and 63..114 dark;
# empty slots +/-6 either way; a person -105..-59 bright (i.e. never bright).
_CARD_BRIGHT_MARGIN = 12.0
_CARD_DARK_MARGIN = 30.0


@dataclass(frozen=True)
class BoardGeometry:
    """Where the board is and where each card slot sits."""

    anchor_x: float
    yellow_y: float
    red_y: float
    dy: float
    top_line_y: float  # under the sponsor banner
    mid_line_y: float  # under the printed 1-14 row
    bottom_line_y: float  # under the red row
    slot_x: list

    # The printed rules are anti-aliased over 2-3 rows, and that transition is
    # a strong left-to-right brightness ramp (150 -> 40 across the board width).
    # Reading a card band that starts on it makes every slot look occupied, so
    # both bands are inset well clear of their rule.
    @property
    def yellow_row(self):
        """Cards hang between the top border and the printed numbers."""
        gap = self.mid_line_y - self.top_line_y
        return (self.top_line_y + 0.14 * gap, self.top_line_y + 0.55 * gap)

    @property
    def red_row(self):
        """Cards hang between the numbers line and the board's bottom border."""
        gap = self.bottom_line_y - self.mid_line_y
        return (self.mid_line_y + 0.16 * gap, self.mid_line_y + 0.75 * gap)


def _blobs(mask, min_area=_MIN_MARKER_AREA):
    n, _, stats, cent = cv2.connectedComponentsWithStats(mask, 8)
    return [
        (float(cent[i][0]), float(cent[i][1]), int(stats[i, cv2.CC_STAT_AREA]))
        for i in range(1, n)
        if stats[i, cv2.CC_STAT_AREA] >= min_area
    ]


def _horizontal_lines(gray, x0, x1, y0, y1):
    """Rows within a column range that are almost entirely dark."""
    band = gray[max(0, y0) : y1, max(0, x0) : x1]
    if band.size == 0:
        return []
    frac = (band < 120).mean(axis=1)
    rows = [y0 + i for i, v in enumerate(frac) if v >= 0.75]
    runs, out = [], []
    for y in rows:
        if runs and y == runs[-1][-1] + 1:
            runs[-1].append(y)
        else:
            runs.append([y])
    for r in runs:
        out.append(float(np.mean(r)))
    return out


def find_board(image) -> "BoardGeometry | None":
    """Locate the wall scoreboard from its two team-colour markers.

    The board sits at a different place on every sheet's wall, so it is found
    rather than assumed. The yellow marker with the red one directly beneath it
    is a distinctive pair; their separation also fixes the board's scale.
    """
    img = np.asarray(image)
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)

    ymask = cv2.inRange(hsv, np.array(YELLOW_HSV[0], np.uint8),
                        np.array(YELLOW_HSV[1], np.uint8))
    rmask = np.zeros(ymask.shape, np.uint8)
    for lo, hi in RED_HSV:
        rmask |= cv2.inRange(hsv, np.array(lo, np.uint8), np.array(hi, np.uint8))
    kernel = np.ones((3, 3), np.uint8)
    ymask = cv2.morphologyEx(ymask, cv2.MORPH_CLOSE, kernel)
    rmask = cv2.morphologyEx(rmask, cv2.MORPH_CLOSE, kernel)

    reds = _blobs(rmask)
    best = None
    for yx, yy, yarea in sorted(_blobs(ymask), key=lambda b: -b[2]):
        for rx, ry, rarea in reds:
            if abs(rx - yx) > _MARKER_DX:
                continue
            if not (_MARKER_DY[0] <= ry - yy <= _MARKER_DY[1]):
                continue
            score = yarea + rarea
            if best is None or score > best[0]:
                best = (score, yx, yy, ry)
    if best is None:
        return None

    _, ax, ay, ry = best
    dy = ry - ay
    slot_x = [ax + _SLOT1_OFFSET * dy + _SLOT_PITCH * dy * k for k in range(SLOTS)]

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(float)
    x0, x1 = int(slot_x[0]), int(slot_x[-1])
    lines = _horizontal_lines(gray, x0, x1, int(ay - 0.6 * dy), int(ry + 0.8 * dy))
    # Three printed rules bound the score area, and the two colour markers say
    # which is which: one above the yellow marker, one between the markers, one
    # below the red marker. Picking the outermost pair instead would span the
    # whole board and put both card rows in the wrong place.
    above = [y for y in lines if y < ay]
    between = [y for y in lines if ay < y < ry]
    below = [y for y in lines if y > ry]
    if not above or not between:
        return None
    top_line = max(above)
    mid_line = min(between)
    # The bottom rule is sometimes lost to glare; fall back on the measured
    # ratio of the red row's height to the marker separation.
    bottom_line = min(below) if below else mid_line + 0.62 * dy

    return BoardGeometry(
        anchor_x=ax, yellow_y=ay, red_y=ry, dy=dy,
        top_line_y=top_line, mid_line_y=mid_line, bottom_line_y=bottom_line,
        slot_x=slot_x,
    )


def read_slots(image, geom: BoardGeometry) -> BoardReading:
    """Which slots carry a card, judged by intra-slot brightness range."""
    gray = cv2.cvtColor(np.asarray(image), cv2.COLOR_BGR2GRAY).astype(float)
    h, w = gray.shape
    half_w = max(2, int(0.14 * geom.dy))

    found = {}
    for name, (ry0, ry1) in (("yellow", geom.yellow_row), ("red", geom.red_row)):
        y0, y1 = int(ry0), int(ry1)
        boxes = []
        for sx in geom.slot_x:
            x0, x1 = int(sx) - half_w, int(sx) + half_w
            if x0 < 0 or y0 < 0 or x1 > w or y1 > h:
                boxes.append(None)
            else:
                box = gray[y0:y1, x0:x1]
                boxes.append(box if box.size >= 12 else None)

        present = [b for b in boxes if b is not None]
        if not present:
            found[name] = set()
            continue
        # Most slots are empty, so the median slot is a robust read of the bare
        # board's brightness -- and it adapts to lighting drift over an evening.
        level = float(np.median([np.median(b) for b in present]))

        slots = set()
        for k, box in enumerate(boxes, start=1):
            if box is None:
                continue
            bright = float(np.percentile(box, 92)) - level
            dark = level - float(np.percentile(box, 8))
            if bright >= _CARD_BRIGHT_MARGIN and dark >= _CARD_DARK_MARGIN:
                slots.add(k)
        found[name] = slots
    return BoardReading(yellow=found["yellow"], red=found["red"])


# The printed 1-14 row is the board's own integrity check: it is always there,
# so if it cannot be seen, something is standing in front of the board.
_MIN_PRINTED_DIGITS = 11


def _printed_digit_groups(image, geom: BoardGeometry) -> int:
    """Count the digit groups visible in the printed 1-14 row."""
    gray = cv2.cvtColor(np.asarray(image), cv2.COLOR_BGR2GRAY)
    gap = geom.mid_line_y - geom.top_line_y
    y0, y1 = int(geom.top_line_y + 0.58 * gap), int(geom.mid_line_y - 0.03 * gap)
    x0, x1 = int(geom.slot_x[0] - 0.3 * geom.dy), int(geom.slot_x[-1] + 0.3 * geom.dy)
    h, w = gray.shape
    if y0 < 0 or y1 > h or x0 < 0 or x1 > w or y1 - y0 < 4:
        return 0

    band = gray[y0:y1, x0:x1]
    dark = (band < 130).mean(axis=0) > 0.20
    # A person is a wide solid block; real digits are narrow with gaps between.
    groups, run = 0, 0
    max_run = 0
    for on in dark:
        if on:
            run += 1
        else:
            if run >= 2:
                groups += 1
            max_run = max(max_run, run)
            run = 0
    if run >= 2:
        groups += 1
    max_run = max(max_run, run)
    if max_run > 0.9 * geom.dy:  # a solid block far wider than any numeral
        return 0
    return groups


def is_readable(image, geom: BoardGeometry) -> bool:
    """Whether the board is unobstructed enough to trust a reading."""
    if geom is None:
        return False
    return _printed_digit_groups(image, geom) >= _MIN_PRINTED_DIGITS


def read_board(image, geom: BoardGeometry | None = None) -> "BoardReading | None":
    """Find and read the board, or return None if it cannot be trusted."""
    geom = geom if geom is not None else find_board(image)
    if geom is None or not is_readable(image, geom):
        return None
    return read_slots(image, geom)


def median_frame(frames):
    """Median of several frames: removes anyone walking past the board.

    Cards stay hung for a whole end, so a window of a minute or two is far
    shorter than the board changes but far longer than anyone stands still.
    """
    stack = np.stack([np.asarray(f) for f in frames])
    return np.median(stack, axis=0).astype(np.uint8)


def read_board_at(video_path, t_seconds, window_s=90.0, max_frames=40):
    """Read the board around a moment in the video, de-occluded by median.

    Samples keyframes rather than a fixed frame rate. A fixed rate still makes
    the decoder reconstruct every frame in the window -- roughly 180 s of
    full-resolution video per read, and a game needs a dozen or more. The board
    only changes once an end, so the ~1 frame per 5 s that keyframes give is
    ample, and vastly cheaper.
    """
    from curling_score.ingest import frames as F

    lo, hi = max(0.0, t_seconds - window_s), t_seconds + window_s
    imgs = []
    for t, img in F.keyframe_sweep(video_path, start_s=lo, end_s=hi):
        if t < lo:
            continue
        if len(imgs) >= max_frames:
            break
        imgs.append(img)
    if not imgs:
        return None
    return read_board(median_frame(imgs))


# How many readings a slot must appear in before it is believed.
_CONFIRM_READINGS = 2


def consolidate(readings, confirm: int = _CONFIRM_READINGS):
    """Enforce the physical constraint that cards only accumulate.

    Within a game a card is hung and stays hung, so the occupied set can only
    grow. A slot seen once and then gone was noise -- usually someone standing
    in front of the lower half of the board, which is why the red row flickers
    far more than the yellow one. Requiring a slot to be seen ``confirm`` times
    before it is believed, and keeping it thereafter, removes that flicker
    without discarding a card that a single frame happens to miss.
    """
    readings = list(readings)
    counts = {"yellow": {}, "red": {}}
    first = {"yellow": {}, "red": {}}
    for i, r in enumerate(readings):
        for name, slots in (("yellow", r.yellow), ("red", r.red)):
            for k in slots:
                counts[name][k] = counts[name].get(k, 0) + 1
                first[name].setdefault(k, i)

    # A confirmed card was already hanging the first time we saw it, so credit
    # it from that reading onward rather than from the one that confirmed it.
    out = []
    for i in range(len(readings)):
        got = {}
        for name in ("yellow", "red"):
            got[name] = {
                k
                for k, n in counts[name].items()
                if n >= confirm and first[name][k] <= i
            }
        out.append(BoardReading(yellow=got["yellow"], red=got["red"]))
    return out
