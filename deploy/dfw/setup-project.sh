#!/usr/bin/env bash
# One-time setup of DFW's own GCP project for dfwcurling.dimmit.net, following
# deploy/README.md "First-time setup". Safe to run again: each step that finds
# its thing already made says so and goes on.
#
#   PROJECT_ID=dfwcurling BILLING=01ED5C-D2A88C-57F6FD ./deploy/dfw/setup-project.sh
#
# Makes: the project (linked to BILLING), its APIs, Firestore (native, REGION)
# with deploy/firestore.indexes.json's indexes and TTLs, the private bucket, the
# API's service account, the four secrets (tokens generated here, never
# printed; the YouTube key an API key made in this project for the YouTube
# Data API alone) and the image registry. Accounts stay off: no Firebase.
set -uo pipefail
: "${PROJECT_ID:?set PROJECT_ID}" "${BILLING:?set BILLING (a billing account id)}"
REGION="${REGION:-us-west1}"
SA="curling-chart@${PROJECT_ID}.iam.gserviceaccount.com"
G=(--project "$PROJECT_ID" --quiet)
say() { printf '== %s\n' "$*"; }
ok() { "$@" >/dev/null 2>&1 && return 0; "$@" 2>&1 | grep -qiE "already exists|ALREADY_EXISTS" && { echo "   (already there)"; return 0; }; "$@"; }

say "project $PROJECT_ID"
gcloud projects describe "$PROJECT_ID" >/dev/null 2>&1 || gcloud projects create "$PROJECT_ID" --quiet || exit 1
gcloud billing projects link "$PROJECT_ID" --billing-account "$BILLING" --quiet >/dev/null || exit 1

say "APIs"
gcloud services enable run.googleapis.com firestore.googleapis.com storage.googleapis.com \
  cloudbuild.googleapis.com artifactregistry.googleapis.com cloudscheduler.googleapis.com \
  secretmanager.googleapis.com youtube.googleapis.com apikeys.googleapis.com "${G[@]}" || exit 1

say "Firestore"
ok gcloud firestore databases create --location="$REGION" "${G[@]}"
python3 - "$PROJECT_ID" <<'PY' | while read -r line; do eval "$line" || true; done
import json, shlex, sys
proj = sys.argv[1]
d = json.load(open("deploy/firestore.indexes.json"))
for ix in d["indexes"]:
    fields = " ".join(f"--field-config=field-path={f['fieldPath']},order={f['order'].lower()}"
                      for f in ix["fields"])
    print(f"gcloud firestore indexes composite create --project {proj} --quiet --async "
          f"--collection-group={ix['collectionGroup']} {fields} >/dev/null 2>&1")
for o in d.get("fieldOverrides", []):
    if o.get("ttl"):
        print(f"gcloud firestore fields ttls update {o['fieldPath']} --project {proj} --quiet --async "
              f"--collection-group={o['collectionGroup']} --enable-ttl >/dev/null 2>&1")
    elif o.get("indexes") == []:
        print(f"gcloud firestore fields update --project {proj} --quiet --async "
              f"--collection-group={o['collectionGroup']} --field-path={o['fieldPath']} "
              f"--disable-indexes >/dev/null 2>&1")
PY

say "bucket"
BUCKET="gs://curling-chart-${PROJECT_ID}"
ok gcloud storage buckets create "$BUCKET" --location="$REGION" --uniform-bucket-level-access "${G[@]}"
LC="$(mktemp)"; echo '{"rule":[{"action":{"type":"Delete"},"condition":{"age":365,"matchesPrefix":["detcache/"]}}]}' > "$LC"
gcloud storage buckets update "$BUCKET" --lifecycle-file="$LC" "${G[@]}" >/dev/null; rm -f "$LC"

say "service account"
ok gcloud iam service-accounts create curling-chart "${G[@]}"
gcloud projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:$SA" \
  --role=roles/datastore.user --condition=None --quiet >/dev/null
gcloud storage buckets add-iam-policy-binding "$BUCKET" --member="serviceAccount:$SA" \
  --role=roles/storage.objectAdmin "${G[@]}" >/dev/null
gcloud iam service-accounts add-iam-policy-binding "$SA" --member="serviceAccount:$SA" \
  --role=roles/iam.serviceAccountTokenCreator "${G[@]}" >/dev/null

say "secrets"
for s in curling-worker-token curling-admin-token curling-ip-salt; do
  if gcloud secrets describe "$s" "${G[@]}" >/dev/null 2>&1; then echo "   $s (already there)"; continue; fi
  python3 -c 'import secrets;print(secrets.token_hex(32),end="")' | gcloud secrets create "$s" --data-file=- "${G[@]}" >/dev/null
done
if ! gcloud secrets describe curling-yt-api-key "${G[@]}" >/dev/null 2>&1; then
  gcloud services api-keys create --display-name="youtube data api" \
    --api-target=service=youtube.googleapis.com "${G[@]}" --format='value(response.keyString)' 2>/dev/null \
    | tr -d '\n' | gcloud secrets create curling-yt-api-key --data-file=- "${G[@]}" >/dev/null
fi
for s in curling-worker-token curling-admin-token curling-ip-salt curling-yt-api-key; do
  gcloud secrets add-iam-policy-binding "$s" --member="serviceAccount:$SA" \
    --role=roles/secretmanager.secretAccessor "${G[@]}" >/dev/null
done

say "image registry"
ok gcloud artifacts repositories create curling --repository-format=docker --location="$REGION" "${G[@]}"
gcloud artifacts repositories set-cleanup-policies curling --location="$REGION" \
  --policy=deploy/artifact-cleanup.json "${G[@]}" >/dev/null

say "done: $PROJECT_ID (indexes and TTLs build in the background: gcloud firestore indexes composite list --project $PROJECT_ID)"
