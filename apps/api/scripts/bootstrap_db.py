"""DB bootstrap — mirrors cpmai deploy.sh fresh-DB handling.

Fresh DB (no alembic_version): build schema from models (Base.metadata.create_all) then
`alembic stamp head` — fast, and avoids re-running migrations whose tables already exist.
Existing DB: `alembic upgrade head` to apply only the new (additive) migrations.
Idempotent: safe to run on every deploy.
"""
from __future__ import annotations

import os

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from app.core.db import engine
from app.models import Base


def main() -> None:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", os.environ.get("APP_DATABASE_URL", "sqlite+pysqlite:///./local.db"))
    insp = inspect(engine)
    if "alembic_version" not in insp.get_table_names():
        # Postgres needs RLS too; create_all builds tables, then apply RLS the baseline would.
        Base.metadata.create_all(engine)
        # RLS deferred: FORCE RLS requires the app to SET app.tenant_id per request, which is not
        # wired yet. Tenant isolation is enforced at the app layer (TenantScope, tested). Enabling
        # RLS now would block the seed and all queries. RLS is a Phase-1 hardening item.
        command.stamp(cfg, "head")
        print("bootstrap: fresh DB -> create_all + stamp head")
    else:
        command.upgrade(cfg, "head")
        print("bootstrap: existing DB -> alembic upgrade head")


def _apply_rls_if_postgres() -> None:
    if engine.dialect.name != "postgresql":
        return
    from sqlalchemy import text
    tables = ["doctors", "sessions", "patients", "consents", "booking_events", "bookings",
              "booking_patients", "tokens", "queue_entries", "idempotency_keys", "usage_events"]
    with engine.begin() as c:
        for t in tables:
            c.execute(text(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY"))
            c.execute(text(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY"))
            c.execute(text(
                f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='{t}' "
                f"AND policyname='tenant_isolation') THEN CREATE POLICY tenant_isolation ON {t} "
                f"USING (tenant_id = current_setting('app.tenant_id', true)); END IF; END $$;"))


if __name__ == "__main__":
    main()
