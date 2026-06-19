"""DB bootstrap — mirrors cpmai deploy.sh fresh-DB handling.

Runs as the ADMIN/owner connection (settings.admin_url; a superuser in prod). It:
  - creates/refreshes the non-superuser APP role the running app connects as (so RLS enforces),
  - builds the schema (fresh: create_all + stamp head; existing: alembic upgrade head),
  - applies RLS policies, and
  - grants the app role DML on all tables/sequences (idempotent; re-run each deploy).

SQLite/dev fall back to a single URL and skip the Postgres-only role/RLS steps.
Idempotent: safe to run on every deploy.
"""
from __future__ import annotations

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from app.core.config import get_settings
from app.core.rls import apply_rls, ensure_app_role, grant_app_privileges
from app.models import Base


def main() -> None:
    settings = get_settings()
    admin_engine = create_engine(settings.admin_url, future=True)
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", settings.admin_url)

    # 1) ensure the non-superuser app role exists (creds parsed from the app's database_url)
    role = None
    with admin_engine.begin() as conn:
        role = ensure_app_role(conn, settings.database_url)

    # 2) schema
    if "alembic_version" not in inspect(admin_engine).get_table_names():
        Base.metadata.create_all(admin_engine)        # fresh: build from models
        with admin_engine.begin() as conn:
            apply_rls(conn)                            # migrations are skipped on fresh path
        command.stamp(cfg, "head")
        print("bootstrap: fresh DB -> role + create_all + RLS + stamp head")
    else:
        command.upgrade(cfg, "head")                  # existing: additive migrations (incl. 0004 RLS)
        print("bootstrap: existing DB -> alembic upgrade head")

    # 3) grant the app role DML (after tables exist; covers any new tables each deploy)
    if role:
        with admin_engine.begin() as conn:
            grant_app_privileges(conn, role)
        print(f"bootstrap: granted DML to app role '{role}'")


if __name__ == "__main__":
    main()
