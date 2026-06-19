"""add otp_challenges (Phase 2 — WhatsApp OTP password reset) — additive only.
Revision ID: 0006_otp_challenges
Revises: 0005_users_roles

Identity/auth infra — NOT under RLS (reset happens before any tenant context).
"""
from alembic import op
import sqlalchemy as sa

revision = "0006_otp_challenges"
down_revision = "0005_users_roles"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "otp_challenges",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), nullable=True),
        sa.Column("destination", sa.String(120), nullable=False, server_default=""),
        sa.Column("purpose", sa.String(30), nullable=False, server_default="password_reset"),
        sa.Column("code_hash", sa.String(200), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("consumed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime()),
    )


def downgrade():
    op.drop_table("otp_challenges")
