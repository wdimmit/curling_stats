#!/usr/bin/env bash
# Build and deploy the API to Cloud Run. First-time setup is in deploy/README.md.
set -euo pipefail
: "${PROJECT_ID:?set PROJECT_ID}"; REGION="${REGION:-us-west1}"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/curling/api:$(git rev-parse --short HEAD)"
gcloud builds submit --project "$PROJECT_ID" --tag "$IMAGE" --file Dockerfile.api . 2>/dev/null \
  || gcloud builds submit --project "$PROJECT_ID" --config <(cat <<EOF
steps:
  - name: gcr.io/cloud-builders/docker
    args: [build, -f, Dockerfile.api, -t, "$IMAGE", .]
images: ["$IMAGE"]
EOF
) .
sed -e "s/PROJECT_ID/${PROJECT_ID}/g" -e "s/REGION-docker/${REGION}-docker/g" \
    -e "s#api:latest#api:$(git rev-parse --short HEAD)#" deploy/cloudrun.yaml > /tmp/cloudrun.yaml
gcloud run services replace /tmp/cloudrun.yaml --project "$PROJECT_ID" --region "$REGION"
gcloud run services add-iam-policy-binding curling-chart --project "$PROJECT_ID" --region "$REGION" \
  --member=allUsers --role=roles/run.invoker >/dev/null
gcloud run services describe curling-chart --project "$PROJECT_ID" --region "$REGION" --format='value(status.url)'
