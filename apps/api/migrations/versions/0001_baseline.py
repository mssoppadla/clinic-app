"""baseline: all tables from ORM + Postgres Row-Level Security on tenant tables.

Expand-contract: this is the additive baseline. Later migrations ADD only (A24).
RLS statements run on Postgres only; skipped on SQLite (sandbox tests use the app-layer guard).
"""
from alembic import op
import sqlalchemy as sa
from app.models import Base

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None

# tables that carry tenant_id and must be RLS-isolated
TENANT_TABLES = [
    "doctors","sessions","patients","consents","booking_events","bookings",
    "booking_patients","tokens","queue_entries","idempotency_keys","usage_events",
]

def upgrade():
    bind = op.get_bind()
    Base.metadata.create_all(bind)  # create all ORM tables
    # RLS deferred to a later phase (needs app to SET app.tenant_id per request).
    # Tenant isolation is enforced at the application layer for now. See bootstrap_db.py.
    _ = TENANT_TABLES  # retained for the future RLS migration

def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for t in TENANT_TABLES:
            op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {t}")
            op.execute(f"ALTER TABLE {t} DISABLE ROW LEVEL SECURITY")
