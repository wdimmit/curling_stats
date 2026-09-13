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
| Release | `detect/release.py` | Watch the *thrower's* house for stones leaving: a throw with no arrival is a hogged rock |
| Segment | `game/segment.py` | Split the stream into games and ends |
| Shots | `game/shots.py` | Turn rest states into an ordered, attributed shot sequence |
| Rules | `game/rules.py` | Scoring, hammer, shot→player — pure logic, no CV |
| Split | `game/split.py` | Time a stone down the sheet — the long split |
| Clock | `game/thinking.py` | Thinking time per team, and the tee crossings it is read from |
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
  the scoring region is accurate, guards less so. The scale itself holds up: a
  stone's detected box is flat to ~2% from the tee out to +4 m.
- **The long split is not hog to hog.** Neither hog line is inside an overhead
  panel — they reach about +4.6 m against a hog line at 6.401 — so the split is
  measured over a stated 24.95 m baseline, from the near hog line to a line
  3.4 m up-sheet of the far tee. Only the throwing end is extrapolated, and
  only across the 1.7–3.9 m the slide covers at a near-constant speed; each
  shot reports how much. Going the other way was measured and rejected: a
  crossing predicted 1.0 m beyond an arrival's track misses by a median 0.71 s.
- **The split covers a minority of shots, and says so.** On the reference end it
  reads 3 of 16. The limit is the release-to-arrival pairing, which was built to
  ask whether a rock arrived at all and is too loose to time with; a split whose
  mean speed exceeds the speed the stone was measured sliding at is refused
  rather than published. A guard that stops above the arrival line never crosses
  it and has no split at all. `end["splits_measured"]` is the count.
- **Thinking time is a lower bound.** An end's first stone has nothing to time
  from, so a seven-end game can never read more than 105 of its 112 rocks, and
  every total says how many it was read from. The clock does not need a paired
  release — it wants the moment the rock crossed the tee line and nothing more,
  which the throwing camera gives up far more readily than it gives up a throw
  (94% against 71% on the game this was measured on). Where even that was not
  seen it is assumed, at the 16 s median measured over 503 timed throws, and
  those intervals are marked `(est.)` and counted in `estimated_shots`.
- **The hammer chain does not yet self-validate.** It is read from who threw
  first, which is wrong when an end's opening deliveries are missed.
- **Scoreboard validation is not implemented** (the board sits at a different
  place on each sheet's wall).
- Archived VODs only — not live streams.

Corrections go in `out/overrides.json`, keyed `"<game>.<end>.<shot>"`, and are
layered over the detections on load so re-running never destroys them.

## Charting a game

```
curling-score analyze <url> --out out
curling-score serve --out out
```

The viewer is a charting tool, not a scoreboard. For each shot it shows the
video, the house it left behind, and the path the stone took; you fill in what
the detector could not read and grade the shot as a coach would.

- **Blanks are explicit.** A shot whose house we could not read is hatched and
  labelled `STATE UNKNOWN` — never drawn as an empty house. The header counts
  how many are left; `n` jumps to the next one.
- **The video starts before the throw.** Each shot seeks to `t_enter_s` minus a
  lead-in (10 s by default, adjustable in the header), so you see the call and
  the delivery rather than a stone already at rest. One embedded player is
  reused throughout — navigating never reloads it.
- **Shot types.** The detector offers only `draw`, `guard`, `hit`, `through`,
  `hogged` or `unknown`, from where the stone stopped, what it moved, and --
  for a hogged rock -- from having seen it thrown and never arrive. The full
  Curl Coach taxonomy (peel, freeze, come around, run back…) is yours to pick,
  because those describe what was *called*.
- **Grading** is Curl Coach's 0–4 per shot, with a miss reason and a note. The
  Report view groups every player's shots by type and gives an average and a
  shooting percentage (`points ÷ 4 × shots graded`). Ungraded shots count as
  thrown but never as misses, and the report says how many are still ungraded.
- **The clock** is charted there too: each team's thinking time accumulated
  rock by rock, with the ends marked along the bottom. A team's line is flat
  through the other team's rocks, so the gap between them at any point is what
  the two have spent — and where it opens is the end that cost it.

Keys: `←`/`→` shots, `n` next blank, `r`/`y` stone colour, `x` delete, `d` mark
the delivered stone, `c` recolour, `t` track overlay, `v` replay, `p`
play/pause, `0`–`4` grade, `Enter` mark charted and move on.

Everything you enter is written to `out/overrides.json` as you go (the viewer
POSTs it back to its own server; `⬇` downloads it if the server is gone).
Re-running `analyze` layers the same file back over fresh detections.

**Scoring is deliberately de-emphasised.** Computed end scores are still
produced and shown in a collapsed panel, but precise measurement and the
occlusion that comes with players clearing rocks make them unreliable, and they
are not what this tool is for.

## Hosting it

The same pipeline runs as a small public service: paste a link, get a private
charting URL; the club's league playlists are watched and processed
automatically; a catalogue lists every game. The API runs on Cloud Run with
Firestore and Cloud Storage, all inside free tiers at club scale; processing
runs on a home GPU machine that pulls jobs over HTTPS. See
[`deploy/README.md`](deploy/README.md).

There are three ways to open a game, and which URL you have decides what you
can do with it:

| | | |
|---|---|---|
| `/g/{game}/` | **Watch** | Public. Steps through a game shot by shot, with the house and the video, and nothing to fill in. Linked from the catalogue, creates nothing, and has no route that could write. |
| `/s/{slug}/` | **View** | Somebody's chart, including their grading, read-only. |
| `/c/{slug}/` | **Chart** | The charting tool. |

The two slugs are 22 random characters and holding one *is* the permission it
carries — that has not changed, and no account is needed for any of it.

**Accounts** are optional and purely additive: signing in with Google gives you
a list at `/mine` of the games you have submitted or charted, so losing the
link stops mattering. A **team** shares one chart per game — everyone fills in
the same sheet rather than four of them — and members see the same list. Anyone
holding a chart link can still edit it whether or not they are signed in, and
whether or not they are on the team; an account changes what you can *find*,
never what a URL lets you do.

Leave `FIREBASE_PROJECT` unset and none of that exists: no sign-in button, and
the service behaves exactly as it did before any of it was added.

```bash
MEMORY_BACKENDS=1 WORKER_TOKEN=w ADMIN_TOKEN=a uvicorn curling_score.service.asgi:app   # try it locally
API_URL=http://127.0.0.1:8000 WORKER_TOKEN=w python -m curling_score.service.worker      # …and a worker
```
