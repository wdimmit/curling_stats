#!/usr/bin/env bash
# One Cloud Scheduler job (free tier: 3 per account) that polls the watched
# playlists hourly. Uses the admin token; the job's own OIDC identity is not
# checked by the API, so the token is what authorises it.
set -euo pipefail
: "${PROJECT_ID:?}"; : "${PUBLIC_BASE_URL:?}"; : "${ADMIN_TOKEN:?}"; REGION="${REGION:-us-west1}"
gcloud scheduler jobs create http curling-poll-playlists --project "$PROJECT_ID" --location "$REGION" \
  --schedule "17 * * * *" --time-zone "America/Los_Angeles" \
  --uri "${PUBLIC_BASE_URL}/api/admin/poll-playlists" --http-method POST \
  --headers "Authorization=Bearer ${ADMIN_TOKEN}" --attempt-deadline 120s \
  || gcloud scheduler jobs update http curling-poll-playlists --project "$PROJECT_ID" --location "$REGION" \
  --schedule "17 * * * *" --uri "${PUBLIC_BASE_URL}/api/admin/poll-playlists" \
  --headers "Authorization=Bearer ${ADMIN_TOKEN}"
