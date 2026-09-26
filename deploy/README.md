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
~2 minute warm run. `WORKER_CACHE_GB` prunes old media (never detections); keep
it comfortably under the free space on whatever volume holds the cache.

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

Note that only the download path in `ingest/cache.py` is wired to it. Metadata
(`ingest/source.py`), clip resolution (`harvest/clips.py`) and playlist
enumeration (`harvest/playlist.py`) each build their own yt-dlp options with no
token provider and no cookies, so a block that lands on one of those is not
fixed by any of this.

## Watching a league

```bash
curl -X POST -H "Authorization: Bearer $ADMIN_TOKEN" -H 'Content-Type: application/json' \
  -d '{"playlist_id":"https://www.youtube.com/playlist?list=PLxxxxxxxx","label":"Tuesday Open League",
       "schedule":[{"days":["tue"],"start":"18:00","end":"23:30"}]}' $PUBLIC_BASE_URL/api/admin/playlists
PUBLIC_BASE_URL=… ADMIN_TOKEN=… ./deploy/scheduler.sh     # poll every 3 min, 1 of 3 free Scheduler jobs
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
# or, in one process with everything in memory:
MEMORY_BACKENDS=1 WORKER_TOKEN=w ADMIN_TOKEN=a uvicorn curling_score.service.asgi:app --port 8080
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
