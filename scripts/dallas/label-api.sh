#!/bin/sh
# Local label server for DFW sets: the member /label page against memory backends
# (state dies with the container: label-snapshot.py keeps the answers).
#
# Sign-in is the Firebase Auth Emulator (container dfw-auth-emu, :9099, project
# demo-dfw), not the production project: production's authorised domains do not
# include localhost, so Google sign-in there fails with auth/unauthorized-domain.
# The emulator's "Google" sign-in is a local account chooser; it touches no real
# project, and the server does not check its tokens' signatures (it says so).
# PROD_AUTH=1 uses the production project's public config instead.
#
# Browse http://localhost:8797/label through a tunnel that carries both ports:
#   ssh -N -L 8797:localhost:8797 -L 9099:localhost:9099 administrator@100.80.66.81
set -e
cd "$(dirname "$0")"
umask 077
[ -s admin_token ] || head -c 32 /dev/urandom | base64 | tr -d '=+/\n' > admin_token
[ -s worker_token ] || head -c 32 /dev/urandom | base64 | tr -d '=+/\n' > worker_token
umask 022
if [ "${PROD_AUTH:-}" = 1 ]; then
  CFG=$(curl -fsS https://curling.dimmit.net/api/auth/config)
  field() { printf '%s' "$CFG" | python3 -c "import json,sys; print(json.load(sys.stdin)['$1'])"; }
  set -- -e FIREBASE_PROJECT="$(field projectId)" -e FIREBASE_API_KEY="$(field apiKey)" \
         -e FIREBASE_AUTH_DOMAIN="$(field authDomain)"
else
  set -- -e FIREBASE_PROJECT=demo-dfw -e FIREBASE_API_KEY=demo-key -e FIREBASE_AUTH_DOMAIN=localhost \
         -e FIREBASE_AUTH_EMULATOR_HOST=localhost:9099
fi
docker rm -f dfw-label-api >/dev/null 2>&1 || true
docker run -d --name dfw-label-api --network host \
  -v /data/wdd/scratch/dallas:/w:ro -e PYTHONPATH=/w/code/src \
  -e MEMORY_BACKENDS=1 -e YOUTUBE=fake -e PORT=8797 -e PROXY_HOPS=0 \
  -e PUBLIC_BASE_URL=http://localhost:8797 \
  -e ADMIN_TOKEN="$(cat admin_token)" -e WORKER_TOKEN="$(cat worker_token)" \
  -e AUTH=firebase "$@" \
  curling-api:ardsley-yt >/dev/null
echo "started dfw-label-api on :8797"
