"""add users + user_roles (Phase 2 auth) — additive only.
Revision ID: 0005_users_roles
Revises: 0004_enable_rls

Identity tables are intentionally NOT under RLS (login happens before any tenant context).
"""
from alembic import op
import sqlalchemy as sa

revision = "0005_users_roles"
down_revision = "0004_enable_rls"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("email", sa.String(200), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(200), nullable=False, server_default=""),
        sa.Column("phone", sa.String(40), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("must_reset_password", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("mfa", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime()),
    )
    op.create_table(
        "user_roles",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("tenant_id", sa.String(36), nullable=True),
        sa.Column("role", sa.String(30), nullable=False),
        sa.Column("created_at", sa.DateTime()),
        sa.UniqueConstraint("user_id", "tenant_id", "role", name="uq_user_role"),
    )


def downgrade():
    op.drop_table("user_roles")
    op.drop_table("users")
