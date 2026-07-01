# Appointment Workflow + WhatsApp Templates — Detailed Implementation Spec (v1)

> Status: **DRAFT for review** — no code written yet. This spec turns the WhatsApp two-way
> agent + booking engine into a full, multi-clinic, **admin-controlled** appointment workflow with
> Meta-approved message templates. Every per-clinic behavior is configurable in the admin UI with
> **no code changes and no deploys**.

Decisions locked with the product owner:
1. **Reminders run in a separate `worker` container** (DB-claim, multi-replica safe).
2. **Templates are created AND submitted to Meta from our own admin UI** (Business Management API),
   with approval status tracked in-app — nobody touches Meta Business Manager.
3. This spec is produced **before** any code.

---

## 0. Principles
- **Config-driven, admin-controlled:** all per-clinic behavior (which notifications, wording
  variables, timing, language, template choice) lives in the DB and is editable in the admin UI.
  No hardcoding, no redeploy to change behavior. (Ref: `config-driven-not-hardcoded`.)
- **Additive & backward-compatible** migrations only; existing bookings/sends keep working.
- **Idempotent & metered:** every message is deduped and recorded in `usage_events`.
- **Multi-tenant:** one shared Tovaitech WABA + optional per-clinic own WABAs.

## 1. Meta rules that shape the design (why it's built this way)
- **24-hour session window:** after a patient messages the clinic, free-form `send_text` is allowed
  for 24h. **Outside** that window, only **pre-approved templates** may be sent. → Reminders and any
  business-initiated message MUST be templates; live conversation replies stay free-form.
- **Templates live on a WABA** and have `category` = UTILITY | MARKETING | AUTHENTICATION. Each is
  **approved by Meta asynchronously** (minutes–hours). Body carries positional variables `{{1}}…{{n}}`
  plus optional header (text/media), footer, and buttons (quick-reply / call-to-action).
- **Template ownership follows the sending number's WABA.** You cannot have per-clinic template
  *bodies* on the shared Tovaitech WABA — so shared-number clinics are customized via **parameters**
  (clinic name/address injected as variables). Own-number clinics register their **own** WABA's
  approved templates.
- **Business Management API** (used by the submit-from-UI feature):
  - Create: `POST /{waba_id}/message_templates`
  - Status/list: `GET /{waba_id}/message_templates?fields=name,status,category,id`
  - Send: `POST /{phone_number_id}/messages` (type=`template`, components→parameters)
- **Interactive replies:** quick-reply buttons produce inbound webhook payloads of `type=="button"`
  (and `interactive`) carrying the button payload — this is how Confirm/Reschedule/Cancel round-trip.

## 2. What already exists (build on, don't rebuild)
- Booking engine (event-sourced): `create_booking`, **`cancel_booking`**, **`reschedule_booking`**,
  refund hook, per-slot capacity + row-lock. Events: `BookingRequested/Confirmed/Cancelled/Rescheduled`.
- WhatsApp agent (menu + AI) for book-slot / join-queue / queue-status; replies via `send_text`.
- `send_template(tenant_id, to_phone, template, params)` — but template **names are hardcoded**
  (`auth_otp`, `booking_confirmed`); no catalog, no per-clinic mapping, no param abstraction.
- Per-clinic config: `integration_config` (`clinic:<tenant_id>` scope) + flags; clinic branding in
  `Tenant.branding` JSON; metering in `usage_events` (partial).
- **No scheduler** anywhere (Redis provisioned, unused). Reminders are net-new infra.

## 3. Data model (all additive migrations)

### 3.1 `message_templates` — the catalog (platform + per-clinic)
| Column | Type | Notes |
|---|---|---|
| id | uuid pk | |
| scope | str | `platform` \| `clinic:<tenant_id>` — which WABA/owner owns this template |
| event_type | str null | `booking_confirmed`\|`reminder`\|`booking_cancelled`\|`booking_rescheduled`\|`queue_token`\|`your_turn`\|`otp`\|`feedback` |
| meta_name | str | the Meta template name |
| language | str | `en_US`, `ml_IN`, … (Meta needs one approved template **per language**) |
| category | str | UTILITY \| MARKETING \| AUTHENTICATION |
| components | JSON | header/body/footer/buttons as submitted to Meta |
| param_map | JSON | ordered resolver keys for `{{1}}…{{n}}` (see §5) |
| meta_status | str | `draft`\|`pending`\|`approved`\|`rejected`\|`disabled` |
| meta_template_id | str null | Meta's returned id |
| rejection_reason | str null | |
| created_at / updated_at | ts | |

Unique: `(scope, meta_name, language)`.

### 3.2 `clinic_message_settings` — per-clinic, per-event config
| Column | Type | Notes |
|---|---|---|
| id | uuid pk | |
| tenant_id | str | |
| event_type | str | |
| enabled | bool | default per event (confirmation on; feedback off) |
| template_id | fk→message_templates null | resolves to a platform default or the clinic's own |
| language | str null | override; else clinic default (`Tenant.languages[0]`) |
| variables | JSON | clinic static values: `display_name`, `address`, `footer`, … |
| reminder_offsets | JSON null | only for `reminder` — minutes-before list, e.g. `[1440, 180]` |
| updated_at | ts | |

Unique: `(tenant_id, event_type)`.

### 3.3 `notifications` — idempotency + delivery log + audit
| Column | Type | Notes |
|---|---|---|
| id | uuid pk | |
| tenant_id | str | |
| booking_id | str null | |
| event_type | str | |
| template_id | fk null | |
| to_phone | str | |
| status | str | `queued`\|`sent`\|`failed`\|`skipped` |
| dedupe_key | str **unique** | e.g. `"{booking_id}:{event_type}:{offset}"` — the natural claim |
| wa_message_id | str null | Meta id (also correlates to `whatsapp_messages`) |
| params | JSON | resolved params (audit/preview) |
| error | str null | |
| created_at / sent_at | ts | |

The **unique `dedupe_key`** is the concurrency claim: even if two workers scan simultaneously, only
one insert wins; the other gets a conflict and skips. → No separate `reminder_jobs` table needed;
"due" is derived from `bookings` + absence of a `notifications` row.

### 3.4 `Tenant.timezone` (new column, default `Asia/Kolkata`)
Needed for correct reminder offsets and human-readable date/time in templates.

## 4. Notification dispatcher — `app/domain/notifications.py`
`notify(*, event_type, tenant_id, to_phone, booking_id=None, context: dict, offset=None) -> dict`
1. Load `clinic_message_settings(tenant, event_type)`; if `disabled` → insert `skipped` row, return.
2. Resolve template: clinic `template_id`, else the platform default for `event_type` on the
   effective sending WABA; require `meta_status == approved` for out-of-session events.
3. Build `dedupe_key`; if a `queued|sent` notification already exists → skip (idempotent).
4. Resolve params from `param_map` + `context` + clinic `variables` (§5).
5. **Channel choice:** in-session (patient messaged < 24h ago) AND event permits text → `send_text`;
   otherwise `send_template`. Reminders always template.
6. Insert `notifications`(queued) → send → update `sent|failed` + `wa_message_id` → meter
   `usage_events(provider="whatsapp", kind="template"|"session", meta={event_type, template})`.

Call sites (replace hardcoded sends):
- `create_booking` → `notify("booking_confirmed")`
- `cancel_booking` → `notify("booking_cancelled")`
- `reschedule_booking` → `notify("booking_rescheduled")`
- token/queue → `notify("queue_token")`, `notify("your_turn")`
- auth OTP → `notify("otp")` (kept working; may migrate later)
- worker → `notify("reminder", offset=…)`

## 5. Parameter resolution (`param_map`) — fully admin-reorderable
`param_map` is an ordered list; each entry names a **resolver key** (+ optional format). Resolvers are
computed from booking + clinic + context:
`patient_name, doctor_name, clinic_name, clinic_address, appointment_datetime, appointment_date,
appointment_time, token_number, queue_position, eta, refund_amount, reschedule_hint, …`
The admin orders these to match the `{{1}}…{{n}}` order of the template they got approved — so a
different template layout needs **no code change**. Static per-clinic values (name/address/footer)
come from `clinic_message_settings.variables`. Header/button parameters map the same way.

## 6. Meta Business Management integration — `app/integrations/whatsapp_templates.py`
Per-WABA-scope (reuses that scope's `token` + `business_account_id` from `integration_config`):
- `create_template(scope, defn)` → `POST /{waba_id}/message_templates` → store `meta_template_id`, `meta_status=pending`.
- `refresh_status(scope)` → `GET …/message_templates` → update `meta_status`/`rejection_reason`.
- `delete_template(scope, name)`.
Approval is async → surfaced as a **status pill** + a **Refresh** action; the worker also polls
periodically so `approved` flips automatically.

## 7. Reminder worker — new `worker` container
- New `worker` service in `deploy/docker-compose.yml`: **same image**, entrypoint `python -m app.worker`.
- Loop every `WORKER_TICK_SECONDS` (config, default 60):
  1. Take a Postgres **advisory lock** (one scanner at a time — optimization).
  2. For each clinic with `reminder` enabled, for each configured offset: find `confirmed` bookings
     whose `slot.start_ts` ∈ `[now+offset, now+offset+tick_window)` with **no** `notifications` row
     for `(booking, reminder, offset)` → `notify("reminder", offset)`.
  3. Periodically call `refresh_status` for each WABA scope to advance template approvals.
- **Multi-replica safety:** the `notifications.dedupe_key` unique constraint is the real guard; the
  advisory lock just avoids redundant scans. Zero double-sends even without the lock.
- Uses clinic `Tenant.timezone` for windows + display.

## 8. Two-way lifecycle in the agent (Slice 4)
- Extend webhook `_iter_inbound` to also parse `type in {button, interactive}` → payload like
  `CONFIRM:<booking_id>` / `RESCHEDULE:<booking_id>` / `CANCEL:<booking_id>`.
- Agent gains `confirm` / `reschedule` / `cancel` intents (menu + AI tools) reusing the existing
  `reschedule_booking` / `cancel_booking` domain ops; reschedule reuses the slot picker.
- Reminder template ships with quick-reply buttons carrying the `booking_id` payload.

## 9. Admin UI (no code / no deploy)

### 9.1 Clinic admin — **Notifications** page (`web/notifications.html`, clinic-scoped)
Per event type (Confirmation, Reminder, Cancellation, Reschedule, Token/Your-turn, OTP):
- **Enabled** toggle · **Language** · **Template** picker (platform default / clinic's own approved)
- **Clinic variables** (display name, address, footer) · **Reminder offsets** (reminder only)
- **Preview** (renders body with sample data) · **Test-send** (to a number)
- **Delivery log**: recent `notifications` (event, to, status, time) + usage counts.

### 9.2 Platform admin — **Templates** tab (extend `web/platform-admin.html`)
- Compose a template (header/body/footer/buttons + category + language) → **Submit to Meta** →
  status pill (pending/approved/rejected + reason) → **Refresh** · map param order to an `event_type`.
- Scoped to a WABA (`platform:test` / `platform:live`).

### 9.3 API endpoints
Clinic (`require_clinic_staff`):
- `GET /clinic/notifications` · `PUT /clinic/notifications/{event_type}` ·
  `POST /clinic/notifications/{event_type}/test` · `GET /clinic/notifications/log`
Platform (`require_role("superadmin")`):
- `GET/POST/PUT /admin/platform/templates` · `POST /admin/platform/templates/{id}/submit` ·
  `POST /admin/platform/templates/refresh`

## 10. Metering
Every dispatch → `usage_events(provider="whatsapp", kind="template"|"session", units=1,
meta={event_type, template, category})`. Powers per-clinic billing + a usage view.

## 11. Rollout (vertical slices — each shipped to prod behind the gates)
| Slice | Scope | Ships |
|---|---|---|
| **1. Templates foundation** | `message_templates` + `clinic_message_settings` + `notifications` tables; dispatcher; **Meta submit-from-UI** (platform Templates tab: compose → submit → status); refactor `booking_confirmed` through the dispatcher; clinic Notifications page for Confirmation (toggle/lang/vars/preview/test); metering | First real approved template sends via config |
| **2. Full lifecycle** | Wire `booking_cancelled`, `booking_rescheduled`, `queue_token`, `your_turn` through the dispatcher + catalog | All transactional notifications |
| **3. Reminders** | `worker` container + `Tenant.timezone` + per-clinic offsets + reminder template + status-poll loop | Automated reminders |
| **4. Two-way** | Interactive buttons in webhook + reschedule/cancel/confirm intents in the agent | Patients self-serve from a reminder |
| **5. Polish** | Feedback/marketing templates, usage dashboard, per-language rollout (ml_IN) | Optional extras |

Each slice: local SQLite tests → `scripts/local_check.sh` (real-Postgres preflight) → PR → deploy-approve.

## 12. Open assumptions to confirm
1. **Timezone:** add `Tenant.timezone` (default `Asia/Kolkata`), editable per clinic. ✔ assumed.
2. **Languages first:** start with `en_US`; add `ml_IN` (Malayalam) in Slice 5 — each language is a
   separately-approved Meta template.
3. **OTP:** leave the working `auth_otp` path as-is; migrate into the catalog later (non-breaking).
4. **Scope of messaging:** transactional/UTILITY only for now; marketing/broadcast is out of scope.
5. **Refund line** in the cancellation template is informational until a payment gateway lands
   (refund hook already exists but no gateway).
6. **Default-on notifications:** Confirmation + Reminder + Cancellation + Reschedule ON by default;
   Token/Your-turn ON for queue clinics; Feedback OFF.

---

## 13. Shared-number routing, discovery & the clinic front-door (QR + link)
Serving **100s of clinics on the shared Tovaitech number(s)** without overwhelming patients with a
"which clinic?" list. Core principle: **the entry point carries the clinic identity — the patient
almost never chooses.** Selection is implicit; a short fallback ladder handles cold arrivals.

### 13.1 Primary — identity-carrying entry points (kills ~95% of ambiguity)
- Each shared-number clinic gets a **unique deep link + printable QR**:
  `https://wa.me/<shared-digits>?text=Book%20at%20<slug>` (helper already exists: `deep_link(slug)`).
  Tapping opens the chat pre-filled → the first message carries the slug → bound to that clinic
  (existing `WhatsAppBinding`), remembered for follow-ups.
- **Capture Meta's `referral` object** on inbound (Click-to-WhatsApp ads / entry points pass
  `source_id`/`ctwa_clk_id`/`headline`) → auto-bind with zero typing.
- **Where the clinic shares it:** its web booking page, reception QR poster, Google Business profile,
  Instagram/WhatsApp bio, SMS. → **Surfaced in the clinic admin (this slice, §13.5).**

### 13.2 Fallback ladder for a cold patient (texts the shared number with no context)
Resolve in order — each step a single interaction, never a list of 100s:
1. **Remembered default** — "Book again at GreenClinic?" (one tap) for returning patients.
2. **Short clinic code** — ask once; clinics print a memorable `short_code` (e.g. `GREEN12`).
3. **Location / city-area** — patient shares WhatsApp **location** (inbound `type=location` lat/lng) or
   types a **pincode / city / area** → filter clinics by proximity → present **nearest 3–5**.

### 13.3 Interactive selection — never a numbered text wall
Any actual choosing uses WhatsApp **Reply Buttons** (≤3) and **List Messages** (≤10 rows, sectioned
by city/area). Rule: **narrow by location first, then present ≤10.**

### 13.4 Scaling the shared number (Meta constraints — plan for it)
- **Messaging tier limits are per phone number** (250→1K→10K→100K→unlimited/24h); one number can't
  carry 100s of clinics at volume.
- **Quality rating is per number and shared** — one clinic's complaints throttle everyone on it.
- → **Number pool:** run several Tovaitech numbers and **shard clinics across them** (routing already
  by `phone_number_id`→clinic). **Graduate high-volume clinics to their own WABA/number** (already
  supported). Hybrid: shared pool for the long tail, own number for big clinics.

### 13.5 Data model & webhook additions
- `Tenant`: `city`, `area`, `pincode`, `lat`, `lng`, `short_code` (unique). Geo powers §13.2.3.
- `platform_numbers` (pool): `phone_number_id`, `display_number`, `waba_id`, `active` — and which
  clinics are assigned to each (for sharding + choosing the right `deep_link` number).
- Webhook `_iter_inbound`: also parse `type in {location, referral, button, interactive}`.
- Patient default clinic(s): extend `WhatsAppBinding` (already remembers one; allow a small MRU list).

### 13.6 Admin UI — clinic front-door (THIS SLICE, ships first)
On the clinic admin, **once the clinic opts into the shared number**, show a **"Your booking link &
QR"** block: the `wa.me` deep link (copyable), a rendered **QR code** (client-side, vendored lib —
no server dep, no external call), a **Download QR** (PNG) button, and short **share guidance**
("Put this QR at reception; add the link to your website / Google profile / Instagram bio").
No Meta approval or new infra needed — self-contained, so it ships as the first slice.

### 13.7 Rollout placement
- **Slice A (now):** clinic front-door — QR + shareable link in the clinic admin (§13.6).
- **Slice B (with the discovery work):** short code + location/pincode fallback + interactive
  List/Button selection + referral capture (§13.1–13.3, §13.5).
- **Slice C (scale):** number pool + clinic→number sharding + own-number graduation (§13.4).
