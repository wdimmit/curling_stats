# A player class in the overhead detector

**Status:** approved by the user, 2026-11-01.

## Why

The house read loses stones that players stand over. A stone the overhead
detector cannot see for most of a rock's read window counts as removed, so
the rock is called a hit and the house it left is wrong. Flag f_0zkA
(10/22 Thursday Mens sheet 1, `s_0XkHC8h6iKjzjuGUt`, video 6vOTUuUSMKc,
game 2 end 7) has two of these:

- Rock 15: the red skip crouched over a red behind the house for the whole
  of the rock's 12 s window, so the red read as removed.
- Rock 16: a player stood over two yellow guards just after the stop, so
  they read as removed. The reach veto now repairs this after the fact.

Frames and a run of overhead-3 on them showed the stone detector is not at
fault: it finds every stone that is uncovered. The rule that turns "not
seen" into "removed" is.

A prototype (branch `occlusion-hold`, 185cad0e, never merged) fixed this
with a rule: put a lost stone back if a player covered its spot in every
frame it went unseen. Player positions came from an off-the-shelf COCO pose
model (yolo11x-pose at confidence 0.1). On the 16-game harness it put back
15 stones, all 15 confirmed on video. Scoreboard agreement went from 41 to
42 of 54 ends with none worse. Impossible additions fell from 15 to 10. It
also fixed both of the flag's rocks.

Its weak link is the person model. COCO learned people side-on, and a
crouched player seen from straight above is a cap and two shoulders. The
model missed the skip on rock 15 for 8 s at its first settings, and it
misses players cut off at the panel edge. It is also a second large model
decoding frames of its own.

The user chose to fix this in the model, in line with their standing
preference for model fixes over rules (memory: prefer-model-fixes). The
overhead detector learns a third class, **player**. It already runs on
every house frame at 10 fps, so the player boxes come at no extra cost, and
a model trained on these views should find top-down and edge players that
COCO misses.

## Decided with the user

- **Labels are made by the pose model and reviewed by people.** The pose
  model draws provisional player boxes, and a person reviews every frame.
- **Review happens on `/label`**, by members, like the stone boxes.
- **One review per frame** is enough, not the two the stone and release
  items get.
- **Model first.** Extraction, labelling and training come before the
  pipeline changes, and the model's convergence is checked before them
  (see Order of work).
- **A pilot first.** Once the hard batch and a few hundred other frames are
  fully reviewed, a pilot model is trained on just those frames (unreviewed
  frames left out whole) for an early read on whether the player class
  learns. It is never shipped.
- **overhead-4 reads only the receiving panel** (the user, 2026-11-02).
  Each end's receiving panel, where the house read and the cover check
  live, is read by overhead-4. The throwing panel (releases, the hog-line
  split, hogged rocks) and the pass that finds games and ends keep
  overhead-3, where occlusion doesn't matter. So only receiving-panel frames
  are labelled and trained on.
- The rest of the design as played back (2026-11-01) was approved: what
  gets labelled, what a box covers, what a reviewer does, the cover check
  reading the detector's own boxes, the shipping bar, and the out-of-scope
  list.

## Goals

1. Every frame of the next overhead training set, and of its validation
   set, has every visible player boxed, reviewed by a person.
2. A new model, **overhead-4**, detects red stones, yellow stones and
   players on the receiving panel, with stones there at least as good as
   overhead-3's.
3. The cover check in `shots.from_deliveries` reads overhead-4's player
   boxes from the house frames already in hand. No frame is read twice and
   there is no extra decoding: overhead-4 replaces overhead-3 on the
   receiving panel rather than running beside it.
4. On the 16-game harness it catches at least what the pose prototype
   caught, and fixes both of f_0zkA's rocks.

## Non-goals

- Retraining the long-camera model.
- Other uses of player boxes, such as detecting the clearing, the thrower,
  or sweepers. Those can come later and are not designed here.
- Shipping the pose prototype in the meantime. It stays parked on its
  branch.
- Practice sessions. They read both houses together with no ends, and
  build their house change without the cover check, so they keep
  overhead-3.
- Reprocessing hosted games. Shipping overhead-4 does not imply a
  reprocess; that is the user's call afterwards, as usual.
- Identifying anyone. A player box is a body outline from above, with no
  identity attached.

## Anti-goals (it fails even if it works)

- **Stones get worse.** Releases, splits, colour flips, board agreement or
  impossible additions regress on the harness. The stone detector is what
  everything else stands on.
- **Training on partly reviewed frames.** A frame whose players are not all
  boxed teaches the model that players are background. It is worse than
  leaving the frame out.
- **A false hold.** The cover check puts back a stone that really left.
  Every put-back is checked against the video before shipping.
- **Rubber-stamped reviews flood the set.** Pose pre-boxes accepted
  untouched on frames where the pose model missed a crouched player put
  exactly the hard cases in as background.
- **Anything here slows or risks the workers' game processing.**

## Constraints from what exists

- **The overhead set.** overhead-3 is overhead-2's fold `all` (2,204
  frames) plus 45 empty top-panel logo crops, trained with
  `scripts/logo1/run_overhead3.sh`. Val is ds11's 364 frames
  (`datasets/overhead-3/README.md`). The folds live on the GPU laptop
  (`administrator@100.80.66.81:/data/wdd/curling/overhead-2/folds` and the
  logo crops). Labels have class 0 red and 1 yellow. Don't use the Ryzen's
  ds17 set: it has class-2 `stone_unknown` edits.
- **Naming.** Datasets and models are `overhead-N` (memory:
  overhead-long-naming). The next is overhead-4.
- **`/label`** (spec `2026-10-22-member-labelling-design.md`) has box items
  and release items in Firestore, a React page, admin publish routes, and
  `scripts/labels_export.py` on the laptop. Box items carry `starts`
  (pre-boxes) and a flagged box. Items are handed out newest first
  (`next_items`, 2026-10-30). Box answers are red/yellow only; the colour
  switch shows the selected box's colour.
- **Publishing** runs on the box that has the images. `label_fp.py publish`
  runs in a throwaway `curling-worker:local` container with `--cpus=2` and
  low `--cpu-shares`, and uploads straight to the bucket.
- **The pose model** is `yolo11x-pose.pt` (on the laptop in
  `/data/wdd/scratch/body/weights`, and on the Ryzen in
  `/data/wdd/scratch/occl`). Class 0 (person) only. The prototype ran it at
  imgsz 640 on the house panel crop.
- **The cover rule** is on branch `occlusion-hold` (185cad0e):
  `detect/cover.py` (`Cover`), `shots.from_deliveries(covered=...)`,
  `scripts/house_read_report.py --cover`, and tests in `tests/test_shots.py`
  (`TestAStoneUnderAPlayer`, `TestCover`). Its rule and thresholds are
  proven: a stone counts as covered when it went unseen in at least 2
  sampled frames, and a player box (padded by a stone radius) was over its
  spot in at least 80% of them.
- **Detections today are stones only.** `detect/yolo.py`
  (`stones_from_rows`, `boxes_to_stones`) turns rows into `rocks.Detection`
  in sheet metres, and every consumer of the house frames assumes each
  detection is a stone. The detection cache (`detections/*.npz`, keyed on
  the detector) and `sequence.detect_end` sit in between.
- **Model change mechanics** (memory: red1-overhead-frames, the overhead-3
  ship): `weights.DEFAULT_NAME`, `Dockerfile.worker` `ARG MODEL`,
  `.gitignore` exception, `cloudrun.yaml` `MODEL_ID`. The API's MODEL_ID
  must follow, or claims get 409. A model change moves `processing_version`,
  which requeues like a version bump, so ship in quiet hours with nothing
  awaiting playlist acceptance. The API deploy is the user's to run when the
  classifier refuses it.
- **No new VOD downloads** (memory: no-more-vod-downloads). New frames come
  from videos already in the worker caches. Don't evict the 11 harness
  videos.
- **The privacy page** says members are asked "to mark where the stones are
  or when a thrower let go". It must change in the same deploy that opens
  player items.
- **Training shares the Ryzen's GPUs with the workers.** The laptop is the
  training box for overhead models; keep it there.

## Design

### What a player box is

- The player's whole visible body: head, torso, arms, legs and feet as seen
  from above. **Not** the broom, and not a stone in their hand.
- A player cut off at the panel edge gets a box over the visible part, up
  to the edge.
- Any person in the panel counts, including players from the next sheet
  whose feet or shoulders show at the side, and a crouched or kneeling
  player.
- A lone shoe, glove or broom head left on the ice is not a player. A
  player's shoe at the edge with the rest of them out of view is: box the
  visible part.

These are the rules reviewers see in the how-to, with three examples: a
crouched skip seen from above, a player cut off at the panel edge, and a
broom shaft over a stone, which is not a player.

### Frames to label

1. **The existing set:** overhead-3's 2,249 training frames (fold `all`
   plus the 45 logo crops), receiving panel only: 1,595 of them.
2. **Its validation set:** ds11 val's 364 frames, receiving panel only (281),
   so player detection can be measured and not only stones.

   The sorting (`label_players.py ends` and `sort`; `datasets/overhead-4/`
   `ends.json` and `sides.json`): a frame's name, or for a member frame its
   export row, gives video, panel and time. Where a hosted timeline covers
   the video (110 of the 221 videos, 943 frames), the end it falls in says
   which panel was receiving; a frame before an end belongs to that end's
   run-up. Otherwise, a frame with 2 or more stones is kept and one with 0
   or 1 is left out. On the frames a timeline placed, 0-1 stones were 54
   receiving to 332 throwing, 2 were 41 to 17, and 3 or more 497 to 2. The
   45 logo crops have no time and are kept: they are empty frames, cheap to
   review, and keep overhead-4 off the logo. In all 1,876 of 2,613 are kept;
   about 50 of them are probably throwing frames (the 2-stone ones), and
   about 55 receiving frames with 0-1 stones are lost.
3. **A hard batch of about 300 new frames**, mined from cached hosted games
   and excluding the 11 harness videos and overhead-2's held-out videos
   (`datasets/overhead-2/heldout-videos.txt`). Weighted toward what COCO
   misses and what the cover check needs:
   - players over or beside a stone, especially where a stone goes unseen
     under them (the prototype's diagnostic finds these: a lost stone with
     the spot unseen and no pose box);
   - crouched and kneeling players, including the skip holding the broom
     behind the house;
   - players cut off at the panel edge;
   - a share of ordinary frames (players standing and sweeping), so the
     batch is not all hard cases.

   The miner reads only each end's receiving panel, inside the end, so the
   hard batch needs no sorting.

   Selection, mix and per-video caps are the builder's call. Spread over
   games, sheets and clubs as the red1 batch was (≤ 3 per kind per video).
   The hard batch's stone boxes need labels too, since these frames join
   the training set. They are pre-boxed by overhead-3 and reviewed as
   ordinary box items before or alongside their player review.

About 2,200 frames in all, one review each.

### Pre-boxes

Run `yolo11x-pose` on each frame at imgsz 640. Its person boxes at
confidence ≥ 0.25 become the starting player boxes (my call: lower floods
reviewers with junk, higher drops the partial players that are the point).
Pose boxes take in the broom when it is held out. The builder may trim boxes
to the keypoints plus a margin if that matches the box rule better; check
on a sample.

The existing labelled stone boxes come with each frame for display. They are
not pre-boxes and are not editable here.

### `/label`: a third item kind, "player"

- **Item:** one overhead panel image, its pre-drawn player boxes (`starts`),
  and its labelled stone boxes (`stones`, display only). It keeps where it
  came from: dataset and fold, file stem, video and time when known, and
  panel rect, so the export can write the labels back beside the image.
- **Flow:** a third flow on `/label`, linked from the top bar like the other
  two. The member sees the frame with player boxes editable and stone boxes
  greyed out (drawn, not selectable). They move, resize, delete or draw
  player boxes (drag on empty ice), then press Enter. `S` means can't tell.
  `N` skips. There is no colour switch in this flow.
- **One label per item.** An item is done after one answer. Can't-tell
  answers and items skipped 5 times go to the user's list.
- **Rubber-stamping.** Enter is allowed with every pre-box untouched; many
  frames really are just two standing sweepers. The answer records, per
  starting box, kept / moved / deleted, plus boxes drawn, as the box flow
  does. The export reports each member's rate of fully untouched answers,
  and the user spot-checks those members first (below).
- **How-to:** a blocking dialog on first visit, like the existing ones, with
  the box rules and the three examples above.
- **Order:** the hard batch first, then the existing set. Items are handed
  out newest first, so publish the hard batch last, or set its
  `created_at` later (builder's call).
- **Images** are uploaded from the box that has them. For the existing set
  and val, that's the laptop. For the hard batch, the Ryzen. Pre-boxing runs
  wherever the images are, off the workers' GPUs (laptop GPU, or the
  Ryzen's quiet hours).

### Fix stones (the user, 2026-11-02)

While boxing players a member can also fix the frame's stones, as a safety
net, not a second review. The player screen stays as it was (stones grey);
a **Fix stones (F)** button turns the rock-box tools on the stones (draw,
move, resize, delete, R/Y colour) and dims the players; F goes back. Enter
finishes the frame from either. A fix is the frame's whole stone set as the
member left it, and it **goes straight into training** in place of the
frame's stone label (the user's call); stones left as they were send
nothing and the label is kept exactly. The export writes each fix
(`players/labelled/stones/<id>.txt`) and a contact sheet of every fixed frame
(`players/stonefix/`, before in grey, after in colour) for a look; the merge
takes the fix over the base's stones and counts them. Hard-batch items are
published with their stones locked (`label_players.py --lock-stones`): their
stones are rock-box items of their own, so the page has no Fix stones there
and the server refuses a fix.

### Export and quality

- `labels_export.py --kind player` writes each answered item as a YOLO label
  file beside its image (class 2 = player) for merging into the overhead-4
  folds. It writes a `members.json` with per-member counts, median seconds
  and untouched rate.
- **Spot check (my call, light):** the export writes a contact sheet of a
  random 5% of answers (more from members with a high untouched rate) with
  the boxes drawn. The user looks through it and can drop a member's
  answers.
- **Frames left without a usable review (my call).** Two cases:
  - **A dropped member's answers are reopened** for other members. An
    admin route puts those items back in the pool with the answer discarded
    (kept in the record, not counted). It ships in the same API deploy as
    the player items, so a drop never needs a second deploy and doesn't land
    on the user.
  - **Can't-tell answers and items retired after 5 skips** go to the user's
    list and are settled offline with `train/boxedit.py`, which needs a
    third class to show and edit player boxes. A frame the user also can't
    settle leaves the set with its stone labels. The export reports how
    many stone frames left this way, so the stones-not-worse comparison
    knows what it is comparing.

### overhead-4

- **Set:** the overhead-3 set's receiving-panel frames with player boxes
  merged in (class 2), plus the hard batch (stones and players). Val: ds11
  val's receiving-panel frames with players.
- **Recipe:** overhead-3's (`scripts/logo1/run_overhead3.sh`), with three
  classes, on the laptop. Train a held-out-fold model too, for a fair
  comparison on overhead-2's held-out videos, as before.
- **Gate:** don't start until every frame in the set has its player review
  (anti-goal 2). Hard-batch frames whose stone boxes are not yet reviewed
  are left out, not trained half-labelled.
- **Class index:** player is class 2. Any consumer that reads classes must
  be updated in the same change (see next section).

### The pipeline reads player boxes

- **Two overhead models.** `build_profile` (games and ends) and each end's
  throwing-panel read keep overhead-3. Each end's receiving-panel read
  (`sequence.detect_end`, or the receiving span of `detect_spans` in the
  one-pass read) uses overhead-4. `detect_spans` takes a detector per span.
  Every panel frame still goes through one model, so inference per end is
  unchanged; the worker loads both. The detection cache already keys on the
  detector. The processing version includes both weights, and both model
  files ship.

- `detect/yolo.py` splits rows into stones (classes 0 and 1, as today) and
  players (class 2). Stones go on exactly as before. Players travel with the
  frame as panel-pixel boxes. The detection cache must carry them too, and
  its key changes with the model anyway.
- **No stone consumer may see a player** (`rest`, `delivery`, `segment`,
  `release`, `fpframes`, `placement`, scoreboard, live paths). The builder
  picks the shape: a second list per frame, or a frame object with
  `stones` and `players`. Whatever it is, the existing `(t, detections)`
  stone lists must stay what they are for every consumer that doesn't ask
  for players.
- **The cover check:** take `Cover` from `occlusion-hold`, keep its rule and
  thresholds, and replace its decoding and pose model with the player boxes
  of the house frames already passed to `from_deliveries`. The sample rate
  becomes the house frames' own (10 fps). Keep `COVERED_SHARE` 0.8 and
  `MIN_UNSEEN` as a count of frames (re-check the value at 10 fps). Port its
  tests and add one with player boxes in the frames.
- **Every rock, and the live path.** The check runs for every rock, before
  the reach veto, as in the prototype, in both VOD and live analysis. It's
  on by default once overhead-4 is the model. With a model that has no
  player class it does nothing.

### Acceptance (before shipping)

On the 16-game harness (laptop `/data/wdd/curling/houseread-1027` kit, and
`ab_rel_ff.py` for releases; memory: validating-release-changes), current
main + overhead-3 against overhead-4 + the cover check:

- **House read:** every one of the prototype's 15 stones is recovered, and
  no fewer stones are recovered in all. Scoreboard agreement ≥ 42 of 54 with
  none worse, impossible additions ≤ 10. "Recovered" (my call) means a stone
  the baseline's house read lost and this one keeps, **whether overhead-4
  now sees it or the cover check puts it back**. A better model that simply
  sees a stone under a player is a win, not a shortfall in put-backs. The
  harness report tags each recovered stone: seen by the model, put back by
  the cover check, or still lost. **Every stone the cover check puts back
  is checked on video**, with `occl_look.py`-style strips. Zero false holds.
- **f_0zkA:** rocks 15 and 16 of 6vOTUuUSMKc g2 e7 lose their phantom
  removals (replay on the Ryzen as on 2026-11-01). The gate is the pipeline
  as it will ship, reach veto on. Also report it with the veto off, to show
  whether the cover check fixes rock 16 by itself (my call: reported, not
  gated).
- **Stones:** releases, splits and colour flips not worse than overhead-3
  beyond seed noise. Releases and the throwing end's split are still read
  by overhead-3, so a change there is a bug in the wiring, not the model.
  Stone mAP on ds11 val's receiving frames and on the held-out videos'
  receiving frames not worse.
- **Players:** recall and precision on the reviewed val, reported, with the
  pose model's numbers beside them for comparison. No bar of its own; the
  house-read bar above is the one that counts.
- **Runtime:** detection time per end not measurably worse (the panel input
  size is unchanged, and each frame still sees one model). Worker memory
  with both models loaded reported.

If overhead-4 misses the bar, report and stop. Don't ship partial wins
without asking.

### Privacy page

In the same deploy that opens player items, change the member-labelling
paragraph to say members may be asked to mark where the stones **and
players** are. Keep "Nothing on the site identifies anyone from their face"
true: a player box carries no identity, and no player box is stored with a
name.

## Order of work

The user, 2026-11-01: extraction, labelling and training come first, so the
model's convergence is known before the pipeline changes. `/label` is the
one change that must come before them, because the labelling happens there.

1. `/label` player items: API, page, how-to, publish and export, privacy
   page. The API deploy is the user's.
2. Publish: the existing set and val (from the laptop), then the hard batch
   (mined on the Ryzen), pose pre-boxed.
3. Members review. This is the long pole; the user hands out the link.
4. Export, spot check, merge, train overhead-4 on the laptop.
5. Model check: training curves, stone AP against overhead-3, player
   precision and recall against the pose model, and the hard frames the pose
   model missed. Report, and wait for the user's go.
6. Pipeline: overhead-4 on the receiving panel and overhead-3 elsewhere,
   class splitting in `yolo.py`, player boxes through the cache, and the
   cover check reading them, with tests.
7. Harness acceptance, then ship (model files, MODEL_ID, worker + API
   deploy in quiet hours) on the user's go.

## Left to the builder

- Mining rules and mix for the hard batch, within the weighting above.
- Whether to trim pose boxes to the keypoints for pre-boxes.
- Firestore layout for the new kind, reusing the box item's structure where
  it fits.
- The data shape that carries player boxes past the stone consumers.
- `MIN_UNSEEN` at 10 fps.
- `boxedit.py`'s third class.
- Tests: the class split (stones unchanged for a two-class model), the
  cover check on frame boxes, the player item routes (one answer completes
  an item, can't tell, skip), and the export format. Run the affected
  subsets only (memory: the full suite OOMs here); the Firestore half of the
  repo contract test needs the emulator.
