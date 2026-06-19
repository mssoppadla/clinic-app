"""enable Postgres Row-Level Security on tenant tables — additive (no schema change).
Revision ID: 0004_enable_rls
Revises: 0003_onboarding_fields

RLS was deferred in 0001 (it blocked the seed before app.tenant_id was wired). The app now
sets app.tenant_id per request (TenantScope) and app.rls_bypass for trusted server paths
(db.system_session), so we can enforce isolation at the DB layer too. No-op on SQLite.
"""
from alembic import op

from app.core.rls import apply_rls, drop_rls

revision = "0004_enable_rls"
down_revision = "0003_onboarding_fields"
branch_labels = None
depends_on = None


def upgrade():
    apply_rls(op.get_bind())


def downgrade():
    drop_rls(op.get_bind())
