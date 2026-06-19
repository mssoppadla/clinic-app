#!/usr/bin/env bash
# Run BEFORE every commit. Mirrors the CI gates so failures surface locally first.
set -euo pipefail
cd "$(dirname "$0")/.."
echo "==> 1. compile check"; python3 -m compileall -q apps/api/app
echo "==> 2. additive-migration (backward-compat) gate"; python3 deploy/check_additive_migration.py
echo "==> 3. unit + integration tests"; (cd apps/api && PYTHONPATH=. python3 -m pytest -q)
echo "==> 4. secret scan (gitleaks if installed)"
if command -v gitleaks >/dev/null 2>&1; then gitleaks detect --no-banner -c .gitleaks.toml || { echo "secrets found"; exit 1; }
else echo "   (gitleaks not installed locally — CI will enforce)"; fi
echo "ALL LOCAL CHECKS PASSED ✔  safe to commit."
