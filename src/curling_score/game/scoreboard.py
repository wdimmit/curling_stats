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

    @property
    def printed_row(self):
        """The printed 1-14 numbers, under the yellow cards in the same band.

        Same bounds `_printed_digit_groups` already uses to count them.
        """
        gap = self.mid_line_y - self.top_line_y
        return (self.top_line_y + 0.58 * gap, self.mid_line_y - 0.03 * gap)


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


# Glyphs are normalised to a fixed box before correlating, so a card and a
# printed number are compared on shape alone rather than on size or exposure.
GLYPH_SHAPE = (22, 16)  # (rows, cols)


def _min_count_bbox(mask, min_count):
    """Bounding box of `mask`, requiring several hits before a row or column
    counts.

    A single stray pixel -- a reflection, adjacent-slot bleed, a rule's
    anti-aliasing -- can be the only lit pixel in its row or column, yet a
    plain bbox-of-nonzero accepts it and stretches all the way out to it,
    which is a bigger box than the pixel earned. Real ink or a real card tile
    lights up several pixels together, not one, so requiring a minimum count
    per row and per column tells the two apart.
    """
    rows = np.flatnonzero(mask.sum(axis=1) >= min_count)
    cols = np.flatnonzero(mask.sum(axis=0) >= min_count)
    if rows.size == 0 or cols.size == 0:
        return None
    return rows.min(), rows.max() + 1, cols.min(), cols.max() + 1


# Ink reads at least this much darker than the window's own median -- true
# for a digit against bare board or against a white card alike.
_INK_MARGIN = 12.0
_MIN_INK_SUPPORT = 3   # rows/cols need this many ink pixels to count
_MIN_INK_PIXELS = 6    # fewer than this many ink pixels total is noise


def _glyph_ink(patch):
    """Localise a digit's ink within a raw window.

    Shared by `templates()` and `_card_glyph()` so a card and a printed
    number are localised exactly the same way before either is ever fitted
    into GLYPH_SHAPE and compared. Returns None where there isn't enough ink
    to trust -- a blank tile or an empty stretch of board.
    """
    level = float(np.median(patch))
    ink = patch < level - _INK_MARGIN
    if ink.sum() < _MIN_INK_PIXELS:
        return None
    bbox = _min_count_bbox(ink, _MIN_INK_SUPPORT)
    if bbox is None:
        return None
    y0, y1, x0, x1 = bbox
    return patch[y0:y1, x0:x1]


def _normalise(patch) -> "np.ndarray":
    """Fit to GLYPH_SHAPE preserving aspect ratio, mean-centre, unit std.

    Padded onto the fixed box rather than stretched to fill it: stretching a
    tight crop erases the one cue that tells a narrow "1" from a wide "3" --
    their relative width -- before correlation ever sees it.
    """
    pad_value = float(np.median(patch))
    ph, pw = patch.shape
    th, tw = GLYPH_SHAPE
    scale = min(th / ph, tw / pw)
    nh, nw = max(1, round(ph * scale)), max(1, round(pw * scale))
    resized = cv2.resize(patch.astype(np.float32), (nw, nh),
                          interpolation=cv2.INTER_AREA)
    g = np.full(GLYPH_SHAPE, pad_value, dtype=np.float32)
    y0, x0 = (th - nh) // 2, (tw - nw) // 2
    g[y0: y0 + nh, x0: x0 + nw] = resized
    g -= g.mean()
    sd = float(g.std())
    return g / sd if sd > 1e-6 else g


def templates(image, geom: BoardGeometry) -> dict:
    """The printed 1-14 glyphs, as normalised correlation templates.

    Built per read rather than shipped: this tracks the board's own lighting,
    the sheet's own camera and any change to the board itself for free.
    """
    gray = cv2.cvtColor(np.asarray(image), cv2.COLOR_BGR2GRAY)
    y0, y1 = (int(v) for v in geom.printed_row)
    half_w = max(2, int(0.22 * geom.dy))
    out = {}
    for k, sx in enumerate(geom.slot_x, start=1):
        x0, x1 = int(sx) - half_w, int(sx) + half_w
        box = gray[y0:y1, x0:x1].astype(np.float32)
        glyph = _glyph_ink(box)
        # A printed digit is always there; fall back to the raw box rather
        # than dropping a slot outright if the ink test somehow comes up dry.
        out[k] = _normalise(glyph if glyph is not None else box)
    return out


# Below this margin between the best and second-best template match, the digit
# is not called. Set in Task 6 against the labelled harvest; a wrong digit is
# worse than no digit, so this fails closed.
MIN_MARGIN = 0.05

# The card is a bright tile inside the slot. Segmenting it beats a fixed band,
# which clips the glyph tops -- the card sits higher in the band than the
# presence test needs to look.
_CARD_TILE_MARGIN = 10.0
_MIN_TILE_SUPPORT = 2   # rows/cols need this many bright pixels to count


def card_window(gray, geom: BoardGeometry, color: str, slot: int):
    """The raw slot window a card's glyph is found inside, or None off-frame.

    Split out of `_card_glyph` so the card-digit training set can store the
    *untouched* window rather than a processed glyph. Tile segmentation, ink
    localisation and GLYPH_SHAPE have each already been changed once in
    response to a measurement, and each change invalidates anything stored
    downstream of it; a stored window survives all three, so the set can be
    re-derived without re-downloading 15 GB of video. Public for that reason,
    and shared with `_card_glyph` so the two can never drift apart.
    """
    row = geom.yellow_row if color == "yellow" else geom.red_row
    y1 = int(row[1])
    # The card's top sits above the presence band, so reach up to the rule.
    y0 = int(geom.top_line_y) if color == "yellow" else int(geom.mid_line_y)
    half_w = max(2, int(0.22 * geom.dy))
    sx = geom.slot_x[slot - 1]
    x0, x1 = int(sx) - half_w, int(sx) + half_w
    h, w = gray.shape
    if x0 < 0 or y0 < 0 or x1 > w or y1 > h or y1 - y0 < 6:
        return None
    return gray[y0:y1, x0:x1]


def _card_glyph(gray, geom: BoardGeometry, color: str, slot: int):
    """One card's ink, localised the same way `templates()` localises a
    printed number.

    Returns None where the slot falls outside the frame, no bright tile is
    found (an empty slot), or no ink is found inside the tile (a blank
    card). The card is found by its tile first -- a printed number has no
    tile to find it by, but a card does, and a spectator's hand never
    brightens anything -- and then its ink is localised inside that tile with
    the same `_glyph_ink` that localises a printed number's ink in its own
    raw window. Localising both the same way, and fitting both into
    GLYPH_SHAPE the same way, keeps a card and its template at the same
    effective scale: cropping one tighter than the other -- or stretching
    one to fill the box and not the other -- puts them at different scales
    that correlation reads as a real difference before digit shape gets a
    say.
    """
    window = card_window(gray, geom, color, slot)
    if window is None:
        return None

    box = window.astype(np.float32)
    level = float(np.median(box))
    tile = box > level + _CARD_TILE_MARGIN     # the white card against the board
    if tile.sum() < 12:
        return None
    bbox = _min_count_bbox(tile, _MIN_TILE_SUPPORT)
    if bbox is None:
        return None
    ty0, ty1, tx0, tx1 = bbox
    card = box[ty0:ty1, tx0:tx1]
    if card.size < 12:
        return None

    return _glyph_ink(card)


def read_digit(glyph, tmpl: dict) -> tuple:
    """Best-matching printed digit for a card glyph, and its margin.

    The margin over the runner-up is the confidence. An occluded or clipped
    glyph matches several templates about equally well, so its margin
    collapses -- which is exactly the signal to refuse it.
    """
    if glyph is None or glyph.size < 4:
        return None, 0.0
    g = _normalise(glyph)
    scored = sorted(
        ((float((g * t).mean()), k) for k, t in tmpl.items()), reverse=True
    )
    best, runner = scored[0], scored[1]
    margin = best[0] - runner[0]
    return (best[1] if margin >= MIN_MARGIN else None), margin


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
