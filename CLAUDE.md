# CLAUDE.md — clinic-app (Appointments product)

> Context handoff for Claude Code. This repo is the **Appointments & Queue** product of
> **Tovaitech**, a Kerala/Malayalam-first clinic SaaS. It is LIVE in production at
> `https://tovaitech.in/appointments/<clinic-slug>` (+ `/admin.html`, `/api/v1/*`),
> co-hosted on a Hostinger VPS alongside `cpmai-prep` and the separate `tovaitech-site`
> company landing — each fully isolated.

## What this is
Multi-tenant SaaS: WhatsApp + voice appointment booking and a live token queue for clinics.
Phase 0 (walking skeleton) is shipped to prod. We are about to start **Phase 1**.

## Architecture (Phase 0, current)
- **Backend** `apps/api` — FastAPI, **event-sourced** booking: `booking_events` is the source of
  truth; `bookings`/`queue_entries`/`tokens` are projections. SQLAlchemy 2.0, Alembic.
  - Runs on **Postgres** in prod; tests run on **SQLite** (portable models, UUIDs generated in Python).
  - **Tenancy**: server-resolved (slug/host/header), enforced at the app layer via `TenantScope`
    (filters every query by `tenant_id`). **Postgres RLS is DEFERRED** — it needs the app to
    `SET app.tenant_id` per request, which isn't wired yet (see `scripts/bootstrap_db.py`). Re-enabling
    RLS is a Phase-1 hardening item.
  - **Integrations** `apps/api/app/integrations` — WhatsApp (Cloud API, direct/no-BSP) and Bhashini
    (Malayalam **voice**: ASR understand, TTS speak/read-token, transliterate name/phone). Each is
    `stub|live` selected by **runtime config** (env + DB `integration_config`, hot-reload) — flip to
    live + paste creds in the admin screen, no redeploy. Failures fall back; every call metered to
    `usage_events`.
  - Config is **env-only, no hardcoding** (`app/core/config.py`); secrets never in code.
- **Web** `web/` — static pages served by Caddy: `index.html` (patient booking, slug from
  `/appointments/<slug>` path), `admin.html` (WhatsApp/Bhashini config), shared `styles/app.css`
  (design tokens extracted from the mock — the single styling source of truth).
- **Prod-mode note**: containers run `APP_ENV=prod` so FastAPI `root_path=/api/v1`; the front Caddy
  forwards `/api/*` **unstripped** (uses `handle`, not `handle_path`). The Dockerfile sets
  `PYTHONPATH=/app` and copies `app/ migrations/ scripts/`. Startup = `bootstrap_db.py`
  (fresh: create_all + stamp; existing: alembic upgrade) → `seed.py` (idempotent canary) → uvicorn.

## Repo layout
```
apps/api/            FastAPI app, tests, Alembic migrations, scripts/bootstrap_db.py
web/                 static patient + admin pages (+ styles/app.css)
deploy/              docker-compose (api+postgres+redis+caddy), Caddyfile, blue-green.sh,
                     cpmai-guard.sh, check_additive_migration.py
scripts/vps/deploy.sh    on-VPS deploy entrypoint (data-guard -> build -> health -> smoke -> verify)
e2e/smoke.py         prod canary smoke (stdlib only): book -> token -> queue -> idempotency
.github/workflows/   backend-ci, frontend-ci, security-scan (advisory) + deploy.yml (gated)
docs/                contracts + plan + mockups (UI source of truth) — see below
ship.sh / ship.bat   one-command release (preflight -> branch -> commit -> push -> PR -> merge)
run_local.bat        one-click local run (Windows)
```

## Authoritative docs (read these)
- `BUILD_GUIDELINES.md` — **MANDATORY before any screen/API/migration.** Mock-first; all UI via
  `web/styles/app.css` (no hardcoded colors); English-always + Malayalam; responsive (>=48px taps);
  additive migrations only; no secrets; write-only secret config; §H is a living log of UI rules.
- `DEPLOYMENT.md` — CI/CD flow, GitHub branch/environment setup, VPS layout, secrets.
- `PHASE0_RUNBOOK.md` — Phase-0 DoD + go-live steps.
- `docs/MASTER_DELIVERY_PLAN_v1.md` — full phase roadmap (0→12 + hardening H1–H6 + GA) and the
  vertical-slice-to-prod principle.
- `docs/CONTRACT_UI_API_DB_v1.md`, `docs/openapi_v1.yaml`, `docs/data_model_v1.sql` — the UI↔API↔DB contract.
- `docs/Mockups_v2_AllPersonas.html` (English-default) and `docs/Mockups_v3_Bilingual.html` — **the UI
  source of truth**; match these for every screen.
- `docs/REQUIREMENTS_REGISTER_v13.xlsx`, `docs/Component_Catalog_and_Traceability_v2.xlsx`,
  `docs/Reconciliation_4Way_Matrix.xlsx` — requirements + traceability.

## Production topology (Hostinger VPS, IP 187.127.163.86)
| Path / host | Stack | VPS dir | Port |
|---|---|---|---|
| `tovaitech.in/` | company site (separate repo `tovaitech-site`) | `/opt/tovaitech-site` | 8090 |
| `tovaitech.in/appointments/*`, `/admin*`, `/api/*` | THIS repo | `/opt/clinic-app` | 8080 |
| `cpmaiexamprep.com` | cpmai | `/opt/cpmai-prep` | 3001/8001 |
- A **host-level Caddy** (systemd, `/etc/caddy/Caddyfile`) owns 80/443 and routes by host/path; it
  terminates TLS (auto-cert) and reverse-proxies to the per-app ports. Each app is its own Docker
  Compose project + DB → **independently deployable; deploys never affect the others**.

## Dev + release workflow
- Local: `run_local.bat` (Windows) or `make run`; tests `make test` / `cd apps/api && PYTHONPATH=. pytest -q`;
  full pre-commit gate `bash scripts/local_check.sh`.
- Release: `./ship.sh "message"` — runs preflight, branches, commits, pushes, opens a PR, waits for
  CI, merges. Then the **deploy** runs on merge and waits for **one manual Approve** in GitHub Actions
  (production environment). The on-VPS `deploy.sh` does data-guard snapshot → build → health gate →
  `e2e/smoke.py` canary → data-preservation verify → rollback on failure.
- Secrets (GitHub `production` env): `VPS_HOST=187.127.163.86`, `VPS_USER=deploy`, `VPS_SSH_KEY`,
  `VPS_HOST_KEY`. App secrets (WhatsApp/Bhashini/Postgres) live in `/opt/clinic-app/.env` on the VPS,
  not in git.

## Status & next (Phase 1)
Phase 0 done & live. **Phase 1** (next vertical slice, each shipped to prod via the gates):
1. Real **slot booking** (`mode: slot`, per-slot capacity) + **atomic slot locking / concurrency**
   (serializable txn + row lock; add a concurrent-booking test).
2. **Reschedule / shift** and **cancel** (with the configurable refund hook).
3. Hardening: **advisory-lock the DB bootstrap** so 2+ API replicas are safe (zero-downtime), and
   **wire `app.tenant_id`** to re-enable Postgres RLS.
Build each behind the existing gates; extend `e2e/smoke.py` to cover slot booking.

## House rules
- Don't start large work without confirming scope. Additive/backward-compatible changes only.
- Test locally before commit; keep the canary smoke green. Match the mocks. No secrets in code.
