# Master Delivery Plan — From Walking Skeleton to GA ("Done") — v1

**Project:** AI patient appointment + queue SaaS (Kerala / Malayalam-first), co-hosted with `cpmai-prep` on Hostinger VPS.
**Status:** Consolidated planning artifact for sign-off. **No code is written until this is approved.**
**Supersedes the roadmap section of** `PHASE_PLAN_and_SKELETON_SPEC_v1.md` (which remains the detailed Phase-0 spec).
**Companion docs:** `CONTRACT_UI_API_DB_v1.md`, `openapi_v1.yaml`, `data_model_v1.sql`, `Component_Catalog_and_Traceability_v2.xlsx`, `REQUIREMENTS_REGISTER_v13.xlsx`, `Reconciliation_4Way_Matrix.xlsx`, `Mockups_v2_AllPersonas.html`, `Mockups_v3_Bilingual.html`.

---

## 1. How to read this document
This is the single source of truth for *what it takes to call the project Done*. It has three layers:

1. **Feature phases (0–12)** — each delivers a thin vertical slice that runs in production and is proven by an automated end-to-end test against production. Sequenced by risk/dependency.
2. **Hardening & launch milestones (H1–H6, GA, Post-GA)** — focused certification passes (compliance, security, SRE, scale, pilot, docs) that gate go-live. They are *not* feature slices but are still validated in prod.
3. **Continuous tracks** — concerns (CI/CD, security, compliance, observability, backward-compat, FinOps, accessibility, docs) that *every* phase must satisfy on the way out the door, not a late add-on.

The governing rule is unchanged: **every phase ends with a working system in production, end-to-end testable in production** — never UI-alone, DB-alone, or integrate-at-the-end.

---

## 2. Governing principle & the four always-ship mechanics

Vertical slices, not horizontal layers. Walking skeleton first; then graft one integration per phase onto the live spine; every merge is shippable because unfinished work ships dark.

| Mechanic | Purpose |
|---|---|
| **Feature flags (dark launch, off by default)** | Unfinished work deploys to prod hidden; flip when ready. |
| **Blue-green + ≥2 replicas** | New build runs beside old, health-gated; instant rollback; no downtime to clinics / cpmai / marketing site. |
| **Seeded `__canary__` tenant in prod** | A synthetic clinic exercised by tests, isolated by `tenant_id` + RLS; excluded from billing/analytics. |
| **Prod E2E smoke** | The merge-gating E2E suite re-runs against prod post-deploy; red → auto-rollback. |

---

## 3. Feature phases (each ships a vertical slice to production)

| Phase | Vertical slice shipped to prod | New integration pierced | Prod E2E proof (canary tenant) | Exit gate |
|---|---|---|---|---|
| **0 — Walking skeleton** | 1 clinic, hosted page books 1 token, appears in queue; WhatsApp confirmation; Malayalam labels via Bhashini. | Multi-tenancy/RLS, event→projection, WhatsApp Cloud API, Bhashini, CI/CD, blue-green, Caddy TLS, secrets, logging | Book → token → queue → WA msg → Malayalam render (+fallback). All green in prod. | Pipeline green; cpmai untouched; rollback proven |
| **1 — Booking depth** | Multi-patient (≤3) overflow, reason-for-visit, slot vs join-queue, reschedule/shift, cancel. | Strong-consistency slot locking | Multi-patient atomic book; concurrent double-book rejected | Concurrency proven in prod |
| **2 — Staff & front desk** | Staff auth (passkey/Google/password+MFA), front-desk console, walk-in, correct-existing, no-show recall, emergency wild-entry shift. | Staff JWT + role scopes; queue reorder | Walk-in → auto-shift → ETAs recompute; emergency highlights row | Role isolation verified |
| **3 — Doctor & live queue** | Doctor console, done/skip/recall, running-late delay, availability timelines/leave. | Real-time push (SSE/WS) | +15m delay → live ETA update in prod | Real-time path stable |
| **4 — Payments (bring-your-own)** | Clinic connects own gateway; prepay + pay-at-clinic fallback; one-tap refund. | Payment gateway (clinic = merchant of record) | ₹1 sandbox order → confirm; refund; gateway-down fallback | RBI/KYC sequencing correct; we never hold funds |
| **5 — AI booking layer** | LLM NLU booking, RAG/FAQ, ASR/TTS voice; guardrail (LLM never finalizes a slot); per-call metering. | LLM + voice; usage metering | NLU books via guardrail; calls land in `usage_events`; cost in FinOps | Guardrail + metering verified |
| **6 — Offline kiosk / PWA** | PWA install, service worker, IndexedDB outbox, leased token blocks, reconcile on sync. | Offline-first + lease/reconcile | Offline → provisional tokens → clean reconcile | No duplicate/lost tokens |
| **7 — Onboarding self-serve** | Readiness engine (mandatory/optional), Embedded Signup, provider override, channel-health monitor. | Embedded Signup full flow | New clinic self-onboards to READY; override audited | Go-live gating correct |
| **8 — Platform / superadmin** | Plans, offers, GST, FinOps profit per clinic (all APIs metered incl Bhashini), platform AI model registry. | Billing/invoicing | Superadmin sees per-clinic cost vs revenue | Metering reconciles |
| **9 — Notifications & reminders** *(was left out)* | Reminder offsets, your-turn alert, delay/no-show notices, opt-in/opt-out, template manager, multi-channel (WhatsApp/SMS/voice). | Scheduler + template engine; SMS fallback | Reminder fires at configured offset; opt-out honored; template localized | Quiet-hours + consent respected |
| **10 — Integrations & interoperability** *(was left out)* | HMS connectors, CRM outbound webhooks, CSV export, Reserve-with-Google, review requests, **ABHA/ABDM** (India health stack) hooks. | External HMS/CRM + ABDM | Booking syncs to HMS; webhook delivered; ABHA ref captured | Tolerant readers; retries/idempotent |
| **11 — Embed & custom domain** *(was left out)* | Web Component embed (Shadow DOM) on clinic's own site, CNAME custom domain, white-label branding. | CNAME TLS + cross-origin embed | Embed books on clinic domain; custom-domain TLS issued | URL stays clinic's; isolation intact |
| **12 — Full bilingual & accessibility** *(was left out)* | Malayalam across ALL screens (English-always baseline), other-language config, WCAG 2.2 AA, large-target/voice accessibility. | i18n pipeline + a11y tooling | Every screen renders bilingual; axe/a11y suite passes | A15 honored everywhere |

---

## 4. Hardening & launch milestones (gates to "Done")
These were the major gaps. They are certification passes layered over the feature phases — each validated in prod.

| Milestone | Scope (what was left out) | Acceptance / proof |
|---|---|---|
| **H1 — Compliance & data governance** | DPDP + GDPR + CCPA: consent management, data-subject access/erasure/export, retention & deletion policies, DPA templates, **Responsible/Explainable/Ethical/Transparent AI** documentation, audit-log completeness, India data residency attestation. | DSAR export & erase run end-to-end on canary; AI decisions explainable & logged; retention jobs verified; governance register signed. |
| **H2 — Security hardening & pen test** | Threat model (STRIDE), WAF, DDoS protection, secrets rotation, dependency/SCA scanning, container hardening, third-party penetration test, remediation. | gitleaks + SCA clean; pen-test report with criticals/highs closed; no PII in logs (audited). |
| **H3 — Observability, SRE & DR** | Metrics/dashboards, alerting, **SLA 3h**, on-call + incident runbooks, structured-log retention, backups + restore drills, disaster recovery, chaos/failover test. | Restore-from-backup drill passes; alert fires & pages; failover keeps clinics + cpmai up. |
| **H4 — Scale & performance** | Load test to **100+ clinics** concurrent, capacity plan, autoscale/replica tuning, query/cost optimization, **dedicated-instance tier** via `tenants.datasource_ref`. | Load test meets latency targets at 100 clinics; dedicated tenant runs on isolated datasource. |
| **H5 — Pilot / beta with real clinics** | Controlled rollout to a few Kerala clinics, feedback loop, rework iterations, documented learnings applied successively. | Pilot clinics live in prod; feedback logged & top issues reworked; learnings doc updated. |
| **H6 — Documentation & enablement** | Self-serve user manual (the step-by-step screens), admin guide, integrator API docs (from `openapi_v1.yaml`), support KB, training material. | Docs published; a new clinic onboards using only the manual. |
| **GA — Production launch & go-to-market** | Marketing site, pricing/signup funnel, billing live, public launch, status page. | Real clinic signs up & pays via public funnel; status page live. |
| **Post-GA — Continuous improvement** | Analytics-driven iteration, A/B testing, AI model retraining/eval, roadmap intake, ongoing rework-feedback loop. | Improvement cadence running; learnings continuously documented & applied. |

---

## 5. Continuous tracks (every phase must satisfy these on exit)
Not phases — standing requirements checked at each phase gate, then certified at the matching hardening milestone.

| Track | Per-phase expectation | Certified at |
|---|---|---|
| **CI/CD + quality + logging** | Lint, tests, gitleaks, build, blue-green, prod smoke green every merge. | continuous |
| **Reusable modular architecture** | New work as modules/plugins; no monolith creep; reuse cpmai R1–R14. | continuous |
| **Trustworthy / Responsible / Explainable AI** | AI calls logged, explainable, guardrailed, metered. | H1 |
| **Security & no PII disclosure** | RLS, tenancy server-resolved, PII redaction, least privilege. | H2 |
| **No secrets in code/deploy** | Secrets from manager only; gitleaks hard gate. | H2 |
| **No hardcoding / fully configurable** | All tunables from env + versioned `tenant_config`. | continuous |
| **Contract-first (UI↔API↔Backend)** | Change contract + tests before code; tolerant readers. | continuous |
| **Local testing before commit** | Ephemeral compose E2E gates the merge. | continuous |
| **Backward compatibility (release gate)** | Additive migrations only; `/api/v2` for breaks; flags off by default; BC tests. | continuous |
| **Containerized** | Everything in Docker; immutable SHA-tagged images. | continuous |
| **FinOps / cost metering** | Every external API metered per clinic. | H4 |
| **Accessibility & i18n** | English-always baseline; bilingual where opted; WCAG progress. | Phase 12 |
| **Documented learnings** | Rework/feedback captured & applied next iteration. | H5 / Post-GA |
| **cpmai data preservation** | Row-count snapshot guard on every deploy; additive only. | continuous |

---

## 6. Project-level Definition of Done (GA criteria)
The project is "Done" (GA-ready) when ALL of the following hold in production:

1. Feature phases 0–12 shipped to prod, each with its prod E2E smoke permanently green.
2. H1 compliance certified: DSAR (export + erase) works; consent + retention enforced; Responsible/Explainable AI documented; India residency attested; GDPR/CCPA/DPDP mapped.
3. H2 security: pen-test criticals/highs remediated; gitleaks + SCA clean; no PII in logs; tenancy isolation proven under attack scenarios.
4. H3 SRE/DR: SLA 3h instrumented; backups + restore drill passed; failover keeps cpmai + clinics up; runbooks exist.
5. H4 scale: load test passes at 100+ clinics; dedicated-instance tier proven on isolated datasource.
6. H5 pilot: real Kerala clinics live; feedback reworked; learnings documented.
7. H6 docs: a new clinic can self-onboard from the manual alone; integrator API docs published.
8. GA: public signup → pay → onboard funnel works end-to-end in prod; status page live.
9. Every continuous track green at its certification milestone.
10. Zero open Sev-1/Sev-2; cpmai + all real-clinic data provably intact across the whole program.

---

## 7. Sign-off checklist (before any code)
- [ ] Vertical-slice-to-prod approach + walking skeleton first — approved.
- [ ] Feature phase set 0–12 (incl. newly added 9–12) — approved or re-ordered.
- [ ] Hardening/launch milestones H1–H6 + GA + Post-GA — approved.
- [ ] Continuous tracks list — approved as per-phase exit criteria.
- [ ] Project Definition of Done (GA criteria) — approved.
- [ ] On approval: generate typed API client from `openapi_v1.yaml` + Alembic baseline from `data_model_v1.sql`, then build the Phase-0 skeleton per `PHASE_PLAN_and_SKELETON_SPEC_v1.md`.

> Open input still needed (from earlier): whether live Meta/WhatsApp + Bhashini credentials & a test number are provisioned for Phase 0, or Phase 0 starts on stubs and swaps to live as a fast-follow.
