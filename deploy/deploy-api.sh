#!/usr/bin/env bash
# Build the API image with Cloud Build and roll it out to Cloud Run.
#
# The repository holds two Dockerfiles, and `builds submit --tag` only ever
# uses one at the root, so the build is driven by an explicit config.
#
#   PROJECT_ID=... [REGION=us-west1] [PUBLIC_BASE_URL=https://...] ./deploy/deploy-api.sh
#
# PUBLIC_BASE_URL may be omitted on a first deploy: the links the API hands out
# are then relative, which the browser resolves correctly, and you can set it
# once Cloud Run has told you the URL.
#
# Accounts are off unless FIREBASE_PROJECT and FIREBASE_API_KEY are set, and
# the site is complete without them -- every chart link works signed out. Set
# both to turn on sign-in and teams (FIREBASE_AUTH_DOMAIN defaults to
# <project>.firebaseapp.com):
#
#   PROJECT_ID=... FIREBASE_PROJECT=... FIREBASE_API_KEY=AIza... ./deploy/deploy-api.sh
set -euo pipefail
: "${PROJECT_ID:?set PROJECT_ID}"
REGION="${REGION:-us-west1}"
TAG="$(git rev-parse --short HEAD)$(git diff --quiet || echo -dirty)"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/curling/api:${TAG}"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
cat > "$WORK/cloudbuild.yaml" <<EOF
steps:
  - name: gcr.io/cloud-builders/docker
    args: [build, -f, Dockerfile.api, -t, "${IMAGE}", .]
images: ["${IMAGE}"]
options:
  logging: CLOUD_LOGGING_ONLY
EOF

echo "building ${IMAGE}"
gcloud builds submit --project "$PROJECT_ID" --region "$REGION" \
  --config "$WORK/cloudbuild.yaml" .

MODEL_ID="${MODEL_ID:-$(python3 -c 'import sys; sys.path.insert(0,"src"); from curling_score import weights, version; print(version.model_id(weights.default_path()))' 2>/dev/null || echo classical)}"
echo "deploying (model ${MODEL_ID})"
sed -e "s|PROJECT_ID|${PROJECT_ID}|g" \
    -e "s|REGION-docker|${REGION}-docker|g" \
    -e "s|api:latest|api:${TAG}|" \
    -e "s|value: \"ds11a-27fe3faa\"|value: \"${MODEL_ID}\"|" \
    -e "s|https://chart.example.org|${PUBLIC_BASE_URL:-}|" \
    -e "s|FB_PROJECT|${FIREBASE_PROJECT:-}|" \
    -e "s|FB_API_KEY|${FIREBASE_API_KEY:-}|" \
    -e "s|FB_AUTH_DOMAIN|${FIREBASE_AUTH_DOMAIN:-${FIREBASE_PROJECT:+${FIREBASE_PROJECT}.firebaseapp.com}}|" \
    deploy/cloudrun.yaml > "$WORK/service.yaml"
gcloud run services replace "$WORK/service.yaml" --project "$PROJECT_ID" --region "$REGION"

# The site is public; the worker and admin surfaces are guarded by their tokens.
gcloud run services add-iam-policy-binding curling-chart \
  --project "$PROJECT_ID" --region "$REGION" \
  --member=allUsers --role=roles/run.invoker >/dev/null

URL=$(gcloud run services describe curling-chart --project "$PROJECT_ID" \
        --region "$REGION" --format='value(status.url)')
echo "deployed: $URL"
[ -n "${FIREBASE_PROJECT:-}" ] && [ -n "${FIREBASE_API_KEY:-}" ] \
  || echo "note: accounts are off (set FIREBASE_PROJECT and FIREBASE_API_KEY to enable sign-in)"
[ -n "${PUBLIC_BASE_URL:-}" ] || echo "note: PUBLIC_BASE_URL is unset; re-run with PUBLIC_BASE_URL=$URL to bake absolute links"
