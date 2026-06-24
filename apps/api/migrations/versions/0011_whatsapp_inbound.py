"""add WhatsApp inbound: messages (dedupe + history) and pending (confirm-before-commit) — additive.
Revision ID: 0011_whatsapp_inbound
Revises: 0010_doctor_user

Tenant-owned -> RLS enabled (apply_rls covers the new tables; idempotent on the rest).
"""
from alembic import op
import sqlalchemy as sa

from app.core.rls import apply_rls

revision = "0011_whatsapp_inbound"
down_revision = "0010_doctor_user"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "whatsapp_messages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False, index=True),
        sa.Column("wa_message_id", sa.String(120), nullable=True, unique=True),
        sa.Column("direction", sa.String(3), nullable=False),
        sa.Column("phone", sa.String(40), nullable=False, index=True),
        sa.Column("text", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime()),
    )
    op.create_table(
        "whatsapp_pending",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False, index=True),
        sa.Column("phone", sa.String(40), nullable=False, index=True),
        sa.Column("action", sa.JSON(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("consumed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime()),
    )
    apply_rls(op.get_bind())   # enable RLS on the new tables (idempotent for the rest)


def downgrade():
    op.drop_table("whatsapp_pending")
    op.drop_table("whatsapp_messages")
