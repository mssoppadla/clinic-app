#!/usr/bin/env bash
# One-command ship: preflight -> branch -> commit -> push -> open PR -> wait for CI -> merge.
# The deploy then runs automatically on merge and pauses for your single Approve click in
# GitHub Actions (production environment). Usage:  ./ship.sh "commit message"
set -euo pipefail
cd "$(dirname "$0")"
REPO="mssoppadla/clinic-app"
MSG="${*:-chore: update}"
BR="ship/$(date +%Y%m%d-%H%M%S)"

if [ -z "$(git status --porcelain)" ]; then echo "Nothing to ship (no changes)."; exit 0; fi

echo "==> [1/6] preflight (tests + gates)"
if   [ -f apps/api/.venv/bin/activate ];     then . apps/api/.venv/bin/activate
elif [ -f apps/api/.venv/Scripts/activate ]; then . apps/api/.venv/Scripts/activate; fi
if command -v pytest >/dev/null 2>&1; then
  ( cd apps/api && PYTHONPATH=. pytest -q ) || { echo "TESTS FAILED — fix before shipping."; exit 1; }
  python deploy/check_additive_migration.py || { echo "MIGRATION GATE FAILED."; exit 1; }
else
  echo "   (no local pytest — CI will run the full gates on the PR)"
fi

echo "==> [2/6] branch $BR"; git checkout -b "$BR"
echo "==> [3/6] commit";      git add -A && git commit -m "$MSG"
echo "==> [4/6] push";        git push -u origin "$BR"

if ! command -v gh >/dev/null 2>&1; then
  echo "gh CLI not installed — branch pushed. Open the PR here, then merge:"
  echo "  https://github.com/$REPO/compare/main...$BR?expand=1"
  git checkout main >/dev/null 2>&1 || true
  exit 0
fi

echo "==> [5/6] open PR"
gh pr create --repo "$REPO" --base main --head "$BR" --title "$MSG" \
  --body "Automated ship via ship.sh. CI quality+security gates enforced; deploy approval kept."
echo "   waiting for CI checks to pass..."
gh pr checks "$BR" --repo "$REPO" --watch || { echo "CI FAILED — PR left open for review."; git checkout main >/dev/null 2>&1 || true; exit 1; }

echo "==> [6/6] merge"
gh pr merge "$BR" --repo "$REPO" --squash --delete-branch
git checkout main >/dev/null 2>&1 || true
git pull --ff-only >/dev/null 2>&1 || true
echo ""
echo "✅ Merged. The deploy is now running in GitHub Actions and is waiting for your approval:"
echo "   https://github.com/$REPO/actions   ->  deploy run  ->  Review deployments -> Approve"
