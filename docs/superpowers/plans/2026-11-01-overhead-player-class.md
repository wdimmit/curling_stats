# Player Class in the Overhead Detector: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The overhead detector learns a third class, player (overhead-4), from pose pre-boxes that members review on `/label`. The house read then puts back a stone lost under a player, using the detector's own player boxes.

**Order (the user, 2026-11-01):** extraction, labelling and training come first, and the model is checked for convergence, first on a pilot (Task 7) and then in full (Task 9), before any pipeline change. `/label` is the one change that has to come before them, because the labelling happens there.

**Architecture:** Each house frame's detections stay a list of stone `Detection`s. It becomes a `Seen` (a `list` subclass) that also carries `.players`, so every stone consumer is untouched and the cover check can read the boxes. `/label` gets a third item kind, `player`, which needs one answer per item and has an admin route to reopen a dropped member's answers. Labels are exported as YOLO class 2 and merged into overhead-3's set, and overhead-4 is trained on the laptop with overhead-3's recipe.

**Tech Stack:** Python 3 (ultralytics YOLO, numpy, FastAPI, Firestore), React 19 via esbuild (`frontend/`), node for the JS tests.

**Spec:** `docs/superpowers/specs/2026-11-01-overhead-player-class-design.md` (approved 2026-11-01). Read it first. This plan argues from it.

## Global Constraints

- Player is **class 2**. Red is 0 and yellow is 1, unchanged.
- The cover rule as proven: covered when unseen in ≥ `MIN_UNSEEN` frames and a player box, padded by `C.STONE_RADIUS_M`, is over the spot in ≥ `COVERED_SHARE = 0.8` of them. The cover check uses the house frames' own rate (10 fps). Start at `MIN_UNSEEN = 5`, which is 0.5 s and the same span as the prototype's 2 samples at 2 fps, and re-check it in Task 14.
- Pose pre-boxes: `yolo11x-pose.pt`, class 0, imgsz 640, confidence ≥ **0.1** (ruled 2026-11-01 from a 74-player count: 0.25 found 42, 0.1 found 54, junk 0.07 a frame). CPU, 2 threads.
- Labels needed per item: box 2, release 2, **player 1**. Retire after 5 skips (`RETIRE_AFTER_SKIPS`).
- No new VOD downloads. New frames come from the worker caches. Don't evict the 11 harness videos. Exclude them and `datasets/overhead-2/heldout-videos.txt` from the hard batch.
- Nothing may slow or risk the workers. Scripts on the Ryzen run in a throwaway `curling-worker:local` container with `--cpus=2` and low `--cpu-shares`, with pose on CPU. Training runs on the laptop.
- Two branches, each in a worktree off main:
  - `player-labels` holds Phases 1–3: `/label`, publishing, export, and the merge and training scripts. It merges to main for the API deploy.
  - `player-class` holds the pipeline (Phase 4) and starts only after the user's go on the model (Task 9). It reaches main only in Task 15, so no worker built from main picks it up early.

  The checkout is shared, so commit only your own files. Don't push. The user runs API deploys when the classifier refuses them. Quote times in Seattle time.
- The admin token is never printed. Read it from the gcloud secret into a variable (as `scripts/users.py` documents).
- Run the affected test files only, because the full suite OOMs on this box. Before running `tests/test_repo_contract.py`, start the Firestore emulator (memory: firestore-emulator).
- Any edit to `detect/yolo.py` or `detect/rocks.py` changes the detection cache key (`cache._SOURCE_MODULES`), and so does the model change. **Don't deploy Tasks 10–12 to the workers before overhead-4 ships.** They ship together in Task 15, so the cache turns over once.
- The privacy page line changes in the same deploy that opens player items.

## Review Focus

1. **A pose box that overshoots the image.** The server refuses an answer box more than 1 px outside the image, so a member who presses Enter on an untouched start like that would be stuck. Publishing clips every start to the image (test in Task 3).
2. **A frame with no players.** With no starts and nothing drawn, Enter is a valid answer meaning "no players" and must be accepted. The export must write an empty label file, not skip the frame (tests in Tasks 1, 2, 5).
3. **A dropped member posting again to a reopened item.** This must be refused (`closed`) and must not replace or revive the discarded answer (test in Task 1).
4. **Answer documents written before this change** have no `discarded` field. They must load as not discarded, in both the memory repo and Firestore (contract test in Task 1).
5. **The harness or replay reading frames that lost their players.** If `rocks.separated` or a list comprehension rebuilds the frames, players are dropped and the cover check silently does nothing. House frames must reach `from_deliveries` as `Seen`, and the harness prints how many frames carried players (test and output in Task 13).

---

## Phase 1: `/label` player items (one API deploy, run by the user)

### Task 1: Server: the player kind, one label, and reopening a dropped member's answers

**Files:**
- Modify: `src/curling_score/service/records.py` (`LABEL_KINDS += ("player",)`, `LabelAnswer.discarded: bool = False`)
- Modify: `src/curling_score/service/labelling.py`
- Modify: `src/curling_score/service/repo.py` (protocol + `MemoryRepo`) and `src/curling_score/service/firestore_repo.py`
- Modify: `src/curling_score/service/api.py`
- Test: `tests/test_labelling.py`, `tests/test_labelling_api.py`, `tests/test_repo_contract.py`

**Interfaces:**
- Produces:
  - `labelling.LABELS_NEEDED = {"box": 2, "release": 2, "player": 1}`. `MAX_LABELS` goes, and `label_submit` passes `LABELS_NEEDED[kind]`.
  - The player payload, as Task 3 writes it: `{"w", "h", "starts": [{"x0","y0","x1","y1","conf"}], "stones": [{"x0","y0","x1","y1","color"}], "origin": {...}}`. `for_member` already drops `origin`.
  - `_check_player_answer(item, answer) -> dict`. It follows the box checks (edges, minimum size, `MAX_BOXES`, one status per start, a start boxed at most once, deleted starts unboxed, other starts boxed), but a box has no `color`, and there is no flagged-start rule. It returns `{"cant_tell": False, "boxes": [{"x0","y0","x1","y1","start"}], "starts": [...]}` or the can't-tell form. Zero starts with zero boxes is valid.
  - `repo.discard_label_answers(uid: str, kind: str, now: datetime) -> list[str]`. For each of that member's not-yet-discarded answers of `kind`, set `discarded=True` and on its item decrement `n_labels`. If the item was `done`, set it back to `open` and `done_at=None`. Leave `uid` in `labelled_by` so the item is not offered to them again. Returns the reopened item ids. Firestore does it per item in a transaction, as `submit_label` does.
  - `submit_label`: when the member's existing answer is discarded, return `"closed"`.
  - `POST /api/admin/label-answers/discard` with body `{"uid", "kind"}` (admin) returns `{"reopened": [ids]}`.
  - The admin listing error text says "kind is box, release or player".
- [ ] **Step 1: Write the failing tests:**
  - `tests/test_labelling.py`: a player answer with a coloured box is refused, an untouched start is accepted, empty with no starts is accepted, can't-tell is accepted, a box off the image is refused.
  - `tests/test_labelling_api.py`:
    - one player answer makes the item `done`;
    - can't-tell makes it `done`;
    - 5 skips make it `retired`;
    - discard reopens the item, a second member is then handed it, and the dropped member's re-post gets 409 `closed`;
    - box items still need 2 answers.
  - `tests/test_repo_contract.py`: `discard_label_answers` on both repos, plus an answer document written without `discarded` loading as `False`.
- [ ] **Step 2: Run** the three files. Start the emulator first for the contract test. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the three files plus `tests/test_labelagree.py tests/test_labels_status_script.py`. Expected: PASS.
- [ ] **Step 5: Commit** with the message "label: player items (one answer) and reopening a dropped member's answers".

### Task 2: The page: a player flow, a how-to with examples, and the privacy line

**Files:**
- Modify: `frontend/core/labelling.mjs`, `frontend/site/Label.jsx`
- Create: `src/curling_score/service/static/label-player-{crouched,edge,broom}.jpg` (each ≤ 480 px wide)
- Modify: `src/curling_score/service/static/privacy.html:63`
- Built output: `npm run build` in `frontend/`, committed as the README says
- Test: `tests/test_labelling_js.py`

**Interfaces:**
- Consumes: the payload and answer forms from Task 1.
- Produces:
  - `labelling.mjs`: `startPlayers(item)` (starts become boxes with no colour), `playerAnswer(state) -> {cant_tell:false, boxes:[{x0,y0,x1,y1,start}], starts:[...]}` (no `color`) and `untouched(state) -> bool`. `canFinish` stays as it is, and is always true without a flagged start.
  - `FLOWS.players = {kind: "player", name: "Player boxes", Flow: PlayerFlow, howto, figures}`. The top bar links to both other flows. `PlayerFlow` is `BoxFlow` without the colour switch, the R/Y/K keys and the colour preference. Stones from `item.stones` are drawn greyed and are not selectable or hit-testable, so a drag that starts on a stone draws a player box. Enter, S (can't tell) and N (skip) work as in the box flow.
  - `HowtoDialog` takes optional `figures: [{src, caption}]`.
- Exact copy:
  - How-to: "Box every person you can see: head, body, arms, legs and feet as seen from above. Not the broom, and not a stone in their hand. A player cut off at the edge gets a box over the part you can see, and so does anyone from the next sheet. A crouched or kneeling player is still a player. A shoe, glove or broom head left on the ice is not. The grey boxes are stones and can't be changed. If there are no people, press Enter."
  - Figure captions: "A crouched skip, from above: box all of them." / "Cut off at the edge: box what shows." / "A broom reaching over a stone: box the player, not the broom." (ruled: the chosen frame shows a player whose broom spans the house). The figures have the intended boxes drawn on them.
  - Privacy, replacing "asked to mark where the stones are or when a thrower let go" with "asked to mark where the stones and players are or when a thrower let go". The next sentence stays. Add after it: "A box around a player is only an outline: nothing about who they are is stored with it."
- [ ] **Step 1: Write the failing JS tests** in `tests/test_labelling_js.py`:
  - `playerAnswer(startPlayers(ITEM_P))` keeps every start, with no `color` key;
  - after `removeBox` the start reads `deleted`;
  - `untouched` is true only before any change;
  - an item with no starts gives `{cant_tell:false, boxes:[], starts:[]}`.
- [ ] **Step 2: Run** `tests/test_labelling_js.py`. Expected: FAIL.
- [ ] **Step 3: Implement** the helpers and the flow. Cut the three example crops from 6vOTUuUSMKc g2 e7 (rock 15's crouched skip) and from harness frames, using the Ryzen's `/data/wdd/scratch/occl` frames. Then edit the privacy page and run `npm run build`.
- [ ] **Step 4: Run** `tests/test_labelling_js.py tests/test_frontend_build.py tests/test_labelling_api.py`. Expected: PASS. Then check the page in a signed-in browser at 1280×800 (memory: signed-in-browser-check, devserve with a `MemoryRepo` holding one player item with starts and stones). Confirm the how-to shows its three figures, stones are grey and can't be selected, and drawing, deleting and Enter post the expected answer. Save before/after screenshots to the scratchpad.
- [ ] **Step 5: Commit** the sources, the built output, the images and `privacy.html` with the message "label: player boxes flow; privacy page says players".
- [ ] **Step 6: Hand the API deploy to the user.** Merge `player-labels` (Tasks 1–2) into main, which is not pushed. `player-class` stays off main. Build from a clean worktree at that commit (memory: concurrent-session-commits and deploying-curling-chart). Then check `/label#players` live, with no items open yet.

---

## Phase 2: Frames and publishing

### Task 3: `scripts/label_players.py`: pose pre-boxes and publishing player items

**Files:**
- Create: `scripts/label_players.py`
- Test: `tests/test_label_players_script.py`

**Interfaces:**
- Consumes: `label_publish.publish`, `label_publish.opaque_id`, `label_publish.preflight`.
- Produces:
  - `pose_starts(img, model) -> list[dict]`: `yolo11x-pose` class-0 boxes at conf ≥ 0.1, imgsz 640, `device="cpu"`, each `{"x0","y0","x1","y1","conf"}`, **clipped to the image** and dropped if under `MIN_BOX_PX` once clipped. Whether to trim boxes to the keypoints plus a margin is the builder's call. Make it on 30 frames rendered both ways and write the decision in the docstring.
  - `stones_from_label(txt: str, w: int, h: int) -> list[dict]`: YOLO class 0/1 lines become `{"x0","y0","x1","y1","color"}` in pixels. Other classes are ignored.
  - `item_for(dataset: str, fold: str, stem: str, w: int, h: int, starts, stones, batch: str, extra_origin: dict) -> dict`, with id `opaque_id("p_", f"{dataset}/{fold}/{stem}")` and kind `"player"`. Origin holds `dataset, fold, stem`, plus `video, t, rect` when known.
  - CLI `publish --images DIR --labels DIR --dataset NAME --fold NAME --batch NAME [--dry-run] [--out DIR]`. `--out` writes the items and a contact sheet of starts over 40 random frames, for a look before publishing. It reads `ADMIN_TOKEN` and `BASE_URL` as `label_fp.py` does.
- [ ] **Step 1: Write the failing tests:**
  - `item_for` shape and stable opaque ids (the same key gives the same id, and the id contains no stem text);
  - `stones_from_label` maps a known line to its pixels;
  - `pose_starts` with a stub model clips a box running off the image and drops one under 3 px;
  - starts under conf 0.1 are dropped.
- [ ] **Step 2: Run** the test file. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** it. Expected: PASS.
- [ ] **Step 5: Commit** with the message "label_players: pose pre-boxes and player items".

### Task 4: Mine the hard batch on the Ryzen; publish everything, the hard batch last

**Files:**
- Create: `scripts/player_mine.py`
- Test: `tests/test_player_mine_script.py` (selection only)

**Interfaces:**
- Consumes: `label_players.pose_starts`, `sequence.detect_span` with overhead-3, `house_read_report.replay`'s window logic, and `label_fp.py publish` for the stone box items.
- Produces:
  - `pick(cands: list[dict], exclude: set[str], per_kind_per_video: int = 3, total: int = 300, seed: int = 0) -> list[dict]`. `exclude` holds video ids. Each candidate is `{"video", "t", "kind"}`, with kind in `lost_no_pose`, `pose_unsure`, `edge` or `ordinary`. It caps each (video, kind) at 3, spreads over videos, sheets and clubs, and fills `ordinary` to about 20% of the total.
  - Output in `/data/wdd/scratch/players/hard/`: `images/<stem>.jpg` (house panel crop), `labels/<stem>.txt` (overhead-3 stone boxes), `picks.jsonl`.
  - Mining rules are the builder's call, within spec §"Frames to label". The starting point is the prototype's diagnostic: a stone the read lost with its spot unseen and no pose box (`lost_no_pose`). A person pose finds only below 0.25 (no ≥ 0.25 box over it) makes the frame `pose_unsure` (ruled 2026-11-01: a size/aspect "crouched" rule picked standing sweepers in the smoke run). Boxes touching the panel edge count as edge.
- [ ] **Step 1: Write the failing tests:** `pick` honours the per-video cap, the total and the ordinary share, never picks an excluded video, and is stable for a seed.
- [ ] **Step 2: Run** the test file. Expected: FAIL. **Step 3: Implement.** **Step 4: Run** it. Expected: PASS. **Step 5: Commit.**
- [ ] **Step 6: Mine** in the throwaway container (`--cpus=2`, low `--cpu-shares`, pose on CPU) from videos already cached on the Ryzen. Exclude the 11 harness videos and `datasets/overhead-2/heldout-videos.txt`. Report the counts by kind and video.
- [ ] **Step 7: Publish the existing set first, then val.** On the laptop, run `label_players.py publish --dry-run --out` for overhead-3's `folds/all` **train only** (`images/train`, `labels/train`: 2,204 frames plus the 45 logo crops; its `images/val` is ds11 val by symlink and must not be published twice), look at the contact sheet, and publish. Then do the same for ds11 val (364 frames). Val is newer than the training set, so members reach it sooner, which gives the pilot (Task 7) a val to measure on. Report the counts.
- [ ] **Step 8: Publish the hard batch last**, so newest-first hands it out first. On the Ryzen, publish its stone boxes as `box` items with `label_fp.py`, then its player items with `label_players.py`. Report the counts and the `/label` link for the user to hand out.

---

## Phase 3: Reviews, export and training (waits on the members; ends at the user's go)

### Task 5: Export `--kind player`: labels, the member report, the spot check and the user's list

**Files:**
- Modify: `scripts/labels_export.py`, `scripts/labels_status.py`
- Modify: `src/curling_score/train/boxedit.py` (`kind="player"`)
- Test: `tests/test_labels_export_script.py`, `tests/test_boxedit.py` (create one if none exists)

**Interfaces:**
- Consumes: `/api/admin/label-items?kind=player` (answers carry `discarded`).
- Produces:
  - `boxedit.render(..., kind="player")`: the stone page plus a `+player (H)` button arming class 2. A class-2 box is drawn in blue (`--ply:#2a6fd0`), and a class-2 box is placed by drag, not by click-sizing. P stays "previous frame". The edits follow `_BROOM_EDITS`' pattern, each matched exactly once (tested).
  - `export_players(items, out, opener, seed=0) -> tuple[int, int, list[str]]`, returning (written, to the user, skipped). It ignores discarded answers.
    - **Written:** a `done` item whose one live answer is not can't-tell goes to `players/labelled/{images,labels}/<id>.{jpg,txt}`. The label holds class-2 lines only, and is empty when there are no players. A line goes to `players/labelled/players.jsonl` with the origin, uid and seconds.
    - **To the user:** a can't-tell answer or a `retired` item goes to `players/to_user/`, as a `boxedit.render(kind="player", proposals=True)` page. Its boxes are the payload's stones (classes 0/1) plus its starts (class 2).
    - **Spot check:** `players/spotcheck/sheet-NN.jpg` are 4×4 PIL grids of `ceil(0.05 × answers)` answers. Each answer is drawn with probability ∝ `1 + 3 × its member's untouched rate`, using a fixed seed. Each tile shows the boxes, the member's email and the item id.
  - `member_report` adds `player` counts, and per member `untouched_rate`: the share of player answers where every start is `untouched` and no box was drawn. The rate is counted over non-can't-tell answers.
  - `--kind` accepts `player`, and the default stays box and release.
  - `labels_status.py` counts player items.
- [ ] **Step 1: Write the failing tests:**
  - boxedit's player edits each apply once, and the page has the button;
  - a done item gives an image and a class-2 label;
  - a no-players answer gives an empty label file;
  - can't-tell and retired items go to `to_user` with stones as class 0/1;
  - a discarded answer is ignored, and its item is not written;
  - the untouched rate is 1.0 for an all-untouched member;
  - the spot-check count is `ceil(5%)`.
- [ ] **Step 2: Run** the test file. Expected: FAIL. **Step 3: Implement.** **Step 4: Run** it plus `tests/test_labels_status_script.py`. Expected: PASS. **Step 5: Commit.**

### Task 6: Merge, the training script, and the player evaluation

**Files:**
- Create: `scripts/overhead4_merge.py`, `scripts/logo1/run_overhead4.sh`, `scripts/player_eval.py`
- Test: `tests/test_overhead4_merge_script.py`, `tests/test_player_eval_script.py`

**Interfaces:**
- Consumes: Task 5's `players/labelled/` and `players/to_user/`.
- Produces:
  - `merge(base_images: Path, base_labels: Path, players: Path, out: Path, leave_out: set[str], pilot: bool = False) -> dict`.
    - For each base frame, it writes the stone lines, minus any class-2 lines, plus the player label's lines.
    - Without `pilot`, it **raises** if a base frame has no player label and is not in `leave_out`.
    - With `pilot`, frames with no player label are dropped and counted instead. It also refuses an `out` whose path contains `overhead-4`, so a pilot set can never become the real one.
    - Frames in `leave_out` are dropped and counted.
    - It returns counts and writes `data.yaml` with `names: {0: red, 1: yellow, 2: player}`.
  - CLI: `overhead4_merge.py --base DIR --players DIR [--hard-stones DIR] [--leave-out FILE] [--pilot] --out DIR`. Hard-batch frames come in only when they have both reviewed stone boxes and a player label. The stone boxes are either agreed (the box export's `agreed/`) or settled by the owner in `boxedit` from the box export's `disputed/` (`--hard-settled EDITS.json`; ruled 2026-11-01: two untouched answers are a rubber stamp and go to the owner, as in the ordinary box flow).
  - `run_overhead4.sh DATA_YAML NAME`: `run_overhead3.sh`'s recipe, with the data and run name as arguments. nc comes from `data.yaml`.
  - `player_eval.match(pred: list[tuple], truth: list[tuple], iou: float = 0.5) -> tuple[int, int, int]`: greedy IoU matching, highest confidence first. Boxes are `(x0, y0, x1, y1[, conf])`. Returns (tp, fp, fn).
  - CLI: `player_eval.py --val DIR --model PT [--pose PT] [--conf 0.25] [--curves RESULTS_CSV ...] [--sheet FRAMES_DIR]`.
    - It prints player precision and recall for class 2 and, given `--pose`, for pose class 0 at 0.25 and at 0.1.
    - `--curves` writes `curves.png`: train and val box, cls and dfl loss, and mAP50-95 by epoch, one line per run given.
    - `--sheet` writes a contact sheet of the model's player boxes at conf 0.25.
- [ ] **Step 1: Write the failing tests:**
  - `merge` raises on a frame with no player label;
  - with `pilot`, the same frame is dropped and counted;
  - `pilot` refuses an `overhead-4` path;
  - `merge` drops and counts a frame in `leave_out`;
  - a merged label is the stone lines plus the class-2 lines;
  - a hard frame with no agreed stones is left out;
  - `match`: a perfect match, a duplicate prediction counted as fp, a miss counted as fn, and IoU 0.49 not matching.
- [ ] **Step 2: Run** the test files. Expected: FAIL. **Step 3: Implement.** **Step 4: Run** them. Expected: PASS. **Step 5: Commit** on `player-labels`.

### Task 7: Pilot: does the player class learn? (report; reviews go on meanwhile)

Not overhead-4, and never shipped. Its only use is an early read on convergence. Every frame in it is fully reviewed, and unreviewed frames are left out whole.

- [ ] **Step 1: Wait for enough reviews.** Start once the hard batch is fully reviewed, plus ≥ 300 frames of the existing set and ≥ 150 of ds11 val. Check with `labels_status.py`, and give the user a progress line when asked.
- [ ] **Step 2: Export and merge.** Run `labels_export.py --kind player`, then `overhead4_merge.py --pilot --out /data/wdd/curling/pilot-players/folds/all` with the hard batch. Merge the reviewed val frames into the pilot's val in the same way.
- [ ] **Step 3: Train** with `run_overhead4.sh <pilot data.yaml> pilot-players` on the laptop. The recipe is unchanged.
- [ ] **Step 4: Evaluate.**
  - `player_eval.py` against the pilot val, with `--pose`.
  - `--curves`, with the pilot's and overhead-3's `results.csv`.
  - `--sheet` on the hard frames the pose model missed: rock 15 of 6vOTUuUSMKc g2 e7 (the crouched skip), AEqL e4 2793–2805 and entT g1 e3 9488+. No training set holds these frames.
  - Stone AP on the pilot val, beside overhead-3's. It is not comparable, because the pilot trains on far fewer stone frames. Report it, but don't judge on it.
- [ ] **Step 5: Report** the curves, the player precision and recall against the pose model, and the contact sheet. Say whether the player loss plateaued cleanly. This is not a gate: reviews continue either way. If it looks wrong, the user decides whether to change anything before the full run.

### Task 8: Settle the user's list; train overhead-4

**Files:**
- Create: `datasets/overhead-4/README.md`

- [ ] **Step 1: Settle the user's list.** Hand the user `players/to_user/index.html`, served over HTTP (memory: ds11-review-workflow), and the spot-check sheets. Discard any member they drop with the admin route. Re-export after the reopened items are answered. Commit the user's edits when they arrive. Frames the user also can't settle go in `leave_out`. Report how many stone frames left.
- [ ] **Step 2: Merge and train on the laptop.** Merge without `--pilot` into `/data/wdd/curling/overhead-4/folds/all`, and merge ds11 val with players into its val. Make the held-out fold the way overhead-2's was. Train `all` and the held-out fold with `run_overhead4.sh`.
- [ ] **Step 3:** Write `datasets/overhead-4/README.md` with the counts, the left-out frames and the recipe. Commit it.

### Task 9: Model check: does overhead-4 converge? (report; stop for the user's go)

- [ ] **Step 1: Check convergence** with `player_eval.py --curves` for `all`, the held-out fold, the pilot and overhead-3. Converged means:
  - val losses plateaued without climbing;
  - the best epoch came before the last (patience ended it, or it flattened);
  - the `all` and held-out-fold runs behaved alike.
- [ ] **Step 2: Check the numbers.**
  - Stone AP per class (red, yellow) on ds11 val, overhead-4 against overhead-3.
  - Stone mAP of the held-out-fold model on overhead-2's held-out videos, against overhead-3's held-out-fold model.
  - Player precision and recall on the reviewed val, against yolo11x-pose and the pilot.
- [ ] **Step 3: Look at the hard cases:** `--sheet` on the same frames as the pilot.
- [ ] **Step 4: Report** to the user with the curves, the numbers and the contact sheet. Pipeline work (Phase 4) starts only on their go. If it hasn't converged, or stones are worse, say so with the evidence and the options. Don't pick one.

---

## Phase 4: Pipeline (branch `player-class`; after the user's go on the model)

### Task 10: Frames carry player boxes; the class split in `yolo.py`

**Files:**
- Modify: `src/curling_score/detect/rocks.py` (add `PlayerBox`, `Seen` beside `Detection`)
- Modify: `src/curling_score/detect/yolo.py`
- Test: `tests/test_yolo.py`

**Interfaces:**
- Produces:
  - `rocks.PlayerBox`, a frozen dataclass: `x0_px, y0_px, x1_px, y1_px, x0_m, y0_m, x1_m, y1_m, confidence` (all `float`). The pixel box is in panel pixels. The metre box is the pixel corners through `calib.to_sheet`, normalised so that `x0_m <= x1_m` and `y0_m <= y1_m`, because a flipped calibration swaps them.
  - `rocks.Seen(list)`: a frame's stones, with `players: tuple[PlayerBox, ...]` (default `()`). `Seen(stones, players=())`. It must pickle with its players intact.
  - `yolo.PLAYER_CLASS = 2`
  - `yolo.frame_from_rows(rows, calib, conf=None) -> rocks.Seen`: rows of class 0/1 go through `stones_from_rows` unchanged (sideline cut and `enforce_separation` included). Class-2 rows become `players`, with no sideline cut, because a next-sheet player still covers what they cover.
  - `YoloDetector.find_stones`, `find_stones_batch` and `find_stones_each` return `Seen` built by `frame_from_rows`.

- [ ] **Step 1: Write the failing tests** in `tests/test_yolo.py`:
  - `test_two_class_rows_give_the_same_stones`: for rows with only classes 0/1, `frame_from_rows(rows, calib) == stones_from_rows(rows, calib)` and `.players == ()`.
  - `test_player_rows_are_not_stones`: rows `[(150,150,40,40,0.9,0), (100,100,60,80,0.7,2)]` give 1 stone, and `players[0]` has `x0_px == 70, y0_px == 60, x1_px == 130, y1_px == 140, confidence == 0.7`, with metres equal to `calib.to_sheet` of those corners.
  - `test_flipped_calib_player_box_is_ordered`: with `flipped=True`, `x0_m <= x1_m and y0_m <= y1_m`.
  - `test_seen_pickles_with_players`: `pickle.loads(pickle.dumps(seen)).players == seen.players`.
- [ ] **Step 2: Run** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_yolo.py -q`. Expected: FAIL (`frame_from_rows` and `Seen` are not defined).
- [ ] **Step 3: Implement** `PlayerBox` and `Seen` in `rocks.py`, then `PLAYER_CLASS`, `frame_from_rows`, and the three `find_stones*` methods in `yolo.py`. `stones_from_rows` keeps its contract for rows of class 0/1 only.
- [ ] **Step 4: Run** the same command. Expected: PASS. Also run `tests/test_detcache.py tests/test_cache.py`: PASS.
- [ ] **Step 5: Commit** `rocks.py`, `yolo.py` and `tests/test_yolo.py` with the message "detect: frames carry the detector's player boxes (class 2)".

### Task 11: Players survive the sideline cut, the cache and the live decode

**Files:**
- Modify: `src/curling_score/detect/sequence.py` (`_on_sheet`)
- Modify: `src/curling_score/detect/cache.py` (`FORMAT_VERSION = 2`, save/load players)
- Test: `tests/test_detcache.py`

**Interfaces:**
- Consumes: `rocks.Seen`, `rocks.PlayerBox` (Task 10).
- Produces: `detect_span`, `detect_end` and `detect_spans` return frames whose detections are `Seen` with players when the detector gives them, both cached and uncached. `_on_sheet` returns `Seen(on_sheet(stones), getattr(stones, "players", ()))`. `cache.load(digest)` returns `Seen` per frame.

- [ ] **Step 1: Write the failing tests:**
  - `test_cache_round_trips_players`: `save` and then `load` of a sequence whose frames are `Seen` with 0, 1 and 2 players gives back equal stones and equal `players`, as float64.
  - `test_on_sheet_keeps_players`: a setup whose `on_sheet` drops one stone keeps the frame's players.
  - `test_detect_spans_keeps_players`: with a stub detector whose `find_stones_batch` returns `Seen(..., players=(box,))`, and `F.windows` monkeypatched to two tiny frames, every frame `detect_spans` returns has that box. This is the live path.
- [ ] **Step 2: Run** `tests/test_detcache.py`. Expected: FAIL.
- [ ] **Step 3: Implement.** In the cache, add arrays `pcounts` (int32) and one float64 array per `PlayerBox` field, named `p_<field>`. Bump `FORMAT_VERSION` to 2.
- [ ] **Step 4: Run** `tests/test_detcache.py tests/test_cache.py tests/test_yolo.py`. Expected: PASS.
- [ ] **Step 5: Commit** with the message "detect: player boxes through the sideline cut, the cache and the live decode".

### Task 12: The cover check reads the house frames' player boxes

**Files:**
- Create: `src/curling_score/detect/cover.py`. Port it from `git show 185cad0e:src/curling_score/detect/cover.py`, with the decoding and pose model removed.
- Modify: `src/curling_score/game/shots.py` (`from_deliveries`)
- Test: `tests/test_shots.py`. Port `TestAStoneUnderAPlayer` and `TestCover` from 185cad0e and rewrite them over frames.

**Interfaces:**
- Consumes: `Seen.players` (Task 10), with house frames from `from_deliveries(house_frames=...)`.
- Produces:
  - `cover.covered(x_m: float, y_m: float, color: str, window) -> bool`. `window` is the read's `(t, detections)` frames. A frame where a detection of `color` lies within `SEEN_M = 0.15` m of the spot says nothing. In every other frame, count it unseen, and count it covered if any `players` box grown by `C.STONE_RADIUS_M` contains `(x_m, y_m)`. Return `unseen >= MIN_UNSEEN and covered >= COVERED_SHARE * unseen`. Frames with no `players` attribute have none.
  - `shots.from_deliveries(..., held: list | None = None)`. The check always runs for every rock, before the reach veto, at the prototype's spot: each `house_delta(...)["removed"]` stone that `covered` holds is passed to `_put_back`. When `held` is given, append `{"shot": <shot number>, "color", "x", "y"}` for each put-back. There is no `covered=` parameter. A two-class model's frames have no players, so the check does nothing.

- [ ] **Step 1: Write the failing tests:**
  - `TestAStoneUnderAPlayer` (the prototype's 6vOT rock-15 end). Its frames are `Seen`, with a player box over (-1.65, -1.52) in every frame of rock 1's window where the back red is missing. Expected: `first.house_delta["removed"] == []`, `first.stones` keeps the red, `second.house_delta["added"] == [{"color": "red", "x": 0.0, "y": 0.0}]`, and `held == [{"shot": 1, "color": "red", "x": -1.65, "y": -1.52}]`.
  - `test_a_stone_seen_gone_stays_removed`: the same end with no players, so the red is removed.
  - `test_plain_lists_change_nothing`: the same end with plain `list` frames gives exactly what main gives today, and `held == []`.
  - `test_the_last_rock_is_checked_too`: the stone is covered in the last rock's window, which `until_disturbed` cuts, and is put back.
  - `TestCover` over frames: covered whenever unseen gives `True`. An open spot in 2 of 6 unseen frames gives `False`. Frames where the stone is seen say nothing. 4 unseen frames is not enough with `MIN_UNSEEN = 5`. A box beside the spot, 0.2 m clear of the stone's edge, gives `False`.
- [ ] **Step 2: Run** `tests/test_shots.py`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement** `cover.py`, with `SEEN_M = 0.15`, `COVERED_SHARE = 0.8` and `MIN_UNSEEN = 5`. Rewrite the module docstring around the detector's own boxes. Keep the prototype's evidence paragraph. Then make the `from_deliveries` change.
- [ ] **Step 4: Run** `tests/test_shots.py tests/test_rest.py`. Expected: PASS.
- [ ] **Step 5: Commit** with the message "shots: a stone lost under a player is put back (the detector's player boxes)".

### Task 13: The harness reports recovered stones

**Files:**
- Modify: `scripts/house_read_report.py`
- Test: `tests/test_house_read_report.py` (new). Load the script with `importlib`, as `tests/test_labels_export_script.py` does.

**Interfaces:**
- Consumes: `from_deliveries(held=...)` (Task 12).
- Produces:
  - Every row gets `held: [{"color","x","y"}]`, the put-backs for that shot.
  - `replay` passes the unseparated detector frames as `house_frames`. It prints `frames with players: N of M` per video.
  - `recovered(old_rows, new_rows) -> list[dict]`: per shot, a stone of `new` with no same-colour stone of `old` within 0.15 m. Each entry is the shot's key plus `color, x, y, how`, where `how` is `"put back"` if the stone is in the new row's `held` and `"seen"` otherwise.
  - `--compare OLD NEW` also prints the recovered stones and their count by `how`. `--expect PROTO_OLD PROTO_NEW` gives the prototype's pair. Each of its recovered stones that is missing from this comparison's recovered list prints as `STILL LOST`, and the exit code is 1.
- [ ] **Step 1: Write the failing tests:**
  - `test_recovered_tags_put_back_and_seen`: two shots, one with a held stone and one with a newly seen stone.
  - `test_moved_less_than_seen_m_is_not_recovered`.
  - `test_expect_reports_still_lost`.
- [ ] **Step 2: Run** the test. Expected: FAIL.
- [ ] **Step 3: Implement.** Also drop `--cover` and the `Cover` wiring if they were cherry-picked. Nothing on main has them.
- [ ] **Step 4: Run** the test. Expected: PASS.
- [ ] **Step 5: Commit** with the message "house_read_report: recovered stones, seen or put back; --expect".

---

## Phase 5: Acceptance and ship

### Task 14: Acceptance on the harness (report; stop if the bar is missed)

**Files:** none committed beyond the reports' notes. Runs on the laptop kit `/data/wdd/curling/houseread-1027` and on the Ryzen for f_0zkA.

- [ ] **Step 1: Baseline.** Run current main with overhead-3 through `house_read_report.py --out base`.
- [ ] **Step 2: Candidate.** Run `player-class` with overhead-4 through `--out new`. Check that `frames with players` is non-zero for every video.
- [ ] **Step 3: Compare.** Run `--compare base new --expect main1101 occlx`, the prototype's pair. Required:
  - no `STILL LOST`;
  - total recovered ≥ the prototype's 15;
  - board agreement ≥ 42/54 with no end worse;
  - impossible additions ≤ 10.
- [ ] **Step 4: Check every put-back on video.** Use `occl_look.py`-style strips into `~/curling-work/occl/looks-o4`, and look at each one. Required: zero false holds.
- [ ] **Step 5: `MIN_UNSEEN`.** Rerun the candidate at 3 and 10. Keep the value with the most recovered stones and zero false holds. If it isn't 5, change it in `cover.py` and commit that.
- [ ] **Step 6: f_0zkA.** On the Ryzen, replay 6vOTUuUSMKc g2 e7 as on 2026-11-01. Required: rocks 15 and 16 lose their phantom removals, with the reach veto on. Also report a run with the veto off, using a throwaway local edit that is not committed.
- [ ] **Step 7: Stones.**
  - Releases, splits and colour flips with `ab_rel_ff.py` (memory: validating-release-changes): not worse beyond seed noise.
  - Stone mAP and player precision and recall, from Task 9, carried into the report.
  - Detection seconds per end: not measurably worse.
- [ ] **Step 8: Report** to the user in one message, with the numbers against each bar. If any bar is missed, stop there.

### Task 15: Ship (on the user's go)

- [ ] **Step 1:** Make the model change commit: `weights.DEFAULT_NAME`, `Dockerfile.worker` `ARG MODEL`, the `.gitignore` exception and `cloudrun.yaml` `MODEL_ID` (memory: red1-overhead-frames, the overhead-3 ship). Merge `player-class` to main.
- [ ] **Step 2:** In quiet hours, with nothing awaiting playlist acceptance (memory: youtube-gentle), deploy both workers from a clean worktree. The API deploy with the new `MODEL_ID` is handed to the user, and the deploys must land together or claims get 409. Expect the detection cache to turn over.
- [ ] **Step 3:** Watch the first processed end on each worker. Confirm the timeline shows player-backed put-backs only where expected, and that per-end time is unchanged. Update memory `occlusion-hold.md` and the index. Leave any reprocess to the user.

