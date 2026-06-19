# Clinic Booking SaaS — UI ↔ API ↔ DB Contract (v1)

> Scope: the agreed contract between the UI layer, the API/services layer, and the data layer.
> Principle: **comprehensive on paper, lean in build.** Everything needed to be functional is defined here; we implement incrementally, but every change must be **additive and backward-compatible** so no other component breaks (req A23–A31). This document is the single source of truth for the boundaries between the 64 components (see `Component_Catalog_and_Traceability_v2.xlsx`).

Companion machine-readable files: `openapi_v1.yaml` (API skeleton) and `data_model_v1.sql` (DDL sketch).

---

## 0. How to read this
- **REQ tags** (e.g. `[F17]`, `[A9]`) trace each rule back to `REQUIREMENTS_REGISTER_v13.xlsx`.
- **MVP** = build now. **LATER** = contract reserved, not built yet (but the seam exists).
- A contract change is only allowed if it is **additive** (new optional field / new endpoint / new event version). Breaking changes require a new API version (`/api/v2`) and a deprecation window `[A25]`.

---

## 1. Cross-cutting conventions

### 1.1 Versioning `[A16, A25]`
- All endpoints live under `/api/v1`. New fields are **optional with safe defaults**; removing/renaming is forbidden in `v1`.
- The embed widget is versioned (`book.js?v=1`); old versions keep working `[A26]`.
- Events carry `event_version`; consumers are **tolerant readers** (ignore unknown fields) `[A29]`.

### 1.2 Tenancy `[A1, A11, A12, F1]`
- Every request resolves a **tenant** from: subdomain/host, path slug `/appointments/{slug}`, embed `data-clinic`, or the auth token's `tenant_id`.
- The resolved `tenant_id` is injected server-side and enforced on **every** query (Postgres RLS). Clients never pass `tenant_id` in the body for write-scoping; it comes from context. Cross-tenant access is impossible `[S14]`.
- Header (server-internal): `X-Tenant-Id`. Public callers use the slug/host/token.

### 1.3 Authentication & roles `[AC1–AC18, S1, S12]`
- **Staff/doctor/admin/superadmin**: session via short-lived access token (JWT) + refresh; login by passkey/Google/password `[AC2]`; MFA for admin `[AC6]`. Token claims: `sub`, `tenant_id`, `role`, `scopes`, `exp`. Roles: `superadmin, clinic_admin, doctor, front_desk, triage` — **multiple admins allowed** `[AC18]`.
- **Patient**: passwordless **OTP login**; token claims `patient_id`(phone-derived), `tenant_id`, `scope=patient.self` — sees only own bookings `[AC8, AC9]`.
- **Integrations**: per-clinic **API key** (or OAuth client) with scopes, rate-limited, revocable, rotated `[AC12]`.
- **Kiosk device**: provisioned **device token**, scope = `kiosk.walkin` only (cannot read other patients/admin) `[K3, K7]`.
- Authorization is scope+role checked per endpoint (table in §4). All auth events audited `[AC14]`.

### 1.4 Standard error envelope
```json
{ "error": { "code": "string.snake", "message": "human readable",
  "field_errors": [{"field":"phone","code":"invalid"}],
  "trace_id": "uuid", "retryable": false } }
```
HTTP codes: 400 validation, 401 unauth, 403 forbidden, 404, 409 conflict (e.g. slot taken), 422 business-rule, 429 rate-limit, 503 dependency-down (with `retryable:true`). The UI maps `code` → localized message (English always + Malayalam optional) `[A15]`.

### 1.5 Pagination / filter / sort
- Cursor pagination: `?limit=50&cursor=...` → `{ "items":[...], "next_cursor": "..." }`. Filters are explicit query params; sorting `?sort=field:asc`. No offset paging on large tables.

### 1.6 Idempotency `[A17]`
- All POST/PUT that create money/slots/messages accept `Idempotency-Key` header. The key + response are stored; replays return the original result. Inbound webhooks dedupe by provider message id.

### 1.7 Events, ledger & outbox `[A9, A10, A17, A29]`
- The booking domain is **event-sourced**: writes append to `booking_events` (immutable); read models (`slots`, `queue`, `bookings`) are projections.
- Cross-component side-effects go through an **outbox** (transactional write → async dispatch) → guarantees delivery (notifications, webhooks, alerts).
- Event envelope:
```json
{ "event_id":"uuid","event_type":"booking.created","event_version":1,
  "tenant_id":"...","occurred_at":"ts","actor":{"type":"patient|staff|system","id":"..."},
  "idempotency_key":"...","payload":{...} }
```

### 1.8 Real-time `[A13, F23]`
- Clients subscribe via WebSocket/SSE to tenant- and resource-scoped channels: `queue:{clinic_id}:{session_id}`, `booking:{booking_id}`. Server fans out via Redis pub/sub. Messages are the same event envelope. Clients auto-reconnect; state is re-fetched on reconnect.

### 1.9 Webhooks
- **Inbound** (Meta WhatsApp, payment gateway): signature-verified, idempotent, fast-ack then process async via queue.
- **Outbound** (clinic CRM): signed (HMAC), retried with backoff → DLQ; replayable `[F48]`.

### 1.10 Config & feature flags `[A5, A22, A27, A28, F-config]`
- Per-tenant config is data (`tenant_config`), **hot-reloaded**, versioned, rollback-able. New config keys **default to current behaviour** `[A28]`. New features behind flags **off by default** for existing tenants `[A27]`.

### 1.11 Backward-compatibility rules `[A23–A31]` (binding on every change)
1. Add columns/tables only; never drop/rename in `v1` (expand-contract) `[A24]`.
2. New request/response fields optional; new enum values tolerated by readers.
3. New event types/versions additive; old versions still replayable.
4. Widget & API changes versioned; old clients unaffected.
5. CI runs backward-compat + contract tests against a prod-like snapshot before deploy `[A31]`.

### 1.12 Audit, PII, secrets `[S3, S4, S6]`
- Every state change to queue/priority/cancel/refund/role/impersonation writes to `audit_log` `[S4]`. PII (name/phone/health-reason) stored encrypted/tokenized in the vault `[S3]`; never in logs (redacted). Secrets/keys in the secrets manager, never in code `[S6]`.

---

## 2. Data model (entities)

> Conventions: every tenant-owned table has `id uuid pk`, `tenant_id uuid` (RLS), `created_at`, `updated_at`, `deleted_at` (soft delete). Money in minor units (paise) + `currency`. Times in UTC; render in clinic TZ `[A18]`. Full DDL in `data_model_v1.sql`.

### Platform / tenancy
- **tenants** — clinic tenant. `slug` (unique), `name`, `status(trial|active|suspended|offboarding|closed)`, `region`, `dedicated(bool)`, `datasource_ref` (for dedicated seam `[A7]`).
- **users** — staff/doctor/admin/superadmin. `email`, `auth_methods`, `mfa`, `status`. (superadmin row has `tenant_id=null`.)
- **user_roles** — (`user_id`,`tenant_id`,`role`) — **many-to-many → multiple admins, cross-clinic membership** `[AC4, AC18]`.
- **api_keys** — per-tenant integration keys (`scopes`, `last_rotated`, `revoked`).
- **devices** — kiosk devices (`device_token`, `scope=kiosk.walkin`, `revoked`) `[K3, K10]`.

### Clinic configuration
- **clinics/clinic_profile** — address, hours, holidays, branding (`logo`,`colors`,`languages[]` with English always) `[A15]`.
- **departments** — name, per-dept queue rules ref.
- **doctors** — `name`, `specialty`, `fee_minor`, `languages[]`, `photo`.
- **availability_blocks** — doctor custom timelines: `rrule`, `start`,`end`,`capacity_per_slot`,`slot_minutes`,`breaks[]` `[F8, A18]`.
- **leaves** — doctor/clinic blackout dates.
- **tenant_config** — key/value JSON, versioned (`version`,`active`); holds slot rules, cut-offs **per session** `[F26]`, refund policy `[F32]`, queue/interleave & emergency capacity `[F24,F29]`, fee `[F18]`, languages, flags. 
- **feature_flags** — (`tenant_id`,`flag`,`enabled`) `[A27]`.
- **plugins** — per-tenant enabled extension modules `[A6]`.

### Scheduling & booking (event-sourced core)
- **sessions** — a doctor's dated session (morning/evening) with computed capacity.
- **slots** (read model) — `(clinic, doctor, session, start, end)`, `capacity`, `used`, `status` — derived from events; **availability is computed here** `[F7, F11c]`.
- **bookings** (read model) — `patient_id`, `doctor_id`, `slot_id`, `channel(online|walkin|advance)`, `status(held|confirmed|cancelled|completed|no_show|skipped)`, `party_size`, `fee_total_minor`, `payment_id`, `created_via`.
- **booking_patients** — one row per patient in a multi-patient booking → own token + ETA `[F11a–c]`.
- **tokens** — `number` (per clinic-session), `booking_patient_id`, `provisional(bool)`, `device_id` (offline leased) `[F25]`.
- **token_leases** — reserved number blocks per device for offline issuance `[F25]`.
- **queue_entries** (read model / projection) — ordering, ETA, `state(waiting|now|done|skipped|wild_entry)` `[F22,F30b]`.
- **booking_events** — append-only event store (the source of truth) `[A9]`.
- **waitlist** — for auto-fill on cancel `[F27]`.

### Patients & consent
- **patients** — `tenant_id`, `phone`(identity), `name`, optional `abha_ref`; returning-patient match by phone `[F10]`.
- **consents** — consent ledger (`purpose`,`granted_at`,`version`,`channel`) `[F12, S5]`.
- **patient_documents** — uploads (LATER).

### Payments & billing
- **payment_providers** — per-tenant connected gateway (bring-your-own): `provider`, `merchant_ref`, `mode`, `kyc_status` `[F31, S8, S9, D5]`.
- **payments** — per booking: `amount_minor`, `provider`, `provider_txn_id`, `status`, `idempotency_key`. Clinic is merchant; we never hold funds `[S8]`.
- **refunds** — `payment_id`, `amount_minor`, `reason`, `status`, `sla_due`, policy-driven `[F32]`.
- **plans** — superadmin-defined; `price_minor`, `model(flat|usage|hybrid)` `[O26]`.
- **subscriptions** — tenant↔plan, `status`, dunning state `[O1]`.
- **offer_codes** — `code`, `discount`, `validity`, `usage_limit` `[O27]`.
- **invoices** — to clinics, GST fields `[O28]`.
- **usage_events** — append-only metered usage per tenant per billable API (LLM/Bhashini/Sarvam/WhatsApp/telephony/gateway) → billing + FinOps/profit `[O2, O10, O29]`.

### Channels, AI, integrations
- **whatsapp_accounts** — per-tenant WABA: `phone_number_id`, `waba_id`, `onboarding_state(signup|registered|webhooks|ready)`, `verification_state`, `quality`, `tier` `[F3a–c, F2a]`.
- **message_templates** — per-language WhatsApp templates: `name`, `lang`, `approval_state`, `version` `[O20]`.
- **notifications** — outbox-backed sends: `type`, `to`, `template`, `status`, `attempts` `[F33]`.
- **otp_challenges** — `phone`, `code_hash`, `expires_at`, `attempts` `[F17,F34,AC15]`.
- **ai_models** — **platform-level** registry: `task(llm|asr|tts)`, `provider`, `key_ref`(vault), `active`, `fallback_of` `[F44, O30]`.
- **rag_sources** — per-tenant FAQ docs + embeddings (pgvector) `[F43]`.
- **hits_connectors** — HMS/EMR connector config + ABDM `[O21]`.
- **emergency_requests** — `raised_by`, `matched_patient_id` (highlight), `reason?`, `name?`,`phone?` (optional), `decision`, `decided_by`, `audited` `[F28,F29,F30a,F30c]`.

### Cross-cutting stores
- **audit_log** `[S4]`, **outbox** `[A17]`, **idempotency_keys** `[A17]`, **webhook_deliveries** (outbound), **events_archive** (cold).

---

## 3. Domain events (catalog)
`tenant.onboarding.step_completed`, `whatsapp.connected`, `whatsapp.quality_changed`, `slot.created`, `booking.held`, `booking.confirmed`, `booking.cancelled`, `booking.rescheduled`, `booking.no_show`, `booking.skipped`, `token.issued`, `token.reconciled`, `queue.advanced`, `queue.eta_recomputed`, `wild_entry.granted`, `payment.captured`, `payment.failed`, `refund.requested`, `refund.completed`, `notification.queued`, `notification.sent`, `usage.recorded`, `subscription.changed`, `audit.recorded`. Each versioned; emitted via outbox; consumed by notifications, realtime, analytics, cost-meter, CRM webhooks.

---

## 4. API surface (by domain)
Format: `METHOD path` — purpose · auth(role/scope) · idempotent? · emits.

### Auth & identity `[C35]`
- `POST /api/v1/auth/login` — passkey/Google/password → tokens · public · —
- `POST /api/v1/auth/refresh` · `POST /api/v1/auth/logout` · `POST /api/v1/auth/forgot` `[AC17]` · `POST /api/v1/auth/reset`
- `POST /api/v1/auth/otp/request` · `POST /api/v1/auth/otp/verify` → patient token `[AC8]` · public · idempotent
- `POST /api/v1/users/invite` (role) `[AC18]` · clinic_admin · ; `PATCH /users/{id}/role`; `POST /users/{id}/revoke` `[AC7]`

### Onboarding `[C34]`
- `POST /api/v1/onboarding/clinic` — create tenant+account · public→admin
- `GET /api/v1/onboarding/status` — readiness per step + go-live `[F2a,F2b]`
- `POST /api/v1/onboarding/whatsapp/embedded-signup/callback` — capture WABA/number/token `[F3a]`
- `POST /api/v1/onboarding/override` — provider waive a check (audited) `[F2c]` · superadmin
- `POST /api/v1/onboarding/preset` `[F2]`

### Clinic / doctor / availability `[C36,C37]`
- `GET/PUT /api/v1/clinic/profile` (branding, languages, hours) · clinic_admin
- `GET/POST/PATCH/DELETE /api/v1/doctors` `[F6]`
- `GET/POST/PATCH/DELETE /api/v1/doctors/{id}/availability` (custom timelines, capacity) `[F8]` · doctor/admin
- `GET/PUT /api/v1/config` (slots, per-session cut-off, queue rules, refund policy, fee, flags) `[F7,F18,F26,F32]` · clinic_admin · hot-reload

### Discovery & availability (public)
- `GET /api/v1/clinics/{slug}` — public landing data (queue count, doctors) `[F16]`
- `GET /api/v1/clinics/{slug}/availability?doctor=&date=` — slots with free capacity (real-time) `[F16,F11c]`

### Booking `[C38]`
- `POST /api/v1/bookings` — body: doctor, slot|join_queue, patients[1..3], contact, channel · patient/front_desk · **idempotent** · emits booking.held/confirmed · returns tokens + ETAs · enforces **multi-slot overflow + atomic lock** `[F11a-c,F17,F21]`
- `POST /api/v1/bookings/{id}/confirm` (after payment) · `POST /bookings/{id}/cancel` (OTP) `[F34]` · `POST /bookings/{id}/reschedule` · `POST /bookings/{id}/shift` · `POST /bookings/{id}/in-premises` `[F19]`
- `GET /api/v1/me/bookings` — patient's own (OTP scope) `[AC9, F33a]`

### Queue & tokens `[C39,C40]`
- `GET /api/v1/queue?doctor=&session=` — live queue + ETA (also via WS) `[F23]`
- `POST /api/v1/queue/{entry}/mark-done` · `/skip` (sets `skipped`) `[F30b]` · `/recall` · `/pause` · doctor/front_desk · emits queue.advanced
- `POST /api/v1/walkins` — front-desk new walk-in (NEW entry) `[F22a]`
- `POST /api/v1/kiosk/token` — issue token (device scope; offline-capable via lease) `[F14,F25]`
- `POST /api/v1/kiosk/sync` — reconcile offline outbox (idempotent) `[F25]`

### Emergency `[C41]`
- `POST /api/v1/emergency` — online patient/bystander; optional name/phone; matches registered patient `[F28,F30a,F30c]` · emits + alerts staff (outbox)
- `POST /api/v1/emergency/{id}/decide` — staff grant wild-entry / ER / decline · triage/front_desk · queue auto-shift `[F29,F30]`

### Payments & refunds `[C20,C42]`
- `POST /api/v1/payments/intent` — create on clinic's gateway (BYO) `[F31]` · idempotent
- `POST /api/v1/payments/webhook/{provider}` — inbound, signed `[F31]`
- `POST /api/v1/refunds` — one-tap; auto path triggered by policy on cancel/no-show `[F32]`
- Fallback: if gateway down → booking proceeds `pay_at_clinic` (circuit breaker) — never blocks `[FMEA #6]`

### Notifications `[C19,C44]`
- `POST /api/v1/notifications/test` · `GET/PUT /api/v1/templates` (per-language, approval state) `[O20]`
- (sends are event-driven via outbox; WhatsApp-only `[F33]`; web/PWA is always-available status `[F33a]`)

### Patients & consent `[C43]`
- `GET /api/v1/patients?phone=` (match) `[F10]` · `POST /api/v1/consents` `[F12]`

### Integrations `[C23,C24,C21,C16,C17]`
- `GET /api/v1/integrations/appointments` (CRM read, paginated) `[F48]`
- `POST /api/v1/integrations/webhooks` (register outbound URL) · `GET /export/appointments.csv`
- `GET/POST /api/v1/integrations/hms` (connector config, ABDM) `[O21]`
- WhatsApp Flows endpoint `[F42]`; Reserve-with-Google link issue `[F37]`

### Billing & superadmin `[C47,C8,C26,C48]`
- `GET/POST /api/v1/admin/plans` (superadmin sets price) `[O26]` · `…/offers` `[O27]` · `…/invoices` (GST) `[O28]`
- `GET /api/v1/admin/tenants` + `POST /admin/tenants/{id}/impersonate` (audited) `[AC13]`
- `GET/POST /api/v1/admin/ai-models` (platform registry) `[F44,O30]`
- `GET /api/v1/admin/finops` — AI/WhatsApp cost vs revenue per tenant `[O29]`

### Analytics `[C46]`
- `GET /api/v1/analytics/{metric}` — no-show, wait, utilisation, revenue, refund (read replica) `[F47]`

---

## 5. Inbound webhooks
- **WhatsApp** (`/payments/.../whatsapp` events): messages, statuses, account_update (verification/quality/tier) → updates `whatsapp_accounts`, feeds channel-health monitor `[F3b,F3c]`.
- **Payment gateway**: payment.captured/failed/refunded → reconcile `payments`/`refunds` (idempotent; reconciliation poll fallback for lost webhooks) `[FMEA]`.

---

## 6. UI ↔ API mapping (per screen → endpoints / channels)
| Screen (component) | Calls |
|---|---|
| Patient landing (C1) | `GET /clinics/{slug}` |
| Choose queue/slot (C1) | `GET /clinics/{slug}/availability` |
| Details+OTP (C1) | `auth/otp/request`,`auth/otp/verify` |
| Payment (C1) | `payments/intent` → gateway SDK |
| Confirm+live (C1) | `bookings`,`bookings/{id}/confirm`, WS `booking:{id}`,`queue:...` |
| Manage/cancel (C1) | `me/bookings`,`bookings/{id}/cancel`,`/reschedule`,`/shift` |
| Kiosk capture/token (C2) | `kiosk/token`, offline → `kiosk/sync` |
| Emergency (C1) | `emergency` |
| Doctor console (C4) | `queue`, `queue/{e}/mark-done|skip|recall|pause`, WS `queue:...` |
| Availability (C4) | `doctors/{id}/availability` |
| Front-desk day (C5) | `queue`, `walkins`, `bookings/{id}` (edit), `emergency/{id}/decide` |
| Admin config (C3) | `clinic/profile`,`config`,`doctors`,`templates`,`integrations/*` |
| Onboarding (C3) | `onboarding/*` |
| Superadmin (C8) | `admin/tenants`,`admin/plans`,`admin/offers`,`admin/ai-models`,`admin/finops` |
| Embed widget (C7) | same public booking endpoints, host = clinic site |

---

## 7. Config keys (per-tenant, hot-reload) — representative
`slot.minutes`, `slot.capacity`, `session.morning.cutoff`, `session.evening.cutoff`, `booking.accept`, `booking.fee_minor`, `refund.on_cancel`, `refund.on_no_show`, `refund.window_minutes`, `queue.walkin_online_mix`, `emergency.reserved_per_session`, `wildentry.autoshift`, `noshow.recall_minutes`, `lang.default=en (locked)`, `lang.also_malayalam`, `ai.assistant_enabled`, `ai.voice_enabled`, `ai.budget_cap_minor`, `flags.*`. New keys default to current behaviour `[A28]`.

---

## 8. Component ↔ contract ownership
Each component owns its entities + endpoints + emitted events (one writer per entity; others read via API/events). Mapping derived from `Component_Catalog_and_Traceability_v2.xlsx` (sheet 3). Booking engine (C38) is the **only** writer to `booking_events`; everything else subscribes.

---

## 9. Review log — 3 critique passes (findings → corrections, folded into the doc above)

### Pass 1 — Completeness vs requirements (199 IDs / 64 components)
- **Found:** missing per-session cut-off in data model (only one cut-off). **Fixed:** `session.morning/evening.cutoff` config keys `[F26]`.
- **Found:** offline token leasing had no table. **Fixed:** added `token_leases` + `tokens.provisional/device_id` `[F25]`.
- **Found:** emergency-to-registered-patient match not modelled. **Fixed:** `emergency_requests.matched_patient_id` `[F30c]`.
- **Found:** AI registry implied per-tenant. **Fixed:** `ai_models` is platform-level; tenants only toggle/budget `[F44,O30]`.
- **Found:** multiple admins not expressible. **Fixed:** `user_roles` many-to-many `[AC18]`.

### Pass 2 — Consistency & integrity
- **Found:** `tenant_id` could be spoofed via body. **Fixed:** tenancy is context/RLS only; never client-supplied for scoping `[A11,S14]`.
- **Found:** availability vs slots double-source-of-truth risk. **Fixed:** `slots`/`queue_entries`/`bookings` are **read-model projections** of `booking_events`; one writer (C38) `[A9]`.
- **Found:** notifications could be sent twice on retry. **Fixed:** outbox + idempotency keys; WhatsApp dedupe by message id `[A17]`.
- **Found:** money as float. **Fixed:** integer minor units + currency everywhere.
- **Found:** no consistent error model / pagination. **Fixed:** §1.4 envelope + cursor pagination §1.5.

### Pass 3 — Extensibility, backward-compat, failure & security
- **Found:** dedicated-instance routing not in schema. **Fixed:** `tenants.datasource_ref` seam `[A7]`.
- **Found:** breaking-change risk on shared endpoints. **Fixed:** §1.11 additive-only rules + `/api/v2` for breaks + CI BC tests `[A23–A31]`.
- **Found:** payment-gateway-down would block bookings. **Fixed:** circuit-breaker `pay_at_clinic` fallback in §4 Payments `[FMEA]`.
- **Found:** PII could leak via logs / lost webhooks. **Fixed:** §1.12 redaction + reconciliation-poll fallback for webhooks.
- **Found:** usage/cost not captured per-API. **Fixed:** `usage_events` records every billable API → FinOps `[O2,O29]`.
- **Found:** kiosk device over-privileged. **Fixed:** `devices` scope `kiosk.walkin`, revocable `[K3,K7,K10]`.

All corrections are reflected in §1–§8 above (this is the corrected final).

---

## 10. Lean MVP slice vs deferred (contract reserved)
**MVP build now:** tenants/users/roles, auth (incl OTP), onboarding (+WhatsApp connect + readiness), clinic/doctor/availability config, booking engine + slots + tokens + queue + multi-patient + offline, payments (BYO) + refund policy, notifications (WhatsApp) + templates, emergency/wild-entry, patient/consent, config/flags, audit/outbox/idempotency, observability, CI/CD + data-preservation, analytics-basic, superadmin (tenants/plans/AI-registry/finops), embed widget + hosted page.
**Deferred (seam exists, additive later):** dedicated-instance datasource, HMS/EMR specific connectors (ABDM-first), patient documents/ABHA fetch-share, Reserve-with-Google, voice/PSTN expansion, plugins marketplace, advanced analytics/warehouse, standby VPS.

— End of contract v1 (post 3-pass review). Machine-readable: `openapi_v1.yaml`, `data_model_v1.sql`.

---

## §11. Reconciliation pass (round 4) — Requirement ↔ Component ↔ Contract ↔ Mock

Cross-checked all 199 requirement IDs against the component catalog (v2), the contract trio, and both mock files. Findings and the corrective actions applied:

**Gaps closed in the CONTRACT (this revision):**

- `F36` reason-for-visit had no carrier. Added `reason` to `BookingCreate`/`Booking` (OpenAPI) and `bookings.reason` + `booking_patients.reason` (DDL, additive).
- `F19` "I'm at the clinic" had no endpoint. Added `POST /bookings/{id}/in-premises` and `bookings.in_premises`.
- `F14` token slip QR/track-link not modeled. Added `tokens.short_code` + `qr_url` on the token sub-object.
- `F23` doctor "running late" not modeled. Added `POST /queue/{entry}/delay`, `sessions.delay_minutes`, and a `queue.delay_set` event that recomputes ETAs.
- Public landing under-specified. Added `ClinicPublic` schema with `queue_count` + `avg_wait_minutes` (F4/F5 live wait).
- `/bookings/{id}/shift` (earlier/later within availability) separated from `/reschedule` (different slot) — the mock had both behaviors but the API conflated them.
- Batch CSV export (`/integrations/appointments.csv`) and the LATER stubs (`/integrations/reserve-with-google`, `/reviews/request`, `review_requests`, `reserve_google_links` tables) reserved so adding them later stays additive (A24).

**Gaps closed in the MOCK (this revision):**

- Added reason-for-visit field to the patient booking-details screen (optional, English-always label).
- Added a doctor "Running late — set delay" control to the doctor queue screen.

**Notes / no-change-needed:**

- Fee precedence is documented: `tenant_config` fee override → doctor `fee_minor` → 0. UI shows the resolved fee only.
- Overuse check: component traceability v2 already showed 0 unused components and 0 uncovered requirement IDs; round-4 added no new components (all changes are fields/endpoints on existing components C-Booking, C-Queue, C-Token, C-PublicSite, C-Integrations).
- All DDL changes are `ADD COLUMN IF NOT EXISTS` / `CREATE TABLE IF NOT EXISTS` — no DROP/RENAME, satisfying the backward-compat release gate (A24–A31).

OpenAPI re-validated after edits: **52 paths, 7 schemas**.
