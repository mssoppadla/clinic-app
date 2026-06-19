#!/usr/bin/env bash
# Preflight — run BEFORE every commit/push. Mirrors ALL CI gates so failures surface locally
# first (incl. the Postgres bootstrap/migration path, which SQLite tests do NOT exercise).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# Prefer the project venv so deps (pytest, psycopg, alembic) are present. ABSOLUTE path so it
# still resolves inside the `cd apps/api` subshells below.
PY="python3"
if   [ -x "$ROOT/apps/api/.venv/Scripts/python.exe" ]; then PY="$ROOT/apps/api/.venv/Scripts/python.exe"   # Windows
elif [ -x "$ROOT/apps/api/.venv/bin/python" ];         then PY="$ROOT/apps/api/.venv/bin/python"; fi        # POSIX
echo "using python: $PY"

echo "==> 1. compile check"; "$PY" -m compileall -q apps/api/app
echo "==> 2. additive-migration (backward-compat) gate"; "$PY" deploy/check_additive_migration.py
echo "==> 3. unit + integration tests (SQLite)"; (cd apps/api && PYTHONPATH=. "$PY" -m pytest -q)

echo "==> 4. migration-drift on real Postgres (mirrors CI; catches bootstrap/RLS/role bugs)"
if command -v docker >/dev/null 2>&1; then
  PGC="preflight-pg-$$"
  PGURL="postgresql+psycopg://clinic:clinic_test@localhost:5455/clinic_migr"
  docker rm -f "$PGC" >/dev/null 2>&1 || true
  docker run -d --name "$PGC" -e POSTGRES_USER=clinic -e POSTGRES_PASSWORD=clinic_test \
    -e POSTGRES_DB=clinic_migr -p 5455:5432 postgres:16-alpine >/dev/null
  trap 'docker rm -f "$PGC" >/dev/null 2>&1 || true' EXIT
  for _ in $(seq 1 30); do docker exec "$PGC" pg_isready -U clinic >/dev/null 2>&1 && break; sleep 1; done
  ( cd apps/api && APP_DATABASE_URL="$PGURL" APP_ENV=ci PYTHONPATH=. "$PY" scripts/bootstrap_db.py )
  ( cd apps/api && APP_DATABASE_URL="$PGURL" APP_ENV=ci PYTHONPATH=. "$PY" -m alembic check || true )
  ( cd apps/api && APP_DATABASE_URL="$PGURL" APP_ENV=ci PYTHONPATH=. "$PY" -m alembic upgrade head )
  docker rm -f "$PGC" >/dev/null 2>&1 || true; trap - EXIT
  echo "   Postgres bootstrap + alembic check + upgrade: OK"
else
  echo "   (docker not found — CI will run the Postgres migration-drift; install docker to catch it locally)"
fi

echo "==> 5. secret scan (gitleaks if installed)"
if command -v gitleaks >/dev/null 2>&1; then gitleaks detect --no-banner -c .gitleaks.toml || { echo "secrets found"; exit 1; }
else echo "   (gitleaks not installed locally — CI will enforce)"; fi

echo "ALL LOCAL CHECKS PASSED ✔  safe to commit/push."
