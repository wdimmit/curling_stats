# Cameras by default, phase 3: results (2026-11-14)

Branch `camera-direct`, commits 5f714de4..HEAD. Nothing is deployed.

## Step 0: does the club list streams ahead?

Yes. Each league day's broadcasts are added to the playlists at about 05:00 PST, with `scheduledStartTime`:
- Supper 17:00 and Super 19:00 on 11/10;
- Mens 19:00 on 11/12;
- Skips 19:00 on 11/08.

Every one started on schedule. On Saturday 11/14 at 17:20 no playlist had an upcoming stream.

## Built

- **Upcoming streams.** `VideoMeta` reads `scheduledStartTime` and `actualEndTime`. The poll keeps each cameras
  playlist's upcoming streams (`WatchedPlaylist.upcoming`, within −30 min..+16 h). A stream's end is dated by YouTube.
- **Lookahead standbys.**
  - Each upcoming stream's sheet is recorded from 10 min before its scheduled start, whatever the sheet's cameras
    are doing. It is given up 90 min after opening if the stream never came.
  - The game already on the sheet is linked to it (`prev_run_id`/`next_run_id`) and told when the next one opened
    (`handoff_at`).
  - The window standby stays as the fallback for a sheet with no scheduled stream.
  - The camera cap counts sheets. `LIVE_MAX_STREAMS` is 5.
- **Reading start and ownership.**
  - The API tells each camera game where to read from: its opening, or 10 min before its stream.
  - It also tells the game its floor: where the game before it said its own ended, or its opening when there is no
    camera game before it, that game failed, or 45 min passed.
  - The session holds while the floor is pending, reads from the floor, and drops what is past its own end.
  - The earlier game finds its own end (`boardsplit.first_changeover`): a gap of 240 s, or 120 s with the board
    cleared, or the sheet quiet that long. It reports that once (`own_until`).
- **Late finish.** A game still going when its stream ends keeps recording until it has found its end, then 120 s
  more for the board, at most 45 min.
- **Shot links.**
  - Timelines carry `source.wall_t0` (at the recording's start and end), copied onto the run.
  - At serve time each shot outside its own stream gets `video: {id, shift_s}`, pointing at the stream before or
    after on its sheet, or `video: null`.
  - A shot before its stream has no own-video link.
- **Viewer.**
  - A shot plays from the video that shows it (`player.show`, which loads the other video in the same player).
  - "No stream for this shot." appears over the player when none does.
  - The YouTube link follows the shot, and is hidden when there is no stream.
  - Following the playhead and pausing after each rock ignore another video.
- **Privacy page.** Recording starts 10 min before the scheduled start, and a game is followed to its end, at most
  45 min more.

## Verified

- **Unit and API tests** for every task, run as subsets: playlists, standbys, service live, worker, boardsplit,
  session, lane, timeline, streamlinks, viewer JS, service API.
- **The 09/29 draw-switch shape** (eight Supper ends, then 470 s, then Super's first end): the boundary falls inside
  the gap, before Super's end.
- **Headless phone check** (390×844, devserve from the worktree, an 11/10 camera timeline seeded with three shots):
  - an early shot links to the previous stream (`?t=6894` = −300 − 5 s lead-in + 7199);
  - a shot with no stream hides its link and shows the overlay;
  - a shot in its own stream is as before, and the overlay goes.

  The first try served the main checkout's older bundle (devserve run from the main checkout's script); rerun from
  the worktree's.

## Not verified offline

- A real overlap on the ice: two recordings of one sheet, the earlier game's word reaching the later, and a game
  followed past its stream.
- The offline replay split of league-1105 named in the plan was not run. A replay can't stand in for the heartbeat
  between two live workers, and the session's span logic is covered by the tests above.

The gate is a back-to-back night after the deploy (Tuesday Supper → Super, 11/17).
