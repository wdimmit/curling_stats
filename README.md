# curling-score

Turn a Seattle Curling Club YouTube stream into a shot-by-shot game timeline:
every delivery, who threw it, where all the stones came to rest, and the score.

```bash
python3 -m venv .venv && ./.venv/bin/pip install -e ".[dev]"

curling-score -v analyze "https://www.youtube.com/watch?v=VXU9xwmugRg" --out out
curling-score serve --out out          # opens the timeline viewer
```

The viewer lets you pick a game, an end and a rock — "3rd end, second's first
rock" — and shows the house as it stood after that shot, with a link straight to
that moment in the video.

## How it works

The club composite puts two near-nadir overhead cameras, one per house, in a
strip down the middle of the frame. Everything is read from those two panels.

| Stage | Module | What it does |
|---|---|---|
| Ingest | `ingest/` | Canonicalise the URL, download the video once, iterate frames by keyframe sweep or dense window |
| Layout | `geometry/layout.py` | Find the two overhead panels by **temporal** invariance |
| Lighting | `geometry/lighting.py` | Classify each panel lit / dim / dark |
| Calibrate | `geometry/calibrate.py` | Fit pixels→metres from the painted rings |
| Detect | `detect/rocks.py` | Find stone **handles** by colour |
| Rest | `detect/rest.py` | Track stones over time; find the configurations that held |
| Segment | `game/segment.py` | Split the stream into games and ends |
| Shots | `game/shots.py` | Turn rest states into an ordered, attributed shot sequence |
| Rules | `game/rules.py` | Scoring, hammer, shot→player — pure logic, no CV |
| Validate | `game/scoreboard.py` | Read the wall scoreboard as an independent check |

Four decisions carry most of the weight:

**Panels are found by what does *not* change over time.** Every sheet has a
different layout (strip widths of 294–302 px were measured across the five), so
nothing is hardcoded. A per-frame "flat and bright" test is unsafe: clean ice is
also flat and bright, and reading a band of it as a separator once put the
bottom-panel crop inside the top panel.

**Scale comes from the sum of the green ring's two edges.** Colour thresholding
erodes the painted ring by ~2 px on each side, so the 12-foot edge reads small
and the 8-foot edge reads large by the same amount. Adding them cancels the bias
exactly, which leaves the blue 4-foot ring unused by the fit and therefore
available as a genuine holdout check — 0.29 cm mean error, 0.69 cm worst case
across all ten panels of all five sheets.

**We detect the handle, not the granite body.** On a nine-stone cluster packed
into the 4-foot the bodies were touching but the ~10 px handles were still ~20 px
apart. This sidesteps the touching-stone merge that dominates the published
literature (0.97 → 0.72 mAP under heavy occlusion).

**Occlusion is resolved globally, not locally.** Players hide stones for 5–9
seconds at a time, so no sliding window short enough to track play can smooth
that away. Instead each stone is tracked across the whole end: an occluded stone
comes back to the same place, a removed one never does.

**The wall scoreboard is read without OCR.** The club board is the traditional
design: a fixed strip of numbers 1–14 that is the *cumulative* score, with the
end number on a card hung above it (yellow) or below (red). A card's position is
therefore the running total, so the score can be read purely by asking which
slots are occupied.

Three details make that reliable. The board is found from its two team-colour
markers, which also fix its scale — every sheet hangs its board somewhere
different. A card is identified as both *brighter* and *darker* than the board
around it: a spectator standing in front has just as much internal contrast, but
is never brighter, which is what separates them (measured: cards +22..+29
brighter, people 59–105 darker). And because cards accumulate through a game and
are never taken down, a slot seen once and then gone is discarded as noise.

It is **never used for timing** — the club often posts it several ends late. It
is read only to say whether the computed scores are believable.

## Coordinates

Sheet metres, origin at the tee of the playing house, `+y` up-sheet toward the
delivery end, `+x` to the right facing down-sheet. Constants come from World
Curling's *Rules of Curling* (July 2025); a stone counts when it is within
`1.971 m` of the tee (12-foot radius 1.829 + stone radius 0.142).

## Tests

```bash
./.venv/bin/pytest                 # everything
./.venv/bin/pytest -m "not slow"   # skips tests that read the full cached VOD
```

Detection and geometry are gated across a six-VOD validation set spanning all
five sheets and four dates, so a change that helps one sheet and hurts another is
caught. Two failure modes found during development are permanent regression
tests: a band of clean ice being read as a panel separator, and a panel whose
lights are out being read as an empty house rather than "no play".

## Finding what the detector missed

Delivery detection currently recovers about 60% of an end's sixteen stones, and
improving that needs ground truth we do not have — the only labels available were
generated by the detector itself, so measuring it against them grades its own
homework.

`review` turns the rules into a short worklist instead. Teams alternate, so the
same colour twice running means exactly one delivery is missing *and* pins it
between two known times — often under a minute of video. Sixteen stones land at a
fairly even rhythm, so a gap well beyond it holds at least one more.

```bash
curling-score review "https://www.youtube.com/watch?v=VXU9xwmugRg" --out review
# add --weights runs/stones/weights/best.pt to detect with a trained model
open review/index.html
```

Each suspected miss becomes one strip of frames spanning the window, with the
expected colour where alternation determines it and a link into the video at that
moment. Ticks are saved in the browser, so a pass can be done in sittings.

A note on the spacing estimate: a gap only ever *grows* when a delivery inside it
was missed, never shrinks, so the median is dragged upward by the very anomalies
it is meant to find. The lower quartile is used instead.

## Known limitations

- **Delivery recall is the limiting factor.** Across both games of the reference
  VOD, 50% of deliveries are found with colour thresholding and 60% with a
  trained YOLO model. Ends built from 5–8 deliveries have unreliable scores.
  `curling-score review` exists to attack this.
- **Scores are not yet trustworthy.** Against the wall scoreboard on game 1,
  colour thresholding matched 0 of 8 ends and YOLO matched 2 of 8. The structural
  bug that scored an end from the post-end staging is fixed — end 4 now reads
  `red 1`, matching the board — but recall still dominates the error.
- **The hammer is wrong whenever an end's first delivery is missed**, since it is
  read from who threw first.
- **Positions far up-sheet are approximate.** The oblique view compresses the
  far field; the calibration is a pure scale about the house. Everything inside
  the scoring region is accurate, guards less so.
- **The hammer chain does not yet self-validate.** It is read from who threw
  first, which is wrong when an end's opening deliveries are missed.
- **Scoreboard validation is not implemented** (the board sits at a different
  place on each sheet's wall).
- Archived VODs only — not live streams.

Corrections go in `out/overrides.json`, keyed `"<game>.<end>.<shot>"`, and are
layered over the detections on load so re-running never destroys them.
