"""message_templates + clinic_message_settings + notifications, and tenants.timezone — additive.
Revision ID: 0013_message_templates
Revises: 0012_whatsapp_binding

The template catalog is scope-based (like integration_config) -> NOT RLS-isolated.
clinic_message_settings + notifications carry tenant_id -> RLS enabled via apply_rls
(TENANT_TABLES updated to match; idempotent on the rest).
"""
from alembic import op
import sqlalchemy as sa

from app.core.rls import apply_rls

revision = "0013_message_templates"
down_revision = "0012_whatsapp_binding"
branch_labels = None
depends_on = None


def upgrade():
    # tenants.timezone — existing rows default to Asia/Kolkata (backward compatible).
    op.add_column("tenants", sa.Column("timezone", sa.String(40), nullable=False,
                                       server_default="Asia/Kolkata"))

    op.create_table(
        "message_templates",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("scope", sa.String(60), nullable=False, index=True),
        sa.Column("event_type", sa.String(40), nullable=True),
        sa.Column("meta_name", sa.String(200), nullable=False),
        sa.Column("language", sa.String(10), nullable=False, server_default="en_US"),
        sa.Column("category", sa.String(20), nullable=False, server_default="UTILITY"),
        sa.Column("components", sa.JSON()),
        sa.Column("param_map", sa.JSON()),
        sa.Column("meta_status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("meta_template_id", sa.String(120), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime()),
        sa.Column("updated_at", sa.DateTime()),
        sa.UniqueConstraint("scope", "meta_name", "language", name="uq_template_scope_name_lang"),
    )

    op.create_table(
        "clinic_message_settings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False, index=True),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("template_id", sa.String(36), nullable=True),
        sa.Column("language", sa.String(10), nullable=True),
        sa.Column("variables", sa.JSON()),
        sa.Column("reminder_offsets", sa.JSON(), nullable=True),
        sa.Column("updated_at", sa.DateTime()),
        sa.UniqueConstraint("tenant_id", "event_type", name="uq_clinic_event"),
    )

    op.create_table(
        "notifications",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False, index=True),
        sa.Column("booking_id", sa.String(36), nullable=True, index=True),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("template_id", sa.String(36), nullable=True),
        sa.Column("to_phone", sa.String(40), nullable=False, index=True),
        sa.Column("status", sa.String(12), nullable=False, server_default="queued"),
        sa.Column("dedupe_key", sa.String(160), nullable=False, unique=True),
        sa.Column("wa_message_id", sa.String(120), nullable=True),
        sa.Column("params", sa.JSON()),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime()),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
    )

    apply_rls(op.get_bind())


def downgrade():
    op.drop_table("notifications")
    op.drop_table("clinic_message_settings")
    op.drop_table("message_templates")
    op.drop_column("tenants", "timezone")
