## What & why
<!-- One or two lines. Link the requirement ID(s) if relevant. -->

## Checklist (see BUILD_GUIDELINES.md)
- [ ] Ran `make check` locally — all green (compile + additive-migration + tests + secret scan)
- [ ] Ran `make docker-test` (prod-like container path) for anything touching the API/deploy
- [ ] **UI:** mock-first; uses shared `web/styles/app.css` (no hardcoded colors); English-always + Malayalam; responsive (≥48px tap targets); cache-bust bumped if CSS changed
- [ ] **API:** contract-first (openapi); tenancy server-resolved; error envelope; idempotency where it mutates; test added + mapped in the phase matrix test
- [ ] **DB:** additive only (no DROP/RENAME); RLS on new tenant tables; no PII in logs
- [ ] **Integrations:** stub/live via env; graceful fallback; metered to `usage_events`; no secrets in code
- [ ] New UI/build recommendation? Appended to `BUILD_GUIDELINES.md` §H so it applies from the next screen

## Screens changed (attach before/after vs mock)
<!-- Paste screenshots and the matching mock screen for visual parity. -->
