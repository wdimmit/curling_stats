#!/usr/bin/env bash
# Cloud Scheduler jobs (free tier: 3 per account): the playlist poll always,
# and the nightly review with REVIEW=1. The poll asks the watched playlists
# every three minutes; each playlist is only asked when its own schedule says
# it is due (hourly outside its league nights). Both use the admin
# token; the job's own OIDC identity is not checked by the API, so the token is
# what authorises it.
set -euo pipefail
: "${PROJECT_ID:?}"; : "${PUBLIC_BASE_URL:?}"; : "${ADMIN_TOKEN:?}"; REGION="${REGION:-us-west1}"
gcloud scheduler jobs create http curling-poll-playlists --project "$PROJECT_ID" --location "$REGION" \
  --schedule "*/3 * * * *" --time-zone "America/Los_Angeles" \
  --uri "${PUBLIC_BASE_URL}/api/admin/poll-playlists" --http-method POST \
  --headers "Authorization=Bearer ${ADMIN_TOKEN}" --attempt-deadline 120s \
  || gcloud scheduler jobs update http curling-poll-playlists --project "$PROJECT_ID" --location "$REGION" \
  --schedule "*/3 * * * *" --uri "${PUBLIC_BASE_URL}/api/admin/poll-playlists" \
  --update-headers "Authorization=Bearer ${ADMIN_TOKEN}"

# The nightly review (POST /api/admin/review), with REVIEW=1: hourly from
# 03:00 to 06:00, after the last league's live job has finished. Each call
# reads up to 25 games and stops starting new ones after 40 s, so a spiel
# day's 40 games take two calls and the later ones find nothing left. The
# second of the three free Scheduler jobs. `update` takes --update-headers,
# not --headers.
if [ "${REVIEW:-}" = "1" ]; then
  gcloud scheduler jobs create http curling-nightly-review --project "$PROJECT_ID" \
    --location "$REGION" --schedule "0 3-6 * * *" --time-zone "America/Los_Angeles" \
    --uri "${PUBLIC_BASE_URL}/api/admin/review" --http-method POST \
    --headers "Authorization=Bearer ${ADMIN_TOKEN}" --attempt-deadline 120s \
    || gcloud scheduler jobs update http curling-nightly-review --project "$PROJECT_ID" \
    --location "$REGION" --schedule "0 3-6 * * *" --time-zone "America/Los_Angeles" \
    --uri "${PUBLIC_BASE_URL}/api/admin/review" \
    --update-headers "Authorization=Bearer ${ADMIN_TOKEN}"
fi
