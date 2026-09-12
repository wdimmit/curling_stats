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

## Watching a league

```bash
curl -X POST -H "Authorization: Bearer $ADMIN_TOKEN" -H 'Content-Type: application/json' \
  -d '{"playlist_id":"PLxxxxxxxx","label":"Tuesday Open League"}' $PUBLIC_BASE_URL/api/admin/playlists
PUBLIC_BASE_URL=… ADMIN_TOKEN=… ./deploy/scheduler.sh     # hourly poll, 1 of 3 free Scheduler jobs
curl -X POST -H "Authorization: Bearer $ADMIN_TOKEN" $PUBLIC_BASE_URL/api/admin/poll-playlists   # backfill now (10 per call)
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
