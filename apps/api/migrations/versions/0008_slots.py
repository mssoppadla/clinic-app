"""add slots (Phase 1 timed appointment slots) — additive only.
Revision ID: 0008_slots
Revises: 0007_user_username

Tenant-owned -> RLS enabled (apply_rls covers the new table; idempotent on the rest).
"""
from alembic import op
import sqlalchemy as sa

from app.core.rls import apply_rls

revision = "0008_slots"
down_revision = "0007_user_username"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "slots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False, index=True),
        sa.Column("doctor_id", sa.String(36), nullable=False, index=True),
        sa.Column("session_id", sa.String(36), nullable=False, index=True),
        sa.Column("date", sa.String(10), nullable=False),
        sa.Column("start_ts", sa.DateTime(), nullable=False),
        sa.Column("end_ts", sa.DateTime(), nullable=False),
        sa.Column("capacity", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("booked", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        sa.Column("created_at", sa.DateTime()),
    )
    apply_rls(op.get_bind())   # enable RLS on slots (idempotent for the others)


def downgrade():
    op.drop_table("slots")
