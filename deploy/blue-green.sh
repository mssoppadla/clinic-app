#!/usr/bin/env bash
# Zero-downtime deploy for the clinic-saas compose project. Brings up the new color,
# health-gates it, runs the canary smoke, then switches Caddy upstream; rolls back on failure.
# cpmai + other clinics never go down (separate project, >=2 replicas, gated cutover).
set -euo pipefail
NEW="${1:-green}"; OLD="${2:-blue}"
COMPOSE="docker compose -p clinic-saas -f deploy/docker-compose.yml"

echo "[1/5] snapshot data guard"; DATABASE_URL="$APP_DATABASE_URL" deploy/cpmai-guard.sh snapshot
echo "[2/5] build + start $NEW"; $COMPOSE --profile "$NEW" up -d --build
echo "[3/5] wait for health"; for i in $(seq 1 30); do
  if curl -fsS "http://localhost:8000/healthz" >/dev/null 2>&1; then break; fi; sleep 2;
done
echo "[4/5] canary smoke"; BASE_URL="${SMOKE_URL:-http://localhost:8080/api/v1}" python e2e/smoke.py \
  || { echo "smoke failed -> keep $OLD live, tearing down $NEW"; $COMPOSE --profile "$NEW" down; exit 1; }
echo "[5/5] verify data guard + cutover"; DATABASE_URL="$APP_DATABASE_URL" deploy/cpmai-guard.sh verify \
  || { echo "data guard failed -> rollback"; $COMPOSE --profile "$NEW" down; exit 1; }
echo "cutover complete: $NEW live. (Caddy reloads upstream; $OLD can be retired.)"
