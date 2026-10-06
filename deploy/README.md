# Deploying Curling Chart

One GCP project holds the API (Cloud Run), the metadata (Firestore) and the
blobs (Cloud Storage). Processing runs on a home machine with a GPU, pulling
jobs from the API over HTTPS — it opens no ports. Everything in the cloud sits
inside free tiers at club scale; the recurring costs are the domain and the
home box's electricity.

## First-time setup (once)

```bash
export PROJECT_ID=your-project REGION=us-west1
gcloud config set project $PROJECT_ID
gcloud services enable run.googleapis.com firestore.googleapis.com storage.googleapis.com \
  cloudbuild.googleapis.com artifactregistry.googleapis.com cloudscheduler.googleapis.com \
  secretmanager.googleapis.com youtube.googleapis.com

# Firestore (Native mode) + indexes + deny-all rules
gcloud firestore databases create --location=$REGION
gcloud firestore indexes composite create --collection-group=jobs \
  --field-config=field-path=state,order=ascending --field-config=field-path=run_after,order=ascending \
  --field-config=field-path=created_at,order=ascending
# Live jobs are claimed first: the same query with kind == "live".
gcloud firestore indexes composite create --collection-group=jobs \
  --field-config=field-path=state,order=ascending --field-config=field-path=kind,order=ascending \
  --field-config=field-path=run_after,order=ascending --field-config=field-path=created_at,order=ascending
gcloud firestore indexes composite create --collection-group=jobs \
  --field-config=field-path=state,order=ascending --field-config=field-path=lease_expires_at,order=ascending
gcloud firestore indexes composite create --collection-group=jobs \
  --field-config=field-path=run_id,order=ascending --field-config=field-path=created_at,order=descending
gcloud firestore indexes composite create --collection-group=vod_runs \
  --field-config=field-path=video_id,order=ascending --field-config=field-path=created_at,order=descending
gcloud firestore indexes composite create --collection-group=vod_runs \
  --field-config=field-path=status,order=ascending --field-config=field-path=created_at,order=descending
# Flags: newest first within a status. Create it before deploying the API
# that lists them, or the admin list fails until the index has built.
gcloud firestore indexes composite create --collection-group=flags \
  --field-config=field-path=status,order=ascending --field-config=field-path=created_at,order=descending
gcloud firestore fields ttls update expires_at --collection-group=rate_limits --enable-ttl
gcloud firestore fields ttls update expires_at --collection-group=invites --enable-ttl
# Two maps nothing ever queries. Left alone, Firestore indexes every subfield
# of both -- thousands of entries per charted game, against a 40,000-per-
# document ceiling -- purely to pay for writes nobody reads.
gcloud firestore fields update --collection-group=charts --field-path=overrides --disable-indexes
gcloud firestore fields update --collection-group=charts --field-path=overrides_meta --disable-indexes
# (or: firebase deploy --only firestore with deploy/firestore.indexes.json + firestore.rules)
#
# No composite index is needed for accounts: every query they add is one
# equality filter with the ordering done in Python, which the automatic
# single-field indexes already cover.

# Accounts (optional -- skip all of this and the site simply has none).
# Identity Platform, the Google provider, and the domains a sign-in may come
# from. The web API key is a public identifier, not a secret: it belongs in
# cloudrun.yaml next to GCP_PROJECT, and the Authorized domains list is what
# actually limits it.
gcloud services enable identitytoolkit.googleapis.com
# Then, in the Firebase console for this project:
#   Authentication > Sign-in method  > enable Google
#   Authentication > Settings > Authorized domains > add chart.example.org,
#     and localhost too if you want sign-in to work against a local run --
#     an Identity Platform project does not necessarily have it already
#   Project settings > General > Your apps > register a Web app, copy its
#     apiKey. A project can have Identity Platform switched on and still have
#     no web app, in which case there is no apiKey to configure yet.
# Nothing new is needed in Secret Manager or IAM: verifying an ID token uses
# only Google's public certificates and the project id.

# Bucket: private, with a lifecycle rule that expires detection caches after a year
gsutil mb -l $REGION gs://curling-chart-$PROJECT_ID
echo '{"rule":[{"action":{"type":"Delete"},"condition":{"age":365,"matchesPrefix":["detcache/"]}}]}' > /tmp/lc.json
gsutil lifecycle set /tmp/lc.json gs://curling-chart-$PROJECT_ID

# Service account for the API: Firestore, the bucket, and the right to sign URLs as itself
gcloud iam service-accounts create curling-chart
SA=curling-chart@$PROJECT_ID.iam.gserviceaccount.com
gcloud projects add-iam-policy-binding $PROJECT_ID --member=serviceAccount:$SA --role=roles/datastore.user
gsutil iam ch serviceAccount:$SA:objectAdmin gs://curling-chart-$PROJECT_ID
gcloud iam service-accounts add-iam-policy-binding $SA --member=serviceAccount:$SA \
  --role=roles/iam.serviceAccountTokenCreator

# Secrets
for s in curling-worker-token curling-admin-token curling-ip-salt; do
  # printf, not print: a trailing newline lands in the secret, and the client's
  # shell strips it in command substitution -- the two then never match.
  python -c 'import secrets;print(secrets.token_hex(32),end="")' | gcloud secrets create $s --data-file=-
done
# YouTube Data API key: APIs & Services → Credentials → API key, restricted to the YouTube Data API v3
echo -n "$YT_API_KEY" | gcloud secrets create curling-yt-api-key --data-file=-
for s in curling-worker-token curling-admin-token curling-ip-salt curling-yt-api-key; do
  gcloud secrets add-iam-policy-binding $s --member=serviceAccount:$SA --role=roles/secretmanager.secretAccessor
done

# Artifact Registry for the image
gcloud artifacts repositories create curling --repository-format=docker --location=$REGION
```

Edit `deploy/cloudrun.yaml`: `PUBLIC_BASE_URL` (your domain), `MODEL_ID`
(`python -c 'from curling_score import weights, version; print(version.model_id(weights.default_path()))'`
— it must match the worker image's `MODEL`, or the API refuses every claim).

## Deploy the API

```bash
./deploy/deploy-api.sh                       # prints the *.run.app URL
gcloud run domain-mappings create --service curling-chart --domain chart.example.org --region $REGION
# add the DNS records it prints; TLS is managed
```

Everything the service runs on is in `cloudrun.yaml`, accounts included, and a
bare `deploy-api.sh` reproduces what is live. That is deliberate: `gcloud run
services replace` is declarative, so anything the file does not say is removed
from the service. The three Firebase values used to be filled from the
deployer's shell and blanked when it was silent, and on 2026-09-13 a deploy
without them switched sign-in off site-wide for thirteen hours. Config for a
live feature cannot live only in a shell.

The one piece that is not in the repo is the Firebase `apiKey`: drop the
snippet the Firebase console gives you into `firebase_api_key.json`, which is
gitignored, and the deploy reads it from there. Public identifier or not, it is
one deployment's config rather than source.

To deploy against a different project, `FIREBASE_PROJECT=… FIREBASE_API_KEY=…`
override both. To run without accounts at all, `ACCOUNTS=off` — and only that:
without a key and without that flag the deploy stops before it starts, and
afterwards it reads `/api/auth/config` back from the service it just deployed
and fails if sign-in went away without being asked to.

## The home worker

On the GPU machine (Docker + nvidia-container-toolkit):

```bash
git clone … && cd curling_score
cp deploy/worker.env.example deploy/worker.env   # API_URL, WORKER_TOKEN (same secret as the API)
mkdir -p /data/wdd/curling-cache          # or wherever you have a few hundred GB
docker compose -f deploy/docker-compose.worker.yml up -d --build
docker compose -f deploy/docker-compose.worker.yml logs -f
```

The worker polls `/api/worker/claim` (10 s when busy, 30 s idle) and heartbeats
once a minute. If the box reboots mid-job the lease expires after 10 minutes
and the job is requeued; the caches under `/data/wdd/curling-cache` make the retry a
~2 minute warm run. `WORKER_CACHE_GB` prunes old media (never detections): kept
live recordings first, oldest kept first, then other media least recently read
first. `WORKER_MIN_FREE_GB` (default 40) also lets kept live recordings go while
the disk has less than that free -- never a download, so the harness videos are
safe from it. A live stream's recording, once the stream
has ended, is kept in the same cache as if it were a download, so reprocessing
the game needs no new one.

Live recordings are kept for `WORKER_RECORDING_DAYS` (default 7) once their stream
ends: a whole one as `videos/<id>.mp4`, one cut short (cap, stall, failed exit) as
`kept/<id>.ts`, never as the video. Each has a marker, `kept/<id>.json`. A whole
recording replays like a download. To replay a partial one, remux it into a
scratch cache (`ffmpeg -i kept/<id>.ts -map 0:v:0 -c copy <scratch>/videos/<id>.mp4`)
and give `scripts/replay_end.py` that `--cache-root`. `worker2`'s 40 GB budget holds
only a few nights; raise it on a box with the room.

The compose file brings up a `pot` sidecar alongside the worker, and the image
carries the matching `bgutil-ytdlp-pot-provider` plugin. Together they mint the
proof-of-origin tokens YouTube asks for before it will serve a video; without
them a download fails with "Sign in to confirm you're not a bot" and
`ensure_cached` waits out 5, 10 and 20 minutes before giving up. Both are pinned
to 2.0.0 and must move together -- they refuse each other across a major bump.
To check the sidecar is being used rather than merely running:

```bash
docker compose -f deploy/docker-compose.worker.yml logs pot | tail   # a line per token minted
```

### Following live games

With `WORKER_LIVE=1` in `worker.env` the worker also follows live streams (the
API queues them when `LIVE_ENABLED=1` there -- see "Watching a league"). A
thread claims live jobs, up to `LIVE_MAX_STREAMS` at once (default 6), and
records each with yt-dlp from the stream's first segment into
`/data/cache/live/<video>/rec.N.ts`; the main loop builds every end as it
settles and publishes it, oldest end first across the streams, and claims no
recording while any live stream is in hand. A recording already being
processed when a stream goes live is handed back (`/yield`) and resumes from
its detection cache afterwards.

- Each stream is ~2.5 Mbps; five need ~13 Mbps down and ~6 GB of disk an hour,
  deleted when the stream completes (and at worker start).
- The stream has to be recorded from its first segment, which YouTube's live
  playlist keeps only for about the first hour of a stream. A stream noticed
  later, or a recorder that drops out past that point, fails its live job, and
  the poller queues the recording the ordinary way once it is archived.
- A worker restart is the same: nothing of a live stream is kept across one
  (the `live/` recordings are cleared at start). Within a stream's first hour
  its job comes back when the lease runs out and is recorded and built again
  from the start; past it, the job fails and the recording's ordinary run
  takes the game. Pages keep showing what was already published.
- Roll the API back only with no live job queued: an API from before live
  jobs would hand one to a worker as a recording.
- Calibration comes from the first 15 minutes and is repeated every 15 until
  two agree; an end is published about four minutes after the next end's
  first stone, plus the time it takes to build.

Note that only the download path in `ingest/cache.py` is wired to it. Metadata
(`ingest/source.py`), clip resolution (`harvest/clips.py`) and playlist
enumeration (`harvest/playlist.py`) each build their own yt-dlp options with no
token provider and no cookies, so a block that lands on one of those is not
fixed by any of this.

### Two workers on one box

The pipeline spends most of an end on one CPU thread, with the GPU idle ~75% of
the time, so the compose file runs a second worker (`worker2`) on the same GPU.
Two build ~1.55x the ends an hour; each end takes ~25% longer while both are
building (measured on the 3070 laptop, 2026-10-02).

- `worker2` has its own cache, `/data/wdd/curling-cache-2`. Sharing one breaks:
  a live worker empties `<cache>/live` when it starts, the pruner can delete
  media the other worker is reading, and a job uploads every new
  `detections/*.npz` as its own. The cost is that a recording `worker2` takes
  is downloaded again into its cache.
- Each has its own `WORKER_ID`; the API tells workers' jobs apart by it alone.
- Each follows at most `LIVE_MAX_STREAMS: "3"` live streams. A worker claims
  them greedily, so a larger limit would let one of them take a whole league.
- `up -d` recreates both, and both drop their live jobs (see above).
  `docker compose ... logs -f worker worker2` follows both.

## Watching a league

```bash
curl -X POST -H "Authorization: Bearer $ADMIN_TOKEN" -H 'Content-Type: application/json' \
  -d '{"playlist_id":"https://www.youtube.com/playlist?list=PLxxxxxxxx","label":"Tuesday Open League",
       "schedule":[{"days":["tue"],"start":"18:00","end":"23:30"}]}' $PUBLIC_BASE_URL/api/admin/playlists
PUBLIC_BASE_URL=… ADMIN_TOKEN=… ./deploy/scheduler.sh     # poll every 3 min, 1 of 3 free Scheduler jobs
REVIEW=1 PUBLIC_BASE_URL=… ADMIN_TOKEN=… ./deploy/scheduler.sh   # + the nightly review, 2 of 3
curl -X POST -H "Authorization: Bearer $ADMIN_TOKEN" "$PUBLIC_BASE_URL/api/admin/poll-playlists?force=1"  # every playlist now (10 new per call)
curl -X PATCH -H "Authorization: Bearer $ADMIN_TOKEN" -H 'Content-Type: application/json' \
  -d '{"schedule":[{"days":["thu"],"start":"18:00","end":"23:30"}],"live_poll_s":180,"idle_poll_s":3600}' \
  $PUBLIC_BASE_URL/api/admin/playlists/p_xxxxxx                        # also label, enabled
```

The scheduler calls the poll every three minutes; each playlist is only asked
when its own schedule says it is due. Inside one of its windows (the club's
time, `CLUB_TZ`, default America/Los_Angeles; a window ending before it starts
runs past midnight) that is every `live_poll_s` (default 180), so a stream is
seen within minutes of going live. Outside, every `idle_poll_s` (default 3600,
or `null` for never). The channel index for the thinking report still runs
once an hour, on the first poll of the hour.

With `LIVE_ENABLED=1` on the API, a stream that has gone live in a watched
playlist is queued as a **live job**: it skips approval, and a worker follows
the game as it is played, publishing each end as it finishes. Leave it off
until a worker that can follow a stream is running -- a live run nobody claims
stands in for the recording, which the poller then never queues the ordinary
way. (One still queued when its recording is archived gives way to it.) The
doubles playlist stays out of the poller entirely.

## The nightly review

Every game whose run finished in the last 3 days is read once for signs that
something went wrong (`curling_score/autoreview.py`): an end cut in two, lost
rocks, a fragment of a game, an end whose brooms/splits/lines/releases fall
well below the last fortnight's normal, a broom off the sheet, odd splits. A
game showing one strong sign (or two weak ones) gets one ⚑ flag with
`origin: "auto"`, in the same list as viewers' flags:

```
ADMIN_TOKEN=… python scripts/flags.py list --origin auto
```

Each finding prints with a link to its rock. Nothing is resolved or
reprocessed automatically. What it read is kept per game and run in the
`reviews` collection, which is also where "normal" comes from.

By hand (all take the admin token):

```
curl -X POST -H "Authorization: Bearer $ADMIN_TOKEN" "$PUBLIC_BASE_URL/api/admin/review?dry_run=true"   # what it would flag
curl -X POST -H "Authorization: Bearer $ADMIN_TOKEN" "$PUBLIC_BASE_URL/api/admin/review?source_id=s_…"  # read one game again
curl -X POST -H "Authorization: Bearer $ADMIN_TOKEN" "$PUBLIC_BASE_URL/api/admin/review?since=2026-09-19T00:00:00Z&until=2026-10-02T00:00:00Z&flag=false"  # seed the baseline, leaving the last 3 days
```

Repeat a call until `pending` is 0. To tune the checks, save served timelines
(`/g/<source_id>/timeline.json` as `<source_id>.json`) into a folder and run
`python scripts/review.py DIR --any`.

## The thinking report

`/thinking` ranks each league's games by thinking time per end, and by how
lopsided the two teams' split was. A league is the YouTube playlist the club
filed the stream in, found by indexing the channel's playlists. The hourly
poll above keeps that index current, reading at most 40 playlists per call.
Each game's thinking totals are kept on its source, written when the run
completes.

After the first deploy, and after restoring a backup, which leaves the index
out because it can be rebuilt, fill both. Repeat each call until it reports
`pending: 0` or `remaining: 0`:

```bash
curl -X POST -H "Authorization: Bearer $ADMIN_TOKEN" $PUBLIC_BASE_URL/api/admin/playlist-index      # {scanned, pending, stamped}
curl -X POST -H "Authorization: Bearer $ADMIN_TOKEN" $PUBLIC_BASE_URL/api/admin/backfill-thinking   # {refreshed, skipped, remaining}
```

## Backups

Nightly on the home box (cron):

```
0 4 * * * curl -sf -H "Authorization: Bearer $ADMIN_TOKEN" $PUBLIC_BASE_URL/api/admin/export > /srv/curling-backups/$(date +\%F).json
```

Restore with `python -m curling_score.service.restore backup.json` (env as for the API).

## Local, no cloud

```bash
docker compose -f deploy/docker-compose.local.yml up --build     # Firestore emulator + API + worker
# or, in one process with everything in memory. MODEL_ID must be the model a
# worker will claim with, or the API refuses it (409 "model mismatch"):
MEMORY_BACKENDS=1 WORKER_TOKEN=w ADMIN_TOKEN=a \
  MODEL_ID=$(python -c 'from curling_score import weights, version; print(version.model_id(weights.default_path()))') \
  uvicorn curling_score.service.asgi:app --port 8080
```

## What the terms say

This service downloads the video from YouTube with yt-dlp in order to analyse
it. Downloading YouTube content is against YouTube's Terms of Service, and
YouTube actively blocks automated downloads. We do this from a single
residential connection belonging to the operator, only for videos from the
club's own channel, and keep no copy of the video beyond what processing needs.
YouTube may at any time block that connection or the account associated with
it, in which case processing stops until it is unblocked; already-processed
charts keep working because the viewer plays the video through YouTube's own
embedded player.

The compliant alternatives both need the channel owner: recording at the
rink's encoder alongside the stream (automatic, full quality), or Google Takeout
of the originals. YouTube Studio's own download is capped at 720p, below what
detection needs. If the club wants in, the worker's download phase becomes
"fetch from the club's drop folder" and nothing else changes.
