# Browser end-to-end tests (real user, Playwright)

These drive the **actual web pages** through Caddy + the API — the layer the in-process
`pytest` suite can't cover. They catch frontend JS errors, broken page flows, and a
**down/502 backend** (e.g. the onboarding "no network" bug, which an API-contract test cannot see
because `TestClient` calls the app in-process and never loads a page or detects a dead server).

`journey.spec.js` builds all its own data through the UI: register a clinic → platform admin
approves go-live (reading the auto-created clinic_admin credentials off the screen) → that admin
adds a doctor **with a login** (one unified profile) and generates slots → the patient booking
page loads. The only bootstrap is the platform superadmin (mirrors prod's `APP_SUPERADMIN_*`).

## Run against a live stack

```bash
# 1. stack must be up: API (root_path /api/v1) behind Caddy on :8085 with a seeded superadmin
# 2. one-time:
cd e2e/ui
npm install
npx playwright install chromium
# 3. run:
BASE_URL=http://localhost:8085 npx playwright test
```

Env knobs: `BASE_URL` (default `http://localhost:8085`), `E2E_SUPERADMIN` / `E2E_SUPERADMIN_PW`
(default `root@local.test` / `rootpass123`). The login helper handles a forced first-login reset
idempotently, so a freshly seeded superadmin (must_reset=true) works without manual setup.
