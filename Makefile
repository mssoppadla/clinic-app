# Developer shortcuts. `make check` is the pre-commit gate.
.PHONY: test check docker-test bootstrap run smoke

test:            ## fast unit+integration tests (sqlite)
	cd apps/api && PYTHONPATH=. pytest -q

check:           ## pre-commit gate (compile + additive-migration + tests + secret scan)
	bash scripts/local_check.sh

bootstrap:       ## create/upgrade the local DB schema + seed canary
	cd apps/api && PYTHONPATH=. python scripts/bootstrap_db.py && PYTHONPATH=. python -m app.seed

docker-test:     ## prod-like: bring up the full stack in Docker and run the canary smoke
	cp -n .env.example .env || true
	docker compose -p clinic-saas -f deploy/docker-compose.yml up -d --build
	@echo "waiting for health..."; for i in $$(seq 1 40); do curl -fsS http://localhost:8080/api/v1/healthz >/dev/null 2>&1 && break || sleep 3; done
	BASE_URL=http://localhost:8080/api/v1 python3 e2e/smoke.py
	docker compose -p clinic-saas -f deploy/docker-compose.yml down

run:             ## run API locally (sqlite)
	cd apps/api && PYTHONPATH=. APP_DATABASE_URL=sqlite+pysqlite:///./local.db python -m app.seed && \
	PYTHONPATH=. APP_DATABASE_URL=sqlite+pysqlite:///./local.db APP_CORS_ORIGINS=http://localhost:8080 uvicorn app.main:app --port 8077
