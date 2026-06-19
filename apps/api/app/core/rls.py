"""Postgres Row-Level Security — the DB-layer half of tenant isolation [S14].

Defense in depth: the app already filters by tenant_id (TenantScope). RLS makes the DB
enforce the same boundary even if app code forgets. It is a no-op on SQLite (sandbox tests).

How it works:
  - Each tenant-owned table has FORCE ROW LEVEL SECURITY + a `tenant_isolation` policy.
  - Normal request paths set `app.tenant_id` (transaction-local) via TenantScope, so only
    that tenant's rows are visible/insertable.
  - Trusted server paths (bootstrap, seed, provider/onboarding) set `app.rls_bypass='on'`
    (see db.system_session) because they legitimately operate across/without a tenant.
  - If NEITHER is set, current_setting(...) is NULL -> the policy matches no rows: a path
    that forgets to establish context FAILS CLOSED (no cross-tenant leak), which is the point.

Applied from TWO places (kept in sync here): bootstrap_db.py fresh-DB path (create_all +
this) and Alembic migration 0004 (existing-DB upgrade). Both are idempotent.
"""
from __future__ import annotations

import re

from sqlalchemy import text
from sqlalchemy.engine import make_url

# Tables that carry tenant_id and must be RLS-isolated (mirrors 0001 baseline TENANT_TABLES).
TENANT_TABLES = [
    "doctors", "sessions", "patients", "consents", "booking_events", "bookings",
    "booking_patients", "tokens", "queue_entries", "idempotency_keys", "usage_events",
]

_POLICY = "tenant_isolation"
_PREDICATE = ("tenant_id = current_setting('app.tenant_id', true) "
              "OR current_setting('app.rls_bypass', true) = 'on'")


def apply_rls(connection) -> None:
    """Enable + force RLS and (idempotently) create the tenant_isolation policy. Postgres only."""
    if connection.dialect.name != "postgresql":
        return
    for t in TENANT_TABLES:
        connection.execute(text(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY"))
        connection.execute(text(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY"))
        connection.execute(text(
            f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_policies "
            f"WHERE tablename='{t}' AND policyname='{_POLICY}') THEN "
            f"CREATE POLICY {_POLICY} ON {t} USING ({_PREDICATE}) WITH CHECK ({_PREDICATE}); "
            f"END IF; END $$;"))


_SAFE_ROLE = re.compile(r"^[a-z_][a-z0-9_]*$")


def ensure_app_role(admin_conn, app_url: str) -> str | None:
    """Create/refresh the non-superuser login role the APP connects as (from app_url's
    user+password), so Postgres RLS actually enforces. Run as a superuser/owner. Postgres
    only; returns the role name (or None if not applicable). Idempotent."""
    if admin_conn.dialect.name != "postgresql":
        return None
    url = make_url(app_url)
    role, pw = url.username, url.password or ""
    # Guard: only manage a clearly app-specific, safe-named role (never postgres/superuser).
    if not role or not _SAFE_ROLE.match(role) or role in ("postgres",):
        return None
    rid = '"' + role.replace('"', '""') + '"'
    pw_lit = "'" + pw.replace("'", "''") + "'"
    exists = admin_conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname=:r"), {"r": role}).scalar()
    verb = "ALTER" if exists else "CREATE"
    admin_conn.execute(text(
        f"{verb} ROLE {rid} WITH LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE PASSWORD {pw_lit}"))
    return role


def grant_app_privileges(admin_conn, role: str) -> None:
    """Grant the app role DML on all current + future tables/sequences. Idempotent; re-run
    each deploy so new (additive-migration) tables are covered. Postgres only."""
    if admin_conn.dialect.name != "postgresql" or not _SAFE_ROLE.match(role):
        return
    rid = '"' + role.replace('"', '""') + '"'
    admin_conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {rid}"))
    admin_conn.execute(text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {rid}"))
    admin_conn.execute(text(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {rid}"))
    admin_conn.execute(text(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {rid}"))
    admin_conn.execute(text(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {rid}"))


def drop_rls(connection) -> None:
    if connection.dialect.name != "postgresql":
        return
    for t in TENANT_TABLES:
        connection.execute(text(f"DROP POLICY IF EXISTS {_POLICY} ON {t}"))
        connection.execute(text(f"ALTER TABLE {t} NO FORCE ROW LEVEL SECURITY"))
        connection.execute(text(f"ALTER TABLE {t} DISABLE ROW LEVEL SECURITY"))
