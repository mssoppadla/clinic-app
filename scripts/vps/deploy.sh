#!/usr/bin/env bash
# On-VPS production deploy entrypoint (invoked over SSH by .github/workflows/deploy.yml).
# Mirrors cpmai scripts/vps/deploy.sh: data-preservation snapshot -> sync -> build ->
# bootstrap (migrations/seed run in the api container) -> health gate -> canary smoke ->
# data-preservation verify -> rollback on any failure. Co-hosted with cpmai: separate
# compose project + DB, so cpmai is never touched.
set -euo pipefail
cd "$(dirname "$0")/../.."                      # repo root
COMPOSE="docker compose --env-file .env -p clinic-saas -f deploy/docker-compose.yml"
[ -f .env ] && set -a && . ./.env && set +a
PREV="$(git rev-parse HEAD)"
GUARD_TABLES="tenants doctors patients bookings booking_events tokens queue_entries"

snapshot(){ for t in $GUARD_TABLES; do echo "$t=$($COMPOSE exec -T db psql -U "${POSTGRES_USER:-clinic}" -d "${POSTGRES_DB:-clinic}" -tAc "SELECT count(*) FROM $t" 2>/dev/null || echo NA)"; done; }
rollback(){
  trap - ERR                                   # don't re-enter rollback if rollback itself errors
  echo "!! deploy failed -> rolling back to $PREV"
  git reset --hard "$PREV"
  $COMPOSE up -d --build
  $COMPOSE up -d --force-recreate caddy        # re-resolve the bind-mounted Caddyfile (see [4b])
  echo "!! rolled back to $PREV"
  exit 1
}
# Any unexpected failure (sync/build/up/bootstrap-crash) -> roll back to the last good commit.
# Explicit '|| rollback' on the health/smoke/data checks below still apply (those are handled,
# so they don't trip this trap); this catches everything else.
trap rollback ERR

echo "[1/7] pre-deploy data-guard snapshot"
BEFORE="$(snapshot || true)"; echo "$BEFORE"

echo "[2/7] sync code (origin/main)"
git fetch --all --quiet && git reset --hard origin/main

echo "[3/7] build images"
$COMPOSE build

echo "[4/7] start (api container runs bootstrap_db + idempotent seed on boot)"
$COMPOSE up -d

# The Caddyfile is bind-mounted as a SINGLE FILE (./Caddyfile:/etc/caddy/Caddyfile).
# `git reset --hard` above replaces that file with a new inode, but Docker pins the file
# bind-mount to the original inode at container-create time -- so the running caddy (and
# `caddy reload`/`restart`, which re-read that same in-container path) keep serving the OLD
# config. Only RECREATING the container re-resolves the mount to the new file. `up -d` alone
# won't recreate caddy (its image/ports/volumes are unchanged), so force-recreate it.
echo "[4b/7] recreate caddy so the updated Caddyfile (new inode) is actually loaded"
$COMPOSE up -d --force-recreate caddy

echo "[5/7] health gate"
ok=0; for i in $(seq 1 40); do if curl -fsS http://localhost:8080/api/v1/healthz >/dev/null 2>&1; then ok=1; break; fi; sleep 3; done
[ "$ok" = "1" ] || rollback

echo "[6/7] canary smoke (book -> token -> queue -> idempotency)"
BASE_URL="http://localhost:8080/api/v1" python3 e2e/smoke.py || rollback

echo "[7/7] data-preservation verify (no row loss vs snapshot)"
fail=0
while IFS='=' read -r t before; do
  [ "$before" = "NA" ] && continue
  after="$($COMPOSE exec -T db psql -U "${POSTGRES_USER:-clinic}" -d "${POSTGRES_DB:-clinic}" -tAc "SELECT count(*) FROM $t" 2>/dev/null || echo NA)"
  if [ "$after" != "NA" ] && [ "$after" -lt "$before" ]; then echo "DATA LOSS on $t: $before -> $after"; fail=1; fi
done <<< "$BEFORE"
[ "$fail" = "0" ] || rollback

echo "OK - deploy complete and verified."
