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
    if bind.dialect.name == "postgresql":
        # app connects with a non-superuser role and sets app.tenant_id per request
        for t in TENANT_TABLES:
            op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
            op.execute(
                f"CREATE POLICY tenant_isolation ON {t} USING "
                f"(tenant_id = current_setting('app.tenant_id', true))"
            )

def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for t in TENANT_TABLES:
            op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {t}")
            op.execute(f"ALTER TABLE {t} DISABLE ROW LEVEL SECURITY")
