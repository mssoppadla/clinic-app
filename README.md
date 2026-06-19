# Clinic Booking SaaS — Phase 0 (walking skeleton)

> **Before building anything, read [`BUILD_GUIDELINES.md`](BUILD_GUIDELINES.md)** — the living standards & checklist (mock-first, shared stylesheet, English+Malayalam, responsive). New UI rules get appended there and apply from the next screen onward.


Spine + WhatsApp + Bhashini, end-to-end, locally testable. English-always + Malayalam (A15).
Event-sourced booking; tenant isolation (app-layer guard + Postgres RLS); config-from-env (no
hardcoding); WhatsApp/Bhashini swappable stub→live via env.

## Layout
- `apps/api` — FastAPI backend (event store + projections, integrations, tests, Alembic+RLS)
- `web/index.html` — responsive patient booking page (phone/tablet/laptop, iOS/Android)
- `deploy/` — Docker Compose (api×2, postgres, redis, caddy), Caddyfile
- `.env.example` — all config keys (copy to `.env`; never commit secrets)

## Run locally — option A: SQLite (no Docker)
```bash
cd apps/api
pip install -r requirements.txt
export APP_DATABASE_URL="sqlite+pysqlite:///./local.db" APP_CORS_ORIGINS="http://localhost:8080"
python -m app.seed                  # creates schema + __canary__ clinic
uvicorn app.main:app --port 8077
# in another shell:
cd ../../web && python -m http.server 8080
# open http://localhost:8080/?api=http://localhost:8077
```

## Run locally — option B: Docker Compose (prod-like, Postgres + RLS + Caddy)
```bash
cp .env.example .env        # set POSTGRES_PASSWORD
cd deploy && docker compose up --build
# open http://localhost:8080   (Caddy serves the page + proxies /api)
```

## Test (must be green before any commit)
```bash
cd apps/api && PYTHONPATH=. pytest -q
```
`tests/test_phase0_matrix.py` maps every in-scope 4-way-matrix requirement to a proving test.

## Go live with WhatsApp + Bhashini
Set in `.env`: `APP_WHATSAPP_MODE=live` + token/phone_number_id, `APP_BHASHINI_MODE=live` +
base_url/api_key/user_id. No code change — the clients switch on env.

## Next (not in Phase 0 code yet)
CI/CD pipeline, preflight (cpmai row-count guard, additive-migration check), security gates
(gitleaks/SCA), blue-green deploy + prod canary smoke, then production.
