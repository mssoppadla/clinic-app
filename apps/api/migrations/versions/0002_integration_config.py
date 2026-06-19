"""add integration_config (runtime integration settings) — additive only.
Revision ID: 0002_integration_config
Revises: 0001_baseline
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_integration_config"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "integration_config",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("scope", sa.String(40), nullable=False, server_default="platform"),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("key", sa.String(60), nullable=False),
        sa.Column("value", sa.Text(), nullable=False, server_default=""),
        sa.Column("is_secret", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("updated_at", sa.DateTime()),
        sa.UniqueConstraint("scope", "provider", "key", name="uq_intcfg"),
    )


def downgrade():
    op.drop_table("integration_config")
