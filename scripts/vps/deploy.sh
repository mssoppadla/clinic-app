#!/usr/bin/env bash
# On-VPS production deploy entrypoint (invoked over SSH by .github/workflows/deploy.yml).
# Mirrors cpmai scripts/vps/deploy.sh: data-preservation snapshot -> sync -> build ->
# bootstrap (migrations/seed run in the api container) -> health gate -> canary smoke ->
# data-preservation verify -> rollback on any failure. Co-hosted with cpmai: separate
# compose project + DB, so cpmai is never touched.
set -euo pipefail
cd "$(dirname "$0")/../.."                      # repo root
COMPOSE="docker compose -p clinic-saas -f deploy/docker-compose.yml"
[ -f .env ] && set -a && . ./.env && set +a
PREV="$(git rev-parse HEAD)"
GUARD_TABLES="tenants doctors patients bookings booking_events tokens queue_entries"

snapshot(){ for t in $GUARD_TABLES; do echo "$t=$($COMPOSE exec -T db psql -U "${POSTGRES_USER:-clinic}" -d "${POSTGRES_DB:-clinic}" -tAc "SELECT count(*) FROM $t" 2>/dev/null || echo NA)"; done; }
rollback(){ echo "!! deploy failed -> rolling back to $PREV"; git reset --hard "$PREV"; $COMPOSE up -d --build; exit 1; }

echo "[1/7] pre-deploy data-guard snapshot"
BEFORE="$(snapshot || true)"; echo "$BEFORE"

echo "[2/7] sync code (origin/main)"
git fetch --all --quiet && git reset --hard origin/main

echo "[3/7] build images"
$COMPOSE build

echo "[4/7] start (api container runs bootstrap_db + idempotent seed on boot)"
$COMPOSE up -d

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
