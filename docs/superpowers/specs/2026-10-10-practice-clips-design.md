# Practice clips: a video of each delivery

**Date:** 2026-10-10. **Status:** approved in conversation; this is the written spec.
**Builds on:** `2026-10-09-practice-sessions-design.md` (sessions, watches, the practice lane).

## Goal

A curler practising on a sheet taps a throw in their session and watches that delivery on
their phone: the slide and release, then the rock to rest. While a session is live, the
newest throw's clip plays by itself a few seconds after the rock stops.

Today the source is the club's YouTube stream (one composite frame per sheet). Later it
will be the club's own cameras, so a clip is described as pieces of camera views, and
only the code that turns a view into pixels knows about the composite.

## What the user asked for (2026-10-10)

- One clip per throw, cut from two long cameras:
  1. **the camera facing the thrower** (the long camera at the destination end, looking
     back up the sheet), from before the push until the rock leaves its frame;
  2. then **the camera behind the thrower** (the other long camera), from the moment the
     rock crosses the far hog line until it comes to rest.
- Stored in and served from Google Cloud, shown on the session page.
- Playback: the newest throw's clip **autoplays** (muted, inline) while a session is live;
  any other throw plays when its row is tapped.

## Assumptions (stated in conversation, not contradicted)

- Portrait, phone-sized, about 1-2 MB a clip.
- Who may watch a clip is who may see the throw: the owner of any session whose window
  holds it (two people on one sheet, or a copied session, both see it).
- Clips are kept as long as the session; no expiry for now.
- Today's 9:16 sheet 3 session (`ps_0o8DrTmt5lss1APHG`, and John's copy
  `ps_0tqHMxjIIbe7d1kbc`) gets clips, cut from the VOD already on the worker box.

## Approach

The worker cuts each clip during the watch, from the recording it is already reading, and
uploads it through the artifact flow it already uses for `practice.json`. The page asks
the API for a short-lived signed link and plays it from Cloud Storage.

Rejected: cutting after the session (no clips while practising, and the recording would
have to be kept, against the practice rule that recordings are never kept); YouTube embeds
at the throw's time (no cropping or camera switch, and gone with local cameras).

## 1. Cutting, on the worker

### The plan: `practice/clips.py`

`plan(throw, sideviews) -> ClipPlan | None`. A plan is a list of `Piece(view, t0, t1)`,
`view` being `"left"` or `"right"` and times on the stream's clock, like the throw's.

- The camera facing the thrower is `sideviews[CAMERA_FOR[OTHER_HOUSE[house]]]` (for a
  throw to the top house, the right-hand view); the camera behind the thrower is
  `sideviews[CAMERA_FOR[house]]`. These are the same choices `practice/enrich.py` makes
  for `hog_view` and `dest_view`.
- **Piece 1, facing the thrower:** from `t_release - PUSH_LEAD_S` (5 s) to `t_exit`, when
  the rock leaves the bottom of that camera's frame.
  - Where it leaves: `view.metres_at(bottom row)` (metres from the throwing tee, from the
    side view's paint calibration; an extrapolation past the hog row it was fitted at).
  - When: the rock's along-sheet position between the near hog line (`t_hog_s`,
    `v_hog_m_s`) and the far hog line (`t_far_s`), modelled as constant deceleration.
  - Fallbacks: no calibration or no hog times, `t_exit = t_far_s`; a throw that never
    arrived, `min(t_exit, t_release + FRONT_MAX_S)` (10 s), and piece 1 is the whole clip.
- **Piece 2, behind the thrower:** from `t_far_s` (or `t_exit` when there is no far
  crossing) to `t_rest + REST_TAIL_S` (1.5 s).
- No release (a stone placed by hand), or no side views at all: no plan, no clip.
- `plan.end` is the last time the clip needs; the clip is cut only once the recording has
  been read past it.

### The cut

`cut(source, rects, plan, out_path, *, t0_s)`: one ffmpeg run. It seeks the input to the
first piece's start, then for each piece trims, crops that view's rectangle out of the
frame and scales it to **600x810** (the long cameras' panels are about 800x1080), joins
the pieces, and encodes H.264 (libx264, veryfast, CRF ~27) at 30 fps, no audio,
`+faststart` so it plays while downloading.

- `rects` maps a view to `(source path, crop rect or None)`. For the composite stream both
  views are the recording file with the `SideView.rect` crops; for local cameras each view
  will be its own file with no crop. Nothing else changes.
- Times convert to the source's clock with `t - t0_s`, on the same convention the
  pipeline's frame reader uses for the recording (`ingest/frames.py`), so a release read at
  time t is at time t in the cut. Verified on real footage, not assumed.
- Bounded like `harvest/clips.py`: `-fs` (20 MB) and a timeout.
- CPU encode: NVENC is not set up in the worker containers (no `video` driver capability),
  and practice runs only when no league does, so the CPU is free. The cut time is measured
  in implementation; a few seconds per clip is expected.

### In the watch and the lane

- `throws.throw_record` adds `t_hog_s` and `t_far_s` (stream clock, or null), and the
  record gains `clip: false`.
- `PracticeWatch` takes an optional `clipper`. After each read it clips, oldest first,
  every throw without a clip whose `plan.end` is now recorded; each throw is tried once. A
  cut that works sets `clip: true`, and the document is published again.
- The lane's clipper cuts into a temporary file and uploads it as the artifact
  `clips/<throw id>.mp4`, then deletes the file. A failed cut or upload is logged; that
  throw keeps `clip: false`.
- `practice-replay --clips` passes a clipper that keeps the files in `<out>/clips/`.

## 2. Storage and serving

- **Key:** `runs/{video_id}/{run_id}/clips/{throw_id}.mp4` (`store.clip_key`).
- **Upload:** the artifacts allowlist for practice jobs accepts `clips/<id>.mp4` where
  `<id>` matches `PRACTICE_THROW_ID`, as `video/mp4`, at most 20 MB. Anything else stays a
  400.
- **Serve:** `GET /api/practice/sessions/{sid}/throws/{tid}/clip` returns
  `{"url": <signed GET, 1 hour>}`. Only the session's owner, only for a throw in the
  session's window whose record says `clip: true`; otherwise 404. Range requests (seeking)
  are Cloud Storage's. A plain `<video src>` needs no CORS.
- **Cost:** a 40-throw session is about 60 MB; no lifecycle rule for now.

## 3. The page

- The throw card plays the clip at its top when the throw has one: portrait, at most 60%
  of the screen's height, `playsInline`, with controls. The signed link is fetched when the
  card shows that throw, and kept for 50 minutes.
- While the session is live, when the newest throw's clip becomes available it plays by
  itself, muted. A throw picked from the table loads its clip without autoplay.
- While live, until the newest throw's clip arrives, the card says "Clip on its way…".
  An ended session shows nothing where there is no clip.

## 4. Filling in today's session

- Rerun `practice-replay --clips` over the downloaded VOD (959-3290 s, lookback 959) with
  the new code: a new `practice.json` with hog times and `clip` flags, and the clips.
- `scripts/practice/import_session.py --replace-run r_1e6nO93eHjTscbqBd --clips <dir>`
  uploads the clips and replaces that run's `practice.json`, leaving the run, watch and
  both sessions as they are. Nobody has hidden a throw yet, so changed ids lose nothing;
  the script says how many hidden ids no longer match.

## Testing

- `plan`: the camera per direction; the exit time from the calibration and the hog times;
  each fallback; no plan without a release.
- `cut`: on a synthetic four-view video (ffmpeg `testsrc`), the output's length, size and
  frame rate, and that each piece shows its view (a colour per view).
- The watch: clips cut once each, only when recorded far enough, the record flagged, the
  document republished; a failing clipper leaves `clip: false`.
- The lane: clips uploaded as `clips/<id>.mp4` artifacts.
- The API: the allowlist, the clip endpoint's owner-only rule, the window rule, the 404s.
- The page: a headless phone-sized check of the card with a clip, autoplay of the newest.
- Real footage: watch a handful of today's clips; the release is in shot, the switch comes
  as the rock leaves the frame, and the rock is followed to rest. Record clip sizes and cut
  times.

## Out of scope

Local camera capture itself; sound; sharing a clip outside the session; downloading;
deleting clips with a session (there is no session delete).
