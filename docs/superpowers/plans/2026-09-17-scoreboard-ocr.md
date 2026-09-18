# Wall-board card OCR Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Read the end number printed on each wall-board card so per-end scores come from the board, and make the board the only score the viewer shows.

**Architecture:** `find_board`/`BoardGeometry` are unchanged. On top of them, the printed 1-14 row in the same frame becomes a per-read template set; each card's glyph is correlated against it and accepted only on a confident margin. Cards plus their end numbers reduce to per-end scores by pure logic, with the board checking itself against two physical constraints. One late read per game usually suffices, so the whole-game sweep goes.

**Tech Stack:** Python 3.12, numpy, opencv-python-headless, pytest. Frontend: React 19 + esbuild, built with `npm run build` from `frontend/`.

**Spec:** `docs/superpowers/specs/2026-09-17-scoreboard-ocr-design.md`

## Global Constraints

- **No wrong digits.** A refused read is always better than a guessed one. Every gate fails closed.
- **Digit reading is a trained classifier, not template correlation.** Correlation was measured and failed; see the spec's *Why template matching failed*. Tasks 2-3 survive as glyph localisation and as the training-data harvester.
- **No new runtime dependency.** Train and infer in numpy and ship a small `.npz`. `torch` is in the `gpu` extra only, and a plain install has none.
- **The board never times anything.** Card positions and digits only; no inference from when a reading was taken.
- **`n_ends` is a stopping hint, never a correctness gate.** A wrong detected end count costs reads, never a wrong score.
- **Ends 10+ are out of scope.** Two-glyph card digits are rejected as unread.
- **Run test subsets, not the whole suite.** The full suite OOMs on this box (exit 137). Run the files named in each task.
- **Stage only your own hunks.** Another session shares this checkout; never `git add -A`.
- **Frontend builds via `npm run build`** from `frontend/`, never esbuild directly, or the build stamp goes stale. Built output is committed under `src/curling_score/`.
- Tests touching the cached VOD are marked `@pytest.mark.slow`.

---

### Task 1: Harvest and label board cards

Builds the validation set the whole design rests on. The cached VOD
`~/.cache/curling_score/videos/VXU9xwmugRg.mp4` is 4 h of 1920x1080 and needs no
network.

**Files:**
- Create: `scripts/harvest_board.py`
- Create: `datasets/board-cards/labels.json` (written by the script, digits filled in by hand)

**Interfaces:**
- Consumes: `scoreboard.find_board`, `scoreboard.read_slots`, `scoreboard.median_frame`, `ingest.frames.keyframe_sweep`
- Produces: `datasets/board-cards/labels.json`, a list of
  `{"frame": str, "t_s": float, "color": "yellow"|"red", "slot": int, "end": int|null}`,
  and PNG frames beside it. `end` is `null` until a human fills it in.

- [ ] **Step 1: Write the harvest script**

```python
"""Pull median-stacked board frames off a cached VOD and list their cards.

The digits are filled in by hand afterwards: this writes every card it finds
with ``"end": null`` and a contact sheet to read them off. That labelled set is
what MIN_MARGIN is set from -- the five-frame probe in the spec is not enough.
"""

import argparse
import json
from pathlib import Path

import cv2

from curling_score.game import scoreboard as SB
from curling_score.ingest import frames as F


def sample(video, t, window_s=90.0, max_frames=40):
    """One de-occluded board image at ``t``, or None."""
    lo, hi = max(0.0, t - window_s), t + window_s
    imgs = []
    for ts, img in F.keyframe_sweep(video, start_s=lo, end_s=hi):
        if ts < lo:
            continue
        if len(imgs) >= max_frames:
            break
        imgs.append(img)
    return SB.median_frame(imgs) if imgs else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--out", default="datasets/board-cards")
    ap.add_argument("--step-s", type=float, default=600.0)
    ap.add_argument("--start-s", type=float, default=0.0)
    ap.add_argument("--end-s", type=float, default=14400.0)
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    t = args.start_s
    while t <= args.end_s:
        img = sample(args.video, t)
        t += args.step_s
        if img is None:
            continue
        geom = SB.find_board(img)
        if geom is None or not SB.is_readable(img, geom):
            continue
        reading = SB.read_slots(img, geom)
        if reading.is_blank():
            continue
        name = f"board_t{int(t - args.step_s):05d}.png"
        cv2.imwrite(str(out / name), img)
        for color, slots in (("yellow", reading.yellow), ("red", reading.red)):
            for slot in sorted(slots):
                rows.append({"frame": name, "t_s": t - args.step_s,
                             "color": color, "slot": slot, "end": None})
        print(f"{name}: yellow={sorted(reading.yellow)} red={sorted(reading.red)}")

    (out / "labels.json").write_text(json.dumps(rows, indent=2) + "\n")
    print(f"{len(rows)} cards over {len({r['frame'] for r in rows})} frames")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it over the cached VOD**

```bash
./.venv/bin/python scripts/harvest_board.py \
  ~/.cache/curling_score/videos/VXU9xwmugRg.mp4 --step-s 600
```

Expected: tens of frames, each listing its cards; `datasets/board-cards/labels.json`
written with `"end": null` throughout.

- [ ] **Step 3: Label the digits by hand**

Open each PNG, read the digit on each card, and fill in its `end`. Where a digit
is genuinely illegible to a human, set `"end": "illegible"` — those rows are the
ones the reject gate must catch, so they are as valuable as the legible ones.

- [ ] **Step 4: Commit**

```bash
git add scripts/harvest_board.py datasets/board-cards/labels.json
git commit -m "harvest: pull and label wall-board cards for digit validation"
```

Do **not** commit the PNGs if the set is large; add `datasets/board-cards/*.png`
to `.gitignore` and note in the commit message how to regenerate them. Per the
scratchpad-loss lesson, they must live under `datasets/`, never `/tmp`.

---

### Task 2: The printed row as a template set

**Files:**
- Modify: `src/curling_score/game/scoreboard.py`
- Test: `tests/test_scoreboard.py`

**Interfaces:**
- Consumes: `BoardGeometry`
- Produces: `BoardGeometry.printed_row -> tuple[float, float]`;
  `scoreboard.templates(image, geom) -> dict[int, np.ndarray]` keyed 1..14,
  each a float32 array of shape `GLYPH_SHAPE`, mean-centred and unit-normalised;
  `scoreboard.GLYPH_SHAPE = (22, 16)`

- [ ] **Step 1: Write the failing test**

```python
class TestTemplates:
    """The printed 1-14 row is the exemplar set for the card digits: same
    font, same scale, same lighting, same frame. Nothing is shipped or
    trained, so these have to come out of the image itself."""

    def test_it_yields_one_normalised_glyph_per_slot(self, known_frame):
        img = known_frame("board_sheet2_t11000.png")
        geom = SB.find_board(img)
        t = SB.templates(img, geom)
        assert sorted(t) == list(range(1, 15))
        for g in t.values():
            assert g.shape == SB.GLYPH_SHAPE
            assert abs(float(g.mean())) < 1e-5
            assert abs(float(g.std()) - 1.0) < 1e-5

    def test_the_printed_row_sits_below_the_cards(self, known_frame):
        """Cards hang above the printed numbers in the same band. Reading the
        card band as the template source would match cards against cards."""
        img = known_frame("board_sheet2_t11000.png")
        geom = SB.find_board(img)
        assert geom.printed_row[0] >= geom.yellow_row[1]
        assert geom.printed_row[1] <= geom.mid_line_y
```

- [ ] **Step 2: Run it to verify it fails**

Run: `./.venv/bin/pytest tests/test_scoreboard.py::TestTemplates -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'templates'`

- [ ] **Step 3: Implement**

Add to `scoreboard.py`, beside the existing row properties:

```python
    @property
    def printed_row(self):
        """The printed 1-14 numbers, under the yellow cards in the same band.

        Same bounds `_printed_digit_groups` already uses to count them.
        """
        gap = self.mid_line_y - self.top_line_y
        return (self.top_line_y + 0.58 * gap, self.mid_line_y - 0.03 * gap)
```

and, after `read_slots`:

```python
# Glyphs are normalised to a fixed box before correlating, so a card and a
# printed number are compared on shape alone rather than on size or exposure.
GLYPH_SHAPE = (22, 16)  # (rows, cols)


def _normalise(patch) -> "np.ndarray":
    """Resize to GLYPH_SHAPE, mean-centre, scale to unit standard deviation."""
    g = cv2.resize(patch.astype(np.float32), (GLYPH_SHAPE[1], GLYPH_SHAPE[0]),
                   interpolation=cv2.INTER_AREA)
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
        out[k] = _normalise(gray[y0:y1, x0:x1])
    return out
```

- [ ] **Step 4: Run to verify it passes**

Run: `./.venv/bin/pytest tests/test_scoreboard.py::TestTemplates -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/game/scoreboard.py tests/test_scoreboard.py
git commit -m "scoreboard: build digit templates from the board's own printed row"
```

---

### Task 3: Read one card's digit

**Files:**
- Modify: `src/curling_score/game/scoreboard.py`
- Test: `tests/test_scoreboard.py`

**Interfaces:**
- Consumes: `templates`, `_normalise`, `GLYPH_SHAPE`
- Produces: `scoreboard.read_digit(tile, tmpl) -> tuple[int | None, float]` —
  the best-matching digit and its margin over the runner-up, digit `None`
  when the margin is below `MIN_MARGIN`; `scoreboard.MIN_MARGIN` (provisional
  `0.05`, set properly in Task 6); `scoreboard._card_glyph(gray, geom, color, slot) -> np.ndarray | None`

- [ ] **Step 1: Write the failing test**

```python
class TestReadDigit:
    """Correlate a card glyph against the printed row. The margin over the
    runner-up is the confidence: on the one known occluded card it collapses
    to near zero, which is what makes it usable as a reject gate."""

    def _cards(self, img):
        geom = SB.find_board(img)
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        return geom, gray, SB.templates(img, geom), SB.read_slots(img, geom)

    def test_it_reads_the_end_number_off_each_yellow_card(self, known_frame):
        img = known_frame("board_sheet2_t11000.png")
        geom, gray, tmpl, reading = self._cards(img)
        got = {}
        for slot in sorted(reading.yellow):
            glyph = SB._card_glyph(gray, geom, "yellow", slot)
            got[slot] = SB.read_digit(glyph, tmpl)[0]
        # Yellow reached 1 after end 1 and 3 after end 3.
        assert got == {1: 1, 3: 3}

    def test_a_card_under_a_hand_is_refused_rather_than_guessed(self, known_frame):
        """Slot 4 of this frame is behind a spectator's hand. A crude match
        called it a 9; the margin says it should not be called at all."""
        img = known_frame("board_sheet2_t11700_person.png")
        geom, gray, tmpl, _ = self._cards(img)
        glyph = SB._card_glyph(gray, geom, "yellow", 4)
        digit, margin = SB.read_digit(glyph, tmpl)
        assert digit is None
        assert margin < SB.MIN_MARGIN

    def test_an_out_of_frame_slot_has_no_glyph(self, known_frame):
        img = known_frame("board_sheet2_t11000.png")
        geom = SB.find_board(img)
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        far = SB.BoardGeometry(
            anchor_x=geom.anchor_x, yellow_y=geom.yellow_y, red_y=geom.red_y,
            dy=geom.dy, top_line_y=geom.top_line_y, mid_line_y=geom.mid_line_y,
            bottom_line_y=geom.bottom_line_y,
            slot_x=[x + 10_000 for x in geom.slot_x],
        )
        assert SB._card_glyph(gray, far, "yellow", 1) is None
```

Add `import cv2` at the top of `tests/test_scoreboard.py` if it is not already
there.

- [ ] **Step 2: Run it to verify it fails**

Run: `./.venv/bin/pytest tests/test_scoreboard.py::TestReadDigit -v`
Expected: FAIL — `AttributeError: ... '_card_glyph'`

- [ ] **Step 3: Implement**

```python
# Below this margin between the best and second-best template match, the digit
# is not called. Set in Task 6 against the labelled harvest; a wrong digit is
# worse than no digit, so this fails closed.
MIN_MARGIN = 0.05

# The card is a bright tile inside the slot. Segmenting it beats a fixed band,
# which clips the glyph tops -- the card sits higher in the band than the
# presence test needs to look.
_CARD_TILE_MARGIN = 10.0


def _card_glyph(gray, geom: BoardGeometry, color: str, slot: int):
    """The ink of one card's digit, cropped to its bounding box.

    Returns None where the slot falls outside the frame or no bright tile is
    found -- which is what an empty slot looks like.
    """
    row = geom.yellow_row if color == "yellow" else geom.red_row
    y0, y1 = int(row[0]), int(row[1])
    # The card's top sits above the presence band, so reach up to the rule.
    y0 = int(geom.top_line_y) if color == "yellow" else int(geom.mid_line_y)
    half_w = max(2, int(0.22 * geom.dy))
    sx = geom.slot_x[slot - 1]
    x0, x1 = int(sx) - half_w, int(sx) + half_w
    h, w = gray.shape
    if x0 < 0 or y0 < 0 or x1 > w or y1 > h or y1 - y0 < 6:
        return None

    box = gray[y0:y1, x0:x1].astype(np.float32)
    level = float(np.median(box))
    tile = box > level + _CARD_TILE_MARGIN     # the white card against the board
    if tile.sum() < 12:
        return None
    ys, xs = np.nonzero(tile)
    card = box[ys.min(): ys.max() + 1, xs.min(): xs.max() + 1]
    if card.size < 12:
        return None

    ink = card < (float(card.max()) + float(card.min())) / 2.0
    if ink.sum() < 4:
        return None
    iy, ix = np.nonzero(ink)
    return card[iy.min(): iy.max() + 1, ix.min(): ix.max() + 1]


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
```

- [ ] **Step 4: Run to verify it passes**

Run: `./.venv/bin/pytest tests/test_scoreboard.py::TestReadDigit -v`
Expected: PASS

If `test_it_reads_the_end_number_off_each_yellow_card` fails, the segmentation
is the suspect, not the correlation: dump `_card_glyph`'s output with
`cv2.imwrite` upscaled 10x and look at it before changing thresholds.

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/game/scoreboard.py tests/test_scoreboard.py
git commit -m "scoreboard: read a card's end number, refusing a weak match"
```

---

### Task 4: Read a whole board of cards

**Files:**
- Modify: `src/curling_score/game/scoreboard.py`
- Test: `tests/test_scoreboard.py`

**Interfaces:**
- Consumes: `read_slots`, `templates`, `_card_glyph`, `read_digit`
- Produces:
  - `scoreboard.Card` — frozen dataclass `(slot: int, end: int | None, margin: float)`
  - `scoreboard.CardBoard` — frozen dataclass `(yellow: tuple[Card, ...], red: tuple[Card, ...])`
    with `is_blank() -> bool`, `all_cards() -> list[tuple[str, Card]]`, `highest_end() -> int`
  - `scoreboard.read_cards(image, geom) -> CardBoard`
  - `scoreboard.cumulative(board: CardBoard) -> dict[str, int]` (existing name, now over a `CardBoard`)

- [ ] **Step 1: Write the failing test**

```python
class TestReadCards:
    def test_it_reads_every_card_with_its_end_and_margin(self, known_frame):
        img = known_frame("board_sheet2_t11000.png")
        board = SB.read_cards(img, SB.find_board(img))
        assert [(c.slot, c.end) for c in board.yellow] == [(1, 1), (3, 3)]
        assert [(c.slot, c.end) for c in board.red] == [(2, 2)]
        assert all(c.margin > 0 for c in board.yellow)

    def test_cumulative_is_the_highest_slot_per_team(self, known_frame):
        img = known_frame("board_sheet2_t11000.png")
        board = SB.read_cards(img, SB.find_board(img))
        assert SB.cumulative(board) == {"yellow": 3, "red": 2}

    def test_an_empty_board_is_blank(self, known_frame):
        img = known_frame("board_sheet2_t7200.png")   # between games
        board = SB.read_cards(img, SB.find_board(img))
        assert board.is_blank()
        assert SB.cumulative(board) == {"yellow": 0, "red": 0}

    def test_highest_end_ignores_cards_whose_digit_was_refused(self, known_frame):
        """An unread digit cannot extend how far the board is known to go."""
        board = SB.CardBoard(
            yellow=(SB.Card(1, 1, 0.3), SB.Card(4, None, 0.01)), red=(),
        )
        assert board.highest_end() == 1
```

- [ ] **Step 2: Run it to verify it fails**

Run: `./.venv/bin/pytest tests/test_scoreboard.py::TestReadCards -v`
Expected: FAIL — `AttributeError: ... 'read_cards'`

- [ ] **Step 3: Implement**

```python
@dataclass(frozen=True)
class Card:
    """One hung card: where it sits, which end it records, how sure we are."""

    slot: int           # the cumulative score this card marks
    end: int | None     # the end that produced it; None when not read
    margin: float       # match margin over the runner-up


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
    """Every card on the board, with the end number each one records."""
    gray = cv2.cvtColor(np.asarray(image), cv2.COLOR_BGR2GRAY)
    tmpl = templates(image, geom)
    present = read_slots(image, geom)
    out = {}
    for color, slots in (("yellow", present.yellow), ("red", present.red)):
        cards = []
        for slot in sorted(slots):
            end, margin = read_digit(_card_glyph(gray, geom, color, slot), tmpl)
            cards.append(Card(slot=slot, end=end, margin=margin))
        out[color] = tuple(cards)
    return CardBoard(yellow=out["yellow"], red=out["red"])
```

Replace the existing `cumulative` with the `CardBoard` form:

```python
def cumulative(board: CardBoard) -> dict:
    """The running total each team has reached.

    Cards accumulate left to right, so only the rightmost one matters. A card
    whose digit was refused still counts here: where it sits is read straight
    off the board and does not depend on the glyph.
    """
    return {
        "yellow": max((c.slot for c in board.yellow), default=0),
        "red": max((c.slot for c in board.red), default=0),
    }
```

- [ ] **Step 4: Run to verify it passes**

Run: `./.venv/bin/pytest tests/test_scoreboard.py::TestReadCards -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/game/scoreboard.py tests/test_scoreboard.py
git commit -m "scoreboard: read the whole board as cards carrying end numbers"
```

---

### Task 5: Cards to per-end scores

Pure logic, no CV. This is where "blank" and "unread" are separated.

**Files:**
- Modify: `src/curling_score/game/scoreboard.py`
- Test: `tests/test_scoreboard.py`

**Interfaces:**
- Consumes: `Card`, `CardBoard`, `ScoreboardError`
- Produces:
  - `scoreboard.BoardScores` — frozen dataclass
    `(per_end: dict[int, dict[str, int]], unread_ends: tuple[int, ...], final: dict[str, int] | None)`
  - `scoreboard.per_end_from_cards(board: CardBoard, n_ends: int) -> BoardScores`

- [ ] **Step 1: Write the failing test**

```python
class TestPerEndFromCards:
    """A card's slot is the cumulative total and its digit is the end that
    produced it, so one board state is a whole game -- except for ends after
    the last card, which may be blank or may simply not be posted yet."""

    def board(self, yellow=(), red=()):
        return SB.CardBoard(
            yellow=tuple(SB.Card(s, e, 0.5) for s, e in yellow),
            red=tuple(SB.Card(s, e, 0.5) for s, e in red),
        )

    def test_one_board_state_gives_every_end(self):
        # The reference frame: Y+1 in end 1, R+2 in end 2, Y+2 in end 3.
        got = SB.per_end_from_cards(
            self.board(yellow=[(1, 1), (3, 3)], red=[(2, 2)]), n_ends=3)
        assert got.per_end == {
            1: {"red": 0, "yellow": 1},
            2: {"red": 2, "yellow": 0},
            3: {"red": 0, "yellow": 2},
        }
        assert got.unread_ends == ()
        assert got.final == {"red": 2, "yellow": 3}

    def test_an_interior_end_with_no_card_was_blank(self):
        """A later end is posted, so end 2 was genuinely passed over. This is
        read, not inferred, and it is the common case."""
        got = SB.per_end_from_cards(
            self.board(yellow=[(1, 1), (2, 3)]), n_ends=3)
        assert got.per_end[2] == {"red": 0, "yellow": 0}
        assert got.unread_ends == ()

    def test_ends_after_the_last_card_are_unread_not_blank(self):
        """Nothing distinguishes 'blank' from 'not posted yet' up here, so it
        is reported as unknown rather than guessed at zero."""
        got = SB.per_end_from_cards(self.board(yellow=[(1, 1)]), n_ends=4)
        assert got.unread_ends == (2, 3, 4)
        assert 2 not in got.per_end

    def test_the_final_is_unknown_while_any_end_is_unread(self):
        got = SB.per_end_from_cards(self.board(yellow=[(1, 1)]), n_ends=4)
        assert got.final is None

    def test_a_board_beyond_the_detected_end_count_is_still_honoured(self):
        """n_ends is a stopping hint. The board is the score, so a card past
        the detected end count is read, not discarded."""
        got = SB.per_end_from_cards(
            self.board(yellow=[(1, 1)], red=[(2, 5)]), n_ends=2)
        assert got.per_end[5] == {"red": 2, "yellow": 0}
        assert got.unread_ends == ()

    def test_rejects_an_unread_digit(self):
        with pytest.raises(SB.ScoreboardError, match="could not be read"):
            SB.per_end_from_cards(self.board(yellow=[(1, None)]), n_ends=1)

    def test_rejects_both_teams_scoring_in_one_end(self):
        with pytest.raises(SB.ScoreboardError, match="both teams"):
            SB.per_end_from_cards(
                self.board(yellow=[(1, 2)], red=[(1, 2)]), n_ends=2)

    def test_rejects_a_team_whose_total_goes_backwards(self):
        with pytest.raises(SB.ScoreboardError, match="backwards"):
            SB.per_end_from_cards(
                self.board(yellow=[(3, 1), (2, 2)]), n_ends=2)

    def test_a_blank_board_reads_every_end_as_unread(self):
        got = SB.per_end_from_cards(self.board(), n_ends=2)
        assert got.unread_ends == (1, 2)
        assert got.per_end == {}
        assert got.final is None
```

- [ ] **Step 2: Run it to verify it fails**

Run: `./.venv/bin/pytest tests/test_scoreboard.py::TestPerEndFromCards -v`
Expected: FAIL — `AttributeError: ... 'per_end_from_cards'`

- [ ] **Step 3: Implement**

```python
@dataclass(frozen=True)
class BoardScores:
    """What the board says, end by end."""

    per_end: dict          # end number -> {"red": int, "yellow": int}
    unread_ends: tuple     # ends the board cannot speak to, ascending
    final: dict | None     # None while any end is unread


def per_end_from_cards(board: CardBoard, n_ends: int) -> BoardScores:
    """Turn one board state into the score of each end.

    Within a team the cards are cumulative, so an end's score is the step from
    the previous card. An end with no card anywhere was blank -- but only if a
    *later* end is posted; past the last card, blank and not-yet-posted look
    identical and the end is reported unread instead.

    ``n_ends`` only extends how far we look for unread ends. It never gates a
    card: the board is the score, so a card past the detected end count is
    still read.
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
```

- [ ] **Step 4: Run to verify it passes**

Run: `./.venv/bin/pytest tests/test_scoreboard.py::TestPerEndFromCards -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/game/scoreboard.py tests/test_scoreboard.py
git commit -m "scoreboard: turn one board state into per-end scores"
```

---

### Task 6A: Harvest the printed row as self-labelled training data

Template matching was measured and failed: 46% on cards, 0 of 11 distinct cards
read consistently, and 73% even on printed digits across frames. The spec section
*Why template matching failed* carries the numbers. `read_digit` is now backed by
a trained classifier. Its training data is free, because the printed 1-14 row is
in every board frame whether or not cards are hung, and the digit at slot *k* is
*k*.

**Files:**
- Create: `scripts/harvest_glyphs.py`
- Create: `datasets/board-glyphs/glyphs.npz` (written by the script)

**Interfaces:**
- Consumes: `scoreboard.find_board`, `scoreboard.is_readable`, `scoreboard.templates`,
  `scoreboard.median_frame`, `ingest.frames.keyframe_sweep`
- Produces: `datasets/board-glyphs/glyphs.npz` holding `x` (N, 22, 16) float32
  normalised glyphs, `y` (N,) int labels in 1..9, and `t_s` (N,) sample times.
  The sample time travels with each glyph so a split can hold out whole FRAMES;
  splitting on individual glyphs leaks augmented copies of the same glyph across
  the split and makes the validation score meaningless.

- [ ] **Step 1: Write the harvester**

Sample the cached VOD at many timestamps. The printed row needs no cards, so
every readable board frame yields 9 labelled glyphs. Reuse `scoreboard.templates`
for the cropping — it already extracts exactly these glyphs — and keep only slots
1..9, because slots 10..14 hold two digits in one slot width and are not valid
card digits.

- [ ] **Step 2: Run it, targeting a few thousand glyphs**

```bash
./.venv/bin/python scripts/harvest_glyphs.py \
  ~/.cache/curling_score/videos/VXU9xwmugRg.mp4 --step-s 60
```

Four hours at 60 s steps is ~240 samples, so ~2000 glyphs before augmentation.
Run it in the background; each sample decodes a window of full-resolution video.

- [ ] **Step 3: Sanity-check the set**

Assert every label is in 1..9, that class counts are near-equal (they must be —
each frame contributes one of each), and that no array holds NaN. Print the glyph
count and the number of distinct frames.

- [ ] **Step 4: Commit**

```bash
git add scripts/harvest_glyphs.py datasets/board-glyphs/glyphs.npz
git commit -m "harvest: the printed row is self-labelled digit training data"
```

---

### Task 6B: Train the digit classifier

**Files:**
- Create: `src/curling_score/game/digits.py`
- Create: `scripts/train_digits.py`
- Create: `src/curling_score/game/digit_weights.npz`
- Test: `tests/test_digits.py`

**Interfaces:**
- Consumes: `datasets/board-glyphs/glyphs.npz`
- Produces:
  - `digits.augment(x, rng) -> np.ndarray` — sub-pixel shift, scale, blur, JPEG, brightness
  - `digits.Model.load(path) -> Model`, `Model.predict(glyph) -> tuple[int, float]`
  - `digits.WEIGHTS` — the packaged `.npz` path
  - a trained `digit_weights.npz`

Numpy only for both training and inference (ruling R10): `torch` lives in the
`gpu` extra and a plain `pip install -e ".[dev]"` has none, so a torch inference
path would break `curling-score analyze` on base dependencies. Start with an MLP
over the normalised glyph (ruling R11), because correlation failed specifically on
alignment and scale noise and augmentation attacks that by teaching invariance.
A small CNN is the escalation, not the starting point.

- [ ] **Step 1: Write the failing tests**

```python
class TestTheDigitModel:
    """Trained on the printed row, which labels itself. The gate is the real
    cards it never saw, because those are what it has to read."""

    def test_it_reads_held_out_printed_digits(self):
        """The correlation matcher managed 73% here."""

    def test_it_reads_every_distinct_real_card(self):
        """The 11 hand-labelled cards in datasets/board-cards. Correlation read
        0 of 11 correctly in every frame they appear in."""

    def test_confidence_is_lower_on_an_empty_slot_than_on_a_card(self):
        """54% of empty slots cleared the old correlation threshold, which is
        why presence detection cannot be folded into the digit read."""
```

Fill in the concrete assertions once the harvest exists; Step 3 sets the numbers.

- [ ] **Step 2: Implement augmentation, the model, and the training script**

Hold out whole frames by `t_s`, never individual glyphs.

- [ ] **Step 3: Train, and measure against the gate**

Report these, and write them into the spec's Validation section:
- accuracy on held-out printed frames — must beat 73% decisively
- accuracy on the 11 distinct real cards, and whether each is correct in *every*
  frame it appears in (correlation: 0 of 11)
- the confidence distribution over the ~519 empty slot positions against cards

**If the real cards do not come good, stop and report it rather than tuning.**
The fallback is hand-labelling real cards across more VODs, which is expensive
and is the user's decision.

- [ ] **Step 4: Commit**

```bash
git add src/curling_score/game/digits.py scripts/train_digits.py \
        src/curling_score/game/digit_weights.npz tests/test_digits.py
git commit -m "digits: a small classifier trained on the board's printed row"
```

---

### Task 6C: Put the classifier behind read_digit

The point of this task is that nothing else changes. Tasks 4, 5 and 7-13 consume
the `read_digit` and `read_cards` *contract*, not their internals.

**Files:**
- Modify: `src/curling_score/game/scoreboard.py`
- Modify: `tests/test_scoreboard.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Produces: `read_digit(glyph, tmpl=None) -> tuple[int | None, float]` — the same
  shape as before. The second element is now model confidence rather than a
  correlation margin, so `MIN_MARGIN` becomes `MIN_CONFIDENCE`, set from 6B.

- [ ] **Step 1: Swap the body of `read_digit`** to use the model, loading it once
  through a module-level lazy cache, and refuse below `MIN_CONFIDENCE`.
- [ ] **Step 2: Keep `templates()`.** It is now the training-data extractor and is
  still used by `scripts/harvest_glyphs.py`. Say that in its docstring so the next
  reader does not delete it as dead.
- [ ] **Step 3: Package the weights.** Add the `.npz` to
  `[tool.setuptools.package-data]` under `"curling_score.game"`, or an installed
  copy cannot read its own weights.
- [ ] **Step 4: Update `TestReadDigit`** to the new threshold name, keeping
  `test_a_card_under_a_hand_is_never_read_as_the_wrong_end` as the safety invariant.
- [ ] **Step 5: Run** `./.venv/bin/pytest tests/test_scoreboard.py tests/test_digits.py -v`
- [ ] **Step 6: Commit**

```bash
git add src/curling_score/game/scoreboard.py tests/test_scoreboard.py pyproject.toml
git commit -m "scoreboard: read card digits with the trained classifier"
```

---

### Task 7: Read a game's board in as few views as possible

**Files:**
- Modify: `src/curling_score/game/scoreboard.py`
- Modify: `src/curling_score/analyze.py:29` and `:301-343`
- Test: `tests/test_scoreboard.py`

**Interfaces:**
- Consumes: `read_cards`, `per_end_from_cards`, `is_readable`, `median_frame`, `find_board`
- Produces:
  - `scoreboard.read_cards_at(video_path, t_seconds, window_s=90.0, max_frames=40) -> CardBoard | None`
  - `scoreboard.GameBoard` — frozen dataclass `(scores: BoardScores, read_at_s: float, reads: int, board: CardBoard)`
  - `scoreboard.read_game_board(video_path, start_s, end_s, n_ends, *, read_at=None) -> GameBoard | None`
  - `scoreboard.MAX_BOARD_READS = 5`, `scoreboard.BOARD_STEP_S = 300.0`, `scoreboard.BOARD_LEAD_S = 60.0`

`read_at` is an injection point for tests: a callable `(t) -> CardBoard | None`
replacing `read_cards_at`, so the sampler is testable without a video.

- [ ] **Step 1: Write the failing test**

```python
class TestReadGameBoard:
    """Cards accumulate, so the latest usable board state is also the most
    complete one. The sampler walks back from the end of the game and stops at
    the first state it can use -- one read, in the ordinary case."""

    def board(self, yellow=(), red=()):
        return SB.CardBoard(
            yellow=tuple(SB.Card(s, e, 0.5) for s, e in yellow),
            red=tuple(SB.Card(s, e, 0.5) for s, e in red),
        )

    def test_a_complete_board_costs_one_read(self):
        seen = []

        def read_at(t):
            seen.append(t)
            return self.board(yellow=[(1, 1), (3, 3)], red=[(2, 2)])

        got = SB.read_game_board("v", 0.0, 3000.0, n_ends=3, read_at=read_at)
        assert got.reads == 1
        assert len(seen) == 1
        assert got.scores.final == {"red": 2, "yellow": 3}

    def test_it_steps_back_past_a_cleared_board(self):
        """Read too late and the board has been wiped for the next game."""
        def read_at(t):
            return self.board() if t > 2000.0 else self.board(yellow=[(1, 1)])

        got = SB.read_game_board("v", 0.0, 3000.0, n_ends=1, read_at=read_at)
        assert got.reads > 1
        assert got.read_at_s <= 2000.0

    def test_it_steps_back_past_an_inconsistent_read(self):
        """A misread digit usually shows up as two teams scoring one end."""
        def read_at(t):
            if t > 2000.0:
                return self.board(yellow=[(1, 2)], red=[(1, 2)])
            return self.board(yellow=[(1, 1)])

        got = SB.read_game_board("v", 0.0, 3000.0, n_ends=1, read_at=read_at)
        assert got.scores.per_end[1] == {"red": 0, "yellow": 1}

    def test_it_never_looks_past_the_end_of_the_game(self):
        """Past end_s the board belongs to the next game: a repopulated board
        would be silently attributed to this one."""
        seen = []

        def read_at(t):
            seen.append(t)
            return self.board(yellow=[(1, 1)])

        SB.read_game_board("v", 0.0, 3000.0, n_ends=1, read_at=read_at)
        assert max(seen) <= 3000.0

    def test_it_gives_up_after_the_read_budget(self):
        got = SB.read_game_board("v", 0.0, 9000.0, n_ends=3,
                                 read_at=lambda t: None)
        assert got is None

    def test_an_incomplete_board_is_still_returned(self):
        """Trailing ends unposted is a partial answer, not a failure."""
        got = SB.read_game_board("v", 0.0, 3000.0, n_ends=4,
                                 read_at=lambda t: self.board(yellow=[(1, 1)]))
        assert got.scores.unread_ends == (2, 3, 4)
        assert got.scores.final is None
```

- [ ] **Step 2: Run it to verify it fails**

Run: `./.venv/bin/pytest tests/test_scoreboard.py::TestReadGameBoard -v`
Expected: FAIL — `AttributeError: ... 'read_game_board'`

- [ ] **Step 3: Implement**

```python
# The board accumulates, so the latest usable state is the most complete one
# and the search runs backwards from the end of the game.
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
    """The cards around a moment in the video, de-occluded by median."""
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
    # No printed row means no templates and no occlusion guarantee, so refuse.
    if geom is None or not is_readable(image, geom):
        return None
    return read_cards(image, geom)


def read_game_board(video_path, start_s, end_s, n_ends, *, read_at=None):
    """The latest usable board state for one game, or None.

    Walks back from just before ``end_s``. Never looks past it: the board is
    cleared for the next game, and a board repopulated by that game would be
    read as this one's. A late final posting is therefore missed rather than
    misattributed, and shows up as a trailing unread end.
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
```

- [ ] **Step 4: Run to verify it passes**

Run: `./.venv/bin/pytest tests/test_scoreboard.py::TestReadGameBoard -v`
Expected: PASS

- [ ] **Step 5: Wire it into analyze.py**

Delete `BOARD_INTERVAL_S` at `analyze.py:29`. Replace the loop body at
`analyze.py:311-343` (the `else:` branch of `skip_scoreboard`) with:

```python
        phase("scoreboard", 0.0, "reading the wall scoreboard")
        progress("reading the wall scoreboard...")
        for game, out_game in zip(games, out_games):
            got = sb.read_game_board(
                path, game.start_s, game.end_s, len(out_game["ends"]),
            )
            if got is None:
                out_game["scoreboard"] = None
                progress(f"  game {game.index + 1}: board not read")
                continue
            out_game["scoreboard"] = {
                "read_at_s": round(got.read_at_s, 2),
                "reads": got.reads,
                "unread_ends": list(got.scores.unread_ends),
                "final": got.scores.final,
                "cards": {
                    color: [
                        {"slot": c.slot, "end": c.end, "margin": round(c.margin, 4)}
                        for c in getattr(got.board, color)
                    ]
                    for color in sb.COLORS
                },
                "per_end": {str(k): v for k, v in sorted(got.scores.per_end.items())},
            }
            progress(
                f"  game {game.index + 1}: board says {got.scores.final} "
                f"in {got.reads} read(s)"
                + (f", ends {list(got.scores.unread_ends)} not posted"
                   if got.scores.unread_ends else "")
            )
        phase("scoreboard", 1.0, "scoreboard read")
```

`agrees_with_detection` and `end["scoreboard_agrees"]` are set in Task 8, where
the detected block is defined. Leave them out here.

- [ ] **Step 6: Run the analyze tests**

Run: `./.venv/bin/pytest tests/test_scoreboard.py tests/test_analyze_write.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/curling_score/game/scoreboard.py src/curling_score/analyze.py \
        tests/test_scoreboard.py
git commit -m "scoreboard: find a game's score in one read, not a sweep"
```

---

### Task 8: Make the board the timeline's score

**Files:**
- Modify: `src/curling_score/timeline.py:64-166` (`build_end`), `:167-226` (`build_game`), `:410-432` (override recompute)
- Modify: `src/curling_score/analyze.py` (pass board scores into the builders)
- Test: `tests/test_timeline.py`

**Interfaces:**
- Consumes: `scoreboard.BoardScores`
- Produces:
  - `timeline.build_end(number, house, start_s, end_s, shots, board_score=None)` —
    `board_score` is `{"red": int, "yellow": int} | None`
  - `timeline.build_game(index, start_s, end_s, ends, board=None)` —
    `board` is `BoardScores | None`
  - end fields: `score` (board or `None`), `running` (board or `None`),
    `score_source` (`"board"` or `None`), `detected_score`
  - game fields: `final` (board or `None`), `detected` (`{"score_by_end", "final"}`)

- [ ] **Step 1: Write the failing test**

```python
class TestTheBoardIsTheScore:
    """Detected scores stay computed -- they are the only thing that can tell
    a misread board from a correct one -- but they are not the game's score."""

    def test_an_end_takes_its_score_from_the_board(self):
        end = timeline.build_end(1, "top", 0.0, 100.0, [],
                                 board_score={"red": 0, "yellow": 2})
        assert end["score"] == {"red": 0, "yellow": 2}
        assert end["score_source"] == "board"

    def test_an_unread_end_has_no_score(self):
        end = timeline.build_end(1, "top", 0.0, 100.0, [], board_score=None)
        assert end["score"] is None
        assert end["score_source"] is None

    def test_the_detected_score_is_kept_but_set_apart(self):
        end = timeline.build_end(1, "top", 0.0, 100.0, [], board_score=None)
        assert "detected_score" in end
        assert end["detected_score"] == {"red": 0, "yellow": 0}

    def test_the_running_score_stops_at_the_first_unread_end(self):
        ends = [
            timeline.build_end(1, "top", 0.0, 10.0, [], board_score={"red": 0, "yellow": 1}),
            timeline.build_end(2, "bottom", 10.0, 20.0, [], board_score=None),
            timeline.build_end(3, "top", 20.0, 30.0, [], board_score={"red": 2, "yellow": 0}),
        ]
        game = timeline.build_game(0, 0.0, 30.0, ends)
        assert game["ends"][0]["running"] == {"red": 0, "yellow": 1}
        assert game["ends"][1]["running"] is None
        assert game["ends"][2]["running"] is None

    def test_the_final_is_unknown_when_the_board_left_an_end_unread(self):
        board = SB.BoardScores(per_end={1: {"red": 0, "yellow": 1}},
                               unread_ends=(2,), final=None)
        ends = [timeline.build_end(1, "top", 0.0, 10.0, [],
                                   board_score={"red": 0, "yellow": 1}),
                timeline.build_end(2, "bottom", 10.0, 20.0, [], board_score=None)]
        game = timeline.build_game(0, 0.0, 20.0, ends, board=board)
        assert game["final"] is None

    def test_the_detected_final_is_kept_for_the_agreement_check(self):
        board = SB.BoardScores(per_end={1: {"red": 0, "yellow": 1}},
                               unread_ends=(), final={"red": 0, "yellow": 1})
        ends = [timeline.build_end(1, "top", 0.0, 10.0, [],
                                   board_score={"red": 0, "yellow": 1})]
        game = timeline.build_game(0, 0.0, 10.0, ends, board=board)
        assert game["final"] == {"red": 0, "yellow": 1}
        assert game["detected"]["final"] == {"red": 0, "yellow": 0}
        assert game["detected"]["score_by_end"] == [{"red": 0, "yellow": 0}]

    def test_the_hammer_chain_runs_off_board_scores(self):
        """The rules fix the hammer sequence from the scores. Those scores are
        now the board's, so a detection mistake cannot bend the chain."""
        ends = [
            timeline.build_end(1, "top", 0.0, 10.0, [], board_score={"red": 0, "yellow": 1}),
            timeline.build_end(2, "bottom", 10.0, 20.0, [], board_score={"red": 1, "yellow": 0}),
        ]
        ends[0]["hammer"] = "red"
        game = timeline.build_game(0, 0.0, 20.0, ends)
        assert game["ends"][1]["hammer_expected"] == "red"

    def test_the_hammer_chain_stops_at_the_first_unread_end(self):
        ends = [
            timeline.build_end(1, "top", 0.0, 10.0, [], board_score={"red": 0, "yellow": 1}),
            timeline.build_end(2, "bottom", 10.0, 20.0, [], board_score=None),
            timeline.build_end(3, "top", 20.0, 30.0, [], board_score={"red": 1, "yellow": 0}),
        ]
        ends[0]["hammer"] = "red"
        game = timeline.build_game(0, 0.0, 30.0, ends)
        assert game["ends"][2]["hammer_expected"] is None
```

Add `from curling_score.game import scoreboard as SB` to `tests/test_timeline.py`.

- [ ] **Step 2: Run it to verify it fails**

Run: `./.venv/bin/pytest tests/test_timeline.py::TestTheBoardIsTheScore -v`
Expected: FAIL — `build_end() got an unexpected keyword argument 'board_score'`

- [ ] **Step 3: Implement in `build_end`**

Change the signature and the score block at `timeline.py:64-72`:

```python
def build_end(number, house, start_s, end_s, shots, board_score=None) -> dict:
    """One end: its shots, the house they left, and the board's score for it.

    ``board_score`` is what the wall board says this end was; None means the
    board could not speak to it. The detected score is still computed, as
    ``detected_score``, because the disagreement between the two is the only
    check we have on either.
    """
    shots = list(shots)
    scoring = shots_mod.scoring_shot(shots)
    final = scoring.stones if scoring else []
    detected_score = rules.score_end(
        [rules.Stone(color=d.color, x=d.x_m, y=d.y_m) for d in final]
    )
```

and in the returned dict replace `"score": score,` with:

```python
        "score": dict(board_score) if board_score else None,
        "score_source": "board" if board_score else None,
        "detected_score": detected_score,
```

Leave `"scored_from_shot"` as it is: it names the shot that settled the house,
which is a detection fact and not a score.

- [ ] **Step 4: Implement in `build_game`**

Replace the running-score block and the hammer block at `timeline.py:169-192`:

```python
def build_game(index, start_s, end_s, ends, board=None) -> dict:
    """One game, with the board's running score carried across its ends."""
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
    # the scores. Those scores are the board's now, so this no longer inherits
    # a detection mistake -- but it can only run as far as the board is known.
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
    if seen and known:
        expected = rules.hammer_chain(
            seen[0] if observed[0] else rules.first_hammer_given(
                seen[0], observed.index(seen[0]) + 1, known,
            ),
            known,
        )
        for end, want in zip(ends, expected):
            end["hammer_expected"] = want
        consistent = all(o == w for o, w in zip(observed, expected) if o)
```

and in the returned dict replace `"final": dict(running),` with:

```python
        "final": dict(board.final) if board and board.final else None,
        "detected": {
            "score_by_end": [e["detected_score"] for e in ends],
            "final": rules.running_total([e["detected_score"] for e in ends]),
        },
```

- [ ] **Step 5: Run to verify it passes**

Run: `./.venv/bin/pytest tests/test_timeline.py -v`
Expected: PASS. Existing tests asserting on `end["score"]` will need updating to
`detected_score` or to pass `board_score=` — update them rather than reverting
the change; they were asserting the old contract.

- [ ] **Step 6: Wire analyze.py to pass the board through**

The board must be read **before** the games are built, because `build_end` now
takes its score. Move the board pass above the end-building loop, keyed by game,
and pass `board_score=scores.per_end.get(end_number)` into `build_end` and
`board=got.scores` into `build_game`. Where `read_game_board` returned None,
pass `board_score=None` throughout and `board=None`.

Then restore the agreement check, now against the detected block:

```python
            agrees = (
                None if got.scores.final is None
                else got.scores.final == out_game["detected"]["final"]
            )
            out_game["scoreboard"]["agrees_with_detection"] = agrees
            for end in out_game["ends"]:
                end["scoreboard_agrees"] = agrees
```

- [ ] **Step 7: Update the override recompute**

At `timeline.py:410-432` the scoreboard verdict is recomputed after overrides.
`game["final"]` is now the board's and does not change when shots are edited, so
the comparison must be against `game["detected"]["final"]`, which does. Rebuild
that block accordingly and keep `end["scoreboard_agrees"]` in step with it.

- [ ] **Step 8: Run the full affected subset**

Run: `./.venv/bin/pytest tests/test_timeline.py tests/test_analyze_write.py tests/test_scoreboard.py tests/test_rules.py -v`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add src/curling_score/timeline.py src/curling_score/analyze.py tests/test_timeline.py
git commit -m "timeline: the wall board is the score, detection is the check"
```

---

### Task 9: Run the board pass on the hosted worker

**Files:**
- Modify: `src/curling_score/service/worker.py:169`
- Modify: `src/curling_score/service/api.py:67`
- Test: `tests/test_worker.py:75`

**Interfaces:**
- Consumes: nothing new
- Produces: hosted runs carry a board-sourced score

- [ ] **Step 1: Update the failing test**

In `tests/test_worker.py`, change the assertion at line 75 and give it a name
that says why:

```python
    def test_it_reads_the_wall_board(self):
        """The board is the only source of score, so a hosted run that skips
        it produces a game with no score at all."""
        ...
        assert seen["skip_scoreboard"] is False and seen["download_attempts"] == 1
```

- [ ] **Step 2: Run it to verify it fails**

Run: `./.venv/bin/pytest tests/test_worker.py -v`
Expected: FAIL — `assert True is False`

- [ ] **Step 3: Implement**

In `worker.py:169` change `skip_scoreboard=True` to `skip_scoreboard=False`.

In `api.py:67` raise the phase budget from `"scoreboard": 0.5` to a value
matching a real 1-2 read pass. Measure it from a local run's phase timings
rather than guessing; note the measured figure in the commit message.

- [ ] **Step 4: Run to verify it passes**

Run: `./.venv/bin/pytest tests/test_worker.py tests/test_service_api.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/service/worker.py src/curling_score/service/api.py \
        tests/test_worker.py
git commit -m "worker: read the wall board, now that it costs one view"
```

---

### Task 10: The viewer's scoreboard table

**Files:**
- Modify: `frontend/viewer/ChartPanel.jsx:213-236`
- Modify: `src/curling_score/viewer/style.css`
- Test: `tests/test_viewer_js.py`

**Interfaces:**
- Consumes: `game.scoreboard`, `end.score`, `game.final`
- Produces: a table that distinguishes blank from unread

- [ ] **Step 1: Write the failing test**

```python
class TestTheScoreboardShowsOnlyWhatWasRead:
    """Blank and unread are different facts: one says nobody scored, the
    other says nobody posted. One mark for both would be the same dishonesty
    in a new place."""

    def test_a_blank_end_and_an_unread_end_read_differently(self):
        got = run_js(
            "out([scoreCell({red: 0, yellow: 0}, 'red'), scoreCell(null, 'red')]);"
        )
        assert got[0] != got[1]

    def test_a_scored_end_shows_its_number(self):
        got = run_js("out(scoreCell({red: 2, yellow: 0}, 'red'));")
        assert got == "2"

    def test_a_blank_end_is_not_shown_as_a_score(self):
        got = run_js("out(scoreCell({red: 0, yellow: 0}, 'red'));")
        assert got != "0"
```

Add `scoreCell` to `frontend/core/` so the JS harness can reach it — the harness
imports the framework-free core, not JSX. Put it in `frontend/core/shots.mjs`
beside the other small display helpers. `core/index.mjs` re-exports each core
module with `export *`, so it needs no edit.

- [ ] **Step 2: Run it to verify it fails**

Run: `./.venv/bin/pytest tests/test_viewer_js.py::TestTheScoreboardShowsOnlyWhatWasRead -v`
Expected: FAIL — `scoreCell is not defined`

- [ ] **Step 3: Implement the helper**

In `frontend/core/shots.mjs`:

```javascript
/* One cell of the score table. An end with no board score is not a zero: the
 * board never spoke to it. "–" is a read blank end, "·" is an unread one. */
export const scoreCell = (score, color) =>
  score == null ? "·" : (score[color] || "–");
```

- [ ] **Step 4: Rewrite the Scoreboard component**

```jsx
function Scoreboard({ game }) {
  const board = game.scoreboard;
  const unread = board?.unread_ends ?? [];
  return (
    <details id="scoreBox">
      <summary>Scoreboard (read from the wall board)</summary>
      {board ? (
        <>
          <table id="score">
            <tbody>
              <tr>
                <th className="name" />
                {game.ends.map(e => <th key={e.number}>{e.number}</th>)}
                <th>Tot</th>
              </tr>
              {["red", "yellow"].map(c => (
                <tr key={c}>
                  <td className="name">
                    <span className="swatch" style={{ background: `var(--${c})` }} />
                    {game.teams[c].name || c}
                  </td>
                  {game.ends.map(e => (
                    <td key={e.number} className={e.score == null ? "unread" : ""}>
                      {scoreCell(e.score, c)}
                    </td>
                  ))}
                  <td><strong>{game.final ? game.final[c] : "·"}</strong></td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="scorekey">
            <span>&ndash; blank end</span>
            <span>&middot; not posted</span>
            {unread.length
              ? <span>{`ends ${unread.join(", ")} were never posted`}</span>
              : null}
          </p>
        </>
      ) : (
        <p className="scorekey">The wall board could not be read for this game.</p>
      )}
    </details>
  );
}
```

Import `scoreCell` from `../core/index.mjs` at the top of `ChartPanel.jsx`.

- [ ] **Step 5: Style the new marks**

In `src/curling_score/viewer/style.css`, near the existing `#score` rules:

```css
#score td.unread { opacity: 0.45; }
.scorekey { display: flex; gap: 12px; flex-wrap: wrap;
            font-size: 12px; opacity: 0.7; margin: 6px 0 0; }
```

- [ ] **Step 6: Build and run**

```bash
cd frontend && npm run build && cd ..
./.venv/bin/pytest tests/test_viewer_js.py tests/test_frontend_build.py -v
```
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add frontend/core/shots.mjs frontend/viewer/ChartPanel.jsx \
        src/curling_score/viewer/style.css src/curling_score/viewer/app.js \
        frontend/.buildstamp.json tests/test_viewer_js.py
git commit -m "viewer: show the board's score, and say which ends were never posted"
```

---

### Task 11: The watch view's end bar

**Files:**
- Modify: `frontend/core/watch.mjs:75-89`
- Modify: `frontend/viewer/Watch.jsx:66-83`
- Test: `tests/test_viewer_js.py:1167-1177`

**Interfaces:**
- Consumes: `end.running`, `end.score`
- Produces: `endSummary` returning `running: {red, yellow} | null` and
  `score: {red, yellow} | null`

- [ ] **Step 1: Rewrite the failing test**

Replace `TestTheEndSwitcher` at `tests/test_viewer_js.py:1167`:

```python
class TestTheEndSwitcher:
    def test_it_reads_the_board_s_running_score(self):
        """Someone reading down the game wants to know who is winning -- and
        wants it to be the score the club posted, not one we worked out."""
        got = run_js(setup(end_four(hammer="yellow",
                                    score={"red": 3, "yellow": 0},
                                    running={"red": 7, "yellow": 1},
                                    thinking_time={"red": 194.47, "yellow": 311.71})) +
                     "const s = endSummary();"
                     "out([s.number, s.of, s.hammer, s.running, s.red, s.yellow]);")
        assert got == [4, 1, "yellow", {"red": 7, "yellow": 1}, "3:14", "5:12"]

    def test_an_end_the_board_never_reached_has_no_running_score(self):
        got = run_js(setup(end_four(hammer="yellow", score=None, running=None)) +
                     "out(endSummary().running);")
        assert got is None
```

`end_four(**end_kw)` does `ends[0].update(end_kw)` (`tests/test_viewer_js.py:1078`),
so `score=None`/`running=None` overwrite the defaults `doc()` set and reach
`endSummary` as nulls. No harness change is needed.

- [ ] **Step 2: Run it to verify it fails**

Run: `./.venv/bin/pytest tests/test_viewer_js.py::TestTheEndSwitcher -v`
Expected: FAIL — `running` comes back as `{"red": 0, "yellow": 0}`

- [ ] **Step 3: Implement**

In `frontend/core/watch.mjs`, replace the two defaulted fields:

```javascript
    score: end.score ?? null,
    running: end.running ?? null,
```

and update the docstring above `endSummary`: the running score is the board's,
and an end the board never reached has none.

- [ ] **Step 4: Update the end bar**

In `frontend/viewer/Watch.jsx`, replace the `<span className="wsc">` block:

```jsx
        {running ? (
          <span className="wsc">
            <i className="wdot red" />{running.red} – {running.yellow}<i className="wdot yellow" />
          </span>
        ) : (
          <span className="wsc wsc-none">not posted</span>
        )}
```

and add to `style.css` beside the existing `.wsc` rule:

```css
.wsc-none { opacity: 0.55; font-size: 12px; }
```

- [ ] **Step 5: Build and run**

```bash
cd frontend && npm run build && cd ..
./.venv/bin/pytest tests/test_viewer_js.py tests/test_frontend_build.py -v
```
Expected: PASS

- [ ] **Step 6: Check no detected score is reachable from the frontend**

```bash
grep -rn "detected_score\|\.detected\b" frontend/ --include=*.jsx --include=*.mjs | grep -v node_modules
```
Expected: no output. If anything matches, remove it — the detected score must
not reach the screen.

- [ ] **Step 7: Commit**

```bash
git add frontend/core/watch.mjs frontend/viewer/Watch.jsx \
        src/curling_score/viewer/style.css src/curling_score/viewer/app.js \
        frontend/.buildstamp.json tests/test_viewer_js.py
git commit -m "viewer: the end bar carries the board's score or says nothing"
```

---

### Task 12: Retire the presence-only score path

`consolidate`, `per_end_scores` and `split_games` are superseded by
`per_end_from_cards`, and after Task 7 nothing calls them.

**Files:**
- Modify: `src/curling_score/game/scoreboard.py`
- Modify: `tests/test_scoreboard.py`

**Interfaces:**
- Consumes: nothing
- Produces: a smaller module surface

- [ ] **Step 1: Confirm they are dead**

```bash
grep -rn "consolidate\|per_end_scores\|split_games\|read_board_at\|BoardReading" \
  --include=*.py src/ tests/ scripts/ | grep -v "game/scoreboard.py:"
```
Expected: no output outside `scoreboard.py` and `tests/test_scoreboard.py`.
If `read_board_at` still has a caller, leave it; only remove what is unused.

- [ ] **Step 2: Remove them**

Delete `consolidate`, `_CONFIRM_READINGS`, `per_end_scores`, `split_games`,
`read_board` and `read_board_at` from `scoreboard.py`. Keep `BoardReading` and
`read_slots`: `read_cards` uses them as its presence step. Keep `median_frame`,
`is_readable` and `_printed_digit_groups`.

Delete the corresponding test classes from `tests/test_scoreboard.py`:
`TestPerEndScores` and the `split_games` test. Keep `TestCumulative`, updating
it to build a `CardBoard`:

```python
class TestCumulative:
    def test_the_rightmost_card_gives_the_running_total(self):
        board = SB.CardBoard(
            yellow=(SB.Card(1, 1, 0.5), SB.Card(3, 3, 0.5)),
            red=(SB.Card(2, 2, 0.5),),
        )
        assert SB.cumulative(board) == {"yellow": 3, "red": 2}

    def test_an_empty_board_is_nil_all(self):
        assert SB.cumulative(SB.CardBoard((), ())) == {"yellow": 0, "red": 0}
```

- [ ] **Step 3: Update the module docstring**

The header still says the total can be read "without any OCR at all". That is
now the *old* design. Rewrite it to say that a card's slot is the cumulative
total and its digit is the end that produced it, so one late read carries the
whole game — and keep the warning that the board must never time anything.

- [ ] **Step 4: Run the affected subset**

Run: `./.venv/bin/pytest tests/test_scoreboard.py tests/test_analyze_write.py tests/test_timeline.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/curling_score/game/scoreboard.py tests/test_scoreboard.py
git commit -m "scoreboard: retire the presence-only score path"
```

---

### Task 13: End-to-end on the reference VOD

**Files:**
- Modify: `README.md` (the `Validate` row of the stage table)
- Modify: `BACKLOG.md`

- [ ] **Step 1: Re-analyse the reference VOD**

```bash
./.venv/bin/curling-score -v analyze \
  "https://www.youtube.com/watch?v=VXU9xwmugRg" --out out
```

- [ ] **Step 2: Read the result**

```bash
./.venv/bin/python -c "
import json
d = json.load(open('out/timeline.json'))
for g in d['games']:
    sb = g.get('scoreboard')
    print('final', g['final'], '| detected', g['detected']['final'])
    print('  board', json.dumps(sb)[:300] if sb else None)
    print('  ends', [(e['number'], e['score'], e['running']) for e in g['ends']])
"
```

Record: how many reads each game needed, whether any end came back unread, and
whether the board now agrees with detection. One read per game is the design
target; note it if it took more.

- [ ] **Step 3: Look at the viewer**

```bash
./.venv/bin/curling-score serve --out out
```

Check by eye: the score table shows board numbers, blank and unread ends are
visibly different, the total is absent where any end is unread, and the watch
view's end bar says "not posted" rather than showing a made-up score.

- [ ] **Step 4: Update the README stage table**

The `Validate` row reads "Read the wall scoreboard as an independent check".
The board is no longer a check — it is the score. Rewrite that row, and the
"Four decisions carry most of the weight" section if the board now deserves a
place in it.

- [ ] **Step 5: Add the follow-ups to BACKLOG.md**

- Two-glyph card digits, for ends 10 and above.
- Widening the harvest to the other four sheets once their VODs are cached, to
  confirm the card font matches the printed font off sheet 2.

- [ ] **Step 6: Commit**

```bash
git add README.md BACKLOG.md
git commit -m "docs: the wall board is the score, not a check"
```

---

## Self-Review

**Spec coverage.** Every section of the spec maps to a task: the glyph work to
Tasks 2-4, cards-to-scores to Task 5, `MIN_MARGIN` and validation to Tasks 1 and
6, the sampler to Task 7, the timeline model to Task 8, the worker to Task 9,
the viewer to Tasks 10-11, the retirement of the presence-only path to Task 12.
The spec's "no wrong digits" bar is a Global Constraint and an assertion in Task
6. The spec's two open numeric targets are filled in at Task 6 Step 5.

**One deliberate departure from the spec, for the user to confirm.** The spec's
sampler says that on a short read it should "try later for a late posting".
Working it through, that is unsafe: past `end_s` the board is cleared and
repopulated by the next game, and a repopulated board would be read as this
game's. Since cards only accumulate, the latest *usable* read is always the most
complete one, so Task 7 walks backwards from just before `end_s` and takes the
first usable state — which costs one read in the ordinary case, the same as the
spec intended. The cost is that a final end posted right at the buzzer is missed
and reported unread rather than misattributed. `test_it_never_looks_past_the_end_of_the_game`
holds that line.

**Placeholder scan.** No TBD/TODO. Three steps deliberately defer a *value*
rather than a decision, each with the measurement that produces it: `MIN_MARGIN`
(Task 6 Step 2), the `api.py` phase budget (Task 9 Step 3), and the spec's
coverage figures (Task 6 Step 5).

**Type consistency.** `Card(slot, end, margin)`, `CardBoard(yellow, red)`,
`BoardScores(per_end, unread_ends, final)` and `GameBoard(scores, read_at_s,
reads, board)` are defined in Tasks 4, 5 and 7 and used with those names and
fields in Tasks 7, 8 and 13. `read_cards`, `read_digit`, `_card_glyph`,
`templates`, `per_end_from_cards`, `read_cards_at` and `read_game_board` keep
one spelling throughout. `cumulative` changes its parameter type in Task 4 and
every later use passes a `CardBoard`. `scoreCell(score, color)` is defined in
Task 10 and used only there.
