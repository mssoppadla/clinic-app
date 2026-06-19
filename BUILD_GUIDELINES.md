# Build Standards & Living Checklist  📌 READ BEFORE EVERY TASK

This is the single source of truth for *how* we build. Any repeatable task (a new screen, an
API, a migration, an integration) **must** start by opening this file and ticking the matching
checklist. It is a **living document**: when a new recommendation is agreed, it is added to the
"Living UI recommendations log" (§H) and becomes mandatory **from the next screen/task onward** —
no need to repeat the instruction each time.

> Rule of thumb: if you find yourself giving the same feedback twice, it belongs in this file.

---

## A. Golden rules (always apply)
- [x] **Mock-first.** Open `Mockups_v2_AllPersonas.html` (English-default) and `Mockups_v3_Bilingual.html` (bilingual) and find the matching screen BEFORE writing UI. Match layout, spacing, components, copy.
- [x] **One stylesheet.** All UI uses `web/styles/app.css` (palette + components extracted from the mock). **No hardcoded hex, no inline color styles** in pages. Add a reusable class to `app.css` instead.
- [x] **English always + Malayalam in addition (A15).** Every label is English first; Malayalam (or opted language) is a `.ml` second line. Never language-only.
- [x] **Config-driven, no hardcoding.** Branding/colors/labels/fees come from the API/config, not literals.
- [x] **No secrets in code.** Secrets only via env/secrets manager; `gitleaks` gate.
- [x] **Backward compatible.** Additive changes only (expand-contract); never break existing screens/data.
- [x] **Local before commit.** `bash scripts/local_check.sh` must be green.

---

## B. New screen / UI — checklist (copy into the task, tick each)
- [ ] Found the matching mock screen in v2 + v3; noted its layout, components, copy.
- [ ] Built using only `app.css` classes; any new pattern added there as a reusable class (not inline).
- [ ] Colors/branding pulled from the clinic API (`branding.color/accent`), never hardcoded.
- [ ] English-always; Malayalam second line via the Bhashini-backed labels (`data-k` + `.ml`).
- [ ] Responsive: `.wrap` max-width container, **≥48px tap targets**, `viewport-fit=cover` safe-area, `Noto Sans Malayalam` in the font stack. Verified at phone / tablet / laptop widths.
- [ ] Buttons on colored backgrounds: bilingual second line is light/legible (`.btn .ml`), not muted-gray.
- [ ] States handled: loading, empty, error (use `.err`), disabled.
- [ ] Accessibility: real `<label>`s, focus outlines, sufficient contrast, semantic headings.
- [ ] Out-of-scope mock elements (e.g. slots, Reserve-with-Google in Phase 0) shown per mock but with an honest "coming soon / enabled later" behavior — never a silent dead button.
- [ ] Cache-bust changed assets (`app.css?v=N`); bump N when CSS changes.
- [ ] Matches the mock at a glance (side-by-side check) before calling it done.

## C. New API endpoint — checklist
- [ ] Defined in the contract first (`openapi_v1.yaml`) and traced to a requirement ID.
- [ ] Tenancy resolved server-side (never client-supplied for scoping); scoped via `TenantScope`.
- [ ] Uniform error envelope; idempotency header where it mutates; pagination where it lists.
- [ ] Money in minor units; times UTC; tolerant readers (ignore unknown fields).
- [ ] Unit + integration test added, mapped in `test_phase0_matrix.py` (or the phase's matrix test).

## D. DB / migration — checklist
- [ ] Additive only (`ADD COLUMN/TABLE IF NOT EXISTS`); no DROP/RENAME (`check_additive_migration.py` passes).
- [ ] `tenant_id` on every tenant table + RLS policy in the baseline.
- [ ] No PII in logs; PII columns documented.
- [ ] Idempotent seed updated if reference data changed.

## E. Integration (WhatsApp/Bhashini/payments/AI) — checklist
- [ ] Provider interface + `stub`/`live` selected by env (no code change to go live).
- [ ] Graceful fallback (never breaks the user flow); failures logged, not thrown to the request.
- [ ] Every external call metered into `usage_events` (FinOps).
- [ ] Secrets from env only.

## F. Before commit
- [ ] `bash scripts/local_check.sh` green (compile + additive-migration + tests + secret scan).

## G. Before deploy (prod)
- [ ] CI green (lint, tests, gitleaks, pip-audit).
- [ ] `cpmai-guard.sh snapshot` taken; `blue-green.sh` health-gates + runs `e2e/smoke.py`; data-guard `verify` passes; auto-rollback on failure.

---

## H. Living UI recommendations log (append-only — each becomes mandatory from the next screen)
| # | Date | Recommendation | Applies from |
|---|------|----------------|--------------|
| 1 | 2026-06-19 | Mock-first: always reference Mockups_v2/v3 for the screen before building. | all screens |
| 2 | 2026-06-19 | All styling via shared `web/styles/app.css`; no inline/hardcoded colors. | all screens |
| 3 | 2026-06-19 | Brand color comes from clinic `branding` (mock green `#0e7c66` default), not a literal blue. | all screens |
| 4 | 2026-06-19 | Bilingual second line on colored buttons must be light/legible, not muted-gray. | all screens |
| 5 | 2026-06-19 | Follow the mock's full journey/layout (identity, stats, primary CTA, secondary buttons, footnote), not a simplified version. | all screens |
| 6 | 2026-06-19 | Cache-bust CSS with `?v=N` and bump on change; hard-refresh to verify. | all screens |
| 7 | 2026-06-19 | Non-functional (future-phase) mock elements shown for parity but with an honest "coming soon" behavior. | all screens |
| 8 | 2026-06-19 | Secrets in admin/config screens are write-only: never echo a saved value, show only 'configured ✓'; entering blank keeps the existing secret. | all config screens |
| 9 | 2026-06-19 | Integration/config changes take effect at runtime (env+DB effective config), no restart; provide a Test action that reports live-vs-fallback. | all integrations |
| 10 | 2026-06-19 | Admin/platform screens may be English-only (A15 'English always' satisfied); patient/kiosk screens stay bilingual. | all admin screens |

## I. Changelog
- 2026-06-19 — Document created; seeded with §H rules 1–7 from the Phase-0 patient-landing rework.
- 2026-06-19 — Added rules 8–10 from the WhatsApp/Bhashini admin config screens.
