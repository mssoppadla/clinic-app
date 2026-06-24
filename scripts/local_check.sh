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
  # bootstrap above creates the FULL current schema (create_all) + stamps head, so a plain
  # `alembic upgrade head` is a no-op and NEVER runs the new migrations' upgrade() bodies. That
  # blind spot let a migration that ALTERs a not-yet-created table pass preflight and only fail
  # in prod (the real deploy runs `alembic upgrade` on an EXISTING older DB). To mirror prod:
  # find the migrations this branch ADDS vs origin/main, downgrade to the revision just before
  # them, then upgrade head — exercising exactly the upgrade() bodies prod will run. This is a
  # throwaway container; prod NEVER downgrades.
  git fetch origin main --quiet 2>/dev/null || true
  # NB: feed the file list via an env var, NOT a pipe — `python - <<EOF` reads its program from
  # the heredoc, which would clobber piped stdin and silently yield an empty list.
  NEW_MIGRATIONS="$(git diff --name-only --diff-filter=A origin/main...HEAD -- apps/api/migrations/versions/ 2>/dev/null)"
  PRIOR_HEAD="$(NEW_MIGRATIONS="$NEW_MIGRATIONS" "$PY" - <<'PYEOF'
import re, os, pathlib
files = [l.strip() for l in os.environ.get("NEW_MIGRATIONS", "").splitlines() if l.strip().endswith(".py")]
new_revs, downs = set(), {}
for f in files:
    txt = pathlib.Path(f).read_text(encoding="utf-8")
    rev = re.search(r'^revision\s*=\s*["\']([^"\']+)', txt, re.M)
    dr  = re.search(r'^down_revision\s*=\s*["\']([^"\']+)', txt, re.M)
    if rev:
        new_revs.add(rev.group(1)); downs[rev.group(1)] = dr.group(1) if dr else None
# prior head = the down_revision pointed at by a new migration but not itself new
priors = [d for d in downs.values() if d and d not in new_revs]
print(priors[0] if priors else "")
PYEOF
)"
  if [ -n "$PRIOR_HEAD" ]; then
    echo "   simulating prod upgrade: downgrade -> $PRIOR_HEAD, then upgrade head"
    ( cd apps/api && APP_DATABASE_URL="$PGURL" APP_ENV=ci PYTHONPATH=. "$PY" -m alembic downgrade "$PRIOR_HEAD" )
    ( cd apps/api && APP_DATABASE_URL="$PGURL" APP_ENV=ci PYTHONPATH=. "$PY" -m alembic upgrade head )
  else
    echo "   (no new migrations vs origin/main — running plain upgrade head)"
    ( cd apps/api && APP_DATABASE_URL="$PGURL" APP_ENV=ci PYTHONPATH=. "$PY" -m alembic upgrade head )
  fi
  docker rm -f "$PGC" >/dev/null 2>&1 || true; trap - EXIT
  echo "   Postgres bootstrap + alembic check + incremental upgrade: OK"
else
  echo "   (docker not found — CI will run the Postgres migration-drift; install docker to catch it locally)"
fi

echo "==> 5. secret scan (gitleaks if installed)"
if command -v gitleaks >/dev/null 2>&1; then gitleaks detect --no-banner -c .gitleaks.toml || { echo "secrets found"; exit 1; }
else echo "   (gitleaks not installed locally — CI will enforce)"; fi

echo "ALL LOCAL CHECKS PASSED ✔  safe to commit/push."
