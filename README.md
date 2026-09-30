# curling-score

Turn a Seattle Curling Club YouTube stream into a shot-by-shot game timeline:
every delivery, who threw it, where all the stones came to rest, the line it
was thrown on against the skip's broom, its hog-to-hog split, each team's
thinking time, and the score off the wall board. It reads archived videos and
live streams, four-player games and mixed doubles.

```bash
python3 -m venv .venv && ./.venv/bin/pip install -e ".[dev,gpu]"

curling-score -v analyze "https://www.youtube.com/watch?v=VXU9xwmugRg" --out out
curling-score serve --out out          # opens the timeline viewer
```

The viewer lets you pick a game, an end and a rock — "3rd end, second's first
rock" — and shows the house as it stood after that shot, the rock's line
against the broom, and a link straight to that moment in the video.

The `gpu` extra is torch and ultralytics. Every default model in `weights/` is
a YOLO model, so to run without the extra, set `CURLING_SCORE_WEIGHTS`,
`CURLING_SCORE_SIDE_WEIGHTS` and `CURLING_SCORE_BROOM_WEIGHTS` to `none`. You
get the colour detector, colour-scan hog crossings and no brooms. The models
run on a CPU, but a GPU is what makes it practical: an end takes about two
minutes on the hosted worker's RTX 3070. If YouTube answers "Sign in to confirm
you're not a bot", point `YTDLP_POT_PROVIDER` at a bgutil token provider, or
`YTDLP_COOKIES` at a cookies file (see [`deploy/README.md`](deploy/README.md#the-home-worker)).

## How it works

The club composite puts two near-nadir overhead cameras, one per house, in a
strip down the middle of the frame. Either side of that strip is a wide camera
at each end of the sheet, looking down it at the *other* end's house. Each
source answers a different question:

- The overhead panels say where every stone is.
- The long cameras say when a rock was thrown, along what line, and at what
  broom.
- The wall scoreboard says what the score is.

| Stage | Module | What it does |
|---|---|---|
| Ingest | `ingest/` | Canonicalise the URL and download the video once, or record a live stream as it grows. Crop the overhead strip into a small proxy. Iterate frames by keyframe sweep or dense window |
| Layout | `geometry/layout.py` | Find the two overhead panels by **temporal** invariance |
| Side views | `geometry/sideview.py` | Find the two long cameras and fit each to the far house's paint |
| Lighting | `geometry/lighting.py` | Classify each panel lit / dim / dark |
| Calibrate | `geometry/calibrate.py`, `geometry/hogpaint.py` | Fit pixels→metres from the painted rings, and find each panel's hog line in its paint |
| Segment | `game/segment.py`, `game/boardsplit.py` | Split the stream into games and ends, then split or join games where the board says to |
| Score | `game/scoreboard.py`, `game/slotmodel.py`, `game/digits.py` | Read the wall board's hung cards. This is the game's actual score, not a check on it |
| Detect | `detect/yolo.py`, `detect/rocks.py` | Find stones in each panel, with the trained model or by colour |
| Deliveries | `detect/delivery.py`, `detect/release.py`, `game/fit.py`, `game/secondpass.py` | Watch stones arriving in one house and leaving the other, and let the rules choose which were the end's rocks |
| Lost rocks | `game/sidereleases.py` | Recover, from the long camera, releases the overhead lost and rocks it saw at neither end |
| Placement | `game/placement.py` | Doubles only: the two positioned stones, and so the hammer |
| Rest | `detect/rest.py` | Track stones over time and find the configurations that held |
| Shots | `game/shots.py` | Turn rest states into an ordered, attributed shot sequence |
| Rules | `game/rules.py`, `game/format.py` | Scoring, hammer and shot→player for fours and doubles. Pure logic, no CV |
| Hog times | `game/hogtime.py`, `game/fartime.py`, `detect/sidemodel.py` | Time the throwing hog line from the long camera and the far hog line from its panel's paint |
| Broom | `game/broomtime.py`, `detect/broommodel.py` | Find the skip's target broom, held still in the second before the tee crossing |
| Line | `game/linetime.py` | Read the thrown line against the broom, where the rock went, which way it curled, and the delivery from the hack |
| Split | `game/split.py` | Time the stone hog line to hog line (the long split) |
| Classify | `game/classify.py` | Name what the shot was, as far as it can be seen |
| Clock | `game/thinking.py` | Thinking time per team, and the tee crossings it is read from |
| Live | `live/` | The same pipeline, run one end at a time on a growing recording |

These decisions carry most of the weight.

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

**That scale only holds near the house, so lines are tripwires, not
distances.** By the top of the frame, a panel's along-sheet scale has fallen to
about a third of what the rings give. The hog line's paint therefore reads at
+4.4 to +4.7 m against a real 6.401. A crossing needs no metres, though, only
the row where the paint sits. So each panel's hog line is found in its own
paint. A single constant for every panel had top panels firing 0.17–0.57 s
late.

**We detect the handle, not the granite body.** On a nine-stone cluster packed
into the 4-foot the bodies were touching but the ~10 px handles were still ~20 px
apart. This sidesteps the touching-stone merge that dominates the published
literature (0.97 → 0.72 mAP under heavy occlusion).

**Occlusion is resolved globally, not locally.** Players hide stones for 5–9
seconds at a time, so no sliding window short enough to track play can smooth
that away. Instead each stone is tracked across the whole end: an occluded stone
comes back to the same place, a removed one never does.

**A delivery is seen at both ends, and the rules decide which ones count.**
Inferring throws from how the house changes does not work. Between ends,
players push stones back toward the end they are about to throw from, and
those staged rocks look exactly like arrivals. So a delivery is a stone that
enters the far panel and runs to rest, and a release is a stone seen leaving
the thrower's own house. A throw with no arrival is a hogged rock. Every
phantom seen in this footage has been a person: a sweeper in team colours, or
the house being cleared. The rules settle those from outside the pixels: an end
holds sixteen rocks, eight a team, thrown strictly in turn (`game/fit.py`).

**The long camera times the throw.** The overhead panel loses the throw before
the hog line on about 40% of deliveries, because it looks straight down at a
stone with the thrower and sweepers standing over it. The camera at the far end
is level with the ice, so the sweepers stand beside the stone rather than over
it. A detector trained for that view (`ds13c`) times the throwing hog crossing.
Its predecessor, against the colour scan on one whole game, took published
splits from 21.2% to 77.9%. On 27 hand-marked crossings it timed 25 against 9,
with a median error of 0.018 s. The same camera sees the skip's broom and the
rock's line past the hog line.

**The wall scoreboard is the score, read one digit at a time.** The club board
is the traditional design: a fixed strip of numbers 1–14 that is the
*cumulative* score, with the end number written on a card hung above the strip
(yellow) or below it (red). A card's slot alone gives the running total — that
part needs no OCR, only which slots are occupied. The digit printed on each card
names the *end* that produced the total at that slot. So one read of a board
late in the game, with every card decoded, hands back the score of every end
posted so far. Presence alone can only say what the total is, never which end
brought it there. That is why the original presence-only design (see
`BACKLOG.md`) was retired once the digit reader existed.

Three details make the board reliable to find and read. It is located from its
two team-colour markers, which also fix its scale, since every sheet hangs its
board somewhere different. Whether a slot holds a card is decided by a small
trained model over the slot's own window (`game/slotmodel.py`, 10,892 labelled
slots from ten videos). It replaced two brightness thresholds, which a narrow
"1" could slip past, losing that board's end-1 score. Held out a video at a
time, it misses 11 cards and calls 12 blanks cards, against the thresholds' 55
and 20. The digit is decoded by another small classifier, which refuses any
call below a 0.9999 class probability. At that bar it made zero wrong reads
and accepted none of 19 blank-slot frames. A refused digit invalidates the
whole read rather than being guessed, and the sampler steps back to an earlier
moment and tries again.

It is **never used for timing**. The club often posts it several ends late,
sometimes after the game is over. So an end the board has not caught up to
comes back unread rather than guessed, and the game's final is withheld until
every end has one.

**The board also says where one game ends and the next begins.** A game ends
when both houses sit empty for four minutes. A changeover with stones still in
view never reads empty that long, and doubles sheet 4 on 2026-09-27 came out as
one twelve-end game. So a game is split where the board is cleared across a gap
of at least 120 s between ends, once it is at least four ends in. It is joined
back up across a pause of up to ten minutes that the board stayed up through.

**A card's digit is the real end number; detection counts blocks.** Club
streams open with twenty minutes of practice that arrives as leading ends,
numbered and scored. The two numberings differ by however many practice blocks
were detected, so attaching the board by block number would hand the first real
end the *second* end's score. A start time settles it. The trim
(`timeline.trim_to_start`) drops the practice and re-derives every kept end's
score from the surviving board block, so how detection cut the stream cannot
change a score. The hosted service takes the start time from the chart or the
link's `t=`. Without one, nothing says how many leading blocks are practice.
A game that shows the practice signature is refused rather than guessed: a
leading end short of a full end, with a board that cannot account for every
detected block. Its ends keep no board score, and the scoreboard block records
why under `scores_withheld`.

## Coordinates

Sheet metres, origin at the tee of the playing house, `+y` up-sheet toward the
delivery end, `+x` to the right facing down-sheet (the thrower's right).
Constants come from World Curling's *Rules of Curling* (July 2025); a stone
counts when it is within `1.971 m` of the tee (12-foot radius 1.829 + stone
radius 0.142).

## Models

| Job | Default | Override | Set to `none` for |
|---|---|---|---|
| Stones in the overhead panels | `weights/ds16a.pt` | `CURLING_SCORE_WEIGHTS` | the colour detector |
| Stones crossing the throwing hog line, in the long camera | `weights/ds13c.pt` | `CURLING_SCORE_SIDE_WEIGHTS` | the colour scan |
| The skip's broom head, in the long camera | `weights/broom2.pt` | `CURLING_SCORE_BROOM_WEIGHTS` | no brooms |
| Card in a scoreboard slot, and its digit | `game/slot_weights.npz`, `game/digit_weights.npz` | — | — |

The two board models run in numpy, so reading the board needs no `gpu` extra.
Each YOLO model's name and content hash go into the timeline's
`processing_version`. Changing a model therefore invalidates cached timelines
rather than quietly mixing them. A path that is set but missing is an error.
Why each model replaced the last, with its numbers, is in `weights.py` and the
dataset READMEs under `datasets/`.

## Mixed doubles

`analyze --format doubles` (or a title that says "doubles") builds each end
from mixed doubles rules. One stone per team is placed before the end, and five
are delivered. The format comes from the caller, then the title, then fours.
When the ends look like the other format, the timeline's format check says so
and the viewer shows a warning, but the format is never overridden.

`game/placement.py` finds the two placed stones and reads them. Everything that
settled before the placement is dropped, and rock 1 is diffed against the
placed stones. The team whose stone is in the house has the hammer, and a power
play is read from which side both stones were moved to. Player A throws a
team's first and last rocks and player B the three between. A per-end "swapped
roles" override makes the stats follow the person. Usually nobody holds a
broom in a doubles house, so a rock with no broom still gets its line, pinned at
the tee. The figures that measure against a broom are left blank.

## Live streams

The hosted worker follows a stream while it is being played. It records from
the stream's first segment into a growing MPEG-TS, calibrates once fifteen
minutes exist, and builds and publishes each end once the next end has started
and it can no longer move. Measured against the archived video on four games,
97–99% of shots match and every end does. To run the same thing against a
cached video:

```bash
curling-score live-replay ~/.cache/curling_score/videos/<id>.mp4 --speed 4   # writes out-live/, every publish kept
```

What the hosted worker needs to follow live games is in
[`deploy/README.md`](deploy/README.md#following-live-games): stream counts,
bandwidth, and the rule that a stream must be caught in its first hour.

## The frontend

The charting viewer and the site pages are React, built from `frontend/` with
esbuild into two files that are **tracked in git**:

```bash
cd frontend && npm install     # once. five packages and their dependencies
npm run build                  # lint, then -> src/curling_score/viewer/app.js
                               #                src/curling_score/service/static/site.js
npm run watch                  # rebuilds on save, unminified and unstamped; reload the page
npm run lint                   # just the lint
npm run check                  # rebuild and compare against the stamp
```

The build lints first, and refuses to bundle if it fails. That is there for one
rule: esbuild compiles an undefined identifier without complaint, so a name
used in a component it was never passed builds clean and renders an empty page.
`no-undef` finds it in a second. A second rule keeps `frontend/core/` away from
the browser: `document`, `fetch`, `window`, `localStorage` and the rest. The
Python suite imports it under bare node, and otherwise the only symptom is a
crash in a test that looks unrelated.

**Always build through `npm run build`, never by calling esbuild directly.**
The build writes `frontend/.buildstamp.json`, and `tests/test_frontend_build.py`
fails when a source has changed without a rebuild, or when a bundle has been
edited by hand. Invoking esbuild yourself skips the stamp and breaks that check
for everyone else. So does leaving `watch`'s output in place: finish with a
`build`.

The output is committed because `curling-score serve` above is the first thing
anyone runs, and it has to work after a plain `pip install` on a machine with
no node on it. Nothing in either image installs node.

To see every surface at once against the real service on in-memory backends —
editing, view-only, the public watch page, a run still processing, the
catalogue and the submit page:

```bash
python scripts/devserve.py out/timeline.json            # needs a checkout: it borrows the API tests' fakes
python scripts/devserve.py out/timeline.json --live 15  # ...and a live watch page, one end every 15 s
```

Under `curling-score serve` the viewer reaches for nothing but YouTube, which
`tests/test_frontend_build.py` also checks. On a hosted page the Flag dialog
loads Firebase sign-in when it is opened, if accounts are on.

## Tests

```bash
./.venv/bin/pytest                 # everything, ~3,000 tests
./.venv/bin/pytest -m "not slow"   # skips tests that read the full cached VOD
```

Detection and geometry are gated against the reference VOD and one validation
video per sheet (`tests/conftest.py`), so a change that helps one sheet and
hurts another is caught. Tests that need a cached video, harvested frames,
ffmpeg, node or torch skip when it is absent; none needs a GPU. Two failure
modes found during development are permanent regression tests: a band of clean
ice being read as a panel separator, and a panel whose lights are out being
read as an empty house rather than "no play".

`tests/test_longview.py::TestAgainstHandMarkedCrossings` fails on main and has
for a while. Deselect it rather than chase it.

## Finding what the detector missed

`review` turns the rules into a short worklist. Teams alternate, so the same
colour twice running means exactly one delivery is missing *and* pins it
between two known times — often under a minute of video. Sixteen stones land at
a fairly even rhythm, so a gap well beyond it holds at least one more.

```bash
curling-score review "https://www.youtube.com/watch?v=VXU9xwmugRg" --out review
open review/index.html
```

Each suspected miss becomes one strip of frames spanning the window, with the
expected colour where alternation determines it and a link into the video at
that moment. Ticks are saved in the browser, so a pass can be done in sittings.

A note on the spacing estimate: a gap only ever *grows* when a delivery inside
it was missed, never shrinks, so the median is dragged upward by the very
anomalies it is meant to find. The lower quartile is used instead.

`review` builds its ends with an older subset of `analyze`'s steps. It has no
clearing filter and no long-camera recovery, and it assumes sixteen rocks. So
it lists windows that `analyze` has already filled, and it does not understand
doubles. Hand observations go in `validation/ground_truth.json` (see
`validation/README.md`).

## Known limitations

- **The score is only as complete as the board.** The club posts late, and the
  last end's card often goes up after the stream has ended, so a game's final
  is often withheld. The digit model has seen real cards only for 1–7, and a
  7 on just one card. It knows 8 and 9 only from the board's printed strip. An
  extra end's "10" is two glyphs in one card. The reader refuses it, so every
  end from 10 on comes back unread. Detection's own
  score is still computed for every end (`detected_score`, and `detected` at
  the game level) as a diagnostic, not a fallback. The interesting signal is
  the two disagreeing.
- **Rocks are still missed, and filled in where the rules allow.** On 45 hosted
  games (pipeline 2026.09.24–.28, 30 fours and 15 doubles), 97.4% of rocks were
  seen and 253 of 270 ends had every one. A rock the rules say must exist but
  nothing saw is a placeholder, drawn as `STATE UNKNOWN`. An end short by more
  than four is not filled at all.
- **In fours the hammer is read from who threw first.** When that first rock is
  a placeholder, the hammer follows the inferred colour. The board's own chain
  (the team that scores throws first next end) is computed as
  `hammer_expected`, and `hammer_consistent` says whether the two agree. It does
  not yet replace the read.
- **Positions away from the house are approximate.** Inside the house the ring
  fit holds to a few millimetres. Beyond it the panel's scale falls away, as
  above, so a guard sits roughly where it is drawn. Timings are unaffected,
  because every line is a tripwire at its paint.
- **A split is timed on two different cameras, and the club's are not in
  sync.** The throwing crossing comes from the long camera and the far one from
  the arriving panel. Eight of nine cached recordings had a camera pair out of
  step, some by most of a second and some jumping mid-game. No correction is
  applied. `long_split_panel_delta_s` records how far the throwing panel
  disagreed with the long camera. The split is hog to hog, over the 21.843 m
  the stone's leading edge covers. It is refused when it implies the stone sped
  up by more than 40%, the slack allowed for two cameras. On 13 held-out games
  it covered 92% of 1,389 rocks. `splits_measured` counts it per end.
- **Thinking time is a lower bound.** An end's first stone has nothing to time
  from, so a seven-end game can never read more than 105 of its 112 rocks. Every
  total says how many it was read from. The clock needs only the moment the
  rock crossed the tee line, and releases the overhead lost are now timed from
  the long camera. Where even that was not seen, it is assumed at the 16 s
  median measured over 503 timed throws. Those intervals are marked `(est.)` and
  counted in `estimated_shots`.
- **Shot types are coarse.** Any contact is a `hit`, including a draw-weight tap
  on a guard. A rock that left play with no split is called `flashed` at half
  confidence.
- **The line reads throws outside the broom.** The line fits straight to
  0.2–0.4 cm, and the camera behind the thrower agrees with it to about 4 cm.
  But throws read systematically wide of the broom, and no person has checked
  that yet, so the viewer claims ±4 in and no more. A third to a half of long
  draws and guards are hidden from the camera behind the thrower, and their line
  is unconfirmed.
- **Doubles: an unseen guard.** If a placement's guard is never seen, a later
  rock can be taken for it.
- **A night with one overhead camera fails.** The layout expects two panels. The
  club streams one on roughly 5% of the season.
- **A live stream has to be caught in its first hour.** After that, a dropped
  recorder or a worker restart fails the live job, and the game waits for the
  archived video.

Corrections go in `out/overrides.json`, keyed `"<game>.<end>.<shot>"`, or
`"<game>.<end>"` for an end-level override such as a doubles role swap. They are
layered over the detections on load, so re-running never destroys them.

## Charting a game

```
curling-score analyze <url> --out out
curling-score serve --out out
```

The viewer is a charting tool, and the score it shows is the wall board's, not
anything computed from the charted shots. For each shot it shows the video, the
house it left behind, and the path the stone took. You fill in what the
detector could not read and grade the shot as a coach would.

- **Blanks are explicit.** A shot whose house we could not read is hatched and
  labelled `STATE UNKNOWN` — never drawn as an empty house. The header counts
  how many are left; `n` jumps to the next one.
- **The video starts before the throw.** Each shot seeks to `t_enter_s` minus a
  lead-in (10 s by default, set under the video), so you see the call and the
  delivery rather than a stone already at rest. One embedded player is reused
  throughout — navigating never reloads it.
- **The house shows what the shot was aimed at and what it hit.** The skip's
  broom is drawn with a line to where the rock stopped. Ghosts mark where the
  stones it hit sat before it.
- **Shot detail** is the rock's line on the sheet, with six figures:
  - where it passed the broom, wide or narrow;
  - which hack it was thrown from, Left or Right, one per player per game;
  - its offset at the hog line;
  - its weight, as the long split;
  - its curl, measured to where it stopped or to the stone it hit;
  - where it came to rest.

  Under it, the delivery chart follows the rock from the hack to 1.5 m past the
  hog line. On the desktop both sit under the video, with the sheet on its side.
- **Shot types.** The detector offers only `draw`, `guard`, `hit`,
  `draw_through`, `flashed`, `hogged` or `unknown`. It reads them from where the
  stone stopped, what it moved, and, for a hogged rock, from having seen it
  thrown and never arrive. A rock that left play is split by its long split:
  over 12.5 s hog to hog it was a draw thrown through, under it a takeout that
  flashed. With no split timed it falls to the flash at half confidence. The
  full Curl Coach taxonomy (freeze, peel, raise, run back…) is yours to pick,
  because those describe what was *called*.
- **Grading** is Curl Coach's 0–4 per shot, with a miss reason and a note. The
  Report view groups every player's shots by type and gives an average and a
  shooting percentage (`points ÷ (4 × shots graded)`). Every type counts once
  it is graded, a throw-away included. Ungraded shots count as thrown but never
  as misses, and the report says how many are still ungraded.
- **The clock** is charted there too: each team's thinking time accumulated
  rock by rock, with the ends marked along the bottom. A team's line is flat
  through the other team's rocks, so the gap between them at any point is what
  the two have spent — and where it opens is the end that cost it.

Keys: `←`/`→` shots within the end, `n` next blank, `r`/`y` stone colour, `x`
delete, `d` mark the delivered stone, `c` recolour, `t` track overlay, `v`
replay, `p` play/pause, `0`–`4` grade, `Enter` mark charted and move on, `Esc`
close the report.

Everything you enter is written to `out/overrides.json` as you go. The viewer
POSTs it back to its own server, and `⬇` in the `⋯` menu downloads it if the
server is gone. Re-running `analyze` layers the same file back over fresh
detections.

**On a phone, a read-only link (`/g/` or `/s/`) is four tabs**: House,
Detail, Delivery and Timing. A pager swipes rock by rock across ends, and
tapping it opens an end picker. While the video plays, the rock follows it.
Timing is the whole game's clock, with every end under its own header. The tab
and the rock are kept in the URL, so a link names a rock.

**Computed scoring is deliberately de-emphasised; the board's is not.** A
collapsed "Scoreboard" panel shows the wall board's own end-by-end score. Blank
and unread ends are visibly distinct from each other and from a real zero, and
the game total is withheld until every end has one. What the detector makes of
the same ends (`detected_score` per end, `detected` per game) is still computed
and written to the timeline, but it is not surfaced anywhere in the viewer.
Precise measurement, and the occlusion that comes with players clearing rocks,
make it unreliable on its own. Showing it next to the board's real score risked
the two being mistaken for each other.

## Hosting it

The same pipeline runs as a small public service. Paste a link and get a
private charting URL. The club's league playlists are watched, each on its own
schedule, and processed automatically. A stream that goes live is followed end
by end while it is played. A catalogue at `/games` lists every game. The API
runs on Cloud Run with Firestore and Cloud Storage, all inside free tiers at
club scale. Processing runs on a home GPU machine that pulls jobs over HTTPS.
See [`deploy/README.md`](deploy/README.md).

There are three ways to open a game, and which URL you have decides what you
can do with it:

| | | |
|---|---|---|
| `/g/{game}/` | **Watch** | Public. Steps through a game shot by shot, with the house and the video, and nothing to fill in. Linked from the catalogue, creates nothing, and has no route that could write. A live game shows from its first end and fills in as ends are published. |
| `/s/{slug}/` | **View** | Somebody's chart, including their grading, read-only. It carries only its own key, never the chart's. |
| `/c/{slug}/` | **Chart** | The charting tool. A live game can be charted once it is over. |

The two slugs are 22 random characters, and holding one *is* the permission it
carries. No account is needed for any of it.

**Accounts** are optional and purely additive. Signing in with Google gives you
a list at `/mine` of the games you have submitted or charted, so losing the
link stops mattering. You can also name a game's teams and league. A **team**
shares one chart per game, so everyone fills in the same sheet rather than four
of them, and members see the same list. Anyone holding a chart link can still
edit it whether or not they are signed in, and whether or not they are on the
team. An account changes what you can *find*, never what a URL lets you do.

**⚑ Flag**, in the `⋯` menu of any hosted page, leaves a note about the rock
on screen for whoever runs the service, signed in or not. `scripts/flags.py`
lists and resolves them. The **thinking report** at `/thinking` ranks each
league's slowest and most one-sided games by thinking time.

Leave `FIREBASE_PROJECT` unset and there are no accounts: no sign-in button,
and the service behaves exactly as it did before any of it was added.
`DOUBLES_ENABLED` and `LIVE_ENABLED` switch on doubles submissions and live
jobs. Both default to off.

```bash
# try it locally. The API must expect the worker's model, or every claim is refused
MEMORY_BACKENDS=1 WORKER_TOKEN=w ADMIN_TOKEN=a \
  MODEL_ID=$(python -c 'from curling_score import weights, version; print(version.model_id(weights.default_path()))') \
  uvicorn curling_score.service.asgi:app
API_URL=http://127.0.0.1:8000 WORKER_TOKEN=w python -m curling_score.service.worker      # …and a worker
```
