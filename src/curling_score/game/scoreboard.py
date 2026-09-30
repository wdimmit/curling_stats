"""Read the club's wall scoreboard.

The board is the traditional club design: a fixed strip of numbers 1-14 that is
the *cumulative* score, with the end number written on a card hung above it
(yellow) or below it (red). So a card's slot is the cumulative total it
records, and the digit on the card names the end that produced it -- which
means one late read of the board, with each card's digit decoded, carries the
score of every end that has been posted so far.

This is **validation only**. The club often updates the board late, sometimes
several ends late, so it must never be used to time anything.
"""

from dataclasses import dataclass

from curling_score.geometry import constants as C

SLOTS = 14
COLORS = ("red", "yellow")
# A team throws eight rocks, so eight is the most it can ever score in one
# end. A bigger step between two of its cards is physically impossible and
# says a card or a digit was misread.
MAX_SCORE_PER_END = C.STONES_PER_TEAM_PER_END


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


def cumulative(board) -> dict:
    """The running total each team has reached.

    Cards accumulate left to right, so only the rightmost one matters. Works
    over a raw `BoardReading` (slots are bare ints) or over a `CardBoard`
    (slots are `Card`s, defined below): either way a card whose digit was
    refused still counts here, since where it sits is read straight off the
    board and does not depend on the glyph.
    """
    def _slot(x):
        return x.slot if isinstance(x, Card) else x

    return {
        "yellow": max((_slot(x) for x in board.yellow), default=0),
        "red": max((_slot(x) for x in board.red), default=0),
    }


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
    pairs = []
    for yx, yy, yarea in _blobs(ymask):
        for rx, ry, rarea in reds:
            if abs(rx - yx) > _MARKER_DX:
                continue
            if not (_MARKER_DY[0] <= ry - yy <= _MARKER_DY[1]):
                continue
            pairs.append((yarea + rarea, yx, yy, ry))

    # The largest pair first -- but since the 2026 re-aim, sheets 3 and 4's
    # camera also shows the next sheet's markers at the frame's right edge,
    # about the size of the home board's, with the rest of that board out of
    # shot. A board whose card slots run off the frame cannot be read, so it is
    # passed over, and so is a pair with no printed rules around it.
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(float)
    for _, ax, ay, ry in sorted(pairs, reverse=True):
        geom = _board_at(gray, ax, ay, ry)
        if geom is not None:
            return geom
    return None


def _board_at(gray, ax, ay, ry) -> "BoardGeometry | None":
    """The board whose markers are at (ax, ay) over (ax, ry), or None when its
    card slots run off the frame or its printed rules are not where the
    markers put them."""
    dy = ry - ay
    slot_x = [ax + _SLOT1_OFFSET * dy + _SLOT_PITCH * dy * k for k in range(SLOTS)]
    if slot_x[-1] >= gray.shape[1]:
        return None
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

    Not dead code: `read_digit` stopped consuming these in Task 6F -- it reads
    with a trained classifier now -- but this is also the self-labelling
    training-data extractor `scripts/harvest_glyphs.py` calls (the digit at
    slot *k* is *k*), and `tests/test_digits.py` reads templates from the card
    frames themselves as a printed-digit comparison. Kept for that.

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


# Below this class probability, the digit is not called. Ruling R19: the coarse
# grid value, not the per-fold "tight" threshold that the leave-one-video-out
# measurement fits to the very data it is scored on. At 0.9999 the measured
# run showed zero wrong digits *and* zero of 19 phantom (no-card) frames
# accepted, at 88.3% coverage -- still above the 87% gate. 0.999 buys about
# five more points of coverage but let a phantom through, and a phantom
# invents a scoring end no board self-check can catch (a 3-point end is legal).
# A wrong digit is worse than no digit, so this fails closed.
MIN_CONFIDENCE = 0.9999

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
    return glyph_in_window(window)


def glyph_in_window(window):
    """Everything `_card_glyph` does *after* `card_window`.

    Split out for the same reason `card_window` was: the card-digit training
    set stores raw windows, and a model trained on those raw windows while
    measured through `_card_glyph` is measuring a path production never takes.
    That mistake has been made here once already and produced a confident 81.5%
    that meant nothing. With the tail a function, the training set and
    production run the same code over the same pixels by construction rather
    than by two copies agreeing.
    """
    box = np.asarray(window, dtype=np.float32)
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


def read_digit(glyph, tmpl: dict | None = None) -> tuple:
    """Best-matching card digit for a glyph, and the model's confidence.

    Backed by the trained classifier in `digits` (`digits.ConvModel`, promoted
    to production in Task 6F). ``tmpl`` is accepted only so callers built
    against the old correlation matcher still type-check; it is unused --
    the model needs no per-read printed-row templates, only the glyph itself.

    The second element is the winning class *probability*, not a correlation
    margin: an occluded or clipped glyph spreads its probability mass over
    several classes, so its top probability collapses towards uniform, which
    is exactly the signal to refuse it below `MIN_CONFIDENCE`.
    """
    if glyph is None or glyph.size < 4:
        return None, 0.0
    from curling_score.game import digits as D  # deferred: digits imports
                                                  # this module at load time

    digit, conf = D.load_default_model().predict(glyph)
    return (digit if conf >= MIN_CONFIDENCE else None), conf


def _row_boxes(gray, row_bounds, slot_x, half_w):
    """Each slot's raw window in one row, or None where it falls off-frame.

    Split out of `read_slots` so `is_readable` can compute the same row
    level it does -- checking a *different* quantity from the one that ends
    up trusted would let the two silently drift apart.
    """
    y0, y1 = int(row_bounds[0]), int(row_bounds[1])
    h, w = gray.shape
    boxes = []
    for sx in slot_x:
        x0, x1 = int(sx) - half_w, int(sx) + half_w
        if x0 < 0 or y0 < 0 or x1 > w or y1 > h:
            boxes.append(None)
        else:
            box = gray[y0:y1, x0:x1]
            boxes.append(box if box.size >= 12 else None)
    return boxes


def _row_level(boxes) -> "float | None":
    """Median of a row's per-slot medians, or None if nothing is in-frame.

    Most slots are empty, so this is a robust read of that row's own bare
    board brightness -- and it adapts to lighting drift over an evening.
    """
    present = [b for b in boxes if b is not None]
    if not present:
        return None
    return float(np.median([np.median(b) for b in present]))


def read_slots(image, geom: BoardGeometry) -> BoardReading:
    """Which slots carry a card, judged by intra-slot brightness range."""
    gray = cv2.cvtColor(np.asarray(image), cv2.COLOR_BGR2GRAY).astype(float)
    half_w = max(2, int(0.14 * geom.dy))

    found = {}
    for name, row_bounds in (("yellow", geom.yellow_row), ("red", geom.red_row)):
        boxes = _row_boxes(gray, row_bounds, geom.slot_x, half_w)
        level = _row_level(boxes)
        if level is None:
            found[name] = set()
            continue

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


@dataclass(frozen=True)
class Card:
    """One hung card: where it sits, which end it records, how sure we are.

    ``confidence`` is the trained classifier's winning class probability (see
    `read_digit`), not a correlation margin -- the field predates the switch
    to a trained model in Task 6F and is named for what it holds now.
    """

    slot: int           # the cumulative score this card marks
    end: "int | None"   # the end that produced it; None when not read
    confidence: float


@dataclass(frozen=True)
class CardBoard:
    """Every card on the board, per team, in slot order."""

    yellow: tuple
    red: tuple

    def is_blank(self) -> bool:
        return not self.yellow and not self.red

    def all_cards(self) -> list:
        return ([("yellow", c) for c in self.yellow]
                + [("red", c) for c in self.red])

    def highest_end(self) -> int:
        """The latest end the board is known to record. 0 if none is."""
        return max((c.end for _, c in self.all_cards() if c.end is not None),
                    default=0)


def read_cards(image, geom: BoardGeometry) -> CardBoard:
    """Every card on the board, with the end number each one records.

    Finds the occupied slots with `read_slots`, then reads each one's glyph
    straight off the image and hands it to `read_digit` alone. `read_digit`
    is backed by a trained classifier (Task 6F) and ignores its old `tmpl`
    argument, so there is no per-read exemplar table to build here -- that
    was only needed by the correlation matcher `read_digit` replaced.
    """
    gray = cv2.cvtColor(np.asarray(image), cv2.COLOR_BGR2GRAY)
    present = read_slots(image, geom)
    out = {}
    for color, slots in (("yellow", present.yellow), ("red", present.red)):
        cards = []
        for slot in sorted(slots):
            end, confidence = read_digit(_card_glyph(gray, geom, color, slot))
            cards.append(Card(slot=slot, end=end, confidence=confidence))
        out[color] = tuple(cards)
    return CardBoard(yellow=out["yellow"], red=out["red"])


@dataclass(frozen=True)
class BoardScores:
    """What the board says, end by end."""

    per_end: dict          # end number -> {"red": int, "yellow": int}
    unread_ends: tuple     # ends the board cannot speak to, ascending
    final: "dict | None"   # None while any end is unread


def per_end_from_cards(board: CardBoard, n_ends: int) -> BoardScores:
    """Turn one board state into the score of each end.

    Within a team the cards are cumulative, so an end's score is the step
    from the previous card. An end with no card anywhere was blank -- but
    only if a *later* end is posted; past the last card, blank and
    not-yet-posted look identical and the end is reported unread instead.

    ``n_ends`` only extends how far we look for unread ends. It never gates
    a card: the board is the score, so a card past the detected end count is
    still read.

    Three physical facts are checked, and a violation raises `ScoreboardError`
    rather than return something wrong: an end number appears at most once
    across both teams; within a team slots strictly increase with end number;
    and a team's step from one card to the next is at most
    `MAX_SCORE_PER_END`, since only eight rocks a side are thrown.

    They are necessary and nowhere near sufficient, so do not read them as a
    net. A board with one card per team passes all three trivially, and a
    single yellow card at slot 2 whose digit is misread as 5 passes them while
    reporting ends 1-4 as *read blank* -- four certainties manufactured out of
    one misread. `MIN_CONFIDENCE` on the digit itself is what actually keeps
    misreads out; these checks are the cheap backstop behind it.
    """
    for color, card in board.all_cards():
        if card.end is None:
            raise ScoreboardError(
                f"{color} card at slot {card.slot}: end number could not be read"
            )

    seen = {}
    for color, card in board.all_cards():
        if card.end in seen:
            raise ScoreboardError(
                f"both teams cannot score in one end: end {card.end}"
            )
        seen[card.end] = color

    per_end = {}
    for color in COLORS:
        cards = sorted(getattr(board, color), key=lambda c: c.end)
        prev = 0
        for card in cards:
            if card.slot <= prev:
                raise ScoreboardError(
                    f"{color} total went backwards: {prev} -> {card.slot}"
                )
            if card.slot - prev > MAX_SCORE_PER_END:
                raise ScoreboardError(
                    f"{color} cannot score {card.slot - prev} in one end: "
                    f"{prev} -> {card.slot}"
                )
            per_end[card.end] = {
                **{c: 0 for c in COLORS}, color: card.slot - prev,
            }
            prev = card.slot

    highest = board.highest_end()
    span = max(n_ends, highest)
    unread = []
    for end in range(1, span + 1):
        if end in per_end:
            continue
        if end < highest:
            per_end[end] = {c: 0 for c in COLORS}   # read as blank
        else:
            unread.append(end)

    return BoardScores(
        per_end=per_end,
        unread_ends=tuple(unread),
        final=None if unread else cumulative(board),
    )


_MIN_PRINTED_DIGITS = 11


def _printed_band(gray, geom: BoardGeometry):
    """The pixel band the printed 1-14 row lives in, or None off-frame.

    Shared by `_printed_digit_groups` and `_printed_row_level` so the two
    can never disagree about which pixels "the printed row" means.
    """
    gap = geom.mid_line_y - geom.top_line_y
    y0, y1 = int(geom.top_line_y + 0.58 * gap), int(geom.mid_line_y - 0.03 * gap)
    x0, x1 = int(geom.slot_x[0] - 0.3 * geom.dy), int(geom.slot_x[-1] + 0.3 * geom.dy)
    h, w = gray.shape
    if y0 < 0 or y1 > h or x0 < 0 or x1 > w or y1 - y0 < 4:
        return None
    return gray[y0:y1, x0:x1]


def _printed_digit_groups(image, geom: BoardGeometry) -> int:
    """Count the digit groups visible in the printed 1-14 row."""
    gray = cv2.cvtColor(np.asarray(image), cv2.COLOR_BGR2GRAY)
    band = _printed_band(gray, geom)
    if band is None:
        return 0

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


def _printed_row_level(gray, geom: BoardGeometry) -> "float | None":
    """Median brightness of bare board *between* the printed digits.

    `_printed_digit_groups` establishes that this strip is unoccluded --
    that is the only thing it checks -- so once it has passed, the board
    pixels here (excluding the digit ink itself, which is not "bare") are a
    trustworthy read of this frame's board brightness under this frame's
    lighting. `is_readable` compares each card row's own level against this
    one, because the printed row can pass while a card row is covered (see
    `is_readable` for why that happens and why it matters).
    """
    band = _printed_band(gray, geom)
    if band is None:
        return None
    dark_cols = (band < 130).mean(axis=0) > 0.20
    bare = band[:, ~dark_cols]
    if bare.size == 0:
        return None
    return float(np.median(bare))


# How far below the bare-board reference a card row's own level can read
# before it counts as occluded rather than merely holding real cards.
# Measured over 339 clean rows and 7 rows containing a phantom (Task 7A):
# clean rows ranged -5.0..11.5 (median 1.5, p95 7.0); rows with a phantom
# ranged -1.5..73.2 (median 35.0, p5 1.5). 20 catches 4 of the 7 bad rows
# and refuses 0 of the 339 good ones -- it is a low bar on purpose, because a
# card row wrongly refused makes the whole frame unreadable (see below).
_ROW_OCCLUSION_MARGIN = 20.0


def _row_occluded(gray, geom: BoardGeometry, row_bounds, reference_level: float) -> bool:
    """Whether one card row reads too dark against the bare-board reference
    to trust, rather than merely holding real (bright) cards.

    Uses `_row_level` over the exact boxes `read_slots` builds for this row,
    so this check and the read it gates are always measuring the same
    quantity -- a row that passes here is a row `read_slots` would compute
    the same level for.
    """
    half_w = max(2, int(0.14 * geom.dy))
    boxes = _row_boxes(gray, row_bounds, geom.slot_x, half_w)
    level = _row_level(boxes)
    if level is None:
        return True  # off-frame or otherwise unreadable -- can't vouch for it
    return reference_level - level >= _ROW_OCCLUSION_MARGIN


def is_readable(image, geom: BoardGeometry) -> bool:
    """Whether the board is unobstructed enough to trust a reading.

    Checking only the printed 1-14 row -- as this function used to, on the
    premise that the row is "always there, so if it cannot be seen,
    something is standing in front of the board" -- misses half the board.
    That premise is false: the printed row sits ABOVE the red card band, in
    the gap between the two colour markers, so someone standing in front of
    only the board's *lower* half leaves the printed row completely clear.
    The check would pass while `read_slots` reads their clothing as cards.
    This is not hypothetical -- on `board_t04500.png` in
    `datasets/board-cards-train/s_iPqkT02q8/` that is exactly what happens:
    the printed row reads clean, `_printed_digit_groups` is satisfied, and
    `read_slots` reports eight red "cards" (a cumulative score of 13) where
    three or four people are standing in front of the red row and only one
    card is real. A phantom card is not a misread digit that a confidence
    threshold can catch downstream: it invents a scoring end, and no board
    self-check can catch it after the fact, because a three-point end is
    perfectly legal.

    So each card row is checked too, against a reference level sampled from
    the printed row itself -- the bare board *between* the digits, not the
    ink -- because that row has just been confirmed unoccluded above and is
    therefore a trustworthy brightness reference for this same board under
    this same lighting. A card row that comes back much darker than that
    reference is occluded. The check is per ROW, not per card or per slot:
    a slot cannot be blacklisted, because the same slot is a head in one
    frame of a video and a real card in another. And if either row fails,
    the whole frame is refused -- a board with one unreadable row cannot
    give a trustworthy cumulative score, and callers such as `read_cards_at`
    already step back and resample on a refused read.
    """
    if geom is None:
        return False
    if _printed_digit_groups(image, geom) < _MIN_PRINTED_DIGITS:
        return False

    gray = cv2.cvtColor(np.asarray(image), cv2.COLOR_BGR2GRAY).astype(float)
    reference = _printed_row_level(gray, geom)
    if reference is None:
        return False
    if _row_occluded(gray, geom, geom.yellow_row, reference):
        return False
    if _row_occluded(gray, geom, geom.red_row, reference):
        return False
    return True


def median_frame(frames):
    """Median of several frames: removes anyone walking past the board.

    Cards stay hung for a whole end, so a window of a minute or two is far
    shorter than the board changes but far longer than anyone stands still.
    """
    stack = np.stack([np.asarray(f) for f in frames])
    return np.median(stack, axis=0).astype(np.uint8)


# The board accumulates and never resets mid-game, so one good late read
# encodes the whole game. The sampler therefore walks BACKWARDS from just
# before the game's end and stops at the first usable state, rather than
# sweeping the whole game or looking for a late posting after `end_s`.
# Looking past `end_s` is unsafe: the board is cleared for the next game and
# then repopulated by it, so a later read risks being silently attributed to
# this one. The price is that a final end posted right at the buzzer can be
# missed -- it then shows up as a trailing unread end, which is the safe
# direction to fail in.
MAX_BOARD_READS = 5
BOARD_STEP_S = 300.0
BOARD_LEAD_S = 60.0


@dataclass(frozen=True)
class GameBoard:
    """One game's board, and what it cost to read."""

    scores: BoardScores
    read_at_s: float
    reads: int
    board: CardBoard


def read_cards_at(video_path, t_seconds, window_s=90.0, max_frames=40):
    """The cards around a moment in the video, de-occluded by median.

    Samples keyframes rather than a fixed frame rate. A fixed rate still makes
    the decoder reconstruct every frame in the window -- roughly 180 s of
    full-resolution video per read, and a game needs a dozen or more. The board
    only changes once an end, so the ~1 frame per 5 s that keyframes give is
    ample, and vastly cheaper.

    Memory, for whoever sizes the container: this pass is the pipeline's peak,
    because it is the only one reading full-resolution frames. ``max_frames``
    of them are held at once -- 40 x 1080p BGR is ~250 MB -- `median_frame`
    stacks them into another ~250 MB, `np.median` partitions a third copy, and
    the median itself comes back float64 (~50 MB at 1080p) before it is cast
    down. Measured at ~3x the frame bytes, so ~0.8 GB transient per read at
    1080p and proportionally more at 4K. That was free while the hosted worker
    always skipped this pass; it no longer skips it. ``max_frames`` is the dial
    if it has to come down.
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
    image = median_frame(imgs)
    geom = find_board(image)
    # is_readable also guards against an occluded card row, not just the
    # printed strip -- refuse the frame and let the caller resample.
    if geom is None or not is_readable(image, geom):
        return None
    return read_cards(image, geom)


def read_game_board(video_path, start_s, end_s, n_ends, *, read_at=None):
    """The latest usable board state for one game, or None.

    Walks back from just before ``end_s``. Never looks past it: the board is
    cleared for the next game, and a board repopulated by that game would be
    read as this one's. A late final posting is therefore missed rather than
    misattributed, and shows up as a trailing unread end.

    A read is "usable" when the board is found, `is_readable` passes, the
    card set is not blank, and `per_end_from_cards` does not raise. Raising
    (a `ScoreboardError`) means the board contradicted itself -- two teams
    scoring one end, or a team's total going backwards -- which is how a
    misread digit or a phantom card surfaces; the response is to step back
    and try another time, never to patch the reading up.

    ``n_ends`` is a stopping hint only, passed straight through to
    `per_end_from_cards`: it says whether another read is worth spending, but
    it never gates a card or changes a score.

    ``read_at`` is an injection point for tests: a callable ``(t) ->
    CardBoard | None`` replacing `read_cards_at`, so the sampler is testable
    without decoding any video.
    """
    read_at = read_at or (lambda t: read_cards_at(video_path, t))
    t = end_s - BOARD_LEAD_S
    for reads in range(1, MAX_BOARD_READS + 1):
        if t < start_s:
            break
        board = read_at(t)
        if board is not None and not board.is_blank():
            try:
                scores = per_end_from_cards(board, n_ends)
            except ScoreboardError:
                scores = None
            if scores is not None:
                return GameBoard(scores=scores, read_at_s=t,
                                  reads=reads, board=board)
        t -= BOARD_STEP_S
    return None
