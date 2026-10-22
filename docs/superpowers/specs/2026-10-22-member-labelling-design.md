# Member labelling: rock boxes and release marks

**Status:** approved by the user, 2026-10-22. The user's mockups were OK'd that day (`.superpowers/brainstorm/.../flows.html`).

## Why

Two training sets are limited by how much labelling the user can do alone:

- **Rock boxes.** Since 2026-10-21 the workers save a few frames per game where the pipeline's own checks caught the detector being wrong (`curling_score.fpframes`, spec `2026-10-21-fp-frame-harvest-design.md`). On the first night that was 81 frames from 5 games. They are useless until somebody boxes every rock in them.
- **Release points.** The hand-on/off classifier and the release tag on the Delivery chart both need many hand marks. The user has marked about 200 rocks with `scripts/mark_release.py`, at about 20 rocks per 5 minutes.

Some club members are keen to help. This builds a page on the hosted site where a signed-in member can do both jobs. Every item is labelled by two members. Items where the two disagree go back to the user, who settles them on the laptop with the existing tools.

## Decided with the user

- **Who.** Any signed-in member. No role is needed. The page is not linked from the site's menus; the user gives the URL to people who want to help.
- **Desktop and laptop only.** The page needs a mouse and keyboard. On a phone-sized screen it says so instead of showing the tools.
- **Two flows on one page:** rock boxes and release marks, with a link between them.
- **Rock boxes: members fix all the boxes**, not just answer a question. Every rock in view gets a box with the right colour, and anything that isn't a rock loses its box.
- **Long-camera frames are cropped** around the flagged spot, as in the ds13 cropped sets, so few rocks are in view. Overhead frames show the one house panel.
- **Release marks: a one-off queue of about 1,000 rocks**, extracted once from games already cached on the worker box. There is no automatic extraction for releases. The mark is the same as the user's own: the first frame where the hand is clearly off the handle (Enter), `h` for still holding at the end, `s` for can't tell.
- **Each item gets two labels** from two different members. Neither sees the other's answer.
- **High disagreement goes to the user on the laptop.** An export brings every finished item down; agreeing items become training data, and disagreeing ones are settled with the existing tools (`boxedit.py`, `mark_release.py`). There is no adjudication page on the site.
- **Members see how many things they've labelled.** It's a count only: no leaderboard and no other people's numbers.
- **No rubber-stamping (2026-10-22).** On a box item the member must act on the flagged box, keeping it (`K`) or deleting it, before Enter will finish the item. A pair of labels that both left every starting box untouched goes to the user's list instead of being counted as agreement.
- **The user's own site labels count as one of the two (2026-10-22)**, like anyone else's. They don't settle an item alone.
- **The layout follows the mockup** (`flows.html`). There is a top bar with the flow name, the count and the link to the other flow. The image or frame strip is on the left, and the keys and buttons are on the right.

## Goals

1. Any signed-in member can open `/label`, pick a flow, and label item after item without setup.
2. Each item ends up with two independent labels from two different people, and nobody is shown the same item twice.
3. The user can export the results to the laptop in one command. Agreeing items arrive in the training formats already used (box edits for ds sets, `datasets/releasemarks` rows). Disagreeing items arrive as a list ready to settle.
4. New harvested FP frames can be added to the queue without code changes, by running one script.

## Non-goals

- Phones and tablets.
- Settling disagreements on the site.
- Leaderboards, badges, or showing anyone else's work.
- Automatic release extraction on the workers.
- Training anything. This builds the data; retraining is a later, separate decision.
- Labelling anything except rocks (brooms, heads and so on).

## Anti-goals (it fails even if it works)

- Members see other members' answers before giving their own. That makes the second label worthless.
- A member's bad or careless labels go straight into a training set without a second opinion.
- A member gets stuck: an unusable item, a broken image, or an item that never loads. Every item must have a skip.
- Anything here slows down or risks the workers' game processing.
- Private frames leak. Images are served only to signed-in members, through expiring links, the way clips already are.

## Constraints from what exists

- **Site.** FastAPI (`service/api.py`), Firestore records (`firestore_repo.py`), and a React frontend in `frontend/site/*.jsx` built by esbuild into `service/static/site.js`. A page is a plain HTML shell picking its screen with `data-page`. Members-only pages are public shells; the `/api/...` routes behind them are gated. Use the same pattern: `require_user` for every labelling route.
- **Images** live in GCS and are handed out as `store.presign_get` links (V4 signed, expiring), the way game clips are. Cloud Run caps a request body at 32 MB. Uploads go into the bucket straight from the box that has the files.
- **FP frames** exist only on the Ryzen worker box, in `<cache>/harvest/fp/<video_id>/` with a `frames.jsonl`. Each row has the flagged box in whole-frame pixels, `camera`, `where`, `rect` (the panel), `check`, `t`, `colour` and the models. The row has only the **flagged** box, not the model's other boxes in the view.
- **Release crops** come from `scripts/mark_release.py --extract-only`. Per rock, it writes about a hundred 560 px wide JPEGs at 30 fps that follow the rock, from the long camera facing the thrower, plus `index.json`. It runs on the box that has the video and timeline.
- **No new VOD downloads.** The 1,000 rocks come from videos already in the worker caches (memory: no-more-vod-downloads). Don't evict the 11 harness videos.
- **Privacy page** (`service/static/privacy.html`) says frames are kept to train the software. It does not say other members may see them. It must be updated in the same change (see below).
- The box editor's interaction model already exists offline in `train/boxedit.py`: drag and resize, Delete, set the colour. Reuse its ideas. **SAM click-to-box is not available on the site** (my call), since Cloud Run has no GPU. Members drag out a new box instead, so the mockup's "click a rock to add (SAM draws the box)" becomes "drag to draw".

## Design

### Items and labels

There are two kinds of item, both kept in Firestore.

- **Box item:** one image (a long-camera crop or an overhead panel) and its starting boxes. Each starting box has a rectangle in image pixels and a colour (red/yellow). It also keeps where the image came from (video, t, camera, crop rect in whole-frame pixels, check, models) so the export can map boxes back to whole-frame pixels and the ds tooling.
- **Release item:** one rock, with a list of frame images (or one sprite; builder's call), the frame rate, each frame's offset from the pipeline's release time, and where it came from (source, video, end, rock, colour, position, the `mark_release.py` index fields).

A **label** is one member's answer to one item:
- For a box item, the final list of boxes (rect + colour), or "can't tell".
- For a release item, the frame offset where the hand comes off, "held", or "can't tell".

It records the member's uid, when it was made, and how long the member spent on it (for spotting careless labelling at export).

### Handing out items

- When a member asks for the next item in a flow, give them an item they haven't labelled yet that has fewer than two labels. **Finish half-done items first** (one label already) so pairs complete quickly (my call).
- Hold the item for that member for a short time (about 10 minutes, my call) so two people aren't given its last slot at once. An expired hold returns the item to the pool.
- **Skip** (`N` in both flows) gives up the hold without a label. It's not a third answer, and the item goes to someone else. **"Can't tell"** (`S` in both flows, as in `mark_release.py`) is a label: it fills one of the two slots. An item skipped 5 times leaves the queue and is exported to the user's disagreement list as unlabellable (my call).
- A member can go back to change their own last label before moving on (the builder decides how far back). They never see another member's label.

### Starting boxes for box items

A box item must arrive with **every rock the model found in the view** pre-boxed, not only the flagged one, so members fix rather than draw from scratch.

When adding to the queue, run the current production detectors (the overhead model and the long-camera model named in the row's `models`) on the crop, on the worker box. Pre-box everything above a low confidence, plus the flagged box. Mark the flagged box so the page can show it dashed, as in the mockup. Use the worker image, the way `fp_harvest.py list` was run, and stay off the workers' GPUs while games are processing (builder's call how: a quiet-hours run, or a low-priority container).

**Long-camera crop.** Centre the crop on the flagged box and size it like the ds13 cropped sets: large enough for the context around a stone, small enough to keep few rocks in view. The builder should check the cropped50 tooling for the exact rule. An overhead item is the panel `rect` from the row.

### Adding items: two scripts on the worker box

1. **FP frames → box items.** Reads new piles from both caches (as `fp_harvest.py list` does), cuts the crop, runs the detectors, uploads each image to the bucket, and creates the items through an admin API route (`require_admin`, ADMIN_TOKEN). It remembers what it has added, so it's safe to rerun. It's run by hand (or later by cron) after a league night.
2. **Releases → release items.** A one-off.
   - Pick about 1,000 rocks from cached games with timelines, spread across games, sheets and throwers (random, seeded, as `tl/train_markset.json` was).
   - Run `mark_release.py --extract-only`, upload the frames, and create the items.
   - Leave out rocks the user has already marked in `datasets/releasemarks/`. Those become checks (see below).

### The page

- `/label` is a plain shell like `/thinking`. It's not in any menu. It redirects or prompts to sign in when signed out.
- **Top bar:** flow name, "You've labelled N" (that member's count for that flow, or both; builder's call), and a link to the other flow.
- **Rock boxes:** as in the mockup. Pre-boxed image, flagged box dashed, colour labels on each box. Drag the corners to fix a box, select it and press Delete to remove it, drag on empty ice to draw a new one. `R`/`Y` set the selected box's colour. `K` keeps the flagged box as it is (dragging, recolouring or deleting it also counts as acting on it). Enter means done, and it's refused, with a hint, until the flagged box has been acted on. `S` means can't tell; `N` means skip. The label records whether each starting box was kept untouched, moved, recoloured or deleted. Zoom or a magnifier is needed for small long-camera stones (builder's call how).
- **Release marks:** as `mark_release.py`'s page, and as in the mockup. One frame large, a frame strip and a slider. The arrow keys step a frame (shift steps 5). Enter means hand off here, `H` means still holding at the end, `S` means can't tell, `N` means skip. The model's read is never drawn.
- **First visit to each flow:** a short how-to. For releases it shows two example rocks the user has marked, a lift and a hover (the two styles found in the release spike). For boxes it gives the rule: every rock gets a box, partly hidden rocks included, people and brooms get none.
- **Window too narrow:** a phone-sized window (below about 1000 px wide, builder's call) gets a note saying the page needs a computer.
- Images load through signed links fetched with the item. Prefetch the next item so moving on feels instant.

### Agreement

Computed at export, not on the site. Thresholds are my calls; the user can change them.

- **Release:** two marks agree when both are "off" and within 3 frames (0.1 s) of each other. Both "held" agree too. Any other mix disagrees, and so does a gap over 3 frames. The agreed mark is the later of the two (the definition is the first frame clearly off). Any "can't tell" means disagree.
- **Boxes:** match the two members' boxes one-to-one by IoU. They agree when every box matches another at IoU ≥ 0.7 with the same colour and none is left over. The agreed boxes are the averages of the matched pairs. Anything else disagrees. **Exception:** if both labels left every starting box untouched (the flagged one only kept with `K`), the pair goes to the user's list even though it matches.

### Export to the laptop

Run `scripts/labels_export.py` on the laptop. It pulls finished items (two labels) through an admin API route and writes:

- **Agreeing box items** as a ds-style label set: the image (downloaded) and the boxes. Convert them to whole-frame coordinates too, so a later merge into a ds set can use `merge_edits`.
- **Agreeing release items** as rows in the `datasets/releasemarks` format, with both members' marks kept.
- **Disagreements** as a list the existing tools can open. Box items go to `boxedit.py` with both members' boxes visible. Release items go to `mark_release.py --prepared` over the same frames, with both marks shown.
- Who labelled what, by uid, with the time spent, so a careless member's labels can be found and dropped.

The export is rerunnable. Settled disagreements are recorded on the laptop (in `datasets/`, committed), not written back to the site.

### Checks on members (my call; light)

Seed each queue with a few items the user has already labelled: the existing release marks, and frames from the ds sets. Hand them out like any other. They count as normal items to the member. At export, report each member's agreement with the user on those items. Nothing is automatic; the user decides whether to drop someone's labels. The reference for these checks is the user's existing laptop labels, not their site labels, so the user labelling on the site doesn't distort the scores.

### Privacy page

Update `privacy.html` in the same change. Still frames and short frame sequences from games may be shown to signed-in members who volunteer to label them for training. The labels are stored with the member's account.

## Left to the builder

- Firestore layout: collections, the hold mechanism and the indexes. Keep the "next item" query cheap. The release queue is a few thousand records at most.
- Frame storage for release items: separate JPEGs or one sprite per rock. Possibly trim to a window around the pipeline's release time if a hundred frames per rock is too heavy (check against the user's existing marks that the window covers them).
- Zoom or magnifier design for small stones.
- How the detector run on the worker box avoids game processing.
- Tests: the API routes (handing out, holds, two different people, skip), the agreement maths, and the export formats. Run the affected subset only (memory: the full suite OOMs here). The Firestore emulator holds for the repo contract test.
- Deploy: the API deploy is the user's call (as usual), with a version bump only if the worker image changes, which it should not.
