# DFW event site — design

2026-11-07. Branch `dfw` (from `cincinnati` at 8bc8e401). Site: **https://dfwcurling.dimmit.net**.

## Intent

DFW Curling Club (Dallas) is running an event next week and streams its games on YouTube
(channel `UCle7XmcABZIpyveRduQH10w`). We want a public site where **the players at that event**
look at their own games: the chart end by end, the house after each rock, per-shot detail, and a
clip of each rock — the same viewer curling.dimmit.net has, for DFW's streams.

It has been proven by hand: two DFW games charted (vvy26k01ZcI 6/6 ends; the 5U qualifier live,
7/8 ends against the board) with DFW-only models (overhead `dfw1a.pt`, long `dfw2b.pt`), a broadcast
normaliser run beside the worker, a local API, and a static demo with clips
(storage.googleapis.com/curling-stats-508323-demos/dallas-5u/). This project makes that a site that
runs by itself.

## Goals

- DFW's streams are found, charted live while they are on, and kept and finalised when they end,
  with no one running scripts by hand (beyond the per-event layout fit below).
- Anyone with the link sees every game: game list, chart, Watch, Report, house images, clips.
- Every rock's clip plays without anyone signing in.
- The operator (the user, or Claude with the admin token) can correct a misread end.

## Non-goals

- Sign-in and everything behind it: I played…, Teams…, My games / My shots, stars, flags,
  labelling, team sharing.
- /thinking, /ice, /practice, /label, /submit, /playlists for the public (admin pages may stay
  reachable by URL with the admin token).
- Making DFW's stream work on curling.dimmit.net. That is option B and was not chosen.
- New model work. dfw1a/dfw2b as they are; the open accuracy items (final read landing in the
  clearing; 720p close calls) are not fixed here.

## Anti-goals (a failure even if it works)

- **Anything touching curling.dimmit.net's data or deploys.** Separate project, separate data;
  Granite's deploy path unchanged in behaviour.
- **The public editing charts.** Today, with accounts off, `may_edit_chart` lets every link edit
  (`service/api.py:519`). On this site the public is read-only.
- Granite-specific wording on DFW's site (league names, "the club's cameras", Seattle time).

## Constraints

- **This weekend and next week.** Date ranges to come from the user.
- One GPU worker: the GPU laptop (administrator@100.80.66.81). It handled one live stream
  comfortably; three at once is untested. Other club tests on the laptop stop for the events.
- DFW's broadcast cuts between layouts and differs per event (First Rock vs the 5U qualifier).
  Nothing downstream reads it raw: every frame must be mapped into the canonical three-panel
  layout first (`scripts/dallas/normalize_layout.py`, layout files `scripts/dallas/layouts/<event>/`).
  A new event's layout can only be fitted from its own broadcast (~10 min of footage,
  a hand-marked skeleton, `fit_layout.py`) — about an hour on the first morning unless a sample
  arrives earlier.
- Streams are 720p or 1080p; the pipeline works at 1920×1080 after normalising.
- Club time zone: America/Chicago.

## Decided (by the user)

- Option A: a separate, full site — not DFW on the main site.
- Public, no sign-in; /thinking and /ice hidden.
- Domain `dfwcurling.dimmit.net`.
- Work on branch `dfw`, based on `cincinnati`.
- **Up to 3 games stream at once.**
- **Charted: this weekend's event and the next one**, by date ranges the user will give. Nothing
  else on the channel.
- **The 5U qualifier stream (46wEMds_tYQ) is on the site.**
- **Next week's broadcast looks like the 5U stream:** its layout (`scripts/dallas/layouts/5u-qualifier`)
  is the one to use; no first-morning fit is planned.
- **DNS:** the user adds the record (`dfwcurling` CNAME `ghs.googlehosted.com.`, as curling.dimmit.net
  has; dimmit.net is verified on wdimmit@gmail.com, which creates the mapping).
- **A new GCP project** for the site (2026-11-07). The deploy scripts already take `PROJECT_ID`;
  a second service in curling-stats-508323 would clash on service, registry and secret names and
  share Firestore permissions with Granite.

## Calls I made (the user can overrule)

1. **The new project uses the same billing account as curling-stats-508323**, unless the user says
   otherwise. Its id: `dfwcurling` if free, else `dfwcurling-<suffix>`.
2. **Read-only for the public.** Chart edits (grading, stones, names) need the admin token;
   the viewer shows no edit controls to the public.
3. **Clips are cut for every rock automatically** once its end is published (live) or the game is
   final (VOD), by a clips worker on the laptop — no one has to ask. Clip… is offered on a rock that
   has one; a rock still being cut says so. (The demo proved ~4 s per rock on the laptop: ~9 min of
   worker time per 8-end game.) Clips kept the same 21 days as the main site.
4. **Game discovery from the channel's uploads playlist**, limited to videos whose stream started
   inside one of the operator's date ranges (a site setting the operator edits; empty = nothing is
   queued). Videos outside every range are never queued, so the back catalogue stays out.
   Discovery source: (`UU` + the channel id's tail), polled by
   the existing playlist poller every 3 min — live streams and finished ones alike — with
   `ALLOWED_CHANNEL_IDS` = DFW's channel only. The builder must check live streams do appear there;
   if not, the admin adds each stream by id (the endpoint exists).
5. **One game per stream** for DFW: rocks after the last end (practice slides after a game) never
   start a second game. A per-club setting, not a per-stream one.
6. **Broadcast conversion inside the worker**, for any club whose profile names a layout: live —
   a recorder that records raw as today and converts as it records, handing the lane the converted
   file (what `scripts/dallas/normalize_live.py` + `live_local.FollowRecorder` did by hand);
   VOD — convert after download, before analysis. The converted video is what is kept and what
   clips are cut from. Converting runs at ~90 fps on the laptop (3× real time).
7. **Which event layout to use** is a worker setting (a layout directory): the 5U layout for both
   events, changeable by the operator if an event's graphics turn out different. Frames that match no view repeat the last good frame (as now).
8. **Site profile by environment**: one setting picks DFW's name, nav, time zone and terms; the
   Granite deploy sets nothing and is unchanged.
9. **Terms and privacy** rewritten for DFW (who runs it, that it charts DFW's public YouTube
   streams, no accounts, what is kept and for how long). Claude drafts; the user approves the text.
10. **Worker image built with DFW's models** (`MODEL=dfw1a.pt SIDE_MODEL=dfw2b.pt`): the image's
   baked weight env beats the club profile today (`Dockerfile.worker`), and the API's `MODEL_ID`
   must match the worker's or every claim is refused.
11. **Three live streams on the laptop**, checked before the event by replaying the 5U recording
   three times at once. If it can't keep up, the stream limit is set to what it can, and the rest are charted from
   the recording after their game ends. No other GPU: the Ryzen is Granite's production worker.
12. **`dfw` stays a deploy branch** for this site, like `cincinnati`; it is not headed for main.
   Changes are still kept inert when no site profile is set where that is cheap, so a later merge
   stays possible, but no main-branch review gates the event. Test 4 below means the branch's own
   suite, not curling.dimmit.net's deploy.
13. **Frames no view matches are watched:** if most of a stream's frames match no view of the
   layout (different graphics after all), its job stops with an error the operator sees, rather than
   publishing a junk chart (there is no delete endpoint).
14. **The 5U game on the site is the live replay's result** (test 1 below), not a recorded-video
   reprocess: the hand-run live chart got 7/8 ends, the recorded-video path 5/8 (the final read
   landing in the clearing; unfixed). Test runs other than that one are wiped before launch.
15. **Games already over when the site comes up** (this weekend's, if the site is not ready in time)
   are charted from their YouTube recordings — the weaker recorded-video path above — and the
   operator checks each against the scoreboard before it is public-facing.

## Left to the builder

- How the site profile is expressed (env vars vs a small profile table), and how nav/branding read it.
- How the read-only rule is enforced (API refuses unauthenticated writes; viewer hides controls).
- The converting recorder's shape, including what happens when yt-dlp restarts a part
  (every part starts at segment 0 — reconverting from the start at 3× real time catches up).
- How auto-cut clips are queued (a clip job per published end, or per game at the end).
- Scheduler, Firestore indexes/rules, bucket lifecycle — as `deploy/README.md` describes.
- Worker compose for the laptop (live + VOD + clips roles, cache size, recording days).

## How we know it works (before the event)

1. **Replay the recorded 5U stream** — once alone, then three copies at once for capacity — (`/data/wdd/scratch/dallas/live2/raw-0.ts`, kept on the laptop)
   through the deployed site end to end as if live — a fake live job reading the file at real
   speed or faster — and get the same 7/8 ends as the hand-run, with clips for every rock.
2. The first-game VOD (vvy26k01ZcI, cached on the laptop) through the VOD path: 6/6 ends.
   (Its layout is `scripts/dallas/plates` — the First Rock event's.)
3. The public site at 390×844 and desktop: no sign-in, no /thinking or /ice, no edit controls,
   Clip… plays; an edit without the admin token is refused.
4. curling.dimmit.net's tests pass unchanged with no site profile set.

## Open

- The date ranges of the two events (the user will give them).
