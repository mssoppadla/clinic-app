# Phase 0 — Runbook & Definition of Done

Walking skeleton: **spine + WhatsApp + Bhashini**, contract-first, config-from-env, tenant-isolated,
event-sourced, containerized, with CI/CD + preflight + security gates and a prod canary smoke.

## What's in the repo
```
apps/api/        FastAPI: event store + projections, tenancy+RLS, integrations, tests, Alembic
web/             responsive patient app (mock palette via styles/app.css), English-always + Malayalam
deploy/          docker-compose (api x2, postgres, redis, caddy), Caddyfile, blue-green.sh,
                 cpmai-guard.sh, check_additive_migration.py
e2e/smoke.py     prod canary E2E gate (book -> token -> queue -> idempotency)
scripts/local_check.sh   run before every commit (mirrors CI)
.github/workflows/ci.yml CI: lint, compile, BC-gate, tests, gitleaks, pip-audit, gated deploy
.gitleaks.toml   secret-scan rules
run_local.bat / stop_local.bat   one-click local run on Windows
```

## Local dev loop (before any commit)
```bash
bash scripts/local_check.sh        # compile + BC-gate + 17 tests + secret scan
```
Windows users: double-click run_local.bat to launch + open the page.

## Going live with WhatsApp + Bhashini (your credentials)
In the server `.env` (never committed):
```
APP_WHATSAPP_MODE=live
APP_WHATSAPP_TOKEN=...            # from Meta
APP_WHATSAPP_PHONE_NUMBER_ID=...  # the clinic's number id
APP_BHASHINI_MODE=live
APP_BHASHINI_BASE_URL=...
APP_BHASHINI_API_KEY=...
APP_BHASHINI_USER_ID=...
```
No code change — the clients switch on env. Stub mode stays the default for CI/local.

## Production cutover (zero-downtime, cpmai-safe) — on the VPS
1. Set GitHub Actions secrets (VPS host/key) and the server `.env`.
2. Merge to `main` -> CI runs lint, BC-gate, tests, gitleaks, pip-audit, builds the image.
3. `./deploy/blue-green.sh green blue` :
   - snapshots cpmai + clinic row counts (data guard),
   - builds + starts the new color, health-gates `/healthz`,
   - runs `e2e/smoke.py` against the new color,
   - verifies the data guard (no row loss), then cuts Caddy over; rolls back on any failure.
4. Caddy serves the page at `/` and proxies the API at `/api/*` (same-origin, auto-TLS).

## Phase-0 Definition of Done
- [x] Hosted page books a token for the seeded clinic; appears in the queue.
- [x] WhatsApp confirmation path (stub now; live via env).
- [x] English-always + Malayalam via Bhashini (with static fallback).
- [x] Event-sourced booking; idempotent; tenant-isolated (app guard + Postgres RLS).
- [x] Config-from-env (no hardcoding); no secrets in code (gitleaks gate).
- [x] 17 local tests green incl. Phase-0 <-> 4-way-matrix coverage map.
- [x] CI/CD pipeline + preflight (cpmai guard, additive-migration) + security gates.
- [x] Blue-green deploy script + prod canary smoke.
- [x] Frontend matches mock palette (shared styles/app.css) and is responsive (phone/tablet/laptop, iOS/Android).
- [ ] **Live cutover to tovaitech.in** — requires VPS secrets + your WhatsApp/Bhashini creds (your action).

## Next phase
Phase 1 (booking depth: slots, reschedule/shift, cancel, concurrency) — same vertical-slice-to-prod rule.
