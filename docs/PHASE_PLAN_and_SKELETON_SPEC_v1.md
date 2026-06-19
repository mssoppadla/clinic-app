# Phase Plan & Phase-0 Walking-Skeleton Spec — v1

**Project:** AI patient appointment + queue SaaS (Kerala / Malayalam-first), co-hosted with `cpmai-prep` on Hostinger VPS.
**Status:** Planning artifact for sign-off. **No code is written until this is approved.**
**Companion docs:** `CONTRACT_UI_API_DB_v1.md`, `openapi_v1.yaml`, `data_model_v1.sql`, `Component_Catalog_and_Traceability_v2.xlsx`, `REQUIREMENTS_REGISTER_v13.xlsx`, `Reconciliation_4Way_Matrix.xlsx`.

---

## 1. The governing principle — vertical slices, not horizontal layers

We do **not** build UI-alone, then API-alone, then DB-alone and integrate at the end. That hides every integration failure until the most expensive moment.

Instead, **every phase delivers a thin vertical slice that runs in production and is proven by an automated end-to-end test executed against production.** A phase is "done" only when a real request flows browser → API → DB (and any external integration) → back, live on `tovaitech.in`, green in the prod smoke suite, without disturbing `cpmai` or existing clinics.

Three consequences:

1. **Walking skeleton first (Phase 0).** The first slice does almost nothing useful for a clinic, but it pierces *every* risky boundary so integration problems surface on day one, not month three.
2. **Each later phase grafts ONE integration onto the live spine.** WhatsApp, payments, AI booking, offline kiosk — each is its own vertical slice that also ships to prod behind a flag with its own prod E2E test.
3. **Every merge is shippable.** Incomplete work ships to prod *turned off* (feature flags, off by default), so "release every build" never exposes half-built features.

### 1.1 The four mechanics that make "always ship to prod" safe

| Mechanic | What it does | Source decision |
|---|---|---|
| **Dark launch / feature flags** | Unfinished features deploy to prod OFF; flag flips when ready. | A28 (flags off-by-default) |
| **Blue-green + ≥2 replicas** | New build runs beside old, health-gated; traffic switches only when green; instant rollback. No downtime to other clinics / cpmai / marketing site. | A-series zero-downtime |
| **Seeded canary tenant in prod** | A real `__canary__` clinic record that exists only to be exercised by tests. Isolated from real clinics by `tenant_id` + RLS. | new (this doc) |
| **Prod E2E smoke** | The same E2E suite that gates the merge re-runs against prod post-deploy. Red smoke → auto-rollback. | "local testing before commit" extended to prod |

---

## 2. Phase roadmap — every phase ends in production

Each row is a vertical slice. "Prod E2E proof" is the automated test that must pass *against production* (as the canary tenant) for the phase to be accepted. Everything ships behind a feature flag and respects the backward-compat gate (A24–A31, additive only).

| Phase | Vertical slice shipped to prod | New integration pierced | Prod E2E proof (canary tenant) | Exit gate |
|---|---|---|---|---|
| **0 — Walking skeleton** | Hosted page books 1 appointment for 1 seeded clinic; token shown; appears in queue. Includes WhatsApp confirmation + Bhashini Malayalam rendering. | Multi-tenancy/RLS, event→projection, **WhatsApp Cloud API**, **Bhashini (ASR/translate/TTS)**, CI/CD, blue-green, Caddy TLS, secrets, logging | Book via hosted page → token returned → row in queue → WhatsApp confirmation received on sandbox number → Malayalam label rendered via Bhashini. All green in prod. | Pipeline green end-to-end; cpmai untouched; rollback proven |
| **1 — Real booking depth** | Multi-patient (up to 3) overflow, reason-for-visit, slot vs join-queue, reschedule/shift, cancel. | Strong-consistency slot locking at load | Multi-patient overflow books atomically; concurrent double-book rejected; all in prod | Concurrency test passes in prod |
| **2 — Staff & front desk** | Staff auth (passkey/Google/password+MFA), front-desk console, walk-in, correct-existing, no-show recall, emergency wild-entry shift. | Staff JWT + role scopes; queue reorder | Front-desk adds walk-in → queue auto-shifts → others' ETA recomputed; emergency highlights row | Role isolation verified |
| **3 — Doctor & live queue** | Doctor console, mark done/skip/recall, running-late delay → ETA recompute, availability timelines/leave. | Real-time push (SSE/WS) | Doctor sets +15m delay → waiting patients' ETA updates live in prod | Real-time path stable |
| **4 — Payments (bring-your-own)** | Clinic connects own gateway (Razorpay/PhonePe/etc.); prepay + pay-at-clinic fallback; one-tap refund. | **Payment gateway** (clinic = merchant of record) | ₹1 sandbox order paid → booking confirmed; refund issued; gateway-down → pay-at-clinic fallback | RBI/merchant-KYC sequencing correct; we never hold funds |
| **5 — AI booking layer** | LLM NLU booking, RAG/FAQ, ASR/TTS voice; guardrail (LLM never finalizes a slot); per-call cost metered. | **LLM + voice**; usage metering ledger | NLU books via guardrailed flow; every call lands in `usage_events`; cost shows in FinOps | AI guardrail + metering verified |
| **6 — Offline kiosk / PWA** | PWA install, service worker, IndexedDB outbox, leased token blocks, reconcile on sync. | Offline-first + lease/reconcile | Kiosk offline → issues provisional tokens → reconciles cleanly on reconnect | No duplicate/lost tokens |
| **7 — Onboarding self-serve** | Readiness engine (mandatory/optional steps), Embedded Signup, provider override, channel-health monitor. | Embedded Signup full flow | New clinic self-onboards to READY in prod; override audited | Go-live gating correct |
| **8 — Platform / superadmin** | Plans, offers, GST, FinOps profit per clinic (all APIs metered), platform AI model registry. | Billing/invoicing | Superadmin sees per-clinic cost vs revenue incl Bhashini line | Metering reconciles |

> Phases 1–8 are sequenced by risk and dependency; they can be re-ordered, but **each one independently ships to prod and is E2E-proven there.** Nothing is "integrated later."

---

## 3. Phase-0 skeleton — detailed spec

**Goal:** the thinnest slice that still pierces the spine + WhatsApp + Bhashini, deployed to prod, proven by a canary E2E test.

### 3.1 In scope (Phase 0 only)
- One seeded clinic `__canary__` with one doctor and one availability block.
- Hosted public page at `tovaitech.in/appointments/__canary__` (Next.js).
- `POST /bookings` (join-queue mode only) → one `booking_events` append → projection writes `bookings` + `queue_entries` under RLS → token returned.
- `GET /clinics/{slug}` returns `ClinicPublic` (queue_count, avg_wait).
- WhatsApp: one outbound template ("booking received, token #N") to the sandbox/test number via WhatsApp Cloud API **direct**.
- Bhashini: the confirmation + the page's key labels rendered in **English + Malayalam** (English-always, A15) using a Bhashini translate/transliterate call, with a static fallback dictionary if Bhashini is down.
- Full delivery rig: Docker Compose, Caddy auto-TLS, CI/CD pipeline, blue-green deploy, secrets manager, structured JSONL logging, gitleaks, canary E2E smoke.

### 3.2 Explicitly OUT of scope for Phase 0
Multi-patient, slot booking, payments, LLM/voice, offline/PWA, staff/doctor consoles, onboarding UI, real clinics. (Each is a later vertical slice.) The DB ships with the *full* `data_model_v1.sql` tables present but most unused — additive-only, so later phases need no breaking migration.

### 3.3 Repo & folder layout (modular, reuses cpmai-prep patterns R1–R14)

```
clinic-saas/
  contracts/                 # source of truth, already drafted
    openapi_v1.yaml
    data_model_v1.sql
    events.md                # domain events
  apps/
    api/                     # FastAPI (reuse cpmai: provider registry, settings_store,
      app/                   #   Redis limiter, JSONL logging, Google-auth module, drift detector, HITL queue)
        core/                # config loader (NO hardcoding — all from env/settings_store), tenancy, RLS session
        domain/booking/      # event append + projections (event-sourced)
        integrations/
          whatsapp/          # Cloud API client (per-clinic number, token from secrets)
          bhashini/          # translate/ASR/TTS client + static fallback dictionary
        api/                 # routers generated against openapi_v1.yaml (tolerant readers)
        observability/       # structured logs, trace_id, metrics
      migrations/            # Alembic, expand-contract / additive-only
      tests/                 # unit + contract + integration (local gate)
    web/                     # Next.js hosted page (Shadow-DOM embed comes in a later phase)
  deploy/
    docker-compose.yml       # api x2 replicas, web, postgres(+pgvector), redis, caddy
    Caddyfile                # auto-TLS, reverse proxy, vhosts (clinic-saas + cpmai untouched)
    blue-green.sh            # health-gated switch + rollback
    cpmai-guard.sh           # row-count snapshot + additive-migration + idempotent-seed guard
  e2e/                       # Playwright; runs locally (merge gate) AND against prod (smoke)
    canary_booking.spec.ts
  .github/workflows/ci.yml   # the pipeline (section 3.4)
  config/                    # per-tenant config schema + defaults (no secrets, no hardcoding)
```

### 3.4 CI/CD pipeline stages (the "every build to prod" engine)

```
on push to main:
  1. lint + typecheck            (fail fast)
  2. unit + contract tests       (openapi + db schema validated; tolerant-reader tests)
  3. gitleaks scan               (no secrets in code — hard gate)
  4. local E2E (Playwright)      (ephemeral compose stack: book → token → queue → WA stub → Bhashini stub)
  5. build container images      (immutable, tagged by SHA)
  6. cpmai-guard preflight       (row-count snapshot of cpmai + clinic data; abort on shrink)
  7. additive-migration check    (reject any DROP/RENAME; Alembic expand-contract only)
  8. deploy blue-green           (new replicas up, health-gated)
  9. PROD E2E smoke (canary)     (real prod: book as __canary__ → token → queue → real WA sandbox msg → Bhashini Malayalam)
 10. switch traffic OR rollback  (green → cutover; red → auto-rollback, keep old live)
 11. post-deploy snapshot diff   (confirm no real-clinic / cpmai data changed)
```

### 3.5 Canary tenant design
- `tenants` row `slug='__canary__'`, `status='active'`, flagged `is_synthetic=true` (additive column).
- Excluded from billing, analytics, and FinOps aggregates by that flag.
- WhatsApp messages go only to a controlled test number; Bhashini calls are real but rate-capped.
- E2E test resets canary state (idempotent seed) before each run; never touches other tenants (RLS enforced, tenancy server-resolved — never client-supplied).

### 3.6 Prod E2E proof (the Phase-0 acceptance test)
Single Playwright spec, run in CI step 9 against prod:
1. `GET /clinics/__canary__` → assert `ClinicPublic` returns, `queue_count` is an integer.
2. `POST /bookings` (join_queue, 1 patient, reason set) with idempotency key → `201`, token returned.
3. Poll `queue_entries` projection → the booking appears with an ETA.
4. Assert a WhatsApp confirmation hit the sandbox number (webhook/echo check).
5. Assert the page label set rendered Malayalam-in-addition-to-English (Bhashini path) and that disabling Bhashini falls back to the static dictionary (no crash).
6. Re-POST with the same idempotency key → no duplicate (idempotency proven).

### 3.7 Co-hosting & data preservation (cpmai safety)
- Same VPS, same Compose pattern as cpmai; **separate** containers, DB schema/database, and Caddy vhost.
- `cpmai-guard.sh` takes a row-count snapshot of cpmai + clinic-saas tables before deploy and after; pipeline aborts/rolls back on any unexpected shrink.
- Migrations additive-only; seeds idempotent. Marketing site and cpmai stay up through every deploy (blue-green).

### 3.8 Cross-cutting rules enforced from Phase 0 (not retrofitted)
- **No hardcoding / fully configurable:** all tunables from env + `tenant_config` (versioned, hot-reload). Pipeline test asserts no literal config in code paths.
- **No secrets in code/deploy:** gitleaks hard gate; secrets only from the manager; WhatsApp & Bhashini tokens injected at runtime.
- **No PII in logs:** structured JSONL with a PII redaction filter; `trace_id` everywhere; audit_log for sensitive actions.
- **India data residency:** app + DB + backups in-region.
- **Backward compatibility is a release gate:** additive migrations, `/api/v2` for any break, tolerant readers, off-by-default flags, CI BC tests.
- **Responsible/Explainable AI seam reserved:** even though AI lands in Phase 5, the model-call wrapper + `usage_events` metering interface is stubbed in Phase 0 so adding it is additive.

### 3.9 Risks the skeleton surfaces early (the whole point)
- RLS actually isolating tenants under real queries (not just in theory).
- Event→projection consistency and idempotency under a real POST.
- WhatsApp Cloud API direct onboarding/token/webhook reality (no BSP) — the known-hard part.
- Bhashini latency/availability and the fallback path for Malayalam.
- Blue-green not disturbing cpmai; Caddy TLS for a second vhost.
- Secrets wiring and the pipeline itself (often the biggest day-one time sink).

---

## 4. Definition of Done — Phase 0
1. `tovaitech.in/appointments/__canary__` is live and books a token in prod.
2. Booking appears in the queue projection with an ETA.
3. WhatsApp confirmation delivered to the sandbox number from the clinic's own number path.
4. Page renders English + Malayalam via Bhashini, with a proven static fallback.
5. CI/CD pipeline green through all 11 stages; blue-green cutover + a deliberate rollback both demonstrated.
6. cpmai data + real-clinic data provably unchanged (snapshot diff).
7. gitleaks clean; no PII in logs (sampled check); tenancy server-resolved only.
8. The canary E2E spec is the permanent post-deploy smoke for all later phases.

---

## 5. Sign-off checklist (before any code)
- [ ] Phasing approach (vertical slices to prod, walking skeleton first) approved.
- [ ] Phase-0 scope = spine + WhatsApp + Bhashini approved.
- [ ] Phase roadmap order (0→8) approved or re-ordered.
- [ ] Canary-tenant-in-prod testing approach approved.
- [ ] Repo layout & cpmai co-host/guard approach approved.
- [ ] On approval: generate typed API client from `openapi_v1.yaml` + Alembic baseline from `data_model_v1.sql`, then build the Phase-0 skeleton.
