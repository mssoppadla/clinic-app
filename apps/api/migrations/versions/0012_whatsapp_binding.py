"""add whatsapp_binding (shared-number routing: patient phone -> clinic) — additive.
Revision ID: 0012_whatsapp_binding
Revises: 0011_whatsapp_inbound

Tenant-owned -> RLS enabled (apply_rls covers the new table; idempotent on the rest).
"""
from alembic import op
import sqlalchemy as sa

from app.core.rls import apply_rls

revision = "0012_whatsapp_binding"
down_revision = "0011_whatsapp_inbound"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "whatsapp_binding",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False, index=True),
        sa.Column("phone", sa.String(40), nullable=False, unique=True, index=True),
        sa.Column("updated_at", sa.DateTime()),
    )
    apply_rls(op.get_bind())


def downgrade():
    op.drop_table("whatsapp_binding")
