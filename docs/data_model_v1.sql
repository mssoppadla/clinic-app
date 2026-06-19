-- Clinic Booking SaaS — data model v1 (DDL sketch, PostgreSQL)
-- Conventions: uuid PKs; tenant_id on every tenant-owned table + Row-Level Security;
-- created_at/updated_at/deleted_at (soft delete); money in minor units (paise);
-- timestamps UTC; expand-contract migrations only (ADD, never DROP/RENAME in v1) [A24].
-- Booking domain is EVENT-SOURCED: booking_events is the source of truth; slots/queue/bookings are projections.

-- ---------- platform / tenancy ----------
CREATE TABLE tenants (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  slug text UNIQUE NOT NULL,
  name text NOT NULL,
  status text NOT NULL DEFAULT 'trial',          -- trial|active|suspended|offboarding|closed
  region text NOT NULL DEFAULT 'in',
  dedicated boolean NOT NULL DEFAULT false,
  datasource_ref text,                            -- dedicated-instance seam [A7]
  created_at timestamptz DEFAULT now(), updated_at timestamptz DEFAULT now(), deleted_at timestamptz
);

CREATE TABLE users (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  email citext UNIQUE,
  name text,
  auth_methods text[] DEFAULT '{}',               -- password|google|passkey
  mfa boolean DEFAULT false,
  status text DEFAULT 'active',                    -- active|revoked
  created_at timestamptz DEFAULT now(), updated_at timestamptz DEFAULT now()
);
-- many-to-many => multiple admins + cross-clinic membership [AC4, AC18]
CREATE TABLE user_roles (
  user_id uuid REFERENCES users(id),
  tenant_id uuid REFERENCES tenants(id),          -- null tenant = platform superadmin
  role text NOT NULL,                             -- superadmin|clinic_admin|doctor|front_desk|triage
  PRIMARY KEY (user_id, tenant_id, role)
);

CREATE TABLE api_keys (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL,
  hash text NOT NULL, scopes text[] DEFAULT '{}', last_rotated timestamptz, revoked boolean DEFAULT false,
  created_at timestamptz DEFAULT now());
CREATE TABLE devices (                            -- kiosk devices [K3,K10]
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL,
  token_hash text NOT NULL, scope text DEFAULT 'kiosk.walkin', revoked boolean DEFAULT false,
  created_at timestamptz DEFAULT now());

-- ---------- clinic configuration ----------
CREATE TABLE clinic_profile (
  tenant_id uuid PRIMARY KEY REFERENCES tenants(id),
  address jsonb, hours jsonb, holidays jsonb,
  branding jsonb,                                  -- logo, colors
  languages text[] DEFAULT '{en}',                 -- English always present [A15]
  updated_at timestamptz DEFAULT now());
CREATE TABLE departments (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL, name text, queue_rules jsonb);
CREATE TABLE doctors (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL,
  name text NOT NULL, specialty text, department_id uuid, fee_minor int DEFAULT 0,
  languages text[] DEFAULT '{en}', photo_url text, deleted_at timestamptz);
CREATE TABLE availability_blocks (                 -- custom timelines [F8,A18]
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL, doctor_id uuid NOT NULL,
  rrule text, start_time time, end_time time, slot_minutes int DEFAULT 30,
  capacity_per_slot int DEFAULT 2, breaks jsonb, deleted_at timestamptz);
CREATE TABLE leaves (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL, doctor_id uuid, date date, reason text);
CREATE TABLE tenant_config (                        -- versioned hot-reload config [A22,A27,A28]
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL,
  key text NOT NULL, value jsonb NOT NULL, version int NOT NULL DEFAULT 1, active boolean DEFAULT true,
  updated_at timestamptz DEFAULT now(), UNIQUE(tenant_id, key, version));
CREATE TABLE feature_flags (
  tenant_id uuid, flag text, enabled boolean DEFAULT false, PRIMARY KEY(tenant_id, flag));
CREATE TABLE plugins (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL, name text, enabled boolean DEFAULT false, config jsonb);

-- ---------- patients & consent ----------
CREATE TABLE patients (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL,
  phone text NOT NULL, name text, abha_ref text,
  created_at timestamptz DEFAULT now(),
  UNIQUE(tenant_id, phone));                        -- returning-patient match by phone [F10]
CREATE TABLE consents (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL, patient_id uuid,
  purpose text, version text, channel text, granted_at timestamptz DEFAULT now());  -- [F12,S5]

-- ---------- event store (source of truth) ----------
CREATE TABLE booking_events (                       -- append-only [A9,A10,A29]
  id bigserial PRIMARY KEY,
  event_id uuid UNIQUE NOT NULL, tenant_id uuid NOT NULL,
  event_type text NOT NULL, event_version int NOT NULL DEFAULT 1,
  aggregate_id uuid NOT NULL,                        -- booking/slot/queue id
  occurred_at timestamptz NOT NULL DEFAULT now(),
  actor jsonb, idempotency_key text, payload jsonb NOT NULL);
CREATE INDEX ON booking_events (tenant_id, aggregate_id, id);

-- ---------- projections / read models ----------
CREATE TABLE sessions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL, doctor_id uuid NOT NULL,
  date date NOT NULL, label text, start_time time, end_time time);
CREATE TABLE slots (                                 -- availability computed here [F7,F11c]
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL, doctor_id uuid NOT NULL,
  session_id uuid NOT NULL, start_ts timestamptz NOT NULL, end_ts timestamptz NOT NULL,
  capacity int NOT NULL, used int NOT NULL DEFAULT 0, status text DEFAULT 'open',
  UNIQUE(tenant_id, doctor_id, start_ts));
CREATE TABLE bookings (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL,
  primary_patient_id uuid, doctor_id uuid NOT NULL, channel text NOT NULL,        -- online|walkin|advance
  status text NOT NULL DEFAULT 'held',                                            -- held|confirmed|cancelled|completed|no_show|skipped
  party_size int NOT NULL DEFAULT 1, fee_total_minor int DEFAULT 0, currency text DEFAULT 'INR',
  payment_id uuid, created_via text, created_at timestamptz DEFAULT now());
CREATE TABLE booking_patients (                      -- one per patient in a multi-patient booking [F11a-c]
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL,
  booking_id uuid NOT NULL, patient_id uuid, slot_id uuid, name text,
  eta_ts timestamptz, status text DEFAULT 'confirmed');
CREATE TABLE tokens (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL,
  booking_patient_id uuid, clinic_session uuid, number text NOT NULL,
  provisional boolean DEFAULT false, device_id uuid,                              -- offline leased [F25]
  created_at timestamptz DEFAULT now());
CREATE TABLE token_leases (                          -- reserved blocks per device [F25]
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL, device_id uuid NOT NULL,
  session_id uuid, range_start int, range_end int, issued int DEFAULT 0, leased_at timestamptz DEFAULT now());
CREATE TABLE queue_entries (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL, session_id uuid NOT NULL,
  booking_patient_id uuid, position int, eta_ts timestamptz,
  state text DEFAULT 'waiting');                                                   -- waiting|now|done|skipped|wild_entry [F22,F30b]
CREATE TABLE waitlist (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL, doctor_id uuid, patient_id uuid, created_at timestamptz DEFAULT now());

-- ---------- emergency ----------
CREATE TABLE emergency_requests (                    -- [F28,F29,F30a,F30c]
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL,
  raised_by jsonb, matched_patient_id uuid, name text, phone text, reason text,
  decision text, decided_by uuid, decided_at timestamptz, created_at timestamptz DEFAULT now());

-- ---------- payments & refunds (clinic = merchant) ----------
CREATE TABLE payment_providers (                     -- bring-your-own [F31,S8,S9,D5]
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL,
  provider text, merchant_ref text, mode text, kyc_status text DEFAULT 'pending', connected_at timestamptz);
CREATE TABLE payments (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL, booking_id uuid,
  amount_minor int NOT NULL, currency text DEFAULT 'INR', provider text, provider_txn_id text,
  status text DEFAULT 'pending', idempotency_key text UNIQUE, created_at timestamptz DEFAULT now());
CREATE TABLE refunds (                               -- [F32]
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL, payment_id uuid,
  amount_minor int, reason text, status text DEFAULT 'requested', sla_due timestamptz, created_at timestamptz DEFAULT now());

-- ---------- our billing ----------
CREATE TABLE plans (                                 -- superadmin sets price [O26]
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), name text, model text, price_minor int, currency text DEFAULT 'INR', active boolean DEFAULT true);
CREATE TABLE subscriptions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL, plan_id uuid, status text, dunning_state text, started_at timestamptz);
CREATE TABLE offer_codes (                           -- [O27]
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), code text UNIQUE, discount jsonb, valid_from date, valid_to date, usage_limit int, used int DEFAULT 0);
CREATE TABLE invoices (                              -- GST [O28]
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL, period text, amount_minor int, gst_minor int, status text, issued_at timestamptz);
CREATE TABLE usage_events (                          -- ALL billable APIs -> billing + FinOps [O2,O10,O29]
  id bigserial PRIMARY KEY, tenant_id uuid NOT NULL,
  api text NOT NULL,                                 -- llm|bhashini|sarvam|whatsapp|telephony|gateway
  quantity numeric, cost_minor int, occurred_at timestamptz DEFAULT now(), ref jsonb);
CREATE INDEX ON usage_events (tenant_id, api, occurred_at);

-- ---------- channels / AI / integrations ----------
CREATE TABLE whatsapp_accounts (                     -- onboarding state machine + health [F3a-c,F2a]
  tenant_id uuid PRIMARY KEY, waba_id text, phone_number_id text,
  onboarding_state text DEFAULT 'signup',            -- signup|registered|webhooks|ready
  verification_state text DEFAULT 'unverified', quality text, tier text, updated_at timestamptz DEFAULT now());
CREATE TABLE message_templates (                     -- per-language [O20]
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL, name text, lang text,
  body text, approval_state text DEFAULT 'pending', version int DEFAULT 1);
CREATE TABLE notifications (                          -- outbox-backed [F33]
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL, type text, to_addr text,
  template text, status text DEFAULT 'queued', attempts int DEFAULT 0, created_at timestamptz DEFAULT now());
CREATE TABLE otp_challenges (                         -- [F17,F34,AC15]
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid, phone text, code_hash text,
  expires_at timestamptz, attempts int DEFAULT 0, created_at timestamptz DEFAULT now());
CREATE TABLE ai_models (                              -- PLATFORM-level registry [F44,O30]
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), task text, provider text, model text,
  key_ref text, active boolean DEFAULT false, fallback_of uuid);
CREATE TABLE rag_sources (                            -- [F43]
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL, name text, embedding vector(1536), content text);
CREATE TABLE hms_connectors (                         -- [O21]
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL, kind text, config jsonb, status text);

-- ---------- cross-cutting ----------
CREATE TABLE audit_log (                              -- [S4]
  id bigserial PRIMARY KEY, tenant_id uuid, actor jsonb, action text, target text, before jsonb, after jsonb, at timestamptz DEFAULT now());
CREATE TABLE outbox (                                 -- transactional dispatch [A17]
  id bigserial PRIMARY KEY, tenant_id uuid, event_type text, payload jsonb, status text DEFAULT 'pending', attempts int DEFAULT 0, created_at timestamptz DEFAULT now());
CREATE TABLE idempotency_keys (                       -- [A17]
  key text PRIMARY KEY, tenant_id uuid, response jsonb, created_at timestamptz DEFAULT now());
CREATE TABLE webhook_deliveries (                     -- outbound CRM [F48]
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL, url text, event_type text, status text, attempts int DEFAULT 0, created_at timestamptz DEFAULT now());

-- RLS (illustrative): enable on every tenant-owned table and force tenant_id = current_setting('app.tenant_id')
-- ALTER TABLE bookings ENABLE ROW LEVEL SECURITY;
-- CREATE POLICY tenant_isolation ON bookings USING (tenant_id = current_setting('app.tenant_id')::uuid);
-- (repeat for all tenant-owned tables) [A11,S14]

-- Concurrency for no-double-book [A10,F21]: book a slot inside a serializable txn:
--   SELECT used,capacity FROM slots WHERE id=$1 FOR UPDATE;  -- row lock
--   if used+party_units<=capacity then UPDATE slots SET used=used+party_units; else overflow to next slot / 409.

-- Backward-compat [A23-A31]: only ADD columns/tables; new columns NULLable or DEFAULTed; never DROP/RENAME in v1.

-- ============================================================
-- v1.1 additive corrections (from 4-way reconciliation) — expand-contract, all ADD-only [A24]
-- ============================================================
ALTER TABLE bookings        ADD COLUMN IF NOT EXISTS reason text;                 -- reason-for-visit [F36]
ALTER TABLE bookings        ADD COLUMN IF NOT EXISTS in_premises boolean DEFAULT false; -- 'I am here' [F19]
ALTER TABLE tokens          ADD COLUMN IF NOT EXISTS short_code text;             -- QR / track link on slip [F14]
ALTER TABLE sessions        ADD COLUMN IF NOT EXISTS delay_minutes int DEFAULT 0; -- doctor running late -> ETA recompute [F23]
ALTER TABLE booking_patients ADD COLUMN IF NOT EXISTS reason text;               -- per-patient reason (optional)

-- Reserve-with-Google / reviews (LATER stubs; reserved so adding them is additive) [F37,F38]
CREATE TABLE IF NOT EXISTS review_requests (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL, booking_id uuid,
  channel text DEFAULT 'whatsapp', status text DEFAULT 'queued', created_at timestamptz DEFAULT now());
CREATE TABLE IF NOT EXISTS reserve_google_links (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL, doctor_id uuid, url text, created_at timestamptz DEFAULT now());
