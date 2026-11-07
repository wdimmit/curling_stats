# DFW Event Site Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A public, read-only site at https://dfwcurling.dimmit.net that charts DFW Curling Club's
event streams live and from recordings, with a clip of every rock, in its own GCP project.

**Architecture:** The worker learns to convert DFW's switching broadcast into the canonical
three-panel layout itself (live, as it records; VOD, after download), driven by a layout named in
the club profile. The API gains a site profile (`SITE=dfw`) that sets name, nav, time zone, terms,
read-only writes, auto-cut clips and date-ranged discovery; Granite's deploy sets nothing and is
unchanged. Deployment is a new project with the existing deploy scripts plus a laptop compose.

**Tech Stack:** Python 3.12, FastAPI, Firestore, GCS, Cloud Run, ffmpeg/OpenCV, YOLO (ultralytics),
React 19 + esbuild, Docker compose on the GPU laptop.

**Spec:** `docs/superpowers/specs/2026-11-07-dfw-event-site-design.md`

## Global Constraints

- Work in worktree `.claude/worktrees/dfw`, branch `dfw`. Never push; never touch curling-stats-508323.
- Tests: `PYTHONPATH=src /home/tcuser/src/curling_score/.venv/bin/python -m pytest -q <files>` —
  run the affected files, not the whole suite (the box OOMs on the full suite, exit 137).
- Frontend: `cd frontend && npm run build` (never esbuild directly); commit the built bundles.
- With `SITE` unset every behaviour is exactly as before (Granite). Every new setting defaults off.
- Club time zone `America/Chicago`. Site name `dfwcurling.dimmit.net`. DFW channel `UCle7XmcABZIpyveRduQH10w`.
- Up to 3 live streams at once (`LIVE_MAX_STREAMS=3`), one worker host: administrator@100.80.66.81.
- Models: overhead `dfw1a.pt` (md5 98233410…), long `dfw2b.pt` (md5 6d3111f9…); `MODEL_ID` on the
  API must equal the worker's.
- Billing account for the new project: `01ED5C-D2A88C-57F6FD`. Project id `dfwcurling`, else
  `dfwcurling-<4 digits>`. Region `us-west1`.
- DNS (the user adds it): `dfwcurling` CNAME `ghs.googlehosted.com.`
- Outward-facing steps (create project, link billing, deploy, domain mapping, scheduler) are run
  only after telling the user in that turn; nothing is deleted without a backup.
- Kill processes by pid or exact executable path, never `pkill -f <substring>`.

## Review Focus

1. **A long pre-game slate or intermission graphic** (minutes of frames no view matches): the live
   layout guard must not fail the job for it; it fails only when no frame has matched for 20 min
   from the start, or <40% of frames matched over the last 10 min after the first match. (Task 3 test.)
2. **yt-dlp restarts a part mid-stream** (`rec.1.ts` takes over from `rec.0.ts`, both from segment 0):
   the converted file must follow the new part without its head going backwards. (Task 3 test.)
3. **A clip asked for while the stream is live** must be cut from the converted recording, never the
   raw one — raw frames are the broadcast's switching layout. (Task 3 test on `find_video`.)
4. **The public page's own POSTs** (anything the viewer or game list sends on load) must not hit the
   read-only 403 and break the page. (Task 6 test + Task 12 headless check: no 403 in the network log.)
5. **A VOD analysed twice** (retry, reprocess) must not convert an already converted file again.
   (Task 2 test.)

---

### Task 1: The broadcast converter in the package

Port `scripts/dallas/normalize_layout.py` (+ the constants it takes from `normalize.py`) into
`src/curling_score/broadcast/`, with the 5U layout as package data.

**Files:**
- Create: `src/curling_score/broadcast/__init__.py`, `src/curling_score/broadcast/layout.py`,
  `src/curling_score/broadcast/convert.py`
- Create: `src/curling_score/broadcast/layouts/dfw-5u/layout.json` (from `scripts/dallas/layouts/5u-qualifier/layout.json`),
  `.../dfw-5u/T.png` (from the laptop: `/data/wdd/scratch/dallas/live2/plates-5u-v2/T.png`, md5 it before and after)
- Modify: `src/curling_score/clubs.py` (`Club.broadcast_layout: str | None = None`; `DALLAS` gets `"dfw-5u"`;
  drop its "No channel id until…" comment, add `channel_ids=("UCle7XmcABZIpyveRduQH10w",)`)
- Modify: `pyproject.toml` package-data so `broadcast/layouts/*/*` ships in the wheel/image
- Test: `tests/test_broadcast.py`

**Interfaces:**
- Produces:
  - `layout.W = 1920`, `layout.H = 1080`, `layout.RECTS: dict[str, tuple[int,int,int,int]]` (from `normalize.RECTS`), `layout.GUARD = 4`
  - `layout.Layout` — `Layout.load(name_or_dir: str) -> Layout` (a bare name resolves under `broadcast/layouts/`; env `BROADCAST_LAYOUT_DIR` overrides the root), attributes `name: str`, `size: tuple[int,int]`, `T: np.ndarray`; methods `classify(frame) -> str` (view name or `"?"`), `convert(frame, kind: str) -> np.ndarray`
  - `convert.Converter(layout)` — `push(frame) -> list[np.ndarray]` (frames ready to write, GUARD behind), `flush() -> list[np.ndarray]`, `counts: dict[str,int]`, `matched_since_first: list[bool]`-style stats via `stats(window_frames: int) -> tuple[bool, float]` = (any frame matched yet, fraction matched in the last `window_frames`)
  - `convert.convert_file(src: Path, dst: Path, layout: Layout, *, follow: bool = False, timeout_s: float = 300, fps: float = 30.0, on_head=None, encoder: str = "auto") -> dict` — counts; `on_head(seconds_written)` about once a second; `encoder` `"auto"` = h264_nvenc when ffmpeg lists it, else libx264 `-preset veryfast -crf 18`. Output MPEG-TS when `dst` ends `.ts`, MP4 otherwise.
  - `clubs.Club.broadcast_layout: str | None`

- [ ] **Step 1: Write the failing tests** — `test_layout_loads_by_name` (`Layout.load("dfw-5u").size == (1280, 720)`, `T.shape == (1080,1920,3)`); `test_classify_reads_the_seams` (a synthetic 1280×720 frame with strong vertical edges at the layout's `seams` x's and none at `no_seams` → that view; a flat grey frame → `"?"`); `test_convert_fills_regions_and_keeps_the_plate_elsewhere` (a frame with each source region a distinct solid colour → each `RECTS` region of the output has that colour at its centre; a pixel outside every region equals `T`); `test_converter_repeats_the_last_good_frame_through_a_dissolve` (push 10 view frames, 3 `"?"`, 10 view frames → 23 frames out after `flush()`, the 3 plus GUARD neighbours equal the last good converted frame); `test_convert_file_round_trip` (a 3 s synthetic 1280×720 30 fps clip written with ffmpeg → `dst` is 1920×1080, 90 frames ±1, `counts` names the view).
- [ ] **Step 2:** Run — FAIL (module missing).
- [ ] **Step 3:** Implement, porting the classify/convert/guard logic verbatim from `scripts/dallas/normalize_layout.py` and `normalize_live.py` (reader: `ffmpeg [-follow 1 -rw_timeout] -i src -map 0:v:0 -fps_mode passthrough -vf scale=W:H -f rawvideo -pix_fmt bgr24 -`; writer as `normalize_live._writer`, `-g 60`). Leave `scripts/dallas/*` as they are.
- [ ] **Step 4:** Run `tests/test_broadcast.py tests/test_clubs*.py` — PASS.
- [ ] **Step 5:** Commit `broadcast: DFW's layout converter in the package, the 5U layout as data`.

### Task 2: Convert a VOD after download

**Files:** Modify `src/curling_score/analyze.py:~1175` (after `cache.ensure_cached`); Create
`src/curling_score/broadcast/vod.py`; Test `tests/test_broadcast.py`.

**Interfaces:**
- Consumes: `Layout.load`, `convert_file` (Task 1).
- Produces: `vod.ensure_converted(path: Path, layout_name: str | None) -> Path` — `None` returns
  `path` untouched. Raises `RuntimeError("broadcast layout mismatch: …")` (the job fails, nothing is
  published) when under 40% of the video's frames matched a view. Otherwise converts into `<path>.converting-<pid>.mp4` and `os.replace`s it over
  `path`, then writes `<path minus .mp4>.layout` holding the layout name; a file whose `.layout`
  marker already names this layout is returned untouched. The pinned mtime of a kept recording
  (`cache.PINNED_MTIME_NS`) is preserved when the original had it.

- [ ] **Step 1: Failing tests** — `test_vod_wrong_layout_fails` (flat grey clip → raises, original untouched); `test_vod_converted_once` (convert a synthetic clip; marker written; second call does not invoke `convert_file` — monkeypatch it to raise); `test_vod_no_layout_is_untouched`; `test_vod_keeps_pinned_mtime`.
- [ ] **Step 2:** Run — FAIL.
- [ ] **Step 3:** Implement; in `analyze.analyze` call `path = vod.ensure_converted(path, club.broadcast_layout)` right after the download, before calibration/proxy (the proxy and detections are keyed on the converted file).
- [ ] **Step 4:** Run `tests/test_broadcast.py tests/test_analyze*.py -k "not slow"` — PASS.
- [ ] **Step 5:** Commit `analyze: a club with a broadcast layout converts its VOD before analysis`.

### Task 3: Convert live, as it records

**Files:** Create `src/curling_score/live/converting.py`; Modify `src/curling_score/live/manager.py:81-95`
(`LiveManager.__init__(…, club=None)`; `_recorder` wraps), `src/curling_score/service/worker.py:~562`
(pass `club=_club(None)` when `CLUB` is set), `src/curling_score/game/clips.py:137` (`find_video`);
Test `tests/test_live_converting.py`, `tests/test_game_clips*.py`.

**Interfaces:**
- Consumes: `convert_file(follow=True, on_head=…)`, `Converter.stats` (Task 1); `LiveError` (`live/session.py:76`).
- Produces: `converting.ConvertingRecorder(inner, layout_name: str, directory: Path, *, clock=time.monotonic)`
  with the recorder interface (`start, check, path, head_s, ended, whole, stop`):
  - converts the inner recorder's current part into `directory/norm.<n>.ts` in a thread; when the
    inner part changes (`inner.path` differs), starts converting the new part into `norm.<n+1>.ts`
    and switches `path` to it only once its head has caught up with the previous one's;
  - `head_s()` = the current converted part's seconds written (never less than before);
  - `ended()` = inner ended and the converter has flushed; `whole()` = `inner.whole()`;
  - `check()` = `inner.check()`, then raises `LiveError("broadcast layout mismatch: …")` when no
    frame has matched a view 20 min (36 000 frames) into the stream, or, after the first match,
    under 40% of the last 10 min (18 000 frames) matched.
- `clips.find_video`: in `live/<id>/`, prefer the largest `norm.*.ts` over `rec.*.ts`.

- [ ] **Step 1: Failing tests** — with a fake inner recorder whose `path` is a synthetic TS growing on disk: `test_head_follows_the_converted_file`; `test_restarted_part_is_followed_without_going_back` (Review Focus 2); `test_slate_does_not_fail_before_twenty_minutes` (feed `"?"` frames via a stubbed classifier for 19 min of frame count → `check()` passes; 20 min → raises `LiveError`) and `test_mostly_unmatched_after_first_match_fails` (Review Focus 1); `test_find_video_prefers_the_converted_recording` (Review Focus 3); `test_manager_wraps_when_the_club_has_a_layout` (`LiveManager(club=clubs.DALLAS)._recorder(job)` is a `ConvertingRecorder`; with `club=None` a `YtDlpRecorder`).
- [ ] **Step 2:** Run — FAIL.
- [ ] **Step 3:** Implement. `cache.keep_recording` already remuxes `recorder.path` into `videos/<id>.mp4`; write the `.layout` marker beside it there too (so Task 2 never reconverts a kept live recording).
- [ ] **Step 4:** Run `tests/test_live_converting.py tests/test_game_clips*.py tests/test_live_manager*.py` — PASS (note: `test_live_session.py` had 45 failures before this branch; don't chase them, but don't add any).
- [ ] **Step 5:** Commit `live: a club with a broadcast layout records converted; clips cut from it`.

### Task 4: Replay a recording as a live job

For the capacity and end-to-end tests (spec "How we know it works" 1). Admin-only; inert unless set.

**Files:** Modify `src/curling_score/live/manager.py` (`_recorder`), `src/curling_score/service/api.py`
(new admin route); Test `tests/test_live_manager*.py`, `tests/test_service_api.py`.

**Interfaces:**
- `WORKER_REPLAY_DIR` (env): when `<dir>/<video_id>.ts` exists, `_recorder` makes
  `replay.ReplayRecording(<that>, root/live/<vid>/rec.0.ts, speed=float(WORKER_REPLAY_SPEED or 1))`
  instead of yt-dlp (still wrapped by Task 3 when the club has a layout).
- `POST /api/admin/live-runs` body `{"video_id": str, "title": str, "format": "fours"}` (admin token)
  → 201 `{"run_id", "source_id"}`: a live run and job exactly as `playlists.poll` makes for a live
  video, without asking YouTube. A second call while one is live → 409.

- [ ] **Step 1: Failing tests** — `test_replay_dir_makes_a_replay_recorder`; `test_admin_live_run_needs_the_admin_token` (401/403 without); `test_admin_live_run_creates_a_claimable_live_job` (claim with `kinds=["live"]` returns it); `test_second_live_run_is_refused`.
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5:** Commit `live: replay a recording as a live job, for rehearsals`.

### Task 5: One game per stream

**Files:** Modify `src/curling_score/clubs.py` (`one_game_per_stream: bool = False`,
`scheduled_ends: int | None = None`; `DALLAS`: `True`, `8`), `src/curling_score/game/segment.py`,
`src/curling_score/timeline.py`, `src/curling_score/analyze.py:~1241`, `src/curling_score/live/session.py:~276`;
Test `tests/test_one_game.py`.

**Interfaces:**
- `segment.one_game(games: list[GameSegment]) -> list[GameSegment]` — all ends in one game, index 0,
  `start_s` of the first, `end_s` and `closed` of the last; `[]` stays `[]`.
- `timeline.drop_past_scheduled(game: dict, scheduled_ends: int) -> dict` — a copy without the ends
  after `scheduled_ends`, except while the running score (from `score` if set, else
  `detected_score`) is level after the scheduled ends: then it keeps extra ends up to and including
  the first that breaks the tie. Recomputes `detected.final`/`score_by_end` as `build_game` does.
- Applied after the board split in `analyze` and in `LiveSession._settled` when
  `club.one_game_per_stream`, and `drop_past_scheduled` where the document's games are built.

- [ ] **Step 1: Failing tests** — `test_one_game_joins_segments`; `test_ends_past_eight_are_dropped` (9 ends, 5-3 after 8 → 8 ends); `test_extra_end_kept_when_tied` (4-4 after 8, end 9 scores 1 → 9 ends; a 10th end after it dropped); `test_granite_unchanged` (`one_game_per_stream=False` → segments as before).
- [ ] **Step 2–4:** FAIL → implement → run `tests/test_one_game.py tests/test_segment*.py tests/test_timeline*.py` PASS.
- [ ] **Step 5:** Commit `game: DFW streams are one game; practice rocks after the last end are dropped`.

### Task 6: Site profile, read-only API, `/api/site`

**Files:** Create `src/curling_score/service/site.py`; Modify `src/curling_score/service/api.py`
(Settings + middleware + route); Test `tests/test_site_profile.py`.

**Interfaces:**
- `site.SiteProfile` (frozen dataclass): `key: str`, `name: str`, `club_name: str`, `tz: str`,
  `nav_hidden: frozenset[str]`, `read_only: bool`, `auto_clips: bool`, `pages_dir: str | None`,
  `discover_ranges: tuple[tuple[date, date], ...] | None`.
- `site.GRANITE` (`key=""`, `name="curling.dimmit.net"`, everything else off/None, `tz="America/Los_Angeles"`)
  and `site.DFW` (`key="dfw"`, `name="dfwcurling.dimmit.net"`, `club_name="DFW Curling Club"`,
  `tz="America/Chicago"`, `nav_hidden={"/thinking","/ice","/practice","/label","/submit","/playlists","/mine","/shots"}`,
  `read_only=True`, `auto_clips=True`, `pages_dir="dfw"`).
- `site.from_env(env=os.environ) -> SiteProfile` — `SITE` picks the profile; `DISCOVER_RANGES`
  (`"2026-11-06/2026-11-09,2026-11-13/2026-11-16"`, inclusive dates in the site's tz) fills
  `discover_ranges`; for `dfw` an unset `DISCOVER_RANGES` means `()` (queue nothing).
  `Settings.site: SiteProfile = site.GRANITE`; `Settings.from_env` sets it and, when `CLUB_TZ` is
  unset, `club_tz = site.tz`.
- `GET /api/site` → `{"name", "club", "nav_hidden": [sorted], "read_only", "auto_clips"}`.
- Read-only middleware: when `settings.site.read_only`, a non-GET/HEAD/OPTIONS request whose path is
  not under `/api/worker/` or `/api/admin/` gets 403 `{"detail": "this site is read-only"}` unless
  `Authorization: Bearer <admin_token>`. Before writing it, list every non-GET the public pages send
  (grep `frontend/` for `method:` / `fetch(` with a body) and allow by exact path any that a viewer
  needs to load or play (Review Focus 4); name them in a constant `PUBLIC_POSTS`.
- Operator corrections go through the existing chart routes with the admin bearer (curl or a small
  script); no editing UI on this site.

- [ ] **Step 1: Failing tests** — `test_from_env_default_is_granite`; `test_dfw_profile_and_ranges` (`SITE=dfw DISCOVER_RANGES=…` → two `(date,date)` pairs, `tz` America/Chicago); `test_read_only_refuses_public_writes` (POST `/api/charts` → 403; with admin bearer → not 403; GET `/api/site` → 200; `/api/worker/claim` with worker token unaffected); `test_granite_writes_unchanged` (no `SITE` → POST `/api/charts` not 403); `test_public_posts_allowed` (each path in `PUBLIC_POSTS` → not 403).
- [ ] **Step 2–4:** FAIL → implement → run `tests/test_site_profile.py tests/test_service_api.py` PASS.
- [ ] **Step 5:** Commit `service: site profiles; DFW is read-only to the public`.

### Task 7: Nav, name, pages and the viewer read the site

**Files:** Modify `frontend/site/ui.jsx:41-50` (Header), `frontend/site/Games.jsx` (hide "Chart this
game" when read-only), `frontend/site/Status.jsx:143`, `frontend/viewer/App.jsx:877-878`, `frontend/core/`
(new `site.mjs`, export from `index.mjs`, add to `tests/js/singleton.mjs`), `src/curling_score/service/api.py`
(page routes: serve `static/<pages_dir>/<name>.html` when present, else `static/<name>.html`; every
HTML `<title>` gets the site name substituted for `curling.dimmit.net`);
Create `src/curling_score/service/static/dfw/terms.html`, `.../dfw/privacy.html`; Test `tests/test_viewer_js.py`, `tests/test_site_profile.py`.

**Interfaces:**
- `core/site.mjs`: `visibleLinks(links: [path,label][], site) -> [path,label][]` (drops `site.nav_hidden`);
  `siteName(site) -> string` (`site?.name || "curling.dimmit.net"`).
- `runtime/site.mjs`: `fetchSite() -> Promise<site|null>` — one `GET /api/site` per page load, cached; `null` on error (pages then render as Granite).
- With `read_only`, the viewer on `/g/` shows no edit, "Chart this", Played/Teams/Flag controls.

- [ ] **Step 1: Failing tests** — JS: `visibleLinks` drops `/thinking` and `/ice` for the DFW site and nothing for `null`; `siteName`. Py: `test_dfw_terms_page_served` (`SITE=dfw` GET `/terms` contains "DFW Curling Club"); `test_titles_carry_the_site_name`; Granite `/terms` unchanged.
- [ ] **Step 2:** Run — FAIL.
- [ ] **Step 3:** Implement. Draft `dfw/terms.html` and `dfw/privacy.html` from the Granite pages: run by Will Dimmit; charts DFW Curling Club's public YouTube streams of the named events; no accounts and nothing collected beyond server logs (salted IPs, as Granite's page says); recordings kept 21 days for clips; contact as on Granite's pages. **Show both drafts to the user and get their OK before Task 11's deploy.**
- [ ] **Step 4:** `cd frontend && npm run build`; run the tests above — PASS.
- [ ] **Step 5:** Commit `site: DFW's name, nav, terms and a read-only viewer`.

### Task 8: Every rock's clip, cut without asking

**Files:** Modify `src/curling_score/service/gameclips.py`, `src/curling_score/service/api.py`
(`/api/worker/jobs/{job_id}/publish` ~3386 and `/complete` ~3421), `frontend/core/shotclip.mjs`,
`frontend/viewer/ShotClip.jsx`, `frontend/viewer/App.jsx` (`clipOffered`); Test `tests/test_game_clips_api.py`, `tests/test_viewer_js.py`.

**Interfaces:**
- `gameclips.ask_all(repo, run, source_id: str, doc: dict, now) -> int` — `ask_shot(…, user_id="auto")`
  for every rock in `doc` (`find_rock` names) with a release and no clip job yet; returns how many it asked.
- Called after a successful publish and complete when `settings.site.auto_clips`.
- `GET /api/shot-clips` for an un-asked rock on an auto-clips site answers `{"state": "queued", "ahead": n}`
  (it will be cut) rather than `"none"`.
- Viewer: with the site's `auto_clips`, Clip… shows on every rock while `shotClipsOffered`; the dialog
  never reaches the sign-in phases (`accountsOn()` is false here; "queued" polls as today).

- [ ] **Step 1: Failing tests** — `test_publish_asks_every_rock_once` (publish twice → `ask_all` asks the new rocks only; jobs `cs_<run>_<rock>`); `test_no_auto_clips_on_granite`; `test_unasked_rock_reads_queued_on_auto_site`; JS: with `{auto_clips:true}` and accounts off, the button's offered predicate is true.
- [ ] **Step 2–4:** FAIL → implement → build frontend → PASS.
- [ ] **Step 5:** Commit `clips: an auto-clips site cuts every rock as its end is published`.

### Task 9: Discover only the events' streams

**Files:** Modify `src/curling_score/service/youtube.py` (`uploads_playlist`),
`src/curling_score/service/playlists.py:91` (`poll`); Test `tests/test_playlists*.py`.

**Interfaces:**
- `youtube.uploads_playlist(channel_id: str) -> str` — `"UU" + channel_id[2:]`.
- `playlists.poll(…, ranges: tuple[tuple[date,date],...] | None = None)` — when `ranges` is not None,
  a video is queued only if its `published_at` (live: actual start), in `tz`, falls on a date inside a
  range; others are recorded as seen and skipped. `None` = as today. The API passes `settings.site.discover_ranges`.

- [ ] **Step 1: Failing tests** — `test_uploads_playlist_id`; `test_out_of_range_not_queued`; `test_in_range_live_queued_live`; `test_empty_ranges_queue_nothing`; `test_none_ranges_unchanged`.
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5:** Commit `playlists: a site's discovery is limited to its events' dates`.
- [ ] **Step 6 (verify, no code):** with the existing YouTube key, list `UUle7XmcABZIpyveRduQH10w`'s
  newest items and confirm 46wEMds_tYQ (a live stream) is in it. If live streams are absent, tell the
  user; the fallback is Task 4's admin route per stream.

### Task 10: The DFW worker image and the laptop compose

**Files:** Create `deploy/dfw/docker-compose.yml`, `deploy/dfw/worker.env.example`, `deploy/dfw/README.md`;
Modify `Dockerfile.worker` only if the weights args need it.

- `curling-worker:dfw` built with `--build-arg MODEL=dfw1a.pt --build-arg SIDE_MODEL=dfw2b.pt`.
- Services: `worker` (`CLUB=dallas`, `WORKER_LIVE=1`, `LIVE_MAX_STREAMS=3`, VOD on, `API_URL=https://dfwcurling.dimmit.net`,
  cache `/data/wdd/dfw-cache`), `clips` (`WORKER_ROLE=clips`, the worker's cache mounted read-only), `pot`
  (as `deploy/docker-compose.worker.yml`). Project name `dfw` so nothing collides with other compose stacks on the laptop.

- [ ] **Step 1:** Build on the laptop from a clean `git archive` of the branch head.
- [ ] **Step 2: Verify** `docker run --rm curling-worker:dfw python -c "from curling_score import weights, clubs, version; …"` prints dfw1a/dfw2b paths and the `MODEL_ID` the API must carry; `python -c "from curling_score.broadcast.layout import Layout; print(Layout.load('dfw-5u').size)"` → `(1280, 720)`.
- [ ] **Step 3:** Commit `deploy: DFW worker image and laptop compose`.

### Task 11: The project, the API and the domain

Ask the user before Step 1 that day ("creating project X on billing 01ED5C-… now").

**Files:** Create `deploy/dfw/setup-project.sh` (idempotent: project, billing link, APIs
`run firestore cloudbuild artifactregistry secretmanager cloudscheduler youtube`, Firestore native
default DB in `us-west1`, bucket + lifecycle, service account + roles, Artifact Registry repo `curling`,
secrets `curling-worker-token`, `curling-admin-token`, `curling-yt-api-key`, `curling-ip-salt`,
indexes/rules); Modify `deploy/cloudrun.yaml` + `deploy/deploy-api.sh` only to pass `SITE`,
`DISCOVER_RANGES`, `CLUB_TZ` through (empty for Granite).

- [ ] **Step 1:** Run `setup-project.sh`; record the project id in `deploy/dfw/README.md`. Tokens go to Secret Manager and `0600` files on the laptop, never printed.
- [ ] **Step 2:** `PROJECT_ID=<id> PUBLIC_BASE_URL=https://dfwcurling.dimmit.net ACCOUNTS=off SITE=dfw ALLOWED_CHANNEL_IDS=UCle7XmcABZIpyveRduQH10w ./deploy/deploy-api.sh`; `MODEL_ID` from Task 10 Step 2.
- [ ] **Step 3:** `gcloud run domain-mappings create --service curling-chart --domain dfwcurling.dimmit.net --region us-west1 --project <id>`; wait for the certificate (`curl -sI https://dfwcurling.dimmit.net/` → 200).
- [ ] **Step 4:** Scheduler poll job (`deploy/scheduler.sh`, `--time-zone America/Chicago`); watched playlist `UUle7XmcABZIpyveRduQH10w` via `POST /api/admin/playlists`. `DISCOVER_RANGES` stays empty until the user gives the dates.
- [ ] **Step 5: Verify** `GET /api/site` → `dfw`, read-only; POST `/api/charts` → 403; `/thinking` link absent.
- [ ] **Step 6:** Commit `deploy: DFW project setup`.

### Task 12: Rehearse, then open

- [ ] **Step 1:** Laptop: stop the other club-test containers (list them first; tell the user which). `docker compose -p dfw up -d`.
- [ ] **Step 2: Replay once.** Copy `live2/raw-0.ts` to `$WORKER_REPLAY_DIR/46wEMds_tYQ.ts`; `POST /api/admin/live-runs` for 46wEMds_tYQ ("DFW 5U Qualifier · Draw 3"); speed 1. Expected: ends published as played; final ≥7/8 ends against the board (end 4 the known close call); `layout mismatch` never raised; a clip for every rock with a release, cut from `norm.*.ts`.
- [ ] **Step 3: Capacity.** Three replays at once (copies under three fake ids, speed 1). Expected: each end published within the live lane's normal lag (≤ ~3 min behind head); if not, set `LIVE_MAX_STREAMS` to what keeps up and tell the user.
- [ ] **Step 4: VOD path.** (Replaces the spec's vvy26k01ZcI check: that event's First Rock layout is
  not in the package and neither event uses it.) Locally on the laptop (not the site): `analyze` with `club=dallas` on an unconverted copy of the 5U recording → converts once, charts (expect ~5/8, the known recorded-path weakness).
- [ ] **Step 5: Pages.** Headless Chrome at 390×844 and 1600×1000 (`scripts/cdp.mjs`): game list, `/g/<5U>/`, Watch, Report, Clip… plays; no sign-in, no /thinking or /ice; network log has no 403 (Review Focus 4).
- [ ] **Step 6: Wipe test runs.** Admin export backup first (see the deleting-charts memory), then delete the three capacity replays' sources/runs via Firestore REST. The single 5U replay stays as the site's 5U game.
- [ ] **Step 7:** Tell the user it is open; ask for the two date ranges and set `DISCOVER_RANGES` (`gcloud run services update --update-env-vars`).
- [ ] **Step 8:** Games already over when discovery starts are charted from their recordings (spec
  call 15): check each against the scoreboard in the stream before telling the user it is up, and
  correct misreads with the admin token.
