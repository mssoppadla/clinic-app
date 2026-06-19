"""add onboarding fields to tenants (go_live + contact) — additive only.
Revision ID: 0003_onboarding_fields
Revises: 0002_integration_config
"""
from alembic import op
import sqlalchemy as sa

revision = "0003_onboarding_fields"
down_revision = "0002_integration_config"
branch_labels = None
depends_on = None


def upgrade():
    # go_live defaults TRUE so existing tenants stay live (unchanged behaviour [A27,A28]);
    # self-registered clinics are inserted with go_live=False until a provider approves.
    op.add_column("tenants", sa.Column("go_live", sa.Boolean(), nullable=False,
                                       server_default=sa.true()))
    op.add_column("tenants", sa.Column("contact_name", sa.String(160), nullable=True))
    op.add_column("tenants", sa.Column("contact_email", sa.String(200), nullable=True))
    op.add_column("tenants", sa.Column("contact_phone", sa.String(40), nullable=True))


def downgrade():
    op.drop_column("tenants", "contact_phone")
    op.drop_column("tenants", "contact_email")
    op.drop_column("tenants", "contact_name")
    op.drop_column("tenants", "go_live")
